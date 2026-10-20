# source-norm

A test that greps source for a forbidden token matches **the comment that
explains the token** — and the better the comment, the more certainly it does.

```python
from source_norm import code, raw, calls_to

code("broker.py")                      # source, comments and docstrings removed
raw("broker.py")                       # everything, for assertions about prose
calls_to("_env_client", "broker.py")   # ['client_for'] — by parsing, not searching
```

No dependencies. One module, Python 3.9+. Handles Python, JS/JSX/TS, and shell.

## Why this exists

This is the shape of the problem:

```python
# this module never names the account
...

def test_it_never_names_the_account():
    assert "account" not in open("broker.py").read()   # FAILS — on the comment
```

The test is right, the code is right, and the assertion is reading the sentence
that documents the rule. The failure mode is worse in the other direction: when
the forbidden token appears in a comment *and* has been correctly removed from
the code, the test passes or fails for reasons unrelated to the code.

In the codebase this was extracted from, that fired **five times in a single
day**:

| what was asserted | what it actually matched |
|---|---|
| a skip site is absent | the comment explaining the skip site |
| `assertNotIn("account", block)` | a comment reading *"never names the account"* |
| `count("_env_client()") == 2` | **three** — definition, call, and a docstring |
| a `.jsx` file has no `//` header | the header, since that file's local stripper handled `#` only |
| a forbidden-substring list | the English word **"replaced"**, via `"place"` |

## The part that makes it work

**Reminding people did not work.** A helper already existed for three of those
five failures and was simply not reached for. So the fix was not a better
helper, it was changing which one you get when you do not think about it:

> Static-analysis tests inspect **normalized** source by default; raw-source
> inspection requires explicit **opt-in**.

`code()` is the short, obvious name and strips comments *and* docstrings.
`raw()` exists, and you have to ask for it by name — which is also the moment
you notice you are asserting about prose.

Five local copies of the stripper had already accumulated across test files, one
of them subtly wrong. A hand-copied helper is the same defect class as a
hand-copied allowlist.

## Scope — what this does **not** do

**Removing comments does not make a substring search sound.** A token inside a
string literal is still there, and is supposed to be — removing it would change
the program:

```python
code('NOTE = "this value is NOT rows_retained_n"')   # keeps rows_retained_n
```

That is the sixth occurrence of this defect class in the original codebase, and
the one normalisation cannot fix: a runtime payload string reading *"NOT
rows_retained_n"* matched a test forbidding `rows_retained_n`. Normalisation
removes the prose a human wrote **about** the code; it cannot tell you what the
code **reads**. For that, parse — which is what `calls_to()` is for. Both halves
are pinned by tests in the `Scope` class, so the boundary stays checkable rather
than merely documented.

**The two languages are not equally sound.** The Python path is
tokenizer-grade: it cannot confuse a comment with a string, which is precisely
what it graduated from. The JS path is **regex-grade** and can still eat the
contents of a string literal — the corruption class the Python side was fixed
to stop:

```js
const c = "/* block */";   // -> const c = "";
const f = "a/*b*/c";       // -> const f = "ac";
const a = "x // y";        // -> const a = "x     (unterminated)
const b = "https://x/y";   // survives — but only because `//` there is not
                           //   preceded by whitespace. Luck, not a rule.
```

So on the JS side this module can do the one thing it says a normaliser must
never do: change what the code means. Treat a JS result as evidence about
comments, not as source, and reach for a real parser for anything load-bearing
in JS. This is a v0.1 boundary, stated rather than discovered later, and pinned
by `Scope.test_the_JS_path_is_REGEX_grade_not_tokenizer_grade` — so the day a
real JS parser goes in, those assertions are the ones that flip.

It is also not a linter. `code()` returns source; every verdict is the caller's.

## Design notes

Each of these is a bug that was shipped, found, and is now pinned by a test.

**`//` is integer division in Python.** Stripping it unconditionally turned

```python
x = minutes // (60 * 24)      into      x = minutes
```

across every Python file inspected. That is not a missed match, it is a
corrupted file whose output still looks like source — so a grep over it reports
a clean pass against a line that no longer exists. It surfaced only because one
test fed the result to `ast.parse` and got a `SyntaxError`. The language is now
taken from the **path suffix**, and raw text without a path is treated as
Python, because failing to strip a `//` comment causes a false match while
wrongly stripping one changes the program.

**Comments are removed by the tokenizer, not by string matching.** The
line-based version truncated at `  # ` wherever it appeared — including inside
string literals. It corrupted its own source line. A tokenizer knows a comment
from a string.

**Docstrings are prose too.** `code()` stripped `#` and stopped there, so an
assertion that a token is absent from the code kept matching the docstring
explaining why it is absent.

**A path it cannot open raises instead of being returned as text.** This is the
quietest failure in the set: an unknown extension came back as the path string,
so `assertNotIn(forbidden, code(path))` was asserting about a *filename* and
passed while the call was still in the file. Two false passes came from a `.sh`
path; a third came from a harness reading `module.py.pre_fix` backups, which
reported seven assertions as load-bearing on the strength of a path string —
inside the harness verifying a fix for this very defect class. Adding
extensions one incident at a time does not converge; the silent fallthrough was
the defect.

**And that guard itself had the defect, until review caught it.** It fired only
when the path contained a separator, so `code("data.txt")` — naming a real file
in the working directory — skipped the open (unknown extension) *and* the raise
(no separator) and came back as the string `"data.txt"`. Measured: the file held
`forbidden_call()` and `assertNotIn("forbidden_call", code("data.txt"))` passed.
The same vacuous no-op, one branch over, inside the module that exists to kill
it. The principle decides it and says nothing about slashes.

