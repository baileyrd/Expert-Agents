<!-- conhost/1246-r1 run 2, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 655 s, 4355842 tokens, $3.56 -->

# Review of PR 1246, rusty_conhost console input (host PR 3)

**Verdict: changes required.** I found seven P1 defects and five P2 defects. Eight of them are confirmed by probes I ran; the rest are traced through the source.

- **Repository:** Rusty-Mill/rusty_mill, PR 1246
- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Reviewed head:** `197fae02607fa8c4428614aef7a4e7ac1b5bca7f`. The fetched `pull/1246/head` equals this SHA, and so does the checked-out HEAD.

**On the conhost citations:** I could not reach github.com from the sandbox (the proxy refused it), so I couldn't clone microsoft/terminal at `09beddc6`. Every conhost reference below comes from memory and names the file and function to check. That's listed again under validation gaps.

**On the probes:** "Executed" means a probe test run in a scratch copy at `scratch/head`, never in the reviewed tree.

## Blocking findings

### 1. P1: Cooked-read echo has no budget and can exceed the 144 KiB output cap without limit
- **Where:** `src/console/read.rs:462-464` pushes `echo` into the output queue unconditionally. `src/cooked.rs:87-94`: Escape erases the whole buffer, including the initial data, at 3 bytes per character. `read.rs:379-392`: a ReadConsole packet never checks the output budget.
- **Trigger:** a client issues ReadConsoleW with 32,000 units of initial data (64,000 bytes, which is within the 64 KiB packet limit). The user then presses Escape once. *Executed:* the queue starts at 80 KiB, the last level at which input is still taken. One key adds 96,000 bytes of echo and the queue peaks at **177,920 B, above the 147,456 B cap**.
- **Worse case:** 200 Escape+Enter presses are queued while nobody reads, then a client makes 200 such ReadConsoleW calls, each of which completes at once. *Executed:* the output queue reaches **19.2 MB** with a stalled reader, and it grows with every further key press.
- **Why it matters:** §7.4 says "The output queue therefore never exceeds … 144 KiB" and gives cooked echo only the 16 KiB host reserve.
- **Fix:** count echo where it is made. Erase a line in bounded bytes, for example with CR and positioning plus EL, rather than one `\b \b` per unit. A read whose echo would pass the reserve should be parked until the output drains.
- **Missing test:** §7.4's own stalled-reader case, "cooked echo under sustained terminal input", isn't in the PR. Add it with initial data plus Escape and with repeated reads, asserting a high-water mark of at most 144 KiB.

