"""The normaliser's own tests.

    Static-analysis tests inspect NORMALIZED source by default; raw-source
    inspection requires explicit opt-in.

Every test here drives the public API or a named private helper. The lints that
keep the invariant true in a REPOSITORY (the ratchet over that repo's own test
files, with its audited-exemption set) are not shipped as tests -- they are
specific to a codebase's layout and belong to it. The README gives the recipe;
what is pinned here is the normaliser's behaviour and its boundary.
"""
import ast
import glob
import os
import pathlib
import tempfile
import unittest

import source_norm as sn

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PKG = pathlib.Path(sn.__file__)
TESTS = pathlib.Path(__file__)


def _own_py_files():
    """This package's own Python: the module and its tests, fixtures included."""
    return sorted([str(PKG), str(TESTS)] + glob.glob(str(FIXTURES / "*.py")))


def _text(path):
    """Read and CLOSE. `open(p).read()` leaves the handle to the collector, and
    the first run of this suite under `-W error::ResourceWarning` reported
    exactly that from two of the lints below -- the promotion earning its place
    on its first execution."""
    return pathlib.Path(path).read_text(encoding="utf-8")


class NormalizationTest(unittest.TestCase):

    def test_python_comments_are_removed(self):
        self.assertEqual(sn.code("# names the account\nx = 1"), "x = 1")

    def test_trailing_python_comments_are_removed(self):
        got = sn.code("y = 2  # mentions account")
        self.assertIn("y = 2", got)
        self.assertNotIn("account", got)

    def test_js_line_comments_are_removed(self):
        self.assertEqual(sn.code("// mentions place\nconst y = 2", js=True).strip(),
                         "const y = 2")

    def test_jsx_block_comments_are_removed(self):
        self.assertNotIn("buy", sn.code("{/* buy sell */}\nconst z = 3", js=True))

    def test_c_block_comments_are_removed(self):
        self.assertNotIn("secret", sn.code("/* secret */\nconst a = 1", js=True))

    def test_a_string_containing_a_hash_survives(self):
        self.assertIn('"#tag"', sn.code('z = "#tag"'))

    def test_a_url_in_code_is_not_eaten_as_a_comment(self):
        """`https://` contains `//` and must not truncate the line."""
        self.assertIn("https://x.test/y", sn.code('u = "https://x.test/y"'))

    def test_raw_keeps_the_prose(self):
        self.assertIn("account", sn.raw("# names the account\nx = 1"))

    def test_calls_to_parses_rather_than_greps(self):
        """A text search counted a docstring mention as a third call site.

        The fixture names `_env_client` three times: the module docstring, the
        caller's docstring, and the one real call. A parse reports one function.
        """
        path = str(FIXTURES / "sample_calls.py")
        self.assertEqual(sn.raw(path).count("_env_client"), 4)
        self.assertEqual(sn.calls_to("_env_client", path), ["client_for"])

    def test_calls_to_ignores_a_mention_in_a_docstring(self):
        src = 'def f():\n    """calls g() a lot"""\n    return 1\n'
        self.assertEqual(sn.calls_to("g", src), [])

    def test_a_path_is_read_and_inline_text_is_not(self):
        self.assertIn("def code(", sn.raw(str(PKG)))
        self.assertEqual(sn.raw("x = 1"), "x = 1")


class AnUnreadablePathIsNotSourceText(unittest.TestCase):
    """`_read` opens only READABLE_EXT. Anything else was returned AS IS, so a
    path it could not open became the "source" and every absence assertion
    about it was vacuously true.

    Two false passes came from a `.sh` path before that extension was added. A
    third came from a flip-test harness that read pre-fix backups named
    `module.py.pre_fix` and reported seven assertions as load-bearing on the
    strength of a path string -- inside the harness verifying a fix for this
    very defect class.

    Adding extensions one incident at a time does not converge; the silent
    fallthrough is the defect. Same reasoning that made `code()` the default
    accessor: a normaliser that silently normalises nothing is worse than none.
    """

    def test_an_existing_file_with_an_unknown_extension_raises(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "module.py.pre_fix")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# a comment\nforbidden_call()\n")
        with self.assertRaises(ValueError) as cm:
            sn.code(p)
        self.assertIn("extension", str(cm.exception))

    def test_the_vacuous_assertion_it_prevents(self):
        """Without the guard the assertion below PASSES while the call it
        forbids is still in the file. That is the whole failure."""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "mod.py.backup")
        with open(p, "w", encoding="utf-8") as f:
            f.write("forbidden_call()\n")
        self.assertNotIn("forbidden_call", p)      # the old behaviour's "source"
        with self.assertRaises(ValueError):
            sn.code(p)

    def test_a_readable_extension_still_opens(self):
        self.assertIn("def code", sn.code(str(PKG)))

    def test_literal_source_is_still_accepted(self):
        self.assertEqual(sn.code("x = 1  # c\ny = 2"), "x = 1\ny = 2")

    def test_a_one_line_string_that_is_not_a_file_is_still_literal(self):
        self.assertEqual(sn.code("not/a/real/path.txt"), "not/a/real/path.txt")


