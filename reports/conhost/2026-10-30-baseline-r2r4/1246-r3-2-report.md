<!-- conhost/1246-r3 run 2, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 299 s, 1554102 tokens, $1.48 -->

# Review of Rusty-Mill/rusty_mill PR 1246: K1 is fixed, but K2's fix stalls keystrokes (changes required)

- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Reviewed head:** `615e082460958ae36fbe19aaf705bc6898e70f5c`. I fetched `pull/1246/head` and it equals the head SHA. The working tree has no changes to tracked files.

This round mainly re-checks K1 and K2, which the new commit `615e0824` addresses; the full PR diff is the input side (host PR 3, §10 row 3).

## Prior findings at this head
- **K1 (P1), all full read replies built before any completes: fixed.** `serve_reads` (`read.rs:449-478`) now stops once `built + r.capacity > READ_REPLY_CAP`, and each read's capacity is capped at 1 MiB (`read.rs:320-323`, `384-386`). Every path's output is at most that read's capacity. Each cut follows at least one completed read, so the follow-up turns always finish.
  - *Executed* probe in a scratch copy: 63 raw W reads of 1 MiB each, fed 700 records with repeat count 65535. The most built in one call was 1,048,576 B, and all 63 reads completed over 63 calls.
- **K2 (P2), write boundaries lost when writes coalesce: the decoding is fixed, but the fix adds a stall (B1 below).**

## Blocking findings

### B1 (P1): an ESC-cut slice leaves the rest of the input unread until something else wakes the engine
- **Where:** `runtime.rs:264-281` (`take_input`), `runtime.rs:552-559` and `runtime.rs:574`, `sched.rs:98`.
- **Trigger:** the terminal writes a bare ESC, then any further bytes, before the engine drains the input lane. This is the exact case the new test sets up.
- **Trace (source-traced, plus an executed transcription):**
  1. `sched::turn` decides `poll` from `input < v.input`, before any bytes are taken.
  2. With writes `"\x1b"` then `"x"`, the plan is `input = 2` with `poll = false`.
  3. `take_input(2)` now cuts at the ESC and returns `[0x1b], end=true`, leaving 1 byte in the lane.
  4. The engine then waits with `until = None`. The `Waker` is an auto-reset event that "collapses several wakes into one" (`condrv.rs:102`), so both writes' wakes were used up by the previous wait.
  5. The `x` sits in the lane until the next keystroke, packet, drain or child exit.
  - The probe printed: `plan.input=2 poll=false took=[27] end=true left=1`.
  - This breaks design §7.2 step 6 (`design.md:543`): "until = now when steps 2–4 left work they could still do".
- **Who hits it:** an idle cmd waiting in ReadConsole sends no packets. A user who presses Esc and then types quickly sees the typed key appear only on the next keystroke. Any VT-mode client is hit the same way whenever the terminal sends Escape as a bare `0x1b`.
- **Required correction:** after `take_input`, poll whenever the lane is still non-empty. For example, have `take_input` return the bytes left and set `until = now` when that is above 0, or move the cut into `sched` so `poll` knows about it.
- **Regression test:** extract the post-take poll decision into a pure function and test it on Linux: lane `"\x1b"` + `"x"`, plan `input = 2`, cut leaves 1 byte, so the turn must poll. Add a Windows `own_pty` test that writes `"\x1b"` and `"x"` back to back, with no other wake, and expects both keys to be read.

### B2 (P2): the `esc_ends` index is retained memory that §7.4 does not count
- **Where:** `runtime.rs:101-107` and `runtime.rs:172-176`; `design.md:621`.
- **Trace (executed transcription):** each completed write ending in ESC pushes a `u64`. A 64 KiB lane full of single-ESC writes gives 65,536 entries, a 524,288 B allocation (probe: `esc_ends len=65536 cap_bytes=524288`).
  - This is reachable with the reader stalled (input pauses above 80 KiB of output) and a terminal that sends bare ESC.
  - §7.4 counts the input lane as 64 KiB and claims "3.3 MiB in all"; this index adds 0.5 MiB that is not counted. It is bounded, so I graded it as a doc/cap mismatch rather than unbounded growth.
