<!-- conhost/1246-r4 run 3, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 106 s, 458342 tokens, $0.55 -->

**Verdict: changes required.** K1 is fixed, but one new P2 is open: the design doc's §7.2 loop doesn't match the code.

**Pinned:** Rusty-Mill/rusty_mill PR 1246. Base `66f4bc38c8181fd669c58f8e34df385a6afa895e`, head `2dfc3a6cd2136d98065b031cf0f65601568dcbd7`. The fetched `pull/1246/head` equals the head SHA. Since the last review (615e0824) there is one new commit, 2dfc3a6cd. It changes no console API, so step 1's API predictions carry over from the prior review. This round covers K1 and the change in the engine's scheduling.

## Prior finding K1: fixed

- **What changed:** `Turn.poll` is gone. `Engine::turn` (`runtime.rs:527-554`) now runs `work()` first. It then decides whether the wait may block by calling `sched::must_poll` (`sched.rs:102-104`) on a fresh view of the state. That view includes the current input length (`Shared::input_len`) and `reads_ready`.
- **Both K1 scenarios now poll** (source-traced):
  - A final slice that leaves reads ready sets `reads_ready`, so the wait doesn't block.
  - A cut at the end of a write that ended in ESC leaves input in the lane, so `input > 0` and the wait doesn't block.
- **No busy-spin** (source-traced):
  - When `must_poll` is true because reads are ready, the next turn handles `ServeReads`. `serve_reads` (`read.rs:449-480`) can only set `reads_cut` after it has completed at least one read (the `built > 0` guard), so every such turn makes progress.
  - When `must_poll` is true because input is queued, `take_input` takes at least one byte. An ESC end is only recorded after at least one byte is queued (`runtime.rs:167-172`), so a cut can't stop at zero bytes.
  - When output is above 80 KiB, input polling stops. Draining that queue always crosses the 64 KiB drain-wake threshold (`runtime.rs:229`), so the engine is woken again.
- **Other failure classes:** this commit adds no buffer, queue or conversion, so containment, retained memory and amplification are unchanged from the prior review. Custody is unchanged too: the `wait` error path still clears `read_in_flight`, and `run` still tears down on error.
- **Tests:**
  - `sched::tests::the_wait_polls_on_what_the_turn_left_not_on_its_plan` passes at the head. It can't compile at 615e0824, where `must_poll` doesn't exist, so it fails there as it should.
  - Two Windows-only engine tests were added: `reads_a_slice_leaves_ready_are_served_without_another_wake` and `input_left_behind_an_esc_cut_is_taken_without_another_wake`. I couldn't run them (see gaps below).

## Blocking findings

**R1 (P2): the §7.2 loop in the design doc omits the step that serves waiting reads, and its poll rule disagrees with the code.**
- **File:** `docs/projects/term/conhost/design.md:540` and `:543` (§7.2 loop).
- **What the doc says:** step 4 takes input whenever the output queue is ≤ 80 KiB. Step 6 says `until = now when steps 2–4 left work they could still do`. No step handles `ServeReads`.
- **What the code does** (source-traced):
  - `sched::turn` (`sched.rs:84-96`) serves reads that were left ready before any new input.
  - It takes no input while reads are ready.
  - `Engine::work` (`runtime.rs:558-579`) handles `Event::ServeReads` between the child-exit step and the input step.
  - `must_poll` polls on `reads_ready`, which none of the steps 2–4 in the doc produce.
- **Why it matters:** a reader of §7.2, which calls itself "the loop's order", would wrongly conclude that new input is never held back behind reads that are owed it. They would also miss the exact condition K1 was about. The §7.4 "Read reply" row describes the behaviour correctly, so the two sections now contradict each other.
- **When it came in:** `ServeReads` and `reads_ready` are new in this PR (added in 615e0824; neither exists at the base).
- **Required correction:** add a step between 3 and 4, "if waiting reads are ready (the last call hit its reply budget): handle(ServeReads)". Gate step 4 on "and no reads are ready". Change step 6's condition to "reads are left ready, or input is left and the output queue is ≤ 80 KiB".
- **Regression test:** none; this is a doc fix. `sched::tests::reads_owed_input_are_served_before_new_input_and_after_control` already pins the ordering the doc should describe.

## Design questions
None.

## Optional improvements
- **The engine tests don't go through `Engine::turn`.** `turns_without_another_wake` calls `work()` and then `must_poll()` itself. If `turn()` went back to waiting on a stale decision, or dropped `until` (e.g. passed `None`), both tests would still pass. A test that calls `turn()` with no outside wake and checks that every read is served would guard the wiring itself.
- **The sched test's assertions on `must_poll` are mostly restatements** of its formula on views built by hand. The `Sim`-driven tests would catch more if `Sim::step` modelled a slice that leaves `reads_ready` set.

## Checks run
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | 108 passed, 0 failed |
| `the_wait_polls_on_what_the_turn_left_not_on_its_plan`, run alone | passes |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux) | clean |
| `python3 .github/scripts/changes.py check` | 539 fragments ok, exit 0 |
| Clippy for the Windows target (`x86_64-pc-windows-gnu`) | not run: the target isn't installed and offline mode couldn't download `windows-sys` |

## Validation gaps
- **Windows-only code is unverified.** The runtime engine tests, including the two new K1 tests, and `own_pty.rs` are `#[cfg(windows)]`. I didn't run them, and I couldn't type-check or lint them for Windows. They need a Windows CI run.
- **Proposed run:** `cargo test -p rusty_conhost --target x86_64-pc-windows-msvc runtime::tests::` on a Windows machine, with log output kept.

**Verdict: changes required** (R1, P2 docs).