class SlashSlashIsNotAlwaysAComment(unittest.TestCase):
    """`//` is INTEGER DIVISION in Python, and stripping it corrupted real code.

        x = minutes // (60 * 24)      became      x = minutes

    for every Python file inspected. That is not a missed match, it is a
    mutilated file whose output still looks like source -- so a grep over it
    reports a clean pass against a line that no longer exists. It surfaced only
    because one test fed the result to ast.parse and got a SyntaxError.
    """

    def test_python_integer_division_survives(self):
        self.assertEqual(sn.code("x = a // b"), "x = a // b")
        self.assertEqual(sn.code("n = total // (60 * 24)"), "n = total // (60 * 24)")

    def test_integer_division_survives_in_a_real_FILE(self):
        """Inline text is treated as Python; a `.py` PATH takes the same branch,
        and this is the half an inline-only test would not notice."""
        out = sn.code(str(FIXTURES / "intdiv.py"))
        self.assertIn("minutes // (60 * 24)", out)
        self.assertIn("n // 2", out)
        self.assertNotIn("the comment goes", out)

    def test_normalized_python_still_PARSES(self):
        """The strongest form of the invariant: normalisation may remove
        comments, never change the program."""
        bad = []
        for f in _own_py_files():
            try:
                ast.parse(sn.code(f))
            except SyntaxError as e:
                bad.append(f"{os.path.basename(f)}:{e.lineno}")
        self.assertEqual(bad, [], f"normalised source no longer parses: {bad}")

    def test_python_comments_are_still_stripped(self):
        self.assertNotIn("secret", sn.code("x = 1  # the secret token"))
        self.assertNotIn("secret", sn.code("# the secret token\nx = 1"))

    def test_js_can_be_forced_for_raw_text(self):
        self.assertNotIn("note", sn.code("const a = 1 // note", js=True))
        self.assertIn("//", sn.code("x = 7 // 2"))


class JsIsNormalisedFromItsPath(unittest.TestCase):
    """A JS/JSX file must be normalised FROM ITS PATH, never from its text.

    `code()` learns the language from the suffix, because `//` is integer
    division in Python and cannot be stripped unconditionally. Hand it `.jsx`
    CONTENT and it normalises as Python, leaving every `//` header in -- which
    is precisely the defect this module exists to prevent, silently
    reintroduced. It happened in three places at once, and one was a real false
    pass: an assertion found `currently_valid` inside the comment explaining
    that the server never asserts it.
    """

    def test_a_jsx_path_strips_every_comment_form(self):
        out = sn.code(str(FIXTURES / "sample.jsx"))
        self.assertNotIn("currently_valid", out,
                         "a .jsx comment survived normalisation")
        self.assertIn("import React", out)

    def test_the_same_jsx_text_handed_over_as_TEXT_keeps_its_header(self):
        """The failure mode, pinned. Passing CONTENT loses the suffix, so the
        file is normalised as Python and the `//` header stays -- which is why
        the lint below exists and why callers pass paths."""
        text = sn.raw(str(FIXTURES / "sample.jsx"))
        self.assertIn("currently_valid", sn.code(text))

    def test_no_normaliser_is_handed_js_text(self):
        """The lint. Applied here to this package's own tests; the README shows
        how to point it at a repository."""
        offenders = []
        for f in _own_py_files():
            try:
                tree = ast.parse(_text(f))
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call) or not n.args:
                    continue
                nm = (n.func.attr if isinstance(n.func, ast.Attribute)
                      else getattr(n.func, "id", None))
                if nm not in {"code", "_code", "_code_only", "_no_comments"}:
                    continue
                if any(k.arg == "js" for k in n.keywords):
                    continue                      # declared explicitly
                arg = ast.unparse(n.args[0])
                if ".read()" in arg and any(x in arg for x in
                                            (".js", ".jsx", ".ts", ".tsx")):
                    offenders.append(f"{os.path.basename(f)}:{n.lineno}")
        self.assertEqual(offenders, [],
                         "these hand JS TEXT to the normaliser, which then "
                         f"treats it as Python: {offenders}")


