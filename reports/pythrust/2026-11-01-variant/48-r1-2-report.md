<!-- pythrust/48-r1 run 2, variant baseline, prompt 3d54c89ed46831a4b52991bfbc9f77f62a08d8be, model claude-opus-5-5, 2.1.316 (Claude Code); 327 s, 1501383 tokens, $1.54 -->

I'm requesting changes on PR 48 (baileyrd/PythRust). The new check in `own::alias` catches the documented cases, but I found five kinds of program that break D3 or D4 and are still accepted. I also found three wrong diagnostics. All were confirmed by running probe programs with `pythrust check` and `run`.

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head:** `4771160b650e6a50edeaf6bc48d244050a69f057`, one commit "Check aliasing in tempered bodies (A6)". The fetched `pull/48/head` matched this SHA.

## Blocking findings

**1. P1: a conditional expression lets a change through a shared list go unreported.** `own/alias.rs:376` (with `roots` at 143–171)
```python
temper def f(c: bool) -> int:
    x = [1]
    y = x
    (x if c else y).append(2)
    return len(x) + len(y)
```
- **Result:** `check` accepts it and `run` prints `4`. Replacing the third body line with `y.append(2)` is rejected, which shows the check is active.
- **Cause:** the temporary holding the conditional's value records both `x` and `y` as its source variables. The check then skips every source variable, so the other live name `y` is never reported.
- **Correction:** skip only a source variable that is the same list as the temporary at that point, or skip nothing when there is more than one source.
- **Test:** the program above, rejected.

**2. P1: a list returned by a callee that reads a global is not linked to locals stored in that global.** `own/alias.rs:244-246`
```python
store: list[list[int]] = [[0]]
temper def keep(owned xs: list[int]) -> None:
    store.append(xs)
temper def last() -> list[int]:
    return store[len(store) - 1]
temper def f() -> int:
    a = [1]
    keep(a)
    b = last()
    b.append(2)
    return len(a)
print(f())
```
- **Result:** accepted, and `run` prints `2`. Writing `b = store[len(store) - 1]` inline is rejected.
- **Cause:** when a callee's result may come from the module's globals, the check marks the result as only "the globals' list". It does not include what the body has stored in the globals, which it does for arguments (line 242).
- **Correction:** map `reachable({World})` to "anywhere" locations, as line 242 does for arguments.
- **Test:** the program above.

**3. P1: two fields of one tuple are assumed to be different lists.** `own/alias.rs:442-454` (`same`), paths built at 223
```python
temper def make() -> tuple[list[int], list[int]]:
    a = [0]
    return (a, a)
temper def f() -> int:
    p = make()
    x = p[0]
    y = p[1]
    x.append(1)
    return len(y)
```
- **Result:** accepted, and `run` prints `2`, as CPython does.
- **Same hole through a parameter:** `temper def f(mut p: tuple[list[int], list[int]])` called as `f((a, a))` is also accepted.
- **Cause:** `Field(0)` and `Field(1)` count as different lists. Nothing proves that for a parameter or a callee's result, and A5 does not cover one argument whose fields alias.
- **Correction:** treat different field indices as possibly the same list unless the body built the tuple itself.
- **Tests:** both programs, rejected.

**4. P1: a parameter is assumed to be different from the module's lists and from lists inside other parameters.** `own/alias.rs:174-180` (separate `Param(i)` and `World`), compared at 443
```python
g: list[int] = [1]
temper def f(mut xs: list[int]) -> int:
    h = g
    xs.append(1)
    return len(h)
print(f(g))
```
- **Result:** accepted, and `run` prints `2`.
- **Same hole with a nested argument:** `temper def f(mut a: list[int], rows: list[list[int]])` with `r = rows[0]; a.append(1); return len(r)`, called as `f(rows[0], rows)`, is accepted. A5's decided rule only covers one value passed to two parameters.
- **Correction:** either treat `World` and the inside of other parameters as possibly the same as a parameter, or record a call-site rule that rules these cases out. Choosing the call-site rule is an owner decision.
- **Tests:** both programs.

