"""Normalized source for static-analysis tests.

THE INVARIANT:

    Static-analysis tests inspect NORMALIZED source by default; raw-source
    inspection requires explicit opt-in.

WHY. A test that greps source for a forbidden token will match the explanatory
COMMENT that describes the token -- and the better the comment, the likelier it
contains it. That is a structurally self-defeating pair, and in the codebase this
was extracted from it fired FIVE times in one day:

  * a grep for a skip site matched its own explanatory comment (3rd that day)
  * assertNotIn("account", block) failed because the comment said "never names
    the account"
  * count("_env_client()") == 2 found THREE: definition, call, and a docstring
  * a .jsx assertion still saw the file's `//` header, because that file's local
    copy of the stripper handled `#` only
  * a forbidden-substring list matched English: "place" found "replaced"

Reminding contributors to be careful does not work -- the helper already existed
for three of those five and was simply not reached for. So `code()` is the
default and `raw()` has to be asked for by name. That is the entire design: the
safe thing is what you get when you do not think about it.

THREE LOCAL COPIES of the stripper had already appeared in test files, one of
them subtly wrong (Python comments only). A hand-copied helper is the same defect
class as a hand-copied allowlist, so this is the one definition.

For "what calls what", prefer `calls_to()` over any text search. A parse cannot
be fooled by prose at all.

SCOPE. Removing comments does not make a substring search sound -- see the
`code()` docstring. A token inside a STRING LITERAL is still text, and is
supposed to be: `x = "#tag"` keeps its `#tag`. The sixth occurrence of this
defect class, found later, was exactly that -- a runtime payload string reading
"NOT rows_retained_n" matched a test forbidding `rows_retained_n`. Normalisation
removes the prose a human wrote ABOUT the code; it cannot tell you what the code
READS. For that, parse.

THE TWO LANGUAGES ARE NOT EQUALLY SOUND, and the asymmetry is the sharpest edge
here. The Python path is TOKENIZER-GRADE: it cannot confuse a comment with a
string, which is exactly what it graduated from. The JS path is REGEX-GRADE and
can still eat the contents of a string literal -- `"/* block */"` becomes `""`,
`"a/*b*/c"` becomes `"ac"`, and `"x // y"` truncates to `"x`, leaving an
unterminated quote. `"https://x/y"` survives only because `//` there is not
preceded by whitespace, which is luck rather than a rule. So on the JS side this
module can still do the thing it says a normaliser must never do: change what
the code means. Treat a JS result as evidence about comments, not as source,
and for anything load-bearing in JS reach for a real parser. Stated rather than
hidden, and pinned by a test, because an unstated boundary is how the first five
occurrences happened.
"""
import ast
import io
import os
import re
import tokenize

__version__ = "0.1.0"

__all__ = [
    "code", "raw", "calls_to",
    "READABLE_EXT", "CHECK_PREFIXES",
    "is_check_file", "production_py", "check_py",
]

_JSX_BLOCK = re.compile(r"\{/\*.*?\*/\}", re.S)
_C_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_TRAILING_SLASHES = re.compile(r"\s+//\s")

_JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

#: Readable as a PATH, but PROSE -- there is no code in it to normalise, and
#: every normaliser here would corrupt it. Found while documenting this package:
#: `code("notes.md")` dropped `# A heading` as a comment and truncated
#: "a `#hash` and // slashes" at the hash, returning 'Some prose with a `'. That
#: is the `//`-is-integer-division failure in a second place -- output that still
#: looks like the file. `raw()` is the accessor for prose; `code()` refuses.
_PROSE_SUFFIXES = (".md",)


def _is_prose(text_or_path):
    s = str(text_or_path)
    return "\n" not in s and s.endswith(_PROSE_SUFFIXES)