class TheCheckFileScopeHasOneDefinition(unittest.TestCase):
    """A filename prefix spelled in seven places is a hand-copied allowlist.

    Seven static tests decided "is this a check file or production?" with their
    own `fn.startswith("test_")`. Renaming fourteen check files to `check_*.py`
    changed all seven predicates at once: three began reading check fixtures AS
    production and one stopped looking at the renamed files altogether.
    """

    def test_both_conventions_are_recognised(self):
        self.assertTrue(sn.is_check_file("test_anything.py"))
        self.assertTrue(sn.is_check_file("check_anything.py"))
        self.assertTrue(sn.is_check_file(os.path.join("abs", "check_x.py")))
        self.assertFalse(sn.is_check_file("broker_client.py"))
        self.assertFalse(sn.is_check_file("latest_test_results.py"))

    def test_the_two_scopes_partition_a_directory(self):
        """Neither list may silently drop a file: every .py is in exactly one."""
        d = tempfile.mkdtemp()
        for name in ("alpha.py", "test_alpha.py", "check_alpha.py",
                     "notes.md", "latest_test_results.py"):
            open(os.path.join(d, name), "w").close()
        prod, chk = set(sn.production_py(d)), set(sn.check_py(d))
        allpy = {f for f in os.listdir(d) if f.endswith(".py")}
        self.assertEqual(prod & chk, set(), "a file counted as both")
        self.assertEqual(prod | chk, allpy, "a .py file is in neither scope")
        self.assertIn("latest_test_results.py", prod)

    def test_a_repository_may_spell_the_convention_itself(self):
        """`prefixes` is a parameter so the convention is spelled ONCE per
        repository -- the point was never that these two strings are universal."""
        d = tempfile.mkdtemp()
        for name in ("alpha.py", "spec_alpha.py"):
            open(os.path.join(d, name), "w").close()
        self.assertEqual(sn.check_py(d, prefixes=("spec_",)), ["spec_alpha.py"])
        self.assertEqual(sn.production_py(d, prefixes=("spec_",)), ["alpha.py"])

    def test_root_is_required(self):
        """The extracted original defaulted to one machine's absolute path,
        which is a silent wrong answer anywhere else."""
        with self.assertRaises(TypeError):
            sn.production_py()


