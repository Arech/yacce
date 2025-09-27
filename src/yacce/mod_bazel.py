import argparse
import itertools
import os
import re
from rich.progress import Progress
import shutil
import signal
import subprocess
import sys

if "linux" == sys.platform:
    import pty
    import shlex

from .common import (
    addCommonCliArgs,
    BaseParser,
    BetterHelpFormatter,
    CompileCommand,
    escapePath,
    # OtherCommand,
    kMainDescription,
    LoggingConsole,
    makeCompilersSet,
    storeJson,
    unescapePath,
    YacceException,
)


def _getArgs(
    Con: LoggingConsole, args: argparse.Namespace, unparsed_args: list
) -> tuple[argparse.Namespace, list]:
    parser = argparse.ArgumentParser(
        prog="yacce bazel",
        description=kMainDescription
        + "\n\nMode 'bazel' is intended to generate compile_commands.json from tracing execution of "
        "a 'bazel build' or any other shell command invoking Bazel using the Linux's strace utility. Hence it "
        "only supports compilation of a single Bazel workspace (including its all external dependencies) "
        "happening locally. If you are using Bazel's remote "
        "caching feature, including '--disk_cache', please make sure you're starting with a clean "
        "cache, otherwise yacce won't see compilation of cache hits.",
        usage="yacce [global options] [bazel] [options (see below)] [-- shell command eventually invoking Bazel]",
        formatter_class=BetterHelpFormatter,
        # argparse.RawTextHelpFormatter, #RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--log_file",
        help="Write strace log to and/or read it from this file.\nSee also '--from_log'.\n"
        "Default: 'strace.txt' in the current directory, %(default)s",
        metavar="path/to/file",
        default=os.path.join(os.getcwd(), "strace.txt"),
    )

    p_log = parser.add_argument_group(
        "Log mode, uses existing strace log and is mutually exclusive with the live mode"
    )
    excl1 = {"from_log"}
    p_log.add_argument(
        "--from_log",
        help="Toggles a mode in which yacce will only parse an existing log file specified by '--log_file', but "
        "will not invoke a build system to spy on.\n"
        "Mutually exclusive with '--keep_log' and requires no build system arguments passed (no "
        "'--' argument and anything after it).\n"
        "Default: not set, i.e. the mode is not activated.",
        action="store_true",
    )

    p_live = parser.add_argument_group(
        "Live mode (default), runs a Bazel build system and is mutually exclusive with the log mode"
    )
    excl2 = {"keep_log"}
    p_live.add_argument(
        "--keep_log",
        choices=["if_errors", "always", "never"],
        help="Determines conditions of keeping of the strace log file after yacce "
        "finishes. Mutually exclusive with '--from_log'.\n"
        "Default is 'always' as it might be useful to run yacce in the log mode with different "
        "arguments later on the same log file.",
    )
    excl2 |= {"clean"}
    p_live.add_argument(
        "--clean",
        choices=["always", "expunge", "never"],
        help="Determines, if a 'bazel clean' or 'bazel clean --expunge' commands should be executed "
        "before running the build.\n"
        "Note that if cleaning is disabled, cached (already compiled) sources will be invisible to "
        "yacce and hence will not make it into resulting compiler_commands.json! (iterative updates "
        "aren't supported yet)\n"
        "Default: not specified, yacce will ask if running 'bazel clean' is ok.",
    )

    parser.add_argument(
        "--external",
        choices=["ignore", "combine-with-overridden", "to-files", "to-external", "combine-all"],
        default="combine-with-overridden",
        help="Determines what to do when a compilation of a project's dependency source file (from 'external/' "
        "subdirectory) is found.\n"
        "- One option is to just to 'ignore' (remove) it and to leave in the resulting "
        "compile_commands.json *only* commands directly related to the project.\n"
        "- The default option 'combine-with-overridden' produces a single compile_commands.json containing "
        "main project's files as well as dependencies that are stored *outside* of their "
        "expected location at '$(bazel info output_base)/external/<repo>' (this typically happens when you "
        "override a dependency repo location for Bazel when you work on the project and its dependency simultaneously).\n"
        "- Option 'to-files' produces individual files nearby the main compile_commands.json, named like"
        "'compile_commands_ext_<repo>.json' for each external dependency '<repo>' (this might be useful "
        "for manual inspection).\n"
        "- Option 'to-external' differs from 'to-files' only in the location and naming of the resulting files. "
        "'to-external' produces an individual compile_commands.json in each external dependency's "
        "directory and is useful when you're going to open the dependency directory in a parallel IDE for a close inspection.\n"
        "- The 'combine-all' option just writes all compilation commands (for the main project and its "
        "dependencies) into a single file.\n"
        "See '--dest_dir' argument for a default location and/or override for the main project's compile_commands.json file.\n"
        "NOTE that since currently yacce doesn't properly process compiler invocations that aren't related "
        "to compiling C or C++ sources (such as linking only, or compiling ASM files), if '--other_commands' "
        "flag is specified, a compound other_commands.json (containing all other invocations of a "
        "compiler for the main project and its externals) will be saved nearby the main compile_commands.json "
        "irrespective of a value of this flag.",
    )

    parser.add_argument(
        "--bazel_command",
        default="bazel",
        help="Override which command to run to communicate with the instance of a Bazel for the build system.\n"
        "You don't typically need this argument, if you have bazelisk installed.\n"
        "To set the workspace directory see '--bazel_workspace' argument.\n"
        "Default: %(default)s",
        metavar='command_or_filepath'
    )

    parser.add_argument(
        "--bazel_workspace",
        help="Overrides Bazel workspace directory to set a current directory context for the bazel command (see "
        "'--bazel_command').\n" \
        "This is useful if yacce needs to be run from an outside of that "
        "workspace. Note that any dir under a real workspace would also work here.\n"
        "Default: a current working directory '%(default)s'.",
        metavar="path/to/dir",
        default=os.getcwd(),
    )

    parser.add_argument(
        "--build_cwd",
        help="By default, a shell command to start the build is invoked from the Bazel workspace "
        "directory (see '--bazel_workspace'). This argument allows to override that and set a "
        "different directory as a cwd for the build command.\n" \
        "Note that this is different from '--cwd' argument, which "
        "for the 'bazel --from_log' mode of yacce specifies a value of '$(bazel info execution_root)' directory.",
        metavar="path/to/dir",
    )

    parser.add_argument(
        "--build_shell",
        default="bash",
        help="Build command is executed by passing it to a shell. By default, '%(default)s' is "
        "used, but you can override that with this argument.",
        metavar="shell_to_use",
    )

    parser.add_argument(
        "--ensure_build_succeeds",
        help="By default yacce only warns if the build command fails (exits with a non-zero code) "
        "and it tries to process the strace log file to produce some results anyway. If you want to "
        "make sure that yacce will only use a full log of a successful build, set this argument to "
        "enforce yacce failure if the build fails.\n",
        action=argparse.BooleanOptionalAction,
        default=False,
    )

    parser = addCommonCliArgs(
        parser,
        {
            "cwd": "In the 'yacce bazel --from_log' mode, this argument overrides an output of "
            "'$(bazel info execution_root)' (i.e. this enables parsing of an existing log file without "
            "querying its build system). In the default live mode (when no '--from_log' argument is "
            "specified) this argument is either has to be unset, or match the output of '$(bazel info execution_root)'.",
            "dest_dir": " Default: directory of the log file (see '--log_file')",
            "other_commands": " Note that yacce currently does not implement attribution of other "
            "compilation commands to the project's external dependencies. I.e. all other commands related to "
            "compiling non-C++ sources and linking will be combined into a single other_commands.json "
            "file irrespective of '--external' argument setting.",
        },
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
        setattr(args, "keep_log", "always")
    # leaving .clean untouched so we could later ask the user
    # if args.clean is None:
    #    setattr(args, "clean", "always")

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

        Con.trace("Starting Bazel-specific processing...")
        self._update()

    def _makeFullPath(
        self, is_external: bool, subdir: str, path_ending: str, is_file: bool, warn=True
    ) -> tuple[str, bool]:
        exists = False
        fullpath = os.path.realpath(os.path.join(self._cwd, subdir, path_ending))
        if self._test_files:
            exists = (is_file and os.path.isfile(fullpath)) or (
                (not is_file) and os.path.exists(fullpath)
            )
            if is_external and not exists:
                fullpath2 = os.path.realpath(os.path.join(self._cwd, "../..", subdir, path_ending))
                exists = os.path.exists(fullpath2)
                if exists:
                    fullpath = fullpath2
                else:
                    if warn:
                        self.Con.warning(
                            f"External '{subdir}/{path_ending}' not found in both expected paths",
                            (fullpath, fullpath2),
                        )
        return fullpath, exists

    def _update(self) -> None:
        ext_paths: dict[str, str] = {}  # external canonical_name -> realpath
        extinc_paths: dict[str, str] = {}  # external include paths
        ext_ccs: dict[str, list[CompileCommand]] = {}
        ext_cctimes: dict[str, list[float]] = {}
        # TODO other commands!

        new_ccs: list[CompileCommand] = []  # new compile_commands for the project only
        new_ccs_time: list[float] = []

        notfound_inc: set[str] = set()

        r_external = re.compile(r"^(?:\.\/)?external\/")
        # generated files such as 'bazel-out/k8-opt/bin/external/<repo>/..' are also externals!
        # matches repo part in any external path spec. Not sure leading optional ./ is useful,
        # haven't seen it, but leaving it just in case
        r_any_external = re.compile(
            r"^(?:\.\/)?(?:bazel-[^\/]+\/[^\/]+\/bin\/)?external\/([^\/]+)\/"
        )
        # matches a whole external/... part in bazel-../../external/.. path spec
        r_bazel_external = re.compile(r"^(?:\.\/)?bazel-[^\/]+\/[^\/]+\/bin\/(external\/.+)$")

        def _fix_path(arg: str, argidx: int, args: list[str] | None) -> str:
            nonlocal extinc_paths, notfound_inc
            m_ext = r_any_external.match(arg)
            if m_ext:
                r = m_ext.group(1)
                if r not in extinc_paths:
                    repo_path, exists = self._makeFullPath(True, "external", r, False)
                    if self._test_files and not exists:
                        self.Con.warning(
                            f"External include repo '{r}' not found at expected path '{repo_path}'"
                        )
                    extinc_paths[r] = repo_path

            path, exists = self._makeFullPath(
                bool(r_external.match(arg)), "", unescapePath(arg), False, warn=False
            )
            if self._test_files and not exists:
                err = True
                if args is not None:
                    # ignoring existence test failure for same qualified args starting with bazel-out/k8-opt/bin/external/... dirs
                    # that exist as just normally qualified external/... args. This seems to be a bazel quirk
                    m_bzl_ext = r_bazel_external.match(arg)
                    if m_bzl_ext:
                        ext = m_bzl_ext.group(1)
                        qual = args[argidx - 1]  # can't be negative
                        # TODO: O(n^2), but maybe will improve later
                        for ai, a in enumerate(args[1:]):
                            # ai refs previous args element
                            if a == ext and qual == args[ai]:
                                err = False
                                break
                if err:
                    notfound_inc.add(arg)
            return escapePath(path)

        with Progress(console=self.Con) as progress:  # transient=True,
            task = progress.add_task(
                "Applying Bazel-specific transformations to the log...",
                total=len(self.compile_commands),
            )
            for ccidx, cc in enumerate(self.compile_commands):
                cctime = self.compile_cmd_time[ccidx]
                args, output, source, line_num = cc

                # deciding if this is external
                m_external = r_any_external.match(source)
                if m_external:
                    repo = m_external.group(1)
                    if repo not in ext_paths:
                        repo_path, exists = self._makeFullPath(True, "external", repo, False)
                        if self._test_files and not exists:
                            self.Con.warning(
                                f"External repo '{repo}' not found at expected path '{repo_path}'"
                            )
                        ext_paths[repo] = repo_path
                else:
                    repo = None

                # checking and updating the source path
                path, exists = self._makeFullPath(
                    bool(r_external.match(source)), "", unescapePath(source), True
                )
                if self._test_files and not exists:
                    self.Con.warning("Source file", path, "not found!")
                source = escapePath(path)
                # no need to check and update output

                # new_args = []
                next_is_path = False
                for argidx, arg in enumerate(args):
                    # resolving symlinks to reduce dependency on bazel's internal workspace structure
                    if next_is_path:
                        next_is_path = False
                        args[argidx] = _fix_path(arg, argidx, args)
                    elif arg in self.kArgIsPath and (
                        arg not in self.kCheckArgForSysrootSpec
                        or not arg.startswith(self.kSysrootSpec)
                    ):
                        next_is_path = True
                    elif m_pfx_arg := self.r_pfx_arg_is_path.match(arg):
                        path_part = arg[m_pfx_arg.end() :]
                        if path_part:
                            args[argidx] = m_pfx_arg.group() + _fix_path(path_part, argidx, None)
                    # new_args.append(arg)

                # new_cc = CompileCommand(new_args, output, source, line_num)
                new_cc = CompileCommand(args, output, source, line_num)
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
        self._new_cc = new_ccs
        self._new_cc_time = new_ccs_time

        # merging processed list back into the base class list storage
        self.compile_commands = list(itertools.chain(new_ccs, *ext_ccs.values()))
        self.compile_cmd_time = list(itertools.chain(new_ccs_time, *ext_cctimes.values()))

        # TODO other commands!

    def storeJsons(self, dest_dir: str, external: str, save_duration: bool, save_line_num: bool):
        # saving other_commands no matter what if requested
        if self._do_other:
            storeJson(
                self.Con,
                dest_dir,
                False,
                self.other_commands,
                self.other_cmd_time if save_duration else None,
                self._cwd,
                save_line_num,
            )

        if "combine-all" == external:
            storeJson(
                self.Con,
                dest_dir,
                True,
                self.compile_commands,
                self.compile_cmd_time if save_duration else None,
                self._cwd,
                save_line_num,
            )
            return

        if external in ("ignore", "to-files", "to-external"):
            storeJson(
                self.Con,
                dest_dir,
                True,
                self._new_cc,
                self._new_cc_time if save_duration else None,
                self._cwd,
                save_line_num,
            )
            if "ignore" == external:
                return

            if "to-files" == external:
                for repo in sorted(self._ext_ccs.keys()):
                    storeJson(
                        self.Con,
                        dest_dir,
                        True,
                        self._ext_ccs[repo],
                        self._ext_cctimes[repo] if save_duration else None,
                        self._cwd,
                        save_line_num,
                        f"_ext_{repo}",
                    )
            else:
                assert "to-external" == external
                for repo in sorted(self._ext_ccs.keys()):
                    storeJson(
                        self.Con,
                        self._ext_paths[repo],
                        True,
                        self._ext_ccs[repo],
                        self._ext_cctimes[repo] if save_duration else None,
                        self._cwd,
                        save_line_num,
                    )

        else:
            if "combine-with-overridden" != external:
                self.Con.warning(
                    "Unrecognized --external=",
                    external,
                    "value. Assuming default 'combine-with-overridden'",
                )
            take_repos = {}
            output_base = os.path.realpath(os.path.join(self._cwd, "../.."))
            for repo in sorted(self._ext_ccs.keys()):
                epath = self._ext_paths[repo]
                if not epath.startswith(output_base):
                    take_repos[repo] = epath
            self.Con.info(
                len(take_repos),
                "repos:",
                take_repos,
                "are detected to be outside of standard location for external dependencies. "
                "These will be saved into a combined file.",
            )

            storeJson(
                self.Con,
                dest_dir,
                True,
                list(itertools.chain(self._new_cc, *(self._ext_ccs[r] for r in take_repos))),
                list(
                    itertools.chain(self._new_cc_time, *(self._ext_cctimes[r] for r in take_repos))
                )
                if save_duration
                else None,
                self._cwd,
                save_line_num,
            )


class BazelWrap:
    """Takes care of communicating with Bazel, including running a build command under strace and
    producing the log file.

    More precisely:
    - checks if bazel is available
    - checks if strace is available
    - if requested, runs 'bazel clean' or 'bazel clean --expunge'
    - sets up strace to log build system execution
    - runs the build system with strace

    Irrecoverable errors are reported as YacceException.
    """

    def __init__(self, Con: LoggingConsole, args: argparse.Namespace):
        """Initializes the runner."""
        # here we define only a bare minimum of vars needed to service all public methods.
        self.Con = Con

        assert args.bazel_command and args.bazel_workspace
        assert hasattr(args, "from_log") and hasattr(args, "build_cwd")
        self._bazel: str = args.bazel_command

        args.log_file = os.path.realpath(args.log_file)

        args.bazel_workspace = os.path.realpath(args.bazel_workspace)
        if not os.path.isdir(args.bazel_workspace):
            raise YacceException(
                f"Bazel workspace directory '{args.bazel_workspace}' doesn't exist.\n"
                "Consider checking the value of '--bazel_workspace' argument."
            )
        self._bazel_workspace: str = args.bazel_workspace

        self._from_log: bool = bool(args.from_log)
        self._bazel_tested: bool = False
        self._execution_root: str | None = None
        self._path: str | None = None

    def _getPath(self) -> str:
        if not self._path:
            path = os.environ.get("PATH", "")
            if not path:
                path = os.defpath
            path = self._bazel_workspace + os.pathsep + path
            self._path = path
        return self._path

    def _resolveBinaryPath(self, binary: str) -> str:
        """Resolves the path to the binary, if necessary."""
        self.Con.debug(f"Resolving absolute path to '{binary}' executable...")
        if not os.path.isabs(binary):
            path = self._getPath()
            binary_path = shutil.which(binary, path=path)
            if not binary_path:
                raise YacceException(f"Failed to find '{binary}' in PATH='{path}'")
            self.Con.debug(f"Resolved '{binary}' to '{binary_path}'")
        else:
            binary_path = binary
        if not os.path.isfile(binary_path) or not os.access(binary_path, os.X_OK):
            raise YacceException(
                f"Binary '{binary}' (as {binary_path}) doesn't exist or is not executable"
            )
        return binary_path

    def _getExecutionRoot(self) -> str:
        if not self._execution_root:
            self._checkBazel()
            try:
                self._execution_root = self._queryBazelThrow("info", "execution_root")
                # this assumes that build system was run and hence execution_root exists
                if not self._execution_root or not os.path.isdir(self._execution_root):
                    raise YacceException(
                        f"Invalid or non-existing execution_root directory '{self._execution_root}' returned by Bazel"
                    )
            except Exception as e:
                raise YacceException(f"Failed to query Bazel execution root: {e}")

        return self._execution_root

    def fixCwdAsExecutionRoot(self, args: argparse.Namespace) -> None:
        """If necessary, queries bazel for execution root and modifies args to set it as cwd."""
        assert hasattr(args, "cwd")
        if args.cwd:
            args.cwd = os.path.realpath(args.cwd)
            # only querying bazel if the build system has to be run. Pure "from_log" should be able
            # to work even on a different machine.
            if not self._from_log:
                self.Con.debug(
                    "--cwd is specified. Checking if it matches with real execution root..."
                )
                exec_root = self._getExecutionRoot()
                if args.cwd != exec_root:
                    self.Con.warning(
                        f"Specified cwd '{args.cwd}' doesn't match to Bazel execution root '{exec_root}'. "
                        "Will ignore '--cwd' specification and use what Bazel provided."
                    )
                    args.cwd = exec_root
        else:
            self.Con.debug("Querying Bazel for the execution root...")
            args.cwd = self._getExecutionRoot()
        self._execution_root = args.cwd

    def _queryBazelThrow(self, *args) -> str:
        self.Con.debug("Querying '", self._bazel, "' with args:", args)
        r = (
            subprocess.run(
                [self._bazel, *args],
                cwd=self._bazel_workspace,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # bazel might spew lot's of useless stuff to stderr
            )
            .stdout.decode("utf-8")
            .rstrip()
        )
        self.Con.debug("result:", r)
        return r

    def _checkBazel(self) -> None:
        if not self._bazel_tested:
            assert self._bazel and self._bazel_workspace  # doing this once
            try:
                self._bazel = self._resolveBinaryPath(self._bazel)
            except YacceException as e:
                raise YacceException(
                    f"{e}\nIf your Bazel workspace differs from current directory and you have "
                    "set '--bazel_workspace' parameter to point to it properly, either you have to "
                    "invoke a custom Bazel binary (i.e. set '--bazel_command' argument) or "
                    "better just install bazelisk."
                )
            self.Con.info("Checking if '", self._bazel, "' works in:", self._bazel_workspace)
            try:
                v = self._queryBazelThrow("--version")
                self.Con.info(
                    f"{v} is detected in directory: '{self._bazel_workspace}' using command '{self._bazel}'"
                )
                self._bazel_tested = True
            except Exception as e:
                raise YacceException(
                    f"Failed to run Bazel ('{self._bazel}') in directory: '{self._bazel_workspace}': {e}"
                )

    def runBuild(self, args: argparse.Namespace, build_system_args: list) -> None:
        self.Con.debug("Running the build...")
        assert not args.from_log
        assert not self._execution_root, "execution_root should be queried after running the build"
        assert len(build_system_args) > 0

        assert args.log_file and args.keep_log

        if args.build_cwd:
            args.build_cwd = os.path.realpath(args.build_cwd)
            if not os.path.isdir(args.build_cwd):
                raise YacceException(
                    f"Build system working directory '{args.build_cwd}' doesn't exist.\n"
                    "Consider checking value of --build_cwd argument."
                )
        else:
            args.build_cwd = self._bazel_workspace

        if args.build_shell:
            shell_path = shutil.which(args.build_shell)
            if not shell_path:
                raise YacceException(
                    f"Failed to find specified build shell '{args.build_shell}' in PATH."
                )
            args.build_shell = shell_path
            self.Con.debug(f"Using '{args.build_shell}' as a shell to run the build command.")

        assert os.path.isabs(args.log_file)
        if os.path.exists(args.log_file):
            self.Con.warning(f"Log file '{args.log_file}' already exists and will be overwritten.")
            os.remove(args.log_file)

        self._checkBazel()
        self._checkStrace()

        self._handleClean(args)

        self._runBazelWithStrace(
            args.log_file,
            args.keep_log,
            build_system_args,
            args.build_cwd,
            args.build_shell,
            args.ensure_build_succeeds,
        )

    def _handleClean(self, args: argparse.Namespace) -> None:
        if args.clean is None:
            ans = input(
                "\nATTENTION!\n\n'--clean' argument wasn't specified. To gather all build system "
                "information, yacce has to supervise the complete build process therefore it's "
                "highly recommended to run a 'bazel clean' command before the build to ensure all "
                "files are recompiled and hence traced.\n"
                "Do you authorize yacce to run 'bazel clean' now? [Y/n]: "
            )
            if ans.lower() == "n":
                args.clean = "never"
            else:
                args.clean = "always"

        if args.clean in ("always", "expunge"):
            self._bazelClean(args.clean == "expunge")
        else:
            self.Con.debug("Skipping 'bazel clean' as requested.")

    def _checkStrace(self) -> None:
        assert self._bazel_workspace
        try:
            self._strace = self._resolveBinaryPath("strace")
        except YacceException as e:
            raise YacceException(
                f"{e}\nProbably the easiest way to install it is via your package manager, such as "
                "'apt install strace' or similar."
            )
        self.Con.info("Checking if '", self._strace, "' is available in:", self._bazel_workspace)
        try:
            run_res = subprocess.run(
                [self._strace, "--version"],
                cwd=self._bazel_workspace,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            stderr = run_res.stderr.decode("utf-8").rstrip()
            if stderr:
                self.Con.warning(
                    "'strace --version' produced output to stderr which is unexpected for return code 0. It might not work properly. STDERR =",
                    stderr,
                )
            stdout = run_res.stdout.decode("utf-8").rstrip()
            self.Con.info(f"Using {stdout}")
        except Exception as e:
            raise YacceException("Failed to check 'strace' utility version: {e}")

    def _bazelClean(self, expunge: bool) -> None:
        self.Con.info(f"Cleaning the build{' with --expunge' if expunge else ''}...")
        args = ("clean",) + (("--expunge",) if expunge else ())
        try:
            self._queryBazelThrow(*args)
            self.Con.debug("Successfully cleaned the build")
        except Exception as e:
            raise YacceException(f"Failed to '{self._bazel} {' '.join(args)}': {e}")

    def _getBazelServerPid(self) -> int:
        self.Con.debug("Querying Bazel's server PID...")
        try:
            pid_str = None
            pid_str = self._queryBazelThrow("info", "server_pid")
            pid = int(pid_str)
            if pid <= 0:
                raise YacceException(f"Invalid Bazel server PID '{pid}' returned")
            self.Con.debug(f"Bazel server PID is {pid}")
            return pid
        except ValueError:
            raise YacceException(f"Non-integer Bazel server PID '{pid_str}' was returned")
        except Exception as e:
            raise YacceException(f"Failed to query Bazel server PID: {e}")

    def _launchStrace(self, server_pid: int, log_file: str) -> subprocess.Popen:
        try:
            strace_cmd = [
                self._strace,
                "--follow-forks",
                "--trace=execve,execveat,exit",
                "--status=successful",
                "--string-limit=8192",
                "--absolute-timestamps=format:unix,precision:us",
                f"--attach={server_pid}",
                f"--output={log_file}",
            ]
            self.Con.debug(
                f"Launching strace on server_pid {server_pid}, logging to '{log_file}' with a command:",
                strace_cmd,
            )

            strace_proc = subprocess.Popen(
                strace_cmd,
                cwd=self._bazel_workspace,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # DEVNULL,  # unfortunately strace outputs everything to stderr
            )
            retcode = strace_proc.poll()
            if retcode is not None:
                if strace_proc.stderr:
                    stderr = strace_proc.stderr.read().decode("utf-8").rstrip()
                else:
                    stderr = "<unknown>"
                raise YacceException(
                    f"Failed to launch strace on PID {server_pid}, it exited immediately with code {retcode}. STDERR='{stderr}'"
                )

            self.Con.debug(f"Strace launched with PID {strace_proc.pid}")
            return strace_proc
        except Exception as e:
            raise YacceException(f"Failed to launch strace on PID {server_pid}: {e}")

    def _runBazelWithStrace(
        self,
        log_file: str,
        keep_log: str,
        build_system_args: list,
        build_cwd: str,
        build_shell: str,
        ensure_build_succeeds: bool,
    ) -> None:
        assert log_file and build_system_args and build_cwd and build_shell
        server_pid = self._getBazelServerPid()

        shown_begin = False
        with self._launchStrace(server_pid, log_file) as strace:
            try:
                build_system_str = " ".join([shlex.quote(s) for s in build_system_args])
                self.Con.info(
                    "Running build from shell '",
                    build_shell,
                    ", directory '",
                    build_cwd,
                    "' command '",
                    build_system_str,
                    "'",
                )
                retcode = -100500
                if "linux" == sys.platform:
                    self.Con.yacce_end()
                    cwd = os.getcwd()
                    os.chdir(build_cwd)
                    try:
                        retcode = pty.spawn([build_shell, "-c", build_system_str])
                    except KeyboardInterrupt:
                        self.Con.yacce_begin()
                        shown_begin = True
                        self.Con.warning(
                            "Build interrupted by user. If you want to abort yacce too, please Ctrl-C again."
                        )
                        retcode = -1
                    finally:
                        os.chdir(cwd)
                else:
                    self.Con.yacce_end()
                    try:
                        retcode = subprocess.run(
                            build_system_args,
                            cwd=build_cwd,
                            check=False,
                            shell=True,
                            executable=build_shell,
                        ).returncode
                    except KeyboardInterrupt:
                        self.Con.yacce_begin()
                        shown_begin = True
                        self.Con.warning(
                            "Build interrupted by user. If you want to abort yacce too, please Ctrl-C again."
                        )
                        retcode = -1

                if not shown_begin:
                    self.Con.yacce_begin()
                    shown_begin = True

                if retcode != 0:
                    msg = f"Build command exited with code {retcode}."
                    if ensure_build_succeeds:
                        raise YacceException(
                            f"{msg} Yacce was requested to ensure build succeeds, so aborting. "
                            "If you want to proceed anyway, don't set --ensure_build_succeeds argument."
                        )
                    self.Con.warning(
                        f"{msg} Processing the log anyway even though the log might be incomplete "
                        "or corrupted. To enforce yacce failure if build fails, set --ensure_build_succeeds argument."
                    )

            except Exception as e:
                if not shown_begin:
                    self.Con.yacce_begin()
                    shown_begin = True
                raise YacceException(f"Failed to run the build command: {e}")

            finally:
                if not shown_begin:
                    self.Con.yacce_begin()
                    shown_begin = True
                self.Con.debug("Stopping strace.")
                strace.send_signal(signal.SIGINT)

                self.Con.debug("Waiting for completion...")
                # _, stderr = strace.communicate()
                strace.wait()
                self.Con.debug("Strace finished.")
                """
                if stderr:
                    stderr = stderr.decode("utf-8").rstrip()
                    self.Con.warning(
                        "'strace' produced output to stderr which might indicate problems. STDERR =",
                        stderr,
                    )"""

                if "never" == keep_log:
                    if os.path.exists(log_file):
                        self.Con.info(
                            f"Removing log file '{log_file}' as requested by --keep_log=never."
                        )
                        os.remove(log_file)


def mode_bazel(Con: LoggingConsole, args: argparse.Namespace, unparsed_args: list) -> int:
    args, build_system_args = _getArgs(Con, args, unparsed_args)

    Con.debug("bazel mode args: ", args)
    Con.debug("build_system_args:", build_system_args)
    Con.cleanNumErrors()

    bzl = BazelWrap(Con, args)

    if args.from_log:
        if build_system_args:
            raise YacceException(
                "Mode '--from_log' isn't intended for running a build system. Use a live mode instead. "
                "(see 'yacce bazel --help' for details)"
            )
    else:
        bzl.runBuild(args, build_system_args)

    # Only after finishing the build we could query bazel properties. Updating args.cwd from bazel
    bzl.fixCwdAsExecutionRoot(args)

    p = BazelParser(Con, args)

    # TODO proper handling of args.external
    dest_dir = (
        args.dest_dir
        if hasattr(args, "dest_dir") and args.dest_dir
        else os.path.dirname(args.log_file)
    )
    p.storeJsons(dest_dir, args.external, args.save_duration, args.save_line_num)

    # 'never' has already been processed and 'always' basically mean 'forget it'
    if not args.from_log and args.keep_log == "if_errors":
        if Con.getNumErrors() > 0:
            Con.info(
                "There were",
                Con.getNumErrors(),
                "reported during the run, hence according to --keep_log=if_errors, leaving the log file '",
                args.log_file,
                "' in place",
            )
        else:
            if os.path.exists(args.log_file):
                Con.info("No errors were detected, hence removing log file '", args.log_file, "'")
                os.remove(args.log_file)

    return 0
