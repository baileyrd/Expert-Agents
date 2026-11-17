<!-- pythrust/48-r2 run 5, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 323 s, 1880220 tokens, $1.64 -->

I'm requesting changes on PR 48. The two earlier findings are only partly fixed, and I traced five more defects in the new alias check.

- **Repository:** baileyrd/PythRust, PR 48
- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head reviewed:** `2a09a304c45dcdd8f090a1751b81eca4944fe229` (fetched and confirmed equal)

## Prior findings, re-checked at this head

- **K1 (tuple slots assumed to hold different lists): fixed only for slots at the same depth.** `pair[0]`/`pair[1]`, a call's tuple result, `rows[0]`/`rows[1]` and a tuple the body built from `(x, x)` are all rejected now. The new test `slots_of_a_tuple_from_elsewhere_may_be_one_list` fails at 4771160 and passes here. But one list sitting at two different depths is still treated as two lists (F1).
- **K2 (an exception holding a list turns off alias checks for the whole program): fixed for exceptions only.** `ValueError([1])` now lowers, and the D4 error fires (probe `k2b`, test `an_exception_holding_a_list_leaves_aliasing_checked`, which fails at 4771160). Any other lowering gap anywhere in the program still switches D3 and D4 off for every tempered function (F4).

## Blocking findings

### F1 (P1): two slots at different depths are assumed to be different lists
- **Where:** `own/alias.rs:28-39` (the `At` comment says "lists at different depths have different types, so they never are") and `same()` at `alias.rs:475-482`.
- **Why it's wrong:** inside a tuple that comment is false. In `(xs, [xs])` the same list is at depth 1 and at depth 2. In `(xs, (xs, 1))` it is at depths 1 and 2 with the same type.
- **Trigger (executed):**
  ```python
  temper def f(mut t: tuple[list[int], list[list[int]]]) -> int:
      a = t[0]
      rows = t[1]
      b = rows[0]
      a.append(7)
      return len(b)
  xs = [1]
  print(f((xs, [xs])))
  ```
  `check` exits 0, and `run` and CPython both print `2`: the change through `a` shows up through `b`. The nested-tuple form `t: tuple[list[int], tuple[list[int], int]]` (probe `d3`) is also accepted. Both should be D4 errors on `a.append(7)`.
- **Fix:** when an abstract object came from a tuple type, compare type-compatible depths, or treat any two depths of a parameter or call result as possibly the same list.
- **Regression test:** add both programs to `slots_of_a_tuple_from_elsewhere_may_be_one_list`.

### F2 (P1): a parameter and a module variable are assumed to be different lists, and changes made through module variables are not modelled
- **Where:** `same()` returns false whenever `seen.obj != changed.obj` (`alias.rs:472`), so `Obj::Param(i)` never meets `Obj::World`.
- **Why it's wrong:** A5 (decided 2) covers only one value passed to two parameters, so nothing rules this case out.
- **Missed cases also in this finding:**
  - `change()` (`alias.rs:398-414`) only looks at live locals, so a later read of a global by name is never "another name".
  - `inst()` (`alias.rs:374-381`) treats only `mut` arguments as changes, so a callee that changes a global is invisible.
- **Triggers (executed, `check` exits 0 on all three; each should be a D4 error):**
  ```python
  G = [1]
  temper def f(mut a: list[int]) -> int:
      g = G
      a.append(1)
      return len(g)
  print(f(G), G)
  ```
  - `g = G; g.append(n); return len(G)` (probe `g2`). The two-local form `g = G; h = G; g.append(n); return len(h)` is rejected (probe `g3`).
  - A tempered `bump()` doing `G.append(1)`, called while `g = G` is still used afterwards (probe `c3`).
- **Fix:**
  - Treat `Param(i)` as possibly `World` in `same`/`overlaps`.
  - Count a later global read as a use of `World`.
  - Add a "changes the module's lists" bit to `Summary`.
- **Regression tests:** these three programs, plus a legal neighbour where the global is not read again.

### F3 (P1): a temporary read from several variables hides every one of them
- **Where:** `roots()` (`alias.rs:150-178`) merges all sources of a temporary. `change()` then excludes every root from the clash (`alias.rs:405`).
- **Trigger (executed):**
  ```python
  temper def f(c: bool) -> int:
      a = [1]
      b = a
      (a if c else b).append(1)
      return len(b)
  print(f(True))
  ```
  `check` exits 0, and `run` and CPython both print `2`. Writing `a.append(1)` instead is rejected (probe `c1ctl`). Probe `c6` (a `mut a` parameter against a fresh `b`) is also accepted.
- **Fix:** exclude a root only when the temporary has exactly one root, or track roots per path in the forward pass.
- **Regression test:** `c1` as a rejected program, with `(b if c else b).append(1)` as the legal neighbour.

### F4 (P1): any lowering gap anywhere still turns off D3 and D4 for every tempered function (K2's class remains)
- **Where:**
  - `own/mod.rs:105-109`: when lowering fails, `aliasing` is set to `BTreeMap::new()`.
  - `own/tests.rs:335-340` `a_conservative_analysis_diagnoses_nothing` writes this behaviour into a test.
  - `roadmap.md:71` claims "A6 closed the last one". That is false: the early-shadow gap (`lower/expr.rs:600`) and the other `unsupported` sites remain.
