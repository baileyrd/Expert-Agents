<!-- conhost/1246-r1 run 1, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 589 s, 5172044 tokens, $3.50 -->

I found blocking defects, so the verdict is **changes required**. One caveat first: github.com was blocked, so I couldn't clone microsoft/terminal. Every conhost reference below is from memory of the source, without line numbers, and unchecked against the pinned commit `09beddc6`. The findings rely only on this crate's code, the design doc, and probes I ran, not on those conhost references.

**Repository** Rusty-Mill/rusty_mill · **PR** 1246 · **Base** `66f4bc38c8181fd669c58f8e34df385a6afa895e` · **Head** `197fae02607fa8c4428614aef7a4e7ac1b5bca7f`. I confirmed that the fetched `pull/1246/head` is that commit, and my build tree matches it.

## Step 1: APIs the diff touches (conhost answers unverified)

| API / path | conhost under ConPTY (recalled) | conhost test | Client that depends on it |
|---|---|---|---|
| GetConsoleInput, also Peek (`0x01000004`) | `DirectReadData::Notify` ignores Ctrl+C and Ctrl+Break | `API_InputTests.cpp` | PSReadLine, .NET `Console.ReadKey` |
| ReadConsole cooked (`0x01000005`) | `COOKED_READ_DATA`: STATUS_ALERTED on Ctrl+C or Break; the leftover goes to `INPUT_READ_HANDLE_DATA` | `ut_host` | cmd, `set /p` |
| ReadConsole raw, RAW_READ | `RAW_READ_DATA::Notify`: Ctrl+C keeps waiting, Ctrl+Break ends the read | none found | ReadFile on stdin |
| GetNumberOfInputEvents, Flush (`0x01000003`, `0x02000003`), RAW_FLUSH | `GetInputBuffer` checks the handle and its access | `InputBufferTests` | .NET `KeyAvailable` |
| GenerateConsoleCtrlEvent (`0x02000001`) | `ServerGenerateConsoleCtrlEvent` has a parent-process fallback (recorded as V7) | none found | cmd, `taskkill`-like tools |
| Terminal input: VT, win32-input-mode, focus | `InputStateMachineEngine`; `InputBuffer::WriteFocusEvent` goes through `TerminalInput::HandleFocus` | `ut_parser` InputEngineTest | edit.exe, ssh.exe |

## Blocking findings

**1. P1: cooked-read echo breaks the 144 KiB output cap (executed).** `src/console/read.rs:462-464` and `src/cooked.rs:87-110`
- **Trigger:** a client's ReadConsoleW passes initial data, then the user presses Esc.
- **What happens:** `attempt` pushes all of the echo with no budget check. Esc echoes `\b \b` (3 bytes) for every unit in the line.
- **Single read:** 32,000 initial units plus one Esc byte gave 96,000 B of echo in one `handle` call. The engine still takes input at 80 KiB, so the queue reaches 81,920 + 96,000 = 177,920 B against the 147,456 B cap.
- **Many reads:** admission let in 16 such reads (`parked` = 1,024,320). One 528-byte slice of Esc+Enter records then produced 1,536,032 B of echo.
- **Typing alone:** a full 8,192-unit line plus Esc gives 24,576 B.
- **Effect:** this breaks the §7.4 promises that "past 80 KiB … echo stops" and that the queue "never exceeds 144 KiB". The §7.4 stalled-reader test for cooked echo is also missing.
- **Fix:** bound the echo per call. Either erase the line with a fixed-size sequence (cursor moves plus `CSI K`), or stop feeding records into a line while `out.len()` is past the reserve and retry reads on `OutputDrained`.
- **Regression test:** a core test with the queue held at 80 KiB, one 32,000-unit cooked read and `Input(b"\x1b")`, asserting `high_water ≤ 144 KiB`. Add the 16-read variant too.

**2. P1: a key split at the engine's 16 KiB input slice becomes an Escape key plus typed text (executed).** `src/input.rs:238-242`, `src/runtime.rs:510-512`
- **Cause:** the decoder treats a lone ESC at the end of its input as the Escape key, because "keys come whole". But the engine cuts input into 16 KiB slices even when more bytes are queued.
- **Probe:** 5,500 × `\x1b[A` (a held Up arrow, queued while output was paused above 80 KiB) cut into 16 KiB slices. 16,384 mod 3 = 1, so the first slice ends on an ESC. Result: one VK_ESCAPE key-down and the text records `[A`.
- **Effect:** in a cooked read (cmd) Esc wipes the line and `[A` is typed in. In PSReadLine, Esc reverts the line.
- **Fix:** have `Event::Input` say whether more input is queued (`plan.input` was less than what the lane holds), and keep a trailing lone ESC pending in that case.
- **Regression test:** the probe above, asserting no Escape and no `[` records. Add a decoder unit test.

