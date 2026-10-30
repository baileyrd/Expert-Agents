<!-- conhost/1246-r4 run 1, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 483 s, 3032065 tokens, $2.90 -->

# Review of Rusty-Mill/rusty_mill PR 1246 (rusty_conhost: console input, host PR 3)

- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Head reviewed:** `2dfc3a6cd2136d98065b031cf0f65601568dcbd7`. `git fetch origin pull/1246/head` gives `FETCH_HEAD` equal to this SHA, and the local HEAD matches it.

**Verdict: changes required.** K1 is fixed, but I found two P1 input-loss defects (both reproduced on Linux) and three P2 defects.

## Prior finding K1, re-checked
**Fixed in the code.** `Engine::turn` (`runtime.rs:527-530`) now runs `work()` first. Only after the turn's events does it decide whether to poll, using `must_poll()` (`runtime.rs:606`, `sched.rs:102`) on a fresh view: reads still ready, input still queued, output under the pause.

I checked whether anything else can leave work with no wake to announce it. Nothing does:
- **Reader drain or held echo:** the drain arrives through the control lane and wakes the engine.
- **A packet served after the wait:** the next turn always runs `work()` before it waits.
- **Endless polling on `reads_cut`:** each pass completes at least one read before it can set the flag again, so it ends.

The tests for the fix don't guard it, though; see F5.

## Blocking findings

### F1 (P1): Alt+[, Alt+Shift+O, Alt+] and Alt+Shift+P typed over plain VT hang or change the user's next keys
- **Where:** `input.rs:250-257` (`escape`, which sends `[`, `O`, `]` and `P` to `csi`, `ss3` and `string`), `input.rs:275-289` and `input.rs:197-203`.
- **Trigger:** a terminal that sends plain VT keys rather than win32-input-mode. The design supports this path (§5.5, V8), and the `rusty_mux` TUI strips `?9001` so its terminal keeps sending plain VT keys (`rusty_mux/crates/tui/src/lib.rs:36-40`).
- **What goes wrong:** the decoder treats a lone ESC at the end of a write as the Escape key, because "a terminal writes a key's sequence whole" (`input.rs:241-243`). It doesn't apply the same rule to `ESC [`, `ESC O`, `ESC ]` or `ESC P` at the end of a write; those wait for more bytes whatever `end` says.
- **Executed probe** (core `Event` test in a scratch copy):
  - Cooked read waiting, then `Input("\x1bP")` followed by `d`, `i`, `r`, `\r` as separate writes: 0 reads complete, nothing is echoed, and all 6 bytes sit in the decoder. Typing stays swallowed until an ESC arrives, which drops it all as `Unknown` (`input.rs:282`), or until 4,096 bytes pass, when it is dropped at the cap (`input.rs:197-202`). So the user's `dir⏎` is lost.
  - `\x1b[` then `a`: no records at all; both keys are lost.
  - `\x1bO` then `A`: produces a VK_UP record (38). Alt+Shift+O followed by Shift+A becomes Up.
- **conhost (from memory, not checked at `09beddc6`; see validation gaps):**
  - `StateMachine::ProcessString` flushes at the end of the string for the input engine (`InputStateMachineEngine::FlushAtEndOfString` returns true).
  - So `ESC x` ending a string goes to `_ActionEscDispatch`, which gives Alt+x.
- **Fix:** when `end` is true, finish `ESC [`, `ESC O`, `ESC ]` and `ESC P` as `Token::Alt('[' / 'O' / ']' / 'P')`, as the lone-ESC case already does.
- **Regression test:** a core test that turns red today:
  - `Input("\x1bP")` then `Input("dir\r")` completes a cooked read with `"dir\r\n"`;
  - `Input("\x1bO")` then `Input("A")` gives Alt+O then `A`, not Up.

  Extend `a_split_anywhere_decodes_as_the_whole` with an `end=true` case for each of the four.