def _is_js(text_or_path):
    """True only for a PATH with a JS-family suffix.

    `//` IS NOT A COMMENT IN PYTHON -- it is integer division, and stripping it
    silently truncated real code. `code()` turned

        x = minutes // (60 * 24)      into      x = minutes

    which is not a failed match but a CORRUPTED file. It surfaced only because a
    test fed the result to `ast.parse` and got a SyntaxError; every test that
    merely greps the normalized text would have searched a mutilated line and
    reported a clean pass. A normaliser that can change what the code MEANS is
    worse than no normaliser, because its output still looks like source.

    Raw text with no path is treated as PYTHON. That direction is the safe one:
    failing to strip a `//` comment can cause a false MATCH, which is the
    pre-existing hazard every test here is already written against, while
    wrongly stripping one changes the program.
    """
    if not isinstance(text_or_path, str):
        return False
    if "\n" in text_or_path:
        return False
    return text_or_path.endswith(_JS_SUFFIXES)


def _strip_python_comments(text):
    """Remove Python comments with the TOKENIZER, not with string matching.

    The line-based version truncated at `  # ` wherever that appeared --
    INCLUDING INSIDE STRING LITERALS. It corrupted this very function
    (`line.split("  # ")`), a test asserting on a string that contained `  # `,
    and any file embedding commented code in a string. Found by a test that fed
    the normalised output to `ast.parse`; every grep-only test over those files
    had been searching a mutilated line and passing.

    A tokenizer knows a COMMENT from a STRING. Comment spans are blanked in
    place so every other byte and every line number survives; a line that was
    only a comment is dropped.

    Falls back to the naive pass if the source does not tokenize (a file
    mid-edit, or Python 2), because a normaliser must never be the reason a test
    cannot run.
    """
    try:
        lines = text.splitlines()
        spans = {}
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                spans.setdefault(tok.start[0], []).append((tok.start[1], tok.end[1]))
        out = []
        for i, line in enumerate(lines, 1):
            if i not in spans:
                out.append(line)
                continue
            for a, b in sorted(spans[i], reverse=True):
                line = line[:a] + line[b:]
            if line.strip() == "":
                continue            # the line was only a comment
            out.append(line.rstrip())
        return "\n".join(out)
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return "\n".join(l for l in (ln.split("  # ")[0] for ln in text.splitlines())
                         if l.lstrip()[:1] != "#")


