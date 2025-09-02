"""Commonly needed data & code"""

import argparse
from collections import namedtuple
import enum
import os
import re
import rich.console


class YacceException(RuntimeError):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)


# taken from https://github.com/Arech/benchstats/blob/be9e925ae85b7dc1c19044ad5f6eddea681f9f77/src/benchstats/common.py#L56
class LoggingConsole(rich.console.Console):
    # @enum.verify(enum.CONTINUOUS)  # not supported by Py 3.10
    class LogLevel(enum.IntEnum):
        Debug = 0
        Info = 1
        Warning = 2
        Error = 3
        Failure = 4
        Critical = 5

    def __init__(self, log_level: LogLevel = LogLevel.Debug, **kwargs):
        assert isinstance(log_level, LoggingConsole.LogLevel)
        self.log_level = log_level
        if "emoji" not in kwargs:
            kwargs["emoji"] = False
        if "highlight" not in kwargs:
            kwargs["highlight"] = False
        super().__init__(**kwargs)

    def _do_log(self, color: str, lvl: str, *args, **kwargs):
        if "sep" in kwargs:
            sep = kwargs["sep"] if len(kwargs["sep"]) > 0 else " "
        else:
            sep = " "
            kwargs["sep"] = sep
        return super().print(f"[[{color}]{lvl:4s}[/{color}]]{sep}", *args, **kwargs)

    def debug(self, *args, **kwargs):
        if self.log_level > LoggingConsole.LogLevel.Debug:
            return None
        return self._do_log("bright_black", "dbg", *args, **kwargs)

    def info(self, *args, **kwargs):
        if self.log_level > LoggingConsole.LogLevel.Info:
            return None
        return self._do_log("bright_white", "info", *args, **kwargs)

    def warning(self, *args, **kwargs):
        if self.log_level > LoggingConsole.LogLevel.Warning:
            return None
        return self._do_log("yellow", "warn", *args, **kwargs)

    def error(self, *args, **kwargs):
        if self.log_level > LoggingConsole.LogLevel.Error:
            return None
        return self._do_log("orange", "Err", *args, **kwargs)

    def failure(self, *args, **kwargs):
        if self.log_level > LoggingConsole.LogLevel.Failure:
            return None
        return self._do_log("red", "FAIL", *args, **kwargs)

    def critical(self, *args, **kwargs):
        if self.log_level > LoggingConsole.LogLevel.Critical:
            return None
        return self._do_log("magenta", "CRIT", *args, **kwargs)

    def yacce_begin(self):
        super().print("[bold bright_blue]==== YACCE >>>>>>>>[/bold bright_blue]")

    def yacce_end(self):
        super().print("[bold bright_blue]<<<<<<<< YACCE ====[/bold bright_blue]")


kMainDescription = (
    "yacce is a compile_commands.json generator for Bazel (and other build systems if/when implemented).\n"
    "Homepage: https://github.com/Arech/yacce"
)


def addCommonCliArgs(parser: argparse.ArgumentParser, addendums: dict = {}):
    """ "Adds arguments common for multiple modes to the given parser."""
    parser.add_argument(
        "--cwd",
        help="Path of working directory of the compilation. "
        "This value goes to 'directory' field of an "
        "entry of compile_commands.json and is used to resolve relative paths found in the command. "
        "yacce will try to test if mentioned files exist in this directory and warn if they aren't, "
        "but passing files existence test alone doesn't guarantee that the resulting compile_commands.json will be correct."
        + addendums.get("cwd", ""),
        type=str,
    )

    parser.add_argument(
        "--ignore-not-found",
        help="If set, will not test if files to be added to compile_commands.json exist. Default: %(default)s",
        action=argparse.BooleanOptionalAction,
        default=False,
    )

    parser.add_argument(
        "-l",
        "--link_commands",
        help="If set, will also generate link_commands.json (in a similar format to "
        "compile_commands, but for linking. Useful to get some insights). Default: %(default)s",
        action=argparse.BooleanOptionalAction,
        default=False,
    )

    parser.add_argument(
        "--save_duration",
        help="If set, will add 'duration_s' field into a resulting .json that contain how long the command run in "
        "seconds with microsecond resolution. Have no automated use, but can be inspected manually, or with a custom "
        "script to obtain build system performance insights. Default: %(default)s",
        action=argparse.BooleanOptionalAction,
        default=False,
    )

    parser.add_argument(
        "-c",
        "--compiler",
        help="Abs path or basename of a custom compiler used by the build system. Many such arguments can be passed.",
        type=str,
        nargs="*",
    )

    parser.add_argument(
        "-d",
        "--dest_dir",
        help="Destination directory into which to create compile_commands.json. Default: current working directory."
        + addendums.get("dest_dir", ""),
    )

    return parser


