# Review fixes under crates, docs, tests, examples

31 commits answer a review; held-out PRs (every commit of 47, 48 and 65) removed.
Read each one's diff (`git show <sha>`) for the defect class it fixed.

| Commit | Subject | Files |
|---|---|---|
| 570b541499 | Let a bool key fit an int key in displays and generic code | `crates/pythrust-compiler/src/infer/expr.rs`, `crates/pythrust-compiler/src/infer/solve.rs`, `crates/pythrust-compiler/src/infer/tests.rs` |
| 5ed7754df4 | Apply Nano's dict/set decisions; fix set probes and get() | `crates/pythrust-compiler/src/infer/builtins.rs`, `crates/pythrust-compiler/src/infer/expr.rs`, `crates/pythrust-compiler/src/infer/mod.rs` … |
| 2336886782 | D7 note: count option costs as the union with today's tested calls | `docs/research/faults.md` |
| 5216be43a2 | raises Exception lint: no warning when the narrower clause names a redefined class | `crates/pythrust-compiler/src/infer/raises.rs`, `docs/grammar.md` |
| 4d7ebb2b8e | raises Exception lint: Nano's #57 conditions | `crates/pythrust-compiler/src/infer/raises.rs`, `crates/pythrust-compiler/src/main.rs`, `crates/pythrust-compiler/tests/conformance.rs` … |
| 4895953036 | Bound native exception repr like run's, iteratively | `crates/pythrust-compiler/src/codegen/exc.rs`, `crates/pythrust-compiler/src/codegen/mod.rs`, `crates/pythrust-compiler/src/codegen/opt.rs` … |
| 33ac41316b | infer: regressions for late-shadow types through parameters and indexing | `crates/pythrust-compiler/src/infer/raises.rs` |
| 0a31e5ee22 | infer: a late builtin shadow leaves no argument surely a number | `crates/pythrust-compiler/src/infer/raises.rs`, `docs/grammar.md` |
| 40950d6977 | infer: a call or read before a shadowing binding reaches the builtin | `crates/pythrust-compiler/src/infer/raises.rs`, `docs/grammar.md`, `examples/raises_late_shadow.out` … |
| d43588260f | native: note every global the skipped suffix of a block assigns | `crates/pythrust-compiler/src/lower/globals.rs`, `crates/pythrust-compiler/src/lower/stmt.rs`, `crates/pythrust-compiler/src/lower/tests.rs` … |
| ec676b1eac | native: a global only a never-returning assignment assigns fails when read | `crates/pythrust-compiler/src/lower/globals.rs`, `crates/pythrust-compiler/src/lower/stmt.rs`, `crates/pythrust-compiler/src/lower/tests.rs` … |
| eab27b4093 | native: one settled-type test for variables, conditionals and and/or | `crates/pythrust-compiler/src/lower/expr.rs`, `crates/pythrust-compiler/src/lower/mod.rs`, `crates/pythrust-compiler/src/lower/tests.rs` … |
| 8f757377ae | native: a variable's type settles only once nothing in it is open | `crates/pythrust-compiler/src/lower/mod.rs`, `crates/pythrust-compiler/src/lower/tests.rs`, `crates/pythrust-compiler/src/lower/vars.rs` … |
| d1ad95ce7c | examples: loop regression reads live arithmetic (`y * y`) | `examples/generic_diverging_retype.out`, `examples/generic_diverging_retype.pyr` |
| de0cd48883 | native: type no variable before the whole body is seen | `crates/pythrust-compiler/src/lower/expr.rs`, `crates/pythrust-compiler/src/lower/mod.rs`, `crates/pythrust-compiler/src/lower/tests.rs` … |
| ead18b460e | native: a live assignment retypes a variable only dead code typed | `crates/pythrust-compiler/src/lower/mod.rs`, `crates/pythrust-compiler/src/lower/tests.rs`, `crates/pythrust-compiler/src/lower/vars.rs` … |
| b61359b6b1 | native: a never-returning branch decides no live type | `crates/pythrust-compiler/src/lower/expr.rs`, `crates/pythrust-compiler/src/lower/mod.rs`, `crates/pythrust-compiler/src/lower/tests.rs` … |
| 9065bbdc94 | Address review: handler names order inference, bounded exception repr | `crates/pythrust-compiler/src/infer/scope.rs`, `crates/pythrust-compiler/src/infer/temper.rs`, `crates/pythrust-compiler/src/infer/tests.rs` … |
| 123f33e910 | native: end only the path a never-returning call is on | `crates/pythrust-compiler/src/lower/expr.rs`, `crates/pythrust-compiler/src/lower/stmt.rs`, `crates/pythrust-compiler/src/lower/tests.rs` … |
| cfb25b4140 | Mark A2 done; exercise list(xs) from a tempered function | `docs/roadmap.md`, `examples/list_copy.pyr` |
| 972a05df72 | Add list(xs) shallow-copy builtin (phase 4 A2) | `crates/pythrust-compiler/src/codegen/mod.rs`, `crates/pythrust-compiler/src/infer/builtins.rs`, `crates/pythrust-compiler/src/infer/tests.rs` … |
| d3d2a638ca | docs: plain wording for the annotation fix-it example | `docs/grammar.md` |
| f3a5bf36f9 | native: keep ops on never-returning results as a follow-up | `crates/pythrust-compiler/src/lower/tests.rs`, `crates/pythrust-compiler/tests/conformance.rs`, `docs/native.md` … |
| 86be04e7ca | docs: M4 closes with bools as ints fixed | `docs/native.md`, `docs/roadmap.md` |
| 0442dc30a6 | docs: mark M4 done, with its follow-ups | `docs/handoff.md`, `docs/native.md`, `docs/roadmap.md` |
| d6a3b62260 | native: bind range types when matching instance signatures | `crates/pythrust-compiler/src/lower/mod.rs`, `docs/native.md`, `examples/range_values.out` … |
| ff58773f66 | native: open results keep the declared shape; undecided instances retry | `crates/pythrust-compiler/src/lower/expr.rs`, `crates/pythrust-compiler/src/lower/mod.rs`, `crates/pythrust-compiler/src/lower/opt.rs` … |
| 1c35c2f03f | native: open display items and recursion before the deciding return | `crates/pythrust-compiler/src/lower/expr.rs`, `crates/pythrust-compiler/src/lower/mod.rs`, `crates/pythrust-compiler/src/lower/seq.rs` … |
| f147074f4d | native: generic calls whose result type the signature leaves open | `crates/pythrust-compiler/src/lower/expr.rs`, `crates/pythrust-compiler/src/lower/mod.rs`, `crates/pythrust-compiler/src/lower/stmt.rs` … |
| fd7316d496 | Hint only where narrowing the truth-tested operand fixes the error | `crates/pythrust-compiler/src/infer/expr.rs`, `crates/pythrust-compiler/src/infer/mod.rs`, `crates/pythrust-compiler/src/infer/stmt.rs` … |
| ddc22a3f8f | Float regressions from the floats review: stress and conversion failures | `crates/pythrust-compiler/tests/conformance.rs`, `examples/float_semantics.out`, `examples/float_semantics.pyr` … |

