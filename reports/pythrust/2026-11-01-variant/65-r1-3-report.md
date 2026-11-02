<!-- pythrust/65-r1 run 3, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 472 s, 6823395 tokens, $3.48 -->

**PR 65 review: native dicts, sets and views — changes required**

- **Repository:** baileyrd/PythRust, PR #65
- **Base:** `5de8b6027190a0d8ba0e857cad338d7813fe0bc4`
- **Head:** `36dcec7f7e704b62e0d9f13d5800bc9e78c2180b`. `git fetch origin pull/65/head` gives that SHA, so the pin holds.
- **Roadmap step claimed:** M6, "`dict` and `set`", the *Native* part.

CLAUDE.md asks me to search and save in remind-me. Those tools aren't available in this session, so the review rests only on the repo and on probes I ran.

## Blocking findings

### 1. P1: a view that is held or copied loses its link to the dict's lists, so D2 and D3 miss changes made through it
- **Where:** `crates/pythrust-compiler/src/own/body.rs:547-551` and `:326`, and `own/alias.rs:365`.
  - `elements_hold_list` only matches `Compound::List`. So for a `View` destination, `Rvalue::View(a)` never stores the dict's contents into the new object.
  - `alias.rs` treats `Rvalue::View(_)` as a fresh path that holds nothing.
  - `list(view)` and `sorted(view)` then copy that empty view, so the loss spreads to them.
- **Executed.** `pythrust check` accepts both of these (the base also accepted them, but only because it ran the analysis conservatively):
  ```python
  temper def f(d: dict[str, list[int]]) -> int:   # no `mut d`
      vs = d.values()
      for v in vs:
          v.append(1)
      return 0
  ```
  ```python
  temper def f(d: dict[str, list[int]]) -> list[int]:
      vs = list(d.values())
      return vs[0]                                  # D3: returns d's list
  ```
  - The same hole shows up with `it = d.items(); for k, v in it: v.append(1)`, with `list(d.items())[0][1].append(1)` and with `sorted(d.values())[0].append(1)`. All are accepted, and running them changes the caller's dict.
  - The legal neighbours behave correctly: `for v in d.values(): v.append(1)` asks for `mut d`, and `return d["a"]` is rejected under D3.
- **Fix:**
  - Make `elements_hold_list` (and the matching rule in `alias.rs`) cover `Compound::View`.
  - For an items view, model each element as a tuple that holds the value, not the value itself. Otherwise `Field(t, 1)` reads one level too deep.
- **Tests that should go red without the fix:** in `own/tests.rs`, each of the four programs above should be rejected, and the `for v in d.values()` neighbour should still need `mut`.

### 2. P1: native `d.get(k, default)` only evaluates `default` when the key is missing
- **Where:** `crates/pythrust-compiler/src/lower/table.rs:204-231`, line 223. The default is lowered inside the miss block, but Python evaluates every argument before the call.
- **Executed:**
  ```python
  def ins(d: dict[str, int]) -> int:
      d["z"] = 9
      return 0
  m = {"a": 1}
  print(m.get("z", ins(m)))      # CPython / run: 9      native: 0
  d = {"a": 1}
  def boom() -> int:
      return 1 // 0
  try:
      print(d.get("a", boom()))  # CPython / run: "raised"   native: 1
  except ZeroDivisionError:
      print("raised")
  ```
  `print(d.get("a", dflt(5)))` also loses `dflt`'s output in native code.
- **Fix:** evaluate `k` and then the default before the `Contains` branch, and on a miss move the already-computed default into the result.
- **Test:** a `NATIVE` corpus program whose default argument prints, raises and changes the dict.

### 3. P1: D3 and the shared-name rule are extended to dicts and sets against a recorded decision
- **The decision:** grammar.md:221 says "D3 and D4 stay list-only". The M6 card (Nano, 2026-10-31, roadmap) extends only D4 ("changing a container's size inside a `for`").
- **What the code now does (executed; the base accepted all three):**
  - `temper def f(d: dict[str, int]) -> dict[str, int]: return d` is rejected under D3.
  - `e = d; d["a"] = 1; return len(e)` is rejected under the shared-name rule.
  - `two(d, d)` against a tempered `mut a` is rejected.
- **Neither half of the docs was updated.** grammar.md:192 (Aliasing in tempered bodies) still says "list", and so does :221.
- **Consequence:** the language has no way to copy a dict (no `dict()` builtin, no `.copy()`). So a tempered function can never return a dict parameter, and no fix-it can work.
- **Fix:** either keep D3 and the shared-name rule list-only for dicts and sets, or get Nano's decision, record it in grammar.md and give a copy that actually works.

