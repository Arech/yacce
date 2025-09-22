"""Commonly needed data & code"""

import argparse
from collections import namedtuple
import enum
import os
import re
import rich.console
import rich.progress


class YacceException(RuntimeError):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)


# adapted from https://github.com/Arech/benchstats/blob/be9e925ae85b7dc1c19044ad5f6eddea681f9f77/src/benchstats/common.py#L56
class LoggingConsole(rich.console.Console):
    # @enum.verify(enum.CONTINUOUS)  # not supported by Py 3.10
    class LogLevel(enum.IntEnum):
        Trace = 0
        Debug = 1
        Info = 2
        Warning = 3
        Error = 4  # recoverable
        Failure = 5  # non-recoverable, can continue working
        Critical = 6  # non-recoverable, must abort

    def __init__(self, log_level: LogLevel = LogLevel.Trace, **kwargs):
        assert isinstance(log_level, LoggingConsole.LogLevel)
        self.log_level = log_level
        self._n_errors: int = 0
        if "emoji" not in kwargs:
            kwargs["emoji"] = False
        if "highlight" not in kwargs:
            kwargs["highlight"] = False
        super().__init__(**kwargs)

    def cleanNumErrors(self) -> int:
        r = self._n_errors
        self._n_errors = 0
        return r

    def getNumErrors(self) -> int:
        return self._n_errors

    def _do_log(self, color: str, lvl: str, *args, **kwargs):
        if "sep" in kwargs:
            sep = kwargs["sep"] if len(kwargs["sep"]) > 0 else " "
        else:
            sep = " "
            kwargs["sep"] = sep
        return super().print(f"[[{color}]{lvl:4s}[/{color}]]{sep}", *args, **kwargs)

    def will_log(self, level) -> bool:
        return self.log_level <= level

    def trace(self, *args, **kwargs):
        if self.log_level > LoggingConsole.LogLevel.Trace:
            return None
        return self._do_log("blue", "trce", *args, **kwargs)

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
        self._n_errors += 1
        if self.log_level > LoggingConsole.LogLevel.Error:
            return None
        return self._do_log("red", "Err", *args, **kwargs)

    def failure(self, *args, **kwargs):
        self._n_errors += 1
        if self.log_level > LoggingConsole.LogLevel.Failure:
            return None
        return self._do_log("bright_red", "FAIL", *args, **kwargs)

    def critical(self, *args, **kwargs):
        self._n_errors += 1
        if self.log_level > LoggingConsole.LogLevel.Critical:
            return None
        return self._do_log("bright_magenta", "CRIT", *args, **kwargs)

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
        # no default as it depends on the mode
    )

    parser.add_argument(
        "--ignore-not-found",
        help="If set, will not test if files to be added to .json exists. Default: %(default)s.",
        action=argparse.BooleanOptionalAction,
        default=False,
    )

    parser.add_argument(
        "-o",
        "--other_commands",
        help="If set, will also generate other_commands.json. This has similar format to "
        "compile_commands.json, but contain all other found compiler invocations (such as for compiling "
        "assembler sources, or for linking), that aren't useful for gathering C++ symbol information "
        "of the project, but handy to get insights about the build in general. Default: %(default)s",
        action=argparse.BooleanOptionalAction,
        default=False,
    )

    parser.add_argument(
        "--save_duration",
        help="If set, will add 'duration_s' field into a resulting .json that contain how long the command run in "
        "seconds with microsecond resolution. This currently doesn't have automated use, but can be inspected manually, or with a custom "
        "script to obtain build system performance insights. Default: %(default)s. Note that currently clangd gets upset "
        "when it encounters fields it doesn't know, so enabling this might prevent you from using clangd with the resulting file",
        action=argparse.BooleanOptionalAction,
        default=False,
    )

    parser.add_argument(
        "--save_line_num",
        help="If set, will add 'line_num' integer field into a resulting .json that contain a line "
        "number that reference the compiler call in the source file. Useful for debugging. "
        "Default: %(default)s. Note that currently clangd gets upset "
        "when it encounters fields it doesn't know, so enabling this might prevent you from using clangd with the resulting file",
        action=argparse.BooleanOptionalAction,
        default=False,
    )

    parser.add_argument(
        "--discard_outputs_with_pfx",
        help="A build system can compile certain dummy source files only to gather information about "
        "compiler capabilities. Typically, such files are placed into /tmp or /dev/null, but other variants are possible. "
        "This setting allows to fully customize detection of which prefixes in compiler's output file "
        "specification would lead to ignoring the compilation attempt. Default: %(default)s. "
        'Pass an empty string "" to disable.',
        nargs="*",
        default=["/dev/null", "/tmp/"],
    )

    parser.add_argument(
        "--discard_sources_with_pfx",
        help="Similar to --discard_outputs_with_pfx, but controls which prefixes of source files "
        "passed to a compiler, should lead to ignoring the compiler call. Default: %(default)s. "
        'Pass an empty string "" to disable.',
        nargs="*",
        default=[""],
    )

    parser.add_argument(
        "--discard_args_with_pfx",
        help="Certain compiler arguments, such as sanitizers, are known to choke clangd. Some others "
        "concerning build reproducibility are just useless for C++ symbols. Set a value "
        "of this parameter to a sequence of prefixes to match and remove such compiler arguments "
        'from the list. Default: %(default)s. Pass an empty string "" to disable.',
        nargs="*",
        default=[
            "-fsanitize"
        ],  # , "-frandom-seed=", "-D__DATE__=", "-D__TIMESTAMP__=", "-D__TIME__="],
    )

    parser.add_argument(
        "--discard_args",
        help="Similarly to --discard_args_with_pfx, values for this argument define a set of "
        "compiler arguments (such as '-DMY_DEF=VALUE') or pipe-delimited argument pairs (such as a "
        "single token value '-I|/certain/dir' defines a two token pair '-I /certain/dir') "
        "that will be removed from a compiler invocation. Note that a single token specification "
        "for -D compiler argument has a special handling and also addresses its two token alternatives. "
        "NOTE: since Python's argparse always treats a leading dash in CLI argument as an argument "
        "name, but not value, use a plus sign '+' instead of a dash '-' to specify leading dashes "
        "(so examples above become '+DMY_DEF=VALUE' and '+I|/certain/dir')"
        "Default: %(default)s. "
        'Pass an empty string "" to disable.',
        nargs="*",
        default=["+DADDRESS_SANITIZER"],
    )

    parser.add_argument(
        "--enable_dupes_check",
        help="If set, will provide a report if a pair <source, output> isn't unique. "
        "Usefulness of this flag solely depends on actual build system implementation. Some might "
        "use lots of temporary compilations just to gather compiler capabilities which could lead "
        "to an avalanche of false positives. This could be mitigated with --discard* family of flags, "
        "but this requires manual intervention. Default: %(default)s",
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
        help="Destination directory into which to create resulting .json files. Must exist."
        + addendums.get("dest_dir", ""),
        # no default as it depends on the mode
    )

    return parser


