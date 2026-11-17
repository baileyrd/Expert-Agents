<!-- pythrust/48-r2 run 3, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 313 s, 1005361 tokens, $1.28 -->

I'm requesting changes. K2 is fixed, but K1 is only partly fixed: the same alias hole still reproduces when the two slots are at different depths. I also found two new ways to get past the D4 check and one wrong D3 error.

**Repository:** baileyrd/PythRust, PR #48
**Base:** 5d76260eef62e1a3c7abfd48635a9a3e82a37650
**Reviewed head:** 2a09a304c45dcdd8f090a1751b81eca4944fe229 (matches the fetched `pull/48/head`)

## Prior findings, re-checked

- **K1 (P1), different tuple slots assumed to hold different lists: partly fixed.** A tuple the function did not build now treats two slots at the same depth as possibly one list (`(xs, xs)`), and that case is now rejected. The same hole is still open when the two names are at different depths. See finding 1.
- **K2 (blocking), an exception holding a list turned off aliasing checks for the whole program: fixed.** `ValueError(xs)` now lowers, the analysis stays precise, and only native `build` refuses it (`codegen/mod.rs:1017`). The test `an_exception_holding_a_list_leaves_aliasing_checked` fails at the previous review's head (4771160) and passes here. The fix introduced finding 4.

## Blocking findings

**1. P1: two lists at different depths are assumed different (K1 still open).** `own/alias.rs:28-39` (the `At::Depth` type) and `same()` at `:471-483`.
- The comment at `:30-32` says lists at different depths "have different types, so they never are" the same list. That is false for tuples, and for module globals, which all share one abstract list (`Obj::World`).
- Executed probes. `check` and `run` accept each one and print `2`:
  ```python
  temper def f(mut pair: tuple[list[int], tuple[list[int], int]]) -> int:
      a = pair[0]          # depth 1
      b = pair[1][0]       # depth 2, the same list when the caller passes (xs, (xs, 1))
      a.append(2)
      return len(b)
  xs = [1]
  print(f((xs, (xs, 1))))
  ```
  The same hole shows with `tuple[list[int], list[list[int]]]` passed `(xs, [xs])`. It also shows with a tuple returned by a tempered callee, `return (a, [a])`.
  Through globals, also accepted:
  ```python
  G = [1]
  H = [G]
  temper def f() -> int:
      a = G
      b = H[0]
      a.append(2)
      return len(b)
  ```
  The control case (both slots at depth 1) is rejected as it should be.
- **What should happen:** a D4 error on `a.append(2)`.
- **Fix:** decide "may be the same list" by the location's static type (or the path of fields to it), not by the number of steps inside it. Then correct the comment.
- **Regression tests:** these programs in `own/tests.rs`, in or next to `slots_of_a_tuple_from_elsewhere_may_be_one_list`.

**2. P1: changing a list through a conditional expression skips D4.** `own/alias.rs:150-178` (`roots`) and `:405`.
- A temporary's roots are joined from every place it is assigned. The check then ignores every root as a possible second name. For `a if c else b`, both `a` and `b` are ignored, so neither counts as the other name.
- Executed: `check` accepts this and `run` prints `4`. CPython also prints `4`; the point is that D4 forbids the program.
  ```python
  temper def f(c: bool) -> int:
      a = [1]
      b = a
      (a if c else b).append(2)
      return len(a) + len(b)
  ```
  The same body with `a.append(2)` is rejected.
- **Fix:** only ignore a root when the temporary is exactly that variable on every path, for example when it has a single definition, or by tracking the "same as variable" fact through the forward pass. Otherwise ignore none.
- **Regression test:** this program must be rejected, and the single-name `rows[0].append(1)` case must stay accepted.

**3. P1: a parameter and a module variable are assumed to be different lists.** `same()` at `own/alias.rs:472` compares `Obj::Param(i)` with `Obj::World` only by identity.
- Executed: `check` accepts this and prints `2`.
  ```python
  G = [1]
  temper def f(mut xs: list[int]) -> int:
      g = G
      xs.append(2)
      return len(g)
  print(f(G))
  ```
