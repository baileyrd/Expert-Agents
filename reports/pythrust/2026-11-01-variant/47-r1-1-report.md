<!-- pythrust/47-r1 run 1, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 235 s, 1047688 tokens, $1.10 -->

# Review of baileyrd/PythRust PR #47: "Reject calls sharing a list with a tempered mut/owned parameter (A5)"

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Reviewed head:** `e243a9a79786d8c10af0ce3fe2e4e5e1b9a12a67`. I fetched `pull/47/head` and it matches this SHA.
- **Roadmap step claimed:** A5 (loose call-site aliasing), marked `[x]`.

**Verdict: changes required.** Three soundness or decision defects are blocking, plus two smaller documentation and test-coverage defects. Two of the holes let `push(x, x)` through to a tempered `mut` parameter. That is the exact program A5 exists to reject, and `run` then executes it.

## Blocking findings

### F1 · P1: The check treats a function's parameters as different lists, so `push(x, x)` gets through a wrapper
- **Where:** `own/body.rs:119-123`. `Flow::new` gives each parameter its own `Obj::Param(i)`, separate from every other parameter and from `World`. `shared()` at `body.rs:173-191` checks each function on its own, and nothing is passed back to that function's callers.
- **What I ran** (`push` is `temper def push(mut xs: list[int], ys: list[int]) -> None: xs.append(len(ys))`):
  ```python
  def g(a: list[int], b: list[int]):
      push(a, b)
  x = [1]
  g(x, x)
  print(x)
  ```
  `check` exits 0 and `run` prints `[1, 1]`. The same happens when a parameter and a module variable are the same list: `a = [1]`, `def g(b): push(a, b)`, `g(a)` is accepted. The direct form `def g(a): push(a, a)` is rejected, so only the "two names are different lists" assumption fails.
- **Why it's wrong:** ownership.md Decided 2 says "A loose call that passes one value to two parameters of a tempered function, one of them `mut`, is a compile error". The loose call `push(a, b)` does exactly that. The test at `own/tests.rs:437` checks the parameter-plus-module-variable shape only with a fresh `g([1])`; it never tries `g(rows[0])`.
- **Fix:** Record a requirement in `g`'s summary: parameters *i* and *j* (or parameter *i* and module state) must not share a list, because `g` passes them on to such a call. Then check that requirement at `g`'s call sites, transitively through the fixpoint. The other option is to treat parameters and `World` as possibly the same list inside `shared`.
- **Regression tests:** both programs above should be rejected at `g(x, x)` and `g(a)`. `g(x, list(x))` should stay accepted.

### F2 · P1: Changes a function makes to module-variable lists are invisible to module code
- **Where:** `own/body.rs:309-323`. `call_stores` only models what a callee does to its own arguments. Module code holds module variables as locals (`Fresh`), and `lower/globals.rs:100-121` copies them into globals in one direction only. So when a callee stores into a global, module code's local view of that list is never updated.
- **What I ran:**
  ```python
  box: list[list[int]] = []
  def stash(r: list[int]):
      box.append(r)
  a = [1]
  stash(a)
  push(box[0], a)      # box[0] is a
  print(a)
  ```
  `check` exits 0 and `run` prints `[1, 1]`. The same program with `box.append(a)` written directly in module code is rejected, so the PR's own rule gives different answers for one aliasing. A second shape behaves the same way: `def g(): rows.append(row)`, then `g()`, then `add(rows, row)` is accepted.
