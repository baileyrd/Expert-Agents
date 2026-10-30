# conhost/1246-r1 (generated variant): claim verification

I graded these runs against `bench/cases/conhost/1246-r1/answer_key.toml`. The code came from these checkouts:
- the head, `197fae02`;
- current main, `ebb92e28`;
- the later fix commits, used to find where a fix landed.

For the conhost source I fetched microsoft/terminal at `09beddc6` raw from GitHub. The files were `stateMachine.cpp`, `terminalInput.cpp`, `inputBuffer.cpp`, `readDataCooked.cpp`, `InteractDispatch.cpp`, `_stream.cpp`, `VtIo.cpp` and `ProcessList.cpp`.

Where a run makes the same claim that `reports/conhost/2026-10-30-baseline/verify.md` already verified, I reuse that verdict and say so.

## Key matches

| Key | Runs | Notes |
|---|---|---|
| K1 | 1, 2, 4 caught | Each names the cooked echo, pushed with no budget check (`read.rs:462-464`), going past 144 KiB in one call. Runs 3 and 5 only list the missing §7.4 cooked-echo test. Neither says that echo can exceed the cap, so I graded both as misses. |
| K2 | 1–5 caught | Each names the 16 KiB slice boundary right after ESC (`input.rs:238-242`) turning into Escape plus text. |
| K3 | none | No run's blocking findings mention per-unit surrogate conversion. Two runs come close outside their blocking findings, and I graded neither:<br>• Run 2, under Optional: a pair typed at the line cap is half kept.<br>• Run 5, under Optional: a lone surrogate becomes U+FFFD in `vt_for` and in ANSI records, which it blames on rusty_term. That is about lone halves, not about pairs converted one unit at a time. |

## Claims reused from the baseline (verdict unchanged)

- **Two cooked reads on one handle lose the kept rest.** Runs: r1 F1 (P0), r2 F7, r5 F4. Verdict: real, unchanged on main.
  - r2 and r5 use the empty-rest trace (baseline r5 F3), which is a clear divergence from conhost.
  - r1 uses two 2-byte buffers, so both rests are non-empty. That is the same as baseline r2 F6, and the same caveat applies: conhost's `SavePendingInput` also replaces the rest in that exact case. I graded it real to stay consistent with the baseline.
- **C0 controls echoed raw.** Runs: r1 F5, r2 F2, r3 F4, r4 F5. Real (baseline r3 F3). The Tab and width parts in r3 and r4 also hold (see Backspace width below).
- **Focus reports reach VT-input clients.** Runs: r2 F6, r3 F2, r5 F2. Real (baseline r2 F4). Run 4 raises it only as a design question, so it is not graded there.
- **Ctrl+C key-up queued against §5.5.** Run: r1 F8. Real (baseline r1 F4).
- **Missing §7.2/§7.4 tests.** Runs: r1 F11, r2 F12, r3 F5, r4 F7, r5 F7. Real (baseline).
  - I re-checked that design.md:569-574 lists "ChildExited racing a pending cooked read" and "a second HangUp".
  - I re-checked that design.md:643 lists the stalled-worker test.

## New claims

- **Ctrl+C in the same slice as typed text: the cancelled text survives.** Run: r1 F4. **Real; still on main.**
  - Probe: I put a temporary test in the `read.rs` tests module, ran it, then restored the file.
  - Result: with a cooked read waiting, feeding `del x\x03` as one event and then a new read plus `\r` returns `"del x\r\n"`. The same bytes as two events return `"\r\n"`. This held at the head and on main `ebb92e28`.
  - Cause: `terminal_input` decodes the whole slice, and `intercept` ends the reads on Ctrl+C. Only after that does it run `serve_reads`.
  - conhost: `_wakeupReadersImpl` (`inputBuffer.cpp:648-661`) wakes waiting readers on every write, so the read has already taken the text when Ctrl+C ends it.
- **Ctrl delivery oldest first.** Runs: r1 F7, r2 F8, r3 F3. **fixed_since.**
  - conhost walks the list newest first with `crbegin` (`ProcessList.cpp:214-216`).
  - Fixed in 1eb6ec680. Main sorts by `Reverse(cookie)` (`read.rs:193`).
- **Teardown does not undo the `?9001h ?1004h` preamble.** Runs: r1 F6, r2 F10, r5 F6. **fixed_since.**
  - conhost's `VtIo::Shutdown` sends `?1004l ?9001l` (`VtIo.cpp:245-260`).
  - Fixed in 1eb6ec680 by adding `POSTAMBLE` (main `console.rs:46`, `runtime.rs:870`).
