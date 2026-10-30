# rusty_conhost PR 3 (console input): review

- Repository: rusty_mill (worktree `/home/claude/trial-conhost`, detached). PR number: not given (blind trial).
- Base `66f4bc38` (an ancestor of the head, checked). Reviewed head: `197fae02607fa8c4428614aef7a4e7ac1b5bca7f` (`git rev-parse HEAD`, checked).
- Diff: `git diff 66f4bc38...197fae02`, 18 files, +3025/-103. Oracle: microsoft/terminal at `09beddc6` (the commit pinned in THIRD_PARTY_NOTICES.md), cloned blobless into scratch space.
- Probes: a `git archive` copy of the head at `scratchpad/probe-conhost`, with probe tests added to the end of `src/console/read.rs` there. Traces labelled *executed* come from those runs.

## Step 1: what the diff touches, and conhost's answer under ConPTY

| API / path | conhost under ConPTY (cited) | conhost test | Client that depends on it |
|---|---|---|---|
| Terminal input decode (plain VT, win32-input-mode) | `InputStateMachineEngine.cpp:152-230` (`_DoControlCharacter`), `:377-520` (`ActionCsiDispatch`, win32 → `WriteCtrlKey`); end-of-input handling `stateMachine.cpp:2030-2075` | `ut_parser/InputEngineTest.cpp` | every client; WT sends win32-input-mode |
| Win32 key → VT for a VIRTUAL_TERMINAL_INPUT client | `inputBuffer.cpp:676-715` (`_WriteBuffer` → `_termInput.HandleKey`), `terminalInput.cpp:225-415` (surrogate join at 244-255), `:911-921` (VK_BACK → DEL) | `ut_adapter/inputTest.cpp` | edit.exe (`crates/edit/src/sys/windows.rs:233`, `input.rs:335-349`), ssh.exe, wsl.exe |
| Focus reports | `inputBuffer.cpp:584-599`, `terminalInput.cpp:420-428` (VT clients get them only after their own `?1004h`) | `InputBufferTests` | ssh/wsl (GH#12799, GH#12900, cited at `InputStateMachineEngine.cpp:386-387`) |
| Ctrl+C / Ctrl+Break / Ctrl+Esc | `input.cpp:108-175` (`HandleGenericKeyEvent`); reads end per `readDataCooked.cpp:216-238`, `readDataRaw.cpp:86-112`, `readDataDirect.cpp:73-95` | `ft_host/API_InputTests` | cmd, `ping -t` |
| Ctrl+S pause | `inputBuffer.cpp:689-698` (`IsPauseKey` under LINE_INPUT sets CONSOLE_SUSPENDED and drops the key) | none found | cmd |
| GetConsoleInput (read/peek/wait) | `ApiDispatchers.cpp:136-205`, `readDataDirect.cpp:55-120` (a waiting peek removes) | `API_InputTests` | PSReadLine, .NET `Console.ReadKey` |
| ReadConsole / RAW_READ | `ApiDispatchers.cpp:212-330`, `IoSorter.cpp:69-85`, `stream.cpp:394-527`, `WaitBlock.cpp:222-225` (^Z) | `API_InputTests`, `ut_host/ReadWaitTests` | cmd (cooked, CtrlWakeupMask=Tab), python `input()` |
| Cooked line and echo | `readDataCooked.cpp:469-533` (`_handleChar`), `:734-820` (`_handlePostCharInputLoop`), `:1349-1380` (controls shown as `^X`, Tab as spaces) | `ut_host/CommandLineTests`, `ReadDataCookedTests` | cmd, `copy con`, `set /p` |
| Status mapping | `ApiSorter.cpp:183-197` (keeps STATUS_BUFFER_TOO_SMALL), `IoSorter.cpp:75` (`NTSTATUS_FROM_HRESULT`, which keeps nothing) | none found | none found |
| GenerateConsoleCtrlEvent | `ApiDispatchersInternal.cpp:66-98`; recipients in `ProcessList.cpp:204-235`, newest first (#18233, fixing #15373) | `ft_host` CloseTest (per #18233) | npm/node, Python `subprocess` |
| GetNumberOfInputEvents, Flush, RAW_FLUSH | `ApiDispatchers.cpp:116-134`, `IoSorter.cpp:87-94`, `ObjectHandle.cpp:142-153` | `API_InputTests` | .NET `Console.KeyAvailable` |
| Input queue size | `InputEventQueue` (std::deque), uncapped; the design's 4,096 is the oracle | none | none |

## Blocking findings

### F1 (P1): VT-input clients get win32 keys through a partial encoder: Backspace sends BS, Shift+Tab sends Tab, and non-BMP characters arrive as two U+FFFD
- **File:** `crates/libs/ui/rusty_conhost/src/input.rs:532-553` (`vt_for`), called from `src/console/read.rs:118-129`.
- **Trace (executed, probe `probe_vt_backspace_shift_tab_surrogates`):** the input mode is PROCESSED|VIRTUAL_TERMINAL_INPUT, the terminal is in win32-input-mode, and Windows Terminal sends:
  - Backspace `CSI 8;14;8;1;0;1_`, which stores `[0x08]`. conhost stores `0x7f`: `terminalInput.cpp:911-921` gives `ctrl == back ? "\x7f" : "\b"`, and BackarrowKey is off by default (`terminalInput.hpp:182`).
  - Shift+Tab `CSI 9;15;9;1;16;1_`, which stores `[0x09]`. conhost sends `CSI Z` (`terminalInput.cpp:922-935`).
  - U+1F600 as two VK_PACKET records (D83D, DE00), which store `[0xFFFD, 0xFFFD]`. conhost joins the surrogates (`terminalInput.cpp:244-255`).
- **Client that breaks:** edit.exe enables VT input (`microsoft/edit crates/edit/src/sys/windows.rs:233`) and parses `0x08` as Ctrl+H (`input.rs:343-346`), which its TUI binds to delete-word (`tui.rs:2755-2757`). Under this host every Backspace in edit.exe deletes a whole word, Shift+Tab doesn't unindent, and emoji become `��`.
- **Recorded?** No: this is a difference from conhost that §5.10 doesn't list, and the doc comment claims a "subset of `TerminalInput::HandleKey`".
- **Required correction:** port the parts of `HandleKey`/`_encodeRegular` that a ConPTY client sees: VK_BACK → DEL (BS with Ctrl), Alt prefix; Shift+Tab → `CSI Z`; a held leading surrogate that is joined with the next record; Ctrl+Alt on non-graphic characters. Otherwise record each difference in §5.10.
- **Regression test:** a core `Event` test in `read.rs` that feeds the three records above under VIRTUAL_TERMINAL_INPUT and asserts stored `uChar`s `[0x7f]`, `ESC [ Z`, and `[0xD83D, 0xDE00]`. Each assertion is red at the head.

### F2 (P1): a win32-input-mode record split right after its ESC becomes Escape plus literal text, which erases the cooked line and submits garbage
- **File:** `src/input.rs:237-248` (`escape`: a lone ESC at the end of the input is always the Escape key; `ESC [` and `ESC O` at the end always wait).
- **Trace (executed, probe `probe_split_after_esc_in_win32_mode`):**
  1. A cooked ReadConsoleW waits, and "dir" arrives as win32 records.
  2. The next record arrives as `\x1b` | `[69;18;101;1;0;1_` followed by Enter.
  3. The read completes with `"[69;18;101;1;0;1_\r\n"`, and the echo is `dir\b \b\b \b\b \b[69;18;101;1;0;1_\r\n`: the user's typed "dir" is erased and cmd runs the garbage.
- **Trigger:** the engine takes at most 16 KiB per turn (§7.2 step 4). Whenever the lane backs up (output past 80 KiB while the user types, or any write chunking by the terminal), a record boundary can land after an ESC.
- **conhost:** `stateMachine.cpp:2056-2075` treats a trailing `ESC` or `ESC x` as a key only while no win32 sequence has been seen. Once one has (`EncounteredWin32InputModeSequence`), the run is cached and waits for the rest. The same rule makes trailing `ESC [` and `ESC O` read as Alt+[ and Alt+O in plain VT; ours wait instead, so Alt+[ followed by A reads as Up.
- **Required correction:** keep a Decoder flag set by the first `Token::Win32`. While it is set, a trailing lone ESC stays pending. Without it, a trailing `ESC [` or `ESC O` is Alt+key, as conhost does.
- **Regression test:** an `input.rs` unit test: after one win32 record, `feed(b"\x1b")` yields nothing and `feed(b"[69;18;101;1;0;1_")` yields one `Token::Win32`. Without a win32 record, `feed(b"\x1b[")` yields `Token::Alt('[')`. A core test then repeats the probe and asserts the line `"dire\r\n"`.

### F3 (P1): a paste into a waiting read loses all but its last 4,096 characters
- **File:** `src/console/read.rs:84-114` (`terminal_input` stores every piece of the slice before calling `serve_reads` at :113) together with `src/input.rs:608-615` (drop-oldest at 4,096).
- **Trace (executed, probe `probe_paste_into_waiting_cooked_read`):**
  1. cmd is waiting in a cooked ReadConsoleW with a 16,384-byte buffer.
  2. The terminal delivers a 5,001-byte paste ("a"×5000, then CR) in one slice.
  3. 5,001 records are pushed, the queue keeps the last 4,096, and `InputDropped` is logged.
  4. The read returns 4,096 units (4,094 "a" plus CRLF), so 906 characters are silently lost although a reader was waiting.
  - With a multi-line paste, whole leading commands are dropped and the later lines run out of context.
- **conhost:** the queue is unbounded (`inputBuffer.cpp:724`, `_storage.push_back`), so the read gets all 5,000.
- **Recorded?** §6.1 caps the queue for when nobody drains it ("about 1 s of … paste"). It does not decide that one 16 KiB turn may overflow the queue in front of a waiting reader.
- **Required correction:** serve waiting reads before the queue overflows: per piece, and whenever `input.len()` reaches MAX_RECORDS. Or take from the input lane only what the queue can absorb; the lane's 64 KiB blocking `write` then provides backpressure.
- **Regression test:** a core `Event` test: a waiting cooked read, then `Event::Input` with "a"×5000 + CR, asserting 5,002 units back and no `InputDropped`.

### F4 (P1): cooked-read echo is unbounded; one Escape over a client's initial data emits 96,000 B, which breaks the 144 KiB output cap
- **Files:** `src/cooked.rs:42-50` (the limit is the initial data's length, up to 32 Ki units), `:87-94` (Escape echoes `\b \b` per unit), `src/console/read.rs:462-464` (the echo is pushed whole, outside any budget or slicing).
- **Trace (executed, probe `probe_escape_echo_with_initial_data`):**
  - ReadConsoleW with 32,000 initial units (64,000 B of payload, 64,020 B parked: within the per-call cap) needs one typed Esc to echo 96,000 B.
  - §7.2 step 4 admits input while the queue holds ≤ 80 KiB, so the output queue can reach 81,920 + 96,000 = 177,920 B against the 147,456 B (144 KiB) cap. That is also past the 64 KiB emission step.
  - Admission allows 15 such reads (15 × 65,556 ≤ 1 MiB − 64 KiB). An "Esc CR" × 15 input (30 bytes) then emits 15 × 96,000 = 1,440,000 B in one `handle` call.
  - The §7.4 stalled-reader test for "cooked echo under sustained terminal input" is missing, so nothing catches this.
- **Required correction:** bound echo where it is made. Either Escape erases with a fixed-size sequence (for example CR, then reprint, or erase in line, as conhost's `_redisplay` erases by distance), or park the read's echo and slice it within the host reserve like any other emission.
- **Regression test:** the §7.4 stalled-reader test for cooked echo: a held queue at 80 KiB, a read with 32 Ki initial units, Esc, asserting high water ≤ 144 KiB. Add the 15-read variant.

### F5 (P1): Ctrl effects aren't coalesced in the core: one 16 KiB input slice of Ctrl+C makes 4,194,304 effects (320 MiB)
- **File:** `src/console/read.rs:140-169` (`intercept` → `ctrl_event` pushes one `Effect::Ctrl` per client per key). Coalescing and the 256-entry cap exist only later, in `runtime.rs` `CtrlSender::push`.
- **Trace (executed, probe `probe_ctrl_c_flood_effects`):** 256 connected clients (the §7.4 client cap) and one input slice of 16,384 × `0x03` give 4,194,304 `Effect::Ctrl` × 80 B = 335,544,320 B allocated inside one `handle` call before the runtime coalesces anything. With one client it is still 1.3 MB. §7.4 says the Ctrl state is bounded at 256 entries and "Any store a later PR adds names its cap"; this vector has no cap.
- **Required correction:** coalesce in the core. Emit at most one `Ctrl{pid, event}` per (pid, event) per `handle` call, for example with a per-call set, or record "Ctrl+C pending" and expand to clients once at the end of `terminal_input`.
- **Regression test:** a core test with 256 clients and 16 KiB of `0x03`, asserting ≤ 256 `Effect::Ctrl` per event kind in the returned effects.

### F6 (P1): cooked echo sends controls raw and erases one cell per unit, so the screen disagrees with the line
- **File:** `src/cooked.rs:78-81` (any unit is echoed as itself), `:109` (`\b \b` per unit, whatever its width), `:126-128`.
- **Traces (executed, probe `probe_cooked_echo_of_controls_and_wide`):**
  - Typing `ab`, Ctrl+A, Backspace, Enter echoes `ab\x01\b \b\r\n`. The terminal shows `a`, but the read returns `ab\r\n`: the user submits a command they can't see.
  - Typing `x漢`, Backspace, `y` echoes `x漢\b \by`. Backspacing over the wide character moves one cell, so `y` lands one column off. The screen shows `x␣y`, but the line is `xy`.
  - Tab (in `set /p`, python `input()`) echoes a raw HT, then one `\b \b`.
  - Ctrl+Z in `copy con` emits a raw SUB.
- **conhost:** `readDataCooked.cpp:1349-1380` shows controls as `^X` (2 cells) and Tab as spaces to the next stop, and erases by measured distance and graphemes (`:515-519`).
- **Recorded?** Not in §5.10. The change fragment lists only the missing editing keys as limitations.
- **Required correction:** echo controls as `^` + (c + 0x40) and Tab as spaces, and erase by the display width of the removed grapheme (rusty_term_core's width rules, §5.3).
- **Regression test:** a `cooked.rs` unit test asserting the echo `ab^A\b\b  \b\b` (or the chosen erase form) and a width-2 erase for a wide character.

### F7 (P1): Ctrl+S under LINE_INPUT is stored as a character instead of pausing output
- **File:** `src/console/read.rs:116-161` (`key`/`intercept` have no pause-key case).
- **Trace (source-traced):**
  - Our host: in cmd, `dir` + Ctrl+S (`0x13`, or a win32 record VK `S` + Ctrl) + Enter. `next_char` returns `0x13`, `Line::char` appends it and echoes a raw XOFF, and cmd receives `dir\x13\r\n`.
  - conhost under ConPTY: `inputBuffer.cpp:689-698` (`IsPauseKey` with ENABLE_LINE_INPUT) sets CONSOLE_SUSPENDED and drops the key. The next key resumes output and is itself dropped (`:686-688`).
- **Recorded?** Not in §5.10, not in the change fragment's limitations.
- **Required correction:** implement suspension (park output-producing calls while suspended), or at minimum drop the pause key under LINE_INPUT and record the missing pause in §5.10.
- **Regression test:** a core test: a cooked read waiting, input `dir\x13\r`. Assert the line `dir\r\n`, and that a WriteConsole between `\x13` and the next key is held.

### F8 (P2): RAW_READ with a zero-length buffer answers 0xC0000023; conhost answers 0xC0070023
- **Files:** `src/console/read.rs:357-362`; the fixture pins the wrong value at `tests/fixtures/synthetic-apis.txt:53`.
- **Trace (source-traced):**
  - conhost: `IoSorter.cpp:69-85` maps RAW_READ's HRESULT through `NTSTATUS_FROM_HRESULT` (`precomp.h:51-54`, low word only). `DoReadConsole`'s STATUS_BUFFER_TOO_SMALL therefore becomes `0xC0070023`.
  - Only the USER_DEFINED path (`ApiSorter.cpp:186-190`) keeps STATUS_BUFFER_TOO_SMALL.
  - Our host returns `0xC0000023` on both paths. A zero-byte ReadFile on a console handle gets a different Win32 error from GetLastError.
- **Required correction:** for `m == None` (RAW_READ), return `from_hresult(STATUS_BUFFER_TOO_SMALL)`; keep the raw value for ReadConsole.
- **Regression test:** change `synthetic-apis.txt:53` to `c0070023`, and add a core test that ReadConsole with output 0 still gets `c0000023`.

### F9 (P2): Ctrl events go to clients oldest-first; conhost dispatches newest-first
- **Files:** `src/console/read.rs:165-167` (sorted by ascending cookie), `src/ctrl.rs:73-83`. The test `ctrl_c_goes_to_every_client_and_ends_cooked_reads` (`read.rs:918-931`) pins `[(10, C), (11, C)]`.
- **Trace (executed, probe `probe_ctrl_order`):** clients 10, 11, 12 give the order 10, 11, 12.
  - conhost: `ProcessList.cpp:214-220` iterates `crbegin`, "because that's how Windows has historically always dispatched these events". It was restored in microsoft/terminal #18233 after the oldest-first regression broke npm/node teardown (#15373).
  - `ctrl.rs` cites ProcessList.cpp but does the opposite. PR 8 reuses `recipients` for CTRL_CLOSE, where #15373 bites.
- **Required correction:** emit the recipients newest-to-oldest (descending connect order), or record the difference in §5.10.
- **Regression test:** change the existing test to expect `[(11, C), (10, C)]`, plus a `ctrl.rs` unit test of `recipients` order.

### F10 (P2): §8.2 says recordings scrub user history; the recorder now writes every typed byte, passwords included
- **Files:** `src/fixture.rs:24-26` (doc: "terminal input are kept, typed passwords included"), `:43` (`input <hex>`), `src/runtime.rs:593-596`, against `docs/projects/term/conhost/design.md:676`: "A sanitiser scrubs CONNECT fields, paths, environment and user history".
- **Trace (source-traced):** with `RUSTY_CONHOST_RECORD` set, `Event::Input(bytes)` is written verbatim by `event_line`, and `sanitise` doesn't touch input. ReadConsoleW initial data (cmd's re-read line) is written too. The design's promise and the code disagree, and the warning lives only in a module comment.
- **Required correction:** sanitise recorded input (for example, map printable text to a fixed placeholder of the same length and keep controls and sequences), or change §8.2 and §6.1's "Logs" bullet to say recordings carry typed input and must be reviewed.
- **Regression test:** extend `recording_blanks_connect_strings_and_titles` with an `Event::Input(b"hunter2\r")` whose recorded line doesn't contain `68756e74657232`.

### F11 (P2): §5.5 says a Ctrl+C under PROCESSED_INPUT leaves "Nothing … queued"; the code queues its key-up
- **Files:** `docs/projects/term/conhost/design.md:403` against `src/console/read.rs:140-150` (only key-downs are intercepted) and the test comment at `read.rs:928` ("Only the key-up is queued"). The fixture `synthetic-input.txt` likewise expects `input-available true` after Ctrl+C.
- **Trace (source-traced):** the code matches conhost (`input.cpp:114-134` intercepts only `bKeyDown`, and `InputStateMachineEngine.cpp:155-158` writes the key-up too). The design row is what's wrong. A client waiting on the input handle is woken by that key-up.
- **Required correction:** amend the §5.5 row: "the key-down is consumed; its key-up is queued, as in conhost".
- **Regression test:** none needed beyond the existing `read.rs:928` assertion, once the doc matches.

### F12 (P2): tests that §7.2–§7.4 and §8.1 require for PR 3 are missing
- **Files:** `src/console/read.rs:741-1179` (core tests), `tests/own_pty.rs`, `examples/conhost_probe.rs` (untouched).
- **Missing:**
  - **§7.2 core (Linux):** drain then enqueue with a waiting read before, during and after (FlushConsoleInputBuffer as the drain); `HangUp` between a Packet and its completion; `ChildExited` racing a pending cooked read; a second `HangUp`. `the_input_event_always_matches_the_queue` (`read.rs:1150`) covers only Input/Read/HangUp.
  - **§7.4 (Linux):** the stalled-reader test for cooked echo (it would have caught F4); "repeated Ctrl with a stalled worker" (10,000 GenerateConsoleCtrlEvent calls plus input Ctrl+C, a blocked worker, the engine still serving, teardown at the deadline). `a_full_ctrl_queue_drops_new_events` (`runtime.rs`) only pushes 261 entries into a lane without a worker or engine.
  - **§7.2 Windows integration:** the Peek/ReadConsoleInput loop asserting the input event at quiescence; hang_up while a ReadConsole is pending.
  - **§8.1 parity probes** for the APIs this PR adds: GetNumberOfInputEvents, GetConsoleInput, ReadConsole, Flush, GenerateConsoleCtrlEvent.
- **Required correction:** add these at the layers named in §7 and §8.1 (core `Event` tests; Windows `own_pty.rs` and probe subcommands).

## Design questions (behaviour the design decides, which hurts a real client)

- **D1: focus reports reach VT-input clients raw.** §5.5 emits `?1004h` at start and passes all VT raw to VIRTUAL_TERMINAL_INPUT clients, so every focus change types `ESC [ I` / `ESC [ O` into ssh.exe and wsl.exe sessions and on to remote shells. conhost forwards focus to a VT client only after that client enabled `?1004h` itself (`terminalInput.cpp:420-428`, `inputBuffer.cpp:584-599`). This was exactly the bug GH#12799/GH#12900 fixed (cited at `InputStateMachineEngine.cpp:386-387`). Proposal: track the client's own `?1004h` from its output and drop focus pieces otherwise.
  - *Probe:* the Windows probe enables VT input, toggles terminal focus, and checks that ReadConsoleInput yields nothing.
- **D2: V7's reason doesn't match conhost's fallback.** `ApiDispatchersInternal.cpp:76-92` doesn't serve "console-less callers". It serves a target group whose leader isn't (yet) a client but whose parent is, and conhost then adds a process record for it. The classic trigger is `CreateProcess(CREATE_NEW_PROCESS_GROUP)` followed at once by `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)` before the child has CONNECTed (Python `subprocess` `send_signal(CTRL_BREAK_EVENT)`). Under this host that call fails with E_INVALIDARG and the Break is lost. Restate V7's reason or implement the fallback.
  - *Probe:* spawn a child in a new group and send Break immediately, under ConPTY and under this host.

## Optional improvements (non-blocking)

- `Line` reserves `limit * 2` bytes in the parked budget, but `Vec<u16>` grows by doubling: initial 5,000 units grow to a capacity of 10,000 (20,000 B) against 16,384 B reserved. `Vec::with_capacity(limit)` makes the reservation exact.
- `Read.retained` for a non-line read counts `initial.len() * 2` that the read no longer holds. That is harmless over-reservation.

## Failure classes (Step 2)

- **Containment:** short descriptors are refused (ReadConsole < 20 B, GetConsoleInput < 8 B, CtrlEvent < 8 B, GetNumberOfInputEvents < 4 B → STATUS_ILLEGAL_FUNCTION). Bad exe/initial lengths → E_INVALIDARG through `get(..)`. The core uses no `?` or `unwrap`, and the arithmetic is bounded. **Ruled out.**
- **Retained memory:** waiting reads count descriptor + write-back (+ line reserve). The ANSI caches are bounded at 3 B / 2 records. Pending rests are capped at 512 Ki units. Decoder pending is ≤ 4,096 B. **No finding beyond F4 and F5**, which are amplification.
- **Amplification:** F4 (echo, executed), F5 (Ctrl effects, executed). Replies for N reads completed by one slice are bounded by the records consumed (≤ 4,096 × 3 for ANSI, about 240 KiB): ruled out.
- **Boundaries:**
  - after ESC: F2 (executed);
  - mid UTF-8: waits (`split_sequences_and_characters_wait_for_the_rest`), ruled out;
  - between surrogates: F1 (VT mode, executed), stored as pairs otherwise;
  - at the 16 KiB slice: F2 and F3;
  - at the client buffer: split and cached (`an_ansi_read_splits_a_character_that_does_not_fit`), ruled out.
- **Custody:**
  - A ctrl worker that fails to spawn fails `prepare` before any child exists (test).
  - If the engine fails to spawn, its closure (and the `CtrlSender`) drops, which stops the worker (`the_ctrl_worker_stops_when_the_engine_cannot_start`).
  - `send_ctrl` and `set_input_available` failures are logged only.
  - The server stays in `Engine`.
  - **Ruled out.**

## Tests (Step 4)

- Each new core test uses `Event::Input(&[u8])`, the `read` module or `Console::new(_, key_for)`, none of which exist at the base, so they cannot compile there: red at base.
- The changed fixtures were run at base (executed): `synthetic-apis.txt` fails after line 52 and `synthetic-input.txt` after line 5. Both are red at base, so they guard the change.
- Behaviours with no guarding test: F1–F7 (no test exists that goes red), plus the F12 list.

## Checks performed

- `cargo test -p rusty_conhost` (Linux, head copy): **93 passed, 0 failed**. `tests/own_pty.rs` ran 0 tests: Windows-only.
- `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux): **clean**.
- `python3 .github/scripts/changes.py check`: **539 fragment(s) ok**.
- The base fixture run, described above: red, as required.
- Probe tests in the scratch copy (`probe_vt_backspace_shift_tab_surrogates`, `probe_split_after_esc_in_win32_mode`, `probe_paste_into_waiting_cooked_read`, `probe_escape_echo_with_initial_data`, `probe_ctrl_order`, `probe_ctrl_c_flood_effects`, `probe_cooked_echo_of_controls_and_wide`): all ran, with the outputs quoted in F1–F6 and F9.

## Validation gaps

- Windows: `own_pty.rs` (`interactive_cmd_runs_typed_lines`, `ctrl_c_stops_the_running_command_not_the_shell`, `probes_agree_with_conpty`), Windows clippy, and `platform-windows` `key_for` tests were not run; no Windows target is installed here.
- D4 (§8.3): the required manual Windows CI run at `197fae02` was not seen.
- The terminal-side rendering in F6 follows standard VT semantics. A rusty_term run log would confirm it.

## Verdict

**changes required.** F1–F7 are P1 and F8–F12 are P2.
