<!-- pythrust/47-r1 run 2, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 211 s, 967891 tokens, $1.01 -->

**PR 47 review: baileyrd/PythRust** (A5: reject a call that passes one list to two parameters of a tempered function)

- Base `5d76260eef62e1a3c7abfd48635a9a3e82a37650`, head `e243a9a79786d8c10af0ce3fe2e4e5e1b9a12a67`. Fetching `pull/47/head` gave the same SHA. The PR is one commit.

**Verdict: changes required.** The new check misses three ways a program can pass one list to a tempered function's `mut` or `owned` parameter twice (P1). The docs also say the check only ever errs toward rejecting, which is false (P2). D2 still says only `mut` triggers this error, but the code now treats `owned` the same way (P2).

## Blocking findings

### 1. P1: when a function stores one module variable's list in another, module code doesn't see it
- **Where:** `own/body.rs:228-231` (`SetGlobal` stores into `World`), `body.rs:309-323` (`call_stores` only models effects through arguments), `body.rs:119-123`.
- **Cause:** in module code, a module variable stays an ordinary local, and only a copy of it goes to the global that functions read (`lower/globals.rs:4-8`). The analysis gives the local its own abstract list, separate from `World`. So anything a callee does to `World` never reaches the module's locals. This hole is in the code the PR adds, and the new check is what makes it reachable.
- **Executed:** `check` and `run` accept both programs below. Both print `[[1], [1]]`, so `add` really does receive `row` while `rows` already holds it. The PR's own test rejects the same aliasing when it's written directly (`rows.append(row)` then `add(rows, row)`).

  ```python
  temper def add(mut rows: list[list[int]], owned row: list[int]) -> None:
      rows.append(row)
  rows: list[list[int]] = []
  row = [1]
  def g():
      rows.append(row)
  g()
  add(rows, row)        # accepted; should be rejected
  ```

  ```python
  # same add
  keep: list[list[int]] = []
  def stash(r):
      keep.append(r)
  row = [1]
  stash(row)
  add(keep, row)        # accepted; should be rejected
  ```
- **Fix:** in the module's function, a local that is copied out to a global must also point to `World`. Otherwise a call must spread `World` into every list stored there.
- **Regression tests:** both programs above, in `own/tests.rs`. Keep a legal neighbour that stays accepted: a module variable that no function reads.

### 2. P1: a parameter is assumed distinct from other parameters and from module variables, without proof
- **Where:** `body.rs:119-123` gives each parameter its own `Param(i)`; `calls.rs` only checks call sites inside a body. `own/tests.rs:437` asserts that one of these cases is accepted.
- **Executed:** `check` and `run` accept both programs. CPython prints the same output, which confirms the aliasing happens at runtime.

  ```python
  def g(rs, r):
      add(rs, r)
  x = [1]
  g([x], x)             # accepted; add([x], x) directly is rejected
  ```

  ```python
  rows: list[list[int]] = [[1]]
  def g(row: list[int]):
      add(rows, row)
  g(rows[0])            # accepted; prints [[1], [1]]
  ```
- **Why it's P1:** ownership.md, Decided 2, says a loose call that passes one value to two parameters of a tempered function, one of them `mut`, is a compile error. Wrapping the call in a loose function gets around it.
- **Fix:** give each function's summary a "these parameters, or a parameter and the module's values, must not share a list" requirement. Check it at every call site of that function, the same way the requirement is checked at calls to tempered functions. How the error is worded at the wrapper's call site is a wording question for the owner. Whether the rule applies is already decided.
- **Regression tests:** both programs above. Replace the assertion at `own/tests.rs:437` with `g([1])` accepted and `g(rows[0])` rejected.

### 3. P2: two doc claims are false
- `own/calls.rs:13-15` and `docs/roadmap.md:73` say the known imprecision is "both erring toward the error". Findings 1 and 2 show it also accepts programs it should reject.
- `roadmap.md:73` marks A5 `[x]` (done) while those programs pass the check.
- **Fix:** correct both texts once 1 and 2 are fixed, or list the remaining gaps there.

### 4. P2: D2 still says only `mut` triggers this error, and the PR changed that without asking
- `docs/grammar.md:209` (D2) still says "Only a tempered callee's `mut` makes passing one list twice an error". ownership.md Decided 2 also says "one of them `mut`".
- `calls.rs:372-376` also rejects when the stronger parameter is `owned`, for example `keep(owned a, b)` called as `keep(xs, xs)`. The new deviation entry at `grammar.md:180` says "`mut` or `owned`", so the two parts of grammar.md now disagree.
- Treating `owned` as covering `mut` is defensible, since ownership modes are ordered read < mut < owned. But CLAUDE.md asks for a new rule like this to be proposed, not decided silently. Either the owner confirms it and D2 and Decided 2 are updated, or the code goes back to `mut` only.

## Design questions
- **Is `owned` in scope for A5?** This is the question behind finding 4.
- **What does a loose wrapper report?** Under finding 2, a loose wrapper that forwards to a tempered function would be rejected at its own call sites. That widens the "narrow exception" in ownership.md line 54 to loose-to-loose calls, so the owner should decide how that error reads.

## Optional improvements
- **The fix-it doesn't say which argument to copy.** "pass a copy (`list(...)`)" is ambiguous. For `push(mut xs: list[list[int]], ys: list[int])` called as `push(rows, rows[0])`, copying `rows` doesn't help, because the copy still holds `rows[0]`. Naming the argument to copy would avoid that.
- **No test fails if the analysis stops tracking program order.** Nothing pins `sensitive: true` in `shared_args`. A test that runs the module-level `what_the_call_or_later_code_stores_does_not_count` case with that analysis turned off and expects a rejection would guard it.

## Probes ruled out (all executed)
- A call that raises before its result is assigned: the handler path is still rejected.
- Two definitions of the same function: rejected with "defined twice".
- Nested `def`: a parse error.
- Keyword arguments in the other order: rejected.
- A default argument that is a module variable: rejected.
- Loops: the analysis is monotone over a finite set of states, so it terminates.

## Checks
- **`cargo fmt --all --check`:** passed.
- **`cargo clippy --locked --all-targets -- -D warnings`:** passed.
- **`cargo test --locked`:** passed (414 + 19 + 27 + 29 tests). I had to set `CARGO_TARGET_DIR` to a scratch directory, because the configured target directory is read-only in the sandbox.
- **New tests at the base SHA:** the four rejection tests fail there, and the acceptance tests pass there, as their role as legal neighbours expects.
- **Rust rules on new code:** no `unwrap`/`expect`, no `unsafe`, no new dependencies.

## Validation gaps
- **CPython version:** the machine has `python3` 3.14.8, not 3.13, so the conformance comparison and my CPython probes used 3.14.
- **Native code:** this PR doesn't change native code generation. I didn't run memcheck, macOS, Windows or AArch64.
- **Session memory:** I couldn't search or save to the remind-me memory CLAUDE.md requires, because those tools aren't available in this session.
