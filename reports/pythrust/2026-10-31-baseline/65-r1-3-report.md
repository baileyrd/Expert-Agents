<!-- pythrust/65-r1 run 3, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 374 s, 4814927 tokens, $2.58 -->

I can't approve this PR yet: I found two P1 defects and three P2 defects. All five come from programs I ran.

**PR:** baileyrd/PythRust #65, base `5de8b6027190a0d8ba0e857cad338d7813fe0bc4`, reviewed head `36dcec7f7e704b62e0d9f13d5800bc9e78c2180b`. `git fetch origin pull/65/head` returned exactly that head.

## Blocking findings

### 1. P1: The ownership analysis loses a dict's values once they pass through a view, so both the `mut` rule (D2) and the aliasing rule (D3) can be broken
- **Where:** `crates/pythrust-compiler/src/own/body.rs:547-551`. `elements_hold_list` only recognises `Compound::List`. The new `Rvalue::CopyList(a) | Rvalue::View(a) if self.elements_hold_list(dest)` arm (body.rs:326) therefore never fires for a view. A view records nothing about its dict, and `list(view)` holds only the view object.
- **Trigger:** a tempered function reads a dict's values or items through a view it stores in a variable, or through `list(d.values())` / `list(d.items())`.
- **D2 (executed).** `pythrust check` accepts this, but `f` changes `d` without `mut d`:
  ```python
  temper def f(d: dict[str, list[int]]) -> None:
      vs = d.values()          # also: items = d.items(); list(d.items()); list(d.values())
      for v in vs:
          v.append(1)
  ```
  The direct forms `for v in d.values():` and `for k, v in d.items():` are correctly rejected with "write `mut d`". So these are four accepted programs next to rejected neighbours.
- **D3 (executed).** This is accepted:
  ```python
  temper def g(mut a: list[int], b: list[int]) -> int:
      a.append(1)
      return len(b)
  temper def f(mut d: dict[str, list[int]]) -> int:
      ys = list(d.values())
      return g(d["a"], ys[0])
  ```
  The list version (`ys = list(xs)` then `g(xs[0], ys[0])`) is rejected with "this call may also pass that list as `b`".
- **Why it's this PR's:** base gave no ownership facts for dict bodies at all ("conservative"; base accepted even the direct forms). This PR adds those facts and marks "the `own` and `rc` facts for dict stores" done in the roadmap.
- **Fix:** make `elements_hold_list` (or the `View` arm) also cover `Compound::View` items and dict/set elements, so a view's contents are the dict's contents.
- **Regression tests (`own/tests.rs`):** the `vs = d.values()` and `list(d.items())` mode errors, and the `g(d["a"], ys[0])` alias error. Each should sit next to the accepted direct `for v in d.values()` form.

### 2. P1: Native `d.get(k, default)` only evaluates `default` when the key is missing; `run` and CPython always evaluate it
- **Where:** `crates/pythrust-compiler/src/lower/table.rs:204-230`. The default is lowered only in the `miss` block (lines 222-225), after the lookup.
- **Trigger:** any `get` whose default has an effect: a print, a raise, or a change to the program's state.
- **Executed:**
  ```python
  d = {1: 10}
  def f() -> int:
      d[1] = 99
      return 0
  print(d.get(1, f()))
  ```
  CPython and `run` print `99`; the native binary prints `10`. With a printing `f()`, native also drops the "default evaluated" line, and a default that raises would not raise.
