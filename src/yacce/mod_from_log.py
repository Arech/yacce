import argparse
import os

from .common import LoggingConsole, kMainDescription


def fromLogArgs(args: argparse.Namespace, unparsed_args: list):
    parser = argparse.ArgumentParser(
        prog="yacce from_log",
        description=kMainDescription
        + "\n\nMode 'from_log' tries to generate compile_commands.json from a strace log file.\n"
        "WARNING: this mode is intended for debugging purposes only and most likely will not "
        "produce a correct compile_commands.json due to a lack of information about the build system.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("log_file", help="Path to the strace log file to parse.", type=str)

    parser.add_argument(
        "--dir",
        help="Absolute path of working directory of the compilation. This value goes to 'directory' field of an "
        "entry of compile_commands.json and is used to resolve relative paths found in the command. "
        "yacce will try to test if mentioned files exist in this directory and warn if they aren't, "
        "but this alone doesn't guarantee that the resulting compile_commands.json will be correct. "
        "Default: directory of the log file.",
        type=str,
    )

    parser.add_argument(
        "--ignore-not-found",
        help="If set, will not test if files to be added to compile_commands.json exist. Default: %(default)s",
        action=argparse.BooleanOptionalAction,
        default=False,
    )

    return parser.parse_args(unparsed_args, namespace=args)


def mode_from_log(Con: LoggingConsole, args: argparse.Namespace, unparsed_args: list) -> int:
    args = fromLogArgs(args, unparsed_args)
    if args.log_file is None or not os.path.isfile(args.log_file):
        Con.critical("Log file is not specified or does not exist.")
        return 1
    Con.info(f"Parsing log file: {args.log_file}")

    cdir = os.path.realpath( args.dir if args.dir else os.path.dirname(args.log_file) )
    Con.info(f"dir = {cdir}")

    return 0
