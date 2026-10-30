<!-- conhost/1246-r1 run 3, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 379 s, 2575587 tokens, $2.35 -->

# Review of PR 1246 (Rusty-Mill/rusty_mill): console input, host PR 3

**Verdict: changes required.** I found two P1 defects and five P2 defects. All four behaviour defects were confirmed by probes I ran. GitHub was blocked here, so every conhost citation below is from memory and needs checking against conhost's source.

- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Reviewed head:** `197fae02607fa8c4428614aef7a4e7ac1b5bca7f`. `git fetch origin pull/1246/head` returned this exact SHA, so the review is pinned.

## Blocking findings

### 1. P1: an ESC cut off by the engine's own 16 KiB slice becomes an Escape key, which erases the cooked line and types the rest of the sequence as text
- **Where:** `src/input.rs:238-241` treats a lone ESC at the end of a feed as the Escape key. Two places cut the input stream at arbitrary bytes: the engine takes at most 16 KiB from the input lane per turn (`src/runtime.rs:236`, `INPUT_SLICE`), and `Shared::write` splits a write wherever the lane runs out of room (`src/runtime.rs:153`).
- **Why it matters:** the code's justification is that "keys come whole". That holds for the terminal's writes, not for the host's own slices. rusty_term doesn't speak win32-input-mode (rusty_mux even filters `?9001` out), so every arrow or function key arrives as plain `ESC [ …`.
- **Trigger:** the lane holds more than 16 KiB, which happens while output is past 80 KiB or after a burst of input, and a slice boundary lands right after an ESC byte.
- **Trace (executed, scratch probe `probe_esc_split_at_slice_boundary`):** a cooked read is waiting and "abc" has been typed.
  - `Input("\x1b[A\r")` in one feed returns `"abc\r\n"`, echo `abc\r\n`.
  - Split as `Input("\x1b")` then `Input("[A\r")`, it returns `"[A\r\n"` and the echo is `abc\b \b\b \b\b \b[A\r\n`. The user's line is erased and cmd runs `[A`.
  - In VIRTUAL_TERMINAL_INPUT mode the raw bytes happen to rejoin, so only key-record clients (cmd, PSReadLine) are hurt.
- **Fix:** don't decide a trailing ESC while more input is known to be coming. For example, `take_input` could report whether the lane still holds bytes (`Event::Input { bytes, more }`), and the decoder would hold ESC when `more` is set. `Shared::write` should also not leave a partial write ending in ESC undecided.
- **Regression tests:**
  - core: `Input` of `"\x1b"` with `more` set, then `"[A\r"`; expect the line `"abc\r\n"`;
  - runtime: a lane of 16 KiB − 1 filler bytes followed by `\x1b[A`; expect an Up key, not Escape.

### 2. P1: focus reports the host asked for leak raw into VT-input clients
- **Where:** `src/console/read.rs:100-104` passes every piece through raw when VIRTUAL_TERMINAL_INPUT is on, including `Token::Focus`. The host itself turns focus reports on in `PREAMBLE` (`src/console.rs`, `\x1b[?1004h`).
- **Trace (executed, probe `probe_focus_reaches_vt_client`):** the client's mode is PROCESSED_INPUT plus VIRTUAL_TERMINAL_INPUT, and the client never asked for focus events. Terminal input `\x1b[O\x1b[I` makes ReadConsoleW return `"\x1b[O\x1b[I"`.
- **conhost (from memory, needs checking):** the input engine turns `CSI I`/`CSI O` into focus state. A VT client sees them only through `TerminalInput::HandleFocus`, which returns nothing unless the client enabled mode 1004 itself (`src/terminal/input/terminalInput.cpp`). §5.5 says the preamble is "as inbox conhost does", and §5.10 records no difference.
- **Client that breaks:** `wsl.exe`, which runs in VT input mode. bash/readline gets `^[[O`/`^[[I` junk on every focus change, and vim in insert mode gets ESC followed by `[I`. The manual gate (§8.1) runs rt-caps.sh inside WSL.
- **Fix:** in VT mode, forward focus reports only if the client enabled `?1004h` (that state arrives with the output parser in PR 4). Until then, drop them for VT clients.
- **Regression test:** a core test in which a VT-mode read after `\x1b[I` stays empty and still waits.

### 3. P2: Ctrl events go to the oldest client first
- **Where:** `src/console/read.rs:166` sorts recipients by ascending cookie, which is connect order.
- **Trace (executed, probe `probe_ctrl_order`):** clients connect as 10, 11, 12, then Ctrl+C gives the order `[10, 11, 12]`. The PR's own test `ctrl_c_goes_to_every_client_and_ends_cooked_reads` locks this order in.
- **conhost (from memory, needs checking):** `ConsoleProcessList::GetTerminationRecordsByGroupId` in `src/server/ProcessList.cpp` walks `_processes` in reverse, so the most recently attached process (the foreground child, e.g. `ping`) is signalled before the shell. On the serial ctrl worker this order is what clients observe.
- **Fix:** sort by descending cookie, and change the test to expect `[11, 10]`.

