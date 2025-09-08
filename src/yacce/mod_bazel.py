import argparse
import os
import re
from rich.progress import Progress
import sys

from yacce.common import CompilersTuple

from .common import (
    addCommonCliArgs,
    BaseParser,
    CompileCommand,
    escapePath,
    OtherCommand,
    kMainDescription,
    LoggingConsole,
    makeCompilersSet,
    storeJson,
    toAbsPathUnescape,
    unescapePath,
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
        "dependencies specific individual compile_commands.json. Default is where the external repo resides "
        "(typically it's '$(bazel info output_base)/external', but depends on the build system "
        "and --override_repository bazel flag value)",
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
    def __init__(self, Con: LoggingConsole, args: argparse.Namespace) -> None:
        Con.trace("Running base parser")
        do_test_files = not args.ignore_not_found
        setattr(args, "ignore_not_found", True)
        super().__init__(Con, args)

        setattr(args, "ignore_not_found", do_test_files)
        self._test_files = do_test_files

        Con.trace("Starting bazel specific processing...")
        self._update()

    def _update(self) -> None:
        ext_paths: dict[str, str] = {}  # external canonical_name -> realpath
        extinc_paths: dict[str, str] = {}  # external include paths
        ext_ccs: dict[str, list[CompileCommand]] = {}
        ext_cctimes: dict[str, list[float]] = {}
        # TODO other commands!

        new_ccs: list[CompileCommand] = []  # new compile_commands for the project only
        new_ccs_time: list[float] = []

        notfound_inc: set[str] = set()

        # generated files such as 'bazel-out/k8-opt/bin/external/<repo>/..' are also externals!
        # matches repo part in any external path spec. Not sure leading optional ./ is useful,
        # haven't seen it, but leaving it just in case
        r_any_external = re.compile(
            r"^(?:\.\/)?(?:bazel-[^\/]+\/[^\/]+\/bin\/)?external\/([^\/]+)\/"
        )
        # matches a whole external/... part in bazel-..././external/.. path spec
        r_bazel_external = re.compile(r"^(?:\.\/)?bazel-[^\/]+\/[^\/]+\/bin\/(external\/.+)$")

        with Progress(console=self.Con) as progress:  # transient=True,
            task = progress.add_task(
                "Applying bazel specific transformations to the log...",
                total=len(self.compile_commands),
            )

            for ccidx, cc in enumerate(self.compile_commands):
                # TODO add progress bar here

                cctime = self.compile_cmd_time[ccidx]
                args, output, source, line_num = cc

                # deciding if this is external
                m_external = r_any_external.match(source)
                if m_external:
                    repo = m_external.group(1)
                    if repo not in ext_paths:
                        repo_path = os.path.realpath(os.path.join(self._cwd, "external", repo))
                        if self._test_files and not os.path.isdir(repo_path):
                            repo_path2 = os.path.realpath(
                                os.path.join(self._cwd, "/../../external", repo)
                            )
                            if os.path.isdir(repo_path2):
                                self.Con.warning(
                                    "External repo '",
                                    repo,
                                    "' doesn't exist at expected path '",
                                    repo_path,
                                    "', but exist under main external path '",
                                    repo_path2,
                                    "'. Using the main external path.",
                                )
                                repo_path = repo_path2
                            else:
                                self.Con.warning(
                                    "External repo '",
                                    repo,
                                    "' doesn't exist at expected path '",
                                    repo_path,
                                    "' as well as under main external path '",
                                    repo_path2,
                                    "'.",
                                )

                        ext_paths[repo] = repo_path
                else:
                    repo = None

                # checking and updating the source path
                path = toAbsPathUnescape(self._cwd, source)
                if self._test_files and not os.path.isfile(path):
                    if m_external:
                        path2 = toAbsPathUnescape(os.path.join(self._cwd, "../.."), source)
                        if os.path.isfile(path2):
                            path = path2
                        else:
                            self.Con.warning(
                                "Translation unit ",
                                path,
                                "doesn't exist in both expected locations of an external!",
                            )
                    else:
                        self.Con.warning("Translation unit ", path, "doesn't exist!")
                source = escapePath(os.path.realpath(path))
                # no need to check and update output

                new_args = []
                next_is_path = False
                for argidx, arg in enumerate(args):
                    # resolving symlinks to reduce dependency on bazel's internal workspace structure
                    if next_is_path:
                        next_is_path = False
                        m_ext = r_any_external.match(arg)
                        if m_ext:
                            r = m_ext.group(1)
                            if r not in extinc_paths:
                                repo_path = os.path.realpath(os.path.join(self._cwd, "external", r))
                                if self._test_files and not os.path.isdir(repo_path):
                                    repo_path2 = os.path.realpath(
                                        os.path.join(self._cwd, "/../../external", r)
                                    )
                                    if os.path.isdir(repo_path2):
                                        self.Con.warning(
                                            "External include repo '",
                                            repo,
                                            "' doesn't exist at expected path '",
                                            repo_path,
                                            "', but exist under main external path '",
                                            repo_path2,
                                            "'. Using the main external path.",
                                        )
                                        repo_path = repo_path2
                                    else:
                                        self.Con.warning(
                                            "External include repo '",
                                            repo,
                                            "' doesn't exist at expected path '",
                                            repo_path,
                                            "' as well as under main external path '",
                                            repo_path2,
                                            "'.",
                                        )
                                extinc_paths[r] = repo_path

                        path = toAbsPathUnescape(self._cwd, arg)
                        if self._test_files and not os.path.exists(path):
                            err = True
                            if m_ext:
                                path2 = toAbsPathUnescape(os.path.join(self._cwd, "../.."), arg)
                                if os.path.isfile(path2):
                                    path=path2
                                    err = False
                            if err:
                                # ignoring existence test failure for same qualified args starting with bazel-out/k8-opt/bin/external/... dirs
                                # that exist as just normally qualified external/... args. This seems to be a bazel quirk
                                m_bzl_ext = r_bazel_external.match(arg)
                                if m_bzl_ext:
                                    ext = m_bzl_ext.group(1)
                                    qual = args[argidx - 1]  # can't be negative
                                    # TODO: O(n^2), but maybe will improve later
                                    for ai, a in enumerate(args[1:]):
                                        if a == ext and qual == args[ai]:  # refs previous args element
                                            err = False
                                            break
                                if err:
                                    notfound_inc.add(arg)
                        arg = escapePath(os.path.realpath(path))

                    elif arg in self.kArgIsPath:
                        next_is_path = True

                    new_args.append(arg)

                new_cc = CompileCommand(new_args, output, source, line_num)
                if m_external:
                    ext_ccs.setdefault(repo, []).append(new_cc)
                    ext_cctimes.setdefault(repo, []).append(cctime)
                else:
                    new_ccs.append(new_cc)
                    new_ccs_time.append(cctime)
                progress.advance(task)

        if self.Con.will_log(self.Con.LogLevel.Debug):
            self.Con.debug(
                "Compiled dependencies list has",
                len(ext_paths),
                "entries:",
                {k: ext_paths[k] for k in sorted(ext_paths.keys())},
            )
            self.Con.debug(
                "Include dependencies list has",
                len(extinc_paths),
                "entries:",
                {k: extinc_paths[k] for k in sorted(extinc_paths.keys())},
            )

        ext_paths |= extinc_paths
        self.Con.print(
            "External dependencies list has",
            len(ext_paths),
            "entries:",
            {k: ext_paths[k] for k in sorted(ext_paths.keys())},
        )

        if len(notfound_inc) > 0:
            self.Con.warning(
                "These",
                len(notfound_inc),
                "paths are used in compiler includes, but doesn't exist. This might mean the "
                "build system is misconfigured, or the log file is incomplete, but sometimes it "
                "just happens and it's fine.",
                [v for v in sorted(notfound_inc)],
            )

        self._ext_paths = ext_paths
        self._ext_ccs = ext_ccs
        self._ext_cctimes = ext_cctimes
        # TODO other commands!

        self._new_cc = new_ccs
        self._new_cc_time = new_ccs_time

    def storeJsons(self, dest_dir: str, save_duration: bool, save_line_num: bool):
        super().storeJsons(dest_dir, save_duration, save_line_num)
        storeJson(
            self.Con,
            dest_dir,
            self._new_cc,
            self._new_cc_time if save_duration else None,
            self._cwd,
            save_line_num,
            "_new",
        )
        for repo in sorted(self._ext_ccs.keys()):
            storeJson(
                self.Con,
                dest_dir,
                self._ext_ccs[repo],
                self._ext_cctimes[repo] if save_duration else None,
                self._cwd,
                save_line_num,
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

    p = BazelParser(Con, args)

    # TODO handling of args.external and args.external_save_path

    dest_dir = args.dest_dir if hasattr(args, "dest_dir") and args.dest_dir else os.getcwd()

    p.storeJsons(dest_dir, args.save_duration, args.save_line_num)

    return 0
