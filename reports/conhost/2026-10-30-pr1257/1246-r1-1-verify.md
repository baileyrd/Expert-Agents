# rusty_conhost PR #1246 claim verification

Head 197fae02, base 66f4bc38, main checked at 5991f222. origin/main has since moved to 65c04a00b, but no commit in 5991f222..65c04a00b touches the crate. Every file and area cited below was added or changed by the PR (`git diff 66f4bc38 197fae02`: read.rs, cooked.rs, ctrl.rs and input.rs are new).

Oracle: microsoft/terminal at 09beddc62315 (the commit pinned in THIRD_PARTY_NOTICES.md), blobless clone at `verify-conhost/oracle/terminal`.

Probes: the tests `probe_f*` were appended to the `tests` module of `src/console/read.rs` in two scratch worktrees, `verify-conhost/head` (197fae02) and `verify-conhost/main` (5991f222). They were run with `cargo test -p rusty_conhost --lib probe_ -- --nocapture`. Both commits gave identical output, quoted per claim. Paths below are relative to `crates/libs/ui/rusty_conhost` unless they start with `docs/` or `src/host`, `src/server`, `src/terminal` (those are in the oracle).

---

## F1-rest: Backspace and Shift+Tab for a VIRTUAL_TERMINAL_INPUT client

**Class: real (P1 is right). Still present on main.**

- **Our code.**
  - `vt_for` (src/input.rs:532-553) returns the record's `ch` as text whenever `ch != 0` (:543-551). Backspace's own character therefore goes out unchanged: 0x08, and 0x7f with Ctrl. Tab goes out as 0x09 whatever Shift is.
  - The match on VK (:561-584) has no BACK or TAB arm.
  - The path is reachable. The host turns on win32-input-mode (src/console.rs:35 PREAMBLE `CSI ?9001h`; design §5.5:399). `Token::Win32` records go through `key()`, then `vt_for` (src/console/read.rs:92, :118-129).
- **conhost.**
  - `TerminalInput::_encodeRegular` (src/terminal/input/terminalInput.cpp:910-936) maps keys by VK, whatever the codepoint is.
  - VK_BACK gives `ctrl == back ? "\x7f" : "\b"`. With the default BackarrowKey mode off, that is DEL, and BS with Ctrl.
  - VK_TAB with Shift gives `"\x1B[Z"`.
  - `HandleKey` calls `_encodeRegular` before `_formatEncodingHelper` (:407-414).
- **Probe output** (win32 records `CSI 8;14;8;1;0;1_`, `CSI 8;14;127;1;8;1_`, Shift then `CSI 9;15;9;1;16;1_`):
  - Backspace gives `[8]`; conhost sends 0x7f.
  - Ctrl+Backspace gives `[127]`; conhost sends 0x08.
  - Shift+Tab gives `"\t"`; conhost sends `ESC [ Z`.
- **Severity.** P1 is right: this is the default input path, and real VT-input TUIs use Backspace and Shift+Tab.

## F3: a paste into a waiting read keeps only its last 4,096 records

**Class: real (P1 is right). Still present on main.**

- **Our code.**
  - `terminal_input` (src/console/read.rs:84-114) stores every record of the slice (`store`, :131-135) and only then calls `serve_reads` (:113).
  - `Queue::push` (src/input.rs:608-615) drops the oldest record past MAX_RECORDS = 4096 (src/input.rs:21). Text is one record per UTF-16 unit.
  - A slice is up to 16 KiB (src/sched.rs:14 INPUT_SLICE).
- **Probe output.** A cooked ReadConsoleW waits with 20,000 bytes of room, and then "a"×6000 + CR arrives:
  - In one Input event, the read returns 4,096 units and `Diag::InputDropped` is logged.
  - The same bytes split into two events give all 6,002 units (6,000 + CR LF).
  - So the loss depends only on how input happens to be sliced, while a reader is waiting.