### F2 (P1): a paste over 4,096 characters into a waiting cooked read loses its beginning
- **Where:** `read.rs:90-121`. `terminal_input` stores every record of the 16 KiB slice first and calls `serve_reads` only at line 120. `Queue::push` drops the oldest record (`input.rs:681-688`).
- **Executed probe:** cmd-style cooked `ReadConsoleW` waiting with a 64 KiB buffer, then one write of 5,000 × `a` plus CR.
  - That makes 5,002 records against the 4,096 cap.
  - The read returns 4,096 units (4,094 `a` + CRLF), and `InputDropped` is logged.
  - 906 characters are lost from the start of the line, and **cmd then runs the rest of the command**.
- **Why it's avoidable:** the waiting read would have taken those records into its own line, which is already reserved under the parked cap (8,192 units). The design caps unread records; it doesn't require dropping records a waiting read can take.
- **conhost:** the input buffer has no record cap (`InputBuffer` storage is a `std::deque`; from memory), so the whole line arrives.
- **Fix:** serve waiting reads before a store would drop a record (or after each piece once the queue is near `MAX_RECORDS`). Keep one 1 MiB reply budget across the whole call: `serve_reads` currently resets `built` on every call.
- **Regression test:** a core `Event` test of the probe above. It should assert all 5,000 `a` plus CRLF are returned and nothing is logged as dropped.

### F3 (P2): the §7.4 row for the ANSI read caches is wrong
- **Where:** `design.md:614` says "2 records". In `read.rs:696-700`, a non-BMP character makes 4 UTF-8 records, so a `count=1` read leaves 3 cached.
- **Executed:** 😀, then ANSI `GetConsoleInput` with count 1, leaves `cached_records.len() == 3`.
- **Fix:** change the row to 3 records. The row's "each read empties them before refilling" is also inaccurate: a count-1 read takes one and leaves two. No refill happens, so the cap holds, but the wording should say that.
- **Test:** a core test asserting `cached_records.len() <= 3` after the probe.

### F4 (P2): a new store, `esc_ends`, is missing from the §7.4 tables
- **Where:** `runtime.rs:104-107` and `170-176`.
- **What it costs:** one `u64` per write that ends in ESC, up to one per queued lane byte. With the reader stalled (input paused past 80 KiB) and a user repeating Escape, the lane holds 65,536 single-ESC writes: 65,536 × 8 B = 512 KiB. Neither the table nor the "3.3 MiB in all" total (`design.md:621`) counts it. §7.4 says "Any store a later PR adds names its cap in one of these tables in the same PR". *Source-traced.*
- **Fix:** add the row and update the total, or bound the entries (e.g. coalesce consecutive entries).
- **Test:** a `Shared` unit test that writes 65,536 × `b"\x1b"` and asserts `esc_ends.len()` is at most the documented bound.

### F5 (P2): K1's regression tests don't exercise `Engine::turn`
- **Where:** `runtime.rs:934-941`. `turns_without_another_wake` calls `engine.work()` and `engine.must_poll()` itself and never calls `turn()`.
- **Result:** moving `let until = self.must_poll()…` back above `self.work()` in `turn()` leaves both new Windows tests green. `the_wait_polls_on_what_the_turn_left_not_on_its_plan` only tests the pure function. So the behaviour K1 fixed has no test that turns red if it regresses.
- **Fix:** drive `turn()` itself. For example, run turns on a thread with only the writers' one wake and assert all 8 reads finish within a deadline. With the regression, `wait` blocks while the `ping` child lives.

## Design questions
1. **Pastes into one-record readers.** PSReadLine and .NET `Console.ReadKey` read one record per `GetConsoleInput`. A single 16 KiB slice (§7.2) can make up to 16,384 records, plus more for wrapped controls, against the 4,096-record cap (§6.1/§7.4). So any paste over about 4,096 characters loses its start by design, which conhost does not do. Suggestion: stop taking slices while the record queue is full, and let the input lane's existing blocking `write` provide backpressure.
2. **Peeks during held echo.** While `echo_held` or `reads_cut` is set, `PeekConsoleInput` returns 0 records even though `GetNumberOfInputEvents` is non-zero (`read.rs:325`). This follows from §7.4's held echo but isn't stated there.

