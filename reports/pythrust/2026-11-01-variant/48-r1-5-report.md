<!-- pythrust/48-r1 run 5, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 347 s, 1608354 tokens, $1.55 -->

# Review of PR 48 (baileyrd/PythRust): A6, aliasing checks in tempered bodies

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head reviewed:** `4771160b650e6a50edeaf6bc48d244050a69f057`. I fetched `pull/48/head` and it matched this SHA, so the review is pinned.
- **Commit:** one, "Check aliasing in tempered bodies (A6)". It adds `own/alias.rs`, gives `Func.names` and `Inst::Append.span` to the IR, adds three corpus programs, and marks A6 done in the roadmap.

**Verdict: changes required.** Five ways to get a program accepted that D3 or D4 forbids, all confirmed by running them, plus four smaller defects.

## What had to hold (step 1)
- **D4** (grammar.md:211): in a tempered body, changing a list through one name while another name for the same list is used later is an error. So is changing a list a `for` loop is running over.
- **D3** (grammar.md:210): a tempered function returns no parameter, and nothing that shares a list with one.
- **The A6 exit test** (roadmap.md:74): every tempered body lowers, and any lowering gap a tempered body can reach gets closed.
- **Two values are treated as different lists only once that is proven.** Swift's exclusivity and Rust NLL both get this from the type and borrow rules. Here it rests on the abstract objects `Param(i)`, `World` and `Fresh(l)`, plus paths into them.

## Blocking findings

### 1. P1: one lowering gap anywhere in the program switches D3 and D4 off for every tempered function, and the PR's "no further gap" claim is false
- **Where:** `own/mod.rs:107` (`aliasing: BTreeMap::new()` on fallback) and `roadmap.md:74`.
- **Trigger:** if any function fails to lower, `analyze` falls back to the conservative summary for the whole program, and the aliasing map is left empty.
- **Executed:** `extend` is copied from `aliasing_shared`. `check` exits 0, prints no error, and runs in each of these cases:
  - a loose `def boom() -> None: raise ValueError([1])` is added next to it;
  - `x = print(7)` followed by `def print(n)` is added (with `same` copied from `aliasing_return`);
  - a tempered `f` uses `return xs + [1]` (`concat_alone.pyr`).
- **List `+` and `+=` are themselves a gap a tempered body can reach.** Lowering reports "arithmetic on a non-number" for them. So these are also accepted:
  - `b = a; b += [2]; return len(a)`, a D4 break (`run` prints 2: `+=` changes the list in place);
  - `ys = xs; ys += [1]; return ys`, a D3 break that returns the caller's list.
- **The doc claim is false.** roadmap.md:74 says the probe "found no further lowering gap; the one left is an exception holding a list". List concatenation is a second gap, and in both cases the effect is that the checks are silently skipped, not just that one function falls back.
- **Fix:**
  - close the list `+`/`+=` lowering gap, or refuse a tempered program whose aliasing cannot be checked;
  - correct roadmap.md:74;
  - add `own/tests.rs` tests: a D4 break next to a lowering gap must still fail, and a tempered `b += [2]` with `a` used later must fail.

### 2. P1: a tuple's two fields are assumed to be two different lists
- **Where:** `alias.rs:223` (`Step::Field(k)`) and `same`, `alias.rs:446-453`. Paths `[Field(0)]` and `[Field(1)]` never compare equal unless the tuple was built in this body, which is the only case where `stored` knows what is inside it.
- **Executed** (`tup_call.pyr`):
  ```
  temper def mk() -> tuple[list[int], list[int]]:
      x = [1]
      return (x, x)
  temper def f() -> int:
      t = mk()
      a = t[0]
      b = t[1]
      a.append(2)
      return len(b)
  ```
  `check` and `build` accept it and it prints 2. The unpacking form `a, b = mk()` (`tup_unpack.pyr`) and a parameter `mut t: tuple[...]` called as `f((xs, xs))` (`tup_param_mut.pyr`) are accepted too. The same body with a local `t = (x, x)` is correctly rejected.
- **Fix:** sibling fields of a tuple the body did not build must be treated as maybe the same list, as `Elem` paths already are. The regression test is the `tup_call` program, rejected.

### 3. P1: a parameter is assumed never to be a list the module also names
- **Where:** `Param(i)` and `World` are never compared with each other (`alias.rs:178`, `:225`, `overlaps` at `:424-433`). `returned` (`:402-410`) checks only `region(Param(i))`.
- **Executed** (`glob.pyr`):
  ```
  ys = [1]
  temper def f(mut xs: list[int]) -> int:
      g = ys
      xs.append(2)
      return len(g)
  print(f(ys))
  ```
  This is accepted and prints 2, so `g` saw the change. The same body with `g = xs` is rejected.
