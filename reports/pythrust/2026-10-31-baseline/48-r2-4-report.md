<!-- pythrust/48-r2 run 4, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 243 s, 948261 tokens, $1.09 -->

**Repository:** baileyrd/PythRust, PR #48
**Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
**Reviewed head:** `2a09a304c45dcdd8f090a1751b81eca4944fe229` (I fetched `pull/48/head` and it matches).

**Verdict: changes required.** Both earlier findings are fixed, but the new aliasing check still accepts programs that rule D4 or D3 forbids. I ran every probe below with `pythrust check` and `run`, and compared with CPython.

## Earlier findings at this head
- **K1 (tuple slots assumed to hold different lists): fixed.** `a = t[0]; b = t[1]; a.append(1); return len(b)` on a tuple parameter is now rejected. `slots_of_a_tuple_from_elsewhere_may_be_one_list` covers it. The fix also brings in finding 1 below.
- **K2 (an exception holding a list turns off aliasing checks): fixed for that case.** `e = ValueError([1])` plus `temper def f(xs) -> list[int]: return xs` now gets the D3 error. The same mechanism can still be triggered another way (finding 3).

## Blocking findings

**1. P1: lists reached at different depths are treated as different lists.**
`own/alias.rs:28-39` (the `At::Depth` doc comment) and `same` at `alias.rs:471-483`. The comment says "lists at different depths have different types, so they never are." That is false once tuples are involved: in `tuple[list[int], tuple[list[int], int]]`, both `t[0]` (depth 1) and `t[1][0]` (depth 2) are `list[int]`.

Executed:
```python
temper def make() -> tuple[list[int], tuple[list[int], int]]:
    xs = [1]
    return (xs, (xs, 1))

temper def f() -> int:
    t = make()
    a = t[0]
    p = t[1]
    b = p[0]
    a.append(2)
    return len(b)

print(f())
```
- `check` exits 0 and `run` prints `2`, as CPython does with `temper` removed. D4 requires an error at `a.append(2)`.
- The same happens with `mut t: tuple[list[int], tuple[list[int], int]]` called as `f((xs, (xs, 1)))`, and with `tuple[list[int], list[list[int]]]` called as `f((xs, [xs]))` using `b = t[1][0]`.
- It also happens with globals, which all share one depth-indexed object: with `G = [1]` and `H = [G]`, a tempered body doing `a = G; b = H[0]; a.append(2); return len(b)` is accepted.
- **Fix:** depth alone doesn't separate lists. Either compare by type (two locations at different depths may be the same list whenever their list types match), or drop depth for objects whose element type allows this.
- **Regression test:** the three programs above as rejected programs in `own/tests.rs`, next to `slots_of_a_tuple_from_elsewhere_may_be_one_list`.

**2. P1: a parameter and a module global are assumed to be different lists, without proof.**
`same` at `alias.rs:472` returns false whenever the two objects differ, so `Obj::Param(i)` and `Obj::World` never clash. A5 doesn't cover this: its decided scope is only "one value to two parameters" (ownership.md, Decided 2).

Executed:
```python
G = [1]

temper def f(mut xs: list[int]) -> int:
    g = G
    xs.append(2)
    return len(g)

print(f(G))
```
- `check` exits 0, but `g` and `xs` are two names for one list, changed through `xs` while `g` is still used.
- **Fix:** let `World` overlap every parameter unless the call sites prove otherwise. If that is too strict, ask the owner and record the decision instead of assuming the lists are distinct.
- **Regression test:** this program rejected, and the same body without the later `len(g)` still accepted.

**3. P1: unrelated loose code still turns off the D3/D4 checks for the whole program.**
`own/mod.rs:96-110`: lowering stops at the first gap anywhere in the program, and `aliasing` is then empty. K2's specific trigger is gone, but the mechanism remains.

Executed:
```python
temper def f(xs: list[int]) -> list[int]:
    return xs

x = print(7)

def print(n):
    return 1

f([2])
```
- `check` and `run` exit 0. Without the loose `print` lines, the same `f` is rejected with "returns its parameter `xs`".
- grammar.md records this fallback for modes only. The new D3/D4 text (grammar.md lines 430-431 and the Deviations entry) doesn't mention it.
- **Fix:** run the alias check on tempered functions even when some other function fails to lower, for example by lowering each function separately. If the owner prefers to keep the fallback, record it in grammar.md D3/D4 as a decision; don't leave it silent.
- **Regression test:** an `own/tests.rs` case that pairs `a_builtin_called_before_its_shadowing_def_falls_back`'s source with a tempered `return xs`, expecting the D3 error.

**4. P2: the fix-it for a change through a temporary names the wrong variable.**
The `None` arm of `aliased` at `alias.rs:424-426`. Executed:
```python
temper def f(n: int) -> int:
    a = [1]
    b: list[int] | None = None
    if n > 0:
        b = a
    if b is not None:
        b.append(2)
    return len(a)
```
- The message suggests `` `a = list(...)` ``. Applying it (`a = list([1])`) gives the same error again.
- The right fix-it is `b = list(a)`. The changed temporary's `roots` (here `{b}`) already holds the variable name to use.
- **Regression test:** assert on this message in `a_narrowed_optional_is_the_same_name`.

## Design questions
- **Exceptions caught locally:** `raise ValueError(xs)` caught inside the same tempered body still requires `owned xs` (executed). This matches the recorded rule ("may outlive the call") but is stricter than needed. Is that intended?
- **Unnamed tuple reads:** `a = t[0]; a.append(1); len(t[1])` on a tuple parameter is accepted, while binding `t[1]` to a name is rejected. This matches D4's wording for lists, but the new tuple sentence in grammar.md reads as if it were stricter.
- **Lists nested inside another argument:** `Param(i)` and `Param(j)` are assumed distinct, which relies on A5, and A5 isn't done. It's unclear whether A5 will cover `f(xs, [xs])`, where one argument holds the other list. That needs to be settled with the owner.

## Optional improvements
None.

## Checks
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: passed.
- `cargo test --locked`: all passed (419, 19, 27, 29 and 0 tests).
- Conformance ran against **Python 3.14.8**, not 3.13.
- Native build of `examples/aliasing.pyr` matches its `.out`.
- `examples/exception_list.pyr` is refused natively with the backend message, as intended.
- No new `unwrap`/`expect`, `unsafe` or dependencies.
- The new tests can't pass at base: `own` tests use the new `Analysis::aliasing` field, and the corpus expect-error messages don't exist there. I worked this out from the source and didn't run them at base.

## Validation gaps
- Conformance under CPython **3.13**: run `PYTHON=python3.13 cargo test --locked --test conformance`.
- valgrind isn't installed, so there was no memcheck. This PR adds no native lowering beyond a refusal.
- macOS, Windows MSVC and AArch64 weren't run.
- The remind-me memory tools that CLAUDE.md requires weren't available in this session, so I made no memory search or capture.