- **`vt_for` gives the wrong VT: Backspace as 0x08, Shift+Tab as TAB, no DECCKM.** Runs: r2 F5, r4 F4, r5 F3. **Real; still on main** (`input.rs:562-569` still emits `k.ch`).
  - At the head, `vt_for` (`input.rs:544-552`) emits `k.ch` as it is.
  - conhost passes records for VT clients through `TerminalInput::HandleKey` (`inputBuffer.cpp:682-709`). That sends DEL for Backspace while DECBKM is reset, BS for Ctrl+Backspace, and CBT for Shift+Tab (`terminalInput.cpp:911-930`). It also honours CursorKey mode (`terminalInput.cpp:1063`).
- **Backspace echo ignores width and wrapping.** Runs: r2 F3, plus the width part of r4 F5 and r3 F4. **Real; still on main** (`cooked.rs:134`).
  - `backspace` always emits one `\b \b` (`cooked.rs:109`).
  - conhost lays the line out by column width and redraws it across rows (`readDataCooked.cpp`, `_layoutLine`/`_appendCUP`, 1235-1287).
- **Alt+[ / Alt+O wait for the next key in plain VT.** Run: r2 F9. **Real; still on main.**
  - The head's `csi` and `escape` return Incomplete (`input.rs:276, 292`).
  - conhost's input engine flushes a trailing run of at most 2 bytes that starts with ESC, as long as no win32-input-mode sequence has been seen (`stateMachine.cpp:2036-2072`).
  - On main, `end` only covers a lone ESC; `csi` still returns Incomplete (`input.rs:309`).
- **Plain-VT text becomes VK 0 key-downs with no key-up, and V8 doesn't say so.** Run: r2 F11. **Real; still on main** (`input.rs:525`).
  - `key_events` turns Text into `KeyEvent::text` (`input.rs:508, 86-95`).
  - conhost's `InteractDispatch::WriteString` uses `CharToKeyEvents` for non-VT clients (`InteractDispatch.cpp:93-101`).
  - V8 (`design.md:478`) records only "scan code 0".
- **An over-long unfinished sequence reaches VT clients.** Run: r1 F9. **Real; still on main.**
  - `feed` gives the sequence up as `Unknown` but keeps its raw bytes (`input.rs:194-199`). The VT arm stores the raw bytes of any piece (`read.rs:98-101`).
  - The §7.4 row (`design.md:611`) says such a sequence is dropped.
- **Appendix A still says the numbers are "pinned in PR 3".** Runs: r1 F10, r4 F8. **Real; still on main** (`design.md:999`).
  - `design.md:970` gives no numbers, while `msg.rs:67,73` defines 0x0100_0003 and 0x0200_0003.
- **No interactive fixture.** Run: r3 F6. **Real; still on main.**
  - `design.md:678` says interactive fixtures are added with the PR that makes them work, and `design.md:723` says PR 3 makes interactive cmd work.
  - The PR adds only `synthetic-input.txt`, and main has the same four fixtures.
- **The recorder keeps typed input, but §8.2 says the sanitiser scrubs user history.** Run: r4 F9. **Real; still on main.**
  - `fixture.rs:22-25` says "typed passwords included", against `design.md:676`.
  - On main, `design.md:683` and `fixture.rs:27` are unchanged.
- **Echo inserted inside a client's unfinished escape sequence.** Run: r4 F2. **design_question.**
  - The trace holds.
  - conhost at `09beddc6` behaves the same way. Client VT goes through `WriteCharsVT`, one state machine with passthrough (`_stream.cpp:405-421`), and the cooked echo is written through `WriteCharsVT` as well (`readDataCooked.cpp:1287`).
  - Main keeps the behaviour on purpose: `screen.rs:100-104` says "Whatever the client's unfinished sequence was, this ends it".
- **The Ctrl effect is emitted before the Complete of the read it ends.** Run: r4 F6. **design_question.** `design.md:560` says Ctrl effects go to a FIFO worker and "can reach the OS after later driver effects. That is intended".

## Judgement calls

- **r1 F1 (two reads, both rests non-empty):** graded real, following the baseline. A stricter grader could call it conhost parity.
- **r4 F2:** graded design_question rather than false_positive. The trace is correct; what it describes matches conhost and is the deliberate behaviour on main.
- **K1 in runs 3 and 5:** a missing-test item that names "cooked echo under sustained terminal input" without saying that echo exceeds the cap does not meet caught_if, so both are misses.
