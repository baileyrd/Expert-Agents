<!-- pythrust/48-r2 run 4, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 360 s, 1575097 tokens, $1.63 -->

# Review of PR 48 (baileyrd/PythRust): changes required

Both earlier findings are fixed and their tests fail at 4771160b, so they guard the fixes. But the new depth rule for tuple slots has a hole of the same kind as K1. Three P1 holes let tempered programs that break D4 pass `check`, and three P2 defects affect messages and docs.

- **Base:** 5d76260eef62e1a3c7abfd48635a9a3e82a37650
- **Head:** 2a09a304c45dcdd8f090a1751b81eca4944fe229 (fetched `pull/48/head`; it matches)
- **Commits:** 4771160b (A6), then 2a09a30 (the fix round)

## Earlier findings at this head
- **K1 (tuple slots), fixed for its stated case.** With `p: tuple[list[int], list[int]]` and a call `f((xs, xs))`, a change through `p[0]` while `p[1]` is still used is now rejected. Covered by `slots_of_a_tuple_from_elsewhere_may_be_one_list`, which fails at 4771160b. The rule that replaced it is still unsound; see N1.
- **K2 (exception holding a list), fixed.** An exception holding a list now lowers, so the aliasing checks keep running. The program `e = ValueError(a); b = a; b.append(n); … len(a)` is rejected. `build` refuses with "the native backend does not support an exception holding a list yet", and `corpus_matches_native` checks that through `examples/exception_list`. Covered by `an_exception_holding_a_list_leaves_aliasing_checked`, which fails at 4771160b.

## Blocking findings

**N1 (P1): the pass assumes lists at different depths are different lists, which is false.**
`own/alias.rs:28-39` (`At::Depth`), `:281-293` (`step`), `:471-483` (`same`, which needs `s == c` for `Change::List`). The doc comment says "lists at different depths have different types, so they never are". That is false when a tuple holds lists at different depths: in `tuple[list[int], list[list[int]]]`, both `t[0]` and `t[1][0]` are `list[int]`. It fails for a callee's result, for a parameter, and for module globals (every global is `World@0`). Executed:
```python
temper def mk() -> tuple[list[int], list[list[int]]]:
    l = [1]
    return (l, [l])

temper def f() -> int:
    t = mk()
    a = t[0]
    b = t[1][0]
    a.append(2)
    return len(b)

print(f())
```
- `check` accepts it. `run`, CPython and the native build all print `2`, so the change through `a` is seen through `b`, which D4 forbids.
- With `(l, l)` and `tuple[list[int], list[int]]` instead, it is rejected, as it should be.
- Same hole (executed): a parameter `p: tuple[list[int], tuple[list[int], int]]` passed `(xs, (xs, 1))`, and globals `g = [[1]]; h = g[0]` read as `a = h`, `b = g[0]`.

**Fix:** decide "may be the same list" by sharing the abstract object and having the same list type, not the same depth, unless the body built the value. **Tests:** these three programs as rejected cases in `own/tests.rs`, next to the existing same-depth legal neighbour.

**N2 (P1): a parameter and a module global are assumed to be different lists.**
`own/alias.rs:183-186` and `:240`: parameters start as `Param(i)`, a global read is `World`, and nothing relates the two. A5 covers only passing one list twice to a call, so nothing catches this. Executed:
```python
g = [1]

temper def f(mut xs: list[int]) -> int:
    n = 0
    for v in g:
        xs.append(v)
        n += 1
        if n > 5:
            return n
    return n

print(f(g))
```
- `check` accepts it. `run` and CPython print `6`, and without the bound it never ends.
- The same body appending to `g` directly is rejected as changing a list a `for` loop is running over.
- Same hole without the loop (executed): `h = g; xs.append(2); return len(h)` with `f(g)` is accepted.

**Fix:** treat `World` as possibly overlapping every parameter's region in the D4 check, or add a call-site check. Which one is the owner's call (see Q3), but as merged D4 is not enforced. **Test:** both programs as rejected cases.

**N3 (P1): a change through a temporary is excused when the temporary may be either of two aliased names.**
`own/alias.rs:402-405`: `roots` is a union over every path, and any live name in `roots[w]` is excused. Executed:
```python
temper def grow(mut xs: list[int]) -> None:
    xs.append(1)

temper def f(c: bool) -> int:
    a = [1]
    b = a
    grow(a if c else b)
    return len(a) + len(b)

print(f(True))
```
- `check` accepts it. `run` and CPython print `4`.
- With `grow(a)` instead, it is rejected.

