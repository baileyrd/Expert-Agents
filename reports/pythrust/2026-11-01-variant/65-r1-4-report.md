<!-- pythrust/65-r1 run 4, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 488 s, 6091684 tokens, $3.23 -->

**Verdict: changes required.** I found two P1 defects (an ownership hole through dict views, and a native `d.get` that skips its default), a tempered-code rule that goes beyond Nano's plan, and four P2 defects.

- **Repository:** baileyrd/PythRust, PR #65
- **Base:** `5de8b6027190a0d8ba0e857cad338d7813fe0bc4`
- **Head reviewed:** `36dcec7f7e704b62e0d9f13d5800bc9e78c2180b` (fetched `pull/65/head` matches it)

The PR claims the *Native* half of the roadmap card "`dict` and `set`" (M6). It touches the runtime tables, the IR, lowering, codegen, `rc`, `own`, the interpreter's `for` over views, and the docs.

## Blocking findings

### 1. P1: a dict view hides the dict's values from the alias analysis, so D3 and D4 programs are accepted
- **Where:** `crates/pythrust-compiler/src/own/body.rs:547-552`. `elements_hold_list` only matches `Compound::List`, so the new `Rvalue::View(a)` case at `body.rs:326` never records what the view shares with the dict. A view's elements, and `list()` of a view, then point to nothing the dict holds. The alias side (`alias.rs:365`, `step`) reads the same empty contents.
- **Trace (executed):**
  ```python
  temper def pick(d: dict[str, list[int]]) -> list[int]:
      vs = list(d.values())
      return vs[0]
  temper def f(mut d: dict[str, list[int]]) -> int:
      a = pick(d)
      b = d["a"]
      b.append(1)
      return len(a)
  print(f({"a": [1]}))
  ```
  `check` passes; `run`, `build` and CPython all print `2`.
  - `pick` returns a list it shares with its parameter, which D3 forbids. `f` changes `b` while `a` is the same list and still used, which D4 forbids.
  - The direct form `for x in d.values(): return x` is rejected correctly. `vs = d.values(); for x in vs: return x` is accepted.
  - The list version (`vs = list(xs)`, `xs: list[list[int]]`) is rejected correctly, both for D3 and D4.
- **Fix:** treat a `View` destination like a list in `elements_hold_list` (`Compound::List(e) | Compound::View(_, e)`), so `View(a)`, and `CopyList` of a view, store the dict's contents.
- **Regression test:** in `own/tests.rs`, `d3_view` and `d4_view` above must be rejected, with `for x in d.values()` and `list(d)` as the accepted neighbours.

### 2. P1: native `d.get(k, default)` evaluates `default` only when the key is missing
- **Where:** `lower/table.rs:204-231`. The default is lowered inside the `miss` block (line 222). CPython and `run` evaluate every argument before the call.
- **Trace (executed):**
  ```python
  def dflt(n: int) -> int:
      print("dflt", n)
      return n
  d = {1: 10}
  print(d.get(1, dflt(5)))
  ```
  CPython and `run` print `dflt 5` then `10`; the native build prints only `10`. A default that raises (`d.get(1, int("x"))`) raises in `run` but not natively, so control flow differs from there on.
- **Fix:** evaluate the default operand before the `Contains` branch.
- **Regression test:** a corpus fixture with a printing default, added to `NATIVE`.

