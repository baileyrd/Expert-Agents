<!-- pythrust/48-r1 run 2, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 283 s, 876364 tokens, $1.13 -->

I found three soundness holes, so the verdict is **changes required**. Probe traces below are *executed* with `pythrust check` at the head. The remind-me memory tools weren't available in this session, so nothing was searched or saved there.

**Repository:** baileyrd/PythRust · **PR:** 48 · **Base:** 5d76260eef62e1a3c7abfd48635a9a3e82a37650 · **Head:** 4771160b650e6a50edeaf6bc48d244050a69f057. I fetched `pull/48/head` and it matches the head SHA. The PR is one commit: "Check aliasing in tempered bodies (A6)".

## Blocking findings

**1. P1: a change through a value that could come from either of two names is never reported.** In `own/alias.rs:376`, the D4 check skips any variable the changed value was read from. A conditional or `or` reads from two variables, so both are skipped (the tracking that causes this is `alias.rs:143-171`).
```python
temper def f(c: bool) -> int:
    a = [1]
    b = a
    (a if c else b).append(2)      # same with (a or b).append(2)
    return len(a) + len(b)
print(f(True))
```
- `check` accepts this (exit 0) and `run` prints 4.
- The same program with a plain `a.append(2)` is rejected with "changes `a` while `b` may be the same list".
- D4 forbids this: two live names for one list, changed through one of them.
- **Fix:** skip a variable only when it is the only source of the value. If the value has several sources, any other name that is still used must still count.
- **Regression test:** an `own/tests.rs` case beside `a_second_name_changed_while_the_first_is_used_is_an_error` with both forms above, expected to be rejected.

**2. P1: an unrelated lowering gap turns off D3 and D4 for every tempered function.** When the whole program doesn't lower, `own/mod.rs:104-108` leaves the aliasing results empty, so `modes::check` reports nothing.
```python
temper def same(xs: list[int]) -> list[int]:
    return xs                       # D3 violation
def other(xs: list[int]) -> None:
    raise ValueError(xs)
print(same([1]))
```
- `check` accepts this (exit 0). Without `other`, the same file is rejected.
- A tempered body can reach the gap itself. This is accepted (exit 0), although the `b.append` breaks D4:
  ```python
  temper def f(n: int) -> int raises ValueError:
      a = [1, 2]
      b = a
      b.append(n)
      if n > 100:
          raise ValueError(a)
      return len(a)
  ```
- `docs/roadmap.md:74` marks A6 done. Its exit criterion says A6 "closes any other lowering gap a tempered body can reach instead of rejecting it", but this gap is still open.
- **Fix:** close the `ValueError(<list>)` lowering gap, or check tempered bodies whenever their own body lowers. Until then, A6 should stay unticked.
- **Regression tests:** both programs above as `own/tests.rs` cases, or as `# expect-error:` corpus files.

**3. P1: a global used after a change through a local name for it is not seen.** The "still used later" check only looks at local variables (`alias.rs:309-317`, `361-386`). A later read of a global is not a local that is still in use.
```python
G = [1]
temper def f() -> int:
    b = G
    b.append(2)
    return len(G)
print(f())
```
- `check` accepts this (exit 0).
- By contrast, `for x in G: G.append(x)` is caught, and so is a case using two locals that both hold `G`.
- **Fix:** treat any later read of a global in the body as a later use of the module's lists (the analysis's `Obj::World`).
- **Regression test:** the program above, expected to be rejected.

**4. P2: the D3 fix-it fails on nested lists.** For `temper def f(rows: list[list[int]]) -> list[list[int]]: return rows`, the error suggests `return list(rows)`. Applying it gives a new error: "returns a value sharing a list with its parameter `rows`; return a copy made with `list(...)`". `return list(list(rows))` is rejected the same way, so neither suggestion can be followed (`alias.rs:412-416`).
- **Fix:** when the parameter holds lists, suggest copying each inner list.
- **Regression test:** apply the fix-it to the nested case and expect `Ok`, the way `tempered_code_returns_no_parameter` already does for `list[int]`.

**5. P2: the D4 fix-it gets the copy backwards when the change goes through the original name.** It always suggests `<changed> = list(<other>)` (`alias.rs:392-394`).
- When the other name is an optional (`b: list[int] | None`, `b = a`, then `a.append(2)`), it suggests `a = list(b)`. Applying it gives a type error: "`list` copies a list: expected list[T3], found list[int] | None".
- Even for plain lists, the decided fix-it copies at the alias (`b = list(a)`), not at the changed name.
- **Fix:** suggest copying at the second name, i.e. the alias.
- **Regression test:** the optional case, with the fix-it applied, expected to check cleanly.

**6. P2: an error about a for loop where there is no loop.** Any unnamed temporary that clashes is reported as a running `for` loop (`alias.rs:374-382`). For `return h(rows[0], grow(rows))`, where `grow` takes `mut rows`, the error at `grow(rows)` says "changes a list while a `for` loop over it is running".
- **Fix:** give this message only for a loop's sequence temporary. Otherwise name the variable the temporary came from.
- **Regression test:** this program with its expected message.

**7. P2: the module doc points to code that doesn't exist.** `alias.rs:15` says "Passing one list twice to a call is A5's check (`own::calls`)". There is no `own::calls`, and A5 is still unticked in the roadmap. The doc should say A5 is pending.

## Design questions (for the owner, not findings)
- **Widened D3.** The PR adds a rule to D3: returning any list inside a parameter also counts (`grammar.md`, D3 "Implemented" note). The recorded decision only covers returning the parameter itself (`return xs`). Together with finding 4, a tempered function can no longer return a shallow copy of a nested list.
- **What counts as a name.** In the D4 note, a tuple or outer list that holds the changed list isn't counted as another name for it. For example, `t = (b, 1); b.append(1); print(t)` is accepted. That reading also isn't recorded as Nano's decision.

## Optional improvements
- The roadmap entry for A6 could list the remaining reachable gaps rather than calling the probe complete.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked` | pass: 416 + 19 + 27 + 29 + 0 |
| Native `build` of `examples/aliasing.pyr` on Linux x86-64 | output matches `.out` and `python3` (with `temper`/`mut` removed) |
| New corpus `aliasing_shared` / `aliasing_return` | would fail at base, since base has no D3/D4 check, so they do guard the change |
| New code against CLAUDE.md rules | no `unwrap`/`expect`/`unsafe`, no new dependencies |

## Validation gaps
- **Python version:** only CPython 3.14.8 is installed, not 3.13. The conformance run passed against 3.14; a run with `PYTHON` set to a 3.13 interpreter would settle it.
- **Memcheck:** valgrind isn't installed, so the new `NATIVE` entry hasn't been run under memcheck.
- **Other targets:** AArch64, macOS and Windows MSVC weren't run.

**Verdict: changes required**