- **Why P1:** native code silently runs the program differently instead of refusing it, and the effect goes beyond one result.
- **Fix:** evaluate the key, then the default, before the lookup (CPython's argument order); keep only the choice between them conditional.
- **Regression test:** a `NATIVE` corpus fixture with a side-effecting default; it goes red at head.

### 3. P2: The roadmap marks the native card done although one of its stated exit tests was dropped
- **Where:** `docs/roadmap.md:113` (the card is now `[x]`) and `examples/dicts.pyr:62-63`.
- The card at base required "the `True`-is-`1` key test in both backends". The PR removes `by_int[True] = "true"` from `dicts.pyr` so the fixture builds natively. It moves that case to the run-only `dict_bool_key` and makes storing it a backend gap; the commit message says this is "pending Nano's call".
- **Fix:** leave that part of the card open, or record Nano's decision, before checking it off.

### 4. P2: `1 in {True}` is refused natively with a message about lists and tuples, and the gap isn't documented
- **Where:** `lower/table.rs:248-251` (`probe` → `expect`), whose message comes from `lower/seq.rs:96`.
- **Executed:** `s = {True}` then `print(1 in s)`. CPython and `run` print `True`. `build` fails with "the native backend does not support a value of another type than its list or tuple holds yet".
- `docs/native.md` says `in` on dicts and sets builds, and Known gaps lists only the bool-store case.
- **Fix:** make the int-needle-into-bool-element case build, or refuse it with a dict/set-specific message and add it to Known gaps. Add a test either way.

### 5. P2: The D4 message calls a dict "a list"
- **Where:** `own/alias.rs:666` (`map_or("a list".into(), …)`), newly reachable for dicts and sets.
- **Executed:** a tempered loop `for k in d: grow(d)` over a `dict[int, int]` reports "tempered `f` changes a list while a `for` loop over it is running". The same happens for `w[0][5] = 1` inside `for k in w[0].items()`.
- **Fix:** name the container kind. Add a test asserting the full message for an unnamed dict.

## Design question for the owner (not a finding)
- **How strict D4 is for dicts.** Base grammar says "assigning to an existing key inside the loop is legal", and the roadmap Nano set said D4 covers "changing a container's size". The PR rewrites both the grammar and the roadmap card so tempered code also rejects a store at an existing key. That means `for k in d: d[k] += 1`, which is legal in CPython and in loose code, becomes a tempered error. It mirrors lists and is sound, but it's a new language rule decided in the PR; it should be put to Nano rather than settled silently.

## Optional improvements
- `sorted` with NaNs gives a different order from CPython in `run`, and now identically in native. That was already true at base and isn't listed in the deviations; it's worth recording or fixing separately.
- Add an own-stage test for `set.add` keeping a `str` element, alongside the dict key test.

## Checks and validation gaps
- `cargo fmt --all --check`: passes.
- `cargo clippy --locked --all-targets -- -D warnings`: passes.
- `cargo test --locked`: everything passes (lib 536, rt 20, cli, and 31 of 32 conformance tests, including `corpus_matches_pythrust` and `tempered_matches_loose`) except `corpus_matches_cpython`. That one stops itself because the only `python3` here is 3.14, not 3.13. I ran my probes against 3.14.
- **Rust rules:** I read the new non-test code and found no `unwrap`/`expect` and no `unsafe` outside `pythrust-rt` (`table.rs` carries the `allow(unsafe_code)` and `# Safety` notes). No new dependency.
- **Parity probes that matched** CPython, `run` and native, with the debug runtime's leak counter reporting nothing:
  - refcount stress (overwritten values, views, nested dicts, a caught `KeyError`, `sorted`/`set()`/`list()` of tables, unpacking, tuple and `None` keys)
  - order-blind `==`
  - generic functions over dicts and sets
  - set size-change `RuntimeError` and unpacking arity
- **Validation gaps:**
  - **Memcheck:** no valgrind on this machine; run `PYTHRUST_VALGRIND=1 cargo test --test conformance`.
  - **CPython 3.13 corpus comparison:** run with `PYTHON` set to a 3.13 interpreter.
  - **Other targets:** I couldn't check macOS, Windows MSVC or AArch64.
  - **New tests at base:** I couldn't build them because the temp volume ran out of quota. That space is held by other sessions' directories, which I didn't touch. From the base binary I'd built earlier, base reported nothing for dict bodies, so the new D4 and mode tests should fail there.
  - **`KeyError` inside loops under the leak counter:** that probe hit the same quota and never ran.
- `CLAUDE.md`'s remind-me memory tools aren't available here, so I couldn't search or save prior context.

**Verdict: changes required**
