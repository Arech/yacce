import argparse
import os
import re

from .common import (
    addCommonCliArgs,
    CompilersTuple,
    kMainDescription,
    LoggingConsole,
    updateCommonCliArgs,
)


def parseLog(
    Con: LoggingConsole,
    log_file: str,
    cdw: str,
    do_test_files: bool,
    compilers: CompilersTuple,
    do_linking: bool,
) -> int:
    Con.debug(
        f"Parsing log file: {log_file}, assuming directory: {cdw}, test_files={do_test_files}, compilers={compilers}"
    )

    running_pids = {}  # int(pid) -> tuple(start_ts: float, args: str, line_idx: int, is_linking: bool)
    compile_commands = []  # list to be later written to compile_commands.json
    linking_commands = []
    # errors = {} # error_code -> array of line_idx where it happened

    def handleExit(pid: int, ts: float, exit_code: str | None, line_idx: int) -> None:
        # negative exit code means the process termination was not found in the log
        nonlocal running_pids, compile_commands, Con

        (start_ts, args, start_line_idx, is_linking) = running_pids[pid]

        is_exit_logged = line_idx > 0
        if is_exit_logged:  # <=0 line_idx is used when we didn't find the process exit in the log
            assert exit_code is not None, (
                f"Line {line_idx}: pid {pid} exited without an exit code. This violates parser assumptions"
            )
            # if exit code isn't set, something is very wrong with the regexp or the log file,
            # so there's no point to try to continue. However, even if the exit code is non-zero,
            # we could at least save the other commands to compile_commands.json.

            if exit_code != "0":
                Con.warning(
                    f"Line {line_idx}: pid {pid} (started at line {start_line_idx}) exited with "
                    f"non-zero exit code {exit_code}. This might mean the build wasn't successful "
                    "and the resulting compile_commands.json might be incomplete."
                )

            if ts < start_ts:
                # depending on used clock type, this might happen due to clock adjustments
                Con.warning(
                    f"Line {line_idx}: pid {pid} (started at line {start_line_idx}) exited at time "
                    f"{ts:.6f} which is before it started at "
                    f"{start_ts:.6f}. Continuing, but the log file might be malformed."
                )
                # todo: save this to errors
        else:
            Con.warning(
                f"pid {pid} (started at line {start_line_idx}) didn't log its exit. "
                "This might mean the log file is incomplete and hence so is the resulting compile_commands.json."
            )

        duration = ts - start_ts if is_exit_logged else 0.0
        (linking_commands if is_linking else compile_commands).append((duration, args))
        del running_pids[pid]

    # greedy match repeatedly blocks ending on escaped quote \" literal, or that doesn't contain
    # quotes at all until first unescaped quote
    rInQuotes = re.compile(r"\"((?:[^\"]*\\\"|[^\"]*)*)\"")
    rInBraces = re.compile(r"\[((?:[^\[]*\\\[|[^\[]*)*)\]")

    def handleExec(call: str, pid: int, ts: float, line_idx: int, line: str) -> None:
        nonlocal running_pids, Con
        assert pid not in running_pids  # should be checked by the caller
        """assert call in ("execve", "execveat"), (
            f"Line {line_idx}: pid {pid} made call {call}. The code is inconsistent "
            "with rExecOrExit regexp"
        )"""
        assert call == "execve", (
            "execveat() handling is not implemented yet, consider making a PR or report "
            "an issue supplying a log file with execveat() calls"
        )
        assert line[0:1] == "(", "Unexpected format of the {call} syscall in the log file"
        if not (line.endswith(" = 0\n") or line.endswith(" = 0")):
            Con.warning(
                f"Line {line_idx}: pid {pid} made call {call} but the return code is not 0. "
                "This might mean the build wasn't successful and the resulting compile_commands.json "
                "might be incomplete."
            )

        # extract the first argument of execve, which is the executable path
        match_filepath = rInQuotes.match(line[1:])
        assert match_filepath, (
            f"Line {line_idx}: pid {pid} made call {call} but the executable path argument couldn't be parsed. "
            "This might mean the log file is malformed or the regexp is incorrect"
        )

        # unescaping quotes and other symbols. Not 100% sure that latin1 is a correct choice
        compiler_path = match_filepath.group(1).encode("latin1").decode("unicode_escape")
        if (
            compiler_path not in compilers.fullpaths
            and os.path.basename(compiler_path) not in compilers.basenames
        ):
            return  # not a compiler we care about

        # finding execv() args in the rest of the line
        args_start_pos = match_filepath.end() + 3
        assert line[match_filepath.end() + 1 : args_start_pos + 1].startswith(", ["), (
            f"Unexpected format of the {call} syscall in the log file"
        )
        match_args = rInBraces.match(line[args_start_pos:])
        assert match_args, (
            f"Line {line_idx}: pid {pid} made call {call} but the arguments array couldn't be parsed. "
            "This might mean the log file is malformed or the regexp is incorrect"
        )
        # TODO: fix rInBraces. A bracket in a filepath doesn't need to be escaped, so the regexp will fail
        args_str = match_args.group(1)

        # checking if it's a linking command (heuristic: if it contains -o and no -c)
        is_linking = True

        if not do_linking and is_linking:
            return  # not interested in linking commands

        # TODO: do we need to fix the first argument in args to be the same as the one used in
        # execve()? It might be different depending how execve() was called.

        running_pids[pid] = (ts, line.strip(), line_idx, is_linking)

    # match the start of the log string: (<pid>) (<time.stamp>) (execve|execveat|exited...)
    rExecOrExit = re.compile(
        r"(?P<pid>\d+) (?P<unix_ts>\d+)\.(?P<unix_ts_ms>\d+) (?P<call>execve|execveat|\+\+\+ exited with (?P<exit_code>\d+) \+\+\+)"
    )

    with open(log_file, "r") as file:
        for line_idx, line in enumerate(file):
            match_exec_or_exit = rExecOrExit.match(line)
            if not match_exec_or_exit:
                continue  # nothing to do here

            pid = int(match_exec_or_exit.group("pid"))
            ts = float(match_exec_or_exit.group("unix_ts")) + float(
                1e-6 * int(match_exec_or_exit.group("unix_ts_ms"))
            )
            call = match_exec_or_exit.group("call")
            exit_code = match_exec_or_exit.group("exit_code")  # could be None

            if call.startswith("+++ "):
                if pid not in running_pids:
                    continue  # this must be not a process we care about
                handleExit(pid, ts, exit_code, line_idx + 1)
            else:
                # handle execve/execveat here
                handleExec(call, pid, ts, line_idx + 1, line[match_exec_or_exit.end() :])

    # finishing unfinished processes
    for pid in running_pids.keys():
        handleExit(pid, 0.0, None, 0)

    assert 0 == len(running_pids)
    Con.print("compile_commands = ", compile_commands)
    Con.print("linking_commands = ", linking_commands)
    return 0


def mode_from_log(Con: LoggingConsole, args: argparse.Namespace, unparsed_args: list) -> int:
    parser = argparse.ArgumentParser(
        prog="yacce from_log",
        description=kMainDescription
        + "\n\nMode 'from_log' tries to generate compile_commands.json from a strace log file.\n"
        "WARNING: this mode is intended for debugging purposes only and most likely will not "
        "produce a correct compile_commands.json due to a lack of information about the build system.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("log_file", help="Path to the strace log file to parse.", type=str)
    parser = addCommonCliArgs(parser)
    args = parser.parse_args(unparsed_args, namespace=args)

    if args.log_file is None or not os.path.isfile(args.log_file):
        Con.critical("Log file is not specified or does not exist.")
        return 1

    args = updateCommonCliArgs(Con, args)

    return parseLog(
        Con,
        args.log_file,
        args.cwd,
        not args.ignore_not_found,
        args.compiler,
        args.linking_commands,
    )
