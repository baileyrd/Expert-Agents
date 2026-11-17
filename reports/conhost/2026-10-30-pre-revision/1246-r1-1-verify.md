# Verification: rusty_conhost claims, host PR 3 (#1246)

- Head checked: `197fae02` (worktree `.claude/worktrees/v-conhost-head`). Main checked: `ad1c3e37` (worktree `.claude/worktrees/v-conhost-main`). Both worktrees were removed afterwards.
- conhost reference: microsoft/terminal `09beddc6`. I read `inputBuffer.cpp`, `readDataCooked.cpp`, `readDataRaw.cpp`, `readDataDirect.cpp`, `inputReadHandleData.cpp`, `InputStateMachineEngine.cpp`, `stateMachine.cpp`, `InteractDispatch.cpp`, `ProcessList.cpp`, `ObjectHandle.cpp`, `ApiDispatchersInternal.cpp` and `VtIo.cpp`.
- Probes: I added 11 `probe_*` tests to the `tests` module of `src/console/read.rs`, using the `Host` harness, and ran the same block at head and at main with `cargo test -p rusty_conhost probe_ -- --nocapture`. All ran. The probes, as a diff against the head file, are in `1246-r1-1-verify-probes.diff` (`git apply` at `197fae02`). Nothing was committed.
- I did not read the reviewer's evidence, the PR, or any report or probe files.

Paths are under `crates/libs/ui/rusty_conhost/`.

## F1: real, P1 rather than P0. On main.
- Executed at head and at main. A cooked ReadConsoleW of 32 KiB is waiting. One `Event::Input` carries 5,000 × `a` plus CR. The read completes with 4,096 chars, which is 4,094 `a` plus `\r\n`, and `Diag::InputDropped` is logged. So 906 leading characters are lost.
- Cause at head:
  - `src/console/read.rs:91-113`: `terminal_input` stores every piece of the slice and only then calls `serve_reads`.
  - `src/input.rs:608-615`: `Queue::push` drops the oldest record past `MAX_RECORDS` = 4,096.
  - The engine hands the core up to 16 KiB per turn (§7.2 step 4).
- Main: `read.rs` `terminal_input` (around lines 90-121) has the same store-then-serve order, and `Queue::push` is unchanged. The probe gives the same result.
- conhost's `InputBuffer` storage has no such cap, so the waiting read would get the whole paste.
- Partly a design matter: the 4,096 drop-oldest cap is documented (§6.1, §7.4). But §6.1 justifies it as a backlog of about 1 s of input. Dropping input that a read already waiting would consume is not covered by that.
- Severity: P1. Truncated pastes reach real clients like cmd, and the truncated line still executes. It needs a single paste over 4,096 characters, and the drop is bounded and logged, so it does not reach P0.

## F3: fixed_since, P1 right. Not on main.
- Executed at head: 100 win32-input-mode records (`CSI 65;0;97;1;0;65535_`, 2,000 input bytes) and a raw ReadConsoleW declaring `u32::MAX - 64` give a 13,107,000-byte reply.
- At head, `read.rs:357` computes capacity as `output_size - at` with no cap, and `:297` does the same for records. Nothing in `src/` enforces the 1 MiB of §7.4.
- Main adds `READ_REPLY_CAP = 1 MiB` (read.rs:33-35), applies it at `get_console_input` and `read_console`, and adds a per-call budget in `serve_reads`. The same probe at main gives 1,048,576 bytes.

## F4: real, P1 borderline. On main.
- Executed at head and at main: 256 clients and one 16 KiB slice of `0x03` give 4,194,306 effects in one `handle` call, 4,194,304 of them `Effect::Ctrl`.
- `size_of::<Effect>()` is 80, so the Vec is about 335 MB, and more while it grows.
- At head, `read.rs:147-169`: `intercept` then `ctrl_event` pushes one effect per recipient for every Ctrl+C key-down. Coalescing happens only later, in the runtime's `ctrl::Queue`.
- Main `ctrl_event` (around read.rs:168-177) is unchanged apart from order.
- Severity: §7.4 bounds per-console memory at about 3.3 MiB, and this transient breaks that containment. The headline number needs the 256-client maximum. With a handful of clients it is MBs, which would be P2.

