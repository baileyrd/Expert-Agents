<!-- conhost/1246-r3 run 2, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 172 s, 691313 tokens, $0.86 -->

I found one new blocking defect, introduced by the K2 fix. K1 is fixed. K2 is fixed for how bytes are cut into slices, but the cut can now leave typed input stuck.

**PR:** Rusty-Mill/rusty_mill #1246
**Base:** 66f4bc38c8181fd669c58f8e34df385a6afa895e
**Head reviewed:** 615e082460958ae36fbe19aaf705bc6898e70f5c (`git fetch origin pull/1246/head` returned exactly this SHA)

## Prior findings

- **K1 (P1), all full read replies built before any completes: fixed.** Each read's capacity is now capped at 1 MiB (`read.rs:320-323`, `384-386`). Only three events serve waiting reads in bulk: terminal input (`read.rs:120`), output drained (`console.rs:555`) and the new serve-reads event (`console.rs:551`). All three go through `serve_reads` (`read.rs:449-478`). It always tries the first read, then stops before any read whose capacity would push the call past 1 MiB (`read.rs:456`). So one call builds at most 1 MiB of replies.
  - The flag that marks a cut is only set after a read has completed. The next turn therefore always makes progress, and the forced poll can't spin.
  - New reads wait behind the cut reads (`read.rs:325`, `421`). A hang-up resets the flag (`console.rs:592-593`).
  - The design table's §7.4 "Read reply" row matches the code. The new tests go red without the change; they don't even compile at 8ec54b85.
- **K2 (P2), write boundaries lost when writes coalesce: fixed as stated, but the fix causes F1 below.** The lane records where each write ending in ESC ended (`runtime.rs:173-176`), and `take_input` cuts a slice there (`runtime.rs:268-274`). Only `take_input` drains the lane, so the recorded positions stay accurate.

## Blocking findings

### F1 (P1, liveness): input after two coalesced ESC-ending writes stalls until an unrelated event

- **Where:** `runtime.rs:264-279` (`take_input` can now return fewer bytes than planned), together with `sched.rs:98` (`poll` is planned from the lane length before the take) and `runtime.rs:574-576` (the turn ends in a blocking wait).
- **Trigger:** plain VT input, i.e. a terminal without win32-input-mode, where the Escape key is a bare `1b`. The terminal makes three writes before one engine turn: `1b`, `1b`, `78` ("x"). For example, the user presses Esc, Esc, x while the engine is busy echoing output.
- **Trace:** the sched part is *executed* in a scratch probe; the `take_input` cut and the waker are *source-traced*.
  1. The lane holds 3 bytes with ESC ends recorded at offsets 1 and 2. The `Waker` is an auto-reset event (`platform-windows/src/condrv.rs:289`), so the three writes leave one signal.
  2. **Turn A:** `take_control` sees 3 bytes, so `sched::turn` plans `input=3, poll=false`. `take_input(3)` cuts at offset 1 and returns `1b` with the end flag set. The wait returns at once, consuming the one signal.
  3. **Turn B:** the plan is `input=2, poll=false`. `take_input(2)` cuts at offset 2 and returns `1b`. Now `wait(…, until=None)` blocks: no signal is pending and no poll was planned.
  4. "x" stays in the lane until a client packet, the child exiting, or the user's next keystroke. cmd, sitting in a cooked ReadConsole, sends no packet, so the keystroke shows up only after the next key is pressed.
  5. Probe output: `turn A: input=3 poll=false`, `turn B: input=2 poll=false`.
- **Why it's a defect:** it breaks the rule that queued input makes progress on the turn that frees it, not on the next unrelated event. Before this commit `take_input` always returned `min(n, len)`, so whenever `poll` was false the planned take emptied the lane and nothing could be stranded.
- **Required correction:** the engine must poll whenever bytes remain in the lane after the take, not only when the plan said so. Either have `take_input` return the remaining length and set `poll |= remaining > 0`, or have `take_input` re-signal the waker when it cuts with bytes left.
- **Regression test:** a runtime test (Windows layer) that writes `1b`, `1b`, `x` before a turn and asserts "x" reaches the console within the following turns with no further write. Alternatively, move the post-take poll decision into a portable function so it gets a `sched` unit test.

## Design questions

None.

## Optional improvements

- `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce` tests `take_input` on its own. A turn-level test would have caught F1, so pair it with one.

## Checks

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | 93 passed, 0 failed. `tests/own_pty.rs` ran 0 tests (Windows-only). |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux, re-run on a clean copy of the head) | Clean |
| `python3 .github/scripts/changes.py check` | 539 fragments ok |
| Probe: `sched::turn` for lanes of 3 then 2 bytes, scratch copy | Executed: `poll=false` both times (supports F1) |

## Validation gaps

- `runtime.rs` and its tests are `#[cfg(windows)]`. The K2 test, the engine-loop changes and the Windows-only clippy lints were not run; Windows CI results were not seen.
- A Windows probe should confirm F1: in an `OwnPty` session with plain VT input, write `1b`, `1b`, `x` before the engine drains, then check that "x" reaches a raw ReadConsole without any further input.
- A Windows probe should also check K1 end to end: eight 1 MiB raw reads, each completed on its own turn, with nothing else arriving.

## Verdict

**Changes required.**