def warnClangdIncompatibilitiesIfAny(Con: LoggingConsole, args: argparse.Namespace) -> None:
    def _warnField(flag: str, fld_name: str):
        Con.warning(
            f"Each .json entry have a custom field '{fld_name}' that might be incompatible "
            f"with clangd. Don't use --{flag} CLI argument if you intend to feed resulting compile_commands.json to clangd."
        )

    if args.save_duration:
        _warnField("save_duration", "duration_s")

    if args.save_line_num:
        _warnField("save_line_num", "line_num")


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
# is_other:bool is the command is other command
# cmd_idx:int is the index of command in a corresponding list
ProcessProps = namedtuple(
    "ProcessProps", ("start_ts_us", "line_num", "is_other", "cmd_idx", "n_sources")
)
# args:list[str], output:str|None, source:str, line_num:int
CompileCommand = namedtuple("CompileCommand", ("args", "output", "source", "line_num"))
OtherCommand = namedtuple("OtherCommand", ("args", "output", "line_num"))


class BaseParser:
    kOutputArgs = ("-o", "--output")
    # List of a compiler arguments that should contain a file/dir path.
    # Note, we're interested only in args that are important for C++ symbols extraction
    # https://gcc.gnu.org/onlinedocs/gcc/Directory-Options.html
    # https://clang.llvm.org/docs/ClangCommandLineReference.html#diagnostic-options
    kArgIsPath = frozenset(
        kOutputArgs
        + (
            "-I",
            "-iquote",
            "-isystem",
            "-idirafter",
            "-isysroot",
            "--include-directory",
            "--include-directory-after--sysroot",
            "-cxx-isystem",
            "--library-directory",
            "-imacros",
            "--imacros",
            "-include",
            "--include",
        )
    )

    # List of a compiler arguments that could start with = or $SYSROOT specification.
    # https://gcc.gnu.org/onlinedocs/gcc/Directory-Options.html#index-I
    kCheckArgForSysrootSpec = frozenset((
        "-I",
        "-iquote",
        "-isystem",
        "-idirafter",
        "--include-directory",
    ))
    kSysrootSpec = ("=", "$SYSROOT")

    # list of a compiler arguments that should contain a path as a suffix.
    kPfxArgIsPath = (
        "-L",
        "--sysroot=",
        "-F",
        "--output=",
        "--embed-dir=",
        "--include-directory=",
        "-cxx-isystem",
        "-I",
        "-idirafter",
        "--include-directory-after=",
        "-imacros",
        "--imacros",
        "--imacros=",
        "-include",
        "--include",
        "--include=",
        "-iquote",
        "-isysroot",
        "-isystem",
        "-isystem-after",
        "-stdlib++-isystem",
        "--library-directory=",
    )
    r_pfx_arg_is_path = re.compile(r"^(" + "|".join(re.escape(s) for s in kPfxArgIsPath) + r")")

    # list of important for compilation, but unsupported (yet?) arguments.
    kArgUnsupported = frozenset((
        "-iprefix",
        "--include-prefix",
        "-iwithprefix",
        "-iwithprefixbefore",
        "--include-with-prefix",
        "--include-with-prefix-after",
        "--include-with-prefix-before",
        "-working-directory",
        "--system-header-prefix",
    ))
    kPfxArgUnsupported = (
        "-working-directory=",
        "-iprefix",
        "--include-prefix=",
        "-iwithprefix",
        "--include-with-prefix-after=",
        "--include-with-prefix=",
        "-iwithprefixbefore",
        "--include-with-prefix-before=",
        "-iwithsysroot",
        "--system-header-prefix=",
        "--no-system-header-prefix=",
    )

    # To properly find source files in compiler's args, we would have to implement a complete parser,
    # which is infeasible. We use an extension/suffix based heuristic and a flags blacklist instead.
    # We aren't interested in all sources, but only those contributing information visible from C++.
    # https://gcc.gnu.org/onlinedocs/gcc/Overall-Options.html
    kExtOfSource = (
        ".c",
        ".i",
        ".ii",
        ".m",
        ".mi",
        ".mm",
        ".M",
        ".mii",
        ".cc",
        ".cp",
        ".cxx",
        ".cpp",
        ".CPP",
        ".c++",
        ".C",
        # C++20 modules
        ".ixx",
        ".cppm",
        ".cxxm",
        ".c++m",
        ".ccm",
    )

    # if a path/file argument is preceded by any of the following flags, the arg is not a source
    kArgIsNotSource = frozenset(("-main-file-name",))

    # If any of these arguments present in a compiler invocation, then the whole invocation should
    # be ignored (information querying arguments)
    # isolated dash is for compilation from stdin - there's nothing we can do with it
    kArgToIgnoreInvocation = frozenset(("--version", "-"))
    # when there's only one arg and it matches anything below, ignore the invocation
    kSoleArgToIgnoreInvocation = frozenset(("-v", "--verbose"))
    # if an arg starts with these substring, the invocation will be ignored
    kArgStartsWithIgnoreInvocation = ("-print", "--print")
    # with the exception of the below args
    kArgStartsWithIgnoreInvocationException = ("--print-missing-file-dependencies",)

    # greedy match repeatedly blocks ending on escaped quote \" literal, or that doesn't contain
    # quotes at all until first unescaped quote
    @staticmethod
    def _makeRInQuotes(capture_inner: bool, no_begin_end: bool) -> str:
        not_word_backslash = r"(?<=\W)(?<!\\)"
        return (
            # either start of string, or NOT a word, or not a backslash
            (not_word_backslash if no_begin_end else r"(?:^|" + not_word_backslash + ")")
            + r"(?P<quote>['\"])"  # starting quote
            + ("(" if capture_inner else "(?:")  # start of capture/group
            # WARNING: never end a comment inside extended regexp with a backslash, even in raw
            # strings - it'll be treated as line continuation char
            + r"""
            (?:(?:(?!(?P=quote)).)*(?:\\(?P=quote))*)*  # any sequence of not quotes and escaped quotes
            (?<!\\)         # no escaping backslash in front of the ending quote
            )               # end of capture/group
            (?P=quote)      # ending quote
            """
            # after the quote either end of string, or NOT a word
            + (r"(?=\W)" if no_begin_end else r"(?:$|(?=\W))")
        )

    _r_in_quotes = re.compile(_makeRInQuotes(True, False), re.VERBOSE)
    # greedy match [] with any chars inside of ""
    _r_in_braces = re.compile(r"^\[(?:[, ]*" + _makeRInQuotes(False, True) + r")*\]", re.VERBOSE)
    # note that dependency of _r_in_braces on essentially _r_in_quotes definition is important as
    # it allows to produce a string on which we can safely match individual arguments with _r_in_quotes.findall()
    # in a batch, instead one by one to ensure correctness of parsing.

    """ from https://gcc.gnu.org/onlinedocs/gcc/Overall-Options.html:
    Options in file are separated by whitespace. A whitespace character may be included
    in an option by surrounding the entire option in either single or double quotes.
    Any character (including a backslash) may be included by prefixing the character to
    be included with a backslash. The file may itself contain additional @file options;
    any such options will be processed recursively."""
    # _r_options = re.compile(
    #    r"([^\s'\"]+|'(?:(?:[^']*\\'|[^']*)*)'|\"(?:(?:[^\"]*\\\"|[^\"]*)*)\")(?:\s+|$)"
    # )
    _r_options = re.compile(
        r"""\s*
        (?:
        ([^\s'\"]+) |
        (?:"""
        + _makeRInQuotes(True, True)
        + r""")
        )
        (?:\s+|$)""",
        re.VERBOSE,
    )

    # ending of execve() line
    _r_execve_end = re.compile(r"\)\s*=\s*0\s*$")

    def __init__(self, Con: LoggingConsole, args: argparse.Namespace) -> None:
        self.Con = Con
        self._compilers = args.compiler
        self._do_other = args.other_commands
        self._cwd = os.path.realpath(args.cwd)
        self._test_files = not args.ignore_not_found
        self._discard_outputs_with_pfx = tuple(
            s for s in args.discard_outputs_with_pfx if len(s) > 0
        )
        self._discard_sources_with_pfx = tuple(
            s for s in args.discard_sources_with_pfx if len(s) > 0
        )
        self._discard_args_with_pfx = tuple(s for s in args.discard_args_with_pfx if len(s) > 0)
        self._discard_args = self._makeDiscardArgs(Con, args.discard_args)
        self._do_dupes_check = args.enable_dupes_check

        if self._test_files and not os.path.isdir(self._cwd):
            Con.warning(
                "Compilation directory '",
                self._cwd,
                "' doesn't exist. If you used --cwd option, check its correctness. "
                "Resulting json will likely be invalid.",
            )

        self._running_pids: dict[int, ProcessProps] = {}

        self.compile_commands: list[CompileCommand] = []
        self.compile_cmd_time: list[float] = []
        self.other_commands: list[OtherCommand] = []
        self.other_cmd_time: list[float] = []
        # errors = {} # error_code -> array of line_idx where it happened

        self._parseLog(args.log_file)

    @staticmethod
    def _makeDiscardArgs(
        Con: LoggingConsole, discard_args: list[str] | None
    ) -> dict[str, str | None]:
        if not discard_args:
            return {}
        ret: dict[str, str | None] = {}

        def _upd(n, v=None):
            nonlocal ret
            if n in ret and ret[n] != v:
                Con.warning(
                    "in parsing --discard_args: argument",
                    n,
                    "is already specified with value",
                    ret[n],
                    ". The value is replaced with",
                    v,
                )
            ret[n] = v

        for da in discard_args:
            if da.startswith("++"):
                da = "--" + da[2:]
            elif da.startswith("+"):
                da = "-" + da[1:]

            splt = da.split("|", maxsplit=1)
            if len(splt) == 1:
                if da.startswith("-D"):
                    _upd("--define-macro=" + da[2:])
                _upd(da)
            else:
                assert len(splt) == 2
                name, value = splt
                if name == "-D":
                    _upd("--define-macro", value)
                _upd(name, value)

        return ret

    def _parseLog(self, log_file: str) -> None:
        # match the start of the log string: (<pid>) (<time.stamp>) (execve|execveat|exited...)
        r_exec_or_exit = re.compile(
            r"^(?P<pid>\d+)\s+(?P<unix_ts>\d+)\.(?P<unix_ts_ms>\d+)\s+(?P<call>execve|execveat|\+\+\+ exited with (?P<exit_code>\d+) \+\+\+)"
        )

        # maps source->{output: (args_str, line_num)} to verify that commands are unique
        self._seen_compile: dict[str, dict[str | None, tuple[str, int]]] = {}
        self._seen_other: dict[str | None, tuple[str, int]] = {}  # just output->(args_str,line_num)

        self._unsupported_args = set()  # set of found unsupported args
        self._num_dropped_args_by_pfx = 0  # for a report on how many args by pfx were dropped
        self._num_dropped_args_by_match = 0

        with rich.progress.open(
            log_file, "r", description="Parsing strace log file...", console=self.Con
        ) as file:
            # sometimes strace breaks reporting of a single execve() call in two lines. All known
            # cases of that have the first line ending on `<unfinished ...>` literal and the next
            # line starting with `)` literal. We have to handle this
            unfinished_line: str | None = None
            unfinished_args: tuple | None = None
            for line_idx, line in enumerate(file):
                line = line.strip()  # dropping line-ends and other whitespaces
                # handling line continuations
                if unfinished_line is not None:
                    prev_line = unfinished_line
                    unfinished_line = None
                    if line.startswith(")"):
                        self._handleExec(*unfinished_args, prev_line + line)
                        continue
                    else:
                        self.Con.error(
                            "Line",
                            line_idx + 1,
                            "has unexpected continuation pattern. Treating this and prev lines as independent.",
                        )
                        self._handleExec(*unfinished_args, prev_line)
                        # falldown to processing below

                match_exec_or_exit = r_exec_or_exit.match(line)
                if not match_exec_or_exit:
                    unfinished_line = None
                    continue  # nothing to do here

                pid = int(match_exec_or_exit.group("pid"))
                ts = float(match_exec_or_exit.group("unix_ts")) + float(
                    1e-6 * int(match_exec_or_exit.group("unix_ts_ms"))
                )
                call = match_exec_or_exit.group("call")
                exit_code = match_exec_or_exit.group("exit_code")  # could be None

                if call.startswith("+++ "):
                    unfinished_line = None
                    if pid not in self._running_pids:
                        continue  # this must be not a process we care about
                    self._handleExit(pid, ts, exit_code, line_idx + 1)
                else:
                    # handle execve/execveat here
                    if line.endswith("<unfinished ...>"):
                        unfinished_line = line[match_exec_or_exit.end() :]
                        unfinished_args = (call, pid, ts, line_idx + 1)
                        self.Con.trace(
                            "Line",
                            line_idx + 1,
                            "pid",
                            pid,
                            "is unfinished. Deferring processing to the next line.",
                        )
                    else:
                        self._handleExec(
                            call, pid, ts, line_idx + 1, line[match_exec_or_exit.end() :]
                        )
            if unfinished_line is not None:
                self.Con.error(
                    "Previous line is marked as unfinished, but this was the last line. Trying to handle it"
                )
                self._handleExec(*unfinished_args, unfinished_line)

        # finishing unfinished processes
        for pid in list(self._running_pids.keys()):  # must rematerialize since exit() deletes them
            self._handleExit(pid, 0.0, None, 0)

        assert 0 == len(self._running_pids)
        n_cc = len(self.compile_commands)
        n_lc = len(self.other_commands)
        if n_cc == 0 and n_lc == 0:
            self.Con.print(
                "No compiler invocation were found in the log. If you're using a custom compiler, pass it in --compiler option."
            )
        else:
            self.Con.print(n_cc, "compilation commands found")
            if self._do_other:
                self.Con.print(n_lc, "other commands found")

        if self._num_dropped_args_by_pfx:
            self.Con.info(
                "In total ",
                self._num_dropped_args_by_pfx,
                "compiler arguments were removed according to the following --discard_args_with_pfx specification:",
                self._discard_args_with_pfx,
            )

        if self._num_dropped_args_by_match:
            self.Con.info(
                "In total ",
                self._num_dropped_args_by_match,
                "compiler arguments were removed according to the following --discard_args specification:",
                self._discard_args,
            )

        if self._unsupported_args:
            self.Con.warning(
                f"Found use of {len(self._unsupported_args)} unsupported arguments: {sorted(self._unsupported_args)}. "
                "These were ignored even though they might affect correctness of the resulting .json. "
                "If you think these should be supported, consider making a PR or reporting an issue."
            )

        # cleanup
        del self._num_dropped_args_by_match
        del self._num_dropped_args_by_pfx
        del self._unsupported_args
        del self._seen_other
        del self._seen_compile

    def _handleExit(self, pid: int, ts: float, exit_code: str | None, line_num: int) -> None:
        # negative exit code means the process termination was not found in the log
        (start_ts, start_line_num, is_other, cmd_idx, n_sources) = self._running_pids[pid]

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
                    "and the resulting .json might be incomplete."
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
                "This might mean the log file is incomplete and hence so is the resulting .json."
            )

        duration = ts - start_ts if is_exit_logged else 0.0
        if is_other:
            self.other_cmd_time[cmd_idx] = duration
        else:
            self.compile_cmd_time[cmd_idx : cmd_idx + n_sources] = [duration] * n_sources

        del self._running_pids[pid]

    def _testPathExists(self, arg: str, line_num: int, pid: int, args_str: str) -> None:
        if self._test_files and not unescapedPathExists(self._cwd, arg):
            self.Con.warning(
                f"Line {line_num}: pid {pid} uses argument '{arg}' "
                "which doesn't exist. This might mean the build system is misconfigured "
                "or the log file is incomplete and hence so is the resulting .json. "
                f"Full command args are: {args_str}"
            )

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

        # this search is very slow, but it can't be simplified and it's an important diagnostics feature.
        if not self._r_execve_end.search(line):
            self.Con.warning(f"Line {line_num}: pid {pid}: unexpected end of '{line}'.")

        # extract the first argument of execve, which is the executable path
        match_filepath = self._r_in_quotes.match(line[1:])
        assert match_filepath, (
            f"Line {line_num}: pid {pid} made call {call} but the argument '{line}' can't be parsed. "
            "The log file is malformed or _r_in_quotes regexp is incorrect"
        )

        # unescaping quotes and other symbols.
        compiler_path = unescapePath(match_filepath.group(2))
        if (
            compiler_path not in self._compilers.fullpaths
            and os.path.basename(compiler_path) not in self._compilers.basenames
        ):
            return  # not a compiler we care about

        # finding execv() args in the rest of the line
        args_start_pos = match_filepath.end() + 3
        # +1 since match_filepath is matched on args[1:]
        assert line[match_filepath.end() + 1 : args_start_pos + 1] == ", [", (
            f"Unexpected format of the {call} syscall in the log file"
        )
        # we can't simply search for the closing ] because there might be braces in file names and
        # they don't have to be shell-escaped
        match_args = self._r_in_braces.match(line[args_start_pos:])
        assert match_args, (
            f"Line {line_num}: pid {pid} made call {call} but the arguments array couldn't be parsed. "
            "The log file is malformed or _r_in_braces regexp is incorrect"
        )

        args_str = match_args.group()

        # Extracting args from the args_str. We can't simply split by ", " because there might be
        # such sequence in file names. So we use the same rInQuotes regexp to extract them one by one.
        # In a sense, it's a duplication of application of the same regexp as above, but we must
        # scope the search to the inside of the braces only
        args = re.findall(self._r_in_quotes, args_str)
        args = [inner for _, inner in args]

        if self._shouldIgnoreInvocation(args, line_num, pid, args_str):
            return
        args = self._expandAtFile(args, line_num, pid)

        # now walking over the args and checking existence of those that we know to be files or dirs.
        # Also getting arguments of some important options, if they are present
        next_is_path = False
        next_is_output = False
        arg_output = None
        sources: list[str] = []
        discard_arg_idx: list[int] = []

        skip_next = False
        for idx, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            elif next_is_path:  # no point to check anything if it's set
                next_is_path = False
                self._testPathExists(arg, line_num, pid, args_str)
                if next_is_output:
                    next_is_output = False
                    if arg_output is not None:
                        self.Con.warning(
                            f"Line {line_num}: pid {pid} uses multiple output options. "
                            f"This is unusual, taking the last one. Full command args are: {args_str}"
                        )
                    arg_output = arg  # it's already escaped
                continue
            elif self._discard_args_with_pfx and arg.startswith(self._discard_args_with_pfx):
                # argument blacklist is the first check
                self._num_dropped_args_by_pfx += 1
                discard_arg_idx.append(idx)
                continue
            elif arg in self._discard_args:
                discard_val = self._discard_args[arg]
                if discard_val is None:
                    self._num_dropped_args_by_match += 1
                    discard_arg_idx.append(idx)
                    continue
                else:
                    if idx + 1 < len(args) and discard_val == args[idx + 1]:
                        self._num_dropped_args_by_match += 2
                        discard_arg_idx.extend([idx, idx + 1])
                        skip_next = True
                        continue
                    # else fall below

            if arg in self.kArgIsPath and (
                arg not in self.kCheckArgForSysrootSpec or not arg.startswith(self.kSysrootSpec)
            ):  # do nothing for sysroot spec, as it's a subdir of tested -sysroot
                next_is_path = True
                if arg in self.kOutputArgs:
                    next_is_output = True
            elif m_pfx_arg := self.r_pfx_arg_is_path.match(arg):
                path_part = arg[m_pfx_arg.end() :]
                if path_part:
                    self._testPathExists(path_part, line_num, pid, args_str)
                    if "--output=" == m_pfx_arg.group(1):
                        if arg_output is not None:
                            self.Con.warning(
                                f"Line {line_num}: pid {pid} uses multiple output options. "
                                f"This is unusual, taking the last one. Full command args are: {args_str}"
                            )
                        arg_output = path_part  # it's already escaped
                else:
                    self.Con.error(
                        f"Line {line_num}: pid {pid} uses '{arg}' argument without a path. "
                        f"This is likely a bug. Full command args are: {args_str}"
                    )
            elif arg in self.kArgUnsupported or arg.startswith(self.kPfxArgUnsupported):
                self._unsupported_args.add(arg)
            elif (
                arg.endswith(self.kExtOfSource)
                and idx > 0
                and args[idx - 1] not in self.kArgIsNotSource
            ):
                sources.append(arg)

        if discard_arg_idx:
            for i in reversed(discard_arg_idx):
                del args[i]

        n_sources = len(sources)
        is_other = n_sources == 0

        if not self._do_other and is_other:
            return  # not interested in other commands

        if arg_output is None:
            if self._do_dupes_check:
                # arg_output could be not specified at all in which case there're rules for making a default
                # value for it, The problem is, the rules are based on input files and we can reliably parse
                # only a subset of possible inputs.
                self.Con.warning(
                    f"Line {line_num} pid {pid}: output wasn't explicitly set for a call '{args_str}'."
                    "\nOutput based duplicate checks might yield false positives."
                )
        else:
            if self._discard_outputs_with_pfx and arg_output.startswith(
                self._discard_outputs_with_pfx
            ):
                self.Con.trace(
                    f"Line {line_num} pid {pid}: call '{args_str}' is ignored due to args.discard_outputs_with_pfx"
                )
                return

        if n_sources > 1:
            self.Con.warning(
                f"Line {line_num} pid {pid}: call '{args_str}' has {n_sources} sources"
            )

        if self._discard_sources_with_pfx:
            sources = [s for s in sources if not s.startswith(self._discard_sources_with_pfx)]
            if not sources:
                self.Con.trace(
                    f"Line {line_num} pid {pid}: call '{args_str}' is ignored due to args.discard_sources_with_pfx"
                )
                return

        # TODO: do we need to fix the first argument in args to be the same as the one used in
        # execve()? It might be different depending how execve() was called.
        if is_other:
            if self._do_dupes_check:
                self._checkSameOther(args_str, line_num, arg_output)
            self.other_commands.append(OtherCommand(args, arg_output, line_num))
            cmd_idx = len(self.other_cmd_time)
            self.other_cmd_time.append(0.0)
        else:
            cmd_idx = len(self.compile_cmd_time)
            for src in sources:
                if self._do_dupes_check:
                    self._checkSameCompile(args_str, line_num, arg_output, src)
                self.compile_commands.append(CompileCommand(args, arg_output, src, line_num))
                self.compile_cmd_time.append(0.0)

        self._running_pids[pid] = ProcessProps(ts, line_num, is_other, cmd_idx, n_sources)

    def _shouldIgnoreInvocation(
        self, args: list[str], line_num: int, pid: int, args_str: str
    ) -> bool:
        args_len = len(args)
        if args_len <= 1:
            self.Con.error(
                f"Line{line_num} pid{pid}: invocation of a compiler '{args_str}' doesn't have arguments. Ignoring it",
            )
            return True

        if args_len == 2 and args[1] in self.kSoleArgToIgnoreInvocation:
            self.Con.trace(
                f"Line{line_num} pid{pid}: invocation '{args_str}' is ignored due to a sole arg in kSoleArgToIgnoreInvocation"
            )
            return True

        if any(1 for a in args if a in self.kArgToIgnoreInvocation):
            self.Con.trace(
                f"Line{line_num} pid{pid}: invocation '{args_str}' is ignored due to an arg in kArgToIgnoreInvocation"
            )
            return True

        if any(
            1
            for a in args
            if a.startswith(self.kArgStartsWithIgnoreInvocation)
            and a not in self.kArgStartsWithIgnoreInvocationException
        ):
            self.Con.trace(
                f"Line{line_num} pid{pid}: invocation '{args_str}' is ignored due to an arg satisfying kArgStartsWithIgnoreInvocation"
            )
            return True

        return False

    def _expandAtFile(self, args: list[str], line_num: int, pid: int) -> list[str]:
        at_idxs = [i for i, s in enumerate(args) if s.startswith("@")]
        added = 0
        for i in at_idxs:
            fname = toAbsPathUnescape(self._cwd, args[i][1:])
            if os.path.isfile(fname):
                fsize = os.path.getsize(fname)
                if fsize > 64 * 1024:  # randomly sufficient threshold
                    self.Con.info(
                        f"Line {line_num}: pid {pid} has @file argument#{i} '{args[i]}' that "
                        "points to a file of size",
                        fsize,
                        ". That seems a bit too much, perhaps something is wrong?",
                    )
                with open(fname, "r") as file:
                    file_content = file.read()
                # self.Con.debug("@file", args[i], "  -->  ", file_content)
                newargs = []
                ofs = 0
                # have to sequentially match one after the other to ensure everything is parsed as
                # expected. Doing .findall() will just skip parts that don't match and this would
                # miss bugs
                m = self._r_options.match(file_content)
                while m:
                    noq, inq = m.group(1, 3)
                    assert int(noq is None) + int(inq is None) == 1
                    newargs.append(noq if noq is not None else inq)
                    ofs += m.end()
                    m = self._r_options.match(file_content[ofs:])
                if len(file_content) != ofs:
                    self.Con.error(
                        f"Line {line_num}: pid {pid} has @file argument#{i} '{args[i]}' "
                        "parsing of which ended prematurely."
                    )
                # self.Con.debug(newargs)
                newargs = self._expandAtFile(newargs, line_num, pid)  # recursive expansion
                # ^^ might produce puzzling messages, but that's a TODO for later

                ni = added + i
                added += len(newargs) - 1
                args[ni:ni] = newargs
                del args[added + i + 1]
                # self.Con.debug(args)

            else:
                self.Con.error(
                    f"Line {line_num}: pid {pid} has @file argument#{i} '{args[i]}' that doesn't "
                    "reference existing file. Processing might yield incomplete results."
                )
                # do nothing
        return args

    def _checkSameCompile(
        self, arg_str: str, line_num: int, arg_output: str | None, arg_compile: str
    ) -> bool:
        if arg_compile in self._seen_compile:
            outp = self._seen_compile[arg_compile]
            if arg_output in outp:
                prev_args, prev_line = outp[arg_output]
                output_repr = "<<not_determined>>" if arg_output is None else arg_output
                if arg_str == prev_args:
                    self.Con.warning(
                        "For source '",
                        arg_compile,
                        "' the same output '",
                        output_repr,
                        "' is produced by the second instance of the ~same compilation command vvv\n",
                        arg_str,
                        f"\n^^^. First line was @line#{prev_line}, now it's line#{line_num}. "
                        "This might be benign, but this isn't normal for a correct build system. ",
                    )
                    return True
                else:
                    self.Con.error(
                        "For source '",
                        arg_compile,
                        "' the same output '",
                        output_repr,
                        f"' is generated by different compilation commands: the first recorded was @line#{prev_line} vvv\n",
                        prev_args,
                        f"\n^^^ and now @line#{line_num} it's vvv\n",
                        arg_str,
                        "\n^^^. This isn't normal and could mean that several build systems were "
                        "executed.",
                    )
            else:
                outp[arg_output] = (arg_str, line_num)
        else:
            self._seen_compile[arg_compile] = {arg_output: (arg_str, line_num)}
        return False

    def _checkSameOther(self, arg_str: str, line_num: int, arg_output: str | None) -> bool:
        if arg_output in self._seen_other:
            prev_args, prev_line = self._seen_other[arg_output]
            output_repr = "<<not_determined>>" if arg_output is None else arg_output
            if arg_str == prev_args:
                self.Con.warning(
                    "The same output '",
                    output_repr,
                    "' is produced by the second instance of the same other command vvv\n",
                    arg_str,
                    f"\n^^^. First was @line#{prev_line}, now it's line#{line_num}. "
                    "This might be benign, but this isn't normal for a correct build system. ",
                )
                return True
            else:
                self.Con.error(
                    "The same output '",
                    output_repr,
                    f"' is generated by different other commands: the first recorded was @line#{prev_line} vvv\n",
                    prev_args,
                    f"\n^^^ and now @line#{line_num} it's vvv\n",
                    arg_str,
                    "\n^^^. This isn't normal and could mean that several build systems were "
                    "executed.",
                )
        else:
            self._seen_other[arg_output] = (arg_str, line_num)
        return False

    def storeJsons(
        self, dest_dir: str, save_duration: bool, save_line_num: bool, sfx: str = ""
    ) -> None:
        if not os.path.isdir(dest_dir):
            raise YacceException(f"Destination directory '{dest_dir}' doesn't exist")

        storeJson(
            self.Con,
            dest_dir,
            True,
            self.compile_commands,
            self.compile_cmd_time if save_duration else None,
            self._cwd,
            save_line_num,
            file_sfx=sfx,
        )
        if self._do_other:
            storeJson(
                self.Con,
                dest_dir,
                False,
                self.other_commands,
                self.other_cmd_time if save_duration else None,
                self._cwd,
                save_line_num,
                file_sfx=sfx,
            )


