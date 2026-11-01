<!-- pythrust/48-r1 run 1, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 270 s, 1039604 tokens, $1.12 -->

# Review of PR 48 (baileyrd/PythRust): tempered-body aliasing check (A6). Verdict: changes required

The PR adds the D3 (returned alias) and D4 (aliased mutation) checks for tempered functions. I found five tempered programs that the PR's own rules forbid but `check` accepts. I ran each one with `pythrust check` and `pythrust run`, and with CPython after removing `temper`/`mut`; both runtimes printed the same output. The PR also marks roadmap step A6 done when its exit condition isn't met. The probes are in `/tmp/claude-1000/rv48.7xBt/`.

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head:** `4771160b650e6a50edeaf6bc48d244050a69f057`. `git fetch origin pull/48/head` returned exactly this SHA (one commit, "Check aliasing in tempered bodies (A6)").

## Blocking findings

**1. P1: an unrelated lowering gap switches off D3/D4 for the whole program, and A6 is marked done although the exit condition fails.**
- **Where:** `own/mod.rs:104-108` (the fallback path sets `aliasing: BTreeMap::new()`), `docs/roadmap.md:74`, and the gap itself at `lower/expr.rs:86`.
- **Trigger:** list `+` or `*` anywhere in the program. Lowering rejects it with "arithmetic on a non-number", so the program falls back to conservative summaries and every alias check is skipped.
- **Executed:**
  ```python
  temper def f(n: int) -> int:
      a = [1, 2]
      b = a
      b.append(n)
      return len(a + [])
  print(f(3))
  ```
  - `check` accepts it and `run` prints `3`.
  - Without `+ []`, `check` reports the expected D4 error.
  - A loose `def g(xs): return xs + xs` elsewhere in the program does the same thing (`gate_loose.pyr`).
  - `temper def f(xs: list[int]) -> list[int]: return xs * 1` is accepted too (`rep.pyr`).
