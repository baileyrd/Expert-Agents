<!-- pythrust/47-r1 run 4, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 236 s, 625547 tokens, $0.79 -->

# Review of PR 47 (baileyrd/PythRust): A5, reject calls that pass one list to two parameters of a tempered function

- **Base:** 5d76260eef62e1a3c7abfd48635a9a3e82a37650
- **Head reviewed:** e243a9a79786d8c10af0ce3fe2e4e5e1b9a12a67. The fetched `pull/47/head` matches this SHA. The diff is one commit.

**Verdict: changes required.** I found two soundness holes (P1) and two lesser defects (P2). In both holes, `check` accepts a call that passes one list as a tempered `mut`/`owned` parameter and as another parameter.

## Blocking findings

### 1. P1: a list stored through a module variable is missed

- **Where:** `crates/pythrust-compiler/src/own/body.rs:218-233` (`flow`) and `:299-313` (`call_stores`). The cause is how lowering keeps module variables (`lower/globals.rs:1-9`).
- **Trigger:** module code calls the tempered function after a function has stored one module variable inside another.
- **Cause:** module code keeps a module variable as a local of its own (`Fresh(rows)`) and copies it into a global, which the analysis models as `World`. Functions only reach it through `World`. When a function does `rows.append(row)`, that is a store from `World` into `World`. It never connects `Fresh(rows)` to `Fresh(row)` in the module code's state. The callee's summary has no "changes the module's lists" effect, so `call_stores` adds nothing.
- **Failure (executed):**
  ```python
  temper def add(mut rows: list[list[int]], owned row: list[int]) -> None:
      rows.append(row)
  rows: list[list[int]] = []
  row = [1]
  def stash():
      rows.append(row)
  stash()
  add(rows, row)        # rows already holds row
  print(rows)
  ```
  `check` exits 0 and `run` prints `[[1], [1]]`. The PR's own test `what_earlier_code_stores_counts` rejects the same program when `rows.append(row)` is written inline. A `mut` variant also gets through: `push(a, box)` after `def stash(): box.append(a)` is accepted and `run` prints `[[1, 1]]`.
- **Required correction:** module variables that functions can read must not be treated as separate lists once a call runs. Two options:
  - In the flow-sensitive pass, a `SetGlobal` of a local adds `World` to that local's points-to set. That makes module code match the imprecision the PR already documents for functions.
  - Or add a summary bit, "may store into the module's lists", and have `call_stores` apply `store_all(reachable(World), reachable(World))` when it is set.
- **Regression test:** both programs above, in `own/tests.rs::what_earlier_code_stores_counts`. They are accepted at head today.

### 2. P1: two parameters are assumed to be different lists

- **Where:** `body.rs:120-128` (`Flow::new` gives each parameter its own `Obj::Param(i)`) and `body.rs:159-177` (`shared`).
- **Trigger:** any function that passes its own parameters on to a tempered callee.
- **Cause:** inside `g`, `Param(0)` and `Param(1)` never meet, so `shared` treats the two arguments as distinct. Nothing proves they are. This is the exact `f(xs, xs)` case that ownership.md "Decided" 2 forbids, routed through a wrapper.
- **Failure (executed):**
  ```python
  temper def push(mut xs: list[int], ys: list[int]) -> None:
      xs.append(len(ys))
  def g(a: list[int], b: list[int]):
      push(a, b)
  x = [1]
  g(x, x)
  print(x)
  ```
  `check` exits 0 and `run` prints `[1, 1]`.
  - The nested form `def g(rows, row): add(rows, row)` with `g([r], r)` is also accepted.
  - So is the global form `def g(row): add(rows, row)` with `g(rows[0])`, which prints `[[1], [1]]`.
  - The PR's test `calls_from_any_caller_are_checked` (`own/tests.rs`, the `param` case) asserts that this global form is accepted, so the test locks the hole in.
