<!-- pythrust/48-r1 run 5, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 296 s, 1097106 tokens, $1.15 -->

I reviewed baileyrd/PythRust PR 48 and the verdict is **changes required**. The new D4 check (no changing a list through one name while another name for it is still used) misses three kinds of program that the rule forbids. I confirmed all three by running them.

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head reviewed:** `4771160b650e6a50edeaf6bc48d244050a69f057`. `git fetch origin pull/48/head` returned this SHA.
- **Claimed step:** roadmap A6, the alias check for tempered bodies (D3, D4).

## Blocking findings

### 1. P1: a conditional expression gets past D4 (`own/alias.rs:143-171`, `:376`)
**Trigger:** the list being changed is a conditional expression whose branches name two aliases.

`roots()` collects, for each temporary, every variable it is ever copied from, across the whole function and regardless of position. `change()` then ignores any live name in that set (`:376`). A temporary assigned in both arms of `a if c else b` gets both `a` and `b` as its sources, so a change through it is never compared against either.

```python
temper def f(c: bool) -> list[int]:
    a = [1]
    b = a
    (a if c else b).append(2)
    return list(b)

print(f(True))
```
*Executed.* `check` exits 0 and `run` prints `[1, 2]`. The same change written as `x = a if c else b` then `x.append(2)` is rejected, and so is `b.append(2)`. This program should be rejected the same way.

**Fix:** only ignore a name when it is the temporary's single source (or work out sources at each point in the function). Any other source is still a clash.

**Regression test:** add the program above to `own/tests.rs` as a rejected case.

### 2. P1: a change a callee makes to a module global never reaches the caller (`own/alias.rs:345-353`; `Summary` in `own/mod.rs:61-66`)
**Trigger:** a tempered callee changes a global list while the caller holds another name for it.

The check counts a call as a change only through its `mut` arguments. `Summary` has no field saying "this callee changes the module's values", so the change is invisible to the caller.

```python
G = [1]

temper def bump() -> None:
    G.append(2)

temper def f() -> int:
    a = G
    bump()
    return len(a)

print(f())
```
*Executed.* `check` exits 0 and `run` prints `2`. Writing `G.append(2)` directly in `f` is rejected ("changes a list while `a` may be the same list…"). Moving the same change into a tempered helper makes the error disappear.

**Fix:** record in `Summary` that a callee may change the module's values. Then treat such a call as a change to every list reachable from the module's values, which `alias.rs` calls `Obj::World`.

**Regression test:** the program above rejected, with a neighbour where `bump` only reads `G` still accepted.

### 3. P1: two fields of a parameter tuple are assumed to be different lists (`own/alias.rs:442-454`, `:252-264`)
**Trigger:** a tempered function takes a tuple parameter and the caller puts the same list in two of its fields.

`same()` treats different paths into a parameter (`[Field(0)]` and `[Field(1)]`) as different lists. Nothing proves that: the caller can pass `(a, a)`. For lists built inside the body this is covered, because `flow.stored` adds every stored list as a possible match. For a parameter, nothing stored by the body means nothing gets added.

```python
temper def f(mut t: tuple[list[int], list[int]]) -> int:
    x = t[0]
    y = t[1]
    x.append(1)
    return len(y)

a = [0]
print(f((a, a)))
```
*Executed.* `check` exits 0, and CPython prints `2`, so `y` saw the change made through `x`.

grammar.md D4 (as amended by this PR) says only that a list inside another is its own list. Same-typed fields of one parameter are not proven distinct.

**Fix:** inside a parameter, treat two paths as possibly the same list whenever the lists at those paths have the same type. Alternatively, ask the owner to settle it as a call-site rule like A5's (see design questions).

**Regression test:** the program above, plus `x = t[0]` with `n = t[1]` where the second field is not a list, which should stay accepted.

### 4. P2: a comment points to a module that does not exist (`own/alias.rs:15`)
The comment says "A5's check (`own::calls`)", but there is no `own/calls.rs`: A5 is still unchecked in the roadmap. Either name A5 alone or say the check is still to come.

## Design questions for the owner
- **Finding 3:** should the callee treat same-typed paths as possibly the same list, or should a call-site rule (an A5 extension) reject `f((a, a))` when the parameter is `mut`? The decided A5 rule covers only `f(xs, xs)`.
- The roadmap now marks A6 done, but findings 1–3 show D4's promise does not hold on every program. Either uncheck it or close the findings first.

## Optional improvements
- For `put(rows, r)` (a `mut` list argument plus an `owned` one), the error is "changes a list while `r` may be the same list… `r = list(...)`". It hides the named argument `rows`, and that fix-it does not fix the call. Rejecting the call itself is sound, since the summaries cannot tell the order of changes inside `put`, but `put(rows, list(r))` would be a better suggestion.
- Three new tests pass at base as well: `each_run_of_an_allocation_is_a_new_list`, `a_narrowed_optional_is_the_same_name` and `loose_code_keeps_python_aliasing`. They are valid checks that legal programs stay accepted. No test covers the case where a list from an earlier loop iteration and the latest one really are the same list and must still be rejected.

## Checks run, all at head in a scratch copy
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: passed.
- `cargo test --locked`: passed (416 + 19 + 27 + 29 + 0), conformance included.
- I ran the new `own/tests.rs` against base: 7 of the new tests fail there, as they should, and the 3 above pass.
- New code follows the Rust rules: no `unwrap`/`expect`, no `unsafe`, no new dependencies.
- `examples/aliasing_shared.pyr` and `examples/aliasing_return.pyr` are rejected with the expected messages.
- My first build reused a stale target directory and gave misleading results. I rebuilt from scratch and every probe result above comes from the fresh build.

## Validation gaps
- **CPython version:** this machine has CPython 3.14.8, not 3.13, so the conformance comparison ran against the wrong oracle. Rerun with `PYTHON=python3.13`.
- **Memcheck:** valgrind is not installed. The memcheck run of the new `NATIVE` program `aliasing` is still to do.
- **Other platforms:** I did not see results for Windows MSVC, macOS or AArch64. CI needs to cover them.
- **Memory tool:** `CLAUDE.md` asks for `remind_me_*` calls, but those tools were not available in this session. I saved nothing to memory.

**Verdict: changes required.**
