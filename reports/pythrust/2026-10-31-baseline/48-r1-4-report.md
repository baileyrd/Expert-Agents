<!-- pythrust/48-r1 run 4, variant baseline, prompt 91b7e979713d73e9e758e39bfcd026e6b9a1f09b, model claude-opus-5-5, 2.1.314 (Claude Code); 252 s, 677119 tokens, $1.03 -->

# Review of PR 48 (baileyrd/PythRust): tempered-body aliasing checks, A6 (D3, D4)

**Verdict: changes required.** The PR is meant to make D4 fire on every program that breaks it. Three short programs break D4 and still pass `check` and `run`, and the D3 fix-it fails for nested lists.

- **Base:** `5d76260eef62e1a3c7abfd48635a9a3e82a37650`
- **Head:** `4771160b650e6a50edeaf6bc48d244050a69f057`. The fetched `pull/48/head` matches it.

## Blocking findings

### 1. P1: two fields of one tuple are assumed to be different lists
- **Where:** `crates/pythrust-compiler/src/own/alias.rs:223` (`Field` adds `Step::Field(k)`) and `alias.rs:446-453` (`same` needs equal paths).
- **The flaw:** `t[0]` and `t[1]` get different paths into the same abstract object, so the check treats them as different lists. Nothing proves that: a tuple can hold the same list twice.
- **Probe (executed):**
  ```python
  temper def pair() -> tuple[list[int], list[int]]:
      x = [1]
      return (x, x)

  temper def f() -> int:
      t = pair()
      a = t[0]
      b = t[1]
      b.append(2)
      return len(a)

  print(f())
  ```
  `check` exits 0 and `run` prints `2`. `b` and `a` are the same list and `a` is used after the change, so D4 should reject it. The same hole shows with a tuple parameter: `temper def f(mut t: tuple[list[int], list[int]])` called as `f((x, x))` is also accepted.
- **Fix:** paths may only tell lists apart when the types make sharing impossible. Different field indices, or paths that end at the same list type, have to count as possibly the same list. The simplest version: treat a `Field` step as `Elem`, or compare paths by type rather than by step.
- **Regression test:** the program above in `own/tests.rs`, plus a fixture `examples/aliasing_tuple_fields.pyr`.