- **Required correction:** the analysis must not treat parameters (or a parameter and `World`) as distinct without proof. Either:
  - record a per-summary precondition, "arguments i and j must not share", and check it at each caller as calls are checked now; or
  - reject at the inner call when it cannot be proven safe.

  The first option makes `g(x, x)` an error about a call to a loose function. The second rejects common wrappers. Both touch the "loose-to-loose calls keep Python aliasing" decision, so which one to take is a question for the owner (see Design questions). The hole itself is a defect either way.
- **Regression test:** `k6` above, in `calls_from_any_caller_are_checked`. Flip the `param` assertion in that test to a rejection, or make it a documented decision.

### 3. P2: the docs now contradict each other and overstate the analysis

- **Where:** `docs/grammar.md:209`, `docs/grammar.md:180` and `docs/roadmap.md:73`.
  - grammar.md:209 (D2) still says "Only a tempered callee's `mut` makes passing one list twice an error".
  - The new line at grammar.md:180 and the code (`calls.rs:345-349`) also reject `owned`.
  - The roadmap's A5 entry says the known imprecision is "both erring toward the error". Findings 1 and 2 err the other way.
- **Required correction:** bring D2 into line with whatever the owner decides about `owned`, and list the unsound gaps or fix them.

### 4. P2: the fix-it is misleading for nested lists

- **Where:** `own/calls.rs:351-354`.
- **Trigger:** the list is shared one level down, as in `push(a, [a])`. The message says "pass a copy (`list(...)`)" but does not say which argument to copy.
- **Applying the fix-it (executed):**
  - Copying the `ys` argument, `push(a, list([a]))`, is still rejected, because a shallow copy still contains `a`.
  - Copying the `mut` argument, `push(list(a), [a])`, is accepted, but the change is silently lost: `run` prints `[1]`.
- **Required correction:** name the argument to copy (the non-`mut`/`owned` one, or the inner list), and never suggest copying the `mut` argument.
- **Regression test:** assert the improved message for `push(a, [a])`, next to `a_list_inside_another_argument_is_shared`.

## Design questions for the owner

- **Rejecting `owned`, not just `mut`:** ownership.md "Decided" 2 says "one of them `mut`". The PR also rejects a shared `owned` parameter, and calls it decided in grammar.md:180 and the roadmap. That is a new language rule decided without asking (CLAUDE.md: "do not decide silently; propose and ask"). It needs the owner's sign-off.
- **The fix for finding 2:** it needs a choice between checking at calls to loose wrappers and rejecting inside the wrapper. Either way, loose-to-loose aliasing stays allowed only up to the point where a tempered callee is reached.

## Optional improvements

- `shared_args` runs a second fixpoint for each function. That is fine for now; it could share block states with `summarize` later.
- `SharedArgs.callee` is matched by name against top-level `FuncDef`s. A `FuncId` would avoid relying on names being unique.

## Checks performed

| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass, no output |
| `cargo test --locked` | all suites pass (414, 19, 27, 29, 0) |
| `cargo test --locked --test conformance` | 27 passed, including `corpus_matches_cpython`, `corpus_matches_native`, `corpus_errors_fail_build` |
| PR's new tests at base (scratch copy with only `tests.rs` and the example applied) | 4 fail as expected (`passing_one_list_twice…`, `a_list_inside_another_argument…`, `what_earlier_code_stores_counts`, `calls_from_any_caller_are_checked`). The other 4 pass at base; they are the legal programs that must stay accepted, which is expected. |
| New code against the CLAUDE.md Rust rules | no `unwrap`/`expect`, no `unsafe`, no new dependencies |
| Probe programs | through `pythrust check`/`run`, with CPython for comparison |

## Validation gaps

- **Python version:** the only interpreter here is `python3` 3.14.8, and there is no 3.13. The CPython conformance run was against 3.14. Run it with `PYTHON=python3.13` to settle this.
- **Platforms and memcheck:** I saw no Windows, macOS, AArch64 or valgrind runs. The PR changes analysis only, not codegen, so the risk there is low.
- **remind-me memory:** the `remind_me_*` tools CLAUDE.md asks for were not available in this session, so nothing was searched or saved.

**Verdict: changes required.**