**`code()` refuses prose.** Found while writing this README: `code("notes.md")`
dropped `# A heading` as a comment and truncated the next line at a `#hash`,
returning `'Some prose with a `'`. Markdown stays readable through `raw()`;
`code()` raises, because a normaliser that changes meaning is worse than none.

**A docstring's characters go; its lines stay.** Found by auditing this
package after it was published. The stripper blanked whole *lines*, so anything
sharing a line with a docstring went with it:

```python
code('"""module doc"""; FORBIDDEN_CALL()')   # -> ''
code('def f(): """doc"""')                   # -> 'pass'
```

The first makes `assertNotIn("FORBIDDEN_CALL", code(src))` **pass with the call
still in the file** — the precise false pass this package exists to stop,
committed by the package. The second deletes a `def`. Both outputs still
`ast.parse`, so the ratchet below could not see either, which is why the repair
is pinned on content as well as on parseability. Removal is now by character
range, re-indenting anything that followed a multi-line docstring's closing
quotes and taking a stranded `;` with it.

**A guard a caller can switch off by accident is not a guard.** The prose
refusal was gated on `js is None`, so `code("notes.md", js=False)` walked past
it and returned ``'Some prose with a `'`` — the corruption the guard was added
for, reached through an unrelated argument. `js` chooses a stripper; it no
longer decides whether the refusal applies. The shell path had the same gate,
which sent `code("deploy.sh", js=False)` through the Python tokenizer.

**`calls_to` named the enclosing function too, and repeated itself.**
`ast.walk` descends through nested definitions, so a call inside an inner
function was reported for its parent as well, and a function calling the target
twice appeared twice — `['twice', 'twice', 'outer', 'inner']`, where `outer`
makes no such call. The answer is the innermost function, once.

**Normalised source must still parse.** The strongest form of the invariant, and
a test, not an aspiration: normalisation may remove comments, never change the
program.

## Using it as a repository invariant

The library makes the safe thing the default. Making it *enforced* is a lint
over your own test files — roughly twenty lines, and the shape matters more than
the code:

```python
def test_no_static_test_greps_unnormalized_source(self):
    offenders = []
    for f in glob.glob("test_*.py") + glob.glob("check_*.py"):
        src = pathlib.Path(f).read_text()
        reads_source  = re.search(r'open\(\s*["\'][^"\']*\.(py|jsx)["\']', src)
        asserts_absence = "assertNotIn(" in src or ".count(" in src
        if reads_source and asserts_absence and "source_norm" not in src:
            offenders.append(f)
    self.assertEqual(offenders, [])
```

Three things to copy along with it:

- **Glob both naming conventions you use.** When fourteen files were renamed
  `test_*.py` → `check_*.py`, a lint globbing only `test_*.py` would have
  dropped all fourteen out of scope on the day of the rename — the ratchet
  "shrinking" by losing coverage rather than by auditing anything.
- **Seed a pre-existing set and let it only shrink.** Files that predate the
  invariant go in an `UNAUDITED` set with a test asserting the set never grows
  and holds no stale entries. A file may leave it by being audited; nothing may
  join it.
- **Every exemption carries a reason, and the reason is tested for.** A file
  that reads source to `exec` it is genuinely exempt. An exemption without a
  stated reason is an exemption nobody will ever revisit.

`is_check_file` / `production_py` / `check_py` exist for the same reason the
stripper does: that prefix was spelled in seven separate predicates, and the
rename changed all seven meanings at once — three began reading check fixtures
*as* production and one stopped looking at the renamed files altogether.

## API

| | |
|---|---|
| `code(text_or_path, js=None, docstrings=False)` | Comments and docstrings removed. Language from the path suffix. Raises on prose, and on an existing file whose extension it cannot read. |
| `raw(text_or_path)` | Everything. For assertions genuinely about the prose. |
| `calls_to(name, text_or_path)` | The **innermost** functions whose own body calls `name`, each listed once, by parsing. Matches `name(...)` and `obj.name(...)`. Python only. |
| `is_check_file(path, prefixes=CHECK_PREFIXES)` | Check file vs production, spelled once. |
| `production_py(root, prefixes=...)` / `check_py(root, ...)` | The two scopes, partitioning a directory's `.py` files. |
| `READABLE_EXT` | Extensions opened as a path. Anything else one-line is literal source. |

A one-line argument is a path if it has a readable extension, or raises if it
names an existing file with an extension this module does not know. Anything
containing a newline is literal source.

## Tests

```bash
PYTHONPATH=src python -W error::ResourceWarning -m unittest discover -s tests -t tests -v
```

46 tests. CI runs them on Linux and Windows across Python 3.9–3.13.

The matrix is not decoration: it caught a defect this project's own author
could not see locally. `_strip_docstrings` caught only `SyntaxError` from
`ast.parse`, but source holding a NUL byte raises **`ValueError` on 3.9 and
3.10** and `SyntaxError` only from 3.11 on — so on the two oldest supported
versions the exception escaped `code()` entirely. It failed on 3.9 and 3.10, on
both operating systems, and passed on 3.11–3.13.

**macOS is not verified here.** The matrix asked for it and GitHub never
allocated a runner: across three runs, all fifteen macOS jobs were cancelled
after ~25 minutes with zero steps started. The claim now matches what
executed.

`ResourceWarning` is promoted to an error, and it is not decoration: its first
execution against this suite reported two unclosed file handles in the lints
above, which were fixed rather than silenced.