- A5 will not cover this: its recorded scope is `f(xs, xs)`, one list passed twice. The same blind spot applies to D3 (`return ys` called as `f(ys)`).
- **Fix:** treat `World` as possibly overlapping every parameter whose type holds a list, in both `overlaps` and `returned`, or record a decision that says otherwise. The regression test is `glob.pyr`, rejected.

### 4. P1: a temporary read from two names hides both of them
- **Where:** `roots` (`alias.rs:143-171`) is worked out over the whole function at once, not per program point. `change` (`:376`) then exempts every root of the changed temporary.
- **Executed** (`cond.pyr`):
  ```
  a = [1]
  b = a
  (a if c else b).append(5)
  return len(a) + len(b)
  ```
  This is accepted and prints 4. The plain `a.append(5)` version is rejected.
- **Fix:** exempt only the root the temporary holds at that point, for example by tracking roots per point in the forward pass. Add `cond.pyr` as a rejected test.

### 5. P2: misleading "for loop" diagnostic
- **Where:** `alias.rs:374-381`. Any unnamed live temporary that clashes gets the `for` loop message.
- **Executed** (`tempmsg.pyr`): `r = rows[0]; return h(rows[0], g(r))` with `g(mut xs)` reports "changes a list while a `for` loop over it is running". The body has no loop.
- **Fix:** give the loop message only for a loop's sequence temporary; mark those in lowering or the IR. Add a test asserting the message for this program.

### 6. P2: the fix-it can point at the wrong name
- **Where:** `alias.rs:388-398`. When the change goes through the original name (`a = [1]; b = a; a.append(5); …len(b)`), the suggestion is "`a = list(b)`".
- **Executed:** writing `a = list(b)` in place of `a = [1]` still fails, now reading `b` before it exists. The real fix, `b = list(a)` at the line that made the alias, is accepted (`fix_a2.pyr`).
- **Fix:** point the fix-it at the binding that created the second name. Add a test that applies the fix-it and re-checks.

### 7. P2: the module doc names a module that doesn't exist
- **Where:** `alias.rs:15`: "A5's check (`own::calls`)".
- `own/` has no `calls` module, and roadmap.md:73 shows A5 is still open.

### 8. P2: coverage gaps
- **Aging:** the only test of the "latest vs earlier" allocation logic, `each_run_of_an_allocation_is_a_new_list`, also passes at base and only shows a legal program is accepted. There is no rejected counterpart, such as `keep = row` inside the loop followed by `row.append(...)` with `keep` used later.
- **Findings 2 to 4** have no tests. Their programs above are the tests to add to `own/tests.rs`.

## Design question for the owner
**Passing a list and a list inside it.** In `f(rows, rows[0])` with a tempered `f(rows, mut r)`, the body is accepted while `x = rows[0]` and `r` are the same list (`nested.pyr`; it prints 2). The PR leaves "passing one list twice" to A5, but A5's recorded scope is literally `f(xs, xs)`. Should A5 cover arguments that overlap without being the same list, or should D4 assume parameters may overlap? This needs a decision, not a guess.

## Optional improvements
- `check` returns the first error in block order, not source order. That only matters if more than one error per function is ever shown.
- Fix-its such as `` `r = list(...)` `` could name the expression (`list(rows[0])`).

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked` (whole workspace, conformance included) | pass: 416 + 19 + 27 + 29 + 0 tests, 0 failed |
| New `own/tests.rs` run on base | 7 of 10 fail there (they guard); the 3 that pass are accept-only tests |
| `aliasing` built natively and run | output matches CPython and the `.out` golden |
| Rust rules on new code | no `unwrap`/`expect` or `unsafe`, no new dependency |

`CARGO_TARGET_DIR` pointed at a read-only path, so I built into `$TMPDIR`. Probes ran in `$TMPDIR/rv48.7et0`. One mistake on my part: I briefly added a git worktree for the base, which wrote to `.git`. I removed and pruned it straight away, and the working tree has no changes to tracked files.

## Validation gaps
- **CPython 3.13:** only 3.14.8 is installed, so conformance and my comparisons used 3.14. Settle with `PYTHON=python3.13 cargo test --locked --test conformance`.
- **Memcheck:** valgrind isn't installed. The native change is limited to the `Append` span field, but `aliasing` should still be run under memcheck.
- **Other CI targets:** Windows MSVC, macOS and AArch64 were not run.
- **Project memory:** the remind-me tools CLAUDE.md asks for are not available here, so nothing was searched or saved.

**Verdict: changes required.**