**5. P1: when lowering falls back, D3 and D4 are switched off for the whole program without any message.** `own/mod.rs:104-108` (`aliasing: BTreeMap::new()`), `own/modes.rs:24-26`
- **Tempered body:** `temper def f(xs: list[int]) -> list[int] raises ValueError:` that `raise`s `ValueError(xs)` and then `return xs` is accepted by `check` and `run`. Only `build` fails, with a backend error rather than the D3 diagnostic.
- **Loose body elsewhere:** in another program, an unannotated loose `g(xs)` containing `raise ValueError(xs)` stops the D4 error for an unrelated tempered `f`. `b = a; b.append(2); return len(a)` is accepted, while the same `f` alone is rejected.
- **Roadmap:** A6's exit criterion says it "closes any other lowering gap a tempered body can reach instead of rejecting it", yet the roadmap marks A6 done.
- **Correction:** close the gap, or reject a tempered function whose program falls back. At minimum, run the check per function. Then fix the roadmap checkbox and the "Implemented (A6)" text in grammar.md.
- **Tests:** both programs.

**6. P2: the decided D3 fix-it does not work for nested lists.** Message at `own/alias.rs:413`; rejection at 402-421
- For `temper def same(rows: list[list[int]]) -> list[list[int]]: return rows`, the error suggests `return list(rows)`.
- Applying that suggestion gives a second error: "returns a value sharing a list with its parameter `rows`; return a copy made with `list(...)`". The code is already using `list(...)`.
- grammar.md D3 records `return list(xs)` as the fix-it, so either the rule or the message is wrong for nested lists (see the design questions below).
- **Test:** apply the fix-it to a nested parameter and re-check.

**7. P2: a shallow copy's element is treated as the original list, so a legal program is rejected with a wrong fix-it.** `own/alias.rs:260-261`, with `body.rs:120-122, 294-301`
```python
temper def f(rows: list[list[int]]) -> int:
    x = list(rows)
    x[0].append(1)
    return len(rows)
```
- **Result:** rejected with "`rows` may be the same list … `rows = list(...)`". `rows` is a `list[list[int]]` and `x[0]` is a `list[int]`, so they cannot be the same list.
- **Conflict with the PR's own docs:** the new D4 text in grammar.md says a list inside another "is its own list", which makes this program legal.
- **Same effect from a suggested fix:** `b = a; b[0].append(2); len(a)` is accepted. After applying the suggested `b = list(a)`, it is rejected.
- **Cause:** the copy's contents include the source list itself, which stands in for its own elements. The check takes it as the same list.
- **Test:** the program above, accepted.

**8. P2: any live temporary is reported as a `for` loop.** `own/alias.rs:374-382`
- `return g(x if c else y, grow(x))` is rejected with "changes a list while a `for` loop over it is running; loop over a copy", but there is no loop.
- The roadmap says a temporary "counts through the variable it was read from, except a `for` loop's sequence". The code instead puts every temporary into the loop message.
- **Correction:** report a loop only for a loop's sequence, and name the source variable otherwise.
- **Test:** the program above, checked against its message.

## Design questions for the owner
- **New D4 rule:** the PR adds a rule to grammar.md D4 without asking: "a list inside another is its own list". So `b = a; b[0].append(2); len(a)` and `r = rows[0]; r.append(1); len(rows[0])` are both legal, but two names for `rows[0]` are not.
- **D3 widened:** D3 now also covers lists inside a parameter and lists the body stored into a parameter. That conflicts with the decided `return list(xs)` fix-it (finding 6).
- Both need the owner's decision per CLAUDE.md. Finding 4 also needs a decision on whether A5 should cover arguments that contain other arguments, or globals passed in.

## Optional improvements
- `aliasing` is inserted out of order in `NATIVE` (`tests/conformance.rs:346`).
- `Body::lists` and `holds_list` are recomputed for each operand during liveness; caching them per local would be cheaper.

## Checks run
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked --all-targets -- -D warnings`: passed.
- `cargo test --locked`: passed (416 + 19 + 27 + 29 tests, conformance included). I used a scratch `CARGO_TARGET_DIR` because the configured one is read-only.
- **New tests against the base:** I ran the new `own/tests.rs` at the base commit. The 7 tests that expect a rejection fail there, as they should. The 3 legal-neighbour tests pass there, which is expected.
- **Rust rules on the new code:** no `unwrap`/`expect`, no `unsafe`, no new dependency.
- **Native build:** `examples/aliasing.pyr` built natively prints the same as CPython.

## Not verified
- Local `python3` is 3.14.8, not 3.13, so the CPython comparison is not the 3.13 run the project specifies.
- valgrind is not installed, so I did not run memcheck on `aliasing`.
- Windows MSVC, macOS and AArch64 were not run.
- The remind-me tools CLAUDE.md asks for were not available in this session, so nothing was searched or saved there.

**Verdict: changes required.**
