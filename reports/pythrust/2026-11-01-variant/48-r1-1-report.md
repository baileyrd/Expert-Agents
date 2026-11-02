<!-- pythrust/48-r1 run 1, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 294 s, 1092716 tokens, $1.33 -->

# Review of PR 48 (baileyrd/PythRust): A6, aliasing checks in tempered bodies

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head reviewed:** `4771160b650e6a50edeaf6bc48d244050a69f057`. I fetched `pull/48/head` and it matches this SHA. The PR is one commit.

**Verdict: changes required.** I traced five soundness holes, each with a probe program I ran. In every case, a tempered program that D3 or D4 forbids is accepted.

## Blocking findings

All probes are `.pyr` files run with the head build. "Accepted" means `pythrust check` printed no error.

### 1. P1: a list changed through a conditional expression skips the D4 check
- **Where:** `own/alias.rs:157-168` and `:376`.
- **Cause:** `roots` is built from every assignment to a temporary, ignoring control flow. For `a if c else b`, the result temporary gets both `a` and `b` as its source names. `change` then treats every source name as "the name being changed", so neither `a` nor `b` can clash.
- **Probe (executed):**
  ```python
  temper def f(c: bool) -> int:
      a = [1]
      b = a
      (a if c else b).append(2)
      return len(a) + len(b)
  ```
  This is accepted, and `run` prints 4. The control case `(b if c else b).append(2)` is rejected. The same hole goes through a `mut` argument: `grow(a if c else b)` is also accepted.
- **Fix:** skip a source name only when it is the single source and it is the very list being changed. Any other source name that is still used later must still clash.
- **Test that should fail without the fix:** both programs above in `own/tests.rs`, plus `(b if c else b)` as the neighbour that is legitimately rejected.

### 2. P1: two fields of one tuple parameter are assumed to be different lists
- **Where:** `alias.rs:223` and `same()` at `:442-454`.
- **Cause:** `Param(i)` at path `[Field(0)]` and at path `[Field(1)]` are treated as distinct lists. Nothing proves that. A caller can pass `(xs, xs)`, and A5 (not implemented yet, and in any case scoped to `f(xs, xs)`) would not cover it. List elements are handled correctly: `rows[0]` and `rows[1]` share a path, so they clash.
- **Probe (executed):**
  ```python
  temper def f(mut t: tuple[list[int], list[int]]) -> int:
      a = t[0]
      b = t[1]
      a.append(2)
      return len(b)
  xs = [1]
  print(f((xs, xs)), xs)
  ```
  This is accepted, and `run` prints `2 [1, 2]`: the change made through `a` is seen through `b`. `t[1].append(2)` while `a = t[0]` is used later is accepted the same way.
- **Fix:** inside a parameter, treat different fields that have the same type as possibly the same list. A body's own tuple display is already handled this way, because its stored contents are recorded as `Anywhere`.
- **Test:** this program, plus a neighbour whose fields have different types and stays accepted.

### 3. P1: a module global is not a name for D4
- **Where:** `alias.rs:225`. `Rvalue::Global` becomes `root(World)`, and each read of a global lands in a fresh temporary, so the global never counts as a live name. The `Summary` (`own/mod.rs`) also has no field for "changes a global".
- **Probes (executed):**
  - `G = [1]`, then in a tempered body `a = G; a.append(2); return len(G)`. Accepted, prints 2. This is D4's own example (`b = a`) with `a` replaced by a global.
  - A tempered `bump()` that does `G.append(1)`, called as `a = G; n = len(a); bump(); return len(a) + n`. Accepted.
- **Fix:** read the decision as written (D4: "two live names for one list with a mutation through either") and either count globals as names and record global changes in the summary, or ask the owner to record that globals are exempt.
- **Test:** both probes.

### 4. P1: an unrelated lowering gap anywhere switches off D3/D4 for every tempered function
- **Where:** `own/mod.rs:105-109`. On `Err(gap)` the `aliasing` map is left empty.
- **Probes (executed):**
  - `gapoff.pyr`: a tempered `f` with a `b = a; b.append(2); len(a)` violation, plus an unrelated loose `g` that does `raise ValueError(xs)`. Accepted.
  - `gapoff2.pyr`: the same `f`, plus the known gap `x = print(7)` followed by `def print`. Accepted.
- **Why it matters:** `f` itself lowers fine. A6's exit criterion is that every tempered body lowers and is checked. The roadmap now marks A6 done but only mentions the fallback, not that it skips checks on unrelated tempered code.
- **Fix:** lower and check tempered bodies per function, or reject when a tempered function cannot be checked.
- **Test:** `gapoff.pyr` as a corpus `expect-error`.