## Optional improvements
- `terminal_input` collects every `Piece` (token plus raw `Vec`) for a slice before storing. A 16 KiB slice of control bytes is 16,384 small allocations, about 1.5 MiB transient. Streaming the pieces would avoid this.
- `intercept` requires `!alt` for Ctrl+Break. Worth confirming against conhost `HandleGenericKeyEvent`.

## Conhost answers per API
These are from memory: the conhost source couldn't be fetched (see validation gaps).

| API | Behaviour under ConPTY | Status here |
|---|---|---|
| ReadConsole, cooked | `COOKED_READ_DATA` ends with `STATUS_ALERTED` on Ctrl+C and Ctrl+Break | Matches |
| ReadConsole raw / RAW_READ | `RAW_READ_DATA::Notify` ignores Ctrl+C and ends the read on Ctrl+Break | Matches |
| GetConsoleInput | `DirectReadData` ignores both Ctrl+C and Ctrl+Break | Matches |
| Ctrl+C / Ctrl+Break handling | `HandleGenericKeyEvent`: Break flushes the input and terminates reads | Matches |
| GenerateConsoleCtrlEvent | Group lookup; the fallback is the recorded divergence V7 | Matches V7 |
| CLOSE_OBJECT / DISCONNECT | Waiting reads end with `STATUS_ALERTED` / `STATUS_THREAD_IS_TERMINATING` | Matches |

- **conhost tests that would give expected values:** `ut_parser/InputEngineTest.cpp` (Alt and ESC cases, for F1) and `ut_host/InputBufferTests.cpp`. Not consulted.
- **Clients:** cmd uses cooked ReadConsoleW with CtrlWakeupMask; PSReadLine and .NET ReadKey use GetConsoleInput.

## Failure classes
- **Containment:** short descriptors are refused before any indexing (`read.rs:302-309`, `360`). Zero capacity gives `STATUS_BUFFER_TOO_SMALL`. The initial-data range is read with `.get()`. No `?` reaches the console core.
- **Retained memory:** a cooked read reserves its whole line plus write-back, at most 64 KiB + 20 per call. Line rests are capped. `esc_ends` is the gap (F4).
- **Amplification:** each call builds at most 1 MiB of replies, and the remaining reads are served on later turns before new input. Echo is checked before every character against 80 KiB. F2 is the paste-into-waiting-read case.
- **Boundaries:**
  - A split after ESC at the engine's slice is handled by `esc_ends` and `InputMore`.
  - Split UTF-8 and split surrogate pairs are handled and tested.
  - A slice ending in `ESC [`, `O`, `]` or `P` where the write ended is not handled (F1).
- **Custody:** the ctrl worker starts first. If the engine thread fails to start, the `CtrlSender` drop stops the worker, and the child doesn't exist yet. The interactive tests now kill their shell if they fail.

## Checks performed
- `cargo test -p rusty_conhost` on Linux: **108 passed, 0 failed**.
- `cargo clippy -p rusty_conhost --all-targets -- -D warnings` on Linux: **clean**.
- `python3 .github/scripts/changes.py check`: **539 fragment(s) ok**.
- Scratch probes (a clone of the head outside the reviewed tree; executed): the F1 and F2 traces above and the F3 cache size. The reviewed tree has no tracked changes.

## Validation gaps
- **Windows tests not run:** the `#[cfg(windows)]` runtime tests (including the two K1 tests), `own_pty.rs`, and `platform-windows` `key_for`. I didn't see CI results for this head.
- **Clippy on Windows:** I only ran it for the Linux target.
- **conhost source not fetched:** the sandbox denied the clone of `github.com/microsoft/terminal`, so the conhost citations above are from memory, not checked at `09beddc6`.
  - F1's parity claim needs a Windows probe: run under ConPTY, write `"\x1bP"` and then `"x"`, and record the records `ReadConsoleInputW` returns.
  - F1 stands without it: the decoder breaks its own stated rule, and the user's keystrokes are lost.