**Fix:** excuse a name only when the temporary is a copy of that one name (`roots[w] == {a}`), and still report any other overlapping live name. **Test:** this program as rejected, with `grow(a)` and the plain non-aliased case as neighbours.

**N4 (P2): a `for` loop error is reported where there is no loop.**
`own/alias.rs:403-411`: every unnamed live temporary is treated as a loop's sequence. Executed: `return h(a if c else b, grow(a))`, with `b = a` and no loop in the body, gets "changes a list while a `for` loop over it is running; loop over a copy". Rejecting it is right; the message and fix-it are wrong. **Fix:** recognise the loop's sequence from the IR, or name the temporary's root variable. **Test:** assert the message for this program.

**N5 (P2): the D3 fix-it fails for a nested-list parameter.**
`own/alias.rs:441-445`, `docs/grammar.md:210`. Executed with `rows: list[list[int]]`:
- `return rows` gets the fix-it "return a copy: `return list(rows)`".
- Applying it (`return list(rows)`, or `out = list(rows); return out`) gets "returns a value sharing a list with its parameter `rows`; return a copy made with `list(...)`". That points back at what the user just wrote.

The decided D3 text promises `return list(xs)` as the fix-it. **Fix:** give no `list(...)` fix-it when the parameter's elements hold lists, and tell the user to copy the inner lists too. Or resolve Q1 first. **Test:** apply the fix-it to `rows` and assert the result.

**N6 (P2): a doc comment points at a module that does not exist.**
`own/alias.rs:16` says passing one list twice is "A5's check (`own::calls`)". There is no `own::calls`, and A5 is unchecked in the roadmap, so this body check rests on an unimplemented check. Executed: `temper def g(mut a: list[int], b: list[int])` that appends to `a` and returns `len(b)`, called as `g(xs, xs)`, is accepted. The gap predates the PR, but the comment states it as covered. **Fix:** say the check is pending A5 (roadmap) and drop the module name.

## Design questions for the owner
- **Q1.** The PR widens D3 to any returned value that holds a parameter's inner list (`return list(rows)` for nested lists, `return [xs]`, `return (xs, 1)`). It is recorded in grammar.md D3 but was never put to the owner. As written, a tempered function cannot return a copy of a nested-list parameter without copying by hand.
- **Q2.** "Holding a list in an exception keeps it (`owned`)" (grammar.md:64) is a new inference rule users see: `raise ValueError(xs)` now requires `owned xs`. It is reasonable, but CLAUDE.md says to propose and ask rather than decide.
- **Q3.** For N2: should a tempered body assume a parameter may be a global it reads (stricter, rejects some correct programs)? Or should callers be checked when they pass a global the callee reads?

## Optional improvements
- `Analysis::aliasing` and `tempered` match functions by name. That is fine today because a name cannot be defined twice, but it will break when overloads or generics reach tempered code.

## Checks run
| Check | Result |
|---|---|
| `cargo fmt --all --check` | passed |
| `cargo clippy --locked --all-targets -- -D warnings` | passed |
| `cargo test --locked` (all crates, conformance included) | passed (419 + 19 + 27 + 29 tests) |
| The head's `own/tests.rs` against 4771160b's code | 4 fail as expected: the K1 test, two K2 tests, and the built-tuple fields test |
| Probes through `check`, `run` and `build`, compared with CPython | as reported above |
| Rust rules in new code | no `unwrap`/`expect` outside tests, no `unsafe`, no new dependencies |

- I built into a scratch target directory because the default one is read-only.
- I briefly created a git worktree, then removed it and pruned. The reviewed tree has no tracked changes.

## Validation gaps
- **CPython version:** the only interpreter here is 3.14.8, so the CPython comparison ran against 3.14, not 3.13.
- **Other targets:** not run on Windows MSVC, macOS or AArch64.
- **Memory checks:** no valgrind run. The PR adds no new runtime code; the native-side change is a refusal.
- **Project memory:** CLAUDE.md asks for the remind-me memory tools, but they are not available in this session, so no prior context came from there.

## Verdict
**Changes required** (N1 to N3 at P1, N4 to N6 at P2).
