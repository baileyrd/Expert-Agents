<!-- pythrust/48-r2 run 2, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 267 s, 1499841 tokens, $1.21 -->

**PR review: baileyrd/PythRust #48** ("A6: tempered body checks, D3/D4")

- **Base:** 5d76260eef62e1a3c7abfd48635a9a3e82a37650
- **Head reviewed:** 2a09a304c45dcdd8f090a1751b81eca4944fe229. I fetched `pull/48/head` and it matches.

**Verdict: changes required.** Both earlier findings are fixed for the cases they named. But I found four programs that break D3 or D4 and are still accepted, plus one guard that no test covers.

## Prior findings, re-checked at this head

- **K1 (tuple slots treated as different lists): fixed.** Each of these is now rejected at `a.append`: a parameter `(xs, xs)`, a tuple returned by a call, a tuple the body built as `(xs, xs)`, `rows[0]` against `rows[1]`, and `a, b = pair` unpacking (executed). One case with the same root cause is still open; see finding 2.
- **K2 (an exception holding a list turned off the checks): fixed for that one trigger.** `ValueError(xs)` now lowers and the analysis stays precise. But the mechanism behind K2 is still there, and another loose-code gap triggers it; see finding 1.

## Blocking findings

**1. P1: a lowering gap anywhere in the program still turns off D3 and D4 for every tempered function** (K2's mechanism)
- **Where:** `own/mod.rs:105-107`. When `lower_every` fails, the result is `aliasing: BTreeMap::new()`, and `modes.rs:24` returns early.
- **Trigger** (executed): loose module code that calls a builtin before a `def` shadows it. This gap is still open and is listed in the roadmap.
  ```python
  temper def extend(n: int) -> int:
      a = [1, 2]
      b = a
      b.append(n)
      return len(a)

  x = print(7)
  def print(n):
      return 1
  y = extend(3)
  ```
  - `check` exits 0 and `run` prints `7`. Without the last four lines it is rejected at `b.append(n)`.
  - The same thing happens to D3: `temper def same(xs) -> list[int]: return xs`, followed by `x = len([0])` and then `def len(n): ...`, is accepted. `run` prints `[1, 2]`, so the returned alias was mutated.
- **Why it's a defect:**
  - grammar.md:180 records this as a compile error with no exception.
  - grammar.md:64 says only that *modes* wait for a body that lowers.
  - The roadmap's A6 entry says the gap is closed.
- **Required correction:** run `alias::tempered` per function, or at least on every tempered function that lowers, instead of gating it on whole-program precision. The other option is to record the exception in grammar.md and the roadmap and ask the owner, since that is a new rule.
- **Regression test:** the program above in `own/tests.rs`, expecting the D4 error.

**2. P1: lists at different depths are assumed to be different lists, which is false through tuples**
- **Where:** `own/alias.rs:28-39`. The doc on `At` says "lists at different depths have different types, so they never are". `same()` at `alias.rs:471-483` relies on that. But `Field` is counted as a depth step (`field` → `step`, `alias.rs:247-293`), so a tuple slot and a list element can have the same type at different depths.
- **Trigger** (executed):
  ```python
  temper def f(mut p: tuple[list[int], list[list[int]]]) -> int:
      a = p[0]
      b = p[1][0]
      a.append(1)
      return len(b)

  xs = [0]
  print(f((xs, [xs])))
  ```
  - `check` accepts it, and `run` and CPython both print `2`: `b` saw the change made through `a`.
  - The neighbouring program with `b = p[1]` is rejected.
- **Required correction:** compare by depth only when the paths have the same shape (list steps versus tuple-field steps), or treat a step through a tuple field from elsewhere as `Anywhere`.
- **Regression test:** add this program to `slots_of_a_tuple_from_elsewhere_may_be_one_list`.

**3. P1: every global is one abstract list at depth 0, so a global and an element of another global are assumed distinct**
- **Where:** `own/alias.rs:240` (`Rvalue::Global` → `root(Obj::World)` at `Depth(0)`), with `same()` at `alias.rs:479`. `Obj::World` stands for every module value, so depth means nothing for it.
- **Trigger** (executed):
  ```python
  g = [[1]]
  h = g[0]

  temper def f() -> int:
      a = g[0]
      b = h
      a.append(5)
      return len(b)

  print(f())
  ```
  - `check` accepts it, and `run` and CPython print `2`.
  - The neighbour with `b = g[0]` is rejected.
- **Required correction:** give a `World` location `At::Anywhere`, the way call results are handled. You can't tell which global, or how deep inside it, a value sits.
- **Regression test:** this program, plus its neighbour `a = g`, `b = g` with `b` used later, which should stay rejected.

**4. P1: a temporary read from two names is excused from clashing with both**
- **Where:** `own/alias.rs:150-178` (`roots`) and `alias.rs:405`.
  - A conditional expression or an `and`/`or` result gets the roots `{a, b}`.
  - `change` then skips every named clash that is in `roots[w]`, so two aliased names both escape the check.
- **Trigger** (executed):
  ```python
  temper def f(c: bool) -> int:
      a = [1]
      b = a
      (a if c else b).append(1)
      return len(a) + len(b)

  print(f(True))
  ```
  - `check` accepts it and `run` prints `4`. The same holds with `(a or b).append(1)`.
  - Writing `b.append(1)` instead is rejected.
- **Required correction:** excuse a root only when it is the *only* root of the temporary. Otherwise, report a clash when one root and another live name may be the same list.
- **Regression test:** both forms above in `own/tests.rs`.

**5. P2: no test covers the new native refusal for an exception holding a list**
- **Where:** `codegen/mod.rs:38` and `codegen/mod.rs:1015-1039` (`refuse_gaps`). The old test `an_exception_holding_a_list_is_a_backend_error` was rewritten to check that the program lowers, and nothing replaced its check on the error.
- **Executed:** in a scratch worktree I disabled `refuse_gaps(program)?`.
  - The full `cargo test --locked` still passed: 419, 19, 27 and 29 tests.
  - `build examples/exception_list.pyr` then succeeded. So the guard docs/native.md relies on (against a list ⇄ exception reference cycle) has no test.
- **Required correction:** add a `cli.rs` or conformance test that `build` of `exception_list.pyr` (or `e = ValueError([1])`) fails with "the native backend does not support an exception holding a list yet".

## Design questions for the owner

- **A list in an exception now counts as `owned`** (`body.rs` stores the argument into `Obj::World`; grammar.md:64 adds "holding it in an exception is `owned`").
  - This is a new inference rule: a tempered `raise ValueError(xs)` must now write `owned xs`.
  - It is reasonable, but I found no record of Nano deciding it. Under CLAUDE.md's open-decisions rule it needs the owner's confirmation.
- **A parameter is assumed to be a different list from every global** (`w2`, executed).
  - `temper def f(mut xs: list[int]) -> int: xs.append(5); return len(g)`, called as `f(g)`, is accepted, and `xs`/`g` are one list. A5 can't see this, because only one argument is passed.
  - Rejecting it would flag every tempered `mut` function that reads a list global. The owner should decide whether D4 covers globals.

## Optional improvements

- **The D4 fix-it can point at a name that isn't bound yet.** In the K1 case it suggests `a = list(b)`, but `b` is assigned after `a`. Applying it gives a use-before-assignment. Consider suggesting a copy at the change site instead.

## Checks run

| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked` | pass: 419 + 19 + 27 + 29 + 0 tests |

- **Rust rules on new code:** no `unwrap`/`expect` outside tests, no `unsafe`, and no new dependencies.
- **Probes:** I ran `check`, `run` and `build` on about 25 scratch programs in `$TMPDIR` and compared them with `python3`.

## Validation gaps

- **CPython version:** the only interpreter here is Python 3.14.8, not 3.13. The conformance test's CPython comparison ran against 3.14, so 3.13 parity is unconfirmed. To settle it, run with `PYTHON=python3.13`.
- **Memcheck:** valgrind isn't installed, so I couldn't run memcheck on the new `NATIVE` program `aliasing`. To settle it, run memcheck on the CI corpus.
- **Other targets:** I couldn't run macOS, Windows MSVC or AArch64. The changes to `codegen` are front-end checks only.
- **Memory tools:** CLAUDE.md asks for the remind-me tools, but they weren't available in this session, so prior context from them wasn't consulted.

**Verdict: changes required.**