- `xs` and `g` are two live names for one list, and one of them changes it. That is D4 as `grammar.md` states it.
- A5's planned call-site check would not catch this either: only one argument is passed, so nothing is passed twice. The new D4 text marks the rule "Implemented (A6)" and lists no such exception.
- **Fix:** treat `Param` and `World` locations of the same type as possibly the same list in `same()`/`overlaps()`, or ask the owner whether this belongs at the call site instead.
- **Regression test:** this program must be rejected.

**4. P2: a false D3 error after an exception holds a parameter.** `own/body.rs:108-112` stores the exception's list into `Obj::World`. `returned()` (`alias.rs:431-449`) then sees every global as sharing the parameter's list.
- Executed: this is rejected with "returns a value sharing a list with its parameter `xs`", which is untrue.
  ```python
  G = [7]
  temper def f(owned xs: list[int]) -> list[int] raises ValueError:
      if len(xs) == 0:
          raise ValueError(xs)
      return G
  ```
  The same function raising `ValueError("empty")` is accepted.
- **Fix:** record "kept by an exception" so it makes the parameter `owned` without putting the list into the globals' contents.
- **Regression test:** this program must be accepted.

**5. P2: nothing tests that native `build` still refuses an exception holding a list.**
- The PR moved the refusal from lowering to `codegen::refuse_gaps` (`codegen/mod.rs:1017`). It turned the old lowering test into `an_exception_holding_a_list_lowers`, which checks the opposite.
- I removed nothing to test this, but no test in the repo fails if `refuse_gaps(program)?` is deleted. Without the refusal, native code would build the reference-count cycle `native.md` warns about.
- I executed the refusal by hand: `build` on `ValueError(xs)` gives `backend error: the native backend does not support an exception holding a list yet`.
- **Fix:** add a `codegen` or `tests/cli.rs` test that builds `raise ValueError([1])` and expects that error, plus the tuple and optional forms.

## Design questions (for the owner, not findings)

- **"Holding a list in an exception is `owned`" is a new inference rule.** The PR writes it into `grammar.md` D2 and `roadmap.md` A6. Before this PR, `temper def f(xs: list[int]) raises ValueError: raise ValueError(xs)` was accepted (the analysis fell back and checked nothing). Now it needs `owned xs`. CLAUDE.md says "do not decide silently; propose and ask", so the owner should confirm this rule.
- **A6 is marked done, but D4 still depends on unimplemented A5.** Inside a body, two parameters are assumed to be different lists. So `temper def f(mut a, b)` called as `f(xs, xs)` is accepted today; I executed this. That is A5's job, and the docs say so, but D4 is not enforced end to end until A5 lands.

## Optional improvements

- In `change()` (`alias.rs:403-412`), any unnamed live temporary that may be the changed list produces the "`for` loop over it is running" message, not only a loop's sequence. The message could be wrong for other temporaries.

## Checks run

- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: passed.
- `cargo test --locked`: passed (419 + 19 + 27 + 29 tests, no failures). I had to point `CARGO_TARGET_DIR` at a temp directory because the configured target directory is read-only.
- The new `own` tests against the previous head (4771160): 4 fail, as they should (the K1 and K2 tests and the built-tuple test).
- Probes run with `check`, `run` and `build` from a scratch directory, compared with `python3`.
- CLAUDE.md Rust rules on the new code: no `unwrap`/`expect`, no `unsafe`, no new dependencies.

## Validation gaps

- The CPython comparison ran on Python 3.14.8, not 3.13, because 3.13 isn't installed.
- valgrind isn't installed, so there was no memcheck run.
- Not run on macOS, Windows MSVC or AArch64.
- CLAUDE.md asks to search and save in the remind-me memory, but those tools aren't available in this session, so I neither searched nor saved anything there.

**Verdict: changes required.**
