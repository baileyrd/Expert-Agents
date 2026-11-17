<!-- pythrust/65-r1 run 2, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 485 s, 5911717 tokens, $3.06 -->

# Review of PR 65 (baileyrd/PythRust): native dicts, sets and views

- **Base:** 5de8b6027190a0d8ba0e857cad338d7813fe0bc4
- **Head:** 36dcec7f7e704b62e0d9f13d5800bc9e78c2180b. `pull/65/head` matched this SHA. Commits: 71890e7, 50b52b0, 36dcec7.

**Verdict: changes required.** One ownership hole (P1), one place where native code computes differently from `run` (P1), and three P2 defects.

The runtime tables, refcounting and the main lowering held up. A broad parity probe and a refcount stress probe gave the same output from `build` as from `run`. The debug runtime, which fails on leaked strings and sequences, reported no leaks. `for` over a dict, set or view raises the size-change `RuntimeError` in all three (CPython, `run`, native). The cycle refusal now covers dicts, sets and views, and the bool-key store refusal also fires through generic functions.

## Blocking findings

### 1. P1: a dict view loses the lists it holds, so D2 and D3 miss writes through views
- **Where:** `own/body.rs:326` and `:547`. The line `Rvalue::CopyList(a) | Rvalue::View(a) if self.elements_hold_list(dest)` is guarded by `elements_hold_list`, which only matches `Compound::List`. For a view destination it is always false, so the new view object records nothing it holds. `own/alias.rs:365` also treats `View(_)` as `fresh`.
- **Effect:** any value reached *through* a view has no alias, whether by `list(view)`, `sorted(view)`, a `for` over a held view, or unpacking a view.
- **Traces (executed; `check` accepts each, `run` and `build` print the shared mutation):**
  - **D3, alias then change:**
    ```
    temper def f() -> None:
        xs = [1]
        d = {"a": xs}
        ys = list(d.values())[0]     # also sorted(d.values())[0], list(d.items())[0][1],
        ys.append(2)                 # vs = d.values(); for v in vs: ys = v
        print(xs)                    # prints [1, 2]
    ```
    `for v in list(d.values()): ys = v` is also accepted, and that is the fix-it pattern this very check recommends. The direct forms (`d["a"]`, `d.get`, `for v in d.values()`, `.items()`) are correctly rejected. The list version `ys = list(zs)[0]` is rejected too.
  - **D2, change without `mut`:**
    ```
    temper def bump(d: dict[str, list[int]]) -> None:
        for v in list(d.values()):   # or: vs = d.items(); for k, v in vs:  / sorted(d.values())
            v.append(1)
    ```
    This is accepted and changes the caller's dict. The `for v in d.values()` form, and the list version, both give "write `mut d`".
  - **Function summaries:** `temper def first(d: dict[str, list[int]]) -> list[int]: return list(d.values())[0]`. A tempered caller then appends to the result while still using the original list, and it is accepted.
- **Base:** at base these functions weren't checked at all (dicts couldn't lower). This PR turns the check on and claims "the `own` … facts for dict stores", so the gap is in the PR's new `View` rule.
- **Correction:** the view should hold what its dict holds:
  - in `body.rs`, test the view's item type (`Compound::View(_, item)`) as `holds_list` does, not only `Compound::List`;
  - in `alias.rs:365`, give `View` the same contents as `CopyList`.
- **Tests that should go red without the fix:** `own/tests.rs` cases rejecting the three programs above, plus the `for v in list(d.values()): v.append` D2 case. The legal neighbour that must stay accepted is `for v in list(d.values()): print(v)`.

### 2. P1: native `d.get(k, default)` evaluates `default` only when the key is missing
- **Where:** `lower/table.rs:222–225`. The default expression is lowered inside the `miss` block only. CPython and `run` evaluate every argument before the call.
- **Trace (executed):**
  ```
  d = {1: "a"}
  def dflt() -> str:
      print("dflt")
      return "x"
  print(d.get(1, dflt()))
  xs = [0]
  print(d.get(1, "q" * xs[3]))
  ```
  - CPython and `run` print `dflt`, then `a`, then fail with `IndexError` (exit 1).
  - The native build prints `a` twice and exits 0.

  Skipped prints, changes to state and exceptions reach past the one call, hence P1.
- **Correction:** evaluate `default` before the `Contains` branch and move it into `dest` on the miss path. On the hit path, release it.
- **Test:** a corpus fixture in `NATIVE` whose `get` default has a side effect and one that raises.

### 3. P2: a missing `bool` key raises `KeyError(1)` natively instead of `KeyError(True)`
- **Where:** `lower/table.rs:128` and `:137`. `probe` turns the bool into an int before `new_exc(ExcKind::Key, &[k])`.
- **Trace (executed):** `d = {2: "a"}`, then `d[True]` under `try` / `except KeyError as e: print(e)`.
  - CPython and `run` print `True`.
  - The native build prints `1`.