### 4. P2: cooked echo writes control characters raw, and Tab breaks Backspace
- **Where:** `src/cooked.rs:80` echoes every buffered unit with `push_units`, and `backspace` (`src/cooked.rs:97`) always erases exactly one cell.
- **Trace (executed, probe `probe_control_echo`):** typing `\x1a\x0e\x07\r` echoes the bytes `1a 0e 07 0d 0a`. SUB cancels or prints a substitute glyph in the terminal, SO shifts it into the G1 charset, and BEL rings.
- **Tab:** an echoed Tab moves the cursor to the next tab stop, but Backspace erases only one cell, so the screen no longer matches the line.
- **conhost:** cooked reads show control characters as `^X`. The familiar case is `copy con file` followed by Ctrl+Z, which shows `^Z`. Tabs are expanded to spaces and erased by their full width (`readDataCooked.cpp`, from memory). §5.10 doesn't record this difference.
- **Fix:** render units below 0x20 as `^@`+ch, expand Tab, and make backspace erase the width that was displayed.
- **Regression test:** a cooked-read core test expecting `vt == "^Z^N^G\r\n"`.

### 5. P2: §7.2 and §7.4 tests owed by PR 3 are missing
- **§7.4 stalled reader:** no test covers "cooked echo under sustained terminal input" with an output high-water mark of at most 144 KiB.
- **§7.4 stalled worker:** "repeated Ctrl with a stalled worker" (10,000 GenerateConsoleCtrlEvent calls, the engine keeps serving packets, teardown completes) has only a queue-unit test (`a_full_ctrl_queue_drops_new_events`).
- **§7.2 core:** there is no "ChildExited racing a pending cooked read" test.
- **§7.2 Windows:** there is no PeekConsoleInput/ReadConsoleInput probe checking that the input event is signalled exactly when records are queued.
- **Layers:** these are core tests, plus one Windows probe in `examples/conhost_probe.rs`.

### 6. P2: no interactive fixture
§8.2 says interactive fixtures are added with the PR that makes them work, re-recorded with the host's own logger. This PR makes interactive cmd work but adds only `synthetic-input.txt`. A recorded cmd session (`tests/fixtures/`) is missing.

## Design questions
- **Fixture recording keeps typed input.** `RUSTY_CONHOST_RECORD` now writes terminal input and ReadConsole replies, including typed passwords (`src/fixture.rs` doc comment). §6.1 and §8.2 promise that the sanitiser scrubs "user history". The owner should decide whether input must be redacted rather than just warned about.
- **The line-rest cap is global.** The 512 Ki-unit cap (§7.4) is shared by all handles, so one client that parks many small-buffer cooked reads can fill it, and another client's line rest is then dropped. The design chose this; it may hurt a multi-client session.

## Optional improvements
- **A read can take later input before a line rest that is waiting on its handle.** When two reads wait on the same handle and the first finishes with a rest, the second (`attempt`/`read_chars`) doesn't look at `pending` and takes later input first. A second cooked read on the same handle overwrites the first one's rest (`keep_pending`). I couldn't settle conhost's behaviour offline.
- **The input-available event ignores the ANSI caches.** `sync_input_available` and GetNumberOfInputEvents look only at the record queue, not `cached_records`/`cached_text`, so a client that waits on the handle after an ANSI split can block. Whether conhost does the same is unconfirmed.

## Checks performed
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost --offline` (Linux) | 93 passed, 0 failed; `own_pty` has 0 tests because it is Windows-only |
| `cargo clippy -p rusty_conhost --all-targets --offline -- -D warnings` (Linux) | clean |
| `python3 .github/scripts/changes.py check` | 539 fragments ok |
| Scratch probes (git-archive copy of the head, separate target dir) | 4 run, results quoted in findings 1–4 |
| New tests run at the base | not runnable: they use `Event::Input(&[u8])` and other APIs this PR adds |

## Validation gaps
- **Windows tests and clippy:** I couldn't run Windows clippy (no Windows target installed) or the Windows tests (`own_pty.rs`: interactive cmd, Ctrl+C under ping). I also didn't see the §8.3 D4 `workflow_dispatch` run for this exact head.
- **conhost source:** github.com was blocked by the sandbox, so all conhost citations need checking at 09beddc6, especially `ProcessList.cpp` (Ctrl order) and `terminalInput.cpp` (`HandleFocus`).
- **`ExeNameLength` unit:** `read_args` treats `ExeNameLength` as UTF-16 units. If kernelbase sends bytes, the initial data for cmd's Tab completion is read from the wrong offset. The probe that settles it is cmd Tab completion under our host, logging the ReadConsole packet.