CompilersTuple = namedtuple("CompilersTuple", ["basenames", "fullpaths"])


def makeCompilersSet(custom_compilers: list[str] | None) -> CompilersTuple:
    """Adds custom compilers to the set of known compilers to find in strace log."""

    if custom_compilers is None:
        custom_compilers = []
    assert isinstance(custom_compilers, list)
    assert all(isinstance(c, str) for c in custom_compilers)

    kGccVers = (9, 18)
    kGccPfxs = ("", "x86_64-linux-gnu-")
    kClangVers = (10, 25)

    basenames = frozenset(
        ["cc", "c++", "gcc", "g++", "clang", "clang++"]
        + [f"{pfx}gcc-{v}" for v in range(*kGccVers) for pfx in kGccPfxs]
        + [f"{pfx}g++-{v}" for v in range(*kGccVers) for pfx in kGccPfxs]
        + [f"clang-{v}" for v in range(*kClangVers)]
        + [f"clang++-{v}" for v in range(*kClangVers)]
        + [c for c in custom_compilers if c and not c.startswith("/")]
    )
    # note there's not much point to try to prune the set of basenames or full paths, as a build system
    # could reference a compiler in a custom path, so we can't detect its presence on the machine.

    paths = frozenset([c for c in custom_compilers if c and c.startswith("/")])

    return CompilersTuple(basenames=basenames, fullpaths=paths)


# line_num:int is the number (1 based index) of line in the log that spawned the process
# is_link:bool is the command is link command
# cmd_idx:int is the index of command in a corresponding list
ProcessProps = namedtuple("ProcessProps", ("start_ts_us", "line_num", "is_link", "cmd_idx"))
# args:list[str], output:str, tu:str (translation unit)
CompileCommand = namedtuple("CompileCommand", ("args", "output", "tu"))
LinkCommand = namedtuple("LinkCommand", ("args", "output"))


