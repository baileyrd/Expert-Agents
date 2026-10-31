<!-- pythrust/65-r1 run 4, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 335 s, 4067794 tokens, $2.56 -->

I've requested changes on PR 65 (baileyrd/PythRust, base `5de8b60…`, head `36dcec7f7e704b62e0d9f13d5800bc9e78c2180b`; the fetched head matches). There are three blocking findings, all traced by reading the source. I couldn't execute anything: the disk quota filled up, so the build, the probes and every check failed to run.

## Blocking findings

### 1. P1 (ownership soundness): a view held in a variable loses track of the dict's lists, so D2, D3 and D4 miss changes made through it
- **Where:** `crates/pythrust-compiler/src/own/body.rs:326`, with `elements_hold_list` at `:547-552`. `own/alias.rs:365` has the same gap, because `step` reads `flow.stored`.
- **Cause:** for `Rvalue::View(a)`, the analysis only links the view to the dict's contents when `elements_hold_list(dest)` is true. That function only recognises `Compound::List`, and a view's type is `Compound::View`. So `vals = d.values()` becomes a new list with nothing stored in it. Reading from it (`Index`) gives back only that new list, never the dict's value lists. `list(d.values())` and `sorted(d.items())` lose the link the same way, because they copy from the disconnected view.
- **Failure (source-traced):**
  ```python
  temper def f(d: dict[str, list[int]]) -> int:
      vals = d.values()
      for xs in vals:
          xs.append(1)
      return len(d)
  print(f({"a": [0]}))
  ```
  The change to `xs` lands on the view's own list, which is outside `d`'s region. So `d`'s mode comes out as `read` (`body.rs:427-434`) and `modes.rs:104` accepts the function without `mut d`. It should reject with "tempered `f` changes `d` in place; write `mut d`".
  - **D4 version:** `for x in d["a"]: for ys in vals: ys.append(x)` grows the list being looped over and is accepted.
  - **D3 version:** `return d.values()` is not reported as sharing lists with the parameter.
  - Loose callers inherit the wrong `read` summary.
- **Fix:** make `elements_hold_list` also match `Compound::View(_, e)`.
- **Regression test:** add one to `own/tests.rs`: the program above must fail with that mode error, and the same body with `mut d` must stay accepted. Add a `list(d.items())[0][1].append(1)` case too.

### 2. P1 (backend parity): native `d.get(k, default)` only evaluates `default` when the key is missing
- **Where:** `crates/pythrust-compiler/src/lower/table.rs:221-224`. The default is lowered only inside the `miss` block.
- **Failure (source-traced):**
  ```python
  def noisy() -> int:
      print("called")
      return 0
  d = {1: 2}
  print(d.get(1, noisy()))
  ```
  CPython and `run` evaluate arguments before the call (`interp/methods.rs` binds the method, then the call evaluates its arguments) and print `called` then `2`. The native build prints only `2`. If the default raises (for example `xs[5]`), native skips the exception and the rest of the program diverges.
- **Fix:** evaluate the default before the `Contains` branch and release it on the hit path.
- **Regression test:** a corpus fixture in `NATIVE` with a default that has a side effect, hit on the key.

### 3. P2 (backend parity): a missing `bool` key's `KeyError` shows `1` natively but `True` in `run`
- **Where:** `lower/table.rs:128` and `:137`. `probe` converts the bool to an int before `new_exc(ExcKind::Key, &[k])`. `interp/mod.rs:871-872` keeps the original key, as CPython does.
- **Failure (source-traced):** `d = {2: "x"}` then `print(d[True])`. CPython and `run` say `KeyError: True`; build says `KeyError: 1`. `d[True] += 1` on a missing key does the same.
- **This contradicts `docs/native.md`**, which says reading with a bool "builds: it is the int it equals, as in `run`".
- **Fix:** build the `KeyError` from the key before conversion.
- **Regression test:** extend `key_error.pyr`, which is in `NATIVE`.

## Design question for Nano
- **Stricter D4 in tempered code:** the grammar says assigning to an existing key inside a `for` is legal. The roadmap card only planned to make changing a container's size in tempered code a compile error. This PR also rejects stores that keep the size, such as `d[k] = d[k] + 1`. It's recorded in Deviations as "tempered code is stricter", but I can't find Nano's approval for it (CLAUDE.md: "do not decide silently").

## Optional improvements
- Refusing `s.add(True)` on a `set[int]`, or a `(True, 1)` key on a `dict[tuple[int, int], V]`, would give the message "a value of another type than its list or tuple holds". A table-specific message, matching the one dict keys get, would be clearer. I haven't confirmed that inference lets these through.
- The refusal of a `bool` key on `dict[int | None, V]` (lookups included) isn't mentioned in native.md Known gaps.

## Checks and validation gaps
- **Not run:** `cargo fmt --check`, `cargo clippy`, `cargo test` and the conformance test. `cargo build` failed with "Disk quota exceeded" partway through, so I didn't run the rest. Later shell commands failed on the quota too.
- **Not done:** I couldn't run the new tests against the base commit. Reading the source, the D4 dict/set test should fail there, because the base refused to lower dicts.
- **Not seen:** memcheck, Windows, macOS and AArch64 results.
- **CPython:** the machine has `python3` 3.14.8, not 3.13. I only used it for spot checks.
- **Rust rules in the new code:** no `unwrap` or `expect` outside tests. The only `unsafe` is in `pythrust-rt`'s C ABI (`table.rs`, with an `allow`), and no new dependencies.
- **Memory tool:** CLAUDE.md asks for the `remind_me` memory tools, but they weren't available in this session, so nothing was searched or saved.

**Disk space:** my build attempt left a 329 MB `target/` directory in the project, and the per-user quota is full. I didn't delete it or anything in `/tmp/claude-1000`. Removing `target/` would free that space if you want to re-run the checks.

**Verdict: changes required.**