- **conhost.** `InputBuffer::_WriteBuffer` (src/host/inputBuffer.cpp:675-728) appends to an unbounded `_storage` deque, and the readers are woken afterwards (:649-662). Nothing is dropped.
- **Design.** The 4,096-record cap with oldest-dropped is documented: §6.1 (design.md:499) and §7.4 (:610). Its rationale is "about 1 s of fast typing or paste at 4K keys/s", which assumes unread input. Nothing in the design says input is dropped while a read is waiting to take it. The cap is a decision; losing a paste to a waiting cooked read is not, so I class this as real and not a design question.
- **Severity.** P1 is right. A paste of more than 4,096 characters into cmd or `input()` is a realistic action.

## F5: a slice of Ctrl+C makes one Effect::Ctrl per client per key

**Class: real. P1 is defensible; the claim understates the memory. Still present on main.**

- **Our code.**
  - `intercept` (src/console/read.rs:140-150) calls `ctrl_event` for each Ctrl+C key-down.
  - `ctrl_event` (:163-168) does `fx.extend` with one effect per recipient. The core does no coalescing.
  - Coalescing and the 256-entry cap happen only later, in the runtime (src/runtime.rs:331-346 `CtrlSender::push`, src/ctrl.rs).
- **Probe output.** With 256 clients (MAX_CLIENTS, src/console.rs:45), one 16 KiB slice of 0x03 gives:
  - `effects=4194306 ctrl=4194304`, with `size_of::<Effect>() = 80`;
  - Vec capacity 8,388,608 entries, about 671 MB (640 MiB) allocated in one `handle` call.
- **Corrections to the claim.** The "~4M effects" figure is exact. The claim's ~320 MiB is length × size; the allocation peak is about twice that, because the Vec grows by doubling.
- **Design.** §7.4 (design.md:621) says "the Ctrl queue adds about 2 KiB". The design does not provide for this transient.
- **Severity.**
  - It needs 256 attached processes, which a client can spawn, plus 16 KiB of Ctrl+C from the terminal side, which only the user or terminal produces.
  - It is bounded (MAX_CLIENTS × INPUT_SLICE per turn), so P1 rather than "unbounded" P0.
  - A hostile client alone cannot trigger it. With the measured 640 MiB per turn, a reviewer could argue for P0.

## F6: cooked echo sends controls raw and erases one cell per character

**Class: real (P1 is acceptable). Still present on main.**

- **Our code.**
  - `Line::char` (src/cooked.rs:78-81) echoes every stored unit as UTF-8 (`push_units`, :126-128). That includes C0 controls, and ESC whenever ESC arrives as a character (VT-input mode, src/console/read.rs next_char, and records from WriteConsoleInput).
  - `backspace` (:97-110) always emits one `"\b \b"`, whatever width the deleted character has. Escape erases by repeated backspace (:91-93).
  - Echo goes straight to the terminal (src/console/read.rs:462-464).
- **One wording fix.** The erase is one cell per *character*, not per UTF-16 unit: a surrogate pair is popped whole and erased once (:101-108). The defect is the same either way, because wide glyphs (CJK, emoji) need two cells.
- **conhost.** `COOKED_READ_DATA::_layoutLine` (src/host/readDataCooked.cpp:1297-1385):
  - renders each control as `^` + (ch+'@') and Tab as spaces to the next tab stop (:1349-1366);
  - measures text with `FitTextIntoColumns`, which is width-aware;
  - redraws from that layout, so the screen always matches the line.
- **Probe output.** After typing "中", DEL, 0x01, 0x07, "x", the terminal received `"中\b \b\x01\x07x"`:
  - one cell erased under a two-cell glyph;
  - a raw SOH;
  - a raw BEL, so the terminal beeps.
  - conhost would show `^A^Gx`.
- **Design.** No section of design.md documents raw control echo or width-blind erase. §10 (:723) scopes PR 3 editing to typing, Backspace, Escape and Enter, but says nothing about rendering.
- **Severity.** P1 is acceptable: CJK input in cmd or `input()`, and arrow keys in a VT-input cooked read, hit it. Calling it P2 as cosmetic would also be arguable.

