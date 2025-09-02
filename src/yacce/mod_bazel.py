import argparse
import os
import re
import sys

from yacce.common import CompilersTuple

from .common import (
    addCommonCliArgs,
    BaseParser,
    CompileCommand,
    LinkCommand,
    kMainDescription,
    LoggingConsole,
    makeCompilersSet,
    rawPathExists,
    storeJson,
)


def _getArgs(
    Con: LoggingConsole, args: argparse.Namespace, unparsed_args: list
) -> tuple[argparse.Namespace, list]:
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

    p_log = parser.add_argument_group(
        "For using existing strace log (mutually exclusive with live mode)"
    )
    excl1 = {"from_log"}
    p_log.add_argument(
        "--from_log",
        help="Toggle a mode in which yacce will only parse the log specified in --log_file, but "
        "will not invoke any build system on its own. Mutually exclusive with --keep_log.",
        action="store_true",
    )

    p_live = parser.add_argument_group("For running live bazel (mutually exclusive with log mode)")
    excl2 = {"keep_log"}
    p_live.add_argument(
        "--keep_log",
        choices=["if_failed", "always", "never"],
        help="Determines what to do with the log file after building, generation and parsing of the "
        "log file finishes. Default is 'if_failed'. Mutually exclusive with --from_log.",
    )
    excl2 |= {"clean"}
    p_live.add_argument(
        "--clean",
        choices=["always", "expunge", "never"],
        help="Determines, if 'bazel clean' or 'bazel clean --expunge' commands are executed, or no "
        "cleaning is done before running the build. Note that if cleaning is disabled, "
        "cached (already compiled) translation units will be invisible to yacce and hence will not "
        "make it into resulting compiler_commands.json!",
    )

    parser.add_argument(
        "--external",
        choices=["ignore", "separate", "squash"],
        default="ignore",
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
        help="If '--external separate' was set, using this option one could override a directory into which to save "
        "dependencies specific individual compile_commands.json. Default is '$(bazel info output_base)/external'",
    )

    parser.add_argument(
        "--output_base",
        help="An override to use in place of $(bazel info output_base).",
    )

    parser.add_argument(
        "--bazel_command",
        default="bazel",
        help="A command to run to communicate with instance of bazel for current build system. "
        "Note that it always assumes that yacce runs inside a bazel workspace directory. "
        "Default: %(default)s",
    )

    parser = addCommonCliArgs(
        parser,
        {"cwd": " Set this to override output of $(bazel info execution_root)."},
    )

    # looking for -- in unparsed_args to save build system invocation args.
    if len(unparsed_args) < 2:  # the shortest is "-- build_script.sh"
        parser.print_help()
        sys.exit(2)

    not_found = 1
    for first_rest, arg in enumerate(unparsed_args):  # .index() with exception is a crap.
        if "--" == arg:
            not_found = 0
            break

    first_rest += 1 + not_found
    if first_rest < len(unparsed_args):
        mode_args = unparsed_args[: first_rest - 1]
        unparsed_args = unparsed_args[first_rest:]
    else:
        mode_args = unparsed_args
        unparsed_args = []

    args = parser.parse_args(mode_args, namespace=args)

    # checking mutually exclusive options
    if any(getattr(args, a, False) for a in excl1) and any(getattr(args, a, False) for a in excl2):
        parser.print_help()
        Con.critical("Options from these two lists are mutually exclusive: ", excl1, excl2)
        sys.exit(2)
    # taking care of defaults that weren't set due to mutual exclusion check. argparse is a crap too
    if args.keep_log is None:
        setattr(args, "keep_log", "if_failed")
    if args.clean is None:
        setattr(args, "clean", "always")

    setattr(args, "compiler", makeCompilersSet(args.compiler))
    return args, unparsed_args