### 4. P2: the fix-its for dicts don't type-check, and the messages call dicts and sets "lists"
- **Where:** `own/alias.rs:683-718` and `own/calls.rs:232`. These were not adapted for dicts.
- **Executed:**
  - "return a copy: `return list(d)`": applying it gives `return type mismatch: expected dict[str, int], found list[T0]`.
  - "pass a copy (`list(...)`)": `two(d, list(d))` gives `expected dict[str, int], found list[T3]`.
  - "`e = list(...)`" fails the same way.
- **Wording:** messages say "changes a list while a `for` loop over it is running" for `ds[0]` (a dict), and "may also pass that list as `b`" for a dict.
- **Fix:** for a set, suggest `set(...)`. For a dict, say "dict" and give no copy hint until a copy exists.
- **Test:** for each message, apply the suggested fix and re-run `check`.

### 5. P2: `in` on a view with a needle of another type is refused natively but undocumented, and the refusal text is misleading
- **Where:** `lower/table.rs:241-256`, through `probe` and then `expect` (`lower/seq.rs:91-99`).
- **Executed:** `1 in {"a": 1.0}.values()` and `1.0 in {"a": 1}.values()` both work in `run`, matching CPython. `build` fails with "a value of another type than its list or tuple holds".
- **Why it matters:** native.md lists `in` as covered for dicts and views, and supports mixed needles for lists (`1 in [True]`). This refusal isn't in Known gaps, and the message talks about a list.
- **Fix:** handle mixed needles the way list `in` does, or record the gap with a message that names the view.

### 6. P2: coverage is missing for the changed behaviour
- There is no test for:
  - view aliasing (finding 1),
  - when the `get` default is evaluated (finding 2),
  - D3 and the shared-name rule on dicts and their fix-its (findings 3 and 4),
  - mixed-type `in` on views (finding 5).
- **Where they belong:** `own/tests.rs` for findings 1, 3 and 4; `NATIVE` corpus programs for findings 2 and 5.
- The new tests in the PR (own tests, codegen refusal tests, runtime table tests, `dict_view_changed_size`) do fail at the base, so they guard what they cover. I checked `dict_view_changed_size` directly: `run` at the base prints "not reached" and exits 0.

## Design questions for Nano
- **Stricter D4 in tempered code.** A store at an existing key inside a `for` over the dict is now a compile error. The roadmap only covers "changing a container's size", and grammar.md:134 says assigning an existing key in the loop is legal. The PR records this as a deviation, but it is a new rule nobody signed off.
- **D3 on dicts at all** (finding 3): should it apply before the language has a way to copy a dict?

## Optional improvements
- `sorted` with NaN gives a different order from CPython (`[3.0, nan, 1.0, ...]`). This already happens in `run` at the base, and native matches `run`, so it isn't introduced here. It is missing from Deviations, though.
- `d[True] += 1` on a `dict[int, V]` is refused natively even when the key already exists. It is refused by name, but Known gaps could mention it.

## Things I attacked that held up
- Type-sensitive generic programs: an empty `{}` or `set()` whose type is open, recursive results, an instance that never returns.
- Evaluation order of stores, displays and `+=`; the `KeyError` repr for tuple, `None` and empty-string keys.
- Bool keys: refused in every stored position, and only allowed where inference allows them.
- D4 through aliases and tempered callees; strings kept by keys and by `set.add`.
- Rebinding the dict inside its own loop, whether the loop takes its own reference, and cycle refusal.
- Order-blind `==`, nested reprs, sets of tuples.
- Native output matched `run` everywhere outside findings 2 and 5, with the debug runtime's leak check clean.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked` | 597 tests; everything passed except `corpus_matches_cpython` |
| `corpus_matches_native`, `corpus_matches_pythrust`, ownership-coverage and borrowed-parameter corpus tests | pass |
| `corpus_matches_cpython` | not run against CPython: the only interpreter here is Python 3.14, and the test requires 3.13 |
| CLAUDE.md Rust rules | met: no new `unwrap`/`expect` outside tests; `unsafe` only in `pythrust-rt/src/table.rs` under `allow(unsafe_code)` with `# Safety` notes; no new dependencies |

I built in `$TMPDIR` because the default target directory is read-only. I added a temporary worktree of the base for comparison and removed it afterwards.

**Validation gaps:**
- **CPython 3.13 parity:** not checked. Every comparison above used 3.14. Settle it with `PYTHON=python3.13 cargo test --locked --test conformance`.
- **Memory checks:** I didn't run valgrind (it isn't installed), so the roadmap's claim "clean under memcheck" is unverified. Settle it with the CI `memcheck` job (`PYTHRUST_VALGRIND=1`).
- **Other targets:** AArch64, macOS and Windows MSVC weren't run.

## Verdict: **changes required**