`mine` keys a review fix on the word "review" in its message, so it misses fixes that
credit the reviewer by name. These were added by hand from the PR history (none in a
held-out PR):

| Commit | Subject | PR |
|---|---|---|
| 8154f83ea7 | own: summaries stay sound through fresh nesting, held globals and shared lists | #43 |
| 1a71db1e4d | A8: lists inside a returned tuple or optional keep its origins (Dottius) | #53 |
| 6a9e1ddbce | A8: follow strings through callees' list elements and returned lists (Dottius) | #53 |
| a16229f7e7 | A8: holds_heap and reaches_mutable predicates; exceptions (Nano) | #53 |
| 60574fc7fb | A7: per-function retains fact, opaque keeps, keep-path tests, A8 | #51 |
| f37dcb4c9c | rc: copies borrow across blocks and take a reference at its last use | #52 |
| 4895953036 | Bound native exception repr like run's, iteratively | #45 |
| 47a4e0f8e5 | build: pass the linker absolute paths only | #42 |
| bb613d03e3 | build: link in a private directory beside the executable, not in TMP | #42 |
| 0d6b98a776 | build: refuse an executable that is the source file, case-insensitively | #42 |
| b668f0ace8 | conformance: memcheck runs fail closed | #35 |
| c510a63d4b | conformance: memcheck passes only on its clean summary | #35 |
| b18523eded | native: strip the debug map on macOS too | #34 |