def _strip_docstrings(text):
    """Blank module/class/function docstrings, keeping line numbers.

    DOCSTRINGS ARE PROSE, and prose is what defeats a token search. `code()`
    stripped `#` comments and stopped there, so an assertion that a token is
    absent from the CODE kept matching the docstring EXPLAINING why it is absent
    -- the better the explanation, the likelier it contains the token. Four
    times in one day, including an `_env_client()` occurrence count that found
    three where two existed.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        # ValueError belongs here and was missing, which CI caught on 3.9 and
        # 3.10 while 3.11-3.13 passed. `ast.parse` rejects source holding a NUL
        # byte with ValueError up to 3.10 and SyntaxError from 3.11 on:
        #
        #     ast.parse("x\0y")   3.9  -> ValueError
        #                         3.10 -> ValueError
        #                         3.11 -> SyntaxError
        #                         3.12 -> SyntaxError
        #                         3.13 -> SyntaxError
        #
        # so on the two oldest supported versions the exception escaped
        # `code()` entirely. `_strip_python_comments` above already catches
        # ValueError; this sibling did not. Two functions guarding the same
        # parse hazard with different except tuples is the one-of-them-quietly-
        # loses-a-step defect this module is otherwise about, and a normaliser
        # must never be the reason a test cannot run.
        return text
    spans = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            # `only` = the docstring is the entire body, so removing it needs a
            # `pass` in its place rather than blank lines.
            only = len(body) == 1 and not isinstance(node, ast.Module)
            spans.append((first.lineno, first.end_lineno, only))
    if not spans:
        return text
    # A docstring that is a body's ONLY statement cannot simply be blanked --
    # that leaves `def f():` with no block and the result no longer parses.
    # Caught by this module's own ratchet (normalised source must still
    # ast.parse), which is the whole reason that test exists.
    kill, stub = {}, {}
    for a, b, only in spans:
        for i in range(a, (b or a) + 1):
            kill[i] = True
        if only:
            stub[a] = True
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if i not in kill:
            out.append(line)
        elif i in stub:
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f"{indent}pass")
        else:
            out.append("")
    return "\n".join(out)


def code(text_or_path, js=None, docstrings=False):
    """Source with comments removed.

    Python `#` via the tokenizer AND docstrings; JS/JSX `//`, `/* */` and
    `{/* */}` only for JS-family paths (or `js=True`); shell `#` quote-aware for
    `.sh`/`.bash` paths. Those distinctions exist because a normaliser that can
    change what the code MEANS is worse than none -- its output still looks like
    source. See `_is_js`, `_strip_python_comments` and `_strip_docstrings`.

    WHAT THIS DOES NOT DO. It removes the prose a human wrote ABOUT the code. It
    does not make a substring search sound, and a token inside a string literal
    is still there to be matched -- deliberately, since removing it would change
    the program. If the question is "does this code USE x", that is a parse:
    `calls_to()`, or `ast` directly.
    """
    if js is None and _is_prose(text_or_path):
        raise ValueError(
            f"{text_or_path!r} is prose, not code. Normalising it would corrupt "
            f"it -- markdown's `#` is a heading and its `//` is text. Use raw() "
            f"for a prose file; code() is for source.")
    text = _read(text_or_path)
    if js is None and _is_shell(text_or_path):
        return _strip_shell_comments(text)
    as_js = _is_js(text_or_path) if js is None else bool(js)
    if not as_js:
        out = _strip_python_comments(text)
        # Docstrings go by DEFAULT. Pass docstrings=True only for an assertion
        # genuinely about the prose -- and prefer raw() for that.
        return out if docstrings else _strip_docstrings(out)
    text = _JSX_BLOCK.sub("", text)
    text = _C_BLOCK.sub("", text)
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("//"):
            continue
        out.append(_TRAILING_SLASHES.split(line)[0])
    return "\n".join(out)


def raw(text_or_path):
    """Source INCLUDING comments and docstrings.

    Only for assertions genuinely about the prose -- that a rationale is
    present, or that a required warning is still in the file. Never for "this
    token must be absent from the code".
    """
    return _read(text_or_path)


def calls_to(name, text_or_path):
    """Functions containing a call to `name`, found by PARSING.

    The text-search version of this question returned a docstring mention as a
    third "call site". Matches both `name(...)` and `obj.name(...)`. Python only.
    """
    tree = ast.parse(_read(text_or_path))
    found = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == name):
                found.append(fn.name)
            elif (isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)
                  and node.func.attr == name):
                found.append(fn.name)
    return found


#: Extensions `_read` will open as a PATH. Anything else is treated as literal
#: source text -- which is why `.sh` had to be added: a test passed
#: `"pump_watch.sh"` and got the PATH STRING back, so
#: `assertNotIn("some_call", ...)` was asserting about a filename and passed
#: while the call it forbade was still in the file. Two false passes in one test
#: class. A normaliser that silently normalises nothing is worse than none,
#: which is the same reason `code()` is the default accessor at all.
READABLE_EXT = (".py", ".jsx", ".js", ".md", ".sh", ".bash")


def _read(text_or_path):
    s = str(text_or_path)
    if "\n" in s:
        return s
    if s.endswith(READABLE_EXT):
        with open(s, encoding="utf-8", errors="replace") as f:
            return f.read()
    if _existing_file(s):
        # A one-line argument NAMING AN EXISTING FILE is a path whose extension
        # this module does not know, and returning it as source text makes every
        # absence assertion about it trivially true. That silent no-op produced
        # two false passes with a `.sh` path, and a third when a flip-test
        # harness read backups named `module.py.pre_fix` and reported seven
        # assertions as load-bearing on the strength of a path string. Fail
        # loudly: the whole reason `code()` is the default accessor is that a
        # normaliser which silently normalises nothing is worse than none.
        #
        # THIS GUARD ITSELF HAD THE DEFECT until review caught it. It read
        # `os.sep in s and os.path.isfile(s)`, so the loud failure fired only
        # for a path containing a separator -- and `code("data.txt")`, naming a
        # real file in the working directory, skipped the open (unknown
        # extension) AND the raise (no separator) and came back as the string
        # "data.txt". Measured: the file held `forbidden_call()` and
        # `assertNotIn("forbidden_call", code("data.txt"))` passed. The same
        # vacuous no-op, surviving one branch over, inside the module that
        # exists to kill it. The principle decides it and says nothing about
        # slashes: a normaliser which silently normalises nothing is worse
        # than none.
        raise ValueError(
            f"{s!r} is an existing file whose extension source_norm cannot read "
            f"(known: {', '.join(READABLE_EXT)}). It would otherwise be treated "
            f"as literal source text and every assertion about it would be "
            f"vacuous. Rename it, or pass the file's CONTENTS.")
    return s


def _existing_file(s):
    """`os.path.isfile`, which must never be the reason a test cannot run.

    Dropping the separator condition above means EVERY one-line argument
    reaches this, including literal source text. `isfile` raises ValueError on
    a string holding a NUL byte and OSError when a component is too long, so a
    normaliser that let those escape would fail on input it is supposed to pass
    straight through.
    """
    try:
        return os.path.isfile(s)
    except (OSError, ValueError):
        return False


def _is_shell(text_or_path):
    s = str(text_or_path)
    return "\n" not in s and s.endswith((".sh", ".bash"))


def _strip_shell_comments(text):
    """Shell `#` comments, quote-aware.

    Not reused from the Python stripper: that one drives `tokenize`, which
    raises on shell source. The rule is the shell's own -- a `#` starts a
    comment only at the start of a word -- so `${#arr}`, `$#` and `x#y` survive,
    which a naive `split("#")` would destroy.

    Heredoc bodies are stripped by the same rule. Where they hold Python, `#` is
    also a comment and the convention agrees; a heredoc carrying data with `#`
    in it would be over-stripped and must use raw().
    """
    out = []
    for line in text.splitlines():
        res, q, i = [], None, 0
        while i < len(line):
            c = line[i]
            if q:
                res.append(c)
                if c == "\\" and q == '"' and i + 1 < len(line):
                    res.append(line[i + 1])
                    i += 2
                    continue
                if c == q:
                    q = None
                i += 1
                continue
            if c in "'\"":
                q = c
                res.append(c)
                i += 1
                continue
            if c == "#" and (i == 0 or line[i - 1] in " \t"):
                break
            res.append(c)
            i += 1
        out.append("".join(res).rstrip())
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


# ---------------------------------------------------------------------------
# WHICH FILES ARE CHECKS, AND WHICH ARE PRODUCTION
#
# Seven static tests decided this with a hand-written `fn.startswith("test_")`.
# Then fourteen check files were renamed `test_*.py` -> `check_*.py` (they were
# scripts that exited at import and zeroed the whole suite), and every one of
# those seven predicates silently changed meaning the moment the files moved:
# three began scanning check fixtures AS production and one stopped scanning the
# renamed files at all. One failed immediately, reading a fixture's call
# arguments and reporting them as a production value.
#
# A prefix spelled seven times is a hand-copied allowlist, which is the same
# defect class as the hand-copied normaliser above. One definition, here.
CHECK_PREFIXES = ("test_", "check_")


def is_check_file(path_or_name, prefixes=CHECK_PREFIXES):
    """True for a hand-written check file under EITHER naming convention.

    `test_*.py` are unittest modules; `check_*.py` are standalone scripts that
    run at import and exit. Both are checks, neither is production. Pass
    `prefixes` if your repository spells the convention differently -- the point
    is that it is spelled ONCE, not that these two strings are universal.
    """
    return os.path.basename(str(path_or_name)).startswith(tuple(prefixes))


def production_py(root, prefixes=CHECK_PREFIXES):
    """Sorted .py basenames directly in `root` that are not check files.

    The scope every "no production caller may do X" test actually wants. `root`
    is required: the extracted original defaulted to one machine's absolute
    path, which is a silent wrong answer anywhere else.
    """
    return sorted(f for f in os.listdir(root)
                  if f.endswith(".py") and not is_check_file(f, prefixes))


def check_py(root, prefixes=CHECK_PREFIXES):
    """Sorted .py basenames directly in `root` that ARE check files."""
    return sorted(f for f in os.listdir(root)
                  if f.endswith(".py") and is_check_file(f, prefixes))