class BaseParser:
    def __init__(
        self,
        Con: LoggingConsole,
        log_file: str,
        cwd: str,
        do_test_files: bool,
        compilers: CompilersTuple,
        do_link: bool,
    ) -> None:
        self.Con = Con
        self._compilers = compilers
        self._do_link = do_link
        self._cwd = cwd
        self._test_files = do_test_files

        assert isinstance(cwd, str)
        if do_test_files and not os.path.isdir(cwd):
            Con.warning(
                "Compilation directory '",
                cwd,
                "' doesn't exist. If you used --cwd option, check its correctness. "
                "Resulting json will likely be invalid.",
            )

        self._running_pids: dict[int, ProcessProps] = {}

        self.compile_commands: list[CompileCommand] = []
        self.compile_cmd_time: list[float] = []
        self.link_commands: list[LinkCommand] = []
        self.link_cmd_time: list[float] = []
        # errors = {} # error_code -> array of line_idx where it happened

        # greedy match repeatedly blocks ending on escaped quote \" literal, or that doesn't contain
        # quotes at all until first unescaped quote
        self._r_in_quotes = re.compile(r"\"((?:[^\"]*\\\"|[^\"]*)*)\"")
        # greedy match [] with any chars inside of ""
        self._r_in_braces = re.compile(r"^\[(?:(?:[, ])*\"(?:(?:[^\"]*\\\"|[^\"]*)*)\")*\]")

        self._parseLog(log_file)

    def _parseLog(self, log_file: str) -> None:
        # match the start of the log string: (<pid>) (<time.stamp>) (execve|execveat|exited...)
        r_exec_or_exit = re.compile(
            r"(?P<pid>\d+) (?P<unix_ts>\d+)\.(?P<unix_ts_ms>\d+) (?P<call>execve|execveat|\+\+\+ exited with (?P<exit_code>\d+) \+\+\+)"
        )

        with open(log_file, "r") as file:
            for line_idx, line in enumerate(file):
                match_exec_or_exit = r_exec_or_exit.match(line)
                if not match_exec_or_exit:
                    continue  # nothing to do here

                pid = int(match_exec_or_exit.group("pid"))
                ts = float(match_exec_or_exit.group("unix_ts")) + float(
                    1e-6 * int(match_exec_or_exit.group("unix_ts_ms"))
                )
                call = match_exec_or_exit.group("call")
                exit_code = match_exec_or_exit.group("exit_code")  # could be None

                if call.startswith("+++ "):
                    if pid not in self._running_pids:
                        continue  # this must be not a process we care about
                    self._handleExit(pid, ts, exit_code, line_idx + 1)
                else:
                    # handle execve/execveat here
                    self._handleExec(call, pid, ts, line_idx + 1, line[match_exec_or_exit.end() :])

        # finishing unfinished processes
        for pid in list(self._running_pids.keys()):  # must rematerialize since exit() deletes them
            self._handleExit(pid, 0.0, None, 0)

        assert 0 == len(self._running_pids)
        if len(self.compile_commands) == 0 and len(self.link_commands) == 0:
            self.Con.warning(
                "No compiler invocation were found in the log. If you're using a custom compiler, pass it in --compiler option."
            )

    def _handleExit(self, pid: int, ts: float, exit_code: str | None, line_num: int) -> None:
        # negative exit code means the process termination was not found in the log
        (start_ts, start_line_num, is_link, cmd_idx) = self._running_pids[pid]

        is_exit_logged = line_num > 0
        if is_exit_logged:  # <=0 line_idx is used when we didn't find the process exit in the log
            assert exit_code is not None, (
                f"Line {line_num}: pid {pid} exited without an exit code. This violates parser assumptions"
            )
            # if exit code isn't set, something is very wrong with the regexp or the log file,
            # so there's no point to try to continue. However, even if the exit code is non-zero,
            # we could at least save the other commands to compile_commands.json.

            if exit_code != "0":
                self.Con.warning(
                    f"Line {line_num}: pid {pid} (started at line {start_line_num}) exited with "
                    f"non-zero exit code {exit_code}. This might mean the build wasn't successful "
                    "and the resulting compile_commands.json might be incomplete."
                )

            if ts < start_ts:
                # depending on used clock type, this might happen due to clock adjustments
                self.Con.warning(
                    f"Line {line_num}: pid {pid} (started at line {start_line_num}) exited at time "
                    f"{ts:.6f} which is before it started at "
                    f"{start_ts:.6f}. Continuing, but the log file might be malformed."
                )
                # todo: save this to errors
        else:
            self.Con.warning(
                f"pid {pid} (started at line {start_line_num}) didn't log its exit. "
                "This might mean the log file is incomplete and hence so is the resulting compile_commands.json."
            )

        duration = ts - start_ts if is_exit_logged else 0.0
        if is_link:
            self.link_cmd_time[cmd_idx] = duration
        else:
            self.compile_cmd_time[cmd_idx] = duration

        del self._running_pids[pid]

    def _handleExec(self, call: str, pid: int, ts: float, line_num: int, line: str) -> None:
        assert pid not in self._running_pids  # should be checked by the caller
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
            self.Con.warning(
                f"Line {line_num}: pid {pid} made call {call} but the return code is not 0. "
                "This might mean the build wasn't successful and the resulting compile_commands.json "
                "might be incomplete."
            )

        # extract the first argument of execve, which is the executable path
        match_filepath = self._r_in_quotes.match(line[1:])
        assert match_filepath, (
            f"Line {line_num}: pid {pid} made call {call} but the executable path argument couldn't be parsed. "
            "This might mean the log file is malformed or the regexp is incorrect"
        )

        # unescaping quotes and other symbols. Not 100% sure that latin1 is a correct choice
        compiler_path = match_filepath.group(1).encode("latin1").decode("unicode_escape")
        if (
            compiler_path not in self._compilers.fullpaths
            and os.path.basename(compiler_path) not in self._compilers.basenames
        ):
            return  # not a compiler we care about

        # finding execv() args in the rest of the line
        args_start_pos = match_filepath.end() + 3
        assert line[match_filepath.end() + 1 : args_start_pos + 1].startswith(", ["), (
            f"Unexpected format of the {call} syscall in the log file"
        )
        # we can't simply search for the closing ] because there might be braces in file names and
        # they don't have to be shell-escaped
        match_args = self._r_in_braces.match(line[args_start_pos:])
        assert match_args, (
            f"Line {line_num}: pid {pid} made call {call} but the arguments array couldn't be parsed. "
            "This might mean the log file is malformed or rInBraces regexp is incorrect"
        )

        args_str = match_args.group()

        # TODO: do args processing after @file extension is done, + argument variation split has been made

        # checking if it's a linking command (heuristic: if it contains -o and no -c)
        has_output = ' "-o"' in args_str
        is_compile = ' "-c"' in args_str
        is_link = not is_compile

        if not self._do_link and is_link:
            return  # not interested in linking commands

        # Extracting args from the args_str. We can't simply split by ", " because there might be
        # such sequence in file names. So we use the same rInQuotes regexp to extract them one by one.
        # In a sense, it's a duplication of application of the same regexp as above, but we must
        # scope the search to the inside of the braces only
        args = re.findall(self._r_in_quotes, args_str)

        if not has_output:
            # TODO: do this only after @file handling

            self.Con.error(
                f"Line {line_num}: pid {pid} made call {call} which doesn't contain an output file (-o). "
                f"Don't know what to do with it, ignoring. Full command args are: {args_str}"
            )
            return

        # now walking over the args and checking existence of those that we know to be files or dirs.
        # Also getting arguments of -o and -c options, if they are present
        next_is_path = False
        next_is_output = False
        next_is_compile = False
        arg_compile = None
        arg_output = None
        for arg in args:
            # TODO argument blacklist
            if next_is_path:
                next_is_path = False
                if self._test_files and not rawPathExists(self._cwd, arg):
                    self.Con.warning(
                        f"Line {line_num}: pid {pid} made call {call} with argument '{arg}' "
                        "which doesn't exist. This might mean the build system is misconfigured "
                        "or the log file is incomplete and hence so is the resulting compile_commands.json. "
                        f"Full command args are: {args_str}"
                    )
                if next_is_compile:
                    next_is_compile = False
                    if arg_compile is not None:
                        self.Con.warning(
                            f"Line {line_num}: pid {pid} made call {call} with multiple -c options. "
                            f"This is unusual, taking the last one. Full command args are: {args_str}"
                        )
                    arg_compile = arg  # it's already escaped
                if next_is_output:
                    next_is_output = False
                    if arg_output is not None:
                        self.Con.warning(
                            f"Line {line_num}: pid {pid} made call {call} with multiple -o options. "
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

        #####
        # quick check if for each path argument that starts with external there exist a corresponding argument that starts with bazel-out/k8-opt/bin/external
        """for arg in args:
            if arg.startswith("external/") and not arg.endswith((".cpp",".c",".cc",".S")):
                found=False
                rMatch=re.compile(r"^bazel-out/[^\/]+/bin/" + re.escape(arg)+"$")
                for a in args:
                    if rMatch.match(a):
                        found = True
                        break
                if not found:
                    Con.warning(f"Line {line_idx}, pid {pid}: no alternative for {arg}")
                    """
        #####

        # TODO: do we need to fix the first argument in args to be the same as the one used in
        # execve()? It might be different depending how execve() was called.

        if is_link:
            self.link_commands.append(LinkCommand(args, arg_output))
            cmd_idx = len(self.link_cmd_time)
            self.link_cmd_time.append(0.0)
        else:
            self.compile_commands.append(CompileCommand(args, arg_output, arg_compile))
            cmd_idx = len(self.compile_cmd_time)
            self.compile_cmd_time.append(0.0)

        self._running_pids[pid] = ProcessProps(ts, line_num, is_link, cmd_idx)

    def storeJsons(self, dest_dir: str, save_duration: bool):
        storeJson(
            self.Con,
            dest_dir,
            self.compile_commands,
            self.compile_cmd_time if save_duration else None,
            self._cwd,
        )
        if self._do_link:
            storeJson(
                self.Con,
                dest_dir,
                self.link_commands,
                self.link_cmd_time if save_duration else None,
                self._cwd,
            )


def storeJson(
    Con: LoggingConsole,
    path: str,
    commands: list[CompileCommand] | list[LinkCommand],
    cmd_times: list[float] | None,
    cwd: str,
    apnd_REMOVE="",
):
    if not commands:
        assert not cmd_times
        Con.debug("storeJson() got empty list for path", path)
        return

    save_duration = cmd_times is not None
    assert not save_duration or len(commands) == len(cmd_times)

    e = next(iter(commands))
    is_link = isinstance(e, LinkCommand)
    assert is_link or isinstance(e, CompileCommand)

    filename = os.path.join(path, ("link" if is_link else "compile") + f"_commands{apnd_REMOVE}.json")

    cwd = cwd.replace('"', '\\"')
    with open(filename, "w") as f:
        f.write("[\n")
        for idx, cmd_tuple in enumerate(commands):
            f.write(("," if idx > 0 else "") + "{\n")
            f.write(f' "directory": "{cwd}",\n')
            if is_link:
                args, arg_output = cmd_tuple
            else:
                args, arg_output, arg_compile = cmd_tuple
                f.write(f' "file": "{arg_compile}",\n')

            args_str = '", "'.join(args)
            f.write(f' "arguments": ["{args_str}"],\n')
            if save_duration:
                f.write(f' "duration_s": {cmd_times[idx]:.6f},\n')
            f.write(f' "output": "{arg_output}"\n')
            f.write("}\n")

        f.write("]\n")
    Con.info("Written", len(commands), "commands to", filename)


def rawPathExists(cwd: str, path: str) -> bool:
    path = path.encode("latin1").decode("unicode_escape")
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.exists(path)
