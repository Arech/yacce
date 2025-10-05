# Yacce is a *non-intrusive* compile_commands.json extractor for Bazel (experimental, local compilation, Linux only)

Yacce extracts `compile_commands.json` and build system insights from a build system by supervising
the local compilation process with `strace`. Yacce primarily supports Bazel (other build systems
might be added later).

## Motivation

Only open-source history of Bazel development spans for over a decade, and yet - it has a ton of C++
specific features, while one of very important ones, - generation of `compile_commands.json`, - is
still not there. There situation is so ridiculous that even G's own commands had to invent and
support their own "wheels" to make compile_commands for their projects (sample refs: [1](https://openxla.org/xla/lsp#how_do_i_generate_compile_commandsjson_for_xla_source_code),
[2](https://cs.opensource.google/pigweed/pigweed/+/master:pw_ide/py/pw_ide/compile_commands_generator.py)).

But there already exist several decent generic `compile_commands.json` extractors, external to Bazel,
with `hedronvision/bazel-compile-commands-extractor` being the most well-known and, probably, respected.
Why bother?

There are several reasons:
- their usability is horrible, - extractors I've seen (I don't claim I saw all
of them in existence!) requires one to make a certain nontrivial modification of the build system
and specifically list there what targets and how exactly are going to be compiled just to spew the
damn compile_commands!
    - what if I'm supporting a complex project spanning across multiple code bases, that don't employ
    such extractor, and I have to work on many code branches across many different remote machines?
    I'd have to first extract potentially branch specific build targets, and then manually inject
    extractor's code into the build system. Do this a few times a week, and you'll start to genuinely
    dislike Bazel (if you don't yet).
    - why it can't be made as simple as, for example, in CMake with its `-DCMAKE_EXPORT_COMPILE_COMMANDS=1` ?
- completely orthogonal to usability there is an InfoSec consideration: what if I don't want to add
a 3rd party, potentially compromisable dependency, into my project? I have no idea what it does
internally there and what could it inject into my binaries under the hood. Why does an extractor
have to be intrusive?

## Benefits of yacce

Supervising a build system doing compilation with a standard system tool have several great benefits:
- Yacce is super user-friendly and simple to use. It's basically a drop-in prefix for a shell command
you could use to build the project, be it `bazel build ...`, `bazel run ...`, or even
`MY_ENV_VAR="value" ./build/compile.sh arg1 .. argn`. Just prepend your build command with `yacce -- ` and hit enter.
- `strace` lets yacce see real compiler invocations, hence `compile_commands.json` made from strace
log reflects the way you build the project precisely, with all the custom configuration details
you might have used, and independently of what the build system lets you to know and not know about that.
- Compilation of all external dependencies as well as linking commands, are automatically included (with a
microsecond timing resolution, if needed).
- There are just no InfoSec risks by design (of course, beyond running a code of yacce itself,
though it's rather small and is easy to verify). Yacce is completely external to the build system and
doesn't interfere with it in any way.

## Limitations

However, the supervising approach have some intrinsic limitations, which make it not suitable
for all use-cases supported by Bazel:

- `strace` needs to be installed (`apt install strace`), which limits yacce to basically **Linux only**.
- **compilation could only happen locally**, on the very same machine, on which yacce runs. This
leaves out a Bazel RBE, and requires building the project from an empty cache, if the cache is used.
- while yacce doesn't care how you launch the build system and lets you use any script or a command
you like, eventually, it should **build only one Bazel workspace**. Yacce does not check if this
limitation is respected by a user, though typically, it's easy to fulfil.

If this is a hard no-go for you, ~~suffer with~~ consider other extractor, such as the above mentioned
[hedronvision's](https://github.com/hedronvision/bazel-compile-commands-extractor) tool.

There are some "soft" limitations that might be removed in the future, such as:
1. currently yacce does not support incremental builds (i.e. you'd have to fully recompile the
project to update `compile_commands.json`). The fix for that is simple and just a matter of implementation.
2. It looks like `strace` sometimes might produce...misformed logs. I always get what I expect on
Debian 12-13, but I had to implement a special handling for unexpected line-breaks it sometimes
produces on Ubuntu 22.04. I can't guarantee that there are no other quirks that could break log parsing.
3. Bazel is monstrous. While yacce works nicely with some code bases, there might be edge cases, that
aren't properly handled.
4. One can't just take all the compiler invocations a build system does and simply dump them to a
`compile_command.json`. A certain filtering is mandatory, and that requires parsing compiler's arguments:
    - gcc- and clang- compatible compilers are the only supported.
    - 100% correct compiler's argument parsing requires implementing 100% of compiler's own CLI
  parser, which is not done and will never be done. Yacce's parser is good enough for many uses, but
  certainly not for all. Yacce could diagnose some edge cases and warn of potentially
  incorrect results, but, again, - certainly not all edge cases are covered by the diagnostics.

You're unlikely to hit the last two. However, if you will, you know what to do (please file a bug report, or better submit a PR).

Give yacce a try with `pip install yacce`! Prepend the build command with `yacce -- ` and let me know how it goes!

> [!IMPORTANT]
> Always add a clangd server startup argument `--compile-commands-dir=` to point to your directory
> with `compile-commands.json` file. It might not work properly without that!
>
> In VSCode it's as simple as opening Settings / Extensions / clangd, and adding to the first setting
> `clangd.arguments` an item with a content `--compile-commands-dir=${workspaceFolder}` (or any
> other path). It might not use the `compiler_commands.json` without that.

## Examples of extracting compile_commands.json from Bazel with yacce

First, install yacce with `pip install yacce`. Python 3.10+ is supported.

Second, ensure you have [strace](https://man7.org/linux/man-pages/man1/strace.1.html) installed with
`sudo apt install strace`. Some distributions have it installed by default.

### Example 1, extracting compile_commands.json for JAX (`jaxlib`)

*An elaborate way to say "just prepend your build script with `yacce -- `"*

JAX is one of Google's machine learning frameworks. Part of it is written in Python, while high
performance code is in C++. A compiled part is called `jaxlib` and is responsible for parts of JAX
common for all execution backends, and for a CPU-based backend. We'll be using currently the latest
JAX v0.7.2 here.

Compiling jaxlib is a good first example for yacce, because it has quite a sizeable code base with
at least one dependency, XLA (a machine learning compiler), that is almost always being worked upon
in parallel with the jaxlib itself. By default, JAX's build system fetches XLA from a
[pinned commit](https://github.com/jax-ml/jax/blob/jax-v0.7.2/third_party/xla/revision.bzl#L24), but
since we emulate a real developer work here, we'll also checkout that pinned commit to a local directory,
so we could work on it, and then tell JAX's build system to use that local directory instead of the
pinned commit. Yacce will automatically generate a single `compile_commands.json` for both jaxlib
and XLA.

First, let's setup the workspace:
```bash
mkdir /src_jax && cd /src_jax  # the dir for both JAX and XLA sources
( git clone https://github.com/openxla/xla && cd ./xla \
  && git checkout 0fccb8a6037019b20af2e502ba4b8f5e0f98c8f6 )
git clone --branch jax-v0.7.2 --depth 1 https://github.com/jax-ml/jax
```
Now we have `/src_jax/jax` directory having v0.7.2 JAX commit checkout, and `/src_jax/xla` having
the same XLA commit, that's designed for JAX v0.7.2. Time to build!

Without yacce, I'd use the following command inside the `./jax` directory:
```bash
jax$ python3 ./build/build.py build --wheels=jaxlib --verbose --use_clang false \
  --target_cpu_features=native --bazel_options=--override_repository=xla=../xla
```

With yacce, it's just prepending the command with `yacce -- ` like this:

```bash
jax$ yacce -- python3 ./build/build.py build --wheels=jaxlib --verbose --use_clang false \
  --target_cpu_features=native --bazel_options=--override_repository=xla=../xla
```

At the start, yacce will test if strace and bazel are available, and then it will ask your permission to
execute `bazel clean` command. Starting with a clean state is mandatory for yacce to capture all
compilation commands, but since cleaning and rebuilding from scratch might be expensive, yacce tries
to prevent accidental harm by asking a permission. You can authorize it to do that from command line
with `--clean always` argument put before `--` separator like this: `yacce --clean always -- `.

After doing `bazel clean`, yacce will setup `strace` supervision over Bazel's server execution, and
then launch the build script. When the build finishes, yacce will start strace log processing
and in few seconds it'll write `/src_jax/jax/compile_command.json` containing all C++ source files
used for `jaxlib` and for parts of XLA, that were required by jaxlib.

> [!TIP]
> If a build system fails or cancelled manually (such as by hitting Ctrl+C), by default yacce will
> still try to process the gathered strace log and produce at least something. You can use that to
> you advantage by running scripts invoking other bazel commands, such as `bazel run` or
> `bazel test` under yacce. Just be aware that not all programs are friendly to strace.
> For example, address sanitizer can't work under strace, so all tests compiled with the sanitizer
> will fail under yacce/strace.

Now fire up your IDE and point `clangd` to that file, so it starts indexing it. In VSCode with `clangd`
extension installed, if `/src_jax` is the main opened directory (workspace), then one could open
Settings / Extensions / clangd, and click "Add Item" for `clangd.arguments` settings, putting
`--compile-commands-dir=${workspaceFolder}/jax` there and then do ctrl+shift+p, "clangd.restart".

### Example 2, how to extract compile_commands.json for AMD's flavor of JAX (handling several Bazel workspaces)

To build JAX running on AMD GPUs, one would have to work with a `rocm/rocm-jax` meta-repository.
How to set up the dev environment is described in the
[appropriate document](https://github.com/ROCm/rocm-jax/blob/0acd84e9d095814f3c2d487ff5c326e56c6d0dc3/DEVSETUP.md) of the repo (we'll
be using a specific commit `0acd84e9d095814f3c2d487ff5c326e56c6d0dc3` of the repo for reproducibility,
it corresponds to JAX v0.6.0).

The most important feature of this project in the context of yacce is that after the dev setup is finished
(more precisely, after `python3 stack.py develop` has been run and
`./jax_rocm_plugin/Makefile` is generated. Hint: take a look into this file to understand what
commands actually invoked to build the wheels!), the topmost directory of the repo checkout will contain
3 bazel workspaces:
- `./jax_rocm_plugin` is the main workspace one would use to build the `plugin` and `pjrt` wheels,
- `./jax` is the checkout of a relevant commit of the upstream JAX, that is used as a
dependency of the ROCm support wheels, and might be needed if one is to rebuild the `jaxlib` wheel
using own XLA checkout.
- `./xla` is the checkout of the relevant commit of AMD's fork of XLA. It's used to build
the wheels as a dependency, and might be useful for running XLA tests on its own.

Typical build procedure to make all 3 wheels (`plugin`, `pjrt` and `jaxlib`) is:
```bash
jax_rocm_plugin$ make refresh refresh_jaxlib
```
However, this is the case where we can't just prepend `yacce -- ` and get away with it, because
from the commands in `./jax_rocm_plugin/Makefile` it's obvious that `make refresh` uses Bazel
workspace `./jax_rocm_plugin`, while `make refresh_jaxlib` uses workspace in `./jax`. Yacce can
monitor only a single bazel workspace at a time, so we have to split these in two. No special treatment
for the first part, `make refresh`, is needed, it's just a simple:
```bash
jax_rocm_plugin$ yacce -- make refresh
```
But we should do something for the second part, `make refresh_jaxlib`: by default, yacce produces
two files in the current directory:
- `strace.txt` recording log file (it's useful for re-running
`compile_commands.json` extraction with a different yacce settings by using old compilation recording,
but if know which yacce settings you need upfront, you can use `yacce --keep_log if_errors -- ` to
remove it automatically if build succeeds), and 
- `compile_commands.json` generated from the log.

While for building jaxlib we can instruct yacce to generate these files in a different directory
(`./jax` is the most suitable one), it's easier just to invoke yacce from this different directory
and instruct it to switch to a different directory before running the build script. The latter
is done with a single `--build_cwd` argument. Here's the full commands line for that to execute from
`./jax` directory:
```bash
jax$ yacce --build_cwd ../jax_rocm_plugin -- make refresh_jaxlib
```
This will generate `compile_commands.json` for jaxlib in `./jax` directory.

### Example 3, extracting compile_commands.json while running bazel test for XLA

Yacce can extract compile_commands.json from running any script as long as it eventually
builds a single bazel workspace. Running `bazel test` when no tests are built is an example where
this could be useful. We could just run it under yacce and after bazel reports it finished compiling
of all tests, just cancel tests execution with Ctrl+C to let yacce proceed with compile_commands
generation faster.

In relation to tests of ROCm fork of XLA (we'll use a dev environment from the previous example),
there are two things to be aware of:
1. The [testing script](github.com/ROCm/xla/blob/rocm-jaxlib-v0.6.0/build_tools/rocm/run_xla.sh)
`./build_tools/rocm/run_xla.sh` uses Bazel `--disk_cache` argument to setup local caching. This
speeds up re-builds of the project, but prevents yacce from seeing compilation of a cached entity.
The fix for this is either disable disk caching for run under yacce (require modification of the
`run_xla.sh` script) or just start from a clean cache. We'll do the latter by calling
`rm -rf /tf/disk_cache/rocm-jaxlib-v0.6.0` before running yacce.
2. XLA tests are built with sanitizers, and most of them aren't working well under `strace`.
Typically this results in just test failures. This is fine, since we're running the tests script for producing
`compile_commands.json`. Just be aware of that.
  - another thing to be aware of is that `clangd`, as of now, chokes when it finds use of sanitizers
  in `compile_commands.json`, but yacce by default removes compilation arguments related to that.
  If needed, you can control that behaviour with certain yacce switches, see below.

Therefore, we need to run yacce in the following manner from `./xla` directory:
```bash
xla$ rm -rf /tf/disk_cache/rocm-jaxlib-v0.6.0 ; \
    yacce -- ./build_tools/rocm/run_xla.sh
```

After bazel reports that it finished compiling and runs only test jobs (these will start to fail due
sanitizers incompatibility with strace), hit Ctrl+C to stop tests execution early and let yacce generate
`compile_commands.json` from the build log.


## Modes of yacce operation and how to configure them

Yacce currently have at least 2 main modes of operation and few submodes, and have a pretty
extensive configurability. Just run `yacce --help` to see the details. Below is a typical output you
can expect, but please note this could be obsolete. Please always query yacce for the actual features
and settings it supports.

### Main options

```
$ yacce -h
usage: yacce [-h] [--debug {0,1,2,3,4,5,6}] [--colors | --no-colors] {bazel,from_log} ...

Yacce extracts compile_commands.json and build system insights from a build system by
supervising the local compilation process with strace.
Primarily supports Bazel (other build systems might be added later).
--> Homepage: https://github.com/Arech/yacce

positional arguments:
  {bazel,from_log}      Modes of operation. Use "--help" with each mode to get more
                        information.
    bazel               Runs a given build system based on Bazel in a shell and extracts
                        compile_commands.json from it (possibly with individual
                        compile_commands.json for each external dependency).
                        This is a default mode activated if the mode specification is just
                        omitted.
                        Hint: use 'yacce bazel --help' to get CLI arguments help.
    from_log            [dbg!] Generates a possibly NON-WORKING(!) compile_commands.json from
                        a strace log file.
                        This mode features the most generic way to parse strace output and
                        since the log generally lacks some important information (such as the
                        working directory in case of a Bazel), it may produce a non-working
                        compile_commands.json. The mode is primarily intended for debugging
                        purposes as it doesn't use any knowledge about the build system used
                        and just parses the strace log file and turns it into
                        compile_commands.json as is.
                        Hint: use 'yacce from_log --help' to get CLI arguments help.

options:
  -h, --help            show this help message and exit
  --debug {0,1,2,3,4,5,6}
                        Minimum debug level to show. 0 is the most verbose.
                        Default level is (info=) 2. Setting it higher than a (warning=) 3 is
                        not recommended.
  --colors, --no-colors
                        Controls if the output could be colored. (default: True)
```

### Options for Bazel mode

```
$ yacce bazel -h
usage: yacce [global options] [bazel] [options (see below)] [-- shell command eventually
invoking Bazel]

Yacce extracts compile_commands.json and build system insights from a build system by
supervising the local compilation process with strace.
Primarily supports Bazel (other build systems might be added later).
--> Homepage: https://github.com/Arech/yacce

Mode 'bazel' is intended to generate compile_commands.json from tracing execution of a 'bazel
build' or any other shell command invoking Bazel using the Linux's strace utility. Hence it
only supports compilation of a single Bazel workspace (including its all external
dependencies) happening locally. If you are using Bazel's remote caching feature, including '
--disk_cache', please make sure you're starting with a clean cache, otherwise yacce won't see
compilation of cache hits.

options:
  -h, --help            show this help message and exit
  --log_file path/to/file
                        Write strace log to and/or read it from this file.
                        See also '--from_log'.
                        Default: 'strace.txt' in the current directory,
                        /home/arech/Documents/src/my/yacce/mantest/strace.txt
  --external {ignore,combine-with-overridden,to-files,to-external,combine-all}
                        Determines what to do when a compilation of a project's dependency
                        source file (from 'external/' subdirectory) is found.
                        - One option is to just to 'ignore' (remove) it and to leave in the
                        resulting compile_commands.json *only* commands directly related to
                        the project.
                        - The default option 'combine-with-overridden' produces a single
                        compile_commands.json containing main project's files as well as
                        dependencies that are stored *outside* of their expected location at
                        '$(bazel info output_base)/external/<repo>' (this typically happens
                        when you override a dependency repo location for Bazel when you work
                        on the project and its dependency simultaneously).
                        - Option 'to-files' produces individual files nearby the main
                        compile_commands.json, named like'compile_commands_ext_<repo>.json'
                        for each external dependency '<repo>' (this might be useful for
                        manual inspection).
                        - Option 'to-external' differs from 'to-files' only in the location
                        and naming of the resulting files. 'to-external' produces an
                        individual compile_commands.json in each external dependency's
                        directory and is useful when you're going to open the dependency
                        directory in a parallel IDE for a close inspection.
                        - The 'combine-all' option just writes all compilation commands (for
                        the main project and its dependencies) into a single file.
                        See '--dest_dir' argument for a default location and/or override for
                        the main project's compile_commands.json file.
                        NOTE that since currently yacce doesn't properly process compiler
                        invocations that aren't related to compiling C or C++ sources (such
                        as linking only, or compiling ASM files), if '--other_commands' flag
                        is specified, a compound other_commands.json (containing all other
                        invocations of a compiler for the main project and its externals)
                        will be saved nearby the main compile_commands.json irrespective of a
                        value of this flag.
  --bazel_command command_or_filepath
                        Override which command to run to communicate with the instance of a
                        Bazel for the build system.
                        You don't typically need this argument, if you have bazelisk
                        installed.
                        To set the workspace directory see '--bazel_workspace' argument.
                        Default: bazel
  --bazel_workspace path/to/dir
                        Overrides Bazel workspace directory to set a current directory
                        context for the bazel command (see '--bazel_command').
                        This is useful if yacce needs to be run from an outside of that
                        workspace. Note that any dir under a real workspace would also work
                        here.
                        Default: a current working directory
                        '/home/arech/Documents/src/my/yacce/mantest'.
  --build_cwd path/to/dir
                        By default, a shell command to start the build is invoked from the
                        Bazel workspace directory (see '--bazel_workspace'). This argument
                        allows to override that and set a different directory as a cwd for
                        the build command.
                        Note that this is different from '--cwd' argument, which for the
                        'bazel --from_log' mode of yacce specifies a value of '$(bazel info
                        execution_root)' directory.
  --build_shell shell_to_use
                        Build command is executed by passing it to a shell. By default,
                        'bash' is used, but you can override that with this argument.
  --ensure_build_succeeds, --no-ensure_build_succeeds
                        By default yacce only warns if the build command fails (exits with a
                        non-zero code) and it tries to process the strace log file to produce
                        some results anyway. If you want to make sure that yacce will only
                        use a full log of a successful build, set this argument to enforce
                        yacce failure if the build fails.
                        (default: False)
  --cwd path/to/dir     Path to the working directory of the compilation.
                        This value goes to a 'directory' field of an entry of
                        compile_commands.json and is used to resolve relative paths found in
                        the command. If '--ignore-not-found' argument isn't set, yacce will
                        try to test if mentioned files exist in this directory and warn if
                        they aren't. Note that passing the file existence test helps, but
                        doesn't guarantee that the resulting compile_commands.json will be
                        correct.
                        In the 'yacce bazel --from_log' mode, this argument overrides an
                        output of '$(bazel info execution_root)' (i.e. this enables parsing
                        of an existing log file without querying its build system). In the
                        default live mode (when no '--from_log' argument is specified) this
                        argument is either has to be unset, or match the output of '$(bazel
                        info execution_root)'.
  --ignore-not-found, --no-ignore-not-found
                        If the flag is set, yacce will not test if files to be added to .json
                        exists, and will not attempt to test if an invoked compiler is a
                        script wrapper.
                        (default: False)
  -o, --other_commands, --no-other_commands
                        If set, yacce will also generate other_commands.json file.
                        This file has a similar to compile_commands.json format, but contains
                        all other compiler invocations found that aren't useful for gathering
                        C++ symbol information of the project, but handy to get insights
                        about the build in general (such as for compiling assembler sources
                        or for linking).
                        Note that yacce currently does not implement attribution of other
                        compilation commands to the project's external dependencies. I.e. all
                        other commands related to compiling non-C++ sources and linking will
                        be combined into a single other_commands.json file irrespective of '
                        --external' argument setting. (default: False)
  --save_duration, --no-save_duration
                        If set, yacce will add a 'duration_s' field into the resulting .json
                        that contain how long the command run in seconds with a microsecond
                        resolution.
                        This feature currently doesn't have automated use, but the file can
                        be inspected manually, or with a custom script to obtain build system
                        performance insights.
                        WARNING: current clangd gets upset when it finds a field it doesn't
                        know, so enabling this option might prevent you from using clangd
                        with the resulting file!
                        (default: False)
  --save_line_num, --no-save_line_num
                        If set, yacce will add a 'line_num' integer field into the resulting
                        .json that contain a line number of the compiler call in the strace
                        log file.
                        Useful for debugging, but have no automated use.
                        WARNING: current clangd gets upset when it finds a field it doesn't
                        know, so enabling this option might prevent you from using clangd
                        with the resulting file!
                        (default: False)
  --discard_outputs [PathFilter ...]
                        A build system can compile some dummy source files only to gather
                        information about compiler capabilities. Presence of these files in
                        the compile_commands.json aren't usually helpful. Typically, such
                        files are placed into /tmp or /dev/null, but other variants are
                        possible.
                        This setting lets you customize finding which compiler output files
                        should lead to ignoring the whole compilation call.
                        See a 'PathFilter specification' section below for details.
                        Pass an empty string "" to disable. Accepts multiple values at once.
                        Default: ['/dev/null', '/tmp/+', '+/.cache/ccache/tmp/+'].
  --discard_sources [PathFilter ...]
                        Similar to --discard_outputs, but controls which source file path
                        patterns should lead to ignoring the compiler call.
                        See a 'PathFilter specification' section below for details.
                        Pass an empty string "" to disable. Accepts multiple values at once.
                        Default: [''].
  --discard_args_with_pfx [+compiler_arg_prefix ...]
                        Certain compiler arguments, such as sanitizers, are known to choke
                        clangd. Some others like those concerning build reproducibility might
                        be useless for C++ symbols.
                        Set a value of this parameter to a sequence of prefixes to match and
                        remove such compiler arguments.
                        Pass an empty string "" to disable. Accepts multiple values at once.
                        ATTENTION: since Python's argparse always treats a leading dash in a
                        CLI argument as a script's argument name, but not value, use a plus
                        sign '+' instead of a dash '-' to specify a leading dash. Example:
                        instead of '-fsanitize' use '+fsanitize'.
                        Default: ['+fsanitize'].
  --discard_args [+compiler_arg_or_args_pair_spec ...]
                        Similarly to '--discard_args_with_pfx', values for this argument
                        define a set of compiler arguments (such as '-DMY_DEF=VALUE') or
                        pipe-delimited argument pairs (like a single token value
                        '-I|/certain/dir' defines a two token pair '-I /certain/dir') that
                        will be removed from a compiler invocation.
                        Note that a single token specification of a '-D' compiler argument
                        has a special handling and also addresses its two token alternatives.
                        Pass an empty string "" to disable. Accepts multiple values at once.
                        ATTENTION: since Python's argparse always treats a leading dash in a
                        CLI argument as a script's argument name, but not value, use a plus
                        sign '+' instead of a dash '-' to specify a leading dash. For the
                        above it's '+DMY_DEF=VALUE' and '+I|/certain/dir'.
                        Default: ['+DADDRESS_SANITIZER'].
  --enable_dupes_check, --no-enable_dupes_check
                        If set, yacce will report if a pair <source, output> isn't unique.
                        Usefulness of this flag solely depends on actual build system
                        implementation. Some might use lots of temporary compilations just to
                        gather compiler capabilities which could lead to an avalanche of
                        false positives. This could be mitigated with --discard* family of
                        flags, but this requires manual intervention, hence it's disabled by
                        default.
                        (default: False)
  -c [compiler_basename_or_path_fragment ...], --compiler [compiler_basename_or_path_fragment ...]
                        Adds an absolute path, a basename, a path suffix, or a path prefix
                        (prepend it with a plus '+' symbol) of a custom compiler to the set
                        of compilers already detectable by yacce. Accepts multiple values at
                        once.
  --not_compiler [compiler_basename_or_path_fragment ...]
                        You can prevent a certain absolute path, a basename, a path suffix,
                        or a path prefix (prepend it with a plus '+' symbol) from being
                        treated as a compiler by using this argument. Accepts multiple values
                        at once.
  --enable_compiler_scripts, --no-enable_compiler_scripts
                        By default, yacce doesn't treat a script (classified by testing for
                        shebang '#!' sequence in the first 2 bytes of the file) invocation as
                        a compiler invocation and ignores it. Set this option when this
                        behavior is unwanted.
                        (default: False)
  -d dir/path, --dest_dir dir/path
                        Destination directory in which yacce should create resulting .json
                        files. Must exist.
                        Default: directory of the log file (see '--log_file')

Log mode, uses existing strace log and is mutually exclusive with the live mode:
  --from_log            Toggles a mode in which yacce will only parse an existing log file
                        specified by '--log_file', but will not invoke a build system to spy
                        on.
                        Mutually exclusive with '--keep_log' and requires no build system
                        arguments passed (no '--' argument and anything after it).
                        Default: not set, i.e. the mode is not activated.

Live mode (default), runs a Bazel build system and is mutually exclusive with the log mode:
  --keep_log {if_errors,always,never}
                        Determines conditions of keeping of the strace log file after yacce
                        finishes. Mutually exclusive with '--from_log'.
                        Default is 'always' as it might be useful to run yacce in the log
                        mode with different arguments later on the same log file.
  --clean {always,expunge,never}
                        Determines, if a 'bazel clean' or 'bazel clean --expunge' commands
                        should be executed before running the build.
                        Note that if cleaning is disabled, cached (already compiled) sources
                        will be invisible to yacce and hence will not make it into resulting
                        compiler_commands.json! (iterative updates aren't supported yet)
                        Default: not specified, yacce will ask if running 'bazel clean' is
                        ok.

PathFilter specification:
-------------------------
Filters are tested before and after applying working directory to a path (if the path tested
isn't an abs path), and after expanding an abs path to a realpath(). Recognizable filter
specifications are:
- exact match: filter not starting or ending on a plus '+' sign
- match prefix: filter ending with a plus '+' sign. A path starting with the string before
the plus sign matches the filter.
- match suffix: filter beginning with a plus '+' sign. A path ending with a substring that
follows the plus sign matches the filter.
- substring match: filter beginning with and ending with a plus '+' sign matches as a
substring.
```

### Generic log parsing mode options

```
$ yacce from_log -h
usage: yacce from_log [-h] [--cwd path/to/dir] [--ignore-not-found | --no-ignore-not-found]
                      [-o | --other_commands | --no-other_commands]
                      [--save_duration | --no-save_duration]
                      [--save_line_num | --no-save_line_num]
                      [--discard_outputs [PathFilter ...]]
                      [--discard_sources [PathFilter ...]]
                      [--discard_args_with_pfx [+compiler_arg_prefix ...]]
                      [--discard_args [+compiler_arg_or_args_pair_spec ...]]
                      [--enable_dupes_check | --no-enable_dupes_check]
                      [-c [compiler_basename_or_path_fragment ...]]
                      [--not_compiler [compiler_basename_or_path_fragment ...]]
                      [--enable_compiler_scripts | --no-enable_compiler_scripts]
                      [-d dir/path]
                      log_file

Yacce extracts compile_commands.json and build system insights from a build system by
supervising the local compilation process with strace.
Primarily supports Bazel (other build systems might be added later).
--> Homepage: https://github.com/Arech/yacce

Mode 'from_log' is a supplementary mode that generates a compile_commands.json from a strace
log file without using any additional information about build system.
ATTENTION: this mode is intended for debugging purposes only and most likely will not produce
a correct compile_commands.json due to a lack of information about the build process details.
If you want to regenerate compile_commands from a log file for Bazel, use 'yacce bazel
--from_log' instead.

positional arguments:
  log_file              Path to the strace log file to parse.

options:
  -h, --help            show this help message and exit
  --cwd path/to/dir     Path to the working directory of the compilation.
                        This value goes to a 'directory' field of an entry of
                        compile_commands.json and is used to resolve relative paths found in
                        the command. If '--ignore-not-found' argument isn't set, yacce will
                        try to test if mentioned files exist in this directory and warn if
                        they aren't. Note that passing the file existence test helps, but
                        doesn't guarantee that the resulting compile_commands.json will be
                        correct.
                        In the 'from_log' mode a relative path specification is resolved to
                        the absolute path using a directory of the log file.
                        Default: directory of the log file.
  --ignore-not-found, --no-ignore-not-found
                        If the flag is set, yacce will not test if files to be added to .json
                        exists, and will not attempt to test if an invoked compiler is a
                        script wrapper.
                        (default: False)
  -o, --other_commands, --no-other_commands
                        If set, yacce will also generate other_commands.json file.
                        This file has a similar to compile_commands.json format, but contains
                        all other compiler invocations found that aren't useful for gathering
                        C++ symbol information of the project, but handy to get insights
                        about the build in general (such as for compiling assembler sources
                        or for linking).
                        (default: False)
  --save_duration, --no-save_duration
                        If set, yacce will add a 'duration_s' field into the resulting .json
                        that contain how long the command run in seconds with a microsecond
                        resolution.
                        This feature currently doesn't have automated use, but the file can
                        be inspected manually, or with a custom script to obtain build system
                        performance insights.
                        WARNING: current clangd gets upset when it finds a field it doesn't
                        know, so enabling this option might prevent you from using clangd
                        with the resulting file!
                        (default: False)
  --save_line_num, --no-save_line_num
                        If set, yacce will add a 'line_num' integer field into the resulting
                        .json that contain a line number of the compiler call in the strace
                        log file.
                        Useful for debugging, but have no automated use.
                        WARNING: current clangd gets upset when it finds a field it doesn't
                        know, so enabling this option might prevent you from using clangd
                        with the resulting file!
                        (default: False)
  --discard_outputs [PathFilter ...]
                        A build system can compile some dummy source files only to gather
                        information about compiler capabilities. Presence of these files in
                        the compile_commands.json aren't usually helpful. Typically, such
                        files are placed into /tmp or /dev/null, but other variants are
                        possible.
                        This setting lets you customize finding which compiler output files
                        should lead to ignoring the whole compilation call.
                        See a 'PathFilter specification' section below for details.
                        Pass an empty string "" to disable. Accepts multiple values at once.
                        Default: ['/dev/null', '/tmp/+', '+/.cache/ccache/tmp/+'].
  --discard_sources [PathFilter ...]
                        Similar to --discard_outputs, but controls which source file path
                        patterns should lead to ignoring the compiler call.
                        See a 'PathFilter specification' section below for details.
                        Pass an empty string "" to disable. Accepts multiple values at once.
                        Default: [''].
  --discard_args_with_pfx [+compiler_arg_prefix ...]
                        Certain compiler arguments, such as sanitizers, are known to choke
                        clangd. Some others like those concerning build reproducibility might
                        be useless for C++ symbols.
                        Set a value of this parameter to a sequence of prefixes to match and
                        remove such compiler arguments.
                        Pass an empty string "" to disable. Accepts multiple values at once.
                        ATTENTION: since Python's argparse always treats a leading dash in a
                        CLI argument as a script's argument name, but not value, use a plus
                        sign '+' instead of a dash '-' to specify a leading dash. Example:
                        instead of '-fsanitize' use '+fsanitize'.
                        Default: ['+fsanitize'].
  --discard_args [+compiler_arg_or_args_pair_spec ...]
                        Similarly to '--discard_args_with_pfx', values for this argument
                        define a set of compiler arguments (such as '-DMY_DEF=VALUE') or
                        pipe-delimited argument pairs (like a single token value
                        '-I|/certain/dir' defines a two token pair '-I /certain/dir') that
                        will be removed from a compiler invocation.
                        Note that a single token specification of a '-D' compiler argument
                        has a special handling and also addresses its two token alternatives.
                        Pass an empty string "" to disable. Accepts multiple values at once.
                        ATTENTION: since Python's argparse always treats a leading dash in a
                        CLI argument as a script's argument name, but not value, use a plus
                        sign '+' instead of a dash '-' to specify a leading dash. For the
                        above it's '+DMY_DEF=VALUE' and '+I|/certain/dir'.
                        Default: ['+DADDRESS_SANITIZER'].
  --enable_dupes_check, --no-enable_dupes_check
                        If set, yacce will report if a pair <source, output> isn't unique.
                        Usefulness of this flag solely depends on actual build system
                        implementation. Some might use lots of temporary compilations just to
                        gather compiler capabilities which could lead to an avalanche of
                        false positives. This could be mitigated with --discard* family of
                        flags, but this requires manual intervention, hence it's disabled by
                        default.
                        (default: False)
  -c [compiler_basename_or_path_fragment ...], --compiler [compiler_basename_or_path_fragment ...]
                        Adds an absolute path, a basename, a path suffix, or a path prefix
                        (prepend it with a plus '+' symbol) of a custom compiler to the set
                        of compilers already detectable by yacce. Accepts multiple values at
                        once.
  --not_compiler [compiler_basename_or_path_fragment ...]
                        You can prevent a certain absolute path, a basename, a path suffix,
                        or a path prefix (prepend it with a plus '+' symbol) from being
                        treated as a compiler by using this argument. Accepts multiple values
                        at once.
  --enable_compiler_scripts, --no-enable_compiler_scripts
                        By default, yacce doesn't treat a script (classified by testing for
                        shebang '#!' sequence in the first 2 bytes of the file) invocation as
                        a compiler invocation and ignores it. Set this option when this
                        behavior is unwanted.
                        (default: False)
  -d dir/path, --dest_dir dir/path
                        Destination directory in which yacce should create resulting .json
                        files. Must exist.
                        Default: current working directory.

PathFilter specification:
-------------------------
Filters are tested before and after applying working directory to a path (if the path tested
isn't an abs path), and after expanding an abs path to a realpath(). Recognizable filter
specifications are:
- exact match: filter not starting or ending on a plus '+' sign
- match prefix: filter ending with a plus '+' sign. A path starting with the string before
the plus sign matches the filter.
- match suffix: filter beginning with a plus '+' sign. A path ending with a substring that
follows the plus sign matches the filter.
- substring match: filter beginning with and ending with a plus '+' sign matches as a
substring.
```