- **Fix:** When a list is stored into `World` (through `SetGlobal` or a callee's stores), treat it as part of `World`. That makes copied module variables count as module state, which matches the imprecision the PR already documents.
- **Regression test:** the program above should be rejected at `push(box[0], a)`.

### F3 · P1: An `owned` parameter with no `mut` now makes calls fail, against D2 as written
- **Where:** `own/calls.rs:375-379` (the `Mode::Owned => ("keeps", ...)` arm).
- **What I ran:**
  ```python
  temper def pair(owned a: list[int], b: list[int]) -> list[list[int]]:
      out: list[list[int]] = []
      out.append(a)
      return out
  x = [1]
  print(pair(x, x))
  ```
  This is rejected with "tempered `pair` keeps `a` after it returns…". It is also rejected when the `owned` is over-declared on a body that only reads `a`, even though D2 allows over-declaring.
- **Why it's wrong:** grammar.md:209 (D2) says "Only a tempered callee's `mut` makes passing one list twice an error (ownership.md, decided 2)", and ownership.md:72 says "one of them `mut`". The PR leaves grammar.md:209 unchanged and adds grammar.md:180, which now contradicts it.
- **Note for the owner:** grammar.md:64 says `owned` means the function "may *also* keep it", which suggests `owned` includes `mut`. That reading may justify the new rule, but it is a language change the PR made without asking (CLAUDE.md, open-decisions rule).
- **Fix:** Either reject only pairs that involve a written `mut`, or get the owner's decision and update D2 and ownership.md Decided 2 to say so.
- **Regression test:** whichever way it is decided, add a test for `owned`-plus-read with no `mut` (see F5).

### F4 · P2: The docs say the imprecision only ever errs toward rejecting
- **Where:**
  - `docs/roadmap.md:73` says "Known imprecision, both erring toward the error".
  - `own/calls.rs:10` says the check "errs toward the error".
  - The module doc at `own/body.rs:1-10` still describes only a flow-insensitive analysis.
- **Why it's wrong:** F1 and F2 are cases where it errs toward accepting.
- **Fix:** Correct these docs together with F1 and F2. Until then, A5 should not be ticked done, because its exit claim is false.

### F5 · P2: No test covers the `owned`-only branch
- **Where:** the `Mode::Owned` arm at `calls.rs:378`. The corpus fixture `modes_shared_call` and the unit tests all use `add(mut rows, owned row)`, so a `mut` parameter is always involved.
- **Fix:** Add a unit test in `own/tests.rs` that asserts the decided `owned`-plus-read behaviour, and its legal neighbour.

## Design questions for the owner
1. **Lists inside an argument.** Decided 2 talks about "one value" passed to two parameters. The PR also rejects a list found *inside* another argument, for example `add([x], x)` or a tuple holding `x`. That is reasonable under the "mutable aliasing" rule (ownership.md, "Where annotations must surface" 1), but nobody decided it.
2. **Elements of one list are not told apart.** Because of this, `f(rows[0], rows[1])` against a `mut` parameter is always rejected. Matrix-style code (a tempered `merge(mut dst, src)` over rows) then needs a copy at every call. This is documented, but it costs real users.

## Optional improvements
- `entries` clones the whole `State` for every block it visits, and `age` walks every local's set. This is fine at current sizes; worth keeping an eye on for large module bodies.

## Checks run
- `cargo fmt --all --check`: passes.
- `cargo clippy --locked --all-targets -- -D warnings`: passes.
- `cargo test --locked`: passes (414 + 19 + 27 + 29 tests).
- `cargo test --locked --test conformance`: passes, but against Python **3.14.8**, not 3.13.
- New tests run against the base code: 4 of the 8 fail there, as they should (`passing_one_list_twice…`, `a_list_inside_another_argument…`, `what_earlier_code_stores_counts`, `calls_from_any_caller…`). The other 4 test programs that must stay accepted, so passing at base is expected.
- Probes run through `check` and `run`: p1, p2, g1, g2 (accepted when they should be rejected), o1, o2 (rejected), and r1, r2 (redefinition ruled out). The corpus fixture `modes_shared_call` is rejected at line 13 as its marker expects.
- CLAUDE.md rules on new code: no `unwrap`, `expect` or `unsafe`, and no new dependencies.

## Validation gaps
- **CPython oracle:** only Python 3.14 is installed here. Rerun conformance with `PYTHON=python3.13`.
- **Other platforms and memcheck:** not run. The diff touches only the analysis (no lowering, codegen or runtime changes), so platform or memory differences are not expected.
- **remind-me memory tools:** CLAUDE.md asks for them, but they were not available in this session, so I checked no saved decisions and recorded nothing.