- **Trigger (executed):**
  ```python
  temper def extend(n: int) -> int:
      a = [1, 2]
      b = a
      b.append(n)
      return len(a)
  x = print(7)
  def print(n):
      return 1
  extend(3)
  ```
  `check` exits 0 and `run` prints `7`. Delete the last-but-two lines and the same function gets the D4 error (probe `k2b`). `return xs` in a tempered function is also accepted next to the gap (probe `k2c`). Neither grammar.md D3/D4 nor the deviation entry says the check can be off.
- **Fix:** run the alias check on tempered bodies independently of whole-program lowering (lower tempered bodies alone), or reject instead of skipping. Correct `roadmap.md:71`.
- **Regression test:** `k2a`, expecting the D4 error.

### F5 (P2): the D4 fix-it suggests copying the wrong list
- **Where:** `alias.rs:417-428` always suggests `` `{b} = list({a})` ``, whatever `b` was bound from.
- **Trigger (executed):** in `fx.pyr`, `a = t[0]; b = t[1]; a.append(1); return len(a)*10+len(b)` called with `([1], [2, 3])` gets the fix-it `a = list(b)`.
  - Applied in place, it refers to `b` before `b` is bound.
  - Reordered so it compiles, it prints `32`; the loosened original prints `22` in CPython. The right fix, `a = list(t[0])`, prints `22`.
- **Fix:** suggest `list(<the expression a was bound from>)`, or use the generic `list(...)` form unless the binding was a plain `a = b`.
- **Regression test:** assert the message for a tuple-slot case.

### F6 (P2): the D3 fix-it `return list(xs)` is offered for parameters it cannot copy
- **Where:** `alias.rs:441-442`.
- **Trigger (executed):**
  - `temper def f(t: tuple[list[int], int]) -> tuple[list[int], int]: return t` gets ``return list(t)``. Applied, it fails: "`list` copies a list: expected list[T0], found tuple[...]".
  - The same happens for `xs: list[int] | None` (probes `s3fix`, `s5fix`).
- **Fix:** offer `return list(xs)` only when the parameter's type is a list; otherwise use the generic "return a copy made with `list(...)`".
- **Regression test:** the tuple and optional cases.

### F7 (P2): the native refusal of an exception holding a list has no test
- **Where:** `codegen/mod.rs:1015-1039` (`refuse_gaps`). The old test that checked the refusal was rewritten into `an_exception_holding_a_list_lowers` (`lower/tests.rs:836`), and `exception_list` is not in `NATIVE`.
- **Effect:** nothing asserts that `build` refuses. Deleting `refuse_gaps` would compile a program that can form a reference cycle, and no test would fail. The refusal itself works: `build exception_list.pyr` and `ValueError((xs, 1))` both give the named backend error.
- **Fix:** add a `tests/cli.rs` or conformance case asserting the `build` error text for `exception_list.pyr`.

## Design questions for Nano
1. **Holding a list in an exception makes the parameter `owned`.** This is a new inference rule that grammar.md now states. It applies even when the exception is caught or dropped inside the function, so such a function must write `owned`. Loose functions with written modes that `check` accepted before this PR can now fail. CLAUDE.md asks that new inference rules be proposed, not decided silently.
2. **What counts as "two names" for one list.** The PR treats a list and its container as different lists. As a result, `b = rows[1]` before the change is rejected, but `len(rows[1])` after `a = rows[0]; a.append(1)` is accepted, although both see the same change.
3. **Passing one list twice is left to A5, which is not implemented.**
   - `temper def f(mut a, b)` called as `f(xs, xs)` is accepted today (probe `g4`).
   - So is a tempered caller doing `grow2(xs, xs)` (probe `c5`).
   - So is `f(xs, [xs])` (probe `g5`).
   - Decided 2 covers only loose calls. Someone should decide who rejects the tempered-to-tempered and nested cases.

## Optional improvements
- In `change()`, any clashing unnamed temporary is reported with the `for`-loop message. A clash that isn't a loop's sequence would get a misleading message. I couldn't build such a case, so this is not a finding.

## Checks run
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: passed.
- `cargo test --locked`: passed (419 + 19 + 27 + 29 tests). Conformance ran against **Python 3.14.8, not 3.13**.
- The head's `own` tests run against 4771160: the four new K1/K2 and exception tests fail there, so they do guard the fix.
- Native build of `aliasing.pyr` matches `aliasing.out`.
- New Rust code: no `unwrap`/`expect`, no `unsafe`, no new dependencies.

## Not verified
- CPython 3.13 comparison: needs a run with `PYTHON=python3.13`.
- Memcheck: valgrind isn't installed, so `aliasing` in `NATIVE` was not checked for leaks.
- macOS, Windows MSVC and AArch64 builds.
- The remind-me memory tools were not available, so prior decisions were taken from the repository only.

## Verdict
**Changes required.**
