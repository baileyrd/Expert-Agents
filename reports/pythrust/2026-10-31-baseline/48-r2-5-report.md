<!-- pythrust/48-r2 run 5, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 295 s, 1434960 tokens, $1.32 -->

# Review: baileyrd/PythRust PR 48

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Reviewed head:** `2a09a304c45dcdd8f090a1751b81eca4944fe229`. I fetched `pull/48/head` and it matches this SHA.
- **Commits:** 4771160 (A6 alias check) and 2a09a30 (the round-1 fixes).

**Verdict: changes required.** K1 is fixed, but the problem behind K2 is still open, and the alias pass has two more ways to accept programs D4 forbids.

## Prior findings at this head

- **K1 (tuple slots): resolved.** Four programs are now rejected with the D4 error: a tuple parameter, a tuple returned by a call, tuple unpacking (`a, b = pair`), and tuples inside a list (`ps[0][0]` / `ps[1][1]`). The regression test `slots_of_a_tuple_from_elsewhere_may_be_one_list` passes at the head and fails at 4771160.
- **K2 (exception holding a list): the specific trigger is fixed, the problem is not.** `ValueError(xs)` now lowers, and `an_exception_holding_a_list_leaves_aliasing_checked` and `…_keeps_it` both fail at 4771160. But any other gap that lowering can't handle, anywhere in the module, still turns off D3 and D4 for every tempered function. That is finding F1.

## Blocking findings

### F1 (P1): any lowering gap anywhere in the module turns off D3 and D4 for every tempered function

- **Where:** `own/mod.rs:107` sets `aliasing: BTreeMap::new()` whenever the analysis falls back to conservative. Lists using `+`, `*` or `+=` reach that fallback through `lower/expr.rs:86` ("arithmetic on a non-number"). A call to a builtin before the `def` that shadows it also reaches it.
- **Trigger:** list `+`, `*` or `+=`, inside the tempered body or anywhere in loose code. These are common operations.
- **Scenario (executed):** take `examples/aliasing_shared.pyr` and add one loose line at the end: `z = [1] + [2]`.
  - `check` accepts it and `run` prints `3`. Without that line it is rejected with the corpus error.
- **Other accepted programs (executed):**
  - `c = [1] + [n]; d = c; d.append(1); return len(c)`
  - The same with `[0] * n`.
  - `b = a; b += [n]; return len(a)`. Pythrust and CPython both change the list in place here.
  - D3: `temper def f(rows: list[list[int]]) -> list[list[int]]: return rows + []`. The returned list shares its inner lists with the parameter.
  - Aliasing in a tempered function, with `x = print(7)` followed by `def print(n)` elsewhere in the module.
- **Why it's a defect:** grammar.md:180 says these programs are a compile error, with no exception. Roadmap A6 says the PR "closes any other lowering gap a tempered body can reach instead of rejecting it."
- **Fix:** lower list `+`, `*` and `+=` (`+=` must count as a change). Separately, stop letting one gap blank the alias results for the whole program: lower and check each tempered body on its own, with conservative summaries for callees that fail to lower. A tempered body that can't lower should be an error, not a silent pass.
- **Regression tests:** in `own/tests.rs`, the D4 program above plus `z = [1] + [2]` must still get the D4 error. Add one test each for `+`, `*`, `+=` and the D3 `rows + []` case.

### F2 (P1): changing a list through a conditional expression or `or` hides both names

- **Where:** `own/alias.rs:150-178` and `:405`. `roots` collects every variable a temporary may come from, ignoring control flow. `change` then skips every root of the changed temporary, so when both arms are names for the same list, both get skipped.
- **Scenario (executed):**
  ```
  temper def f(c: bool) -> int:
      a = [1]
      b = a
      (a if c else b).append(2)
      return len(a) + len(b)
  ```
  - `check` accepts it, and it builds natively, so the analysis was precise. `(b or a).append(2)` is accepted too.
  - The control, which writes `b.append(2)` directly, is rejected.
- **Fix:** exclude only the root this path actually came from. Alternatively, skip a root only when the temporary has exactly one. Otherwise, another live name that may be the same list should still be reported.
- **Regression test:** the program above must be rejected. The neighbour `(a if c else [0]).append(2)` with only `a` used later should stay accepted.

### F3 (P1): a parameter and a module global are assumed to be different lists

- **Where:** `own/alias.rs:183-187` gives each parameter its own object, `:240` maps every global read to the world object, and `same` (`:471-474`) treats different objects as never the same list.
- **Scenario (executed):**
  ```
  G = [1]
  temper def f(mut xs: list[int]) -> int:
      a = G
      xs.append(2)
      return len(a)
  print(f(G))
  ```
  - `check` accepts it. `run` and CPython both print `2`: `a` and `xs` are the same list, it is changed through one name, and the other is used later.
  - The control `a = xs` is rejected.
  - A5 does not cover this, because only one parameter is involved.
- **Fix:** a parameter (and anything inside it) may be a module global, so changes to one should conflict with names holding the other.
- **Regression test:** the program above must be rejected. A tempered function that reads `G` and changes only a fresh list should stay accepted.

### F4 (P2): the D4 fix-it for a changed parameter changes what the program does

- **Where:** `own/alias.rs:417-428`. The fix-it always copies the changed name.
- **Scenario (executed):** `temper def f(mut xs: list[int]) -> int: a = xs; xs.append(2); return len(a)` gets the suggestion `xs = list(a)`.
  - Applying it compiles, but the caller's list no longer changes: the output becomes `1 [1]` instead of `2 [1, 2]`.
- **Fix:** when the changed name is a parameter, or more generally the older name, suggest copying the other one (`a = list(xs)`).
- **Regression test:** assert the fix-it text for this program.

### F5 (P2): the docs claim gaps are closed that are still open

- `docs/roadmap.md:71` says "A6 closed the last one."
- `docs/roadmap.md:74` says a probe "found no further lowering gap."
- `docs/grammar.md:180` states the D3/D4 error with no condition.
- All three are false while F1 stands.

## Design questions for the owner

- **An exception as a second name for a list.** `xs = [1]; e = ValueError(xs); xs.append(n); raise e` is accepted, and the raised message shows the changed list (Pythrust and CPython agree). Should an exception holding a list count as a live name under D4? The PR already treats holding a list in an exception as keeping it (`owned`).
- **`owned` arguments count as changes.** `put(rows, a)` with `owned x` is reported as changing `a` because `mode >= Mut` (`alias.rs:378`). This is sound, but it rejects code that only keeps the list.

## Optional improvements

- `alias::tempered` matches functions by name. A loose `def` with the same name as a tempered one would also be checked as tempered.

## Checks run

| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked` | pass: 419 + 19 + 27 + 29 tests |
| `own/tests.rs` from the head, run against 4771160 | the 3 K1/K2 tests fail there, so they guard the fixes |
| Probes through `check`, `run` and `build`, compared with `python3` | as listed above |

No new `unwrap`/`expect`, `unsafe` or dependencies.

## Validation gaps

- **CPython 3.13:** only 3.14.8 is installed, so `conformance` and my comparisons ran against 3.14. Rerun with `PYTHON=python3.13`.
- **Memcheck:** valgrind isn't installed.
- **Other platforms:** I couldn't test macOS, Windows MSVC or AArch64.
- **Native refusal:** `refuse_gaps` was checked on Linux x86-64 only.

## Notes

- One of my `build` probes wrote an untracked `examples/list_semantics` binary into the tree. I deleted it, and the tree is back to its original state.
- `CLAUDE.md` says to use the remind-me memory tools, but they weren't available in this session, so nothing was searched or saved.
