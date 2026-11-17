<!-- pythrust/47-r1 run 3, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 279 s, 1178570 tokens, $1.21 -->

# Review of PR 47 (baileyrd/PythRust): changes required

The PR adds the A5 check: a call that passes one list to two parameters of a tempered function, one of them `mut` or `owned`, is a compile error. The check works for lists that stay inside one function's own variables. It misses lists shared through module variables, and lists shared through a loose function's own parameters. In both cases it accepts programs the decided rule forbids. The PR's docs say every known imprecision "errs toward the error", and these probes show that is false.

- **Repository:** baileyrd/PythRust, PR #47
- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head reviewed:** `e243a9a79786d8c10af0ce3fe2e4e5e1b9a12a67` (fetched `pull/47/head`, and it matches)

## Blocking findings

### 1. P1: a list a callee stores through a module variable goes unseen in module-level code (executed)
**Where:** `own/body.rs:228-230` and `own/body.rs:309-323`.

Module variables are local variables of the module's code, and each assignment is copied into a global (`lower/globals.rs`). The analysis models all module-level values as one abstract list, "World". When a callee adds a list to World, the module code's own local for that variable (here `G`) never learns about it.

```python
temper def add(mut rows: list[list[int]], owned row: list[int]) -> None:
    rows.append(row)

def keep(row):
    G.append(row)

G: list[list[int]] = []
r = [1]
keep(r)
add(G, r)      # G already holds r
```

- **What happens:** `check` exits 0, and `run` prints `[[1], [1]]` with one list held twice.
- **What should happen:** the same error as `G.append(r); add(G, r)`, which is correctly rejected (probe `g0`).
- **Variants also accepted:**
  - `def link(): G.append(H)` then `link(); add(G, H)`. The callee has no arguments, and no summary bit records that it writes into a module variable.
  - `def get(): return G` then `get().append(r); add(G, r)`.
- **Fix:** make a store into World reach every list stored in World. Add a summary bit for "may store into the module's values", so argument-free callees like `link()` count. The simpler alternative is to treat mirrored module variables as World in module code too. That is the same imprecision functions already accept.
- **Regression tests:** these three programs as rejected cases in `own/tests.rs`.

### 2. P1: two parameters of a loose function are assumed to be different lists (executed)
**Where:** `own/body.rs:121`, which gives each parameter its own distinct `Obj::Param(i)`, and `own/body.rs:173`.

```python
temper def push(mut xs: list[int], ys: list[int]) -> None:
    xs.append(len(ys))

def g(a, b):
    push(a, b)

x = [1]
g(x, x)       # push receives x twice
```

- **What happens:** `check` exits 0, and `run` prints `[1, 1]`.
- **Same pattern with a parameter and a module variable** (probe `p2`): `def g(b): push(G, b)` then `g(G)` is also accepted.
- **Locked in by a test:** `calls_from_any_caller_are_checked` (`own/tests.rs:437`) asserts this pattern is accepted. ownership.md Decided 2 says "a loose call that passes one value to two parameters of a tempered function, one of them `mut`, is a compile error". The call `push(a, b)` does exactly that whenever `g`'s arguments share a list.
- **Fix:** record in `g`'s summary a requirement that parameters 0 and 1 must not share a list, and check it at `g`'s callers, transitively. The alternative is to reject inside `g`, which would break the `param` test's neighbour case.
- **Owner question:** which call site gets the error.
- **Regression tests:** `p1` and `p2` as rejected programs.

Tempered callers are safe here: A4 forces them to write `mut`, so their own callers are checked.

### 3. P2: the fix-it can point at the wrong argument (executed)
**Where:** `own/calls.rs:69-71`.

The message names the stronger parameter but only says "pass a copy (`list(...)`)", without saying which argument to copy.

- **Program:** `temper def f(mut rows: list[list[int]], r: list[int])`, called as `f(rows, rows[0])`.
- **Message:** "changes `rows` in place … pass a copy".
- **Applying it to `rows`:** `f(list(rows), rows[0])` is still rejected, because a shallow copy shares its elements.
- **What works:** `f(rows, list(rows[0]))`.
- **Fix:** name the argument to copy, which is the inner one when the sharing is nested.
- **Regression test:** apply the fix-it and assert `check` passes, as `passing_one_list_twice…` already does for the simple case.

### 4. P2: docs and the roadmap disagree with the code
- `docs/grammar.md:209` (D2) still says "Only a tempered callee's `mut` makes passing one list twice an error". The new deviation entry at `grammar.md:180` and the code use "`mut` or `owned`".
- `docs/roadmap.md:73` marks A5 done and says both known imprecisions "err toward the error". The same claim is in the commit body and the `calls.rs` module doc. Findings 1 and 2 show the analysis also errs toward acceptance.
- `own/body.rs:1-10` still describes the module as "a flow-insensitive points-to analysis". It now also runs a flow-sensitive pass.

## Design questions for the owner
- **`owned` counts as well as `mut`.** ownership.md Decided 2 and the A5 roadmap item say only `mut`. The lattice (grammar.md:64: `owned` "may also keep", so `owned` includes `mut`) arguably implies it. Still, the PR widens a recorded rule without asking, so Nano should confirm it. A non-mutating `owned a` plus a `read b` on the same list is now rejected.
- **Where the error goes for finding 2:** at the outer loose call `g(x, x)`, or inside `g`. Erroring inside `g` would make every loose function that forwards two parameters to a tempered `mut` rejectable.

## Optional improvements
- "may also pass that list as `r`" is slightly off when `r` is a list *inside* the `mut` argument. Something like "a list inside it" would be more accurate.
- `shared_args` gathers results for every function, even when the program has no tempered callee with `mut`/`owned`. It could skip the pass in that case.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked` | pass (414 + 19 + 27 + 29 + 0) |
| `cargo test --locked --test conformance` | pass (27), but the oracle was **Python 3.14.8**, not 3.13 |
| New tests run on the base code | 4 rejection tests fail there, as they should: `passing_one_list_twice…`, `a_list_inside_another_argument_is_shared`, `what_earlier_code_stores_counts`, `calls_from_any_caller_are_checked`. The accept cases pass at base, which is fine for neighbour tests. |
| Rust rules in CLAUDE.md | No `unwrap`/`expect`, no `unsafe`, no new dependencies in new code. |

**Attacks that were correctly rejected:** a list stored by an earlier call, by an argument a callee keeps, or by a returned list; `try`/`except`; `while` loops with branches; narrowed optionals; a nested list in a tuple.

**Attacks ruled out:** redefining a function, which is rejected as "defined twice"; a `def` that is not at the top level, which is a parse error.

**Validation gaps:**
- CPython 3.13 parity was not run; only 3.14 is installed.
- No native or runtime code changed, so valgrind, Windows, macOS and AArch64 are not affected, but I didn't run them.
- The remind-me memory tools that CLAUDE.md requires weren't available in this session, so I didn't search or save prior decisions.

**One process note:** my first attempt to set up a base copy collided with an existing scratch folder, `$TMPDIR/base`, dated Oct 27, and I overwrote its `own/tests.rs`. Every other file I compared matched base commit `5d76260e`, so I restored that file from the same commit. If that folder belonged to some other work with a different `tests.rs`, it's worth checking. I redid the base run in a folder of my own and then deleted it. My probe programs are in `$TMPDIR/p`.

## Verdict: **changes required**
