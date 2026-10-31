<!-- pythrust/65-r1 run 2, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 341 s, 3609312 tokens, $2.33 -->

I found three blocking problems in PR 65. The most serious is an ownership hole: a tempered function can change a dict's contents without writing `mut`, and `check` accepts it. Verdict: **changes required**.

- **Repository:** baileyrd/PythRust, PR 65
- **Base:** 5de8b6027190a0d8ba0e857cad338d7813fe0bc4
- **Head:** 36dcec7f7e704b62e0d9f13d5800bc9e78c2180b (fetched `pull/65/head`; it matches)

## Blocking findings

### 1. P1: a dict view (`d.values()`, `d.items()`) is treated as a new, empty container, so changes made through it are not seen
- **Where:** `crates/pythrust-compiler/src/own/body.rs:547-552`. `elements_hold_list` only recognises a list. The new `Rvalue::View(a)` arm at `body.rs:326` therefore never fires, because a view's own type is a view, not a list. The view gets nothing stored in it, and a `list()` or `sorted()` of it then copies only the empty view. `own/alias.rs:365` also treats `View` as a fresh value.
- **Trigger:** any tempered function that keeps a view of a dict whose values hold lists, or copies one. A loop written directly as `for v in d.values():` is handled correctly, because lowering loops over the dict itself.
- **Failure (executed):**
  ```python
  temper def f(d: dict[str, list[int]]) -> None:
      vs = d.values()
      for v in vs:
          v.append(99)
  d = {"a": [1]}
  f(d)
  print(d)
  ```
  `check` accepts it; `run` and `build` both print `{'a': [1, 99]}`. D2 requires the error "tempered `f` changes `d` in place; write `mut d`", which the direct loop does get.
- **Other programs accepted the same way (executed through `check`):**
  - `list(d.values())[0].append(1)`
  - `list(d.items())[0][1].append(1)`
  - `sorted(d.values())[0].append(1)`
  - A tempered `vals(d)` that returns `list(d.values())`, then `vals(d)[0].append(1)` in a caller: the summary wrongly says the result is unrelated to `d`.
- **Exclusivity is broken too (executed):** with `d` declared `mut`, `vs = list(d.values()); push(d["a"], vs[0])` is accepted. The list version, `vs = list(xs); push(xs[0], vs[0])`, is correctly rejected.
- **Why this PR introduced it:** at the base commit, programs with dicts were never checked (the analysis fell back to a conservative mode that diagnoses nothing). This PR turns checking on for dicts and adds `Rvalue::View`.
- **Fix:** treat a view like a list of its items in `elements_hold_list`. For an items view the item is a tuple that may hold a list. Then check that `alias.rs` sees the view's contents too.
- **Regression tests (`own/tests.rs`):**
  - Rejected: the program above, the `list(d.values())` and `list(d.items())` forms, and the `push(d["a"], vs[0])` call.
  - Still accepted: a read-only `for v in vs: print(len(v))`, and the same body with `mut d`.

### 2. P2: a native `KeyError` from a `bool` key prints the int instead of the bool
- **Where:** `crates/pythrust-compiler/src/lower/table.rs:128` converts the key with `probe` (`bool` becomes `int`), and line 137 then builds the `KeyError` from the converted key.
- **Failure (executed):** `d = {1: "a"}; print(d[False])`
  - CPython and `run`: `KeyError: False`
  - `build`: `KeyError: 0`
  - From the code, `except KeyError as e: print(e)` would print `0` as well (not executed).
- **Fix:** build the exception from the key as written, before conversion.
- **Test:** add a missing-`bool`-key read to `key_error.pyr`, which is already in `NATIVE`.

### 3. P2: the D4 error calls a dict or set "a list"
- **Where:** `own/alias.rs:666` falls back to the text `"a list"`.
- **Failure (executed):**
  - `for k in d: grow(d)` gives "tempered `f` changes a list while a `for` loop over it is running".
  - `d["a"].add(...)` inside a loop over `d["a"]` gives the same wording.
  - `ds[0][k] = 1` inside a loop over a dict from `ds` gives the same wording.
- **Fix:** name the container type in the message.
- **Test:** an `own/tests.rs` assertion on the full message for a dict and for a set.

## Design questions
None.

## Optional improvements
- The new deviation text in `grammar.md` gives the fix-it as `for k in list(d)`, but the compiler actually suggests `for ... in list(...)`. One of them should change to match the other.

## What held up
- **Runtime (`table.rs`):** reference counts are right on insert, replace and lookup, on entry reads, and on view and `list()` snapshots. Hashing agrees with equality, and the hash index grows correctly.
- **D4:** it fires for direct loops, an alias (`e = d`), a callee, `.items()`/`.values()`, and nested containers.
- **D2 on dict stores:** `set.add` and dict stores require `mut` and `owned` as expected.
- **`dict[int, V]` with a `bool` key:** storing one is refused natively, as recorded in Known gaps; lookups with a `bool` key work.
- **Wide parity probe (executed):** dicts of lists, tuple and optional keys, `+=` (including on a missing key), raising inside a dict loop, `break`/`return` inside loops, generic functions over dicts, `sorted` and `set(xs)`. CPython 3.14, `run` and a debug native build printed identical output, and the debug runtime's leak counter reported nothing.
- **Size-change errors:** the `RuntimeError` messages for a dict, a set, and a dict changed by a callee during `.items()` match between CPython, `run` and the native build.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked --no-fail-fast` | pass, except `corpus_matches_cpython`, which refuses to run because the only interpreter here is Python 3.14 and the corpus needs 3.13 |
| Native corpus parity tests in `conformance` | pass (31 of 32 conformance tests pass; the one failure is the 3.14 refusal above) |
| Rust rules on new code | no `unwrap`/`expect` outside tests; `unsafe` only in `pythrust-rt`; no new dependencies |

## Validation gaps
- **CPython 3.13 comparison:** not run, since only 3.14 is installed. Settle it with `PYTHON=python3.13 cargo test --locked --test conformance`.
- **Memcheck:** valgrind isn't installed, so the roadmap's "clean under memcheck" claim is unchecked. Only the debug runtime's leak counter was seen.
- **Other platforms:** Windows MSVC, macOS and AArch64 were not run.
- **New tests at the base commit:** not run, because the disk quota ran out and I couldn't build a second target directory. Running them at the base would show whether they guard anything.
- **Project memory:** CLAUDE.md asks for the remind-me memory tools, but they aren't available in this session, so nothing was searched or saved.

**Verdict: changes required.**
