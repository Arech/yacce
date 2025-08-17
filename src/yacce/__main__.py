import argparse
import sys

from yacce import common


# default mode is the first
kModes = {
    "bazel": "Run a given build system based on Bazel and make compile_commands.json from it.",
    "from_log": "Generate compile_commands.json from strace log file.",
}


def getModeArgs():
    """Makes mode + some early options parser.
    Possible modes are:
    - 'from_log': just takes strace log file and tries to make a compile_commands.json from it.
    - 'bazel': takes a bunch of options and runs a build system passed after -- argument assuming
        it's based on Bazel. This is a default mode activated if the first script argument doesn't
        match to any of the defined modes.
    - <add here other build systems when needed as separate modes>.
    - 'help | --help | -h': prints help message and exits.
    """
    # unfortunately, for some unexplained and dumb reason, argparse doesn't support parsing known
    # arguments only up to the first unknown argument, so we have to manually control for that.
    parser = argparse.ArgumentParser(
        description=common.kMainDescription,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--debug",
        help="Minimum debug level to show. 0 is the most verbose. Default: %(default)s",
        type=int,
        choices=range(0, common.LoggingConsole.LogLevel.Critical.value + 1),
        default=common.LoggingConsole.LogLevel.Info.value,
    )

    parser.add_argument(
        "--colors",
        help="Controls if the output could be colored. Default: %(default)s",
        action=argparse.BooleanOptionalAction,
        default=True,
    )

    modes = parser.add_subparsers(help='Modes of operation. Use "help" for more info.')

    for mode, description in kModes.items():
        p = modes.add_parser(mode, help=description)
        p.add_argument("--mode", dest="mode", default=mode, help=argparse.SUPPRESS)

    if len(sys.argv) <= 2:
        parser.print_help()
        sys.exit(2)

    idx = 0
    for idx, arg in enumerate(sys.argv[1:]):
        if not arg.startswith("--") and arg not in kModes and not arg.isdigit():
            break

    if idx == len(sys.argv) - 1:
        parser.print_help()
        sys.exit(2)

    return parser.parse_args(sys.argv[1 : idx + 1]), sys.argv[idx + 1 :]


def main():
    args, unparsed_args = getModeArgs()
    Con = common.LoggingConsole(
        no_color=not args.colors,
        log_level= common.LoggingConsole.LogLevel(args.debug)
    )
    Con.yacce_begin()

    Con.debug("mode args:", args)
    Con.debug("args beyond mode:", unparsed_args)

    if not hasattr(args, "mode"):
        Con.debug("Mode is not specified, using the default")
    mode = args.mode if hasattr(args, "mode") else next(iter(kModes))


if __name__ == "__main__":
    main()
