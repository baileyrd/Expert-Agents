<!-- pythrust/65-r1 run 1, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 391 s, 4929779 tokens, $2.62 -->

# Review of PR #65 in baileyrd/PythRust

- **Base:** `5de8b6027190a0d8ba0e857cad338d7813fe0bc4`
- **Head:** `36dcec7f7e704b62e0d9f13d5800bc9e78c2180b`. I confirmed it: `git fetch origin pull/65/head` gives that SHA.
- **Scope:** three commits that add dict and set tables to the runtime, the IR forms they need (`Dict`, `Set`, `View`, `Entry`, `Sort`), native lowering, and the `own`/`rc` facts. The roadmap card "M6 dict and set, Native" is marked done.

**Verdict: changes required.** Two P1 defects and two P2 defects block. I couldn't run the test suite or clippy because the scratch disk ran out of quota.

## Blocking findings

### 1. P1: the ownership checks miss a dict's values reached through a view held in a variable or copied with `list()`
- **Where:** `crates/pythrust-compiler/src/own/body.rs:326` together with `:547`.
- **Cause:** the PR adds `Rvalue::View(a)` to the arm at line 326. That arm only runs when `elements_hold_list(dest)` is true, and `elements_hold_list` matches only `Compound::List`. A view's destination is a `Compound::View`, so the arm never runs. The new view is recorded as holding nothing, and `list(view)` then copies nothing.
- **Effect:** in tempered code, `d.values()` and `d.items()` lose the link to the lists inside the dict. The mutation rule (D2), the keep rule (`owned`), the "returns a value sharing a list" rule and D4 all stop firing.
- **Executed traces** (`check` accepts all four):
  ```python
  temper def f(d: dict[int, list[int]]) -> int:   # d is read-only
      vs = list(d.values())          # also: vs = d.values(); for v in vs: ...
      vs[0].append(9)
      return 0
  d = {1: [1]}
  print(f(d), d)                     # run and build both print: 0 {1: [1, 9]}
  ```
  - `list(d.items())` followed by `its[0][1].append(9)` is also accepted.
  - A tempered `f(mut out, d)` that appends each `v` from `vs = d.values()` to `out` is accepted without `owned d`.
  - `temper def vals(d) -> list[list[int]]: return list(d.values())` is accepted, so a caller looping over `d[1]` can grow it through `vals(d)[0]` (`own_view_ret2.pyr`).
- **The legal neighbours are caught:** `for v in d.values(): v.append(9)` gives "write `mut d`"; `return list(xs)` on a list, and the explicit append loop, give "returns a value sharing a list".
- **Why this PR:** at base these programs also passed `check`, but only because the `own` analysis didn't look at dict bodies. This PR turns that analysis on, removes the `conservative(...)` assertions in `own/tests.rs`, and marks "the `own` … facts" done.
- **Fix:** let `elements_hold_list` (or the guard at line 326) cover `Compound::View(_, t)`. Check `alias.rs` `value()`/`step` for the same gap.
- **Regression tests:** in `own/tests.rs`, the four programs above should be rejected, and `for v in d.values(): v.append(9)` with `mut d` should stay accepted.

### 2. P1: natively, `d.get(k, default)` evaluates `default` only when the key is missing, and after the lookup
- **Where:** `crates/pythrust-compiler/src/lower/table.rs:204–227`. The lookup is at line 210; `self.expr(&a.value)` at line 223 runs only in the `miss` block.
- **What should happen:** Python evaluates every argument before the call. `run` does that.
- **Executed trace:**
  ```python
  d: dict[str, int] = {}
  def fill() -> int:
      d["a"] = 7
      return 0
  print(d.get("a", fill()))
  ```
  CPython and `run` print `7`. The native build prints `0`.
- **More consequences** (traced from the source):
  - A default with a side effect (a `print`, an `append`) is silently skipped on a hit.
  - A default that raises, such as `d.get(k, 1 // 0)`, doesn't raise.
