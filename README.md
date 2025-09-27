# Yacce is a *non-intrusive* compile_commands.json extractor for Bazel (experimental, local compilation, Linux only)

Yacce extracts `compile_commands.json` and build system insights from a build system by supervising
the local compilation process with `strace`. Yacce primarily supports Bazel (other build systems
might be added later).

## Motivation

Only open-source history of Bazel development spans for over a decade, and yet - it has a ton of C++
specific features, while one of very important ones, - generation of `compile_commands.json`, - is
still not there. There situation is so ridiculous, that even G's own commands had to invent and
support their own "wheels" to make compile_commands for their projects (for example: [XLA](https://openxla.org/xla/lsp#how_do_i_generate_compile_commandsjson_for_xla_source_code), [pigweed](https://cs.opensource.google/pigweed/pigweed/+/master:pw_ide/py/pw_ide/compile_commands_generator.py)).

But there already exist several decent generic `compile_commands.json` extractors, with `hedronvision/bazel-compile-commands-extractor` being the most well-known and, probably, respected. Why bother?

There are several reasons:
- their usability is horrible, - all extractors I've seen (I don't claim I saw all
of them in existence!) require you to make a certain nontrivial modification of your build system and specifically list there what targets and how exactly you're going to compile just to spew the damn compile_commands for them!
    - what if I'm supporting a complex project spanning across multiple code bases, that don't employ such extractor, and I have to work on many code branches across many different remote machines? I'd have to first extract potentially branch specific build targets, and then manually inject extractor's code into the main build system. Do this a few times a week, and you'll start to genuinely hate Bazel (if
    you don't yet).
    - why it can't be made as simple as, for example, in CMake with its `-DCMAKE_EXPORT_COMPILE_COMMANDS=1`
        - did I just discovered the only thing made right in CMake? :trollface:
- completely orthogonal to usability is an InfoSec consideration: what if I don't want to add a 3rd party, potentially compromisable dependency, into my project? I have no idea what it does internally there
and what could it inject into my binaries under the hood. Why does it have to be so intrusive?


## Benefits of yacce

Supervising a build system doing compilation with a standard system tool have several great benefits:
- Yacce is super user-friendly and simple to use. It's basically a drop-in prefix for a shell command
you could use to build the project, be it `bazel build ...`, `bazel run ...`, or even
`MY_ENV_VAR="value" ./build/compile.sh arg1 .. argn`. No modification of the build system is required, just
prepend your build command with `yacce -- ` and hit enter.
- `strace` lets yacce see real compiler invocations, hence `compile_commands.json` made from strace
log reflects the way you build the project precisely, with all the custom configuration details
you might have used, and independently of what the build system lets you to know and not know about that.
- compilation of all external dependencies as well as linking commands, are automatically included (with a
microsecond timing resolution, if needed).
- there are just no InfoSec risks by design (of course, beyond running a code of yacce itself,
though it's rather small and is easy to verify). Yacce is completely external to the build system and doesn't interfere with it.

## Limitations

However, the supervising approach have some intrinsic limitations, which make it not suitable
for all use-cases supported by Bazel:

- `strace` needs to be installed (`apt install strace`), which limits yacce to basically **Linux only**.
- **compilation could only happen locally**, on the very same machine, on which yacce runs. This
leaves out a Bazel RBE, and requires building with an empty cache, if the cache is used.
- while yacce doesn't care how you launch the build system and lets you use any script or a command
you like, eventually, it should **build only one Bazel workspace**. Yacce does not check if this
limitation is respected by a user, though typically, it's easy to fulfil.

If this is a hard no-go for you, ~~suffer with~~ consider other extractors, such as [hedronvision's](https://github.com/hedronvision/bazel-compile-commands-extractor) one.

There are some "soft" limitations that might be removed in the future, such as:
1. currently yacce does not support incremental builds (i.e. you'd have to fully recompile the
project to update `compile_commands.json`)
2. Bazel is monstrous. While yacce works nicely with some code bases, there might be edge cases, that
aren't properly handled.
3. One can't just take all the compiler invocations a build system does and simply dump them to a
`compile_command.json`. A certain filtering is mandatory, and that require parsing compiler's arguments:
    - gcc- and clang- compatible compilers are the only supported.
    - 100% correct compiler's argument parsing requires yacce to reimplement 100% of compiler's own CLI
  parser, which is not done and will never be done. The parser implemented is good enough for many
  cases, but certainly not for all. Yacce could diagnose some edge cases and warn of potentially
  incorrect results, but again - certainly not all edge cases are covered.

You probably will never hit the last two, however, if you will, you know what to do (please file a bug report, or better submit a PR).

---

Why do I pay so much attention documenting the limitations which make yacce look like it is an incapable
tool, even though it's awesome when applicable?

I strongly dislike a "modern" software engineering approach that is shortly described as
"overpromise and underdeliver, assuming your clients are idiots and won't notice.". I don't work for
idiots, hence I warn upfront of potential issues one could encounter, and label the tool as
"experimental". Once it becomes more battle-tested, "experimental" tag will go away.

## Examples of extracting compile_commands from Bazel

First, install yacce with `pip install yacce` for Python 3.10+.

Second, ensure you have [strace](https://man7.org/linux/man-pages/man1/strace.1.html) installed with `sudo apt install strace`. Some distributions have it installed by default.