class ShellSourceIsActuallyRead(unittest.TestCase):
    """A path the reader does not recognise comes back as ITS OWN FILENAME.

    `code("pump_watch.sh")` once returned the path itself, so an
    `assertNotIn(...)` passed while the forbidden call was still in the file --
    two false passes in one test class. The same shape as the five
    comment-matching greps that made `code()` the default: a normaliser that
    normalises nothing still looks like one.
    """

    SH = None

    @classmethod
    def setUpClass(cls):
        cls.SH = str(FIXTURES / "sample.sh")

    def test_a_shell_path_is_opened_not_echoed(self):
        """Asserted on CONTENT, never on length.

        This test first read `len(src) > len(self.SH) * 2`, which is a claim
        about how long the absolute path to the fixture happens to be: it
        passed from a short checkout directory and FAILED from a 109-character
        one, on identical code. A test whose outcome depends on where the
        repository sits would have passed CI by luck. The property is that the
        file was OPENED, and the evidence for that is its contents.
        """
        src = sn.code(self.SH)
        self.assertNotEqual(src.strip(), self.SH, "the path was echoed back")
        self.assertIn("OUT=$(mktemp)", src, "_read did not open the file")

    def test_every_readable_extension_is_opened_not_echoed(self):
        """The guard against the next extension being forgotten.

        Each probe names a string that can only come from INSIDE the file, for
        the same reason as above: a length comparison here was really a
        comparison against the length of the checkout path.
        """
        probes = {".py": (str(PKG), "def code"),
                  ".sh": (self.SH, "OUT=$(mktemp)"),
                  ".jsx": (str(FIXTURES / "sample.jsx"), "import React")}
        for ext, (path, marker) in probes.items():
            self.assertIn(ext, sn.READABLE_EXT)
            out = sn.code(path)
            self.assertNotEqual(out.strip(), path, f"{ext} path was echoed back")
            self.assertIn(marker, out, f"{ext} was not opened")

    def test_the_shell_stripper_is_quote_aware(self):
        """`#` starts a comment only at the start of a word. A naive split would
        destroy ${#arr}, $# and a URL fragment."""
        S = sn._strip_shell_comments
        self.assertEqual(S("x=1  # drop me"), "x=1")
        self.assertEqual(S('echo "${#arr}"'), 'echo "${#arr}"')
        self.assertEqual(S("echo $#"), "echo $#")
        self.assertEqual(S('echo "a # b"'), 'echo "a # b"')
        self.assertEqual(S("echo 'a # b'"), "echo 'a # b'")
        self.assertEqual(S("url=http://x/y#frag"), "url=http://x/y#frag")
        self.assertEqual(S("# whole line"), "")

    def test_shell_does_not_go_through_the_python_tokenizer(self):
        """It raises on shell source; a silent fallback would return the file
        unstripped and every comment-matching assertion would come back."""
        src = sn.code(self.SH)
        self.assertNotIn("ONESHOT", src)
        self.assertIn("OUT=$(mktemp)", src)
        self.assertIn("${#arr}", src)


class OneDefinitionOnly(unittest.TestCase):
    """Five local copies of the stripper existed, one handling Python comments
    only -- the same defect class as a hand-copied allowlist."""

    def test_nothing_here_defines_a_second_stripper(self):
        import re
        copies = [os.path.basename(f) for f in _own_py_files()
                  if re.search(r"^def (_code_only|_no_comments)\(",
                               _text(f), re.M)]
        self.assertEqual(copies, [], f"{copies} define a local stripper")

    def test_nobody_rolls_their_own_check_file_prefix(self):
        """The lint that would have caught the rename breaking seven
        predicates. `source_norm` itself is where the prefix is spelled."""
        offenders = []
        for f in _own_py_files():
            if os.path.basename(f) in {os.path.basename(str(PKG)),
                                       os.path.basename(str(TESTS))}:
                continue
            try:
                tree = ast.parse(sn.code(f))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "attr", None) != "startswith":
                    continue
                lits = []
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        lits.append(a.value)
                    elif isinstance(a, ast.Tuple):
                        lits += [e.value for e in a.elts
                                 if isinstance(e, ast.Constant)
                                 and isinstance(e.value, str)]
                if any(v in sn.CHECK_PREFIXES for v in lits):
                    offenders.append(f"{os.path.basename(f)}:{node.lineno}")
        self.assertEqual(offenders, [], f"hand-rolled prefix: {offenders}")


