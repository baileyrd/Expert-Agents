<!-- pythrust/47-r1 run 1, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 238 s, 602555 tokens, $0.79 -->

# Review of baileyrd/PythRust PR 47: A5 rejects calls that share one list between a tempered function's `mut`/`owned` parameters

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head reviewed:** `e243a9a79786d8c10af0ce3fe2e4e5e1b9a12a67`. The fetched `pull/47/head` matches it.
- **Scope:** one commit touching `own/body.rs`, the new `own/calls.rs`, `own/mod.rs`, `own/modes.rs`, `own/tests.rs`, `grammar.md`, `roadmap.md` and the corpus file `modes_shared_call`.

**Verdict: changes required.** There are two P1 soundness holes and one coverage finding.

## Blocking findings

### 1. P1: a callee that writes to a module list is not modelled, so the shared list is missed
- **Where:** `crates/pythrust-compiler/src/own/body.rs:309-323` (`call_stores`) and `:228` (`SetGlobal`), used by `shared_args` at `:76`.
- **Cause:** In the module's code, module variables are locals that point at `Fresh` objects; `SetGlobal` only copies them into `World`. A callee whose body stores one module list in another has no parameters for `call_stores` to look at. So nothing links the caller's `Fresh(rows)` to `Fresh(row)`, and the flow-sensitive state at the later call says they are separate. A parameter the callee keeps is written into `World` but not into the module local that holds the same list.
- **Trigger** (*executed*, `pythrust check` exits 0):
  ```python
  temper def add(mut rows: list[list[int]], owned row: list[int]) -> None:
      rows.append(row)
  rows: list[list[int]] = []
  row = [1]
  def g():
      rows.append(row)
  g()
  add(rows, row)        # row is already inside rows
  ```
  A second version passes `row` to `def stash(r: list[int]): store.append(r)` and then calls `add(store, row)`. It is also accepted.
- **What goes wrong:** At runtime (confirmed in CPython: `any(x is row for x in store)` is True) this call passes one list both inside `rows` and as `row`. That breaks ownership.md Decided 2 and the PR's own grammar.md entry ("a call that **may** pass one list, or a list inside it… is a compile error").
- **Required correction:** Model module-level effects across calls. For example, add a summary bit "may store into module values". At a call whose callee has that bit, merge every list stored in `World` into one region, as `World` already does inside function bodies. Lists the call does not touch should keep their precision.
- **Regression tests:** both programs above in `own/tests.rs` (`what_earlier_code_stores_counts`), each asserting the A5 error.

### 2. P1: a loose function's parameters are assumed to hold different lists
- **Where:** `own/body.rs:117-122` (`Flow::new` gives each parameter its own `Obj::Param(i)`) and `own/calls.rs:48-50` (calls to loose functions are never checked).
- **Cause:** Inside a loose caller, `Param(0)`, `Param(1)` and `World` never overlap, so `push(a, b)` is accepted. Nothing checks the caller's own call sites either, because the caller is loose. No step proves the two names distinct before treating them so.
- **Trigger** (*executed*, `check` exits 0; CPython prints `[1, 1]`, meaning `push` ran with `xs is ys`):
  ```python
  temper def push(mut xs: list[int], ys: list[int]) -> None:
      xs.append(len(ys))
  def g(a, b):
      push(a, b)
  x = [1]
  g(x, x)
  ```
  The same happens with nesting: `def g(rs, r): add(rs, r)` followed by `g([x], x)`. It also happens with a module list and a parameter: `def g(row): add(rows, row)` followed by `g(rows[0])`.
- **Test entrenching it:** `calls_from_any_caller_are_checked` asserts that the `param` case is `Ok` (`own/tests.rs`, the `def g(row: list[int]): add(rows, row)` line), so it locks the hole in.
- **Required correction:** Do one of these:
  - inside the flow-sensitive pass, let a loose caller's parameters (and `World`) possibly overlap each other; or
  - pass the constraint up: when a loose caller forwards parameters `i` and `j` into a tempered callee's shared pair, check the loose caller's own call sites for `i`/`j` too.

  Either way, the roadmap's "Known imprecision" list must state the choice.
- **Regression tests:** `g(x, x)` and `g([x], x)` above, rejected. As a legal neighbour that must stay accepted: `g(x, list(x))`.

### 3. P2: no test covers either route above
- The new tests cover direct `append`, loops, rebinding, tuples and globals read inside a function. None covers a callee that writes module values, or parameters forwarded by a loose caller.
- The tests named in findings 1 and 2 belong in `own/tests.rs`. A corpus program for each would go next to `examples/modes_shared_call.pyr`.

## Design question (for the owner, not a finding)

**The PR adds `owned` to the rule without asking.** Ownership.md Decided 2 covers only a parameter "one of them `mut`". The PR also rejects calls where one parameter is `owned` and the other is `read`. Executed example: `temper def keep(owned a: list[int], b: list[int])` called as `keep(x, x)` is now a compile error, though neither parameter is `mut`. The rule is listed in grammar.md, but the decision was not changed first. CLAUDE.md says "do not decide silently; propose and ask". Nano should confirm `owned` belongs in the rule, or the check should be limited to `mut`.

## Optional improvements
- `calls::check` matches calls to definitions by source name through a `HashMap` over top-level `def`s. That is safe today only because a second definition of the same name is rejected ("`push` is defined twice", executed). Matching on the IR's `FuncId` would not depend on that rule.
- `Flow` now carries both `pts`/`stored` and a `State` copy of them. If `Flow` held a `State` directly, `load` and `state` would not need to clone.

## Checks run
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: passed, no warnings.
- `cargo test --locked`: the library (414) and the other suite run (19) passed. The first `conformance` run had 3 native failures: `build_takes_option_like_directories_as_paths`, `corpus_matches_native` and `native_matches_run_at_the_repr_limit`. Two full reruns passed 27/27. The PR touches no native or build code, so I put the failures down to a transient environment problem; that is a guess, I did not prove it.
- `cargo test -p pythrust-compiler --lib own::`: 40 passed, including all 8 new tests.
- New tests at the base commit: they refer to `SharedArgs` and a diagnostic that does not exist there, so they cannot pass at base. The rejection tests guard the new check.
- CLAUDE.md Rust rules on the new code: no `unwrap`/`expect` outside tests, no `unsafe`, no new dependencies.
- Probes run with `pythrust check` and `python3`: g1, g2, g3, g5, g6 and h2 confirmed the issues above. g4 (redefinition), g7, g8, g9 and h1 (exception edges) were handled correctly.

## Validation gaps
- **CPython version:** the machine has Python 3.14.8, not 3.13, so `corpus_matches_cpython` passing is not proof against 3.13. A rerun with `python3.13` would settle it.
- **Other targets and memcheck:** I did not run Windows MSVC, macOS, AArch64 or valgrind. The diff changes compile-time analysis only, so the risk there is low.
- **Memory tool:** CLAUDE.md asks for the remind-me memory tools, but they are not available in this session, so no prior context was looked up or saved.
