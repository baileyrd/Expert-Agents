<!-- conhost/1246-r4 run 2, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 135 s, 582062 tokens, $0.72 -->

I re-checked K1 at this head: the fix is right, but nothing tests the line that carries it, so the verdict is **changes required** on one P2 coverage finding.

**PR:** Rusty-Mill/rusty_mill #1246 · base `66f4bc38c8181fd669c58f8e34df385a6afa895e` · head `2dfc3a6cd2136d98065b031cf0f65601568dcbd7`. `git fetch origin pull/1246/head` resolves to the same SHA, the local HEAD matches, and the base is an ancestor.

**Scope of this round:** I checked K1 and the one commit added since the last review (`2dfc3a6cd`, which touches `runtime.rs`, `sched.rs`, `console.rs` and `console/read.rs`), plus the design's §7.2 text against it. I did not re-review the other six commits of the PR (the 615e0824 tree) in this round.

## Prior finding K1 (P2): the wait's poll decided before the turn's events
**The defect is fixed at this head.**
- `Engine::turn` (`runtime.rs:527-554`) now runs the turn's work first, then decides the wait at line 530 with `self.must_poll()`. That check looks at fresh state (`runtime.rs:606-608`): the console's `reads_ready()`, the input lane's current length and the output queue's current length. The old plan-time `Turn.poll` is gone.
- **Agreement with the planner:** `sched::must_poll` (`sched.rs:102-104`) is true only when `sched::turn` (`sched.rs:82-96`) would then do work: either serve the owed reads, or take a slice while output is at most 80 KiB.
- **No hot spin:** every such turn makes progress.
  - `serve_reads` always attempts the first owed read, because the budget cut needs `built > 0` (`console/read.rs:456`).
  - `take_input` takes at least one byte or pops one ESC end. An ESC end always sits past `taken`, since it counts the ESC itself (`runtime.rs:174`).
- **Paused input resumes:** when input waits on output above 80 KiB, the reader's drain wake fires on any read that starts above 64 KiB (`runtime.rs:229`). Echo held at the 80 KiB reserve resumes the same way.
- **Panic path:** `die` → `teardown` sets `server` to `None`, so the loop ends instead of polling on stale state.
- **Design text:** §7.2 step 6 ("until = now when steps 2–4 left work they could still do") now matches the code.

## Blocking findings

### R1 (P2, missing test): nothing goes red if the K1 fix is reverted
- **Where:** `crates/libs/ui/rusty_conhost/src/runtime.rs:530`, and the test helper at `runtime.rs:930-941`.
- **Trigger:** any edit that moves line 530 back before `self.work()`, or replaces `self.must_poll()` with a plan-time value or with `false`.
- **Why nothing catches it (source-traced):**
  - The two new Windows tests drive `turns_without_another_wake`. That helper calls `engine.work()` and then `engine.must_poll()` itself, so it rebuilds the fixed ordering inside the test rather than running `Engine::turn`.
  - `grep` shows no test calls `turn()`. The only caller is `run()` (`runtime.rs:518`).
  - The new sched test `the_wait_polls_on_what_the_turn_left_not_on_its_plan` (`sched.rs:274`) checks the pure `must_poll` function on views written by hand. It knows nothing about when the engine calls it.
  - The integration tests in `tests/own_pty.rs` never write a slice that leaves reads owed, or a write ending in ESC followed by more input. So the real `run()` loop never meets the K1 case either.
- **Result:** the two scenarios from K1 can regress with every test green:
  - eight large raw reads left owed by one input slice;
  - `ESC` then `x` coalesced in the lane.

  After the regression, the engine blocks in `server.wait` with work it could do at once, until some unrelated wake arrives.
- **Required correction:** make the regression test go through `Engine::turn`'s own wait decision. Either:
  - drive `engine.turn()` in the two Windows tests with the writers' one wake spent, under a watchdog that fails the test if a turn blocks longer than a bound; or
  - factor the wait deadline into one method (say `fn wait_until(&self) -> Option<Instant>`), call it from `turn()` after `work()`, and assert it in the tests.
- **Test that should go red:** `reads_a_slice_leaves_ready_are_served_without_another_wake` and `input_left_behind_an_esc_cut_is_taken_without_another_wake`, rewritten to call `turn()`, should time out when line 530 is reverted.

## Design questions
None.

## Optional improvements
- `turns_without_another_wake` leaves a READ_IO in flight against the attached `ping` because the test never waits; `teardown` cleans it up. A comment saying so would save the next reader a trace.

## Checks performed
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost --offline` (Linux) | Ran and passed: 108 unit tests, including the new `sched` test. `own_pty` and doc-tests have 0 tests on Linux. |
| `cargo clippy -p rusty_conhost --all-targets --offline -- -D warnings` (Linux) | Ran and finished with no crate warnings. The only warnings were unrelated manifest ones in `rusty_tls` and `rusty_h2`. |
| `python3 .github/scripts/changes.py check` | Ran and passed: 539 fragments ok. |

The tests and clippy were built in a separate target directory under `scratch/`, so the reviewed tree was not touched.

## Validation gaps
- **Windows-only code, not compiled or run:** `runtime` is `#[cfg(windows)]`, so the new engine tests and `Engine::turn` were neither compiled nor run here. Only `x86_64-unknown-linux-gnu` is installed. Still owed on Windows:
  - `cargo test -p rusty_conhost`, with particular attention to the two new engine tests;
  - `tests/own_pty.rs`;
  - clippy for the Windows target.
- **The test's turn count is traced, not run.** The `turns > 1` assertion in `reads_a_slice_leaves_ready_are_served_without_another_wake` should hold: one read is served per turn after the first, so 8 turns for 8 reads. That comes from reading `serve_reads`; I have not seen a Windows run.

## Verdict
**Changes required.** K1's defect is fixed, but the fix has no regression test that exercises it (R1, P2).
