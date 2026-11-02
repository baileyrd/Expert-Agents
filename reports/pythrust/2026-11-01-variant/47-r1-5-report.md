<!-- pythrust/47-r1 run 5, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 234 s, 999473 tokens, $1.04 -->

# PR 47 review: A5 rejects tempered calls that share one list between two parameters

**Verdict: changes required.** The direct rule works, but probes found four kinds of program that should be rejected and are accepted. The docs say the analysis only errs toward rejecting, which those probes show is false.

**Pinned:** baileyrd/PythRust PR 47. Base `5d76260eef62e1a3c7abfd48635a9a3e82a37650`, head `e243a9a79786d8c10af0ce3fe2e4e5e1b9a12a67`. `git fetch origin pull/47/head` returned exactly that head SHA. One commit: "Reject calls sharing a list with a tempered mut/owned parameter (A5)".

**What the PR does:** it adds a flow-sensitive pass over the control-flow graph (`body::shared_args`) that finds calls which may pass one list to two parameters. A new check (`own::calls`) rejects such a call when the callee is tempered and one of the two parameters is written `mut` or `owned`. It also adds the corpus program `modes_shared_call`, a deviation entry in grammar.md, and marks roadmap A5 done.

**What holds** (all *executed* with `pythrust check`):
- **Direct cases are rejected:** `push(a, a)`, `add([x], x)`, a list inside a tuple, a list inside an optional, a callee that returns its argument in a tuple, and a callee that leaves an argument inside another argument.
- **Exception paths:** calls inside an `except` handler are checked, so the CFG does reach handlers.
- **Redefinition is not an escape:** a second `def` of the same name, or a `def` inside a block, is already an error, so a call can't reach the wrong definition by name.
- **Ages of reallocated locals:** the Latest/Earlier split stays sound across joins and loops in the cases I traced.

## Blocking findings

### 1. P1: a list a callee stores in a module variable is not seen by later module-level code
- **Where:** `own/body.rs:228-231` (`SetGlobal` only stores into `World`), `body.rs:283` (`Global` reads give `{World}`), `body.rs:309-323` (`call_stores` stores what a callee keeps only into `World`).
- **Cause:** in module code, a module variable is a local, `Fresh(rows)`, which is mirrored into `World`. When a callee stores into the global (by appending a parameter, or through a returned global), only `World` gains the stored list; `Fresh(rows)` does not. So the module code's later `add(rows, row)` sees two separate lists.
- **Trigger** (*executed*: `check` exits 0; `run` prints `[[1], [1]]`):
  ```python
  temper def add(mut rows: list[list[int]], owned row: list[int]) -> None:
      rows.append(row)
  rows: list[list[int]] = []
  def keep(r: list[int]):
      rows.append(r)
  row = [1]
  keep(row)
  add(rows, row)
  ```
  The same happens with `def get() -> list[list[int]]: return rows` followed by `get().append(row)`. If you write `rows.append(row)` directly instead, the call is rejected (`what_earlier_code_stores_counts`), so the PR's own rule is broken.
- **Should:** reject at `add(rows, row)`.
- **Correction:** treat the lists stored in `World` as `World`. A store into `World` must also reach every list `World` holds, and `contents`/`reachable` of a mirrored local must include `World`.
- **Regression tests:** both programs above, in `own/tests.rs` next to `what_earlier_code_stores_counts`.

### 2. P1: inside a function, a parameter is assumed distinct from the module's lists
- **Where:** `body.rs:118-123` (`Param(i)` is its own object) and `body.rs:182-183` (`meets`). The test `calls_from_any_caller_are_checked` (`tests.rs:438-439`) only asserts the safe neighbour `g([1])`.
- **Trigger** (*executed*: `check` exits 0, `run` prints `[[1], [1]]`):
  ```python
  rows: list[list[int]] = []
  row = [1]
  rows.append(row)
  def g(r: list[int]):
      add(rows, r)
  g(row)
  ```
  `g(rows[0])` with `rows = [[1]]` is accepted the same way. Written directly, `add(rows, rows[0])` is rejected (*executed*).
- **Should:** reject.
- **Correction:** either let `Param(i)` meet `World` unless the analysis proves otherwise, or record in each function's summary the parameter/global pairs that must not alias and check those at its callers.

