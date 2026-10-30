<!-- conhost/1246-r4 run 4, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 79 s, 299330 tokens, $0.44 -->

K1 is fixed at this head, and the change since 615e0824 adds no new defect.

**Repository:** Rusty-Mill/rusty_mill, PR 1246
**Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
**Head:** `2dfc3a6cd2136d98065b031cf0f65601568dcbd7`. I fetched `pull/1246/head` and it equals this SHA.

**Scope:** this round covers the re-check of K1 and the one commit added since the last review, `2dfc3a6cd` ("decide the engine's wait on what the turn left"). It is not a fresh full review of the 3,900-line diff; the other files are as reviewed at 615e0824.

## Prior finding

**K1 (P2), "the engine waits on a poll decision made before the turn's events": resolved.**
- `Turn.poll` is removed from `sched.rs`. `Engine::turn` (`runtime.rs:527-553`) now runs `work()` first, then sets `until` from `must_poll()` (`runtime.rs:530`).
- `must_poll()` reads a fresh view taken after the turn's events: reads still owed, input still queued and current output (`runtime.rs:606-608`, `sched.rs:102`). Both cases from the original finding now poll:
  - **Reads left owed:** a final input slice that leaves reads waiting sets `reads_cut`, so `reads_ready` is true.
  - **Input left after an ESC cut:** `take_input` stops at the end of a write that ended in ESC and leaves bytes in the lane, so `input_len() > 0`.
- The design agrees. §7.2 step 6 says "until = now when steps 2–4 left work they could still do", which is what the code now does.

**Can the new condition spin hot?** I checked each way it could and ruled them out from the source:
- **Owed reads:** each `ServeReads` (`read.rs:449-480`) either completes at least one read, because the first read is always tried, or clears `reads_cut`. At most 64 calls can wait, so this ends.
- **Queued input:** `must_poll` polls for queued input only while output is at or under `INPUT_PAUSE_ABOVE` and no reads are owed. That is exactly when `sched::turn` takes input (`sched.rs:84-88`), so every poll turn consumes bytes. When output is over the pause, it doesn't poll and waits for the drain wake, as before.
- **After a panic:** `die` calls `teardown`, which sets `server` to `None`, so `run` leaves its loop (`runtime.rs:747-756`, `513`). No turn runs with `reads_cut` stuck.
- **Control flags:** `must_poll` ignores the control lane (`Control::default()`). That is fine because every control setter calls `waker.wake()` (`runtime.rs:193`, `207`, `231`), and the auto-reset event stays set until the next `wait`.

## Blocking findings
None.

## Design questions
None.

## Optional improvements
- **The new engine tests don't go through `turn()`.** Both tests (`runtime.rs:933-968`) call `work()` and `must_poll()` themselves. So a regression inside `turn()`, such as deciding `until` before `work()` again, would still pass them. A test-only hook that records the `until` that `turn()` passes to `wait` would cover the code path K1 was about.
- **The regression test can't simply be run at the old commit.** `the_wait_polls_on_what_the_turn_left_not_on_its_plan` won't compile at 615e0824 because `must_poll` didn't exist there. Its scenario does fail in substance there: `before` has `input: 10`, the plan takes all 10, and the old `poll` would have been false.

## Checks performed
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | 108 passed, 0 failed |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux) | clean |
| `python3 .github/scripts/changes.py check` | "539 fragment(s) ok" |
| `cargo clippy --target x86_64-pc-windows-msvc` | could not run: the target isn't installed and `windows-sys` can't be downloaded offline |

## Validation gaps
- **Windows-only engine tests not run:** `reads_a_slice_leaves_ready_are_served_without_another_wake` and `input_left_behind_an_esc_cut_is_taken_without_another_wake` are `#[cfg(windows)]`, so I neither compiled nor ran them. The same goes for the `#[cfg(windows)]` re-export of `owing_large_reads`. A Windows CI run of `cargo test -p rusty_conhost` would settle this.
- **Windows clippy not seen:** I did not see `cargo clippy --all-targets -- -D warnings` pass on Windows.

## Verdict
**No blocking findings.**
