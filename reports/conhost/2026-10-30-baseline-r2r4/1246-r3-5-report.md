<!-- conhost/1246-r3 run 5, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 221 s, 1132786 tokens, $1.05 -->

# Review of PR 1246 (Rusty-Mill/rusty_mill): changes required

- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Reviewed head:** `615e082460958ae36fbe19aaf705bc6898e70f5c`. I fetched `pull/1246/head` and its SHA matches.
- **Delta since the last review:** commit 615e08246, which fixes K1 and K2.

K1 is fixed. K2's slicing works, but the change introduces two new defects and leaves the design doc out of date.

## Prior findings, re-checked
- **K1 (P1), all full read replies built before any is completed: fixed.** `serve_reads` (`console/read.rs:449-478`) stops once a call has built 1 MiB of replies. The reads it didn't reach stay queued and set `reads_cut`. The scheduler then sends `ServeReads` before taking any new input (`sched.rs:87-98`, `runtime.rs:549-551`). New reads and GetConsoleInput wait behind the queued reads (`read.rs:325, 421`). Each turn always tries the first read, so the queue keeps draining. Each read's capacity is capped at 1 MiB (`read.rs:320-323, 384-386`), as §7.4 already records. **Executed:** when I disabled the budget check in a scratch copy, `one_call_builds_at_most_one_reply_budget_and_the_rest_follows` failed at `read.rs:1106`. So the test guards the fix.
- **K2 (P2), write boundaries lost when completed writes coalesce: the slicing is fixed, but the fix adds B1 and B2.**

## Blocking findings

**B1 (P1): input after an ESC cut can sit in the lane indefinitely.**
- **Where:** `runtime.rs:264-279` (`take_input`), with `sched.rs:98` and `runtime.rs:552-574`.
- **Trigger:** the terminal sends one write ending in ESC and then another write before the engine thread runs. This is the exact case the K2 fix exists for.
- **Trace (source-traced; scheduler part executed):**
  1. The engine is blocked in `server.wait` with no deadline.
  2. The terminal writes `"\x1b"`, then `"x"`. Both call `SetEvent` on the auto-reset waker (`condrv.rs:289`), which leaves a single signal.
  3. The engine wakes and the signal is consumed. `take_control` sees `input = 2`, so `turn` plans `input = 2, poll = false`, because poll is only set when the planned take is less than `v.input`. I ran this in a scratch probe and got `PROBE input=2 poll=false`.
  4. `take_input(2)` cuts at the ESC end and returns only `"\x1b"`.
  5. `wait` then blocks again with nothing signalled. `"x"` stays in the lane until some unrelated write, packet or child exit wakes the engine.
- **Why it's new:** before this commit, `take_input` could never return fewer bytes than planned, because the lane only grows between `take_control` and `take_input`. §7.2 step 6 requires `until = now` when steps 2–4 leave work; the code now breaks that rule.
- **Who hits it:** any interactive client (cmd, PSReadLine, microsoft/edit). Press Esc and then a key quickly, or Esc then Enter, and the second key doesn't arrive until the user presses something else.
- **Fix:** when `take_input` leaves bytes in the lane, the engine should poll again. Either return the remaining length and set `until = now`, or call `waker.wake()` after a short take.
- **Regression test:** a Windows `own_pty`/runtime test. Write `"\x1b"` and then `"x"` while the engine is parked, with no further writes, and assert both reach the client. Add a core or scheduler-level assertion that a short take forces a poll.

**B2 (P1, broken cap): the new `esc_ends` list grows with no limit beyond the 64 KiB lane cap.**
- **Where:** `runtime.rs:101-107, 170-176`.
- **Trigger:** terminal input with many writes that each end in ESC while the engine isn't taking input. That happens while output is above 80 KiB or while `reads_ready` is set. Held-down Esc autorepeat is one example.
- **Trace (source-traced):** each write ending in ESC pushes one `u64`, and entries are only removed by `take_input`. 65,536 one-byte `"\x1b"` writes fill the 64 KiB lane. That leaves 65,536 entries × 8 B = 512 KiB in `esc_ends`, which is 8× the lane itself.
- **Why it's a defect:** this buffer isn't among §7.4's named buffers or in its 3.3 MiB total. Its own comment ("at most one per queued byte") admits the bound is the lane length, not its bytes.
- **Fix:** count each entry against `INPUT_LANE_CAP` and block the writer as with lane room, or merge consecutive ESC-only writes into one entry. Then update §7.4.
- **Regression test:** a runtime test that does 65,536 `write(b"\x1b")` calls and asserts `esc_ends.len()` (or its byte cost) stays inside the documented cap. This needs Windows: `runtime` is `#[cfg(windows)]`, so my probe wouldn't build on Linux.

**B3 (P2): the design's engine loop no longer describes the code.**
- **Where:** `docs/projects/term/conhost/design.md:533-554` (§7.2).
- **Problem:** the loop has no `ServeReads` step. Step 4 doesn't say input pauses while reads are owed it, and "Bounded turn length" doesn't count the up-to-1 MiB of reads served per turn. The commit only changed the §7.4 table row.
- **Fix:** add the step between 3 and 4, state the pause condition, and add the per-turn read budget.

## Design questions
- Every write ending in ESC is now always treated as the Escape key. If rusty_term ever splits a paste or a key sequence across writes right after an ESC (for example the ESC that starts `ESC[201~`), the decode is now consistently wrong instead of only sometimes. This rests on the existing rule in `input.rs:241` ("a terminal writes a key's sequence whole"). I couldn't find rusty_term's paste chunking to confirm or rule it out.

## Optional improvements
- `reads_cut` is not cleared in `clear_input` or `end_reads`. It costs at most one extra empty `ServeReads` turn, so it's harmless.

## Checks performed
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (head, Linux) | 107 passed, 0 failed |
| The three new K1/scheduler tests | ran and passed |
| K1 budget test with the budget disabled (scratch copy) | failed, as it should |
| Scheduler probe for a short take | `poll=false`, confirming B1's premise |
| `cargo clippy -p rusty_conhost --all-targets -D warnings` | clean |
| `python3 .github/scripts/changes.py check` | 539 fragments ok |

## Validation gaps
- **Windows-only tests:** the runtime tests, including the new K2 test `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce`, and `own_pty.rs` are Windows-only, so I didn't run them. The B1 and B2 regression tests also need Windows.
- **conhost source:** the network sandbox refused my clone of microsoft/terminal@09beddc6 from github.com. I couldn't add conhost citations for this delta. Its only API-visible change is the 1 MiB clamp on ReadConsole, RAW_READ and GetConsoleInput, which §7.4 records. The proposed Windows probe is a 2 MiB raw `ReadConsoleW` with more than 1 MiB of input queued, to confirm how much conhost returns.

**Verdict: changes required** (B1 and B2 are P1, B3 is P2).
