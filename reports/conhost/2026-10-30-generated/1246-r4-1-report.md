<!-- conhost/1246-r4 run 1, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 78 s, 398663 tokens, $0.47 -->

# Review of PR 1246: the K1 fix holds and nothing blocks the merge

**Repository:** Rusty-Mill/rusty_mill. **PR:** 1246. **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`. **Head reviewed:** `2dfc3a6cd2136d98065b031cf0f65601568dcbd7`. I fetched `pull/1246/head`, and it matches the head SHA.

**Verdict: no blocking findings.** K1 is fixed. The two engine tests that show it are Windows-only, and I could not run them.

## K1 (P2): the engine waited on a poll decision made before the turn's events — fixed

Commit `2dfc3a6cd` addresses it.
- **The change:** `Turn.poll` is removed. `Engine::turn` (`runtime.rs:527`) now runs `work()` first. Only then does it decide whether the wait may block, by calling `must_poll()` (`runtime.rs:606`). That call builds a fresh view of the state the turn left: reads ready, input still in the lane, and output length. `sched::must_poll` (`sched.rs:102`) returns `v.reads_ready || (v.input > 0 && v.output <= INPUT_PAUSE_ABOVE)`.
- **Both original cases are covered:**
  - A final input slice that sets `reads_cut` is now seen before the wait.
  - An input slice cut at a write that ended in ESC leaves bytes in the lane, which `input_len()` sees before the wait.
- **The packet path is also safe.** A packet served after the wait can leave state behind, but the next turn's `work()` and `must_poll()` both run before that turn's wait.

I also checked that the new poll cannot spin hot or stall:
- **Input left in the lane:** the next turn takes `min(input, INPUT_SLICE)`. `take_input` always takes at least one byte, because each ESC end is recorded only after a non-empty write is added, so it is always ahead of what has been taken. Each polled turn therefore makes progress.
- **Reads ready:** `serve_reads` clears `reads_cut` before it runs, and the first read is always served (`built == 0`). The flag cannot stay set without progress.
- **Closed or panicked console:** `close()` clears `reads_cut` (`console.rs:593`). `die()` tears the server down, so the loop exits.
- **Output above the 80 KiB pause, or echo held:** there is no poll. The engine waits for the drain wake. `DRAIN_WAKE_ABOVE` is 64 KiB and the pause is 80 KiB, so a reader draining a paused queue always sends that wake, and `OutputDrained` then retries the held reads.
- **Docs:** the design's §7.4 read-reply row ("the next turns serve the rest before taking new input, without waiting for more") matches the code. The change fragment makes no claim about this.

**Test coverage:**
- `sched::tests::the_wait_polls_on_what_the_turn_left_not_on_its_plan` targets the bug directly. At the old code, the view with `input: 10` gave `poll = reads_ready || 10 < 10 = false`, which is the defect. The test can't run against the old code as written, because `must_poll` didn't exist there.
- `reads_a_slice_leaves_ready_are_served_without_another_wake` and `input_left_behind_an_esc_cut_is_taken_without_another_wake` drive the real engine. They sit in `runtime.rs`, which only builds on Windows.

## Blocking findings

None.

## Design questions

None raised by this change.

## Optional improvements

- `turns_without_another_wake` checks `must_poll()` only through the helper and never through a real `server.wait` with a past deadline. A Windows log of the two engine tests passing would confirm the whole wait path end to end.

## Checks

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | 108 passed, 0 failed |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux host) | finished clean (one unrelated manifest warning from `rusty_h2`) |
| `python3 .github/scripts/changes.py check` | 539 fragment(s) ok |

## Validation gaps

- I could not run the two new Windows-only engine tests (`reads_a_slice_leaves_ready_are_served_without_another_wake`, `input_left_behind_an_esc_cut_is_taken_without_another_wake`), nor the `own_pty.rs` integration tests. They need a Windows run log.
- I did not run Clippy for the Windows target (`x86_64-pc-windows-msvc`), so the `#[cfg(windows)]` code was never linted. It needs the CI result.
- This pass re-checked K1 and its surrounding liveness paths at the new head. I did not repeat the full predict-and-break pass over the rest of the 3,900-line diff.

**Verdict: no blocking findings.**