- **Doc impact:** `native.md:89` says reading with a bool "builds: it is the int it equals, as in `run`", which is false for this message.
- **Correction:** build the `KeyError` from the original operand.
- **Test:** add the case to `key_error.pyr` (it is in `NATIVE`).

### 4. P2: the aliasing fix-its suggest `list(...)` for dicts and sets, and applying them breaks the program
- **Where:** `own/alias.rs:683`, `:694`, `:697` and `own/calls.rs:232`. Dicts and sets are now treated as "lists" in the own stage, so the messages say "may be the same list" and offer `x = list(y)`.
- **Traces (executed):**
  - `e = d; e["b"] = 2; print(d)` in tempered code suggests `` `e = list(d)` ``. Applying it gives "expected int, found str".
  - For a set the suggestion is `t = list(s)`, which is wrong; `t = set(s)` is the working copy.
  - For a call that passes one dict twice, the suggestion `pass a copy (list(...))` gives "expected dict[str, int], found list[T6]".
  - At base these programs were accepted, so the misleading fix-it is newly reachable.
- **Correction:** pick the fix-it by type:
  - for a set, `set(x)`;
  - for a dict, there is no copy builtin yet (`dict` is not defined), so drop the fix-it or ask Nano what to offer;
  - say "dict" or "set" instead of "list".
- **Test:** `own/tests.rs` assertions on the message for dict and set aliases.

### 5. P2: roadmap card ticked done although its exit test was weakened
- **Where:** `docs/roadmap.md:113` and `examples/dicts.pyr:59–60`.
- **What happened:**
  - The card's exit criterion was "the `True`-is-`1` key test in both backends".
  - The PR removed the `by_int[True] = "true"` store from `dicts.pyr` and moved it into the run-only `dict_bool_key`. It records storing a bool key as a backend gap "pending Nano's call" (commit 50b52b0), then ticks the card `[x]`.
  - The gap itself is honestly recorded and refused by name; I checked `{True: v}`, `d[True] = v`, `d[True] += v` and the generic `put(x, True)`.
- **Correction:** leave the card unticked, or get Nano's sign-off on narrowing the criterion. Either way, record the decision.

## Questions for the owner
- **D4 is stricter than the agreed card.** Tempered code now rejects any store into the container a `for` runs over, including `d[k] = d[k] + 1`. CPython allows that, and grammar.md's own Dicts paragraph calls it legal. Nano's card said "changing a container's size". The PR records this as a deviation (grammar.md, size-change bullet), but not as Nano's decision. That is a new language rule, so it needs the owner's yes.
- **There is no way to copy a dict** (`dict(d)` is "not defined"), so tempered code has no fix for a dict alias. Should `dict(...)` or `d.copy()` come with M6?

## Optional improvements (not blocking)
- `d[True] += v` on a key that already exists keeps the original key in CPython, so it could build instead of being refused.
- **Outside this PR (also at base):**
  - `sorted([3.0, nan, 1.0, 2.0])` gives `[1.0, 2.0, 3.0, nan]` in both `run` and native, but `[3.0, nan, 1.0, 2.0]` in CPython. The difference is not listed in Deviations.
  - `(2, "b") in d` for `d: dict[tuple[int | None, str], int]` is rejected as "never equal", which is wrong: they can be equal.

## Checks run
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: passed.
- `cargo test --locked`: everything passed except `corpus_matches_cpython`, which refused to run because this machine only has Python 3.14 and the test requires 3.13. Lib tests 536/536; `corpus_matches_native`, `corpus_matches_pythrust` and `tempered_matches_loose` passed.
- **New fixtures against CPython 3.14 by hand:** `dicts`, `sets`, `dict_changed_size`, `dict_view_changed_size`, `dict_bool_key` and `key_error` match their goldens. `dict_views` and `set_order` differ, as their `# deviation:` markers record.
- **The PR's new own tests are red at base:** `changing_a_dict_or_set_a_loop_runs_over_is_an_error` and `dict_stores_and_set_adds_are_mut_and_keep_what_they_store`.
- **Rust rules in the diff:** no `unwrap`/`expect` outside tests (the `self.expect` hits are the lowering's own method), `unsafe` only in `pythrust-rt`, no new dependencies.

## Not verified
- **The CPython 3.13 conformance run:** needs a 3.13 interpreter (`PYTHON=python3.13 cargo test --test conformance`).
- **memcheck:** valgrind isn't installed, so the roadmap's "clean under memcheck" claim is unchecked. Run with `PYTHRUST_VALGRIND=1`.
- **Other targets:** AArch64, macOS and Windows MSVC were not run.
- **Shared memory:** the remind-me tools weren't available in this session, so nothing was searched or saved there.

I removed the scratch base worktree I had created; the checkout was not modified.
