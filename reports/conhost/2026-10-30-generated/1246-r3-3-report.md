<!-- conhost/1246-r3 run 3, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 217 s, 1071476 tokens, $1.15 -->

# Review of Rusty-Mill/rusty_mill PR 1246, re-reviewed at 615e0824

- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Reviewed head:** `615e082460958ae36fbe19aaf705bc6898e70f5c`. `git fetch origin pull/1246/head` returned the same SHA, so the review is pinned.

The verdict is **changes required**. The fix for K2 adds a liveness defect (B1, P1): typed input can sit in the input lane while the engine sleeps. K1 is fixed.

## Prior findings at this head

- **K1 (P1, all full read replies built before any completes): resolved.** `serve_reads` (`console/read.rs:449-479`) now stops before a read that would push the call past `READ_REPLY_CAP`. Each read's own capacity is capped at 1 MiB too (`:320-323`, `:384-386`).
  - **Reply size:** every reply built is at most `r.capacity`. Records are `count × 20`; `read_chars` and `consume` are bounded by `capacity`; `finish_line` also consumes at most `r.capacity`.
  - **Progress:** a cut can only happen after at least one read has completed (`built > 0`). So each `ServeReads` turn either finishes a read or ends with `reads_cut` cleared, and the polling stops.
  - **Order:** new reads and GetConsoleInput queue behind the owed reads (`reads_behind`). Hang-up and CloseServer clear the flag (`console.rs:592-593`).
  - **Test:** `one_call_builds_at_most_one_reply_budget_and_the_rest_follows` covers it. It can't compile at base, because `READ_REPLY_CAP` and `Event::ServeReads` don't exist there.
- **K2 (P2, write boundaries lost when completed writes coalesce): decoding fixed, liveness broken (see B1).** `take_input` cuts a slice at each `esc_ends` entry, so Escape followed by typing decodes the same way however writes coalesce. But the cut makes the engine take fewer bytes than the turn planned, and the plan still believes the lane was drained.

## Blocking findings

### B1 (P1, liveness): input after two ESC-ending writes stays in the lane until an unrelated event

- **Trigger:** the terminal completes two or more writes that end in ESC, then more input, all before the engine takes from the lane. For example, the user presses Esc, Esc, x while the engine is busy with a packet or an output slice.
- **Where:**
  - `runtime.rs:264-281`: `take_input` returns fewer bytes than it was asked for when it cuts.
  - `sched.rs:98`: `poll` is computed from `input < v.input`, before the take.
  - `runtime.rs:574`: `let until = plan.poll.then(Instant::now);` is never revised after a cut.
  - The `Waker` is an auto-reset event (`platform-windows/src/condrv.rs:286-299`, `sys::auto_reset_event`), so all three writes leave it set only once.
- **Trace** (*source-traced*, and *executed* against a line-for-line model of `write`, `take_input` and `turn`'s poll, at `scratch/probe/esc_stall.rs`):
  - Lane `1b 1b 78`, event set.
  - **Turn 1:** `v.input` is 3 and the plan takes 3. The cut returns `[1b]` with `end=true`, and `poll` is false. `wait` returns at once, because the event was set, and that consumes it.
  - **Turn 2:** `v.input` is 2 and the plan takes 2. The cut returns `[1b]`, leaving `[78]`. `poll` is false (2 < 2 is false) and the event is clear, so **`wait` blocks**.
  - The model printed `stalled with [120] in the lane`.
  - The `x` reaches a waiting ReadConsole or GetConsoleInput only when the next key, packet, resize or drain arrives. A client blocked in a read sends no packets, so nothing else wakes the engine.
- **Who hits it:** a cooked cmd prompt (Esc clears the line), and any VT-input client such as edit.exe that sees Esc followed by a keystroke.
- **Correction:** after a take, keep polling whenever the lane still holds bytes and the output queue is at or below 80 KiB. For example, have `take_input` return the remaining length, and OR "bytes left" into `until`. Alternatively, call `waker.wake()` when a cut leaves bytes behind.
- **Regression test:** a `sched` or engine-level test that writes `1b`, `1b`, `78` before a turn. It should assert that every turn after a cut polls and that all three bytes are handed to the console within three turns. `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce` calls `take_input` directly, so it can't catch this.

### B2 (P2, docs disagree with code): §7.2's normative loop and §7.4's memory total no longer match the engine

- **The loop (`design.md:529-545`).** The engine now has a step between steps 3 and 4: `ServeReads` (`runtime.rs:549-551`). Step 4 also skips the input lane while `reads_ready` (`sched.rs:85-90`), and `poll` now includes `reads_ready`. §7.2's pseudocode, the "Bounded turn length" bullet and the "until = now when steps 2–4…" rule describe none of this. Only the §7.4 table row was edited.
- **The memory total.** `Lanes::esc_ends` (`runtime.rs:101-107`, push at `:175`) holds one `u64` per ESC-ending write, so up to one per queued byte: 64 Ki × 8 B = 512 KiB, more with `VecDeque` growth. The §7.4 table does not list it, and the "3.3 MiB in all" total omits it.
- **Correction:** update §7.2 (step, ordering, poll rule) and add the `esc_ends` row and total to §7.4. Or bound `esc_ends` (u32 offsets, or coalescing adjacent entries) and record that bound.
- No test applies; this is a docs defect.

## Design questions

- While `reads_cut` is set, a PeekConsoleInput with `NOWAIT` returns 0 records even though GetNumberOfInputEvents reports more than 0. This is the decided "queue behind" behaviour in §7.4. It only happens after a call has built 1 MiB of replies, which needs repeat-count win32 records. I haven't filed it as a defect; the owner may want it noted in §5.10.

## Optional improvements

- When `end_reads` empties the read list while `reads_cut` is still set, the next turn spends one `ServeReads` with nothing to serve and holds new input back for that turn. Clearing `reads_cut` in `end_reads` when `reads` is empty would avoid that.

## Checks and validation gaps

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | **pass**: 107 unit tests, plus the fixture replay. `own_pty.rs` ran 0 tests off Windows. |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux) | **pass**, no diagnostics |
| `python3 .github/scripts/changes.py check` | **pass** ("539 fragment(s) ok") |
| Probe `scratch/probe/esc_stall.rs` (model of the lane, sched and waker) | reproduces B1 |

Validation gaps:

- **Windows-only code:** `runtime.rs`, which includes the K2 test `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce` and the engine turn, compiles only on Windows. I couldn't build, lint or test it here.
- **`own_pty.rs` interactive tests** need a Windows run log.
- **B1 on Windows:** a probe would write `\x1b`, `\x1b`, `x` to `OwnPtyMaster` while a client holds a raw `ReadConsoleW`, with no further input. The read should complete within a turn; at this head I expect it to hang until another write.

## Verdict

**changes required**