- **Doc claim this falsifies:** A6's exit condition requires closing "any other lowering gap a tempered body can reach". The roadmap entry says a probe "found no further lowering gap; the one left is … `ValueError(xs)`". List concatenation and repetition are gaps a tempered body can reach.
- **Fix:** lower list `+` and `*` as new lists, and model them in `body.rs` (fresh list, with the operands' elements stored in it). Separately, decide whether a gap in one function should leave other, fully lowered tempered bodies unchecked. Until then, un-tick A6 and correct the roadmap text.
- **Regression test:** an `own/tests.rs` case where `alias_err` on the program above returns `Some`.

**2. P1: D4 misses changes made through a conditional, `and` or `or` expression.**
- **Where:** `own/alias.rs:376` together with the `roots` computation at `alias.rs:143-171`.
- **Why it happens:** the temporary for `(a if flag else b)` has roots `{a, b}`. Both live names are excluded as "the name being changed", so nothing is left to clash.
- **Executed:**
  ```python
  temper def f(flag: bool) -> int:
      a = [1]
      b = a
      (a if flag else b).append(2)
      return len(a) + len(b)
  ```
  `check` accepts it; `run` and CPython both print `4`. `(b or a).append(n)` is accepted the same way (`orexpr.pyr`). Writing `b.append(2)` instead is rejected.
- **Fix:** exclude another name only when it is provably the single name the change goes through. When a temporary's roots contain more than one name, any other live root that may be the same list must clash.
- **Regression test:** the program above, with a legal neighbour where `b = list(a)` stays accepted.

**3. P1: two different tuple fields are assumed to be different lists, which is unproven.**
- **Where:** `same()` at `alias.rs:442-454` together with `Step::Field` at `alias.rs:223`.
- **Why it happens:** `Path([Field(0)])` and `Path([Field(1)])` of the same object never match. The tuple's contents are unknown when it comes from a parameter or a call result, because `Flow::call_result` stores nothing inside a fresh result.
- **Executed (`tuple_ret.pyr`):**
  ```python
  temper def pair() -> tuple[list[int], list[int]]:
      a = [1]
      return (a, a)
  temper def g() -> int:
      t = pair()
      x = t[0]
      y = t[1]
      x.append(2)
      return len(y)
  ```
  `check` accepts it; `run` and CPython both print `2`.
- **Second case (`tuple_param.pyr`):** `temper def f(mut p: tuple[list[int], list[int]])` doing `x = p[0]; y = p[1]; x.append(1); return len(y)`, called as `f((a, a))`, is also accepted. A5 can't catch this, because the call passes only one argument.
- **Control:** `y = x` instead of `y = t[1]` is rejected.
- **Fix:** paths that differ only in their field indices must count as possibly the same list, unless the body built the tuple itself and the fields' stored sets are disjoint.
- **Regression test:** both programs above.

**4. P1: a parameter is assumed never to be a module global's list.**
- **Where:** `Obj::Param(i)` versus `Obj::World` (`alias.rs:178`, `225`, `443`).
- **Executed (`glob.pyr`):**
  ```python
  G = [1]
  temper def f(mut xs: list[int]) -> int:
      g = G
      xs.append(1)
      return len(g)
  print(f(G))
  ```
  `check` accepts it, though `xs` and `g` are two live names for one list. Both runtimes print `2`. A5's planned check would not catch this either, since the call passes only one argument.
- **Fix:** treat `Param` and `World` as possibly the same list in `same()` and `overlaps()`. Alternatively, record a decision that callers may not pass a global to a function that reads that global; that is a new rule and needs the owner's sign-off.
- **Regression test:** `glob.pyr` rejected, with a version that reads no global staying accepted.

**5. P2: D3 fix-it messages that fail or change meaning when applied.**
- **Where:** `alias.rs:412-416`.
- **(a) Nested lists (executed):** `temper def f(rows: list[list[int]]) -> list[list[int]]: return rows` suggests `return list(rows)`. Applying it gives a new error, "returns a value sharing a list with its parameter `rows`" (`copy_fix0.pyr`, then `copy_fix.pyr`).
  - D3 at `grammar.md:210` fixes the fix-it as `return list(xs)`, a shallow copy.
  - The PR's added sentence ("a value that holds … a list inside one counts") means that fix-it can never succeed for nested lists. That rule is also a design question (see below).
- **(b) Wrong expression (executed, `retdirect.pyr`):** in `out = [0]; if flag: out = xs; return out`, the message suggests `return list(xs)`. Applying that literally returns `xs`'s contents even when `flag` is false, which changes what the function does. The suggestion should copy the returned expression: `return list(out)`.
- **Regression tests:** apply each fix-it in `tempered_code_returns_no_parameter` and re-check the result.

## Design questions for the owner
- **Shallow copies of nested lists:** should D3 reject `return list(rows)` for `list[list[int]]`? D3 was decided with a shallow-copy fix-it. The PR widened it silently (`grammar.md:210`), and there is no deep-copy builtin to offer instead.
- **Parameter distinctness:** D4 inside a body assumes different parameters are different lists and leaves that to A5. A5 is still unticked, so `f(xs, xs)` against a `mut` parameter is accepted today. Should A6 count as done before A5 lands?

## Optional improvements
- The "for loop" wording is used for any live unnamed temporary that clashes, not only a loop's sequence. No probe of mine produced a wrong message, but tagging the loop-sequence local explicitly would make the message reliable.

## Checks run
| Check | Result |
|---|---|
| `cargo fmt --all --check` | passed |
| `cargo clippy --locked --all-targets -- -D warnings` | passed |
| `cargo test --locked` | passed (416 + 19 + 27 + 29 tests, 0 failures) |
| `cargo test --locked --test conformance` | 27/27 passed, including `corpus_matches_cpython`, `corpus_matches_native` and `corpus_ownership_coverage` |

- The new code in `alias.rs` has no `unwrap`, `expect` or `unsafe`, and the PR adds no dependencies.
- The new unit tests can't compile at the base commit (the `alias` module doesn't exist there), so they do guard the change. None of them covers findings 1 to 5.

## Validation gaps
- **CPython version:** the local `python3` is 3.14.8, not 3.13. Rerun the conformance suite under 3.13 (`PYTHON=python3.13`).
- **Memcheck:** valgrind isn't installed, so I couldn't run memcheck on the native build of `aliasing`. The native-side change is only the `Append` pattern gaining a `span` field.
- **Other targets:** AArch64, macOS and Windows MSVC were not run.

CLAUDE.md asks for the remind-me memory tools, but none were available in this session. Saving to them would also go beyond this review's read-only scope, so I skipped them.

**Verdict: changes required.**
