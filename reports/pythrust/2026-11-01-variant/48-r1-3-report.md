<!-- pythrust/48-r1 run 3, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 265 s, 1145715 tokens, $1.26 -->

I found 7 blocking defects, so the verdict is **changes required**. In each one, D3 or D4 says a program must be rejected, but `pythrust check` accepts it. Running each probe shows the aliasing is real: `pythrust run` and CPython print the same aliased result.

**Review of PR #48, baileyrd/PythRust**
- Base `5d76260eef62e1a3c7abfd48635a9a3e82a37650`, head `4771160b650e6a50edeaf6bc48d244050a69f057`. The fetched `pull/48/head` equals the head SHA. One commit, "Check aliasing in tempered bodies (A6)".

## Blocking findings

**1. P1: One lowering gap anywhere in the program turns off D3 and D4 for every tempered function. The A6 exit criterion is not met.**
- **Where:** `own/mod.rs:105-109` leaves `aliasing` empty under `Precision::Conservative`, and `own/modes.rs:22-24` returns early. List `+` and `*` don't lower (`lower/expr.rs:86`, "arithmetic on a non-number").
- **Program (executed):**
  ```python
  temper def same(xs: list[int]) -> list[int]:
      return xs
  temper def extend(n: int) -> int:
      a = [1, 2]
      b = a
      b.append(n)
      return len(a)
  def loose() -> list[int]:
      return [1] + [2]
  print(same([1]), extend(3), loose())
  ```
  `check` exits 0 and `run` prints `[1] 3 [1, 2]`. Both tempered functions break D3/D4, but one loose list `+` hides them.
- The same gap is the only reason these are accepted: `return rows + []` in a tempered body (the result shares `rows[0]`; CPython shows `[[1, 7]]`), `rows = [r] + [[2]]`, and `[[1]] * 2`.
- **Docs made false:** roadmap A6 requires that it "closes any other lowering gap a tempered body can reach" and says a probe "found no further lowering gap". `grammar.md` marks D3/D4 "Implemented (A6)" with no caveat.
- **Fix:** either lower list `+`/`*`, or reject a tempered body that hits a gap. Also decide whether a gap in loose code may switch off tempered checks; scoping the fallback per function would avoid that.
- **Test:** `corpus_ownership_coverage`, or an `own/tests.rs` case where a D4 violation sits next to a loose `[1] + [2]`.

**2. P1: Two different paths inside one abstract list are assumed to be different lists.**
- **Where:** `alias.rs:442-454` (`same` compares paths with `s == c`), together with `step` at `alias.rs:252-264`. This is only safe when the heap records what was stored. For a parameter, a global or a call result nothing is recorded, so `t[0]` and `t[1]` look distinct.
- **Program (executed):**
  ```python
  temper def f(mut t: tuple[list[int], list[int]]) -> int:
      a = t[0]
      b = t[1]
      a.append(9)
      return len(b)
  x = [1]
  print(f((x, x)))
  ```
  Accepted; prints 2 (CPython 2).
- The same hole appears for a tempered callee's fresh result. With `mk()` returning `(x, x)`, `t = mk(); a = t[0]; b = t[1]` is accepted (`calltuple2`, prints 2).
- **Fix:** only treat two paths as distinct when one cannot be the other: either the static types differ, or one is a prefix of the other. Otherwise treat them as overlapping.

**3. P1: The module's lists are separate from every name, and globals are never "used later".**
- **Where:** `Rvalue::Global` becomes `root(World)` at `alias.rs:225`. Liveness covers only IR locals (`alias.rs:309-317`), and `World@[Elem]` vs `World@[]` falls under finding 2. All three programs below are executed and accepted, each printing 2 like CPython:
  - `g.pyr`: with `first = rows[0]` at module level, `r = rows[0]; g = first; r.append(5); return len(g)`.
  - `globalread`: `b = g; b.append(9); return len(g)`. The read of `g` comes after the change, so it is never live.
  - `paramglobal`: `temper def f(mut xs: list[int]) -> int: xs.append(9); return len(g)`, called as `f(g)`. Parameter and `World` are assumed disjoint, and A5's planned `f(xs, xs)` check does not cover a global.