def storeJson(
    Con: LoggingConsole,
    path: str,
    is_compile_commands: bool,
    commands: list[CompileCommand] | list[OtherCommand],
    cmd_times: list[float] | None,
    cwd: str,
    save_line_num: bool,
    file_sfx="",
):
    filename = os.path.join(
        path, ("compile" if is_compile_commands else "other") + f"_commands{file_sfx}.json"
    )
    if os.path.exists(filename):
        os.remove(filename)

    if not commands:
        assert not cmd_times
        Con.info("storeJson() got empty list to save into '", filename, "'")
        return

    save_duration = cmd_times is not None
    assert not save_duration or len(commands) == len(cmd_times)

    e = next(iter(commands))
    is_other = isinstance(e, OtherCommand)
    assert is_other or isinstance(e, CompileCommand)
    assert int(is_other) + int(is_compile_commands) == 1

    cwd = cwd.replace('"', '\\"')
    with open(filename, "w") as f:
        f.write("[\n")
        for idx, cmd_tuple in enumerate(commands):
            f.write(("," if idx > 0 else "") + "{\n")
            f.write(f' "directory": "{cwd}",\n')
            if is_other:
                args, arg_output, line_num = cmd_tuple
            else:
                args, arg_output, arg_compile, line_num = cmd_tuple
                f.write(f' "file": "{arg_compile}",\n')

            if save_line_num:
                f.write(f' "line_num": {line_num},\n')
            if save_duration:
                f.write(f' "duration_s": {cmd_times[idx]:.6f},\n')
            if arg_output is not None:
                f.write(f' "output": "{arg_output}",\n')

            args_str = '", "'.join(args)
            f.write(f' "arguments": ["{args_str}"]\n')
            f.write("}\n")

        f.write("]\n")
    Con.print("Written", len(commands), "commands to", filename)


def unescapePath(path: str) -> str:
    # not sure this is correct
    return path.encode("latin1").decode("unicode_escape")


def escapePath(path: str) -> str:
    return path.encode("unicode_escape").decode("latin1")


def toAbsPathUnescape(cwd: str, path: str) -> str:
    path = unescapePath(path)
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.realpath(path)  # resolve symlinks so isfile() or isdir() works properly


def unescapedPathExists(cwd: str, path: str) -> bool:
    return os.path.exists(toAbsPathUnescape(cwd, path))
