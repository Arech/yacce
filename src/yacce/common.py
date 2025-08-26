"""Commonly needed data & code"""

import argparse
from collections import namedtuple
import enum
import os
import rich.console


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
        help="Absolute path of working directory of the compilation. If starts with a literal "
        "'%%LOG%%', the literal part is replaced with the directory of the log file. "
        "This value goes to 'directory' field of an "
        "entry of compile_commands.json and is used to resolve relative paths found in the command. "
        "yacce will try to test if mentioned files exist in this directory and warn if they aren't, "
        "but this alone doesn't guarantee that the resulting compile_commands.json will be correct."
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
        type=str,
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


def storeJson(
    filename: str, commands: list[tuple], cmd_times: list[float], cwd: str, is_link: bool
):
    cwd = cwd.replace('"', '"')
    with open(filename, "w") as f:
        f.write("[\n")
        for idx, cmd_tuple in enumerate(commands):
            f.write(("," if idx > 0 else "") + "{\n")
            f.write(f' "directory": "{cwd}",\n')
            if is_link:
                args_str, arg_output = cmd_tuple
            else:
                args_str, arg_output, arg_compile = cmd_tuple
                f.write(f' "file": "{arg_compile}",\n')

            f.write(f' "arguments": {args_str},\n')
            f.write(f' "output": "{arg_output}",\n')
            f.write(f' "duration_s": {cmd_times[idx]:.6f}\n')
            f.write("}\n")

        f.write("]\n")


def rawPathExists(cwd: str, path: str) -> bool:
    path = path.encode("latin1").decode("unicode_escape")
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.exists(path)
