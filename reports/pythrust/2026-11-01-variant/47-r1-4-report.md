<!-- pythrust/47-r1 run 4, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 214 s, 1006357 tokens, $1.01 -->

# Review of PR 47 (baileyrd/PythRust): A5, rejecting calls that pass one list to two parameters of a tempered function

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head reviewed:** `e243a9a79786d8c10af0ce3fe2e4e5e1b9a12a67` (`git fetch origin pull/47/head` gave the same SHA)
- **Diff:** `own/body.rs`, new `own/calls.rs`, `own/mod.rs`, `own/modes.rs`, `own/tests.rs`, `docs/grammar.md`, `docs/roadmap.md`, and the new corpus program `examples/modes_shared_call.*`.

**Verdict: changes required.** Five probe programs share one list between the two parameters and the check still accepts them. The PR's code and docs say the check "errs toward the error", but these cases go the other way.

## Blocking findings

### 1. P1: A list a called function stores into a module variable is missed by module code
`own/body.rs:228-231` and `body.rs:309-323` (`call_stores`). Module code keeps each module variable as its own local and copies it into a global (`lower/globals.rs:4-8`). The analysis treats that global as one separate list. When a called function stores something in the global, the analysis never connects that back to module code's local copy. *Executed*, both accepted by `check`:

```python
# g2.pyr (same `add` as the corpus program)
rows: list[list[int]] = []
def keep(r: list[int]) -> None:
    rows.append(r)
row = [1]
keep(row)
add(rows, row)        # check: exit 0; run prints [[1], [1]]
```

`g1.pyr` does the same with a function `stash()` that runs `rows.append(row)`, followed by `add(rows, row)`. `run` and CPython both print `[[1, 5], [1, 5]]`, so the list really is shared. When module code does `rows.append(row)` itself, the call is rejected, as the test `what_earlier_code_stores_counts` shows.

- **Fix:** In the module's own code, make each copied local point to `World` as well as its own list, or have everything a call stores into `World` also reach those locals.
- **Regression tests:** g1 and g2 as rejected programs in `own/tests.rs`, with `add(rows, [1])` after `keep(row)` as the accepted neighbour.

### 2. P1: The check assumes two parameters, or a parameter and a module variable, never share a list
`own/body.rs:118-122`. `Param(i)`, `Param(j)` and `World` are separate lists, so `meets` never fires between them. A loose wrapper's callers are never checked either, because `calls::call` returns early for loose callees. *Executed*, accepted:

```python
def g(a: list[list[int]], b: list[int]) -> None:
    add(a, b)
x: list[list[int]] = [[1]]
g(x, x[0])            # check: exit 0; run prints [[1, 2], [1, 2]]
```

`w2.pyr` does the same with a module variable: `def g(row): add(rows, row)` called as `g(rows[0])` is accepted. The test `calls_from_any_caller_are_checked` at `tests.rs:605` assumes this is legal, using `g([1])`. `g(x, [2])` is a correct accepted neighbour.

- **Fix:** Inside a loose function, either:
  - treat two parameters, or a parameter and `World`, as possibly the same list when checking a call to a tempered function; or
  - record "these two must not share a list" in the loose function's summary and check it at its callers.
- Choosing between these is a new rule for the owner to decide (CLAUDE.md, open decisions). Either way, today's silent acceptance contradicts the PR's own rule.
- **Regression test:** w1 and w2 should be rejected; w3 should stay accepted.

### 3. P1: Lists built with `+` or `*` hide the lists inside them
`own/body.rs:285`. `Rvalue::Concat` and `Rvalue::Repeat` fall through to `_ => Objs::new()`, so the result appears to hold no lists. The flow-sensitive update at `body.rs:266` then also replaces whatever the local held before. *Executed*, all accepted:
- `rows = [row] + []; add(rows, row)`: `run` prints `[[1, 9], [1, 9]]`
- `rows = [row] * 2; add(rows, row)`
- `def mk(r): return [r] + []` followed by `add(mk(row), row)`

The summaries had the same gap at the base commit, but this PR makes it reachable as a wrongly accepted call.

- **Fix:** For list `Concat`/`Repeat`, return a fresh list that stores the contents of the operands, and count both as allocations.
- **Regression test:** these three programs should be rejected.

### 4. P2: Docs and comments say the check only errs toward rejecting
- `own/calls.rs:10-14` says "errs toward the error".
- `docs/roadmap.md:73` says "Known imprecision, both erring toward the error", and marks A5 done.

Findings 1–3 show cases that are wrongly accepted. Once those are fixed, correct both texts, or list the remaining gaps.

### 5. P2: grammar.md now contradicts itself on `owned`
- `docs/grammar.md:209` (D2, unchanged) still says "Only a tempered callee's `mut` makes passing one list twice an error".
- The new deviation at `grammar.md:180` says "`mut` or `owned`".
- The roadmap A5 title also says "`mut`".

*Executed:* `stash(x, x)` with `owned a` and a read-only `b` is rejected, while CPython prints `1`. Make the docs agree once the design question below is settled.

## Design questions for the owner
- **Is `owned` covered by the decision?** `ownership.md:72` (Decided 2) says "one of them `mut`". The PR also rejects an `owned` + read pair where nothing is ever changed (o1 above), and doesn't flag this as a widening. CLAUDE.md says new rules are proposed, not decided silently.
- **Two elements of one list count as the same list.** So `f(rows[0], rows[1])` is rejected, which will hit everyday row and matrix code. It is documented, but it hurts real callers.
- The rejection for a default argument (`push(D)` where `ys = D` by default) says "this call may also pass that list as `ys`", although the caller passed nothing for `ys`. This is a minor wording issue.

## Optional improvements
- `calls::check` looks callees up by source name in the module's `def`s. That is safe today because defining a function twice is rejected. Carrying the callee's index (`FuncId`) would remove the lookup.

## Checks run
| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked` | pass: 414 + 19 + 27 + 29 + 0 tests |
| `cargo test --locked --test conformance` | 27 passed, but the CPython oracle was **3.14.8**; no 3.13 is installed |
| New tests run against the base commit | 4 fail as expected (`passing_one_list_twice…`, `a_list_inside_another_argument…`, `what_earlier_code_stores_counts`, `calls_from_any_caller…`). The other 4 new tests pass at base. They cover programs that should be accepted, which is acceptable, though `what_the_call_or_later_code_stores_does_not_count` only guards against a flow-insensitive version of the check. |

- **Rust rules:** no `unwrap`/`expect`, no `unsafe`, no new dependency in the diff.
- **Ruled out by probe:** aliasing through returned tuples, optionals, a callee storing into an argument, `try`/`except`, loops with `break`, keyword arguments, default arguments, and functions defined twice. Each was handled correctly.

## Validation gaps
- **CPython 3.13:** the comparison ran against 3.14.8. Rerun `PYTHON=python3.13 cargo test --test conformance`.
- **Other platforms:** I did not run Windows, macOS or AArch64. The PR changes no native code, so the risk is low.
- **Memory checks:** I did not run valgrind/memcheck. No runtime or code-generation code changed.
- **Project memory:** CLAUDE.md asks for the `remind_me` memory tools first, but they weren't available in this session, so I checked no earlier decisions there and saved nothing.