### 3. P1: a loose function in between hides two parameters that are one list
- **Where:** `body.rs:118-123`. Two parameters `Param(i)` and `Param(j)` are treated as distinct with no proof.
- **Trigger** (*executed*: `check` exits 0, `run` prints `[1, 1]`):
  ```python
  temper def push(mut xs: list[int], ys: list[int]) -> None:
      xs.append(len(ys))
  def g(a: list[int], b: list[int]):
      push(a, b)
  x = [1]
  g(x, x)
  ```
  The `owned` version, `def g(rs, r): add(rs, r)` with `g([x], x)`, is accepted too.
- **Why it matters:** at runtime `push(a, b)` passes one list to a tempered `mut` parameter. ownership.md "Decided" 2 forbids exactly that.
- **Correction needs an owner decision.** Making `a` and `b` possibly-aliased rejects many ordinary loose functions. Carrying a "must be distinct" requirement up to `g`'s callers makes the loose-to-loose call `g(x, x)` an error. Until one is chosen, record this as a known gap.

### 4. P2: the docs say the analysis only errs toward the error, which is false
- **Where:** `docs/roadmap.md:73` ("Known imprecision, both erring toward the error"), `own/calls.rs:10-14`, and the grammar.md:180 deviation ("a call that may pass one list ... is a compile error").
- **Problem:** findings 1 to 3 are cases where it errs the other way. Roadmap A5 is marked `[x]` while the exit criterion, the decided rule, does not hold on those programs.
- **Correction:** fix findings 1–2, and list finding 3 as a recorded gap until the owner decides it.

### 5. P2: the suggested fix does not work for nested lists
- **Where:** `own/calls.rs:69-72`.
- **Trigger** (*executed*): with `temper def f(mut a: list[list[int]], b: list[list[int]])`, `f(rows, rows)` gets the advice "pass a copy (`list(...)`)". Applying it, `f(rows, list(rows))`, is still rejected with the same message.
- **Why:** a shallow copy shares the inner lists, and `mut` covers lists inside (grammar.md:64). So rejecting it again is correct, but the advice is not.
- **Correction:** when the parameter's element type can hold a list, don't offer a shallow `list(...)` copy as the fix. Add a test that applies the fix and checks the result.

## Design questions for the owner
- **`owned` as well as `mut`:** ownership.md "Decided" 2 says "one of them `mut`". The PR also rejects calls where the parameter is only `owned`, for example a callee that keeps `a` without changing anything, called as `store(x, x)`. grammar.md:64's "may *also* keep it" suggests `owned` includes `mut`, but this widening should be confirmed, not decided silently.
- **Finding 3:** which way the loose-function case should go (see above).

## Optional improvements
- The module doc at `body.rs:1-10` still calls the whole file "a flow-insensitive points-to analysis". It now has a flow-sensitive mode as well.
- `calls::check` builds its own map of `def`s by name, as `modes::check` does. The two now have the same shape and could share it.

## Checks
- `cargo fmt --all --check`: pass.
- `cargo clippy --locked --all-targets -- -D warnings`: pass.
- `cargo test --locked`: all pass (414 + 19 + 27 + 29 + 0).
- `cargo test --locked --test conformance`: 27 pass, but the oracle was **Python 3.14.8**, not 3.13. No 3.13 interpreter is available here.
- Builds used a scratch target directory, because the configured `CARGO_TARGET_DIR` is on a read-only file system.
- **New tests at base**, in a scratch copy: four fail there (`passing_one_list_twice…`, `a_list_inside_another_argument…`, `what_earlier_code_stores_counts`, `calls_from_any_caller…`), so they do guard the change. Four pass there; those are the "stays accepted" neighbours, which is expected.
- **Rust rules:** no new `unwrap`/`expect`, no `unsafe`, no new dependency.
- **Native code:** the change is analysis only, and the rejection is shared by `check`, `run` and `build`.

## Validation gaps
- A CPython 3.13 conformance run.
- CI on Windows (MSVC), macOS and AArch64.
- Memcheck: native code generation is untouched, so I did not run it.

The `remind_me` memory tools that CLAUDE.md requires were not available in this session, so nothing was searched or saved there.
