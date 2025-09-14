import argparse
import sys

from yacce import common
from yacce.mod_bazel import mode_bazel
from yacce.mod_from_log import mode_from_log


# default mode is the first
kModes = {
    "bazel": "Runs given build system based on Bazel and extract compile_commands.json from it (possibly "
    "with individual auxiliary compile_commands.json for each dependency). This is a default mode "
    "activated if the mode specification is just omitted.",

    "from_log": "[dbg] Generate a possibly NON-WORKING(!) compile_commands.json from a strace log file. "
    "This mode features the most generic way to parse strace output and since the log generally "
    "lacks some important information (such as the working directory in case of Bazel, "
    "which substitutes PWD with a useless /proc/self/cwd), it may produce a non-working "
    "compile_commands.json. This mode is primarily intended for debugging purposes as it doesn't "
    "use any assumptions about the build system and just parses the strace log file and turns it "
    "into compile_commands.json as is. Note that you might want to disable checking files existence "
    "with --ignore-not-found",
}

kModeFuncs = {"bazel": mode_bazel, "from_log": mode_from_log}


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
        prog="yacce",
        description=common.kMainDescription,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # dumb, but argparse doesn't have a way to query for defined flags
    definedFlags = {"--debug"}
    parser.add_argument(
        "--debug",
        help="Minimum debug level to show. 0 is the most verbose. Default: %(default)s",
        type=int,
        choices=range(0, common.LoggingConsole.LogLevel.Critical.value + 1),
        default=common.LoggingConsole.LogLevel.Info.value,
    )

    definedFlags |= {"--colors", "--no-colors"}
    parser.add_argument(
        "--colors",
        help="Controls if the output could be colored. Default: %(default)s",
        action=argparse.BooleanOptionalAction,
        default=True,
    )

    modes = parser.add_subparsers(
        help='Modes of operation. Use "--help" with each to get more info.'
    )

    for mode, description in kModes.items():
        p = modes.add_parser(mode, help=description)
        p.add_argument("--mode", dest="mode", default=mode, help=argparse.SUPPRESS)

    if len(sys.argv) <= 2:  # there's always more than 1 arg
        parser.print_help()
        sys.exit(2)

    not_found = 1
    for first_rest, arg in enumerate(sys.argv[1:]):
        if arg not in definedFlags and arg not in kModes and not arg.isdigit():
            not_found = 0
            break
    first_rest += 1 + not_found
    if first_rest >= len(sys.argv):  # mode specific args always exist
        parser.print_help()
        sys.exit(2)

    return parser.parse_args(sys.argv[1:first_rest]), sys.argv[first_rest:]


def main():
    args, unparsed_args = getModeArgs()
    Con = common.LoggingConsole(
        no_color=not args.colors, log_level=common.LoggingConsole.LogLevel(args.debug)
    )
    Con.yacce_begin()

    Con.debug("mode args:", args)
    Con.debug("args past the mode:", unparsed_args)

    if not hasattr(args, "mode"):
        Con.debug("Mode is not specified, using the default")
    mode = getattr(args, "mode", next(iter(kModes)))

    try:
        ret = kModeFuncs[mode](Con, args, unparsed_args)
        common.warnClangdIncompatibilitiesIfAny(Con, args)
        
        Con.debug(f"Exiting with code {ret}")
        sys.exit(ret)
    except common.YacceException as e:
        Con.critical(repr(e))
        Con.debug("Exiting with code 1")
        sys.exit(1)



if __name__ == "__main__":
    main()
