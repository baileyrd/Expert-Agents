<!-- pythrust/48-r2 run 2, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 337 s, 1830535 tokens, $1.49 -->

# PR 48 review (baileyrd/PythRust): A6 aliasing checks in tempered bodies

The PR fixes both prior findings as reported, but the same two kinds of hole are still open by other routes. I traced seven defects, so the verdict is **changes required**.

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head:** `2a09a304c45dcdd8f090a1751b81eca4944fe229`. I fetched it with `git fetch origin pull/48/head` and confirmed it matches.
- **Commits:** 4771160 (A6) and 2a09a30 (the fix for K1 and K2).

## Prior findings at this head

- **K1 (tuple slots assumed to hold different lists): fixed for slots at the same depth.** Fields of a tuple the body did not build now count as possibly the same list. The test `slots_of_a_tuple_from_elsewhere_may_be_one_list` fails at 4771160, so it guards the fix. The same mistake remains at different depths of one value (finding 1).
- **K2 (an exception holding a list turned off aliasing checks for the whole program): fixed for exceptions.** `ValueError(xs)` now lowers, and the `own` stage treats it as storing the list in a global. `an_exception_holding_a_list_leaves_aliasing_checked` and `…_keeps_it` both fail at 4771160. But any other lowering gap, even in unrelated loose code, still turns off D3 and D4 for the whole program (finding 3).

## Blocking findings

All probe outputs below are *executed*: `pythrust check` at the head, and CPython running the program with `temper`, `mut` and `owned` removed.

### 1. P1: Lists at different depths of one value are assumed to be different lists (K1 still open)
- **Where:** `own/alias.rs:28-39` (`At::Depth`) and `same()` at `:471-483`. The comment says "lists at different depths have different types, so they never are". That holds for one list type, but not inside a tuple, or for a call result holding one.
- **Program:**
  ```python
  temper def f(mut t: tuple[list[int], list[list[int]]]) -> int:
      a = t[0]
      b = t[1][0]
      a.append(2)
      return len(b)
  xs = [1]
  print(f((xs, [xs])))
  ```
  `check` accepts it (exit 0); CPython prints `2`, so `a` and `b` are the same list. The same happens when the tuple comes from a tempered call, `mk() -> (xs, [xs])` (`depth_call.pyr`, accepted).
- **Fix:** depth can only tell lists apart when the types along the path differ. Either compare the static type of the list reached, or treat every list-typed slot inside one object as possibly any other slot of the same type.
- **Regression test:** both programs above should be rejected; `pair = ([1], [[2]])` built in the body should stay accepted.

### 2. P1: All globals are one object at depth 0, so a global and an element of another global look distinct
- **Where:** `own/alias.rs:240` gives every global the location `root(Obj::World)` at depth 0.
- **Program:**
  ```python
  G1 = [1]
  G2 = [G1]
  temper def f() -> int:
      a = G1
      b = G2[0]
      a.append(2)
      return len(b)
  print(f())
  ```
  `check` accepts it; CPython prints `2`.
- **Fix:** a global read should start at "anywhere" inside the world, i.e. `anywhere(Obj::World)`.
- **Regression test:** this program should be rejected.

### 3. P1: Any lowering gap anywhere still turns off D3 and D4 for the whole program (K2's class)
- **Where:** `own/mod.rs:105-107`. On any `Err(gap)` the result is `aliasing: BTreeMap::new()`.
- **Program** (`gap_late.pyr`): a tempered `f` doing `b = a; b.append(2); return len(a)`, plus a tempered `same(xs)` doing `return xs`, plus this unrelated loose code:
  ```python
  def g(n: int) -> int:
      if n > 0:
          return h()
      return 0
  x = g(0)
  def h() -> int:
      return 1
  ```
  `check` and `run` both accept it and print `2 [1] 0`. Without the loose code, `check` gives the D4 error.
- **Docs:**
  - The roadmap's A6 entry ("found no further lowering gap") and the new Deviations entry for D3/D4 do not record that these checks are skipped.
  - D2's text records this skipping for modes only.
- **Fix:** either make the fallback per function (lower and check each tempered function and its tempered callees even when other code fails to lower), or reject a tempered program the analysis cannot check. Record whichever is chosen in grammar.md.
- **Regression test:** `gap_late.pyr` should be rejected at `b.append(2)`.

### 4. P1: Changes a callee makes to a module list are invisible to the caller
- **Where:** `own/alias.rs:374-381` only counts arguments passed to `mut` or `owned` parameters as changed. `Summary` (`own/mod.rs:61-66`) has no "changes globals" field.
- **Program:**
  ```python
  G = [1, 2]
  temper def push() -> None:
      G.append(3)
  temper def f() -> int:
      n = 0
      for x in G:
          if n < 1:
              push()
          n += 1
      return n
  print(f())
  ```
  `check` accepts it, and CPython loops 3 times. Writing `G.append(3)` directly in the loop is rejected ("changes a list while a `for` loop over it is running"). `a = G; push(); return len(a)` is also accepted.
- **Fix:** add a field to `Summary` recording that a function may change what globals hold, set transitively. Treat such a call as a `Within` change of `World`.
- **Regression test:** both programs above should be rejected.

