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


def storeJson(
    filename: str, commands: list[tuple], cmd_times: list[float], cwd: str, is_link: bool
):
    cwd = cwd.replace('"','\"')
    with open(filename, "w") as f:
        f.write("[\n")
        for idx,cmd_tuple in enumerate(commands):
            f.write("{\n")
            f.write(f" \"directory\": \"{cwd}\",\n")
            if is_link:
                args_str, arg_output = cmd_tuple
            else:
                args_str, arg_output, arg_compile = cmd_tuple
                f.write(f" \"file\": \"{arg_compile}\",\n")

            f.write(f" \"arguments\": {args_str},\n")
            f.write(f" \"output\": \"{arg_output}\",\n")
            f.write(f" \"duration_s\": {cmd_times[idx]:.6f}\n")
            f.write("}\n")

        f.write("]\n")


def rawPathExists(cwd: str, path: str) -> bool:
    path = path.encode("latin1").decode("unicode_escape")
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.exists(path)


def parseLog(
    Con: LoggingConsole,
    log_file: str,
    cwd: str,
    do_test_files: bool,
    compilers: CompilersTuple,
    do_link: bool,
) -> tuple[list, list, list, list]:
    Con.debug(
        f"Parsing log file: {log_file}, assuming directory: {cwd}, test_files={do_test_files}, compilers={compilers}"
    )

    running_pids = {}  # int(pid) -> tuple(start_ts: float, line_idx: int, is_link: bool, cmd_idx:int)
    compile_commands = []  # list to be later written to compile_commands.json
    compile_cmd_time = []
    link_commands = []
    link_cmd_time = []
    # errors = {} # error_code -> array of line_idx where it happened

    def handleExit(pid: int, ts: float, exit_code: str | None, line_idx: int) -> None:
        # negative exit code means the process termination was not found in the log
        nonlocal running_pids, compile_cmd_time, link_cmd_time, Con

        (start_ts, start_line_idx, is_link, cmd_idx) = running_pids[pid]

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
        if is_link:
            link_cmd_time[cmd_idx] = duration
        else:
            compile_cmd_time[cmd_idx] = duration

        del running_pids[pid]

    # greedy match repeatedly blocks ending on escaped quote \" literal, or that doesn't contain
    # quotes at all until first unescaped quote
    rInQuotes = re.compile(r"\"((?:[^\"]*\\\"|[^\"]*)*)\"")
    # greedy match [] with any chars inside of ""
    rInBraces = re.compile(r"^\[(?:(?:[, ])*\"(?:(?:[^\"]*\\\"|[^\"]*)*)\")*\]")

    def handleExec(call: str, pid: int, ts: float, line_idx: int, line: str) -> None:
        nonlocal running_pids, Con, link_commands, compile_commands, compile_cmd_time, link_cmd_time
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
        # we can't simply search for the closing ] because there might be braces in file names and
        # they don't have to be shell-escaped
        match_args = rInBraces.match(line[args_start_pos:])
        assert match_args, (
            f"Line {line_idx}: pid {pid} made call {call} but the arguments array couldn't be parsed. "
            "This might mean the log file is malformed or rInBraces regexp is incorrect"
        )

        args_str = match_args.group()

        # checking if it's a linking command (heuristic: if it contains -o and no -c)
        has_output = ' "-o"' in args_str
        if not has_output:
            Con.error(
                f"Line {line_idx}: pid {pid} made call {call} which doesn't contain an output file (-o). "
                f"Don't know what to do with it, ignoring. Full command args are: {args_str}"
            )
            return
        is_compile = ' "-c"' in args_str
        is_link = not is_compile

        if not do_link and is_link:
            return  # not interested in linking commands

        # Extracting args from the args_str. We can't simply split by ", " because there might be
        # such sequence in file names. So we use the same rInQuotes regexp to extract them one by one.
        # In a sense, it's a duplication of application of the same regexp as above, but we must
        # scope the search to the inside of the braces only
        args = re.findall(rInQuotes, args_str)

        # now walking over the args and checking existence of those that we know to be files or dirs.
        # Also getting arguments of -o and -c options, if they are present
        next_is_path = False
        next_is_output = False
        next_is_compile = False
        arg_compile = None
        arg_output = None
        for arg in args:
            if next_is_path:
                next_is_path = False
                if do_test_files and not rawPathExists(cwd, arg):
                    Con.warning(
                        f"Line {line_idx}: pid {pid} made call {call} with argument '{arg}' "
                        "which doesn't exist. This might mean the build system is misconfigured "
                        "or the log file is incomplete and hence so is the resulting compile_commands.json. "
                        f"Full command args are: {args_str}"
                    )
                if next_is_compile:
                    next_is_compile = False
                    if arg_compile is not None:
                        Con.warning(
                            f"Line {line_idx}: pid {pid} made call {call} with multiple -c options. "
                            f"This is unusual, taking the last one. Full command args are: {args_str}"
                        )
                    arg_compile = arg  # it's already escaped
                if next_is_output:
                    next_is_output = False
                    if arg_output is not None:
                        Con.warning(
                            f"Line {line_idx}: pid {pid} made call {call} with multiple -o options. "
                            f"This is unusual, taking the last one. Full command args are: {args_str}"
                        )
                    arg_output = arg  # it's already escaped
            elif arg == "-o":
                next_is_path = True
                next_is_output = True
            elif arg == "-c":
                next_is_path = True
                next_is_compile = True
            elif arg in (
                "-I",
                "--include-directory",
                "-isystem",
                "-iquote",
                "-isysroot",
                "--sysroot",
                "-cxx-isystem",  # TODO proper list + parsing combined args like --sysroot=/path
            ):
                next_is_path = True
            """elif do_test_files and arg.startswith((
                "-I",
                "--include-directory=",
                "-isystem",
                "-iquote",
                "-isysroot",
                "--sysroot=",
                "-cxx-isystem"
            )):
                # TODO
                pass"""

        assert arg_output is not None
        assert is_link or arg_compile is not None

        # TODO: do we need to fix the first argument in args to be the same as the one used in
        # execve()? It might be different depending how execve() was called.

        if is_link:
            link_commands.append((args_str, arg_output))
            cmd_idx = len(link_cmd_time)
            link_cmd_time.append(0.0)
        else:
            compile_commands.append((args_str, arg_output, arg_compile))
            cmd_idx = len(compile_cmd_time)
            compile_cmd_time.append(0.0)

        running_pids[pid] = (ts, line_idx, is_link, cmd_idx)

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
    for pid in list(running_pids.keys()):
        handleExit(pid, 0.0, None, 0)

    assert 0 == len(running_pids)
    if len(compile_commands) == 0 and len(link_commands)==0:
        Con.warning("No compiler invocation were found in the log. If you're using a custom compiler, pass it in --compiler option.")
    return compile_commands, compile_cmd_time, link_commands, link_cmd_time


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

    compile_commands, compile_cmd_time, link_commands, link_cmd_time = parseLog(
        Con,
        args.log_file,
        args.cwd,
        not args.ignore_not_found,
        args.compiler,
        args.link_commands,
    )

    dest_dir = args.dest_dir if hasattr(args, "dest_dir") and args.dest_dir else os.getcwd()

    storeJson(
        os.path.join(dest_dir, "compile_commands.json"),
        compile_commands,
        compile_cmd_time,
        args.cwd,
        False,
    )
    if args.link_commands:
        storeJson(
            os.path.join(dest_dir, "link_commands.json"),
            link_commands,
            link_cmd_time,
            args.cwd,
            True,
        )

    return 0
