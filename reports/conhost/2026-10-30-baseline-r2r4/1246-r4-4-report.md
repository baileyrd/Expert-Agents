<!-- conhost/1246-r4 run 4, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 116 s, 515972 tokens, $0.61 -->

I re-checked K1 and it is fixed at this head. One P2 test-coverage gap remains: no test exercises `turn()` itself, so the fix's ordering isn't guarded. Verdict: **changes required**.

**Repository:** Rusty-Mill/rusty_mill, PR 1246
**Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
**Reviewed head:** `2dfc3a6cd2136d98065b031cf0f65601568dcbd7`. `git fetch origin pull/1246/head` returned exactly this SHA.

**Scope of this round:** I re-checked K1 and reviewed the one commit since the last review, `615e0824..2dfc3a6c` (`2dfc3a6cd`: runtime.rs, sched.rs, console.rs, console/read.rs). I did not redo the earlier rounds' conhost parity work on the full base→head diff; it doesn't change this verdict.

## K1 at this head: fixed

`Engine::turn` (`runtime.rs:527-555`) now runs `work()` first and only then works out whether its wait can block: `must_poll()` (`runtime.rs:605-607`) reads the reads-ready flag, the input lane's length and the output queue's length *after* the turn's events. `sched::must_poll` (`sched.rs:102-104`) is `reads_ready || (input > 0 && output <= INPUT_PAUSE_ABOVE)`. Both cases from the earlier finding are now covered:
- **A slice that leaves reads ready:** `serve_reads` sets `reads_cut` (`read.rs:456-457`), so `reads_ready()` is true and the wait polls instead of blocking.
- **A slice cut after a write that ended in ESC:** `take_input` stops at the front entry of `esc_ends` (`runtime.rs:265-271`), the lane is not empty, and the wait polls.

I also checked that the new rule can't cause a busy loop (source-traced):
- **Reads ready:** `reads_cut` is only set once `built > 0` (`read.rs:456`), so every `ServeReads` turn that leaves it set has completed at least one read. With at most 64 waiting calls, it clears after a bounded number of turns.
- **Input queued:** the plan always takes `min(input, 16 KiB)` when not paused and no reads are ready. Each ESC entry is popped when it is reached, so a zero-length cut still makes progress, and writers fail after hang-up, so the lane is finite.
- **Paused on output:** when output is above 80 KiB, input waits without polling. A drain then wakes the engine, because `DRAIN_WAKE_ABOVE` (64 KiB) is below `INPUT_PAUSE_ABOVE` (80 KiB) (`runtime.rs:229`).
- **Cooked echo held back:** this case is rightly left out of `must_poll`. It sits above `ECHO_ABOVE` (80 KiB), so draining also wakes it.
- **Control flags and child exit:** new control flags always signal the waker, and an exit seen in the wait is handled by the very next `work()`.

The design doc's `Waker` and `wait` rows (design.md:858-860) agree with the code. No reference to the removed `Turn.poll` is left.

## Blocking findings

**F1 (P2, test coverage): no test fails if `turn()` goes back to deciding the wait before its events.**
- **Where:** `crates/libs/ui/rusty_conhost/src/runtime.rs:912-967`, and `sched.rs:273-293`.
- **Failure scenario (source-traced):**
  - The behaviour this commit changes is the order inside `Engine::turn` (`runtime.rs:528-530`): work first, then decide whether to poll.
  - Both new Windows tests go through `turns_without_another_wake` (`runtime.rs:934-942`). That helper calls `engine.work()` and `engine.must_poll()` directly and never calls `turn()`. The sched test only checks `must_poll` on views built by hand.
  - Mutant: in `turn()`, move `let until = self.must_poll().then(Instant::now);` above `self.work()?;`. That restores K1's bug: when one final slice leaves reads ready, or a write ending in ESC leaves input queued, the engine blocks in `wait` with work it could do straight away. All three new tests stay green.
  - The new tests do fail at `615e0824`, but only because `work`, `must_poll` and `owing_large_reads` don't exist there yet, not because they detect the bug.
- **Required correction:** drive the real `turn()`. Either:
  - have `turns_without_another_wake` call `engine.turn()` and fail if any one turn takes longer than a short deadline, with a child that sends no packets while the test runs (`ping` writes about once a second, which could end a blocked wait and hide the bug); or
  - move the wait-deadline decision into a helper that `turn()` calls after `work()`, and assert on that.
- **Regression test:** `reads_a_slice_leaves_ready_are_served_without_another_wake` and `input_left_behind_an_esc_cut_is_taken_without_another_wake`, rewritten to go through `turn()`, should both fail under the mutant above.

## Design questions
None.

## Optional improvements
- The `sched::must_poll` doc comment and the comment on `Engine::must_poll` say almost the same thing; one could point to the other.

## Checks performed
| Check | Result |
|---|---|
| `git fetch origin pull/1246/head` | Matches `2dfc3a6cd…` |
| `cargo test -p rusty_conhost --offline` (Linux) | Passed: 108 unit tests; `own_pty` has 0 tests on Linux |
| `cargo clippy -p rusty_conhost --all-targets --offline -- -D warnings` (Linux) | Passed (only manifest warnings from other crates) |
| `python3 .github/scripts/changes.py check` | Passed: 539 fragments ok |

## Validation gaps
- **Windows-only code:** `runtime.rs` is only compiled on Windows (`lib.rs:34-35`), and only the Linux target is installed here. So neither the changed engine code nor its three new tests were compiled, linted or run in this review. That includes `own_pty.rs` and every test in `runtime.rs`.
- **D4 Windows CI run:** design §D4 (design.md:683) requires a successful Windows CI run at this exact head before review. I couldn't check it: the sandbox blocked `api.github.com`, so I couldn't read the PR body or the run. The reviewer needs to see that run's result at `2dfc3a6c`.
- **Probe to confirm the K1 fix end to end:** on Windows, through `OwnPty`, hold 8 raw `ReadConsoleW` calls of 1 MiB or more, then write 513 win32-input-mode `a` records in a single write, then nothing more. All 8 reads should complete without any further input.

## Verdict
**changes required** (F1, P2).
