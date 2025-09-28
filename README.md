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

## Examples of extracting compile_commands from Bazel

First, install yacce with `pip install yacce`. Python 3.10+ is supported.

Second, ensure you have [strace](https://man7.org/linux/man-pages/man1/strace.1.html) installed with `sudo apt install strace`. Some distributions have it installed by default.

### 1. Compiling JAX (`jaxlib` wheel)

JAX is one of Google's machine learning frameworks. It has interface code written in Python, while
most high performance code is in C++ seen with Python bindings. A compiled part is called `jaxlib`
and is responsible beyond some general JAX parts for a CPU-based execution backend. We'll be using current
latest JAX v0.7.2 here.

Compiling jaxlib is a good first example for yacce, because it has quite a large code base with
at least one dependency, XLA (a machine learning compiler), that is almost always being worked upon
in parallel with the jaxlib itself. By default, JAX's build system will fetch XLA from a
[pinned commit](https://github.com/jax-ml/jax/blob/jax-v0.7.2/third_party/xla/revision.bzl#L24), but
since we emulate a real developer work here, we'll also checkout that pinned commit to a local directory,
so we could work on it, and then tell JAX's build system to use that local directory instead of the
pinned commit. Yacce will automatically generate a single `compile_commands.json` for both jaxlib
and XLA.

First, let's setup the workspace:
```bash
mkdir /src_jax && cd /src_jax # the dir for both JAX and XLA sources
( git clone https://github.com/openxla/xla && cd ./xla \
  && git checkout 0fccb8a6037019b20af2e502ba4b8f5e0f98c8f6 )
git clone --branch jax-v0.7.2 --depth 1 https://github.com/jax-ml/jax
```
Now we have `/src_jax/jax` directory having v0.7.2 JAX commit checkout, and `/src_jax/xla` having
the same XLA commit, that's designed for JAX v0.7.2. Time to build!

Without yacce, we'd use the following command inside `./jax` directory:
```bash
python3 ./build/build.py build --wheels=jaxlib --verbose --use_clang false \
  --target_cpu_features=native --bazel_options=--override_repository=xla=../xla
```
This is a helper script that knows how to properly invoke bazel to build JAX. A couple of arguments needs comments:
- `--use_clang false` tells the script to use gcc instead of clang. While clang is the recommended
compiler, I'm feeling a bit lazy to install the recommended latest version, so I opt-in for `gcc`.
If you have the latest clang installed - remove that option.
- `--bazel_options=--override_repository=xla=../xla`: `--bazel_options` script's argument will pass its value directly
to Bazel. Here Bazel will get `--override_repository=xla=../xla` option which requires it to use `../xla` directory
for a `xla` dependency instead of a hardcoded commit fetched from the Internet.

With yacce, we just prepend the command with `yacce -- ` like this

```bash
cd ./jax # since we didn't change the dir yet
yacce -- python3 ./build/build.py build --wheels=jaxlib --verbose --use_clang false \
  --target_cpu_features=native --bazel_options=--override_repository=xla=../xla
```

At the start, yacce will test if strace and bazel are available, and then it will ask your permission to
execute `bazel clean` command. While yacce just will not be able to gather all necessary information
and produce a proper `compile_commands.json` if bazel's execution root directory is not clean when
build started, cleaning it and rebuilding from scratch might be expensive on some code bases. Yacce
tries not to bring harm accidentally, but if you want it authorize to do that from the beginning,
you can instead invoke yacce with a `--clean` argument like this: `yacce --clean always -- `.

After doing `bazel clean`, yacce will setup `strace` supervision over Bazel's server execution, and
then will launch the build script. When the build finishes, yacce will start strace log processing
and in a few seconds it'll write `/src_jax/jax/compile_command.json` containing all C++ source files
used for `jaxlib` and for parts of XLA, that were required by jaxlib.

Now fire up your IDE and point `clangd` to that file, so it starts indexing it. In VSCode with `clangd`
extension installed, if `/src_jax` is the main opened directory (workspace), then one could open
Settings / Extensions / clangd, and click "Add Item" for `clangd.arguments` settings, putting
`--compile-commands-dir=${workspaceFolder}/jax` there and then do ctrl+shift+p, "clangd.restart".