## F7: Ctrl+S under ENABLE_LINE_INPUT is stored instead of pausing output

**Class: real. I'd put the severity at P2, not P1. Still present on main.**

- **Our code.** `intercept` (src/console/read.rs:140-161) handles only Ctrl+C, Ctrl+Break, Ctrl+Esc and Alt+Esc. No other code in the crate pauses or suspends output (grep on main finds only the `sched` backpressure).
- **Probe output.** A cooked read fed `a ^S b CR`, as a VT 0x13 and as a win32 Ctrl+S record, returns `[97, 19, 98, 13, 10]` both times. The 0x13 is in the line.
- **conhost.**
  - `InputBuffer::_WriteBuffer` (src/host/inputBuffer.cpp:693-698): when the mode has ENABLE_LINE_INPUT and `IsPauseKey` matches (:638-647: VK_PAUSE, or Ctrl+S without Alt), conhost sets CONSOLE_SUSPENDED and drops the key.
  - The next key-down releases the pause (:688-692).
  - This runs under ConPTY for both win32 records and plain 0x13, because both reach `_WriteBuffer`.
- **Design.** design.md never mentions pause, Ctrl+S or suspend, and it is not among the §5.10 divergences.
- **Severity.** P2. It is a missing conhost feature that only a user's keystroke triggers, and the visible effect is a stray control character in the line. It is still a real divergence.

## F8: RAW_READ with a zero-length buffer answers 0xC0000023

**Class: real (P2 is right). Still present on main.**

- **Our code.**
  - src/console/read.rs:357-362 answers `STATUS_BUFFER_TOO_SMALL` (0xC0000023) for `capacity < 1`. The same function serves RAW_READ, with `m = None` (src/console.rs:596).
  - The fixture tests/fixtures/synthetic-apis.txt:52-53 (`packet 6 1 2 0 0 -`, `expect reply c0000023`) pins that answer; this PR changed it from c0000001.
  - On main, the same answer is at read.rs:388-389 and the fixture is unchanged.
- **Probe output.** `status=0xc0000023`.
- **conhost.**
  - IoSorter (src/server/IoSorter.cpp:69-84) handles CONSOLE_IO_RAW_READ with `Status = NTSTATUS_FROM_HRESULT(ServerReadConsole(...))`.
  - `DoReadConsole` returns `STATUS_BUFFER_TOO_SMALL` for `buffer.size() < 1` (src/host/stream.cpp:486-488).
  - `NTSTATUS_FROM_HRESULT` is `NTSTATUS_FROM_WIN32(HRESULT_CODE(hr))` (src/server/precomp.h:51-54), so 0xC0000023 becomes 0xC0070023.
  - The exemption for STATUS_BUFFER_TOO_SMALL exists only in ApiSorter (src/server/ApiSorter.cpp:187-193), which serves USER_DEFINED ReadConsole, not RAW_READ. ReadConsole's 0xC0000023 is therefore right, and only RAW_READ is wrong.

## F9: Ctrl events go oldest client first

**Class: real (P2 is right). Still present on main.**

- **Our code.**
  - `ctrl_event` (src/console/read.rs:163-168) sorts clients by ascending cookie. Cookies increase with each CONNECT (src/console.rs:614-616, :641).
  - `recipients` (src/ctrl.rs:73-83) keeps that order and also keeps the first, oldest, entry for a duplicate pid.
  - The test read.rs:918-931 pins `[(10,C),(11,C)]`.
- **Probe output.** With three clients, the order is `[(10, C), (11, C), (12, C)]`.
- **conhost.** `ConsoleProcessList::GetTerminationRecordsByGroupId` (src/server/ProcessList.cpp:214-220) walks `_processes.crbegin()`, which is newest to oldest: "because that's how Windows has historically always dispatched these events".
- **Design.** design.md does not document an order: §5.5 and §7.2 only say "in queue order".

## F10: design says recordings scrub user history, but the recorder writes typed input