- **Fix:** evaluate the default argument before the `Contains`, then move it into `dest` on a miss and release it on a hit.
- **Regression test:** a corpus fixture in `NATIVE` with a side-effecting default. `dicts.pyr` only uses literal defaults, so it can't catch this.

### 3. P2: natively, a missing `bool` key reports `KeyError: 1`, not `KeyError: True`
- **Where:** `lower/table.rs:126–137`. `dict_read` builds `KeyError` from the key after `probe` has converted the `bool` to an `int`.
- **Executed trace:**
  ```python
  d = {2: "a"}
  try:
      print(d[True])
  except KeyError as e:
      print("caught", e)
  ```
  CPython and `run` print `caught True`; native prints `caught 1`. The uncaught message differs the same way.
- **Fix:** build the exception from the original key. Add a `d[True]` miss to `key_error.pyr`.

### 4. P2: the D4 diagnostic says "a list" when the container is a set or dict
- **Where:** `own/alias.rs:666`.
- **Executed trace:** `for x in ss[0]: ss[0].add(x + 1)` and `d["a"].add(...)` both report "tempered `f` changes a list while a `for` loop over it is running".
- **Fix:** name the container's kind. Assert the message in `changing_a_dict_or_set_a_loop_runs_over_is_an_error`.

## Design questions for Nano
- **Stricter D4 for dicts in tempered code.** Tempered code now rejects any store into a dict a loop is running over, so `for k in d: d[k] = d[k] + 1` is rejected. The roadmap card said "changing a container's size". The PR records this rule itself as a deviation in `grammar.md`, without Nano's attribution. It's consistent with how lists are treated, but it rejects a common idiom.
- **Storing a `bool` key in a `dict[int, V]` is now a native backend gap** ("pending Nano's call"). The card's exit criterion was "the `True`-is-`1` key test in both backends". The PR satisfies it only for reads and marks the item done. `dict_bool_key` runs only under `run`.

## Optional improvements
- `pt_view_new` snapshots values that are dropped immediately in `list(d.values())`. Lowering `list(view)` straight to `pt_list_of` over the dict would save an allocation.
- Not introduced by this PR, but noticed: `run` and `build` agree with each other and differ from CPython on two things.
  - **NaN identity:** `{1: n} == {1: n}` and `[n] == [n]` are `False`; CPython says `True`.
  - **NaN sort order:** `sorted` puts NaNs in a different order than CPython.

  Neither is listed in Deviations, so they're worth recording there.

## Checks and validation gaps
- **Ran and passed:**
  - `cargo fmt --all --check`.
  - Debug build of head and base.
  - About 30 probe programs through `check`, `run`, `build` and CPython. Native output matched `run` everywhere except findings 2 and 3, and the debug runtime reported no leaked objects on the probes I ran.
- **Could not finish:**
  - `cargo clippy --locked --all-targets -- -D warnings` and `cargo test --locked` both failed with "Disk quota exceeded". The scratch volume is mostly filled by other sessions' build directories, and I didn't delete them.
  - So I haven't seen the new unit tests, the conformance tests (including the `NATIVE` additions), or the base-SHA runs of the new tests pass.
- **To settle the gaps:** run those two commands on a machine with space, plus `PYTHRUST_VALGRIND=1 cargo test --test conformance`.
- **Other gaps:**
  - Only Python 3.14.8 is installed, not 3.13, so I compared probes against 3.14. One difference I saw, the unpack message "got 3", comes from 3.14.
  - There's no valgrind here.
  - I couldn't test Windows MSVC, macOS or AArch64.
- **Rust rules on new code:** I found no `unwrap`/`expect` outside tests. The new `unsafe` is all in `pythrust-rt/src/table.rs`, under `allow(unsafe_code)` with Safety notes. There are no new dependencies.
- **Memory tools:** the `remind_me` tools that CLAUDE.md asks for aren't available here, so nothing was searched or saved.
