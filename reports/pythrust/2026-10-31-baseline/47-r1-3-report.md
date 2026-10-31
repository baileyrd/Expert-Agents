<!-- pythrust/47-r1 run 3, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 251 s, 718875 tokens, $0.83 -->

**PR 47, baileyrd/PythRust: changes required.** The PR adds the A5 check: a call that may pass the same list to two parameters of a tempered function, one of them `mut` or `owned`, is now a compile error. The check is well built and the tests cover it. But it misses a case: at module level it accepts programs that break this rule when a function in between changes the module's lists.

- Base: `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- Head: `e243a9a79786d8c10af0ce3fe2e4e5e1b9a12a67`. I fetched `pull/47/head` and it matches this SHA.
- Commit reviewed: one, e243a9a, "Reject calls sharing a list with a tempered mut/owned parameter (A5)".

## Blocking findings

### P1: module-level calls miss sharing that a callee creates through module variables
- **Where:** `crates/pythrust-compiler/src/own/body.rs:309-323` (`call_stores`) and `:449-463` (`reachable`), used by `shared` at `:173-191`. The doc comment at `own/calls.rs:319-323` and the A5 entry in roadmap.md also describe this wrongly.
- **Trigger:** module-level code calls a function that stores into a module list, then passes those module lists to a tempered `mut`/`owned` parameter.
- **Failure scenario (executed):**
  ```python
  temper def add(mut rows: list[list[int]], owned row: list[int]) -> None:
      rows.append(row)

  keep: list[list[int]] = []

  def k(r: list[int]):
      keep.append(r)

  row = [1]
  k(row)
  add(keep, row)      # row is already inside keep
  print(keep)
  ```
  - `pythrust check` accepts it, and `run` prints `[[1], [1]]`, the same as CPython. So `row` is passed both on its own and inside `keep`, which Decided 2 (ownership.md) and the PR's own A5 entry say must be an error about the call.
  - A second form is also accepted: `def g(): rows.append(row)`, then `g()`, then `add(rows, row)` at module level (probe `g1`).
  - Inside a function the same pattern is correctly rejected (probe `g6`).
- **Cause (source-traced):**
  - A module variable that a function reads stays a local of the module's code (`lower/globals.rs:4-8`). In the module's code it is `Obj::Fresh(local, …)`, and its value is only copied into `Obj::World` by `SetGlobal`.
  - When the callee `k` stores into `World` (`call_stores` adds the kept `Fresh(row)` to `stored[World]`), nothing connects that back to `Fresh(keep)`.
  - So `reachable({Fresh(keep)})` is just `{Fresh(keep)}`, it does not meet `{Fresh(row)}`, and the call is accepted.
  - The documented rule "module variables a function reads count as one list" holds inside functions, where they read `Global`. It does not hold in the module's own code.
- **Required correction:** once a list is stored into `World` (it is in `reachable({World})`), treat it as a `World` list. For example, add `Obj::World` to the reach set of any argument that reaches such a list, or add `World` to a local's points-to set at its `SetGlobal`. Then a callee that changes the module's lists counts as possibly storing one in another.
  - This keeps the documented imprecision and makes it true in module code too.
  - The doc comment at `calls.rs:319-323` and the roadmap A5 text should then describe the rule accurately.
- **Regression test:** add the program above (and the `g()` form) to `own/tests.rs::calls_from_any_caller_are_checked`, expecting the `add` message at `add(keep, row)`. Both are accepted at head, so these tests fail without the fix.

## Design questions (for the owner, not findings)
- **The check now covers `owned`, not just `mut`.** ownership.md Decided 2 and the roadmap A5 text both say "one of them `mut`". The PR also rejects a pair of `owned` + read, e.g. `temper def keep(owned a: list[int], b: list[int])` called as `keep(x, x)`, with the message "keeps `a` after it returns".
  - Rust would reject a move plus a borrow just the same, but the PR decides this new rule without asking. It records it only in grammar.md's Deviations list.
  - Under CLAUDE.md's open-decisions rule, Nano should confirm it, or the check should go back to `mut` only.

## Optional improvements
- Four new tests already pass at the base commit:
  - `what_the_call_or_later_code_stores_does_not_count`
  - `read_parameters_and_values_without_lists_may_share`
  - `loose_callees_keep_python_aliasing`
  - `a_conservative_analysis_rejects_no_call`

  That is expected: they are the "still accepted" programs that pair with each rejection, and the first one guards the flow-sensitive behaviour against a slide back to the old analysis. The other four new tests fail at the base commit, as they should.

## Attacks tried that the PR handled
- **Rejected correctly:**
  - a loose callee that stores one argument into another (`put(rows, row)`)
  - storing on only one branch
  - a callee returning its argument, directly, inside a tuple, or as an optional
  - a list stored into another list inside a function
  - a callee that keeps an argument in a global, called inside a function
  - a `try`/`except` handler path
  - a list stored on one loop iteration and passed on a later one
- **Ruled out:**
  - Redefining a function is already an error ("`add` is defined twice"), so there is no ambiguity about which `def` a call means.
  - Nested `def`s don't parse.
  - The new Latest/Earlier split of allocations looked sound: the merge at branch joins only grows, and marking a list as older is consistent along each path.
- **Not applicable:** the change is check-only, so native code generation, the runtime and refcounting are untouched.

## Checks run
| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked` (whole workspace, conformance included) | pass: 414 + 19 + 27 + 29 + 0 tests |
| New `own::` tests on a clean base snapshot with the head's `tests.rs` | 4 fail, 4 pass (as described above) |
| Probes `g1` and `h1` through `check` and `run`, plus CPython | executed; output above |
| CLAUDE.md Rust rules on new code (no `unwrap`/`expect`, no `unsafe`, no new dependencies) | none found |

## Validation gaps
- The only Python here is 3.14.8, so conformance was compared against 3.14, not 3.13. Rerun `cargo test --locked --test conformance` with `PYTHON` set to a 3.13 interpreter.
- I did not see the Windows MSVC, macOS or AArch64 CI runs. This PR has no backend change, so the risk there is low.
- The remind-me memory tools that CLAUDE.md requires were not available in this session, so I neither searched nor saved memory. This review is read-only either way.

**Verdict: changes required.**