**Class: real (P2 is right). Still present on main.**

- **Design.** design.md:676 (§8.2): "A sanitiser scrubs CONNECT fields, paths, environment and user history."
- **Our code.**
  - The PR changed the recorder to write every terminal byte: `Event::Input(bytes) => format!("input {}", hex_or_dash(bytes))` (src/fixture.rs:43). The base wrote a bare `input`.
  - The recorder records every event before `handle` (src/runtime.rs:593-596).
  - The module doc (src/fixture.rs:24-26) admits "terminal input are kept, typed passwords included, so review a recording before committing it".
- **Why not a design question.** The design states the opposite, and the deviation is documented only in the code. That is docs disagreeing with code, and the PR made it reachable.
- **Severity.** P2: recording is opt-in through RUSTY_CONHOST_RECORD and writes a local file.

## F11: design says Ctrl+C under PROCESSED_INPUT leaves nothing queued

**Class: real (P2, docs disagree with code). The code is the conhost-correct side, so the fix belongs in design.md. Still present on main.**

- **Design.** design.md:403 (§5.5) says "Nothing is queued."
- **Our code.**
  - `intercept` returns false for key-ups (src/console/read.rs:141-143), so the synthesized Ctrl+C key-up is stored.
  - The test read.rs:928-930 says "Only the key-up is queued" and asserts `input.len() == 1`.
- **Probe output.** After 0x03, 1 record is queued. After a win32 Ctrl+C down/up pair, the Ctrl event is delivered and the key-up is queued as well.
- **conhost.**
  - `InputStateMachineEngine::_DoControlCharacter` (src/terminal/parser/InputStateMachineEngine.cpp:151-158) sends both key-down and key-up through `WriteCtrlKey`, which calls `HandleGenericKeyEvent` (src/terminal/adapter/InteractDispatch.cpp:57-60).
  - `HandleGenericKeyEvent` (src/host/input.cpp:115-176) intercepts only `bKeyDown`, so the key-up reaches `pInputBuffer->Write`.
  - conhost queues the key-up too.
- **Conclusion.** The claim is accurate as "the doc disagrees with the code", but the defect is in the document, not in the behaviour.

## F12: tests required by §7.2–§7.4 and §8.1 for PR 3 are missing

**Class: real (P2 is right). Partly addressed on main, not fixed. The claim's evidence is partly wrong.**

**Wrong parts of the claim:**
- tests/own_pty.rs is *not* untouched. The PR adds 109 lines to it, including `interactive_cmd_runs_typed_lines` and `ctrl_c_stops_the_running_command_not_the_shell`.
- read.rs:741-1179 is the new test module, which the PR added.
- The §7.2 core invariant test exists: `the_input_event_always_matches_the_queue` (read.rs:1148-1178).

**Right part:** examples/conhost_probe.rs has no diff in the PR.

**Still missing at head:**
- §8.1 parity probes for the PR 3 input APIs ("each API PR adds probes", design.md:653). conhost_probe.rs is untouched.
- §7.2 Windows integration test, a PeekConsoleInput/ReadConsoleInput loop (design.md:574). Not in the probe or in own_pty.rs.
- §7.2 shutdown test with a ReadConsole pending. `hang_up_closes_the_console_and_releases_waiters` (own_pty.rs:309) predates the PR and has no pending read.
- §7.2 core interleavings: "ChildExited racing a pending cooked read", and enqueue/drain with a waiting read before, during and after. Only the single sequence in read.rs:1150 exists.
- §7.4 "repeated Ctrl with a stalled worker: 10,000 GenerateConsoleCtrlEvent plus input Ctrl+C … engine keeps serving … teardown at the deadline". Only unit tests of the queue exist (runtime.rs:845, ctrl.rs:90).
- §7.4 stalled-reader test for cooked echo under sustained input.

**On main:** 8ec54b85a added `cooked_echo_stays_within_the_host_reserve_and_resumes_on_drain` (read.rs:999 on main), which covers the cooked-echo item. The other items are still missing on 5991f222.
