# conhost-expert review: rusty_conhost host PR 3 (console input)

## Pin

- Repository: rusty_mill (local worktree `/home/user/rusty_mill/.claude/worktrees/conhost-trial`)
- PR: host PR 3 (console input). Reviewed as a commit range, not a fetched PR head.
- Base: `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- Head: `197fae02607fa8c4428614aef7a4e7ac1b5bca7f`. Worktree `HEAD` equals it, `66f4bc38` is an ancestor, and the tree is clean.
- Commits: 047f7de2 (console input), dc4aed5e (cap kept line rests; docs, notices, fragment), 2af81d01, 197fae02 (Windows test fixes).
- Oracle: microsoft/terminal `09beddc62315b7eb9e63949252102c4e9b431929`, the commit pinned in THIRD_PARTY_NOTICES.md. Paths are checked out at that commit, and `src/terminal/input` and `src/interactivity/base` were added from it.
- "Executed" traces ran as extra `#[test]` probes in a **scratch export** of the head commit (`git archive 197fae02`, in `scratchpad/exec`). The reviewed worktree was not modified.

## Step 1: Predictions (conhost under ConPTY)

| API / path | conhost under ConPTY | conhost test | Client that depends on it |
|---|---|---|---|
| GetConsoleInput (read/peek, NOWAIT) | `ApiDispatchers.cpp:136-210` ServerGetConsoleInput: zeroes NumRecords, rejects unknown flags with E_INVALIDARG, needs GENERIC_READ. `directio.cpp:65-68`: 0 records returns at once. A waiting peek removes what it reads (`readDataDirect.cpp:107-112`, peek=false). Ctrl+C/Break don't end it (`readDataDirect.cpp:73-76`). | `ft_host/API_InputTests.cpp:215-241` TestPeekConsoleInvalid, `:243-271` TestReadConsoleInvalid; `ut_host/InputBufferTests.cpp` CanPeekAtEvents, StreamPeekingDeCoalesces | .NET `Console.ReadKey` reads with ReadConsoleInput (`dotnet/runtime v9.0.0 ConsolePal.Windows.cs:390,441`), as do PSReadLine and edit.exe |
| ReadConsole (cooked) | `stream.cpp:466-527` DoReadConsole: buffer < 1 gives BUFFER_TOO_SMALL; pending input first; LINE_INPUT starts COOKED_READ_DATA. `readDataCooked.cpp:469-523` _handleChar (wakeup mask, CR, grapheme backspace); `:734-826` post-loop (`\r\n`, or `\r` without PROCESSED; pending saved only when non-empty). Echo is drawn through the screen buffer, so control characters show as `^X` (`readDataCooked.cpp:1349-1366`). Ctrl+C/Break end it with STATUS_ALERTED (`:216-221`). | API_InputTests TestReadConsolePasswordScenario, TestReadWaitOnHandle | cmd's prompt (ReadConsoleW, CtrlWakeupMask Tab, nInitialChars). cmd's source isn't available; behaviour comes from the CONSOLE_READCONSOLE_CONTROL docs |
| ReadConsole (raw) / RAW_READ | `stream.cpp:394-440` ReadCharacterInput: BUFFER_TOO_SMALL below one char; repeat counts split one char at a time (`inputBuffer.cpp:400-445`). `IoSorter.cpp:69-74`: RAW_READ is ReadConsole with ProcessControlZ, honoured only under PROCESSED (`ApiDispatchers.cpp:282-290`). Ctrl+C doesn't end it, Ctrl+Break does (`readDataRaw.cpp:84-94`). | API_InputTests `:671` RawReadUnpacksCoalescedInputRecords | `type con`, ReadFile on stdin, libuv |
| RAW_FLUSH / FlushConsoleInputBuffer | `IoSorter.cpp:87-94`, `ApiDispatchers.cpp:506`; both need write access | API_InputTests TestFlushValid/Invalid | any client that flushes typeahead |
| GetNumberOfConsoleInputEvents | `ApiDispatchers.cpp:116-134`; `_storage.size()` (`inputBuffer.cpp:320-323`) | InputBufferTests CanGetNumberOfReadyEvents | .NET `Console.KeyAvailable`. No other client checked |
| GenerateConsoleCtrlEvent, groups | `ApiDispatchersInternal.cpp:66-100`. A group nobody is in falls back to the parent pid and pre-registers the process. Delivery goes newest to oldest (`ProcessList.cpp:204-216`) | none found | Python `os.kill(pid, CTRL_BREAK_EVENT)` after `Popen(creationflags=CREATE_NEW_PROCESS_GROUP)` |
| Ctrl+C / Ctrl+Break keys | `input.cpp:106-152` HandleGenericKeyEvent. Only the key-down is intercepted; the key-up is still written. Ctrl+Break flushes. ConPTY routes 0x03 and every win32 record here (`InputStateMachineEngine.cpp:150-158, 511-519`) | none found for ConPTY | cmd, `ping -t` |
| VT / win32-input-mode decoder | `InputStateMachineEngine.cpp`. A trailing ESC or ESC+char is a key only while no win32-input-mode record has been seen; after one, it is cached as a split sequence (`stateMachine.cpp:2033-2076`). In non-VT mode, a CPR `CSI r;c R` is not F3 once win32 mode has been seen (`InputStateMachineEngine.cpp:402-418`). Unhandled CSI, OSC and DCS pass through as text in both modes (`:579-589`, `stateMachine.cpp:1939-1965`) | `ut_parser/InputEngineTest.cpp` AltIntermediateTest, TestWin32InputParsing, TestWin32InputOptionals | edit.exe (VT input, `microsoft/edit crates/edit/src/sys/windows.rs:233`), wsl.exe |
| Focus reports | `InteractDispatch.cpp:197-210`, `inputBuffer.cpp:585-599`: a VT-input client gets `CSI I/O` only if it enabled focus mode itself (`terminalInput.cpp:420-428`, GH#13238). Otherwise a FOCUS_EVENT record | none found | any VT-input client that never sends `?1004h` (edit.exe: `crates/edit/src/bin/edit/main.rs:600` doesn't enable it) |
| Text in non-VT mode | `InteractDispatch.cpp:47-80` WriteString calls CharToKeyEvents (VkKeyScan VK, key-down and key-up; `EventSynthesis.cpp:35-62`) | InputEngineTest | .NET ReadKey reads `wVirtualKeyCode` into `ConsoleKeyInfo.Key` (`ConsolePal.Windows.cs:441`) |
| Input-available event | reset on flush or empty (`inputBuffer.cpp:333-337`) | InputBufferTests EmptyingBufferDuringReadSetsResetWaitEvent | WaitForSingleObject(stdin) callers |
| Start/stop sequences | `VtIo.cpp:198-204` sends `CSI c`, `?1004h`, `?9001h`; `VtIo.cpp:250-262` sends `?1004l ?9001l` on shutdown | VtIoTests | the terminal |
| CLOSE_OBJECT / DISCONNECT with reads | `ObjectHandle.cpp:224-251`: closing an input handle that has reads notifies **all** waiters with HandleClosing (STATUS_ALERTED); a process exit gives ThreadDying (STATUS_THREAD_IS_TERMINATING) | none found | multithreaded readers |

## Step 2: Failure classes

- **Containment.** Ruled out, with reasons. Every fixed-field read is an `Option` or is length-checked first (`read.rs:279-286, 333-336`; `read.rs:242-244`; `read.rs:256`). Zero capacity gives BUFFER_TOO_SMALL (`read.rs:359-362`). Zero records answers at once (`read.rs:298, 314`). `read_args` slices with `.get()` (`read.rs:679-682`). `vt_for`'s table index is in range for F5–F12. No `?` or `unwrap` sits in a path that a packet or input reaches. `cargo test` and clippy pass on Linux.
- **Retained memory.** A cooked read reserves `2×max(initial, 8192) + 20` (`read.rs:382`), which is at most 65,556 = `MAX_RETAINED_PER_CALL`. Admission requires `parked + 65,556 ≤ 1 MiB` (`sched.rs:99-101`), so at most 60 cooked reads wait (60 × 16,404 = 984,240 B). A wakeup unit and the `\r\n` suffix go up to 3 units past the reservation. That is 6 B per read, negligible, so ruled out. The ANSI caches stay at 3 B and 2 records. Kept rests are capped (`read.rs:497-502`). **Broken:** the read reply has no 1 MiB cap (F3), and Ctrl effect fan-out is unbounded (F4).
- **Amplification.** **Broken:** cooked echo (F2), Ctrl fan-out (F4) and read replies (F3). One input slice completes only the first waiting read, because the queue is consumed once, so N-read amplification is ruled out.
- **Boundaries.** **Broken:** ESC at a slice boundary (F5); a whole 16 KiB slice decoded before reads are served (F1); UTF-16 surrogates in VT mode (F9); an OSC reply over 4 KiB split across slices (F10). Mid-UTF-8 is held correctly (`input.rs:225-231`), except that an invalid byte earlier in the same run turns the split character into U+FFFD (minor). At the client's buffer size, ANSI splits are cached and tested.
- **Custody.** Ruled out. The ctrl worker starts before the child exists, a failure returns before `spawn_attached` (`runtime.rs:383-386`), and the worker stops when `CtrlSender` drops (tested, `runtime.rs:826-843`). The worker holds pids, not handles. Teardown doesn't wait for it. This is Windows runtime code I could not run.

## Blocking findings

### F1 [P0] A paste longer than 4,096 characters loses its head, even while cmd's cooked read is waiting
- **Where:** `src/console/read.rs:91-113` (every piece of the slice is stored before `serve_reads` at :113), `src/input.rs:608-615` (`Queue::push` drops the oldest record), `src/sched.rs:14` (`INPUT_SLICE` = 16 KiB).
- **Scenario (executed):** a cooked ReadConsoleW is waiting with a 20,000 B buffer. The terminal sends 5,000 × `a` followed by `\r` in one write. rusty_term sends non-bracketed paste as plain UTF-8 (`rusty_term/src/gui/window.rs:5875-5894`), so each character becomes one record. 5,002 records go into a 4,096 queue before any read runs, so 906 are dropped. The read returns 4,096 units (4,094 `a` + `\r\n`). In general a 16 KiB slice can drop up to 12,288 records. conhost's queue is unbounded and the read drains it (`inputBuffer.cpp`).
- **Client:** interactive cmd (the PR's own acceptance target): pasting a long command line silently drops its start.
- **Fix:** stop decoding when the queue is full and leave the rest of the bytes in the input lane, so backpressure holds the writer. Or call `serve_reads` before a push would drop a record. Keep the 4,096 drop only for records nobody is waiting for.
- **Test:** a core `Event` test: a waiting cooked read, then `Event::Input(5000×'a' + '\r')`, should return 5,000 `a` + `\r\n`.

### F2 [P1] Cooked-read echo has no output budget: about 14.9 MB from one 16 KiB slice, against a 144 KiB cap
- **Where:** `src/console/read.rs:449-464` (the loop drains every record, then `out.push(&echo)`), `src/console.rs:239-241` (`push` is unbounded), `src/input.rs:632-641` (repeat counts expand one character at a time), `src/cooked.rs:91-93,109`.
- **Scenario (executed):** a waiting cooked read, then one slice of 455 × (`CSI 65;0;97;1;0;65535_` + `CSI 27;0;27;1;0;1_`) = 16,380 B. Each pair types 8,192 `a` (8,192 B of echo), then Escape erases them (8,192 × 3 = 24,576 B). Total echo: **14,909,440 B** in one `handle` call, against §7.4's 144 KiB output bound. The engine only checks the queue (≤ 80 KiB) before taking input, not while echo is made. A related trace: 819 such records (no Escape) keep one Input event busy for **936 ms** (release build). That is 53 M `pop_stream` iterations once the line is full, which breaks §7.2's bounded turn. Without repeat counts, `8192×a + Esc record` gives about 4× (≈ 64 KiB per slice), which already reaches 80 + 64 = 144 KiB. This is the same input path conhost has, but conhost draws the echo through its screen buffer, so ConPTY's output is bounded by the viewport.
- **Trigger:** bracketed paste passes ESC bytes (`window.rs:5877-5889`), so clipboard content can carry win32 records once any client has left `?2004h` on.
- **Fix:** stop the cooked loop once `out.len()` reaches the 16 KiB host reserve, leaving records queued for the next turn. When the line is full, drop a record's remaining repeats in O(1).
- **Test:** the §7.4 stalled-reader test "cooked echo under sustained terminal input" is listed in the design but missing. It should assert high water ≤ 144 KiB for the trace above.

### F3 [P1] The 1 MiB read-reply cap in §7.4 isn't enforced
- **Where:** `src/console/read.rs:357` (`capacity = output_size - at`, uncapped), `:508-519` (`read_chars` fills it through `pop_stream`), `:297`.
- **Scenario (executed):** a raw ReadConsoleW declaring 16 MiB, with 64 queued records of repeat 65,535 (1,280 input bytes), builds an **8,388,480 B** reply. The worst case is 4,096 × 65,535 × 2 = **536,862,720 B**, against the documented 1 MiB (`design.md:618`). conhost writes into the client's buffer; this host builds the whole reply in its own memory.
- **Fix:** `capacity = min(output_size - at, 1 MiB)` for ReadConsole, RAW_READ and GetConsoleInput. Define the constant in `sched` next to `PARKED_CAP`.
- **Test:** a core test: the trace above should give `reply.output.len() ≤ 1 MiB`, with the rest still queued.

### F4 [P1] Ctrl effects grow with clients × Ctrl+C bytes: 335 MB in one `handle` call
- **Where:** `src/console/read.rs:147-156, 164-169`. Coalescing happens only later, in `CtrlSender::push` (`runtime.rs:336-347`).
- **Scenario (executed):** 256 connected clients, then one 16 KiB slice of `0x03` bytes. That makes 16,384 × 256 = **4,194,304 `Effect::Ctrl`** × 80 B = **335,544,320 B** of `fx` Vec before the 256-entry queue coalesces anything. This is outside every §7.4 bound. The slice needs bracketed paste or a hostile terminal, since plain paste strips C0.
- **Fix:** coalesce in the core. Keep a per-call set of `(pid, event)` already emitted, or a pending flag like conhost's `CtrlFlags` (`input.cpp:228-245`).
- **Test:** a core test: 256 clients plus 16 KiB of `0x03` should give ≤ 256 Ctrl effects per call.

### F5 [P1] A trailing ESC is always the Escape key, so a win32 record split after ESC clears the line and types garbage
- **Where:** `src/input.rs:237-242`, and `:245-248` / `:244` for `ESC O` and `ESC [` at the end of input.
- **Scenario (executed):** with win32-input-mode active (the host requests it, `console.rs:35`), the input `CSI 65;30;97;1;0;1_ CSI 65;30;97;0;0;1_`, then `ESC`, then `[66;48;98;1;0;1_ CSI 13;28;13;1;0;1_` returns the cooked line `"[66;48;98;1;0;1_\r\n"`. The Escape key erased the `a`, and the rest of the record was typed as text. A 16 KiB slice cuts a record right after ESC about once in 16 to 20 boundaries during a large burst. conhost caches the trailing ESC once a win32 record has been seen (`stateMachine.cpp:2033-2076`). Without win32 mode, conhost treats a trailing `ESC [` / `ESC O` as Alt+`[` / Alt+`O` (AltIntermediateTest, GH#1209). Ours holds them, so the next key `A` becomes Up.
- **Client:** cmd (line lost). A VT-input client such as edit.exe receives a raw `CSI …_` record it doesn't parse.
- **Fix:** remember that a win32 record has been seen. If one has, keep a trailing ESC pending (bounded by `MAX_PENDING`). If not, end-of-input `ESC x` (x ≤ 2 bytes) is a key.
- **Test:** a core `Event` test (the trace above), plus the AltIntermediateTest case as a fixture step.

### F6 [P1] Focus reports reach VT-input clients that never asked for them
- **Where:** `src/console/read.rs:100-104` (a `Token::Focus` piece passes through raw in VT mode).
- **Scenario (executed):** a client in `ENABLE_VIRTUAL_TERMINAL_INPUT`, then the terminal sends `CSI I`. ReadConsoleW returns `"\x1b[I"`. Because the preamble turns `?1004h` on for the terminal, every focus change injects `CSI I` / `CSI O`. conhost sends them only if the client enabled focus mode (`inputBuffer.cpp:585-599`, `terminalInput.cpp:420-428`; the GH#13238 comment).
- **Client:** wsl.exe with bash/readline (stray `I` / `O` or a beep), and any VT-input app.
- **Fix:** in VT mode, drop Focus pieces unless the client has written `?1004h` (track it from client output, as PR 4's query routing will). In non-VT mode keep the FOCUS_EVENT record.
- **Test:** a core test in VT mode: `Input(CSI I)` should queue nothing.

### F7 [P1] Cooked echo writes raw control characters and ESC to the terminal, and isn't laid out
- **Where:** `src/cooked.rs:78-81, 126-128` (raw UTF-16 to UTF-8 echo), `:96-110` (always `\b \b`, one code unit).
- **Scenario (executed):** typing `a`, Ctrl+L, Ctrl+N, Ctrl+G echoes `a\x0c\x0e\x07`: form feed, shift-out, bell. A win32 record `CSI 0;0;27;1;0;1_` followed by `]0;pwn\x07` echoes `\x1b]0;pwn\x07`, which the terminal runs as OSC (title, or OSC 52 clipboard write). conhost shows `^L`, `^N`, `^[` (`readDataCooked.cpp:1349-1366`). Also executed: Backspace after `e`+U+0301 deletes only the combining mark (conhost: `GraphemePrev`, `:493-500`), and `\b \b` erases one column of a wide character. Neither handles a line that has wrapped.
- **Client:** interactive cmd (Ctrl+L moves the cursor and later backspaces desync), and pasted text.
- **Fix:** echo C0 as `^X` and Tab as spaces, delete by grapheme, and erase by column width. Until the line is laid out against the Grid (PR 4), at least never emit C0 or ESC.
- **Test:** a core test: cooked read + `Input("a\x0c")` should give VT `a^L`. Add the `CSI 0;0;27;1;0;1_` case.

### F8 [P1] A second cooked read on the same handle overwrites the first read's kept rest
- **Where:** `src/console/read.rs:497-502` with `:649-653` (`keep_pending` assigns even an empty rest).
- **Scenario (executed):** two cooked reads wait on one handle, the first with a 4 B buffer. Input `abcdef\r`: read 1 gets `ab` and `cdef\r\n` is kept. Input `xy\r`: read 2 gets `xy\r\n` and replaces the kept rest with empty. A third read waits, and `cdef\r\n` is lost. conhost saves pending only when it is non-empty (`readDataCooked.cpp:813-816`). It is data loss but needs two concurrent readers, so I rate it P1 rather than P0.
- **Fix:** append to the kept rest, or skip `keep_pending` when the rest is empty. Serve the kept rest to a read that is already waiting before it takes new input.
- **Test:** a core test with the trace above.

## Narrower defects (P2)

- **F9 [P2] Surrogate pairs in VT mode become two U+FFFD.** `src/input.rs:544-552` encodes each record's unit alone. Executed: records 0xD83D, 0xDE00 give `"��"`. conhost joins them (`terminalInput.cpp:244-255`). Client: a terminal that sends a pair as two records (Windows Terminal), typing into edit.exe. Fix: keep a lead surrogate in the console. Test: a core VT test. (Separately, rusty_term sends only the first unit of a non-BMP character, `rusty_term/src/gui/input.rs:462`, which is out of this PR's scope.)
- **F10 [P2] An OSC or DCS reply over 4,096 B split across slices is typed as text.** `src/input.rs:194-200` gives up on the start, and the next slice's body decodes as Text. Example: an OSC 52 clipboard-read reply of 20 KB for a VT client (nvim on WSL) delivers about 16 KB of base64 as keystrokes. Fix: once you give up on a string sequence, drop everything up to its terminator. Test: a fixture with a 10 KB OSC in two `input` steps.
- **F11 [P2] For non-VT clients, a CPR reply with row 1 becomes Ctrl/Shift+F3.** `src/input.rs:382-385, 421-425`. Executed: `CSI 1;5R` queues Ctrl down, F3 down, F3 up, Ctrl up. conhost skips the F3 reading once a win32 record has been seen (`InputStateMachineEngine.cpp:402-418`). Fix: apply the same rule as F5. Test: a core test.
- **F12 [P2] V8 understates the divergence for synthesized text.** `src/input.rs:508` gives text a key-down only, with VK 0. conhost makes VkKeyScan VKs and key-down/up pairs (`InteractDispatch.cpp:68-77`, `EventSynthesis.cpp:35-62`). V8 (`design.md:478`) mentions only scan codes. Client: .NET `Console.ReadKey().Key` is 0 for pasted text (`ConsolePal.Windows.cs:441`). Fix: use `key_for` and pairs, or extend V8. Test: a core test of the record fields.
- **F13 [P2] Replies dropped for non-VT clients aren't in §5.10.** `src/console/read.rs:105-110`, `src/input.rs:524`, design §5.5 "dropped". conhost passes OSC and unhandled CSI/DCS through as text in any mode (`InputStateMachineEngine.cpp:579-589` cites the documented contract; `stateMachine.cpp:1939-1965`). Fix: add a V9 row with its reason. Test: the existing `replies_and_unknown_sequences_are_dropped_for_a_win32_client`, linked to V9.
- **F14 [P2] Ctrl delivery order is oldest first; conhost's is newest first.** `src/console/read.rs:165-167` vs `ProcessList.cpp:214-216` ("how Windows has historically always dispatched"). Fix: sort by cookie descending. Test: update `ctrl_c_goes_to_every_client_and_ends_cooked_reads` to expect `[(11, C), (10, C)]`.
- **F15 [P2] No `?1004l ?9001l` at teardown, and no DA1.** `src/runtime.rs:474, 687-701`. ConPTY sends `?1004l ?9001l` on shutdown (`VtIo.cpp:250-262`). A pane that outlives the child stays in win32-input-mode and focus reporting. The doc's "as inbox conhost does" (`design.md:399`) also leaves out conhost's `CSI c`. Fix: push the reset before `Eof`. Test: a runtime/fixture step expecting the bytes at close.
- **F16 [P2] V7's reason doesn't hold.** `src/console/read.rs:252-261`, `design.md:477`. conhost's fallback (`ApiDispatchersInternal.cpp:76-92`, unchanged since the 2019 import d4d59fa3) serves a target that hasn't **connected yet**: a child just spawned with CREATE_NEW_PROCESS_GROUP, whose parent is a client. "Our clients always carry their group" doesn't cover that race. Example: Python `Popen(..., CREATE_NEW_PROCESS_GROUP); os.kill(p.pid, CTRL_BREAK_EVENT)` immediately gets `E_INVALIDARG` (WinError 87). Fix: restate V7 with this case, or implement the parent-pid fallback. Validation: a Windows probe that spawns a new-group child and signals it at once.
- **F17 [P2] CLOSE_OBJECT ends only its own handle's reads.** `src/console.rs:587-590`. conhost notifies **every** waiter on the input buffer with HandleClosing (`ObjectHandle.cpp:235-238`). Ours is arguably saner, but it is an unrecorded divergence. Fix: add it to §5.10. Test: none needed beyond the existing `closing_a_handle_or_disconnecting_ends_its_reads`.
- **F18 [P2] The docs disagree with the code.** `design.md:403` says Ctrl+C "Nothing is queued", and the Ctrl+Break row says "Never queued". The key-up **is** queued (`read.rs:141-143`; the fixture `synthetic-input.txt` expects `input-available true` after `03`), which matches conhost (`input.cpp:155-162`). `design.md:618` says read replies are capped at 1 MiB, but the code doesn't cap them (F3). Fix: correct the rows.
- **F19 [P2] Tests the design asks for are missing.**
  - §7.2's core interleavings "ChildExited racing a pending cooked read" and "a second HangUp" (only HangUp is in `read.rs:1149-1178`). Layer: core `Event`.
  - §7.4's cooked-echo stalled reader (see F2).
  - Ctrl+C under VIRTUAL_TERMINAL_INPUT with PROCESSED on and off. Layer: core.
  - §7.2's Windows integration "input event signalled iff records queued at quiescence". `tests/own_pty.rs` covers only typed cmd lines and Ctrl+C. Layer: Windows probe (`examples/conhost_probe.rs` peek/read loop).

## Optional improvements

- `numbers()` (`input.rs:323-333`) turns an over-long parameter into `None` (default), where conhost saturates it. That is harmless.
- `Console::new(size, key_for)` vs the design's `ConsoleConfig { size, key_for }` (`design.md:161-164`) is crate-internal, but the sketch and the code could match.
- rusty_term's win32 encoder drops the second UTF-16 unit (`rusty_term/src/gui/input.rs:462`). Worth its own ticket.

## Scope and provenance

- The §10 row for PR 3 is covered: parser, raw passthrough, reply filter, waiting reads, input event, cooked read, Ctrl+C/Break, groups, GenerateConsoleCtrlEvent, 65001 (`console.rs:1101-1106`). The ctrl worker moving from PR 8 is recorded in the row. No scope drift found.
- THIRD_PARTY_NOTICES lists `input.rs`, `cooked.rs`, `ctrl.rs` and `console/read.rs` with their conhost sources. No provenance gap found.
- Change fragment: present and consistent, apart from what F12 and F18 note.

## Checks

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux, worktree) | passed: 93 unit tests; `own_pty` 0 (Windows-only) |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux) | passed. Windows-only code (`runtime.rs`, `condrv.rs`) wasn't compiled because no Windows target is installed |
| `python3 .github/scripts/changes.py check` | passed: 539 fragments ok |
| Probe tests (scratch export, `cargo test --release -p rusty_conhost --lib probe_`) | 12 probes ran; the numbers are quoted in F1–F11 |

**Validation gaps:**
- I couldn't run the Windows tests (`tests/own_pty.rs`, `runtime.rs` tests, `condrv::key_for`) or Windows clippy.
- I saw no D4 `workflow_dispatch` run at 197fae02.
- F16 needs a Windows probe.
- Whether a ConDrv client can actually declare a multi-MiB ReadConsole output (F3's worst case) needs a probe; 16 MiB is ordinary for ReadFile.

## Verdict

**Changes required.**
