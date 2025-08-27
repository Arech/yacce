import argparse
import os

from .common import (
    addCommonCliArgs,
    CompilersTuple,
    kMainDescription,
    LoggingConsole,
    makeCompilersSet,
    rawPathExists,
    storeJson,
)


def _getArgs(
    Con: LoggingConsole, args: argparse.Namespace, unparsed_args: list
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yacce bazel",
        description=kMainDescription
        + "\n\nMode 'bazel' is intended to generate compile_commands.json from tracing execution of "
        "invocation of 'bazel build' or similar command, using Linux's strace utility. This mode uses "
        "some knowledge of how bazel works to produce a correct output.",
        usage="yacce [global options] [bazel] [options (see below)] [-- shell command eventually invoking bazel]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--log_file",
        help="Use this file path template for the strace log. See also '--from_log'. Default: %(default)s",
        default=os.path.join(os.getcwd(), "strace.txt"),
    )

    mut_ex_group = parser.add_mutually_exclusive_group()

    mut_ex_group.add_argument(
        "--from_log",
        help="Toggle a mode in which yacce will only parse the log specified in --log_file, but "
        "will not invoke any build system on its own. Mutually exclusive with --keep_log.",
        action="store_true",
    )

    mut_ex_group.add_argument(
        "--keep_log",
        choices=["if_failed", "always", "never"],
        help="Determines what to do with the log file after building, generation and parsing of the "
        "log file finishes. Default is 'if_failed'. Mutually exclusive with --from_log.",
    )

    parser.add_argument(
        "--external",
        choices=["ignore", "separate", "squash"],
        help="Determines what to do when a compilation of a project's dependency (from 'external/' "
        "subdirectory) is found. Default option is to just 'ignore' it and not save into the "
        "resulting compile_commands.json. You can also ask yacce to produce individual 'separate' "
        "compile_commands.json in each respective external/ directory, which is useful for "
        "investigating dependencies compilation (see also --external_save_path to override "
        "destination path for this). The last option is just to "
        "'squash' these compilation commands of all externals into the main single compile_commands.json",
    )

    parser.add_argument(
        "--external_save_path",
        help="If '--external separate' this option will override the directory into which save "
        "dependency specific compile_commands.json. Default is '$(bazel info output_base)/external'",
    )

    parser = addCommonCliArgs(
        parser,
        {"cwd": " Default: directory returned by '$(bazel info execution_root)' after build ends."},
    )

    # TODO -- handling!

    args = parser.parse_args(unparsed_args, namespace=args)

    setattr(args, "compiler", makeCompilersSet(args.compiler))
    return args


def mode_bazel(Con: LoggingConsole, args: argparse.Namespace, unparsed_args: list) -> int:
    args = _getArgs(Con, args, unparsed_args)

    Con.info(args)

    return 3