- **Correction:** either charge 8 B of lane room per entry, or store the cut points as a bitmap (65,536 bits = 8 KiB). Alternatively, count the index in the §7.4 table and the total.
- **Test:** a runtime unit test that fills the lane with single-ESC writes and asserts the index stays within its stated size.

### B3 (P2): the design doc does not describe the new engine loop or events
- **Where:** `design.md:540-554` and `design.md:166`.
- **What is out of date:**
  - The §7.2 loop has no serve-reads step.
  - Step 4 still says input is taken whenever output ≤ 80 KiB; the code also requires `!reads_ready`.
  - "Bounded turn length" leaves out the up-to-1 MiB `ServeReads` call.
  - The §3.3 `Event` enum lists neither `ServeReads` nor `InputMore`.
  - The ESC cut in the input lane is not described anywhere.
- **Correction:** update §3.3 and §7.2 to match `sched::turn` and `Engine::turn`.

### B4 (P2): an unrecorded divergence while reads wait to be served
- **Where:** `read.rs:324-329`, `read.rs:221-225` and `read.rs:264-273`.
- **Trace (executed):** while `reads_cut` is set, a `GetConsoleInput` with NOWAIT|NOREMOVE (a PeekConsoleInput) returned 0 records with 692 records queued. `GetNumberOfInputEvents` reports 692 at the same moment.
- **Conhost's answer:** it serves every waiting read in one pass when input arrives, so a later peek sees whatever is left. I could not verify the `inputBuffer.cpp` wake-up path (see validation gaps).
- **Client impact:** .NET `Console.KeyAvailable` (built on PeekConsoleInput) reads false for that window.
- **Correction:** record this in §5.10 or the §7.4 row. Otherwise, make `GetNumberOfInputEvents` and the peek agree.
- **Test:** a core `Event` test that pins whichever answer is chosen.

## Design questions
None.

## Optional improvements
- **Decoder cases that never complete:** `ESC [` or `ESC O` at the end of a write (Alt+[ / Alt+O on plain VT) stays incomplete even when `end` is true (`input.rs:240-262`). I believe conhost treats these as Alt keys at the end of a string, but I could not check the source; see validation gaps.
- **Leftover build artifacts:** the scratch directory holds stale targets from earlier rounds. `scratch/target` ran a `probe_one_call_budget` test that is not in this head, and it failed showing 67,238,910 B built in one call. That was the old K1 behaviour; I discarded it and reran everything in a fresh target dir.

## Checks and results
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost --offline` (fresh target dir, Linux) | **107 passed**, 0 failed |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` | clean |
| `python3 .github/scripts/changes.py check` | 539 fragment(s) ok, exit 0 |
| Probe: K1 per-call budget | max 1,048,576 B per call, 63/63 reads served |
| Probe: peek while reads are owed input | 0 records with 692 queued (B4) |
| Probe: ESC cut vs `sched::turn` | 1 byte left with `poll=false` (B1) |
| Probe: `esc_ends` size | 524,288 B (B2) |

- **New tests at BASE:** none of them compile at BASE or at `8ec54b85`, because they use `ServeReads`, `reads_ready` and `esc_ends`, which don't exist there. The K2 runtime test would return the coalesced bytes at `8ec54b85`, so it does guard the decoding.

## Validation gaps
- **Conhost source:** the blobless clone of microsoft/terminal was refused by the sandbox proxy (CONNECT 403). None of the parity statements above has a verified `path:line`; the probes that would settle them are listed below.
- **Windows-only code:** `runtime.rs` is `#[cfg(windows)]`, so `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce` and the `own_pty` tests did not run here. I have not seen any Windows CI result.
- **Probes to propose for `examples/conhost_probe.rs` under ConPTY:**
  - Write `"\x1b"` + `"x"` back to back; both keys should arrive.
  - Write `"\x1b["` at the end of a write; is it Alt+[?
  - PeekConsoleInput while several large waiting reads are being served.

## Verdict
**Changes required.**