- **Fix:** treat a module-level read as a later use of `World`, and treat `World` as possibly overlapping any parameter.
- **Tests:** these three programs, plus a legal neighbour that uses `list(g)`.

**4. P1: A temporary read from several variables excuses all of them.**
- **Where:** `alias.rs:376` drops any clashing name in `roots[w]`. Roots are joined flow-insensitively (`alias.rs:143-171`).
- **Program (executed):**
  ```python
  temper def f(c: bool) -> int:
      a = [1]
      b = a
      (a if c else b).append(1)
      return len(a) + len(b)
  ```
  Accepted; prints 4. The change goes through `a` while `b` is still used.
- **Fix:** excuse a root only when the temporary must be that one variable (a single root, at that point in the flow).

**5. P1: A callee that changes a module list is not counted as a change.**
- **Where:** `Summary` records no "mutates `World`" flag, and `alias.rs:345-352` only looks at arguments.
- **Program (executed, `calleeglobal`):** `G = [1]`, a tempered `grow()` that does `G.append(1)`, and `f` doing `b = G; grow(); return len(b)`. Accepted; prints 2.
- **Fix:** add a "mutates the module's lists" bit to `Summary`, and treat such a call as a `Change::Within` of `World`.

**6. P1: Two parameters are assumed disjoint, and no check exists yet that makes that true.**
- **Where:** `alias.rs:176-179, 431`.
- **Program (executed, `twoparams`):** `temper def f(mut rows: list[list[int]], r: list[int])`, which does `rows[0].append(1); return len(r)`, called as `f([x], x)`. Accepted; prints 2.
- `alias.rs:15` hands this case to A5, but A5 is unchecked in the roadmap and is worded as `f(xs, xs)`. The case here is "a list, and an element of another argument".
- **Fix:** either widen A5's scope, as a recorded decision, to "reachable from two arguments", or make this module say what it assumes until A5 lands. Not deciding it leaves a hole that can be reached today.

**7. P2: Missing tests and docs that overstate.**
- None of the accepted programs in findings 1–6 has a test. Seven of the ten new tests fail at the base commit, which is good. The three that pass at base (`each_run_of_an_allocation_is_a_new_list`, `a_narrowed_optional_is_the_same_name`, `loose_code_keeps_python_aliasing`) only check that legal programs stay legal.
- `grammar.md`'s "Implemented (A6)" lines need to state the coverage-gate caveat.

## Design question
- Should a tuple that holds `a` (or a list that holds it) count as a second name for `a`? The PR decides that it does not ("a list inside another is its own list"). `grammar.md` D4 is now written that way, but no recorded decision of Nano's was found for it. That is a widening the owner should confirm.

## Optional improvements
- When the original name is the one changed, the fix-it says `a = list(b)`, not D4's `b = list(a)` at the line where the list is shared. Applied, it does compile (checked), so this is not a defect.

## Checks
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: no warnings.
- `cargo test --locked`: all suites passed (416 + 19 + 27 + 29), conformance included.
- The new `own::` tests, applied to a worktree at the base: 7 failed, 3 passed, as described above.
- New code has no `unwrap`/`expect`/`unsafe` outside tests and adds no dependency.

## Validation gaps
- The local `python3` is 3.14.8, not 3.13, so the CPython comparisons (conformance's and mine) used 3.14.
- I did not see Windows, macOS, AArch64 or valgrind results. The codegen change only adds a field to a pattern, so the risk is low.
- The default `CARGO_TARGET_DIR` is read-only, so I built into `$TMPDIR`.
- The `$TMPDIR/p` scratch directory already held other files; I only read my own probes.
- The remind-me memory tools that `CLAUDE.md` requires were not available in this session, so I neither searched nor saved anything there.

**Verdict: changes required.**