### 3. P1: tempered code now rejects stores at existing keys, which goes beyond Nano's plan
- **Where:** `own/alias.rs:650`. The rule is recorded in `grammar.md:134,173` and in the rewritten `roadmap.md:113`.
- **The recorded plan** (roadmap M6, "Nano, 2026-10-31", text at base): "D4 extended to dicts and sets in tempered code: *changing a container's size* inside a `for` over it is a compile error". `grammar.md:134` (from the front-end PR, decided on #64) says "assigning to an existing key inside the loop is legal".
- **Trace (executed):**
  ```python
  temper def bump(mut d: dict[str, int]) -> None:
      for k in d:
          d[k] = d[k] + 1
  ```
  `check` rejects this with "changes `d` while a `for` loop over it is running". The program never changes the dict's size and is legal in CPython.
- **What to do:** either implement the decided rule, or get Nano's sign-off on the stricter one. The PR rewrote the card text instead of asking.

### 4. P2: the D3, D4 and A5 fix-its for dicts and sets suggest `list(...)`, which does not type-check
- **Where:** `own/alias.rs:683,694,697,711`, `own/calls.rs:232`. The messages also call a dict a "list".
- **Trace (executed):** each suggestion applied and re-checked.
  - `return d` → "return a copy: `return list(d)`" → after applying: "return type mismatch: expected dict[str, int], found list[T0]".
  - `merge(d, d)` → "pass a copy (`list(...)`)" → after applying: "expected dict[str, int], found list".
  - `e = d; e["b"] = 2` → "`e = list(d)`" → after applying: "expected int, found str".
- There is no `dict` copy builtin (`dict(d)` gives "name `dict` is not defined"), so no correct fix-it exists yet.
- **Fix:** messages that depend on the container type, and tests in `own/tests.rs` that apply each fix-it.

### 5. P2: native `KeyError` holds the int, not the bool the program used
- **Where:** `lower/table.rs:107-113` and `137`. `probe` turns a `bool` into an int before `new_exc(ExcKind::Key, &[k])`.
- **Trace (executed):** `d = {1: "a"}; try: d[False] except KeyError as e: print(e)`. CPython and `run` print `False`; native prints `0`. An uncaught one prints `KeyError: 0`.
- **Fix:** build the exception from the key before conversion.
- **Regression test:** a line in `key_error` or a new fixture in `NATIVE`.

### 6. P2: the roadmap card is ticked without its exit test
- **Where:** `docs/roadmap.md:113`. The plan required "the `True`-is-`1` key test in both backends".
- The PR removes `by_int[True] = "true"` from `dicts.pyr`, moves it to a run-only `dict_bool_key`, and makes native storing a backend gap. The commit message itself says "pending Nano's call".
- The card should stay open, or record Nano's decision, until that test runs natively.

### 7. P2: docs now disagree with the code on D3, D4 and A5
- `grammar.md:221` ("D3 and D4 stay list-only"), `docs/research/ownership.md:20`, and roadmap A8 ("D3, D4 and A5 stay list-only") are now false: dicts and sets count as lists in all three checks (`own/mod.rs:328`).
- Only the D4 extension was in the plan; see design question 1.

### 8. P2: a misleading native refusal for membership in an empty set
- `s = set(); print(1 in s)` builds with "a value of another type than its list or tuple holds" (`lower/seq.rs:96`). The message names the wrong container, and the gap isn't recorded in `native.md` Known gaps. `run` prints `False`; `1 in {}` builds and prints `False`.
- Lists have the same gap (`xs = []; 1 in xs`), so this matches existing behaviour, but the message should name the type.

## Design questions for the owner
1. **D3 and A5 now apply to dicts and sets.** Was that intended? Only D4 was planned. With no dict copy builtin, a tempered function cannot follow "return a copy" for a dict parameter.
2. **Stores at existing keys inside a loop** (finding 3): adopt the stricter rule, or track the decided size-change rule?
3. **Bool keys in a `dict[int, V]`:** how should native code store them (for example, a per-entry first-key tag)? Until then `dict_bool_key` stays a refusal.

## Optional improvements
- `sorted` with NaN gives `[2.0, 3.0, nan, 1.0, nan, 0.5]` in `run` and native, while CPython's timsort gives `[3.0, nan, 1.0, 2.0, nan, 0.5]`. This difference was already in `run`, and native matches `run`. It isn't in the Deviations list.
- `may_cycle` checks `Compound::Set` even though its comment says set elements never can hold an exception. The code is harmless, but the comment and code should agree.

## Checks and results
- `cargo fmt --all --check`: passes.
- `cargo clippy --locked --all-targets -- -D warnings`: passes.
- `cargo test --locked --no-fail-fast`: compiler unit tests 536/536, the `pythrust` binary's unit tests 20/20, cli 5/5, vocabulary 6/6, runtime 36/36. Conformance 31/32: `corpus_matches_cpython` refuses to run because the only interpreter here is Python 3.14. The native corpus parity tests pass.
- **Tests at base:** I ran the head's `own/tests.rs` against a separate base checkout. `changing_a_dict_or_set_a_loop_runs_over_is_an_error` and `dict_stores_and_set_adds_are_mut_and_keep_what_they_store` both fail there, so they guard the change.
- **CLAUDE.md rules on new code:** no `unwrap()`/`expect()` outside tests (the `self.expect` hits are a lowering method), `unsafe` only in `pythrust-rt`, no new dependencies.
- **Probes (executed), where native matched `run` and CPython:** displays, lookups, nested dicts, tuple and optional keys, views, `sorted`, `set(xs)`, unpacking, `for` over views in generic code (size-change `RuntimeError`), rebinding the iterated dict inside the loop, and raise, return and break paths inside table loops. The debug runtime's leak counter reported no leaks. The only differences were the documented set order and a Python 3.14 message.

## Validation gaps
- **CPython 3.13 comparison not run:** run `cargo test --test conformance` with `PYTHON` pointing at a 3.13 interpreter.
- **Memcheck not run:** valgrind isn't installed, so the PR's "clean under memcheck" claim is unverified. Run `PYTHRUST_VALGRIND=1 cargo test --test conformance`.
- **Not run on macOS, Windows MSVC or Linux AArch64.**
- **Shared memory not used:** the remind-me tools CLAUDE.md requires weren't available in this session, so I didn't search or save to them. That was also consistent with the read-only remit.
