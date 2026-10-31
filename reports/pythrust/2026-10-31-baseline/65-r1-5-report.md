<!-- pythrust/65-r1 run 5, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 469 s, 5341374 tokens, $3.12 -->

# Review of PR 65, baileyrd/PythRust: native dicts, sets and views (M6 native card)

- **Base:** `5de8b6027190a0d8ba0e857cad338d7813fe0bc4`
- **Head:** `36dcec7f7e704b62e0d9f13d5800bc9e78c2180b`. The fetched `pull/65/head` matches this SHA.
- **Verdict: changes required.** The PR adds two ownership-soundness holes and two places where native builds behave differently from `run` and CPython. All four were confirmed by running probes.

## Blocking findings

**1. P1: a held dict view loses its link to the dict's values, so D2 and D3 miss mutations made through it.**
- **Where:** `crates/pythrust-compiler/src/own/body.rs:326` and `:547`, with the same effect in `own/alias.rs:365`.
- **Cause:** the new arm `Rvalue::CopyList(a) | Rvalue::View(a) if self.elements_hold_list(dest)` never fires for a view, because `elements_hold_list` only matches `Compound::List`. A fresh view therefore holds nothing. Reading an element from it points back at the view itself, not at the dict's value lists, even though `pt_view_new` shares those same lists.
- **Executed (`check`):**
  ```python
  temper def f(d: dict[str, list[int]]) -> None:
      vs = d.values()
      for xs in vs:
          xs.append(1)
  ```
  This is accepted, but the direct form `for xs in d.values():` is rejected with "changes `d` in place; write `mut d`" (D2 hole). `for xs in list(d.values()):` is also accepted.
- **Executed (D3):**
  ```python
  temper def f(mut d: dict[str, list[int]]) -> int:
      vs = d.values()
      for r in vs:
          d["a"].append(1)
          return len(r)
      return 0
  ```
  This is accepted, while `r = d["a"]` followed by `d["a"].append(1)` and `return len(r)` is rejected as an alias. In CPython, `r` is the same list object as `d["a"]`.
- **Fix:** make `elements_hold_list` (or the `View` arm) treat a view's items as holding the source's contents. Check that `alias.rs`'s `step` then sees them, because it reads from `flow.stored`.
- **Regression tests (`own/tests.rs`):**
  - Mirror `a_copy_shares_only_elements`: the held-view `poke` should infer `MUT`.
  - `def vals(d): return list(d.values())` should report `result_aliases`.
  - The D3 program above should be rejected, and its `list(...)` neighbour should still be accepted.

**2. P1: native `d.get(k, default)` evaluates `default` only when the key is missing.**
- **Where:** `lower/table.rs:223`. `self.expr(&a.value)` is lowered inside the miss block. Python evaluates every argument before the call, and `run` does too.
- **Executed:**
  - `d.get(1, noisy())` with `1` present: `run` and CPython print `default evaluated` then `2`. The native build prints only `2`.
  - `d.get("a", int("x"))` inside a `try`: `run` and CPython print `caught invalid literal…`. The native build prints `1`, skipping the exception entirely.
- **Fix:** lower the default before the `Branch`, then move it into `dest` on the miss path and release it on the hit path.
- **Regression test:** add a corpus case to `dicts.pyr` (or a new `NATIVE` fixture) with a default that has a side effect and one that raises.

**3. P2: a missing `bool` key reports the converted int in its `KeyError`.**
- **Where:** `lower/table.rs:128` and `:137`. `dict_read` builds `KeyError` from the probed key, which has already been converted from bool to int.
- **Executed:** `d = {2: "b"}`, then `d[True]` inside `try`/`except KeyError as e: print("caught", e)`. `run` and CPython print `caught True`; the native build prints `caught 1`.
- **Fix:** pass the original operand to `new_exc` and use the converted value only for the lookup.
- **Regression test:** add this case to `key_error.pyr`.

**4. P2 (coverage): the new named refusal "a bool key stored in an int-keyed dict" has no test.**
- **Where:** `lower/table.rs:118`.
- There is no test in `lower/tests.rs` or `tests/cli.rs`, and `dict_bool_key` is simply left out of `NATIVE`. I confirmed by running it that `build` refuses `d[True] = "b"` and `d[True] += 1`, but nothing would go red if that refusal turned into silently storing `1`.
- **Fix:** add a lowering or CLI test asserting the backend error for a display, a store and an augmented store, plus a neighbouring test where a bool read (`d[True]`, `True in d`) still builds.

## Design question for Nano
- Native code refuses to store a `bool` key into a `dict[int, V]`, and the commit calls this "pending Nano's call". Meanwhile the roadmap card's original exit criterion ("the `True`-is-`1` key test in both backends") was rewritten, `dicts.pyr` was edited to drop the store, and the card is marked `[x]`.
- The refusal names the case, which is allowed, but closing the card while the owner's decision is still open should be confirmed by Nano.

## Optional, non-blocking
- `sorted()` with a NaN differs between `run` and CPython: `[3.0, 1.0, nan, 2.0, 0.5]` gives `[1.0, 3.0, nan, 0.5, 2.0]` in `run` versus `[0.5, 1.0, nan, 2.0, 3.0]` in CPython. Native matches `run`. This comes from the earlier interpreter PR, but it isn't listed in Deviations.
- When an element type doesn't match, `expect` still says "its list or tuple holds", even for sets and dicts.

## Checks run
| Check | Result |
|---|---|
| `cargo fmt --all --check` | Pass |
| `cargo clippy --locked --all-targets -- -D warnings` | Pass |
| `cargo test --locked` | 536 compiler unit tests, 20 runtime tests and 5 CLI tests pass; conformance 31/32 |
| `corpus_matches_cpython` | Did not run: this machine has only Python 3.14 and the test requires 3.13 |

- New code has no `unwrap`/`expect` outside tests, `unsafe` stays inside `pythrust-rt`, and no dependencies were added.
- I did not re-run the new tests at the base SHA because of disk limits. They need dict lowering, which the base refuses, so they can't pass there.

## Validation gaps
- **Memory checking:** valgrind isn't installed, so the "clean under memcheck" claim for the new native fixtures is unverified.
- **Other platforms:** I could not run macOS, Windows MSVC, AArch64, or the CPython 3.13 conformance comparison.
- **Repository memory:** the `remind_me` tools that CLAUDE.md requires aren't available in this session, so I couldn't search or record anything there.
- **Disk quota:** the sandbox ran out of disk quota partway through (the linker crashed). It cleared on its own and I deleted nothing in your checkout. Probe files are in `$TMPDIR/probes`, alongside older files I didn't create.