## F6: real, P1 right. On main.
- Executed at head and at main: a client with VIRTUAL_TERMINAL_INPUT that never sent `?1004h` reads `"\x1b[I"` after the terminal sends a focus report.
- At head, `read.rs:100-104`: the `(_, true)` arm passes every raw piece through, focus reports included. The host never tracks the client's `?1004` (grep finds only the PREAMBLE).
- conhost `inputBuffer.cpp:585-599` (`WriteFocusEvent`): in VT mode it writes only what `_termInput.HandleFocus` returns, and that is empty unless the client enabled focus mode (GH#13238).
- Main is unchanged (read.rs:107-111).
- This is the bug conhost fixed. Every focus change injects `ESC [ I` or `ESC [ O` into VT-input apps.

## F7: real but partly right, P2 rather than P1. On main.
Executed at head and at main.
- **Controls in the echo: right.** Typing `\x01\x0e` in a cooked read echoes raw `\x01\x0e`. Under VT input, an Up arrow in a cooked read echoes raw `\x1b[A`, so the terminal's cursor moves.
  - Cause: `cooked.rs:78-81` pushes any char at or above the limit check through `push_units`. In VT mode `next_char` returns ESC as a plain character.
  - conhost `readDataCooked.cpp:1349-1366` renders `^X` (and Tab as spaces).
- **Backspace by UTF-16 unit: wrong.** `backspace` (`cooked.rs:96-110`) removes a surrogate pair whole, so it erases by code point.
- **Width: right.** Backspace always emits a single `\b \b`. Typing `中` then Backspace echoes `中\b \b`, so a wide character is not fully erased, and grapheme clusters are not handled.
- Main `cooked.rs` changed the UTF-8 assembly and added Escape stepping, but still echoes C0 and ESC raw and still erases with one `\b \b` per character. The probe gives the same output.
- Severity: P2. These are display artefacts in cooked echo. VT input combined with a cooked read is uncommon.

## F8: real but partly right, P2 rather than P1. On main.
- Executed at head and at main. Two cooked ReadConsoleW wait on the same handle, with capacities of 4 and 64 bytes. Input `abcdef\rxy\r` completes them with `"ab"` and `"xy\r\n"`. The next read on the handle waits: read A's rest `cdef\r\n` is gone.
- Cause: `finish_line` (read.rs:497-502) calls `keep_pending`, and `keep_pending` (read.rs:649-653) assigns `o.pending = rest` even when `rest` is empty.
- conhost compared:
  - `readDataCooked.cpp:811-816` calls `SavePendingInput` only when the input is non-empty.
  - `inputReadHandleData.cpp:33-41` then overwrites `_buffer`.
  - So conhost also loses A's rest when B has a non-empty rest of its own. The divergence is the empty-rest case, which is the case the probe hits.
- Main is unchanged (read.rs:565-571 and 755-759).
- Severity: P2. It needs two concurrent cooked reads on one handle.

## F10: real, P2 right. On main.
- Executed at head and at main: `ESC ] 52;c;` plus 5,000 × `Q` in one input, then `ZZZ ESC \` in the next. A raw read returns `"ZZZ"`, so the reply's tail is typed as text.
- At head, `input.rs:194-200`: past `MAX_PENDING` (4,096) the whole buffered rest becomes `Token::Unknown`. The decoder forgets it was inside a string, so the rest of the OSC parses as text and the `ESC \` parses as Alt+`\`.
- Main's `feed` (input.rs:183-209) has the same logic.
- §7.4 documents dropping a sequence over 4 KiB, but not typing its tail.

## F11: real, P2 right. On main.
- Executed at head and at main, with a non-VT client:
  - `CSI 1;5R` gives the records Ctrl down, F3 down with LEFT_CTRL, F3 up, Ctrl up (VK 0x11, 0x72, 0x72, 0x11).
  - `CSI 7;5R` gives nothing.
- At head, `input.rs:382-385` and `:421-425`: `cursor_vk('R')` is F3, and the key is accepted when the first parameter is empty or 1.
- conhost `InputStateMachineEngine.cpp:412-433` does not treat `CSI R` as F3 once it has seen win32-input-mode sequences, and the host enables that mode at startup.
- Narrow: the host strips CPR queries (§5.6), so a CPR only arrives if something else asked for one.

## F12: false_positive. Not applicable on main.
- The claim says synthesized text records with VK 0 and no key-up are an extra difference from conhost. They are not a difference.
- conhost's input `StateMachine::ProcessString` sends ground-state runs to `ActionPrintString` (`stateMachine.cpp:1995-2006`). That calls `InteractDispatch::WriteString`, then `InputBuffer::_writeString` (`inputBuffer.cpp:799-816`). There every unit becomes `SynthesizeKeyEvent(true, 1, 0, 0, wch, 0)`: a key-down, VK 0, scan 0, no key-up.
- `input.rs:508` (`Token::Text` to `KeyEvent::text`) matches this.
- V8 (§5.10, design.md:478) therefore does not understate anything here.

## F13: real, P2 right (a gap in the docs). On main.
- At head, `read.rs:105-110`: for a non-VT client, `key_events` turns `Token::Reply` and `Token::Unknown` into nothing. Executed by the existing test `replies_and_unknown_sequences_are_dropped_for_a_win32_client`.
- conhost passes these through:
  - Its CSI dispatch returns false for DA1 after the first one, for `$y` and for unknown sequences (`InputStateMachineEngine.cpp:482-526`).
  - OSC always returns false (`:579-589`), and a DCS is flushed at its ST (`:535-541`, `:316-321`).
  - `_SafeExecute` then calls `FlushToTerminal`, then `WriteStringRaw`, then `InputBuffer::WriteString` (`stateMachine.cpp:2171-2179`, `InteractDispatch.cpp:106-110`). So a non-VT client gets the reply as characters.
- §5.5's table does state "dropped" as our behaviour, so it is intended. But §5.10 does not list it as a divergence, which is exactly the claim.
- Main's §5.10 adds only V9 and V10.

## F14: fixed_since, P2 right. Not on main.
- Executed: at head the order is `[(10, C), (11, C)]`, oldest first, from `read.rs:165-167` (`sort_by_key(cookie)`).
- conhost `ProcessList.cpp:214-216` iterates `crbegin` to `crend`, newest first.
- Main sorts by `Reverse(cookie)` (read.rs:171-176), and its probe gives `[(11, C), (10, C)]`.

## F15: fixed_since, P2 right. Not on main.
- At head, `runtime.rs:687-701` `teardown` closes the server and the lanes but sends nothing to the terminal. `?1004l` and `?9001l` appear nowhere in `src/` at head.
- conhost `VtIo.cpp:250-262` (`Shutdown`) writes `\x1b[?1004l\x1b[?9001l`.
- Main adds `POSTAMBLE` (console.rs:39), pushes it in `teardown` (runtime.rs:869), and tests it (runtime.rs:972).

## F16: real, P2 right (the docs are wrong). On main.
- V7 (design.md:477) gives as its reason that "the fallback serves console-less callers; our clients always carry their group".
- conhost `ApiDispatchersInternal.cpp:76-92` shows otherwise. When no client has the group, it treats the group id as a pid. It looks up that process's parent, requires the parent to be a client of this console, and allocates process data for the group.
- That serves a `CREATE_NEW_PROCESS_GROUP` child whose id is the group id and which has not connected yet. The usual pattern is to spawn it and immediately send `GenerateConsoleCtrlEvent(CTRL_BREAK, child.pid)`. It is not about console-less callers.
- The code comment at `read.rs:252-261` describes the behaviour accurately. Only the stated reason is wrong.
- Main's V7 row is unchanged.

## F17: real, P2 right (a divergence not recorded). On main.
- Executed at head and at main: with reads waiting on the input handle and on a second input handle, CLOSE_OBJECT of the second ends only its own read (1 completion).
- Code: `console.rs:587-590` at head, `console.rs:681-684` at main: `end_reads(|r| r.object == p.object, ...)`.
- conhost `ObjectHandle.cpp:235-238`: if the closing handle has reads, `NotifyWaiters(true, HandleClosing)` runs on the whole input WaitQueue. Every cooked, raw and direct read returns STATUS_ALERTED on HandleClosing without checking its handle (`readDataCooked.cpp:234-239`, `readDataRaw.cpp:108-112`, `readDataDirect.cpp:90-94`).
- Our behaviour is arguably saner, but it is not recorded in §5.10. conhost's behaviour applies only when the closed handle itself had a waiting read.

## F18: real, P2 right. Partly on main: the Ctrl key-up half remains, the reply-cap half is fixed.
- **Ctrl+C:** executed. After `\x03`, one record stays queued, a key-up (`bKeyDown` = 0). The head test `ctrl_c_goes_to_every_client_and_ends_cooked_reads` asserts "Only the key-up is queued".
- **Ctrl+Break:** `intercept` (read.rs:129-149) acts only on key-down, so a Break key-up record is stored too.
- §5.5 (design.md:403-405) still says "Nothing queued" and "Never queued", at head and at main.
- **Reply cap:** the second half is F3. It is not implemented at head and is implemented on main, which also reworded the §7.4 row (design.md:618).

## F19: real, P2 right. Mostly on main.
- At head, these tests are missing:
  - a ChildExited-versus-pending-cooked-read interleaving with the §7.2 input-event invariant;
  - a second HangUp under that invariant;
  - Ctrl+C under VIRTUAL_TERMINAL_INPUT. The VT tests at read.rs:990-1000 never send `0x03`.
  - the §7.2 Windows test of the input event against PeekConsoleInput/ReadConsoleInput. `tests/own_pty.rs` has no such test, nor does `examples/conhost_probe.rs`.
- Partial coverage at head:
  - `the_input_event_always_matches_the_queue` covers one HangUp.
  - `the_last_disconnect_replies_then_closes_the_console` sends a HangUp after close, without the invariant.
- Main adds second HangUp and ChildExited tests (console.rs:1557-1572) and a HangUp between a packet and its completion (`a_hang_up_between_a_packet_and_its_completion_keeps_the_call`). Those check close behaviour, not the input-event invariant with a pending cooked read.
- Still missing on main: the ChildExited-versus-cooked-read invariant test, Ctrl+C under VT input, and the Windows input-event test.
