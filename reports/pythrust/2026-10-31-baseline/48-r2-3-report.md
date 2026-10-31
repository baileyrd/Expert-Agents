<!-- pythrust/48-r2 run 3, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 269 s, 1354558 tokens, $1.25 -->

# PR #48 review (baileyrd/PythRust): tempered aliasing checks (A6)

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head reviewed:** `2a09a304c45dcdd8f090a1751b81eca4944fe229`. I fetched `pull/48/head` and it matches this SHA.

**Verdict: changes required.** The round-1 fixes work for the cases they named. But the D4 check still accepts three kinds of programs that change a list through one name while another name for the same list is still used. The docs say D4 is fully done.

## Prior findings, re-checked at this head

| | Status | Evidence (executed) |
|---|---|---|
| **K1** (P1): different tuple slots assumed to be different lists | **Fixed for slots at the same depth.** It is still open when the two slots sit at different depths (F1 below). | `f(mut pair: tuple[list[int], list[int]])` with `a = pair[0]; b = pair[1]; a.append(2); return len(b)` is now rejected at `a.append(2)`. The test `slots_of_a_tuple_from_elsewhere_may_be_one_list` covers this. |
| **K2**: an exception holding a list turned off aliasing checks for the whole program | **Fixed.** `ValueError(xs)` now gets through lowering, so the analysis no longer falls back to its conservative mode. The NewExc is recorded as storing the list in the module's values (`body.rs`), and `codegen::refuse_gaps` refuses it by name. | A loose `boom(xs)` that raises `ValueError(xs)` no longer hides a D4 error in another tempered function. `build` refuses `ValueError(xs)` and `ValueError((xs, 1))` with "the native backend does not support an exception holding a list yet", exit 1. |

## Blocking findings

### F1 (P1): two lists at different depths are assumed never to be the same list, which is false through tuples
- **Where:** `own/alias.rs:28-39` (the `At::Depth` comment says lists at different depths "have different types, so they never are"), `alias.rs:284-285` (`step`), and `alias.rs:478-482` (`same` with `Change::List` needs `s == c`).
- **Why it's wrong:** a tuple field adds one step of depth, just like a list element. So `p[0]` is at depth 1 and `p[1][0]` is at depth 2, yet both can be `list[int]` and both can be `xs`.
- **Failure scenario (executed):**
  ```python
  temper def f(mut p: tuple[list[int], list[list[int]]]) -> int:
      a = p[0]
      b = p[1][0]
      a.append(2)
      return len(b)
  xs = [1]
  print(f((xs, [xs])))
  ```
  `check` and `run` accept it and print `2`, the same as CPython, so the aliasing goes unreported. D4 requires an error at `a.append(2)`.
- **Same hole, also executed and accepted:**
  - a nested tuple: `tuple[list[int], tuple[list[int], int]]` called with `(xs, (xs, 0))`;
  - a callee's result: `make()` returns `(xs, [xs])`, then `p[0]` and `p[1][0]` are used the same way;
  - a `for` loop: `for x in p[1][0]: p[0].append(x)` gets past the running-loop rule.
- **Control:** `grow(p[0])` while `b = p[1][0]` is live is rejected, because a `Within` change uses `s >= c`.
- **Required correction:** tell locations apart by the list's type, or count only list-element steps in the depth. Do not rely on depth alone to prove two lists are different. Then fix the comment, the D4 text in grammar.md and the A6 entry in the roadmap.
- **Regression tests for `own/tests.rs`:** the program above should be rejected at `a.append(2)`. Its legal neighbour, with `b = list(p[1][0])`, should stay accepted (it prints `1`).

### F2 (P1): changing a list through `(a if c else b)` is exempt from D4
- **Where:** `alias.rs:405`, which skips every named variable in `self.roots[w]`, with the roots built at `alias.rs:150-178`.
- **Why it's wrong:** the temporary for a conditional expression has the roots `{a, b}`. So when a change goes through it, both `a` and `b` are excused, even though one of them is a second name for the same list.
- **Failure scenario (executed):**
  ```python
  temper def f(c: bool) -> int:
      a = [1]
      b = a
      (a if c else b).append(2)
      return len(a) + len(b)
  print(f(True))
  ```
  This is accepted and prints `4`, matching CPython. Writing plain `a.append(2)` instead is rejected (the control). The roadmap's A6 entry says conditional expressions were probed, so this case was missed.
