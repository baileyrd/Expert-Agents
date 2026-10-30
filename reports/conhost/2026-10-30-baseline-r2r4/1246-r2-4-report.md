<!-- conhost/1246-r2 run 4, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 325 s, 2123237 tokens, $1.89 -->

**Verdict: changes required.** I found two P2 defects, both new in the fix commit 8ec54b85. Two limits on this review: I couldn't read conhost's source, and the Windows-only code didn't run here.

- **Repository:** Rusty-Mill/rusty_mill, PR 1246
- **Base:** 66f4bc38c8181fd669c58f8e34df385a6afa895e
- **Reviewed head:** 8ec54b85a08dc6e0a57e2bafde91952c839dcf18. `git fetch origin pull/1246/head` gave the same SHA, and the working tree was at it.

## Prior findings at this head

- **K1 (P1, cooked echo bypasses backpressure): fixed in the core.** Echo is now checked against the 80 KiB limit before each character (`read.rs:485`), and Escape erases one character per step. A read that stops there holds back the reads behind it and GetConsoleInput (`read.rs:313`, `:407`, `:445`).
  - The output can go at most about 11 bytes past 80 KiB per finishing read: one character's echo plus the newline.
  - Held reads always resume. A read is only held when the queue is over 80 KiB, and the reader then signals a drain on every read until the queue is at or below 64 KiB (`runtime.rs:216`).
  - Taking the check out turns `cooked_echo_stays_within_the_host_reserve_and_resumes_on_drain` and the two other hold tests red (executed).
- **K2 (P2, trailing ESC at a slice boundary read as the Escape key): fixed in the core.** I checked the decoder by tracing the code and by mutation. The other half, the input lane's `writing` counter in `runtime.rs:141-170, 250-257`, is Windows-only and didn't compile here. By reading the code, each `write` adds one and takes one away on every path, including the error and empty-buffer ones.
- **K3 (P2, surrogate pairs converted one unit at a time): fixed** for cooked echo, ANSI raw reads, ANSI GetConsoleInput records and the conversion back to VT. There is a red test for each of those. The fix also brings in F1 and F2 below.

## Blocking findings

### F1 (P2): with echo off, a lone high surrogate is printed to the terminal as U+FFFD
- **Where:** `cooked.rs:143` (`Line::finish` calls `self.utf8.flush(echo)`) and `console/read.rs:533-536`.
- **Trigger:** a cooked read with ENABLE_ECHO_INPUT off, such as a password prompt. A high surrogate that never gets its low half is followed by Enter.
- **What happens (executed):**
  - `finish` flushes the waiting half into `newline`.
  - `finish_line` writes `newline` to the terminal even without echo, because it is meant to carry only the line's `\r\n`.
  - The terminal therefore gets `EF BF BD 0D 0A` instead of `0D 0A`.
- **Effect:** a no-echo prompt shows a character it should never show.
- **Fix:** only flush the surrogate when echo is on. One way is to pass `echoing` into `finish`, or to flush into the echo buffer in `attempt`, where it is already gated.
- **Regression test:** a core test. Set mode `LINE_INPUT | PROCESSED_INPUT`, start a waiting ReadConsoleW, send `\x1b[0;0;55357;1;0;1_` then `\r`, and assert the VT is exactly `\r\n`. I ran this probe and it fails at the head.

### F2 (P2): design §7.4 understates the ANSI record cache, and the new fixed stores aren't listed
- **Where:** `docs/projects/term/conhost/design.md:614` says "one character: 3 bytes, 2 records".
- **What happens (executed):** a surrogate pair now converts to 4 UTF-8 bytes. An ANSI GetConsoleInput for 1 record on 😀 returns 1 record and leaves **3** in `cached_records`.
- **Also missing:** §7.4 requires every store a PR adds to be named with its cap, and these new ones aren't: `held_unit`, `record_utf8`, `vt_utf8` (`console.rs:100-106`) and the ESC the decoder now keeps.
  - The memory involved is tiny, but the document is now wrong.
- **Fix:** change the row to "3 bytes, 3 records" and list the new one-unit stores. A core test should assert `cached_records.len() <= 3` after an ANSI read of 1 record on a pair.

## Design questions
- **Peek and the event count disagree while echo is held.** When a cooked read is held, a PeekConsoleInput or GetConsoleInput with NOWAIT from another client returns 0 records, while GetNumberOfConsoleInputEvents still reports N. Inside conhost the same stall would block the call instead. This is a side effect of the backpressure §7.4 chose, and §5.10 doesn't record it. A Windows probe would settle it: a stalled terminal reader, one client in a cooked read, and a second client calling `_kbhit` or PeekConsoleInputA, compared against ConPTY.

## Optional improvements
- **A later read can take a held half.** `held_unit` is shared by every reader. If one ANSI raw read is holding a high half and a second read arrives before the low half, the second read takes the high half and both get lone halves. Giving the held unit to the first waiting read would fix it.
- **Out-of-date doc comment.** The module comment in `read.rs` says waiting reads are retried "on every arrival of input". They are now also retried when the output drains.

## Checks performed
- `cargo test -p rusty_conhost` on Linux: 104 passed, 0 failed; `own_pty.rs` ran 0 tests (Windows-only).
- `cargo clippy -p rusty_conhost --all-targets -- -D warnings` on Linux: clean.
- `python3 .github/scripts/changes.py check`: "539 fragment(s) ok".
- **Mutation runs**, in a scratch copy of the head made with `git archive`, each removing one fix:
  - echo hold removed: 3 hold tests red;
  - the `echo_held` check removed from GetConsoleInput: `a_read_behind_held_echo…` red;
  - ANSI pairing removed: the two ANSI raw-read tests red;
  - trailing ESC always treated as Escape: 3 decoder tests and the fixture replay red.
- **Running the new tests at the base SHA isn't meaningful.** The whole input path is new in this PR, so at 66f4bc38 the tests don't compile. The mutation runs above stand in for that check.
- **Probes for F1 and F2:** both executed, with the results given above.

## Validation gaps
- **No conhost source.** Cloning microsoft/terminal at `09beddc6` was refused: the sandbox proxy denied github.com. Because of that:
  - I have no `path:line` conhost answers or conhost test citations for the APIs this PR touches: ReadConsole (cooked and raw), RAW_READ, GetConsoleInput, GetNumberOfInputEvents, FlushConsoleInputBuffer, GenerateConsoleCtrlEvent, and the VT and win32-input-mode decoder.
  - In particular I can't say whether conhost under ConPTY pairs surrogate halves in ANSI ReadConsoleInputA and ReadConsoleA, or converts each unit to U+FFFD on its own. If it does the latter, part of the K3 fix moves us away from conhost.
  - A probe would settle it: under ConPTY and under our host, send halves D83D and DE00 as separate win32-input-mode records, then call ReadConsoleInputA for 8 records and ReadConsoleA with a 2-byte buffer, and compare the bytes.
- **Windows-only code not compiled or run here.** That covers `runtime.rs`: the `writing` counter, the InputMore dispatch, and `input_ends_where_a_write_ended_not_where_the_engine_cut`. No Windows target is installed.
- **D4 run not checked.** I couldn't reach GitHub to confirm that a Windows `ci.yml` run passed at exactly 8ec54b85.
