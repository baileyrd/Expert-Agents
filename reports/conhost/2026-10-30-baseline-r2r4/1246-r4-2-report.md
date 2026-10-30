<!-- conhost/1246-r4 run 2, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 118 s, 393179 tokens, $0.60 -->

I'm requesting changes for PR 1246 (Rusty-Mill/rusty_mill). The old finding K1 is fixed, but tracing it turned up one new P1 with the same root cause, which I confirmed by running a probe.

**Pin**
- Base: `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- Reviewed head: `2dfc3a6cd2136d98065b031cf0f65601568dcbd7`. I fetched `pull/1246/head` and it resolves to this SHA, so the review is pinned.
- The diff is 7 commits; the re-review centres on 2dfc3a6c ("decide the engine's wait on what the turn left").

## Prior finding re-checked

**K1 (P2): fixed.** `Turn.poll` is removed. `Engine::turn` (`runtime.rs:529-530`) now calls `work()` first and then `must_poll()`, which looks at the state the turn left: reads still ready, input still queued, and output at or below the pause threshold. That covers both cases from the earlier review: a slice that leaves reads ready, and input left over after an ESC cut.
- I checked whether a wake could still be lost. Control flags and terminal writes that arrive during the turn signal the waker. A child exit is reported by the wait itself. Input paused above 80 KiB is restarted by the reader's drain wake, because 64 KiB is below the 80 KiB pause.
- Tests added: `the_wait_polls_on_what_the_turn_left_not_on_its_plan` (Linux, passes) and two engine tests that only build on Windows, `reads_a_slice_leaves_ready_are_served_without_another_wake` and `input_left_behind_an_esc_cut_is_taken_without_another_wake`. I couldn't run the Windows ones.

## Blocking findings

### N1 (P1): READ_IO admission is still decided before the turn's events, and the wait doesn't account for it

- **Where:**
  - `sched.rs:94`: `submit_read` is computed from the view at the start of the turn.
  - `runtime.rs:560` / `588`: `work()` acts on that early plan.
  - `sched.rs:102` / `runtime.rs:606`: `must_poll` ignores a READ_IO that is now allowed but wasn't sent.
- **Trigger:** 64 calls are waiting in the host. A hostile client can arrange this, or so can 64 threads or clients blocked in ReadConsole. The same happens when parked bytes plus 64 KiB is over 1 MiB.
- **Trace (executed in a scratch copy of the head):**
  1. One client connects and holds 64 raw ReadConsoleW reads (`hold_raw_reads(.., 64, 64)`).
  2. At the start of the turn the lane holds 1 byte (`a`) and 64 calls are waiting. `sched::turn` returns `input = 1` and `submit_read = false`.
  3. `Event::Input(b"a")` completes one read. The probe printed `after: waiting=63 parked=1260 reads_ready=false`.
  4. `admits_read(63, 1260)` is now true, but `must_poll(after)` is false.
  5. In the engine that means `work()` sends no READ_IO and leaves `read_in_flight` false. `server.wait(waker, exit, None)` then blocks with nothing in flight.
- **Wrong result:** every client's next call (for example the reading app's WriteConsole echo, or a different client's output) stays in ConDrv unserved. It is only picked up after the next terminal keystroke, resize, output-drain wake or child exit. If the user is idle, that is indefinitely. One client's packets therefore stall all the others, which breaks the containment rule.
- **Design conflict:** §7.4 says "READ_IO is submitted only while fewer than 64 calls wait". Here 63 are waiting and nothing is submitted. §7.2 step 6 says the wait polls when the turn left work it could still do.
- **Also affects:** `Event::ServeReads` completing reads, and `OutputDrained` releasing parked writes. The `OutputDrained` path already existed at the base. This PR makes the stall reachable through terminal input and waiting reads, both of which it adds.
- **Required correction:** decide the submit after the events. Either recompute `!read_in_flight && admits_read(waiting, parked)` in `work()` after handling, or have `must_poll` also return true when `!closed && !read_in_flight && admits_read(..)`. Recomputing in `work()` is better, because it doesn't spend an extra turn.
- **Regression tests that go red without the fix:**
  - Linux `sched` test: `must_poll(&View { waiting: 63, read_in_flight: false, ..})` is true, or a check that `work` admits on the post-event view.
  - Linux core-plus-sched test: my probe above, with its last assertion flipped.
  - Windows engine test in the style of the K1 tests: `owing`-style 64 held reads, `shared.write(b"a")`, `turns_without_another_wake`, then assert `engine.read_in_flight`.

## Design questions
None.

## Optional improvements
- The K1 engine tests stand in for `turn()`'s wait by calling `must_poll()` directly. A short comment linking them to `turn()` would stop the two drifting apart.

## Checks run
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | 108 passed, 0 failed (two other test binaries had 0 tests) |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` | clean |
| `python3 .github/scripts/changes.py check` | 539 fragment(s) ok |
| N1 probe (`probe_submit_after_events`, scratch copy) | ran; confirmed the stall state shown above |

## Validation gaps
- The `#[cfg(windows)]` engine tests (`runtime.rs` tests, including both K1 engine tests) and `tests/own_pty.rs` interactive tests need a Windows run, which I couldn't do.
- This round focused on the K1 commit and the engine/scheduler path it touches. I didn't re-trace the rest of the 3.9k-line input diff against conhost source this round.

## Verdict
**Changes required** (N1).
