<!-- pythrust/65-r1 run 5, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 463 s, 5260887 tokens, $3.01 -->

# Review of PR 65, baileyrd/PythRust: dicts, sets and views in native code

**Pinned:** base `5de8b6027190a0d8ba0e857cad338d7813fe0bc4`, reviewed head `36dcec7f7e704b62e0d9f13d5800bc9e78c2180b`. The fetched `pull/65/head` matches the head SHA. Three commits: 71890e7, 50b52b0, 36dcec7.

**Verdict: changes required.** I traced and ran two P1 defects and two P2 defects. Every probe was run under CPython, `pythrust run` and `pythrust build` from scratch directories outside the tree.

## Blocking findings

### 1. P1, ownership soundness: a change made through a dict view is lost, so D2 does not fire
- **Where:** `crates/pythrust-compiler/src/own/body.rs:326`, guarded by `elements_hold_list` at `body.rs:547`. That check only matches `Compound::List`, so the `Rvalue::View(a)` arm this PR added never runs when the destination is a view. The view's new object never receives the dict's contents. Anything read out of it, or out of `list(...)` / `sorted(...)` of it, then points only at the view itself and never at the parameter.
- **Trigger, executed with `check`:**
  ```python
  temper def f(d: dict[str, list[int]]) -> None:
      v = d.values()        # also: list(d.items()), sorted(d.items()), sorted(d.values())
      for xs in v:
          xs.append(1)
  ```
  The PR accepts this program. Its neighbour `for xs in d.values(): xs.append(1)` is correctly rejected with "changes `d` in place; write `mut d`". The list equivalent (`list(d)` over a `list[tuple[str, list[int]]]`) is also rejected. Loose code gets `d` summarised as `read`, so callers' D3 and D4 checks see no change either.
- **Fix:** make `elements_hold_list` (or the `View` arm) cover `Compound::View` items, and check `CopyList` of a view the same way.
- **Regression test:** add to `own/tests.rs` a rejected program for each of `v = d.values()`, `list(d.items())` and `sorted(d.values())`. Keep the direct `for xs in d.values()` program as the accepted-shape control.

### 2. P1, backend parity: native `d.get(k, default)` skips the default on a hit and runs it after the lookup
- **Where:** `crates/pythrust-compiler/src/lower/table.rs:204-230`. The default is lowered only inside the `miss` block, after the `Contains` lookup. Python, and `run`, evaluate every argument before the call.
- **Traces, all executed:**
  - **A print in the default:** `d = {1: 10}; print(d.get(1, noisy(5)))`. CPython and `run` print "default evaluated 5" and then 10. Native prints only `10`.
  - **A default that changes the dict:** `def add(d): d[2] = 20; return 0` then `print(d.get(2, add(d)))`. CPython and `run` print `20`; native prints `0`.
  - **A default that raises:** `d.get(1, xs[5])` inside a `try`. CPython and `run` print "caught list index out of range"; native prints `10`.
- **Fix:** lower the key and the default first, in source order, then branch, and release the default on the hit path.
- **Regression test:** a corpus program in `NATIVE` covering these three cases.

### 3. P2, Python fidelity: a native `KeyError` from a bool key shows the int
- **Where:** `lower/table.rs:128-137`. `dict_read` builds `KeyError` from the probe after converting the bool to an int.
- **Trace, executed:** `d = {1: "a"}; d[False]`. CPython and `run` show `KeyError: False` (and `str(e)` is `False`); native shows `KeyError: 0`, both when caught and uncaught.
- **Fix:** build the exception from the original operand.
- **Regression test:** add the case to `key_error` or `dicts` in `NATIVE`.

### 4. P2, roadmap and docs: the native card is marked done after its exit criteria were rewritten
- **Where:** `docs/roadmap.md:113`.
- **What changed:** the base card's exit criterion was "the `True`-is-`1` key test in both backends". The PR does three things to meet it:
  - removes `by_int[True] = "true"` from the `dicts` fixture;
  - moves that case to a new run-only fixture, `dict_bool_key`;
  - makes storing a bool key a backend refusal, which the commit message itself calls "pending Nano's call".
- **D4 wording:** the base card said "changing a container's size inside a `for` over it is a compile error". The PR rewrote that line to "any store, a store at an existing key included", to match the new behaviour.
- **Fix:** leave the card open, or record the owner's decision on both points (see Design questions) before ticking it.

## Design questions for the owner (Nano)
- **D4 for stores at an existing key.** In tempered code, D4 now rejects `for k in d: d[k] = d[k] + 1`, a common Python idiom that CPython allows. `grammar.md:134` and `:173` document this, but no decision by Nano is recorded. It is a new language rule; under CLAUDE.md it should be proposed and asked, not decided.
- **Bool keys in an int-keyed dict natively.** `d[True] = v` and `{True: v}` on a `dict[int, V]` are refused by `build` but accepted by `run`. Should they stay a recorded gap, or should native keys remember their first spelling?

## Optional improvements
- `lower/seq.rs`: the `Ty::View` arm in `unpack` cannot be reached, because inference rejects unpacking a view ("cannot unpack dict_items"). Remove it or test it.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked` | 536 lib, 20 bin and 5 cli tests pass. Conformance: 31 pass, including `corpus_matches_native` and `corpus_matches_pythrust`. `corpus_matches_cpython` refused to run because the only interpreter here is Python 3.14. |
| New `own` tests run against base code | `changing_a_dict_or_set…` and `dict_stores_and_set_adds…` both fail at base, so they guard the change |
| Rust rules in new code | no `unwrap`/`expect` outside tests (the `expect` hits are a `FnBuilder` method); `unsafe` only in `pythrust-rt`; no new dependencies |
| Probes | about 30, all run under CPython 3.14, `run` and `build`. Size-change errors (including on the last step, and through a callee on a global), loops with `else`, `break` and nested loops, unpacking, views, reprs, equality, generic open key types, dead-branch typing, D4 through an alias, through a callee and over `items()`, D3 for `d[k] = xs` and `s.add(x)`, and exception unwinding out of table loops all matched. The debug runtime's leak counter reported no leaks on normal exit. |

## Validation gaps
- **No CPython 3.13:** the CPython oracle was 3.14, so the corpus check against CPython 3.13 did not run.
- **No valgrind:** the PR's "clean under memcheck" claim is unverified. Run `PYTHRUST_VALGRIND=1 cargo test --test conformance` on x86-64 Linux.
- **Other targets not run:** AArch64, macOS and Windows MSVC.

## Housekeeping
- **Stray files, now removed:** one `cd` failed, so five probe files (`m_*.pyr`) were briefly written into the checkout. I moved them out, and `git status` showed none of them afterwards.
- **No remind-me entries:** the remind-me tools CLAUDE.md asks for were not available, and this review is read-only, so nothing was saved there.
