<!-- pythrust/48-r1 run 4, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 347 s, 1609705 tokens, $1.57 -->

# Review of PR 48 (baileyrd/PythRust): A6 alias checks for tempered bodies

**Verdict: changes required.** The new check accepts three kinds of program that D4 forbids, and a lowering gap anywhere in a program turns D3 and D4 off for every tempered function. All formatting, lint and test checks pass.

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head reviewed:** `4771160b650e6a50edeaf6bc48d244050a69f057` (the fetched `pull/48/head` matched it)
- **Diff:** one commit, 18 files, +845/−22. Most of it is the new `own/alias.rs`, plus a span on `Inst::Append`, `Func::names`, three corpus programs, and edits to the docs and roadmap.

## Blocking findings

### 1. P1: two fields of one tuple are assumed to be different lists
**Where:** `own/alias.rs:252-263` (`step`) and `:442-454` (`same`).

`t[0]` and `t[1]` get different paths, `[Field(0)]` and `[Field(1)]`. The code only falls back to "may be any list" when this function's own heap has something stored in that tuple. For a tuple that comes from a call result, a global or a parameter, nothing is stored locally, so the two fields are treated as distinct. Nothing proves that.

Probe (executed):
```python
temper def make() -> tuple[list[int], list[int]]:
    a = [1]
    return (a, a)
temper def f() -> int:
    x, y = make()
    x.append(2)
    return len(y)
print(f())
```
- **Got:** `check` exits 0 and `run` prints `2`. `x` and `y` are two names for one list, changed through `x` while `y` is used later.
- **Should:** be rejected, as D4 requires.
- **Same hole, also executed and accepted:** a global `G = (xs, xs)` read as `G[0]`/`G[1]`, and a parameter `mut p: tuple[list[int], list[int]]` called with `f((xs, xs))`. A5 does not cover the parameter case: one value is passed once.
- **Neighbour that is correctly rejected:** the same body with a local `t = (a, a)`.
- **Fix:** a `Field(k)` step into a list the body did not build should also add `Anywhere` for that object, or `same` should treat field paths of one object as possibly equal.
- **Regression test:** `tup_call` above, rejected, in `own/tests.rs`.

### 2. P1: a temporary read from two aliased names excuses both of them
**Where:** `own/alias.rs:376`.

The check skips any live name listed in `roots[w]`, the variables the changed temporary was read from. A conditional expression has two roots, so both names are skipped, even though either one sees the change.

Probe (executed):
```python
temper def f(flag: bool) -> int:
    a = [1]
    b = a
    (a if flag else b).append(2)
    return len(a) + len(b)
print(f(True))
```
- **Got:** accepted; `run` prints `4`.
- **Should:** be rejected. `(b if flag else b).append(2)` followed by `len(a)` is rejected, which shows the rule should fire here too.
- **Fix:** skip a root only when the temporary has exactly one root. Otherwise, also check whether the roots alias each other.

### 3. P1: a parameter is assumed never to be a global's list
**Where:** `own/alias.rs:176-180` with `same` at `:443`. `Obj::Param(i)` and `Obj::World` are never compared.

Probe (executed):
```python
G = [1]
temper def f(mut xs: list[int]) -> int:
    G.append(2)
    return len(xs)
print(f(G))
```
- **Got:** accepted; prints `2`. A variant using `g = G; xs.append(2); return len(g)` is also accepted.
- **Should:** be rejected. Two names, `G` and `xs`, see one changed list, and A5 cannot catch this because no list is passed twice. Two globals (`g = G; h = G`) are correctly rejected.
- **Fix:** let `Param` and `World` overlap in `overlaps`/`same`, or record a decided check at the call site.

### 4. P1: any lowering gap in the program silently disables D3 and D4 for every tempered function
**Where:** `own/mod.rs:107` (`aliasing: BTreeMap::new()` on the conservative path).