class Scope(unittest.TestCase):
    """What stripping comments does NOT buy you.

    This is the boundary people assume they have bought and have not, so it is
    pinned here rather than only described in the README.
    """

    def test_a_token_in_a_STRING_LITERAL_still_matches(self):
        """The sixth occurrence of this defect class, and the one normalisation
        cannot fix: a runtime payload string read "NOT rows_retained_n" and a
        test forbidding `rows_retained_n` matched it. Removing it would change
        the program, so `code()` keeps it -- and a substring search is still
        not a sound way to ask whether code USES something."""
        src = 'NOTE = "this value is NOT rows_retained_n"\n'
        self.assertIn("rows_retained_n", sn.code(src))

    def test_the_parse_answers_what_the_search_cannot(self):
        """The same question asked by parsing: nothing CALLS it."""
        src = ('def f():\n'
               '    note = "we never call rows_retained_n here"\n'
               '    return note\n')
        self.assertIn("rows_retained_n", sn.code(src))
        self.assertEqual(sn.calls_to("rows_retained_n", src), [])

    def test_it_is_not_a_linter_and_reports_no_findings(self):
        """`code()` returns SOURCE. It makes a search less wrong; it does not
        decide anything, and anything that reads like a verdict is the caller's."""
        self.assertIsInstance(sn.code("x = 1"), str)
        self.assertEqual(sn.code(""), "")

    def test_a_file_with_NO_SEPARATOR_in_its_name_is_not_source_text(self):
        """The hole review found in the guard that exists to close it.

        `_read` raised only when `os.sep in s`, so a one-line argument naming a
        real file in the working directory skipped the open (unknown extension)
        AND the raise (no separator) and came back as its own filename.
        Measured before the fix: the file below held `forbidden_call()` and
        `assertNotIn("forbidden_call", code("data.txt"))` PASSED. Same vacuous
        no-op as the `.sh` false passes, one branch over.
        """
        d = tempfile.mkdtemp()
        cwd = os.getcwd()
        try:
            os.chdir(d)
            with open("data.txt", "w", encoding="utf-8") as f:
                f.write("forbidden_call()\n")
            with self.assertRaises(ValueError) as cm:
                sn.code("data.txt")          # no separator, unreadable extension
            self.assertIn("extension", str(cm.exception))
        finally:
            os.chdir(cwd)

    def test_literal_source_that_is_not_a_file_still_passes_through(self):
        """The other half: dropping the separator condition must not turn
        ordinary source text into an error. Every one-line argument now reaches
        `os.path.isfile`, including a string holding a NUL byte, which makes it
        raise ValueError rather than return False."""
        self.assertEqual(sn.code("x = 1"), "x = 1")
        self.assertEqual(sn.code("not/a/real/path.txt"), "not/a/real/path.txt")
        self.assertEqual(sn.code("data.txt"), "data.txt")   # nothing by that name here
        self.assertEqual(sn.code("x\0y"), "x\0y")

    def test_the_JS_path_is_REGEX_grade_not_tokenizer_grade(self):
        """The asymmetry, pinned as the boundary it is.

        The Python side cannot confuse a comment with a string -- that is what
        the tokenizer bought. The JS side still can, so this module can do the
        very thing it says a normaliser must never do: change what the code
        means. Pinned so the limitation is checkable, and so the day someone
        swaps in a real JS parser, these assertions are the ones that flip.
        """
        self.assertEqual(sn.code('const c = "/* block */";', js=True),
                         'const c = "";')
        self.assertEqual(sn.code('const f = "a/*b*/c";', js=True),
                         'const f = "ac";')
        self.assertEqual(sn.code('const a = "x // y";', js=True),
                         'const a = "x')
        # survives only because `//` is not preceded by whitespace -- luck, not
        # a rule, and that is the point of calling this regex-grade.
        self.assertEqual(sn.code('const b = "https://x.test/y";', js=True),
                         'const b = "https://x.test/y";')
        # the Python side, for contrast: a `#` inside a string is untouched
        self.assertEqual(sn.code('p = "# not a comment"'), 'p = "# not a comment"')

    def test_a_DOCSTRING_takes_ITS_CHARACTERS_not_ITS_LINES(self):
        """Nothing that merely SHARES a line with a docstring is removed.

        The stripper blanked whole lines, so anything on a docstring's first or
        last line went with it -- this package's cardinal sin, output that
        still looks like source. Measured before the fix:

            '\"\"\"module doc\"\"\"; FORBIDDEN_CALL()'   ->  ''
            'def f(): \"\"\"doc\"\"\"'                   ->  'pass'

        The first makes `assertNotIn("FORBIDDEN_CALL", code(src))` pass with
        the call still in the file, which is the false pass this package exists
        to stop. Both outputs still ast.parse, so the normalised-source-parses
        ratchet could not see either -- which is why this is pinned on CONTENT
        as well as on parseability.
        """
        cases = [
            ('"""module doc"""; FORBIDDEN_CALL()\ndef f():\n    pass\n',
             ["FORBIDDEN_CALL"]),
            ('def f(): """doc"""\ndef g(): FORBIDDEN_CALL()\n',
             ["def f()", "FORBIDDEN_CALL"]),
            ('class C: """doc"""\n', ["class C"]),
            ('def f():\n    """a\n    b"""; KEEP_ME()\n', ["KEEP_ME"]),
            ('class C:\n    def m(self):\n        """a\n        b"""; KEEP()\n'
             '        return 1\n', ["KEEP", "return 1"]),
        ]
        for src, must_survive in cases:
            out = sn.code(src)
            ast.parse(out)                 # normalisation never breaks syntax
            for token in must_survive:
                self.assertIn(token, out,
                              f"{token!r} shared a line with a docstring and "
                              f"was deleted with it:\n{out!r}")
            self.assertNotIn("doc", out.replace("docstring", ""),
                             "the docstring itself must still be gone")

    def test_calls_to_names_the_INNERMOST_function_ONCE(self):
        """One entry per function whose own body holds the call.

        `ast.walk` descends through nested definitions, so a call inside an
        inner function was reported for the enclosing one too, and a function
        calling it twice was listed twice:

            ['twice', 'twice', 'outer', 'inner']

        `outer` does not call it. A README example asserting equality against a
        one-element list survives neither artefact.
        """
        src = ("def twice():\n    target()\n    target()\n"
               "def outer():\n    def inner():\n        target()\n"
               "def attr():\n    obj.target()\n"
               "def never():\n    other()\n")
        got = sn.calls_to("target", src)
        self.assertEqual(sorted(got), ["attr", "inner", "twice"])
        self.assertEqual(len(got), len(set(got)), f"repeated entries: {got}")
        self.assertNotIn("outer", got,
                         "the call is in `inner`; `outer` does not make it")
        self.assertNotIn("never", got)

    def test_the_PROSE_REFUSAL_is_not_reachable_PAST(self):
        """`js` chooses a stripper; it does not switch the refusal off.

        Gating the prose check on `js is None` meant `code("notes.md",
        js=False)` walked straight past it and returned 'Some prose with a `' --
        the corruption the guard was added for, reached through an unrelated
        argument. A guard a caller can disable by accident is not a guard.
        """
        d = tempfile.mkdtemp()
        md = os.path.join(d, "notes.md")
        with open(md, "w", encoding="utf-8") as f:
            f.write("# A heading\nSome prose with a `#hash` and // slashes\n")
        for kwargs in ({}, {"js": False}, {"js": True}):
            with self.assertRaises(ValueError, msg=f"js={kwargs}"):
                sn.code(md, **kwargs)
        self.assertIn("# A heading", sn.raw(md))

    def test_SHELL_stays_shell_unless_JS_is_asked_for_EXPLICITLY(self):
        """`code("deploy.sh", js=False)` must not run the Python tokenizer.

        That gate was also `js is None`, so an explicit js=False sent shell
        source through `tokenize`, which raises on it and falls back to the
        naive `  # ` split the tokenizer path exists to replace.
        """
        d = tempfile.mkdtemp()
        sh = os.path.join(d, "deploy.sh")
        with open(sh, "w", encoding="utf-8") as f:
            f.write('echo "a  # not a comment"  # real\nx=${#arr}\n')
        for kwargs in ({}, {"js": False}):
            out = sn.code(sh, **kwargs)
            self.assertIn("a  # not a comment", out, f"js={kwargs}")
            self.assertIn("${#arr}", out, f"js={kwargs}")
            self.assertNotIn("# real", out, f"js={kwargs}")

    def test_PROSE_is_refused_rather_than_corrupted(self):
        """Found while writing this package's README. `code("notes.md")` dropped
        `# A heading` as a Python comment and truncated the next line at a
        `#hash`, returning 'Some prose with a `'. Output that still looks like
        the file, which is the `//`-is-integer-division failure in a second
        place. It raises now; `raw()` is the accessor for prose."""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "notes.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# A heading\nSome prose with a `#hash` and // slashes.\n")
        with self.assertRaises(ValueError) as cm:
            sn.code(p)
        self.assertIn("prose", str(cm.exception))
        self.assertIn("# A heading", sn.raw(p))
        self.assertIn(".md", sn.READABLE_EXT)      # still READABLE, via raw()

    def test_docstrings_can_be_KEPT_but_must_be_asked_for(self):
        """Opt-in, both ways: `docstrings=True` keeps them, `raw()` keeps
        everything. Neither is what you get by default."""
        src = 'def f():\n    """mentions account"""\n    return 1\n'
        self.assertNotIn("account", sn.code(src))
        self.assertIn("account", sn.code(src, docstrings=True))
        self.assertIn("account", sn.raw(src))


if __name__ == "__main__":
    unittest.main(verbosity=2)