### 5. P1: A parameter and a module variable are assumed to be different lists
- **Where:** `own/alias.rs:183-186` and `:240`. `Obj::Param(i)` and `Obj::World` are never treated as possibly the same list. A5, as decided in ownership.md:54, covers only one list passed to two parameters, so neither check proves this.
- **Program:**
  ```python
  G = [1]
  temper def f(mut xs: list[int]) -> int:
      ys = G
      xs.append(2)
      return len(ys)
  print(f(G))
  ```
  `check` accepts it; CPython prints `2`.
- **Fix:** a parameter that holds lists should also be able to point at `World`, or the call site must prove the argument is not reachable from a global.
- **Regression test:** this program should be rejected.

### 6. P1: Changing a list through a conditional expression skips the D4 check
- **Where:** `own/alias.rs:150-178` and `:405`. The temporary that holds `a if c else b` has both `a` and `b` as source names. `.find(|a| !roots[w].contains(a))` then skips both, so no other live name is ever reported.
- **Program:**
  ```python
  temper def f(c: bool) -> int:
      a = [1]
      b = a
      (a if c else b).append(2)
      return len(a)
  print(f(False))
  ```
  `check` accepts it. The same code with a named `t = a if c else b; t.append(2)` is rejected at `t.append(2)`.
- **Fix:** skip a live name only when it is the temporary's *only* source name, or check the source names per path.
- **Regression test:** this program should be rejected at the `.append`.

### 7. P2: The D3 fix-it suggests code that does not type-check
- **Where:** `own/alias.rs:441-442`, the D3 message.
- **Programs:**
  - `temper def f(xs: list[int] | None) -> list[int] | None: return xs` gets "return a copy: `return list(xs)`". Applying it gives "`list` copies a list: expected list[T0], found list[int] | None".
  - A tuple parameter gets `return list(t)`, which fails the same way ("found tuple[…]").
- **Fix:** offer `return list(xs)` only when the parameter is exactly a list; otherwise use the generic "return a copy made with `list(...)`" wording.
- **Regression test:** an `own/tests.rs` case that applies the fix-it to both programs and re-checks.

### 8. P2 (coverage): Nothing tests the native refusal of an exception holding a list
- **Where:** `codegen/mod.rs:48` and `:1017`. The old `lower/tests.rs` test checked the refusal message; its replacement at `lower/tests.rs:836` only checks that the program lowers.
- **What I ran:** in a scratch copy with `refuse_gaps(program)?` disabled, the full `cargo test --locked` passes (419 + 19 + 27 + 29). `examples/exception_list.pyr` then builds and runs natively.
- **Why it matters:** this refusal is the only thing stopping the reference cycle that native.md warns about.
- **Needed:** a test that `build` of `ValueError([1])` fails with "does not support an exception holding a list". Also cover a tuple and an optional holding a list.

## Design questions for the owner

These are new readings of D4 and D2, not findings.

- **Only the outer list counts as aliased.** The new D4 text ("A list inside another is its own list") makes `b = a; b[0].append(1); len(a[0])` legal for lists of lists, and the same for `u = t` with tuples (`shared_tuple.pyr`, accepted). The recorded D4 says "two live names for one list"; the PR chose this reading itself.
- **One list passed twice depends on A5, which is still open.** A6 assumes different parameters are different lists. A5 is unchecked in the roadmap, so `f(xs, xs)` against `mut a, b` is accepted today. `f(rows, rows[0])` against `mut rows` is outside A5's decided wording ("the same list"), so neither check would catch it.
- **Any exception holding a parameter requires `owned`.** This applies even when the exception is raised and caught inside the same function, or never raised. It adds a requirement to tempered code that the decided D2 text does not spell out.

## Optional improvements
- `Change::Within` recomputes `flow.reachable` for every changed location on every check. Caching it would be cheaper; not a correctness issue.

## Checks run
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: finished with no warnings.
- `cargo test --locked` (all crates, conformance included): 419 + 19 + 27 + 29 passed, 0 failed.
- New `own::` tests run at 4771160: 4 fail there (`slots_of_a_tuple…`, `fields_of_a_tuple…`, `an_exception_holding_a_list_keeps_it`, `…_leaves_aliasing_checked`), so they guard the fixes.
- Rust rules: no `unwrap`, `expect` or `unsafe` in the new code, and no new dependencies.

I built with a scratch `CARGO_TARGET_DIR` because the configured target directory is read-only. Probes ran in a scratch directory. Two probe files briefly landed in the repo root after a failed `cd` and were moved out; the reviewed tree is unchanged. A scratch `git worktree` for 4771160 left an entry in the repo's worktree list that `git worktree remove` did not clear.

## Validation gaps
- The installed `python3` is **3.14.8**, not 3.13, so the CPython comparisons were against 3.14.
- valgrind is not installed, so there is no memcheck run (it matters for finding 8's cycle).
- I did not see Windows MSVC, macOS or AArch64 runs.
- CLAUDE.md asks to search remind-me first, but no remind-me tools were available in this session, so prior decisions come from the repo only. Nothing was saved there.

## Verdict
**Changes required.**
