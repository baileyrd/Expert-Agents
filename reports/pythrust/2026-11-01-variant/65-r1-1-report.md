<!-- pythrust/65-r1 run 1, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 420 s, 4721575 tokens, $2.80 -->

# Review of PR 65 (baileyrd/PythRust): native dicts, sets and dict views

- **Base:** `5de8b6027190a0d8ba0e857cad338d7813fe0bc4`
- **Head:** `36dcec7f7e704b62e0d9f13d5800bc9e78c2180b`. I fetched `pull/65/head` and it matches this SHA.
- **Verdict: changes required.** One P1 soundness hole and one P2 native gap that the docs don't mention.

## Blocking findings

### 1. P1: the `own` analysis loses everything reached through a stored view, `list(view)` or `sorted(view)`, so D2, D3 and the `owned` rule all miss

**Where:** `crates/pythrust-compiler/src/own/body.rs:326`, with `elements_hold_list` at `:547`.

**Cause:** `Rvalue::View(a)` is only linked to the dict's contents when `elements_hold_list(dest)` is true. That function accepts only `Compound::List`, so a view of type `View(_, list…)` never gets the dict's contents. Indexing the view then yields only the fresh view object. `list(view)` and `sorted(view)` then copy that unrelated object, so they lose the dict's contents too. The `alias.rs` `step` and the `strs.rs` keep tracking both read this flow, so all three rules below miss. Loops written directly over `d.values()` or `d.items()` go through `Entry` instead and are caught. So the hole is only in the new `View` rvalue.

**What it breaks (all executed with `pythrust check` at the head):**

- **D2, `mut` not required.** This is accepted, but it should be rejected with "write `mut d`":
  ```python
  temper def f(d: dict[str, list[int]]) -> None:
      it = d.items()
      for k, v in it:
          v.append(1)
  ```
  The same happens with `ps = list(d.items()); ps[0][1].append(1)`. The inline neighbours `for k, v in d.items():` and `for v in d.values():` are correctly rejected.
- **D3, aliasing not detected.** These are accepted, but should give "this call may also pass that list as `xs`":
  ```python
  temper def f(mut d: dict[str, list[int]], xs: list[int]) -> int:
      d["a"].append(len(xs))
      return len(xs)
  x = {"a": [1]}
  vs = x.values()
  for v in vs:
      print(f(x, v))
  ```
  The same happens with `y = list(x.values()); f(x, y[0])` and with `it = x.items(); for k, v in it: f(x["a"], v)`. The list analogues (`list(x)` then `y[0]`, `x["a"]`, `x.get(0)`, inline `x.values()`) are all rejected.
- **`owned` not required.** This is accepted, but should give "write `owned s`":
  ```python
  temper def f(mut d: dict[str, list[str]], s: str) -> None:
      vs = d.values()
      for v in vs:
          v.append(s)
  ```
  The same function looping over inline `d.values()` is rejected.

**Required correction:** link a view's contents to its dict's contents in `Flow::assign`, for example by making `elements_hold_list` accept `Compound::View(_, item)` when `holds_list(item)` is true. Also make sure `CopyList` from a dict, set or view carries the source's contents.

**Regression tests (in `own/tests.rs`):** each of the five programs above must be rejected. Keep the legal neighbour accepted: `f(x, list(v))` over a stored `x.values()`, which I checked is accepted today.

### 2. P2: `in` on a view with a needle of another type is refused natively, though `run` accepts it and `native.md` lists `in` on views without a caveat

**Where:** `crates/pythrust-compiler/src/lower/table.rs:250`, where `table_contains` sends the needle through `probe` and then `expect`.

**Trace (executed):**
```python
d = {1: 1.0}
v = d.values()
print(1 in v, True in v)
```
- CPython and `run` print `True True`.
- `build` fails with "backend error: … does not support a value of another type than its list or tuple holds yet".

Lists natively handle a needle of another type element by element, as `==` does (`docs/native.md`, Covered now). Views are compared slot by slot like lists, but they don't get that path. `docs/native.md:25` says views support `in` without exception, and the Known gaps section doesn't list this.

**Required correction:** for a `Ty::View` hay, use the list membership path (`pt_list_contains` with the mixed-needle handling). Otherwise, record the gap in Known gaps.

**Regression test:** add `1 in d.values()` for a float-valued dict, and `True in v`, to a `NATIVE` fixture.

## Design questions for the owner

- **D4 now rejects stores at an existing key.** In tempered code, `for k in d: d[k] = d[k] + 1` is now a compile error. The roadmap card asked only for "changing a container's size inside a `for` over it" to be an error. The PR goes further and records the stricter rule as a deviation (`grammar.md:134`, `:173`), but Nano wasn't asked first. This is the most common dict-update pattern, so it needs Nano's decision (CLAUDE.md: "do not decide silently").
- **The bool-key refusal is reasonable.** Storing a `bool` key into a `dict[int, V]` is refused by name, recorded in Known gaps and labelled pending Nano's call. The refusal is narrow: inference already rejects `bool` in `set[int]`, tuple keys and optional keys, as I probed. Nano still needs to make that call.

## Optional improvements

- The refusal message for generic `set()` and `{}` filled later says "list or tuple holds". It should name dicts and sets as well. The gap itself predates this PR; the list analogue fails the same way.
- No unit test in `lower/tests.rs` asserts the bool-key refusal message. The corpus harness only accepts the refusal implicitly.

## Checks performed

| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked` | 536 lib tests and the other suites pass, including `corpus_matches_pythrust` and `corpus_matches_native`. `corpus_matches_cpython` fails only because the oracle is Python 3.14 and the corpus is written against 3.13. |
| New `own` tests run against the base tree | `changing_a_dict_or_set_a_loop_runs_over_is_an_error` and `dict_stores_and_set_adds_are_mut_and_keep_what_they_store` fail there, so they do guard the change. |

**Probes I ran across CPython 3.14, `run` and native (debug runtime, so leaks are checked at exit):** duplicate keys in displays, evaluation order of `d[k] = v`, 100k-key growth, `sorted` of strings, tuples, ranges, tables, views and NaN, optional and tuple keys, `KeyError` caught and uncaught (including from `+=`), stored-view snapshots (a recorded deviation), nested dicts, `get`, and shadowing of `set` and `sorted`. I also ran a refcount stress program covering `break`, `continue`, `return` and `raise` inside dict loops, value replacement mid-loop, views, `get` defaults and set-of-tuple adds. Native output matched `run` everywhere it built, with no leaks reported. The only `run`/CPython differences were set order, which is a recorded deviation.

**Rust rules:** the new code has no `unwrap` or `expect` outside tests, no new dependency, and `unsafe` only in `pythrust-rt`'s C ABI (`table.rs` carries `#![allow(unsafe_code)]` with SAFETY notes).

## Validation gaps

- **memcheck:** valgrind isn't installed here, so the roadmap's claim that the fixtures are "clean under memcheck" is unchecked. To settle it, run `PYTHRUST_VALGRIND=1 cargo test --test conformance corpus_matches_native`.
- **CPython 3.13 oracle:** run `corpus_matches_cpython` with `PYTHON` set to a 3.13 interpreter.
- **Other targets:** I didn't see results for AArch64, macOS or Windows MSVC.

## Housekeeping

- The remind-me memory tools that CLAUDE.md requires weren't available in this session, so I didn't search or save anything to memory.
- For the base comparison I created a temporary git worktree and removed it afterwards. The reviewed tree is unchanged.