- **Required correction:** excuse only the variable the temporary is that same list on every path, so a temporary with one root, or excuse only its container. Do not excuse every root.
- **Regression tests:** the program above should be rejected. With `b = list(a)` it should stay accepted (prints `3`).

### F3 (P1): a tempered parameter is assumed to be a different list from every module-level list
- **Where:** `alias.rs:185` (a parameter starts as `Param(i)`), `alias.rs:240` (a global read gives `World`), and `alias.rs:472` (`same` needs the same abstract object). On the summary side, `body.rs` `region` stops at "another parameter or the module's values".
- **Why it's wrong:** nothing proves the two are distinct. A5 only covers passing one list twice to a call, and here `G` is passed once.
- **Failure scenarios (executed):**
  ```python
  G = [1]
  temper def f(mut xs: list[int]) -> int:
      g = G
      xs.append(2)
      return len(g)
  print(f(G))
  ```
  This is accepted and prints `2`.

  A second version runs `for x in G:` with `xs.append(x)` inside the loop, called as `f(G)`. That changes the list the loop is running over, and it is also accepted.
- **Required correction:** either treat `Param(i)` and `World` as possibly the same list in `same`/`overlaps`, or reject the call site that passes a module list to a `mut` parameter of a tempered function that reads the same global. The second is a new language rule, so it needs Nano's decision first.
- **Regression test:** the program above should be rejected.

### F4 (P2): the docs claim more than the code does
- **grammar.md:211** says D4 is "Implemented (A6)".
- **roadmap.md:74** marks A6 `[x]` and says "a path into each abstract list so an element is not its container" and that "conditional expressions" were probed.
- F1 to F3 make these claims false. Correct them along with the fixes.

## Design questions (for the owner)
- **Two parameters aliased.** `temper def f(mut xs, ys)` called as `f(a, a)` is accepted today and prints `2`. The PR defers this to A5, which the roadmap still lists as open. It is consistent with ownership.md decision 2, so it is not a finding. Still, D4's promise has this gap until A5 lands, and the A6 entry could say so.
- **Containers.** A tuple or exception that holds `a` is not counted as a second name for `a`. For example, `t = (a, 1); a.append(2); print(t)` is accepted. That follows the documented "a list inside another is its own list" rule. Please confirm this is intended for tuples, which are values the body built.

## Optional improvements
- None of the new tests guards the depth case or the conditional-expression case. The tests for F1 and F2 above belong in `own/tests.rs`, next to `slots_of_a_tuple_from_elsewhere_may_be_one_list`.

## Checks performed
| Check | Result |
|---|---|
| `cargo fmt --all --check` | passed |
| `cargo clippy --locked --all-targets -- -D warnings` | passed |
| `cargo test --locked` | passed: 419 + 19 + 27 + 29 tests, 0 failures, conformance included |
| CLAUDE.md Rust rules on the new code | no `unwrap`/`expect` outside tests, no `unsafe`, no new dependency |
| Probes through `check`, `run` and `build`, compared with `python3` | results as stated above |
| Native refusal of an exception holding a list | confirmed |

## Validation gaps
- **Python version:** the machine has Python 3.14.8, not 3.13, so the conformance comparison and my probes ran against 3.14.
- **Tests at the previous commit:** I tried to run the head's `own::tests` at 4771160, to confirm the K1 and K2 tests fail there. The shared temp volume ran out of disk quota (EDQUOT), and most of it is other sessions' files. I removed only my own worktree and did not delete anything else. To settle it, run `cargo test -p pythrust-compiler --lib own::tests` at 4771160 with the head's `own/tests.rs` on a volume with free space.
- **Other platforms:** I did not see Windows MSVC, macOS, AArch64 or a valgrind run myself.
- **Memory tools:** the remind-me tools that CLAUDE.md asks for are not available in this session, so I didn't search or save anything there.

**Verdict: changes required** (F1 to F3 are P1, F4 is P2).
