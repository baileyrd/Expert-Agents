<!-- conhost/1246-r1 run 5, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 1280 s, 6266288 tokens, $3.86 -->

I've finished the review of PR 1246. The verdict is **changes required**: six blocking findings, three of them P1, all reproduced by probe tests I ran.

One limit up front: GitHub was blocked by the sandbox (403 on `github.com`), so I couldn't clone microsoft/terminal at `09beddc6`. Any conhost behaviour below is from memory and marked unverified. The parity axis is therefore incomplete; the robustness axis and the code-vs-design comparison are done.

## Pin
- **Repository:** Rusty-Mill/rusty_mill, PR #1246.
- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`.
- **Head:** `197fae02607fa8c4428614aef7a4e7ac1b5bca7f`. `git fetch origin pull/1246/head` gives the same SHA.

## Blocking findings

**1. P1: A paste of more than 4,096 characters loses its beginning, even when a read is waiting** (executed)
- **Where:** `src/console/read.rs:84-113`, `src/input.rs:608-615`.
- **Cause:** `terminal_input` turns the whole input slice (up to 16 KiB) into records before it serves any waiting read (`serve_reads` runs at line 113). The record queue drops its oldest entries once it holds 4,096.
- **Trace:** a cooked ReadConsoleW is waiting with a 32,000-byte buffer. Pasting `"a"×6000 + "\r"` makes the read return 4,096 units, not 6,002, and logs `InputDropped`. About 1,904 characters are silently lost, and the read gets the tail of the paste.
- **Who hits it:** pasting long text into cmd. PSReadLine reads one record per call, so all its records are queued before any is consumed. As far as I recall, conhost's input buffer has no cap, so the same paste works under ConPTY.
- **Why this isn't just the design's cap:** §6.1 justifies the cap as "about 1 s of fast typing or paste at 4K keys/s", but a paste arrives in one write. The design already has backpressure (the input lane blocks `write`), so this can be fixed without changing the cap.
- **Fix:** serve waiting reads while records are being produced, and take from the input lane only as many bytes as the queue has room for, so the writer blocks instead of input being dropped.
- **Regression test (core):** paste 6,000 characters plus CR into a waiting cooked read and expect the whole line.

**2. P1: The engine's 16 KiB input slice can end right after an ESC, which turns into a phantom Escape key** (executed)
- **Where:** `src/input.rs:237-242`, together with `sched.rs:83` and `runtime.rs:510-512`.
- **Cause:** the decoder assumes "keys come whole", but the engine cuts input at exactly 16,384 bytes. `Shared::write` (`runtime.rs:145-156`) also appends partially when the lane is nearly full.
- **Trace:** a backlog of Up keys (`ESC [ A`, 3 bytes each) is always cut after an ESC, because 16,384 mod 3 = 1. With "dir" typed at a waiting cooked read, the read returns `"[A\r\n"`, and the echo shows "dir" erased and `[A` typed.
- **Who hits it:** clients without VIRTUAL_TERMINAL_INPUT (cmd, PSReadLine). VT-input clients get the raw bytes through intact.
- **Fix:** the runtime tells the core whether more input is queued, and a trailing lone ESC is held back until the next slice. Only when the lane is empty does it become the Escape key.
- **Regression test:** feed the backlog cut at 16,384 bytes and expect "dir".

**3. P1: A later cooked read on the same handle wipes out a line's kept rest** (executed)
- **Where:** `src/console/read.rs:497-502` and `649-653`. The kept rest is only checked when a read starts (line 373).
- **Cause:** `finish_line` always calls `keep_pending`, which replaces the handle's kept rest, even with an empty one.
- **Trace:** two cooked reads wait on one handle, read A (4 bytes) and read B (64 bytes).
  - Input `abcdef\r`: A returns "ab" and "cdef\r\n" (6 units) is kept on the handle.
  - Input `x\r`: B returns `x\r\n`, and the kept rest drops to 0.
  - A third read waits instead of returning "cdef\r\n".
- Order is also wrong: B reads new input while A's rest is still kept. I couldn't check whether conhost allows two concurrent cooked reads.
- **Fix:** append to the kept rest rather than replace it, and have a waiting read take the kept rest first.
- **Regression test:** the trace above.

**4. P2: Surrogate pairs are converted one UTF-16 unit at a time** (executed)
- **Echo** (`cooked.rs:79-80`, `126-128`): typing 😀 at a cooked prompt echoes `EF BF BD EF BF BD` (two U+FFFD).
- **ANSI reads** (`read.rs:511-516`): RAW_READ of 😀 returns `[ef,bf,bd,ef,bf,bd]`.
- **ANSI GetConsoleInput** (`read.rs:582`): the same per-record conversion. Traced in the source, not executed.
- **Width** (traced in the source): Backspace always emits one `\b \b` (`cooked.rs:109`). After a wide CJK character this moves back one column instead of two.
- **Fix:** convert whole code points and erase by display width.
- **Tests:** a core echo test and a RAW_READ test with a non-BMP character.

**5. P2: ESC ESC followed by an arrow becomes a literal `[A`** (executed)
- **Where:** `input.rs:251`.
- **Trace:** `\x1b\x1b[A` decodes to Alt+Esc (which is then dropped) and then the text records `[` and `A`. That is wrong under either reading: Escape then Up, or Meta+Up.
- **Fix:** end the first ESC as the Escape key and parse again from the second ESC.

**6. P2: The design doc disagrees with the code, and tests the design requires are missing**
- **Doc mismatch:** §5.5 (`design.md:403`, `:405`) says Ctrl+C under PROCESSED_INPUT and Ctrl+Break are "never queued". The code queues the key-up record. The PR's own test asserts this ("Only the key-up is queued"), and the fixture shows `input-available true` after Ctrl+C. The code probably matches conhost, so the doc should be fixed.
- **Missing §7.4 test:** the stalled-reader test for "cooked echo under sustained terminal input" (high-water ≤ 144 KiB).
- **Missing §7.2 tests:**
  - the core interleavings "ChildExited racing a pending cooked read" and "a second HangUp";
  - the Windows Peek/ReadConsoleInput input-event loop;
  - hang_up while a ReadConsole is pending.

## Design question
- When no read is waiting (for example, the user pastes while a command runs), dropping records past 4,096 is what §6.1/§7.4 decide. It still loses pasted text. Consider blocking the input lane instead of dropping.

## Failure classes
- **Containment:** ruled out. Short descriptors, zero or odd capacities and bad initial data each fail only their own call.
- **Retained memory:** within the 1 MiB cap. A waiting cooked read reserves `max(initial, 8192) × 2` bytes plus its 20-byte struct; replies built from released lines are bounded by what was parked.
- **Amplification:** one 16 KiB slice of win32-input-mode Escape records produced 28,668 B of echo; the queue reached 110,588 B against the 147,456 B (144 KiB) cap. It holds only because finding 1's record cap drops input. If finding 1 is fixed, the echo bound needs its own test.
- **Boundaries:** findings 2, 4 and 5. Mid-UTF-8 splits and splits at the client's buffer size are handled correctly.
- **Custody:** ruled out. The ctrl worker stops when `CtrlSender` drops on every failure path; that test is Windows-only.

## Optional improvements
- `sync_input_available` ignores `cached_records`.
- A Chars read's `retained` counts initial data it never keeps.

## Checks
I built a small workspace (`rusty_conhost`, `platform`, `rusty_wire`) from `git archive` at each SHA, with rustc 1.98.1 on Linux and the Windows-only dependency removed.
- `cargo test -p rusty_conhost`: 93 passed at head, 51 at base.
- `cargo clippy -p rusty_conhost --all-targets -- -D warnings`: clean at head (Linux target only).
- `python3 .github/scripts/changes.py check`: "539 fragment(s) ok".
- The head fixtures fail on base code (`synthetic-apis.txt` at line 52, `synthetic-input.txt` at line 5), so they guard the change. The other new core tests depend on APIs that don't exist at base, so they can't compile there.
- My probes: 6 of 7 fail at head, as described above; the echo-amplification probe passes.
- I couldn't run the full-workspace `cargo test`: `~/.cargo` is read-only in the sandbox, so cargo can't take its package-cache lock.

## Validation gaps
- **Not run (need Windows):** `own_pty.rs`, the runtime ctrl-worker tests, and `key_for`.
- **Unchecked against conhost (no source):**
  - whether the CRLF is shown with echo off;
  - Ctrl+Break leaving GetConsoleInput waiting;
  - a waiting peek removing records;
  - STATUS_ALERTED when a handle closes.
- **Proposed probes** for `conhost_probe.rs` under ConPTY: ReadConsoleW with LINE_INPUT and ECHO off (check the VT output for `\r\n`); a 6,000-character paste into a waiting ReadConsoleW and into ReadConsoleInput; a pending GetConsoleInput interrupted by Ctrl+Break.

## Verdict
**Changes required.**