### 5. P2: two different globals are reported as the same list
- **Where:** `alias.rs:225`. Every global maps to the same `root(World)`.
- **Probe (executed):** `G = [1]`, `H = [2]`, then `a = G; b = H; a.append(2); return len(b)`. This is rejected with "changes `a` while `b` may be the same list". The rule allows this program; the analysis just cannot tell the lists apart, so it becomes a new restriction with a false message.
- **Fix:** one abstract list per global, or no D4 error between two global roots.
- **Test:** this program asserted `Ok(())`.

### 6. P2: the "`for` loop" message fires where there is no loop
- **Where:** `alias.rs:374-382`. Any temporary that is still used and clashes is treated as a loop's sequence.
- **Probe (executed):** `return h(rows[0], grow(rows))` with `grow(mut rows)`. The error says "changes a list while a `for` loop over it is running; loop over a copy".
- **Docs:** this also contradicts the roadmap's A6 text ("counts through the variable it was read from, except a `for` loop's sequence").
- **Fix:** mark the actual loop-sequence temporaries, and give a separate message for argument temporaries.
- **Test:** this program with an exact-message assertion.

### 7. P2: the D3 fix-it `return list(xs)` is itself rejected for nested lists
- **Where:** `alias.rs:412-416`.
- **Probe (executed):** `temper def f(rows: list[list[int]]) -> list[list[int]]: return rows` suggests `return list(rows)`. After applying that, the error becomes "returns a value sharing a list with its parameter `rows`; return a copy made with `list(...)`", which is circular advice.
- **Fix:** for nested element types, suggest a deep copy (e.g. a loop of `list(r)`), or treat a shallow copy as legal. That choice is the design question below.
- **Test:** apply the fix-it and re-run `check`.

### 8. P2: the fix-it for a narrowed optional names the wrong variable
- **Probe (executed):** `a = [1]`, `o: list[int] | None = a`, then `o.append(2)` inside `if o is not None:` with `len(a)` used later. The error says "changes a list while `a` … `a = list(...)`". The narrowed `o` has lost its name, and the fix that works is `o = list(a)`.
- **Fix:** carry the source name through `Unwrap` into the message.

### 9. P2: doc points to a module that does not exist
`alias.rs:15` says "A5's check (`own::calls`)". There is no `own::calls` module.

### 10. P2: missing coverage
None of findings 1–5 has a test. Those tests belong in `own/tests.rs` (A6 section) and as corpus `expect-error` fixtures.

## Design questions for the owner
These are rules the PR decides without asking (CLAUDE.md: "do not decide silently; propose and ask"):
- **Changing a list during a `for` loop is a new error.** D4 is about two names; a loop holds no name, and CPython allows it.
- **"A list inside another is its own list".** The PR allows `r = rows[0]; r.append(1)` while `rows` is still used.
- **Shallow copies count for D3.** The PR treats a value that holds a parameter's inner list as an alias, which is what defeats the decided `return list(xs)` fix-it for nested lists.
- **Parameter vs. parameter, and parameter vs. global.** Inside the callee, `f(mut a, b)` called as `f(x, x)`, and a `mut` parameter that is the same list as a global, are assumed distinct. A5 is still open, and its stated scope is `f(xs, xs)` only.

## Optional improvements
- `Body::change` recomputes `flow.reachable` for every changed location; this could be cached.
- The roadmap could note that the D2 and D4 diagnostics share the whole-program fallback.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all --check` | Pass |
| `cargo clippy --locked --all-targets -- -D warnings` | Pass |
| `cargo test --locked`, conformance included | Pass: 416 / 19 / 27 / 29 / 0 across the suites |
| New tests run against base (base tree plus head's `own/tests.rs`) | 7 of 10 fail, as they should. 3 pass at base: `each_run_of_an_allocation_is_a_new_list`, `a_narrowed_optional_is_the_same_name` and `loose_code_keeps_python_aliasing`, which are accepted-program neighbours |
| CLAUDE.md Rust rules on new code | No `unwrap`/`expect`, no `unsafe`, no new dependencies |

Two notes on how these ran:
- The configured `CARGO_TARGET_DIR` is read-only, so everything built into `$TMPDIR/target`.
- The shared `$TMPDIR/p` directory already held other people's probes, so all results above come from a fresh `mktemp` directory.

## Validation gaps
- **CPython version:** `python3` here is 3.14.8, not 3.13, so the conformance comparison ran against 3.14.
- **Memory checking:** valgrind is not installed, so there was no memcheck run on `aliasing`. The codegen change is only a pattern update for `Append`'s new `span` field.
- **Other targets:** I did not run macOS, Windows MSVC or AArch64.
- **remind-me:** the remind-me tools CLAUDE.md asks for were not available, and this review is read-only, so I neither searched nor saved anything there.

**Verdict: changes required.**
