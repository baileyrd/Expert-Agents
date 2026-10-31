<!-- pythrust/47-r1 run 5, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 246 s, 909965 tokens, $0.96 -->

I found two blocking problems in PR 47. In each, a program that passes one list to a `mut` or `owned` parameter of a tempered function and also to a second parameter is accepted, when this PR says it should be rejected. Every probe below was run against the head build, and `run` and CPython print the same aliased output.

**PR:** baileyrd/PythRust #47 · base `5d76260eef62e1a3c7abfd48635a9a3e82a37650` · head `e243a9a79786d8c10af0ce3fe2e4e5e1b9a12a67`. The fetched `pull/47/head` matches the head SHA.

## Blocking findings

### 1. P1: Module-level code misses lists that functions store into module variables
**Where:** `crates/pythrust-compiler/src/own/body.rs:228-231`, with `age`/`assign` at `body.rs:236-273`.

**Why:** In the module's code, a module variable is a local of its own (`Fresh(local)`) and is also copied into a global (`lower/globals.rs:1-9`). Anything a function stores goes to `World`, the analysis's stand-in for the module's values. Nothing links `World` back to those locals. So once a function or a global read puts a list into `rows`, the new per-call check still thinks `rows` holds only what the module's code stored there itself.

**Failure (executed):**
```python
temper def add(mut rows: list[list[int]], owned row: list[int]) -> None:
    rows.append(row)
rows: list[list[int]] = []
row = [1]
def stash():
    rows.append(row)
stash()
add(rows, row)      # check: exit 0; run: [[1], [1]]
```
The same thing happens with two variants:
- a loose `def keep(r: list[int]): rows.append(r)` followed by `keep(row); add(rows, row)`;
- `def get() -> list[list[int]]: return rows` followed by `get().append(row); add(rows, row)`.

Writing `rows.append(row)` directly at module level is rejected, as `what_earlier_code_stores_counts` expects. So the rule depends on whether the append happens inline or inside a function.

**Should:** reject `add(rows, row)` with the A5 message.

**Fix:** when a mirrored local's list is written to a global, make it reach `World` too. For example, add `World` to its points-to set, or treat the contents of `World` as contents of every list stored in `World`. Then a store through `World` is visible from the local. This matches the limitation the PR already documents: module variables count as one list.

**Regression test:** add the three programs above to `what_earlier_code_stores_counts`. All three pass `check` at the head.

### 2. P1: Separate parameters are treated as separate lists, so a loose function can forward one list to both
**Where:** `body.rs:118-123`, where each parameter gets its own `Param(i)`; `calls.rs:24-46`; the test at `own/tests.rs:437`.

**Why:** `shared_args` treats any two parameters, and any parameter and the module's values, as different lists. Inside a loose `g`, the call `push(a, b)` is accepted. But `g` is loose, so its own callers may legally pass it the same list twice. Nothing records that `g` needs `a` and `b` to be different, so nothing checks it at `g`'s call sites.

ownership.md "Decided" 2 says: "A loose call that passes one value to two parameters of a tempered function, one of them `mut`, is a compile error." Here that exact call runs. The docs also claim the known imprecision always errs toward the error (`calls.rs:10-14` and the roadmap A5 entry, `docs/roadmap.md:73`). For parameters, that claim is false.

**Failure (executed):**
```python
temper def push(mut xs: list[int], ys: list[int]) -> None:
    xs.append(len(ys))
def g(a: list[int], b: list[int]) -> None:
    push(a, b)
x = [1]
g(x, x)             # check: exit 0; run and CPython: [1, 1]
```
Two more variants pass `check`:
- **Parameter and module variable:** `rows = [[7]]; def g(row): add(rows, row)`, then `g(rows[0])`. The test at `tests.rs:437` asserts that this kind of program is accepted.
- **Through two levels:** a tempered `g(mut a, b)` calls `push(a, b)`, and a loose `h(p, q)` calls `g(p, q)`, then `h(x, x)`.

**Should:** reject the program. The usual way, as with Swift's exclusivity checks, is to give each function a summary of which parameter pairs, and which parameter–module pairs, must not share a list. Callers then check those pairs, and the error is reported at the outermost call that may share. Rejecting every `push(a, b)` inside loose code would also be sound, but it would refuse ordinary forwarding.

**Regression test:** a rejected `g(x, x)` plus a legal neighbour `g(x, list(x))`. Flip the assertion at `tests.rs:437` into a rejection when the caller may pass a module list.

## Design questions for the owner
- **`owned` also triggers the error.** Decided 2 and the roadmap A5 entry say "one of them `mut`". The PR also rejects `owned` paired with a read-only parameter: `temper def keep(owned xs, ys)` with `keep(a, a)` is a compile error (executed). That rule may be right, but it is a new language rule decided without asking (CLAUDE.md). It is now in grammar.md:180, and Nano should confirm it.
- **The whole-program fallback skips the check.** If any function in the program cannot be lowered, the analysis falls back for the whole program and the A5 check is skipped entirely: `push(a, a)` then runs like CPython (`a_conservative_analysis_rejects_no_call`). This matches A4's precedent and the roadmap says so. The new grammar.md deviation entry, however, says it "is a compile error" without that caveat.
- **Unreachable calls are not checked.** A call after `return` is accepted, because blocks the analysis never reaches are skipped (`body.rs:84-87`, probe `dead.pyr`). Nothing unsafe runs, but other compile errors fire in dead code.

## Optional improvements
- `SharedArgs.callee` holds the function's name, and `calls::check` looks it up by name. That works because redefining a function is rejected ("`add` is defined twice", executed). Carrying the function's ID would remove the dependency on that rule.

## Checks
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: passed.
- `cargo test --locked`: passed (414 + 19 + 27 + 29 + 0; conformance is the 27).
- Conformance compared against **Python 3.14.8**, not 3.13, so the CPython comparison is only approximate.
- New tests run at the base in a scratch worktree, since removed: the four rejection tests fail there, as they should. The tests that only expect acceptance pass at the base, which is fine because they are the legal neighbours.
- CLAUDE.md rules on the new code: no `unwrap`/`expect`, no `unsafe`, no new dependencies.
- I did not save anything to remind-me: its tools weren't available in this session, and this review is read-only.

**Validation gaps:**
- Not run on Windows MSVC, macOS or AArch64, and no memcheck. The PR changes only analysis, not native code.
- No CPython 3.13 run; that needs `PYTHON=python3.13 cargo test --locked --test conformance`.

## Verdict
**Changes required.**
