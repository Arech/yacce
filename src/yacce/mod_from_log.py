import argparse
from collections import namedtuple
import os
import re

from .common import (
    addCommonCliArgs,
    BaseParser,
    kMainDescription,
    LoggingConsole,
    makeCompilersSet,
    warnClangdIncompatibilitiesIfAny,
    YacceException,
)


def _fixCwdArg(Con: LoggingConsole, args: argparse.Namespace) -> argparse.Namespace:
    """Fixes the --cwd argument if it's relative path spec.
    If --cwd is not set, returns the directory of the log file.
    Also tests existence of the directory if it is set and not ignored, and modifies args.ignore_not_found
    if the directory doesn't exist.
    """
    assert isinstance(args, argparse.Namespace) and hasattr(args, "ignore_not_found")
    assert hasattr(args, "log_file") and isinstance(args.log_file, str)

    if hasattr(args, "cwd") and args.cwd:
        cwd = (
            args.cwd if os.path.isabs(args.cwd) else os.path.dirname(args.log_file) + "/" + args.cwd
        )
    else:
        cwd = os.path.dirname(args.log_file)

    cwd = os.path.realpath(cwd)
    if not args.ignore_not_found and not os.path.isdir(cwd):
        Con.warning(
            f"Working directory '{cwd}' does not exist, will not check file existence. "
            "Resulting compile_commands.json will likely be incorrect."
        )
        setattr(args, "ignore_not_found", True)

    setattr(args, "cwd", cwd)
    return args


def _getArgs(
    Con: LoggingConsole, args: argparse.Namespace, unparsed_args: list
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yacce from_log",
        description=kMainDescription
        + "\n\nMode 'from_log' tries to generate compile_commands.json from a strace log file.\n"
        "WARNING: this mode is intended for debugging purposes only and most likely will not "
        "produce a correct compile_commands.json due to a lack of information about the build system.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("log_file", help="Path to the strace log file to parse.", type=str)
    parser = addCommonCliArgs(
        parser,
        {
            "cwd": " Relative path specification is always resolved to "
            "the absolute path using directory of the log file. "
            "Default: directory of the log file.",
            "dest_dir":" Default: current working directory."
        },
    )
    args = parser.parse_args(unparsed_args, namespace=args)

    if args.log_file is None or not os.path.isfile(args.log_file):
        raise YacceException(f"Log file '{args.log_file}' is not specified or does not exist.")
    
    if not args.dest_dir:
        args.dest_dir = os.getcwd()

    args = _fixCwdArg(Con, args)
    setattr(args, "compiler", makeCompilersSet(args.compiler))
    return args


def mode_from_log(Con: LoggingConsole, args: argparse.Namespace, unparsed_args: list) -> int:
    args = _getArgs(Con, args, unparsed_args)

    p = BaseParser(
        Con, args.log_file, args.cwd, not args.ignore_not_found, args.compiler, args.other_commands
    )

    p.storeJsons(args.dest_dir, args.save_duration, args.save_line_num)    
    return 0
