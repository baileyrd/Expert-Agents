<!-- conhost/1246-r4 run 5, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 204 s, 1030301 tokens, $1.08 -->

I've checked the K1 fix and found one gap: no test covers where the fix's decision sits inside `turn()`. Writing up now.

# Review of PR 1246 (Rusty-Mill/rusty_mill), re-review after 615e0824

- **Base:** 66f4bc38c8181fd669c58f8e34df385a6afa895e
- **Reviewed head:** 2dfc3a6cd2136d98065b031cf0f65601568dcbd7. `git fetch origin pull/1246/head` returned this same SHA, so the head is pinned.
- **Scope this round:** the change since the last review is one commit, `2dfc3a6cd` ("decide the engine's wait on what the turn left"). It touches `runtime.rs`, `sched.rs` and some test plumbing in `console.rs` and `console/read.rs`. It changes no console API, mode or input decoding; it only changes how the engine schedules its turns. I did not rebuild conhost citations for the rest of the PR; earlier rounds reviewed it.
- **Parity:** conhost has no equivalent of this change. Its IO thread blocks on its ReadIo call, and terminal input reaches it on a separate thread, so it has no "poll or block" decision to compare against. I didn't clone microsoft/terminal because no API in this change needs an oracle.

## Prior finding

**K1 (P2), the engine waited on a poll decision made before the turn's events: fixed.**
- `Turn.poll` is gone. `Engine::turn` (`runtime.rs:527-553`) now runs `work()` first and only then decides whether to block. It calls `must_poll()` (`runtime.rs:606-608`), which checks a fresh state: reads ready now, input still in the lane, and output at or under 80 KiB (`sched.rs:102-104`).
- **It can't busy-spin (source-traced).** Every time `must_poll` is true, the next turn makes progress:
  - **Reads ready:** `serve_reads` only marks reads ready again after it has completed at least one read (`read.rs:456-461`). At most 64 reads wait, so this ends. If a cooked read's echo is held, the ready flag is cleared and the engine waits for the reader instead. `DRAIN_WAKE_ABOVE` (64 KiB) is below the echo limit, so the reader's next read wakes it.
  - **Input left:** `sched::turn` takes at least one byte. The cut after a write that ended in ESC moves forward by at least one byte, because the recorded ESC positions strictly increase and the cut never passes the first one (`runtime.rs:264-279`).
  - **After a panic:** the console is torn down, so the engine loop exits rather than spinning on a ready flag that can no longer clear.
- **Every work-creating event is now followed by the check.** A packet served after the wait is followed by the next turn's `work()`, and the check runs after that.

## Blocking findings

**K2 (P2, missing regression test). No test checks that `turn()` decides on the state *after* `work()`.**
- **File and line:** `crates/libs/ui/rusty_conhost/src/runtime.rs:527-530` (the ordering) and `runtime.rs:929-941` (the test helper).
- **Why no test catches it:** `turns_without_another_wake` copies the engine loop by calling `engine.work()` and `engine.must_poll()` itself. It never calls `turn()`. The sched test only checks the pure `must_poll` formula.
- **Mutant that every test misses (source-traced):** swap the two lines so the check runs first: `let until = self.must_poll().then(Instant::now); self.work()?;`.
  - Both new runtime tests still pass, because they don't go through `turn()`.
  - On Windows the mutant blocks with work it could do. A cooked read's echo is held, and two raw reads that declare 1 MiB each are queued behind it. The reader drains output. Its `read` sets `output_drained` and wakes the engine, and the previous turn's wait uses up that wake.
  - The mutant checks before `work()`: the lane is empty and no reads are ready, so it decides to block.
  - `work()` then handles `OutputDrained`, which calls `serve_reads` (`console.rs:552-556`). That completes the cooked read and the first raw read, then hits the 1 MiB reply budget, leaving reads ready.
  - The engine then blocks with a read it could serve at once. It stays blocked until an unrelated wake arrives, and the reader only sends another one when more than 64 KiB is queued.
- **Required correction:** add a test that calls `Engine::turn()` itself.
  - Spend the waker first, for example with one `server.wait(&waker, None, Some(Instant::now()))`.
  - Leave the console in a state where the turn's own events create the work, such as the `OutputDrained` case above, or the existing ESC-then-`x` case.
  - Run `turn()` on a thread and use `recv_timeout` to assert that it returns and that the next turn serves the work. Unblock a failing run with `hang_up`.
  - This test should hang or fail with the mutant and pass at this head.

## Design questions

None.

## Optional improvements

- `sched::tests::the_wait_polls_on_what_the_turn_left_not_on_its_plan` only checks the `must_poll` formula on hand-built views. It could be described that way, since the ordering it is named after lives in `runtime.rs`.

## Other failure classes for this change

- **Containment:** the change adds no decoding or `?` paths.
- **Retained memory:** the change adds no buffers.
- **Amplification:** the CPU-spin risk is ruled out above.
- **Boundaries:** the ESC cut is covered by `input_left_behind_an_esc_cut_is_taken_without_another_wake`, which is Windows-only.
- **Custody:** unchanged. If `work()` fails, `run` tears down. If the server closes, `turn` returns before waiting.

Docs: §7.2 step 6 ("until = now when steps 2–4 left work they could still do") matches the code. The change fragment and `THIRD_PARTY_NOTICES.md` don't need to change for this commit.

## Checks run

| Check | Result |
|---|---|
| Pin: fetched head equals 2dfc3a6c | ✅ |
| `cargo test -p rusty_conhost` (Linux) | ✅ 108 passed, 0 failed; `own_pty.rs` ran 0 tests (Windows-only) |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux) | ✅ no warnings (finished in 0.21 s, so mostly cached) |
| `python3 .github/scripts/changes.py check` | ✅ "539 fragment(s) ok" |
| Clippy for `x86_64-pc-windows-gnu` | ❌ could not run: the Windows target isn't installed, and the sandbox denied `static.crates.io` when cargo tried to download `windows-sys` |

## Validation gaps

- **New Windows-only tests:** `reads_a_slice_leaves_ready_are_served_without_another_wake` and `input_left_behind_an_esc_cut_is_taken_without_another_wake` are `cfg(windows)`. I couldn't compile or run them. By reading, their helpers exist and are visible: `ping_for_a_while`, `SIZE`, `Signal`, `Host.c`, and `owing_large_reads` re-exported under `cfg(windows)`.
- **Windows build:** I didn't see clippy or a build for the Windows code in `runtime.rs` and `platform-windows`, nor a Windows CI result for this head.
- **Old base:** the new tests don't compile at 66f4bc38 (`work`, `must_poll` and `owing_large_reads` don't exist there). That shows nothing about what they guard, which is why K2 uses the mutant above instead.

## Verdict

**Changes required.** K1 is fixed in the code, but K2 is open: nothing goes red if the decision moves back before the turn's work.