### 2. P1: a conditional expression as the receiver hides both names
- **Where:** `alias.rs:150-168` (`roots` is gathered without regard to control flow) and `alias.rs:376` (any name in the temporary's roots is exempt).
- **The flaw:** in `(b if c else a).append(2)`, the temporary is assigned in two branches, so its roots are `{a, b}`. Both live names are then exempt.
- **Probe (executed):**
  ```python
  temper def f(c: bool) -> int:
      a = [1]
      b = a
      (b if c else a).append(2)
      return len(a) + len(b)
  ```
  `check` exits 0 and `run` prints `4`. The same program with `b.append(2)` is rejected.
- **Fix:** exempt a variable only when the temporary must be that variable on every path, for example by tracking roots per program point in the forward pass. A temporary with more than one possible root should exempt nothing.
- **Regression test:** the program above, rejected. Keep `b.append(2)` with only `b` used afterwards as the accepted neighbour.

### 3. P1: a call that returns a global's list drops what the caller stored there
- **Where:** `alias.rs:244-246`.
- **The flaw:** `result_global` adds only `anywhere(World)`. It leaves out what the body stored in `World`, unlike the argument case two lines above, which uses `reachable`. `anywhere(World)` does not cover `Fresh` objects stored in `World`, because `same` requires the same object.
- **Probe (executed):**
  ```python
  G = [[0]]
  temper def keep(owned xs: list[int]) -> None:
      G.append(xs)
  temper def last() -> list[int]:
      return G[len(G) - 1]
  temper def f() -> int:
      a = [1]
      keep(a)
      b = last()
      b.append(2)
      return len(a)
  ```
  `check` exits 0 and `run` and CPython both print `2`, so `b` is `a`. The control, `b = G[len(G) - 1]` written inline, is rejected correctly.
- **Fix:** `out.extend(self.flow.reachable(&Objs::from([Obj::World])).into_iter().map(anywhere))`.
- **Regression test:** the program above.

### 4. P2: the D3 fix-it does not work for nested lists
- **Where:** `alias.rs:402-417`, `docs/grammar.md:210`.
- **Probe (executed):** for `temper def f(rows: list[list[int]]) -> list[list[int]]: return rows`, the compiler says `return a copy: return list(rows)`. Applying that fix gives "returns a value sharing a list with its parameter `rows`; return a copy made with `list(...)`". That is also unsatisfiable with `list(...)`, since it makes a shallow copy.
- **Why it matters:** the recorded D3 decision names `return list(xs)` as the fix-it. The PR's line "a list inside one counts" widens D3 beyond it without asking.
- **Fix:** for a parameter whose elements hold lists, either give a fix-it that works (an element-wise copy), or accept a shallow copy as D3 says. Which one is the owner's call (see Design questions).
- **Regression test:** apply the fix-it and check again in `own/tests.rs`.

### 5. P2: the "`for` loop" message appears where there is no loop
- **Where:** `alias.rs:374-382`. Every live temporary that clashes is reported as a loop sequence.
- **Probe (executed):** `return h(rows[0], grow(rows))`, where `grow` is `mut`, reports "changes a list while a `for` loop over it is running". There is no loop. The roadmap text itself says only the loop's sequence should get this message.
- **Fix:** mark the `for` sequence temporaries when lowering, or recognise them in the IR. Give other temporaries a message about the argument or expression.
- **Regression test:** the program above, asserting on the message.

### 6. P2: the roadmap overstates the work
- **Where:** `docs/roadmap.md:74` marks A6 done and says "a path into each abstract list so an element is not its container".
- **The flaw:** findings 1 to 3 are programs that break D4 and pass, so the exit criterion ("every diagnostic D4 promises fires") is not met. The probe list names conditional expressions and tuples, yet both reach accepted D4 violations.
- **Fix:** keep the box unchecked until findings 1 to 3 are fixed and covered by tests.

## Design questions (for the owner, not findings)
- **Container live while an element changes:** `docs/grammar.md:211` now says `r = rows[0]; r.append(1)` while `rows` is still used is legal. The recorded decisions don't say this, and `ownership.md:5` asks tempered values to "resolve to a single owner". It is a new language rule written in without asking (CLAUDE.md, open decisions).
- **D3 widened to deep sharing:** see finding 4. Should a shallow `list(xs)` satisfy D3, as the recorded fix-it implies?

## Optional improvements
- `Analysis::aliasing` holds only the first error per function, so fixing one hides the next. That's fine for now, but it should be noted in the doc comment.

## Checks and validation gaps

| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked --all-targets -- -D warnings` | pass |
| `cargo test --locked` (416 + 19 + 27 + 29 tests, conformance included) | pass |

- **Rust rules:** no `unwrap`/`expect`/`unsafe` and no new dependency in the new code.
- **New tests at the base:** they need the new `aliasing` field and the new fixtures, so they cannot pass at the base. They do guard the change.
- **Probes:** I ran each probe above through `check` and `run` and compared with CPython, with modes and `temper` stripped.

What I could not verify:
- **CPython version:** the local `python3` is 3.14.8, not 3.13, so the conformance comparison ran against 3.14.
- **Native build:** I didn't run a separate `pythrust build` of `examples/aliasing.pyr`. My attempt passed the wrong arguments.
- **Memcheck:** valgrind isn't installed, so the new `NATIVE` entry `aliasing` has not been checked for refcount balance. Settle with valgrind on that build.
- **Other targets:** macOS, Windows MSVC and AArch64 weren't run.
- **Memory tool:** the remind-me tools were not available in this session, so I didn't search or save to them.

**Verdict: changes required.**