Held out (every commit of the PR, all review rounds, not only up to each key's fix):
PRs 47, 48 and 65. `mine` removed only the commits up to each case's head and fixes, so
seven later-round commits of those PRs were dropped from the table above by hand.

## Defect classes

1. **Ownership summary loses a path.** What a callee keeps, returns or stores must reach
   the caller through every container the value can sit in: nested fresh lists, a
   global a callee leaves in an argument, lists inside a returned tuple or optional,
   elements a callee keeps or returns. (8154f83, 1a71db1, 6a9e1dd, a16229f)
2. **Refcount elision keeps less than it gives up.** A retain is dropped only where
   the IR proves the reference is held; anything unclassified counts as keeping; a
   borrowed copy never gives a reference away. Memory checks fail closed. (60574fc,
   f37dcb4, b668f0a, c510a63)
3. **Name resolved too early.** Python resolves a name when it runs, so a call or read
   before a later shadowing `def` or assignment reaches the builtin; analyses that see
   only the final binding admit a broken contract. (40950d6, 0a31e5e, 33ac413, 9065bbd,
   5216be4)
4. **Dead code decides a live type.** A never-returning call or branch, a skipped
   statement suffix, or an open (unsettled) generic type fixes a variable's or
   result's type before live code is seen. (b61359b, ead18b4, de0cd48, 8f75737,
   eab27b4, ec676b1, d435882, 123f33e, f3a5bf3)
5. **Generic instance shape.** An open result type, a range or nested type missing from
   `bind`, mutual recursion through undecided instances, or a key type a generic call
   decides late. (f147074, 1c35c2f, ff58773, d6a3b62, 570b541)
6. **Native differs from `run` at a limit.** Nesting and recursion limits, rendering
   deep values, messages: native must stop where `run` stops with the same diagnostic,
   and `run` must match CPython 3.13. (4895953, 9065bbd, cc0553c, 8c783a7)
7. **Build on a hostile path or platform.** Relative paths parsed as linker options,
   shared TMP, case-insensitive file systems, macOS and Windows linker differences.
   (47a4e0f, bb613d0, 0d6b98a, b18523e)
8. **Fix-it or doc overstates.** A suggestion that does not re-check clean, a hint not
   at the constraint it fixes, a doc claiming more than the code or measurements show.
   (4d7ebb2, 5216be4, fd7316d, b18523e, f3a5bf3, 2336886)

## Oracles

- **CPython 3.13**: the semantic oracle (`corpus_matches_cpython`, pinned in CI).
- **`pythrust run`**: the reference for native builds (`corpus_matches_native`,
  `NATIVE_GAPS` lists every refusal with its reason).
- **valgrind memcheck**: refcount balance of native programs (CI job, fail closed).
- **Prior art** (`docs/research/ownership.md`): Perceus (Reinking et al., PLDI 2021),
  Swift's law of exclusivity (SE-0176) and ARC optimisation, Mojo argument
  conventions, Lobster's compile-time refcount elision, Rust NLL (RFC 2094).
- **Type narrowing**: mypy and Pyright narrowing rules, PEP 604 unions.
- **Cranelift** docs and the `cranelift-*` crates the repo pins in `Cargo.lock`.

## Settled decisions

- `docs/grammar.md`: Decided (D1 to D8, A6 choices, B4 choices), Deviations from
  Python, Deliberate omissions.
- `docs/research/ownership.md` (Decided), `faults.md` (D7 revisit), `ir.md`,
  `backend.md`.
- `docs/native.md`: Choices made inside the settled design, Known gaps.
- `docs/roadmap.md`: phase 4 plan and open follow-ups; `docs/handoff.md`.
- `CLAUDE.md`: engineering rules and the open-decisions rule.