class BazelParser(BaseParser):
    def __init__(
        self,
        Con: LoggingConsole,
        log_file: str,
        cwd: str,  # cwd is execution root dir
        do_test_files: bool,
        compilers: CompilersTuple,
        do_link: bool,
        output_base: str,
    ) -> None:
        Con.debug("Running base parser")
        super().__init__(Con, log_file, cwd, False, compilers, do_link)

        self._test_files = do_test_files
        self._output_base = output_base

        assert isinstance(output_base, str), (
            "Output base parameter is mandatory. Use --output_base CLI option."
        )
        if do_test_files and not os.path.isdir(output_base):
            Con.warning(
                "Output base directory '",
                output_base,
                "' does not exist. If you used an override --output_base, you might need to fix it. "
                "Resulting json will likely be invalid.",
            )

        Con.debug("Starting bazel specific processing...")
        self._update()

    def _update(self) -> None:
        ext_paths: dict[str, str] = {}  # external canonical_name -> realpath
        ext_ccs: dict[str, list[CompileCommand]] = {}
        ext_cctimes: dict[str, list[float]] = {}
        # TODO link commands!

        new_cc = []  # new compile_commands for the project only
        new_cc_time = []

        r_external = re.compile(r"^(?:\.\/)?external\/([^\/]+)\/")
        #TODO: generated files such as 'bazel-out/k8-opt/bin/external/xla/xla/xla_data.pb.cc' are
        # also externals! Validate via output: bazel-out/k8-opt/bin/external/xla/xla/_objs/xla_data_proto_cc_impl/xla_data.pb.pic.o

        for ccidx, cc in enumerate(self.compile_commands):
            cctime = self.compile_cmd_time[ccidx]
            m_external = r_external.match(cc.tu)
            if m_external:
                repo = m_external.group(1)
                if repo not in ext_paths:
                    repo_path = os.path.realpath(os.path.join(self._cwd, "external", repo))
                    if self._test_files and not os.path.isdir(repo_path):
                        self.Con.warning(
                            "External repo", repo, "failed existence test at path", repo_path
                        )
                    ext_paths[repo] = repo_path

                ext_ccs.setdefault(repo, []).append(cc)
                ext_cctimes.setdefault(repo, []).append(cctime)

            else:
                new_cc.append(cc)
                new_cc_time.append(cctime)

        self.Con.debug("externals mapping", ext_paths)

        self._ext_paths = ext_paths
        self._ext_ccs = ext_ccs
        self._ext_cctimes = ext_cctimes
        # TODO link commands!

        self._new_cc = new_cc
        self._new_cc_time = new_cc_time

    def storeJsons(self, dest_dir: str, save_duration: bool):
        super().storeJsons(dest_dir, save_duration)
        storeJson(
            self.Con,
            dest_dir,
            self._new_cc,
            self._new_cc_time if save_duration else None,
            self._cwd,
            "_new",
        )
        for repo,lst in self._ext_ccs.items():
            storeJson(
                self.Con,
                dest_dir,
                lst,
                self._ext_cctimes[repo] if save_duration else None,
                self._cwd,
                f"_ext_{repo}",
            )


def mode_bazel(Con: LoggingConsole, args: argparse.Namespace, unparsed_args: list) -> int:
    args, build_system_args = _getArgs(Con, args, unparsed_args)

    Con.debug("bazel mode args: ", args)
    Con.debug("build_system_args:", build_system_args)

    if not args.from_log:
        # TODO call bazel clean
        # TODO run the build system and gather trace. Note, there'll be different trace filename!
        # so an update to args.log_file is needed
        pass

    # only after finishing the build we could query bazel properties
    # TODO update args.cwd from bazel
    # TODO update args.output_base from bazel

    # parsing strace log to produce raw commands.
    p = BazelParser(
        Con,
        args.log_file,
        args.cwd,
        not args.ignore_not_found,
        args.compiler,
        args.link_commands,
        args.output_base,
    )

    # TODO handling of args.external and args.external_save_path

    dest_dir = args.dest_dir if hasattr(args, "dest_dir") and args.dest_dir else os.getcwd()

    p.storeJsons(dest_dir, args.save_duration)

    return 0