**3. P2: an ANSI raw read turns non-BMP characters into U+FFFD (executed).** `src/console/read.rs:511-516` and `581-588`
- **Cause:** `read_chars` converts one UTF-16 unit at a time, so a surrogate pair is split into two invalid halves.
- **Probe:** typing 😀 gave `EF BF BD EF BF BD` from both ReadConsoleA (raw) and RAW_READ. The cooked path returns the correct `F0 9F 98 80`. GetConsoleInputA records have the same problem.
- **Effect:** the user's text is corrupted, which contradicts §5.9 ("the A APIs treat their bytes as UTF-8").
- **Fix:** take the low surrogate together with the high one before converting.
- **Regression test:** assert the output is `F0 9F 98 80`.

**4. P2: the design doc disagrees with the code on Ctrl+C.** §5.5 says of Ctrl+C under PROCESSED_INPUT: "Nothing is queued". The code queues the key-up record, `ctrl_c_goes_to_every_client_and_ends_cooked_reads` asserts that, and the input-available event goes true (see the fixture `synthetic-input.txt`). Fix whichever one is wrong.

**5. P2: tests the design requires for PR 3 are missing.**
- the §7.4 stalled-reader test for cooked echo;
- the §7.2 Windows tests: the input event tracking the queue during a Peek/Read loop, and `hang_up` while a ReadConsole is pending;
- the §7.4 "repeated Ctrl with a stalled worker" test;
- §8.1 parity probes for the new APIs (`examples/conhost_probe.rs` has no input subcommands).

## Design question

- **Focus reports go to every VT-input client.** At startup the host turns on focus reports (`?1004h`), and rusty_term honours that mode. Under VIRTUAL_TERMINAL_INPUT, `\x1b[I` and `\x1b[O` then reach every client raw, including clients that never asked for focus reports (executed). My recollection is that conhost only sends them when the client itself enabled `?1004` (`TerminalInput::HandleFocus`). A client that forwards its input raw, such as ssh.exe, would pass them on to the remote session. §5.5's "VT passed through raw" arguably decides this, so it needs a design call rather than a code fix.

## Optional improvements

- Appendix A still says the two input-API numbers are "pinned in PR 3"; write in `0x01000003` and `0x02000003`.
- `read_console` counts initial data in `retained` for non-line reads that drop it (an overcount, harmless).
- `vt_for` sends Backspace as `0x08`; I recall conhost sending `0x7f`. This only matters with a terminal that speaks win32-input-mode, which rusty_term doesn't.

## Checks

The pinned 1.98.1 toolchain couldn't be installed (`~/.rustup` is read-only), and crates.io was blocked. So I ran the checks with the installed stable 1.98.1 toolchain, in a minimal offline workspace under `$TMPDIR`. It holds `rusty_conhost`, `platform` and `rusty_wire` from `git archive` of each commit, with platform-windows stubbed out.

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` at head | 93 passed, 0 failed |
| Linux clippy, `--all-targets -D warnings` | clean |
| `python3 .github/scripts/changes.py check` | 539 fragments ok |
| Head fixtures replayed on base code | `synthetic-apis.txt` and `synthetic-input.txt` both fail, so they guard the change |
| New unit tests at base | they don't compile there (red by construction) |
| My probes (findings 1–3, focus, two waiting reads) | executed at head |

Another process was writing into the shared `scratch/` directory during the review. It added its own probe tests and replaced my lock file there. I discarded those results and redid everything in an isolated `$TMPDIR` copy.

## Validation gaps

- **conhost source and history were unreachable.** None of the parity answers above is cited or checked.
- **Two waiting cooked reads on one handle lose a line's rest (executed).** With 2-unit buffers, "abcd⏎" then "wxyz⏎" returned "ab", then "wx", then "yz\r\n". "cd\r\n" was overwritten in `keep_pending` (`read.rs:649-652`). Whether conhost behaves the same is unknown: check `SavePendingInput`, or run a Windows probe with two threads blocked in ReadConsoleW.
- **I couldn't run anything on Windows:** the `own_pty.rs` tests (interactive cmd, Ctrl+C), the runtime ctrl-worker tests, and Windows clippy.
- **D4:** I couldn't see the PR body, so I couldn't confirm the required successful Windows CI run at the head commit.
- **Unchecked statuses:** the STATUS_ALERTED answer when a handle closes, and how ANSI GetConsoleInput handles surrogates in conhost.

## Verdict: **changes required**
