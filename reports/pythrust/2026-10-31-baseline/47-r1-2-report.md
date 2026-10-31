<!-- pythrust/47-r1 run 2, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 196 s, 515115 tokens, $0.70 -->

I found three ways to get past the new check, so this PR needs changes. In each, a tempered function receives one list in both `rows` and `row`, the program is accepted, and it runs.

**Repository:** baileyrd/PythRust, PR #47
**Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
**Head:** `e243a9a79786d8c10af0ce3fe2e4e5e1b9a12a67` (the fetched `pull/47/head` matches)

## Blocking findings

**1. P1: Two parameters of a loose function are assumed to hold different lists, so a loose wrapper hides the shared argument.**
`own/body.rs:118-123` gives each parameter its own abstract list (`Obj::Param(i)`). `shared()` (`body.rs:173-191`) therefore never sees `Param(0)` and `Param(1)` as overlapping. The call into the loose wrapper is never checked, because loose callees are skipped (`calls.rs:51`). So the decided rule (ownership.md "Decided" 2) can be bypassed with one extra function. *Executed*:
```python
temper def push(mut xs: list[int], ys: list[int]) -> None:
    xs.append(len(ys))
def g(a, b):
    push(a, b)
x = [1]
g(x, x)
print(x)
```
`check` exits 0 and `run` prints `[1, 1]`, so `push` got `x` as both `xs` and `ys`. The direct call `push(a, a)` is rejected. `g([x], x)` with `add` gets through the same way.
- **Fix:** either treat two parameters (and a parameter and a module variable) as possibly the same list when checking calls in a body, or give the wrapper a "these must be different lists" requirement and check its callers against it. Which one is a design choice for Nano (see Design questions).
- **Regression test:** the program above, in `own/tests.rs`, expecting the error.

**2. P1: A module variable read inside a function is assumed to differ from that function's parameter, even in tempered code.**
Inside a function, a module variable is `Obj::World` and a parameter is `Param(i)`, and the two never meet. The test `calls_from_any_caller_are_checked` (`tests.rs`, the `param` case) builds this assumption in. A one-argument call has no pair of arguments to compare, so nothing catches it. *Executed*:
```python
temper def add(mut rows: list[list[int]], owned row: list[int]) -> None:
    rows.append(row)
holder: list[list[int]] = [[1]]
temper def h(owned row: list[int]) -> None:
    add(holder, row)
h(holder[0])
print(holder)
```
`check` exits 0. `run` and CPython 3.14 both print `[[1], [1]]`, so `add` got `holder` and a list inside it. The loose form behaves the same (`a = [1]; def g(ys): push(a, ys); g(a)`).
- **Fix:** treat `World` and `Param(i)` as possibly overlapping when checking calls, or carry the requirement up to callers as in finding 1.
- **Regression test:** the program above, expecting the error.

**3. P1: In module code, a callee that stores into a module variable is not seen by the module's local copy of that variable.**
A module variable that functions read is a local of the module code, mirrored into a global (`lower/globals.rs:4-8`). A callee's stores are recorded only into `World` (`body.rs:309-323`, `call_stores`), not into the module's local copy (`Fresh(holder)`). Anything stored in `World` is unreachable from that local, so the next call misses the overlap. *Executed*:
```python
temper def add(mut rows: list[list[int]], owned row: list[int]) -> None:
    rows.append(row)
holder: list[list[int]] = []
def stash(x: list[int]):
    holder.append(x)
row = [1]
stash(row)
add(holder, row)
print(holder)
```
`check` exits 0 and `run` prints `[[1], [1]]`. Writing `holder.append(row)` directly in module code is rejected, and so is the same call from inside a function (it reads `holder` as `World`).
- **Fix:** in module code, treat a mirrored local as also reaching `World` (or its contents), so whatever a callee leaves in `World` is reachable from it.
- **Regression test:** the program above, expecting the error.

**4. P2: The grammar doc now contradicts itself on which mode triggers the error.**
- `docs/grammar.md:209` (D2) still says "Only a tempered callee's `mut` makes passing one list twice an error".
- The new deviation entry (`grammar.md:180`) and the code (`calls.rs:64-69`) count `owned` as well.
- So does a pair of one `read` and one `owned` parameter. The decided text (ownership.md "Decided" 2) says "one of them `mut`".
- **Fix:** bring D2 into line once the owner rules on this (Design question 2).
- **Regression test:** a `read` + `owned` pair test, pinned to whichever answer is chosen.

## Design questions (for the owner, not findings)
1. **How should a wrapper's parameters be treated?** The fix for findings 1 and 2 means choosing between two options. One rejects more loose wrappers inside the body (stricter, but it puts a restriction on loose code). The other adds requirements to function summaries that callers are checked against (more work, and loose callers would then be constrained by a tempered function further down). CLAUDE.md says a new language rule must not be decided silently, so this needs Nano's call.
2. **Should `owned` count?** The PR widens the decided "one of them `mut`" to "`mut` or `owned`" without asking. It is defensible, since a kept list can later be mutated through the other name, but it is a rule change that needs approval.

## Optional improvements
- `roadmap.md:73` says the known imprecisions "both err toward the error". Findings 1 to 3 are cases where they err the other way, so the sentence should change along with the fix.
- `shared_args` (`body.rs:84-94`) runs the instructions of each reachable block a second time after the fixpoint. That is correct, but a short comment saying the per-call check reads the state before the call would help.

## Checks performed
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: passed.
- `cargo test --locked`: passed (414 + 19 + 27 + 29 + 0, no failures). This includes the conformance suite, but `python3` here is **3.14.8**, not 3.13.
- New tests at the base: they call `own::check` and expect the new A5 errors, which the base does not emit, so they would fail there. The corpus fixture `modes_shared_call` runs successfully at the base, so its `expect-error` would fail. I reasoned this from the code and did not run them at the base.
- Probes run through `check` and `run` in a scratch directory:
  - Findings 1 to 3 are accepted (shown above).
  - Correctly rejected: the direct `push(a, a)`, a returned wrapper (`rows = wrap(row)`), a store on one side of a branch, and a module variable read through a function.
  - Defining a function twice is already refused (`defined twice`), so that route is closed.
- Rust rules in CLAUDE.md, on the new code: no `unwrap`/`expect` outside tests, no `unsafe`, no new dependencies.
- The PR is analysis only (no IR or code generation changes), so it adds no native-parity or memory-safety risk.

## Validation gaps
- **CPython 3.13 comparison:** conformance ran against Python 3.14.8. Settle it with `PYTHON=python3.13 cargo test --locked --test conformance`.
- **Native build:** my `pythrust build` call used the wrong arguments (it takes only `<file>`), so I did not build or run the probes natively. The PR changes no code generation, but a `build` and memcheck run of `modes_shared_call` and the probes would close this.
- **Other platforms:** Windows MSVC, macOS and AArch64 CI were not observed.
- **remind-me:** CLAUDE.md asks for `remind_me_search`, but those tools are not available in this session, so I could not check stored prior decisions.

## Verdict: **changes required**