Probe (executed):
```python
def unrelated(xs):
    if len(xs) > 5:
        raise ValueError(xs)
    return 0
temper def same(xs: list[int]) -> list[int]:
    return xs
print(same([1]), unrelated([1]))
```
- **Got:** `check` exits 0 and `run` prints `[1] 0`. `examples/aliasing_return.pyr`, which is the same tempered function without the unrelated one, is rejected.
- **Same result with the other recorded gap:** `x = print(7)` placed before `def print`.
- **Why it matters:** the roadmap's A6 exit criterion says the work "closes any other lowering gap a tempered body can reach instead of rejecting it". Here the tempered body lowers fine but is not checked. A4's "precise summaries only" rule was decided for modes. Extending it to D3/D4 was decided silently. Neither the roadmap line nor grammar.md's "Implemented (A6)" for D3/D4 mentions this.
- **Fix:** lower and check each tempered function and its callees on their own when lowering the whole program fails, or reject. If the owner prefers to keep the fallback, record it as a known gap and un-tick A6.

### 5. P2: the fix-its for D4 and D3 point at the wrong copy or do not fix the error
**Where:** `own/alias.rs:388-399` and `:412-416`. All four cases below were executed.

- **The suggested copy goes on the changed name, whichever name was bound second.** With `prev = cur` then `cur.append(1)`, the error suggests `cur = list(prev)`. D4's fix-it copies at the binding, so it should be `prev = list(cur)`. The same happens for `c = a; a.append(2)`, which suggests `a = list(c)`.
- **A change through a `mut` argument gives a vague fix-it.** `b = a; grow(b); len(a)` suggests `` `a = list(...)` `` instead of D4's `b = list(a)`, because the argument is a temporary. Existing tests check only the span.
- **D3's fix-it fails for nested lists.** For `rows: list[list[int]]`, applying the suggested `return list(rows)` gives a second error ("…sharing a list with its parameter `rows`; return a copy made with `list(...)`"), so following the advice never ends.
- **Fix:** name the variable whose binding made the alias, and use the root variable when the changed operand is a single-root temporary. Change the nested-list D3 message so it does not offer `list(...)` as the fix.
- **Tests:** add assertions on the message text for the `mut`-argument and reversed-binding cases.

### 6. P2: coverage gaps
- No test covers findings 1 to 4.
- No rejected program exercises the "earlier run" ageing logic. `each_run_of_an_allocation_is_a_new_list` and `a_narrowed_optional_is_the_same_name` pass at the base commit as well. They are legal-neighbour tests, so they guard precision but not soundness.
- The other 7 new tests fail at base, as they should.

## Design questions for the owner
- **Aliasing across parameters.** Inside the body, `f(rows, rows[0])` (with `mut r`) and `f((xs, xs))` reach the parameters as distinct objects. Ownership.md's Decided item 2 covers only "one value to two parameters", so the decided caller-side check (A5) would not reject these. Should A5 cover an argument together with its element, and a single argument that contains the same list twice?
- **Should D3 forbid a shallow copy of a nested-list parameter?** That is what the PR's grammar.md text implies, and it is what makes D3's own fix-it fail (finding 5).

## Optional improvements
- `aliasing` is inserted out of alphabetical order in `NATIVE` (`conformance.rs:346`).
- The D3 check runs at every assignment to a returned local, even one that never reaches the `return`. For example, `out = xs; out = [1]; return out` is accepted only because lowering adds a temporary.
- For `return xs if c else [0]`, the fix-it suggests `return list(xs)`, which would drop the `else` branch.

## Checks performed
| Check | Result |
|---|---|
| `cargo fmt --all --check` | passed |
| `cargo clippy --locked --all-targets -- -D warnings` | passed |
| `cargo test --locked` (includes conformance) | passed: 416 + 19 + 27 + 29 + 0 tests, 0 failed |
| New `own::tests` run against the base commit | 7 fail there as intended; 3 pass there (see finding 6) |
| `examples/aliasing.pyr`: `build` vs `run` vs CPython | output identical, exit 0 |
| New code against the Rust rules | no `unwrap`/`expect`/`unsafe`, no new dependency |

The build's configured target directory is read-only here, so I built with `CARGO_TARGET_DIR` set to a scratch directory.

## Validation gaps
- **CPython version:** only Python 3.14.8 is installed, so the conformance comparison ran against 3.14, not 3.13.
- **Memcheck:** valgrind is not installed. The IR change only adds a span, but the corpus was not run under memcheck.
- **Other targets:** I did not see results for macOS, Windows MSVC or AArch64.
- **Shared memory:** the remind-me tools that CLAUDE.md requires were not available, and my authority here is read-only, so nothing was searched or saved there.
