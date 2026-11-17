<!-- pythrust/48-r2 run 1, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 267 s, 1561719 tokens, $1.35 -->

I found five defects that block this PR, so the verdict is **changes required**. K1 is only partly fixed: the same hole remains when two lists sit at different depths. K2 is fixed.

## Review: baileyrd/PythRust PR 48
- **Base:** 5d76260eef62e1a3c7abfd48635a9a3e82a37650
- **Head:** 2a09a304c45dcdd8f090a1751b81eca4944fe229. I fetched `pull/48/head` and it matches.
- **Commits:** 4771160 (A6 alias check) and 2a09a30 (the fix round for K1 and K2).

### Prior findings at this head
- **K1, different tuple slots treated as different lists: partly fixed.** Two slots at the same depth are now flagged. This works for a parameter tuple, a callee's result and a tuple inside a list (probes k1, k1b, k1c, d2same; all rejected). Slots at different depths are still assumed different; see F1.
- **K2, an exception holding a list turns off aliasing checks: fixed.** `e = ValueError(a)` no longer makes the whole program conservative. `b = a; ValueError(a); b.append(n); len(a)` is rejected (k2). Holding a parameter in an exception now needs `owned` (k2b). `build` refuses `examples/exception_list.pyr` with the backend error (executed). That refusal has no test; see F5.

### Blocking findings

**F1 (P1): lists at different depths are assumed to be different lists.** `crates/pythrust-compiler/src/own/alias.rs:28-39`, `:471-483` (`same`), `:240`
- **Cause:** a location only records how deep it is. The code comment says "lists at different depths have different types, so they never are". That is false in two cases:
  - Through a tuple, `p[0]` is one step deep and `p[1][0]` is two, yet both can be `list[int]`.
  - Every global is the same abstract object at depth 0 (`root(Obj::World)`), so `G` and `H[0]` look different.
- **Executed, `check` accepts all four, CPython prints `2`:**
  ```python
  temper def mk() -> tuple[list[int], list[list[int]]]:
      xs = [1]
      return (xs, [xs])
  temper def f() -> int:
      p = mk()
      a = p[0]
      b = p[1][0]
      a.append(1)
      return len(b)
  print(f())
  ```
  - The same holds for a parameter `p: tuple[list[int], list[list[int]]]` called with `(xs, [xs])`, and for one inside `list[tuple[...]]`. A5 cannot see inside one argument, as the PR's own test comment says.
  - Globals: `G = [1]; H = [G]`, then in a tempered body `g = G; h = H[0]; g.append(2); return len(h)`.
  - The same layout at equal depth (`(xs, xs)`) is rejected, which isolates depth as the cause.
- **Fix:** compare locations by the type of list they hold rather than by depth, or treat any `Depth` as possibly the same whenever the types match. Also give each global its own object, or treat World as "anywhere".
- **Tests that should go red:** add the three programs above to `slots_of_a_tuple_from_elsewhere_may_be_one_list`.

**F2 (P1): `+` and `*` on lists of lists lose the elements, for both D4 and D3.** `alias.rs:242` (`_ => Locs::new()`) and `own/body.rs:146`
- **Cause:** list concatenation and repetition give no locations, so an index into the result knows of no list. The summary pass in `body.rs` has the same gap at base. This PR makes it reachable by building D3 and D4 on it.
- **Executed, D4:** `row = [1]; grid = [row] * 2; a = grid[0]; row.append(2); return len(a)` is accepted; CPython prints 2. `[row] + [[3]]` behaves the same. Plain `grid = [row]` is correctly rejected.
- **Executed, D3:** `temper def f(xs: list[int]) -> list[list[int]]: return [xs] * 2` is accepted. The caller's `zs[0].append(2)` then changes `ys`, and `run` and CPython both print `[1, 2]`. `return rows + []` is also accepted. grammar.md's new D3 text says such a value "counts", so the doc now disagrees with the code.
- **Fix:** model `Concat` and `Repeat` in both `alias::value` and `Flow::assign`/`value`. The result should be a fresh list that stores the contents of both operands.
- **Tests that should go red:** these programs in `a_list_inside_another_is_its_own_list` and `tempered_code_returns_no_parameter`, plus a mode test that `def f(xs): return [xs]*2` gives `result_aliases`.

**F3 (P1): a change through a conditional expression is let off when one branch's name is used later.** `alias.rs:405`, with the roots computed at `:150-178`
- **Cause:** the check skips every variable the changed temporary was read from. That exempts `a` even when the temporary is `b`.
- **Executed:** `a = [1]; b = a; (a if c else b).append(2); return len(a)` is accepted, but with `c=False` this is exactly the `aliasing_shared` error (`b.append` while `a` is used later). CPython prints 2.
- **Fix:** skip only when the temporary has exactly one source variable. Otherwise treat the other sources as other names.
- **Test:** this program, rejected; next to it, `a.append(2)` with `b` dead, accepted.

**F4 (P2): the fix-it names the wrong list when the alias did not come from `b = a`.** `alias.rs:417-428`
- **Executed:** for `a = p[0]; b = p[1]; a.append(1); return len(a)*10+len(b)`, the error suggests `` `a = list(b)` ``. Applying it changes the output from 41 (CPython) to 21, because `a` becomes a copy of `p[1]`.
- **Same problem elsewhere:** probe r0 (`grid = [row]; a = grid[0]; row.append(2)`) gets the suggestion `row = list(a)`.
- **Fix:** offer `x = list(<source expression>)` only when `x` was bound by `x = y`. Otherwise use the generic `copy … with list(...)` wording.
- **Test:** add to `slots_of_a_tuple_from_elsewhere_may_be_one_list` an assertion on the message.

**F5 (P2, coverage): nothing tests `codegen::refuse_gaps`.** `codegen/mod.rs:1015-1039`
- The conformance harness treats any "does not support" result as acceptable, so deleting the refusal turns no test red.
- The old `lower/tests.rs` assertion on this message was replaced with one that only checks lowering.
- **Fix:** add a `tests/cli.rs` or codegen test that `build` of `ValueError([1])` fails with this message and leaves no output file.

All five make the `[x] A6` entry in roadmap.md and the new D3/D4 "Implemented (A6)" text in grammar.md claim more than the code does.

### Design questions
- **Two parameters, or a parameter and a global, are assumed different** (`Obj::Param(i)` versus `Obj::World`). D4 hands that case to A5, which is still unchecked in the roadmap, and A5 as written covers `f(xs, xs)`, not `f(G)` where the body also reads `G`. Is the gap acceptable until A5 lands, and should A5 also cover a parameter that is also a global?

### Optional improvements
- `each_run_of_an_allocation_is_a_new_list` and `a_narrowed_optional_is_the_same_name` pass at base. They are useful as legal neighbours, but each pairs with no rejected program of its own.

### Checks
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: passed.
- `cargo test --locked`, conformance included: all passed (416 + 19 + 27 + 29).
- New `own/tests.rs` run at base: 11 of the new or changed tests fail there, as they should.
- CLAUDE.md rules on the new code: no `unwrap`/`expect`, no `unsafe`, no new dependency.
- The default `CARGO_TARGET_DIR` is read-only here, so I built into `$TMPDIR`.

### Validation gaps
- **CPython 3.13 was not available.** The conformance comparison and my probes ran on Python 3.14.8. Re-run conformance with `PYTHON=python3.13`.
- **Not run:** Windows, macOS, AArch64 and valgrind/memcheck. This PR's native change is only a refusal, so the risk there is low.
- **Memory tools:** the remind-me tools CLAUDE.md requires were not available in this session, so I searched and saved no memory entries.

### Verdict: **changes required**
