<!-- pythrust/48-r2 run 1, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 161 s, 823995 tokens, $0.86 -->

**Verdict: changes required.** K2 is fixed, but K1 is only partly fixed, and that leaves an aliasing hole the tempered-body check accepts.

**Repository:** baileyrd/PythRust, PR #48
**Base:** 5d76260eef62e1a3c7abfd48635a9a3e82a37650
**Head:** 2a09a304c45dcdd8f090a1751b81eca4944fe229 (the fetched `pull/48/head` matches it)

## Prior findings

**K1 (P1), different tuple slots assumed to hold different lists: partly fixed, still blocking.** Two slots at the same depth are now treated as possibly one list: `pair[0]` and `pair[1]` of a parameter, of a call result, and of `g[0][0]` / `g[1][0]`. The new test `slots_of_a_tuple_from_elsewhere_may_be_one_list` covers these, and my probe `k1.pyr` is now rejected. Slots at different depths of a tuple are still assumed to be different lists (F1 below).

**K2 (blocking), an exception holding a list turned off aliasing checks for the whole program: fixed.** `ValueError(xs)` now lowers, and `body.rs:108-113` treats it as stored in the module's values. So the program gets precise summaries, and a tempered function holding its parameter in an exception must write `owned`. My probe `exc.pyr` gets the right error ("write `owned xs`"). `an_exception_holding_a_list_leaves_aliasing_checked` covers it, and the native build refuses it by name (`exception_list.pyr` and a tuple-wrapped case both get the backend error). That refusal has no test (F2).

## Blocking findings

### F1 (P1): a tuple can hold the same list at two depths, and the check misses it

- **Where:** `crates/pythrust-compiler/src/own/alias.rs:28-39` (`At::Depth`), `:471-483` (`same`), `:279-293` (`step`).
- **The false assumption:** the comment at `alias.rs:30-32` says "lists at different depths have different types, so they never are". That holds for lists, which have one element type, but not for tuples. `tuple[list[int], list[list[int]]]` has a `list[int]` at depth 1 (`p[0]`) and another at depth 2 (`p[1][0]`). A caller can pass `(xs, [xs])`.
- **How it slips through:** `p[0]` is `Param(0)` at depth 1 and `p[1][0]` is `Param(0)` at depth 2. For a change like `append`, `same` requires equal depths, so the two names never clash.
- **Failure scenario** (*executed*):
  ```python
  temper def f(mut p: tuple[list[int], list[list[int]]]) -> int:
      a = p[0]
      b = p[1][0]
      a.append(1)
      return len(b)

  xs = [0]
  print(f((xs, [xs])))
  ```
  - `pythrust check` exits 0, and `run` prints `2`; CPython 3.14 prints `2` as well.
  - D4 requires an error here: `a` is changed while `b`, which may be the same list, is used later. That is the same rule the K1 fix enforces for `pair[0]` / `pair[1]`.
  - Variants that are also accepted:
    - `tuple[list[int], tuple[list[int], int]]` with `q = p[1]; b = q[0]`
    - `p[0].append(1)` with `b = p[1][0]` taken first
    - a fresh tuple from a callee returning `(l, [l])` follows the same `step` path (source-traced).
- **Required correction:** two depths may be one list whenever a tuple type sits between them. Either keep the step path and treat tuple fields as unknown in both depth and slot, or compare the static list types of the two locations instead of their depth. Then fix the doc comment at `:30-32`, and the D4 wording in `grammar.md` and the A6 entry in `roadmap.md`, which only mention same-depth slots.
- **Regression test:** add the program above to `slots_of_a_tuple_from_elsewhere_may_be_one_list` in `own/tests.rs`, expecting the error on `a.append(1)`. It passes (wrongly accepted) at this head and must go red without the fix.

### F2 (P2): nothing tests the native refusal of an exception holding a list

- **Where:** `crates/pythrust-compiler/src/codegen/mod.rs:48` and `:1015-1039` (`refuse_gaps`).
- **Failure scenario** (*executed*): in a scratch copy I disabled the refusal (`let _ = refuse_gaps;`).
  - `pythrust build examples/exception_list.pyr` then succeeds, and the binary prints the golden output.
  - The full `cargo test --locked` stays green: 419 + 19 + 27 + 29 passed.
  - `exception_list` is not in `NATIVE`, but the conformance harness (`conformance.rs:447-461`) compares whatever builds, so dropping the refusal goes unnoticed.
- **Why the refusal matters:** `native.md` gives the reason as a reference-count cycle. `es: list[ValueError]` with `es.append(ValueError(es))` type-checks under these rules and would leak natively without the refusal (source-traced; I could not run memcheck).
- **Required correction:** add a codegen or CLI test asserting that `build` on `ValueError([1])`, and on a tuple holding a list, fails with "does not support an exception holding a list". Or have conformance assert that `exception_list` gets the `NATIVE_UNSUPPORTED` refusal. The old test asserting the refusal in lowering was removed (`lower/tests.rs:836`), so nothing replaces it.

## Design questions (not findings)

- **Different parameters holding the same list.** `temper def f(mut a: list[int], p: tuple[list[int], int])` called as `f(xs, (xs, 1))` is accepted (`depth3.pyr`, prints `2`). The alias check treats different parameters as different lists. Today that is A5's job, which is still open, but A5 as decided looks only at the arguments themselves ("one list passed twice"). It would also need to look inside a tuple argument for this case. Worth confirming the scope with the owner before A5 lands.

## Optional improvements

- `depth5.pyr` is a conservative false positive: looping over `p[1]` while appending to `p[0]` is rejected as "changes a list while a `for` loop over it is running", even when the slots hold different lists. That is sound; the message could say "may be".

## Checks run

| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked` | pass: 419, 19, 27 (conformance) and 29 passed, 0 failed |
| Probes (`check` / `run` / `build`) | k1, depth, depth2, depth3, depth4, depth5, ret, exc, exception_list, ex2, as reported above |
| Refusal disabled in a scratch copy | full suite still green (F2) |

The new Rust code has no `unwrap`/`expect` outside tests, no `unsafe`, and no new dependencies.

## Validation gaps

- **Python version:** the only Python here is 3.14.8, not 3.13, so the conformance comparison ran against 3.14. Rerun `cargo test --locked --test conformance` with 3.13.
- **Memcheck:** valgrind is not installed, so there is no run of the corpus under memcheck, and no confirmation of the cycle leak in F2.
- **Other targets:** I did not see CI on Linux AArch64, macOS or Windows MSVC.
- **Memory tool:** the remind-me tools `CLAUDE.md` asks for were not available in this session, so nothing was searched or saved there.

## Verdict

**Changes required:** F1 (P1) and F2 (P2).