### 2. P1: Cooked echo sends C0 control characters to the terminal raw
- **Where:** `src/cooked.rs:78-80` echoes any character as typed; `read.rs:462` sends it.
- **Trace (executed):** in a cooked read, the user presses Ctrl+[ (the record `CSI 219;26;27;1;8;1 _`, which carries an ESC character), types `[2J`, then presses Ctrl+N.
  - The host sends `\x1b[2J\x0e`: the terminal **clears the user's screen and shifts to the G1 character set**.
  - conhost's cooked read shows control characters as `^X` (`readDataCooked.cpp`, recalled), so typing Ctrl+A in cmd shows `^A`.
- **Who hits it:** any cmd, `set /p` or `input()` user who types Ctrl+[ (a common Escape key for vim users) or other Ctrl+letter keys.
- **Fix:** echo a character below 0x20 (other than Tab, which needs its own handling) as `^` plus `ch + 0x40`, and make Backspace erase both cells.
- **Test:** a core test asserting the VT produced for typing Ctrl+[ and Ctrl+N during a cooked read.

### 3. P1: Backspace echo is wrong across a wrapped line and over wide characters
- **Where:** `src/cooked.rs:96-110` always emits `\b \b` for one character.
- **Trace (executed against rusty_term_core's own parser):**
  - **Wrapped line:** on a 10-column screen, typing 11 characters and then Backspace twice leaves `j` on the screen while the line holds `abcdefghi`. A Backspace at column 0 doesn't reverse-wrap.
  - **Wide character:** typing `a`, `あ`, Backspace, `z` puts `z` at column 3 instead of column 1.
- **Why it matters:** conhost's cooked read lays the line out by column width and redraws it. Interactive cmd, the stated goal of PR 3 in §10, hits this on any long command and with any CJK input.
- **Fix:** keep the column width of each echoed character and erase by moving back that many cells, using cursor movement that can cross rows.
- **Test:** core tests that feed the host's echo into a `Grid` and compare the screen with the line.

### 4. P1: An ESC that ends a 16 KiB input slice is read as the Escape key
- **Where:** `src/input.rs:238-242` treats an ESC at the end of a feed as a complete key. `src/runtime.rs:510-511` and `src/sched.rs:83` cut the input lane at fixed 16 KiB boundaries that ignore sequences.
- **Trace (executed):** a cooked read has `ab` typed. One slice ends with `…\x1b`, and the next slice starts with `[67;46;99;1;0;1_\x1b[13;28;13;1;0;1_`.
  - The read returns `"[67;46;99;1;0;1_\r\n"`: the line was erased and the rest of the record was inserted as text.
  - In win32-input-mode about 1 byte in 18 is an ESC, so a 100 KiB paste crosses about 6 boundaries and has roughly a 30% chance of this happening.
- **Why it matters:** this breaks stream fidelity. conhost's chunks are the terminal's own pipe writes; here the host splits the stream itself.
- **Fix:** apply the lone-ESC rule only when the input lane was empty after the slice was taken, or end each slice on a sequence boundary.
- **Test:** the probe trace above, as a core test.

### 5. P1: Win32 records are turned into the wrong VT for clients using VIRTUAL_TERMINAL_INPUT
- **Where:** `src/input.rs:531-589` (`vt_for`).
- **Executed:** a Backspace record (`ch` 8) goes out as `\x08`, and Shift+Tab (`ch` 9 with Shift) goes out as `\t`.
- **What conhost does** (`src/terminal/input/terminalInput.cpp`, keyboard map, recalled):
  - Backspace sends `\x7f` and Ctrl+Backspace sends `\x08` (DECBKM reset).
  - Shift+Tab sends `CSI Z`.
  - With DECCKM set by the client, arrow keys are `ESC O A`.
- **What the PR does:** it tracks no DECCKM state (grep finds none), so it always sends `CSI A`.
- **Who hits it:** WSL, which is in the manual gate via rt-caps.sh. Backspace doesn't erase in Linux canonical-mode reads such as sudo password prompts (VERASE is `^?`). Back-tab is lost. ncurses applications that turn on keypad mode (htop, for example) don't see arrow keys, because their terminfo `kcuu1` is `\EOA`.
- **Fix:** follow TerminalInput's map. Track DECCKM and DECBKM from the client's output.
- **Tests:** `vt_for` cases for Backspace, Ctrl+Backspace, Shift+Tab, and arrows under DECCKM.

### 6. P1: Focus reports the host asked for reach VT-input clients that never enabled them
- **Where:** `src/console.rs:35` sends `?1004h` in the preamble; `src/console/read.rs:100-104` passes `CSI I` and `CSI O` through raw.
- **Executed:** a VT-input client reads `"\x1b[I"` after a focus change.
- **What conhost does:** `InputBuffer::WriteFocusEvent` calls `TerminalInput::HandleFocus`, which produces output only if the *client* enabled `?1004` (recalled).
- **Who hits it:** a WSL bash or readline session gets `ESC [ I` inserted every time the window gains or loses focus.
- **Fix:** forward focus reports to a VT-input client only after that client has written `?1004h`; otherwise drop them.
- **Test:** a core test with focus input in VT mode, with and without `?1004h`.

### 7. P1: Two waiting cooked reads on one handle lose the first read's leftover text
- **Where:** `src/console/read.rs:649-652` (`keep_pending` overwrites); `read.rs:409-422` and `read.rs:497-502` (a waiting read never looks at the handle's leftover text).
- **Trace (executed):** reads A (4-byte buffer) and B are both waiting on one handle, and the user types `abcdef\rgh\r`.
  - A returns `ab`, and the rest, `cdef\r\n`, is kept on the handle.
  - B then reads from the input queue, returns `gh\r\n`, and stores an empty rest. That **overwrites `cdef\r\n`, which is lost**.
  - A third read waits even though the earlier text should have been served first.
- **Second problem:** without the second line, B waits for unrelated new input even though text is already available on its handle.
- **Fix:** let a waiting read on a handle with leftover text be served from it first, and add to the leftover text rather than replacing it.
- **Test:** the probe trace above.

### 8. P2: Ctrl events are sent oldest client first
- **Where:** `src/console/read.rs:164-169` sorts by ascending cookie. The test at `read.rs:925` asserts the order `[(10, C), (11, C)]`, which I also saw when I ran the probe.
- **What conhost does:** `ConsoleProcessList::GetTerminationRecordsByGroupId` (`ProcessList.cpp`) builds the list newest first (recalled). Earlier reviews of this crate also set the rule as newest client first.
- **Who notices:** cmd receives Ctrl+C before its running child does.
- **Fix:** reverse the order and update the test.

### 9. P2: In plain-VT input, Alt+[ and Alt+O wait for the next key and merge with it
- **Where:** `src/input.rs:245-248` and `input.rs:284-293` return `Incomplete` for a bare `\x1b[` or `\x1bO`. *Source-traced.*
- **Trace:** a mux client on a terminal without win32-input-mode sends Alt+[ and nothing follows, so the key isn't delivered. When the user next presses `A`, it becomes Up.
- **What conhost does:** `StateMachine::ProcessString`, for an input engine, flushes the CsiEntry and Ss3Entry states at the end of the string as Alt+[ and Alt+O (recalled; the source comment there uses the "alt+[" example).
- **Fix:** do the same, together with finding 4's rule that the lane must be empty.

### 10. P2: The host never turns off what its preamble turned on
- **Where:** `src/runtime.rs:474` sends `CSI ?9001h CSI ?1004h`. `src/console.rs:546-557` tears down with `CloseServer` and `Eof` and emits no `CSI ?9001l CSI ?1004l`. *Source-traced.*
- **Effect:** the terminal or mux pane stays in win32-input-mode and focus reporting after the console ends.
- **Fix:** emit the reset before `Eof`, and add a test for it.

### 11. P2: Text from plain-VT input becomes key-downs with VK 0 and no key-up, and §5.10 doesn't record it
- **Where:** `src/input.rs:508`.
- **What conhost does:** `InteractDispatch::WriteString` uses `CharToKeyEvents`: the VK comes from `VkKeyScanW`, the key goes down and up, and Shift is wrapped around it.
- **Divergence record:** V8 records only "scan code 0".
- **Fix:** synthesise the records through `key_for` as `control()` already does, or widen V8 to describe this.

### 12. P2: Tests the design requires for the changed behaviour are missing
- **§7.4 stalled reader:** "cooked echo under sustained terminal input". Its absence is how finding 1 got through.
- **§7.2, Windows:** the integration test where a client loops PeekConsoleInput and ReadConsoleInput while the input-available event is checked.
- **§7.2, core:** "ChildExited racing a pending cooked read".
- **§7.4:** "repeated Ctrl with a stalled worker" (10,000 GenerateConsoleCtrlEvent calls plus Ctrl+C). The PR moves the ctrl worker into PR 3, but only the queue cap is tested.

## Design questions
- §5.5 says "text, keys, VT | VIRTUAL_TERMINAL_INPUT on | passed through raw". With win32-input-mode on, no key arrives raw: the host has to re-encode it, and conhost re-encodes through TerminalInput, which follows the client's modes (focus, DECCKM, DECBKM). Findings 5 and 6 come from this gap. I'd suggest the design adopt conhost's encoding for VT-input clients.

## Optional improvements
- `cooked.rs:77`: a surrogate pair typed right at the line cap is half kept.
- V7's wording ("the caller's parent process") doesn't quite match the code comment at `read.rs:252-254` ("a child of a client, found by its parent pid").

## Checks
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux, head) | **passed**: 93 unit tests; `own_pty.rs` has 0 tests off Windows |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` | **passed** on Linux only |
| `cargo clippy -p platform-windows --all-targets -- -D warnings` | **passed** on Linux only; the `cfg(windows)` code was not compiled |
| `python3 .github/scripts/changes.py check` | **passed**: 539 fragments ok |
| Probes in scratch (`console/read.rs` tests, plus a rusty_term_core test) | ran; results as quoted in findings 1–8 |
| New tests at base | they use APIs this PR adds (`Event::Input(&[u8])`, `Console::new(size, key_for)`), so I expect them not to compile at base; I didn't build base to see it |

## Validation gaps
- **conhost source not consulted:** the sandbox's network proxy refused github.com. Behaviour to confirm at `09beddc6`:
  - `ProcessList.cpp`: order in `GetTerminationRecordsByGroupId`.
  - `terminalInput.cpp`: Backspace, Shift+Tab, DECCKM and `HandleFocus`.
  - `StateMachine.cpp`: the end-of-string flush for input.
  - `readDataCooked.cpp`: `^X` echo, and whether the newline is echoed without ECHO_INPUT.
  - `VtIo.cpp`: whether ConPTY resets the preamble on exit.
- **Possible P1, unconfirmed:** whether `RAW_READ_DATA::Notify` (`readDataRaw.cpp`) ends a raw ReadConsole on Ctrl+C. This PR ends raw reads only on Ctrl+Break. If conhost ends them on both, `pause` in cmd keeps waiting after Ctrl+C. Windows probe: run `cmd /c pause` under both hosts, send `\x03`, and compare when the read returns.
- **Windows code not built or run:** `runtime.rs` (ctrl worker), `condrv::key_for`, and the `own_pty.rs` tests `interactive_cmd_runs_typed_lines` and `ctrl_c_stops_the_running_command_not_the_shell`. No Windows target is installed here.
- **D4 Windows CI run:** not seen. I couldn't read the PR body from the sandbox.
