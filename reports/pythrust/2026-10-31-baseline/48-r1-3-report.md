<!-- pythrust/48-r1 run 3, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 266 s, 1204463 tokens, $1.20 -->

I found four blocking defects, so the verdict is **changes required**. The worst is that the new D3/D4 checks stop running for the whole program whenever any function hits a lowering gap. Ordinary list code hits one: `xs + []`, `b += [2]` and `[0] * n` all do, and a tempered `return xs` is then accepted.

**PR 48, baileyrd/PythRust** ("Check aliasing in tempered bodies (A6)"), one commit.
- Base: `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- Head: `4771160b650e6a50edeaf6bc48d244050a69f057`. I fetched `pull/48/head` and it matches.

## Blocking findings

**1. P1: List `+`, `+=` and `*` in any function turn off D3/D4 for the whole program, though the roadmap marks A6 done.**
- **Where:** `crates/pythrust-compiler/src/own/mod.rs:96-110` (when lowering fails, `aliasing` is left empty), `docs/roadmap.md:74`, `docs/grammar.md:210-211`.
- **What the docs say:** A6's exit criterion says it "closes any other lowering gap a tempered body can reach instead of rejecting it". The PR says a probe "found no further lowering gap; the one left is an exception holding a list".
- **Executed:**
  ```python
  temper def f(xs: list[int]) -> list[int]:
      ys = xs + []          # also: b += [2], zs = [0] * n
      return xs
  print(f([1]))
  ```
  `check` exits 0. `build` fails with "arithmetic on a non-number". Remove line 2 and `check` gives the D3 error.
- **Second trigger (executed):** a *loose* function elsewhere doing `raise ValueError(xs)` stops D4 firing on `a = [1]; b = a; b.append(2); return len(a)` in a tempered `f`. Change the raise to `ValueError("big")` and the error comes back.
- **Fix:** lower list concat and repeat, and make the D3/D4 result independent of unrelated loose functions. Or keep A6 open and list the gap in grammar.md's D3/D4 text, as grammar.md:64 does for modes.
- **Regression tests:** the probe above, plus the loose-`ValueError(xs)` neighbour, in `own/tests.rs`.

**2. P1: A callee that changes a module list goes unseen.**
- **Where:** `own/alias.rs:339-353`. Only arguments with mode ≥ `mut` count as a change, and `Summary` has no "changes the module's values" effect (`body.rs` `summary`).
- **Executed:**
  ```python
  g = [1, 2]
  temper def bump() -> None:
      g.append(3)
  temper def f() -> int:
      n = 0
      for x in g:
          if n < 5:
              bump()
          n += 1
      return n
  ```
  `check` accepts it, and `run` and CPython both print 7, because the list grows during the loop. Inline the `g.append(3)` and it is rejected with the for-loop error. The same holds for `a = g; bump(); return len(a)`.
- **Fix:** add a summary bit for "may change the module's values" (whether `mutated` meets the World region), and treat such a call as `Change::Within` on `Obj::World`.
- **Regression tests:** both programs rejected; a callee that only reads `g` stays accepted.

**3. P1: A temporary read from two names is excused for both.**
- **Where:** `own/alias.rs:143-171` (roots are gathered flow-insensitively) and `:376` (every root is excluded).
- **Executed:**
  ```python
  temper def f(c: bool) -> int:
      a = [1]
      b = a
      (a if c else b).append(2)
      return len(a) + len(b)
  ```
  `check` accepts it; `run` and CPython print 4. With `b.append(2)` it is rejected.
- **Fix:** excuse a name only when it is the temporary's sole root on that path. That means computing roots flow-sensitively, or requiring `roots[w] == {a}`.
- **Regression test:** this program rejected; `x = a if c else b` with only `x` used later stays accepted.

**4. P2: Any live temporary is reported as a `for` loop.**
- **Where:** `own/alias.rs:374-382`.
- **Executed:** with a tempered `grow(mut xs: list[list[int]]) -> int` that changes `xs[0]`, the line `print(a[0], grow(b))` after `b = a` reports "changes a list while a `for` loop over it is running". The program has no loop. The rejection is right; the message and fix-it are wrong.
- **Fix:** have lowering mark the loop's sequence temporary and use a neutral message for other temporaries.
- **Regression test:** assert the message on this program.

## Design questions
- In `glob_ctl`, the D4 fix-it suggests `a = list(...)` for a name bound from a global. That works, but it reads like "replace the binding". The owner may want the fix-it to point at the binding site.

## Optional improvements
- `inst_operands`/`rvalue_operands` (`alias.rs:537-584`) repeat what each IR form reads. The match is exhaustive, so a new form won't compile until it's added there, but a shared helper on `ir` would stop the two copies drifting apart.

## Tests
- Run against the base source, these new tests fail as they should: `a_second_name…`, `aliasing_is_checked…`, `a_list_inside…`, `a_mut_argument…`, `changing_a_list_a_loop…`, `tempered_code_returns…`, `aliasing_comes_before_modes`.
- `a_narrowed_optional…`, `each_run_of_an_allocation…` and `loose_code_keeps_python_aliasing` pass at base too. That is expected: they check that legal programs stay accepted.
- The missing tests are the ones named under each finding.

## Checks run
- `cargo fmt --all --check`: pass.
- `cargo clippy --locked --all-targets -- -D warnings`: pass.
- `cargo test --locked`: all pass (416 + 19 + 27 + 29 + 0), conformance included.
- New code has no `unwrap`/`expect`, no `unsafe` and no new dependencies.
- CLAUDE.md asks for the remind-me memory tools; none were available here, and this review is read-only.

## Validation gaps
- Conformance ran against CPython **3.14.8**, the only interpreter on this machine. Rerun with `PYTHON=python3.13`.
- I did not run memcheck/valgrind. Codegen only changed a pattern, but `examples/aliasing` joined `NATIVE`, so valgrind its native build.
- macOS, Windows MSVC and AArch64 were not run.

## Verdict
**changes required**
