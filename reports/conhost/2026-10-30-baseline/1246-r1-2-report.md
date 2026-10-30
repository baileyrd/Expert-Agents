<!-- conhost/1246-r1 run 2, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 1764 s, 6399391 tokens, $3.97 -->

# Review of Rusty-Mill/rusty_mill PR 1246: rusty_conhost console input (host PR 3)

- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Reviewed head:** `197fae02607fa8c4428614aef7a4e7ac1b5bca7f`. `git fetch origin pull/1246/head` returned this exact SHA.
- **Verdict: changes required.** There are four P1 and four P2 blocking defects, all confirmed by running probe tests. The parity half of the review is incomplete because the sandbox blocks github.com (details under validation gaps).

## Blocking findings

### F1 · P1 · Cooked-read echo can exceed the 144 KiB output cap
- **Where:** `src/console/read.rs:443-464` (the echo is pushed with no budget check), `src/cooked.rs:42-50` (the line limit is `initial.len().max(8192)`) and `src/cooked.rs:87-94` (Escape writes `\b \b` once per unit).
- **Trigger:** a client parks a cooked ReadConsoleW with the most initial data a 64 KiB packet carries (32,754 units), and the user presses Esc once.
- **What happens (executed):** a 16-byte Esc record produces 98,262 B of echo. The engine takes input only while the queue is ≤ 80 KiB, so the queue reaches 81,920 + 98,262 = 180,182 B, past the 147,456 B cap.
- **Worse case (executed):** the parked-payload rule admits 16 such reads (16 × 65,528 B). One 528-byte slice of (Esc, Enter) × 16 then echoes 1,572,224 B.
- **Second trigger (source-traced):** win32-input-mode repeat counts. One `a` record with Rc=65535 plus one Backspace record with Rc=65535, about 42 B together, echo 8,192 + 24,576 B. A 16 KiB slice of such pairs echoes megabytes.
- **Fix:** count echo where it is made. Stop taking records once the queue passes the 80 KiB + 16 KiB reserve, leave the rest queued, and resume them on `OutputDrained`. Also bound or charge the echo for erasing initial data.
- **Regression test:** a core test that parks the read at `held` = 80 KiB, feeds one Esc and asserts the high-water mark ≤ 144 KiB, plus the 16-read variant. §7.4 already requires a stalled-reader test for "cooked echo under sustained terminal input", and none exists (see F8).

### F2 · P1 · Read replies have no 1 MiB cap; repeat counts inflate them
- **Where:** `read.rs:357` (`capacity` is the client's `output_size`, never clamped), `read.rs:508-519` (`read_chars`), `input.rs:632-641` (`pop_stream` yields one character per repeat) and `input.rs:343` (Rc accepted up to 65535).
- **What happens (executed):** a raw ReadConsoleW with a 16 MiB buffer, then 16 records `ESC[65;30;97;1;0;65535_` (336 B in all). One reply comes back at 2,097,120 B, against §7.4's "the read's declared capacity, up to 1 MiB".
- **Also:** `serve_reads` (`read.rs:409-422`) builds every completed reply inside one `handle` call. That contradicts the same row's "built when it completes, one at a time".
- **Fix:** clamp `capacity` to 1 MiB, and either clamp or charge the repeat expansion. Then make the code and the §7.4 row agree.
- **Regression test:** the probe above, asserting the reply is ≤ 1 MiB.

### F3 · P1 · An Esc at the end of a 16 KiB input slice becomes the Escape key
- **Where:** `sched.rs:83` cuts each slice at `min(lane, 16 KiB)` whatever the bytes are. `runtime.rs:510-512` feeds that slice in. `input.rs:238-242` treats a trailing ESC as the Escape key ("keys come whole").
- **Why it's wrong:** the host makes this split itself, while the continuation is already sitting in its own input lane.
- **What happens (executed core trace):** the stream is `ab`, then a Left-arrow record, then Enter. Unsplit, cmd's read returns `"ab\r\n"`. Split after the arrow's ESC, it returns `"[37;75;0;1;0;1_\r\n"`: Esc wiped `ab`, and the rest of the record was typed as text.
- **Real trigger:** a stalled reader (queue > 80 KiB) while the user keeps typing. Under the win32-input-mode the host asks for at startup, about one byte in 17 is an ESC.
- **Fix:** never end a slice on a trailing ESC while the lane holds more bytes, or pass a "more follows" flag so the decoder keeps the ESC pending.
- **Regression test:** a core or `sched` test that splits a stream after ESC.

### F4 · P1 · Focus reports reach VT-input clients that never asked for them
- **Where:** `console.rs:35` (the host's own `CSI ?1004h` preamble) and `read.rs:100-104` (in VT mode, `Token::Focus` passes through raw).
- **What happens (executed):** a client with VIRTUAL_TERMINAL_INPUT that never wrote `?1004h` reads `"\x1b[O\x1b[I"` on every focus change.
- **Who breaks:** WSL shells (`wsl.exe` uses VT input; `rt-caps.sh` in WSL is on the manual gate) and libuv/node raw-VT apps. They show stray `[I`/`[O`.
- **Not recorded:** §5.5 doesn't decide this case.
- **Parity, unverified:** from memory, conhost's `InputBuffer::WriteFocusEvent` → `TerminalInput::HandleFocus` sends focus only when the client itself enabled the mode. I couldn't check this; see gaps.
- **Fix:** drop focus pieces for VT clients until the host tracks the client's own `?1004`.
- **Regression test:** feed `Input("\x1b[I")` in VT mode and assert nothing is queued.

### F5 · P2 · Surrogate pairs become two U+FFFD for VT-input clients
- **Where:** `input.rs:550` (`vt_for` converts each UTF-16 unit separately) and `read.rs:118-128`.
- **What happens (executed):** two win32 records carrying U+D83D and U+DE00 make a VT client read `[fffd, fffd]`. A non-VT client gets 😀 correctly.
- **Who breaks:** edit.exe when the terminal sends pair records, as Windows Terminal does. rusty_term sends only the lead unit, which is a separate bug of its own.
- **Fix:** hold a pending lead surrogate in `key()` and emit the pair together.
- **Regression test:** the probe above, expecting `"😀"`.

### F6 · P2 · A second cooked read overwrites the first read's leftover text
- **Where:** `read.rs:497-502` and `649-653`. `keep_pending` replaces whatever the handle already holds.
- **What happens (executed):** two cooked reads wait on one handle with 2-unit buffers, and the user types `abcdef\r` then `ghijkl\r`. The reads complete `"ab"` and `"gh"`, and the next read gets `"ijkl\r\n"`. The rest of the first line, `"cdef\r\n"`, is lost.
- **Fix:** append to the handle's leftover text, or serve it before taking new input.
- **Regression test:** the probe above.

### F7 · P2 · The design doc disagrees with the code on Ctrl+C and Ctrl+Break
- **Where:** `design.md:403` says Ctrl+C means "Nothing is queued", and `design.md:405` says Ctrl+Break is "Never queued".
- **What the code does:** `read.rs:141` stores the key-up record. The PR's own test `ctrl_c_goes_to_every_client_and_ends_cooked_reads` asserts it ("Only the key-up is queued").
- **Fix:** settle which matches conhost and correct the doc or the code. The existing test then guards it.

### F8 · P2 · Required tests are missing
These tests are required by the design but absent:
- **§7.4:** the stalled-reader test for cooked echo (core). It would catch F1.
- **§7.2 core:** "ChildExited racing a pending cooked read".
- **§7.2 Windows:** the PeekConsoleInput/ReadConsoleInput loop asserting the input event matches the queue.
- **§8.1 parity probes:** `examples/conhost_probe.rs` adds no input subcommands. Uncovered: GetConsoleInput, GetNumberOfInputEvents, the status a cooked read returns after Ctrl+C, GenerateConsoleCtrlEvent, and RAW_READ with an output size of 0 → `c0000023`.

## Design questions
- **Ctrl+C during a cooked read:** the read completes with STATUS_ALERTED (0x101) and no `^C` echo. §7.4 lists `^C` as host-originated output. Whether kernelbase/cmd expect the host to echo it needs the probe below.

## Optional improvements
- In `input.rs:225-228`, an invalid byte earlier in a text run turns a UTF-8 character that is still arriving at the slice end into U+FFFD.
- `Line::reserved` under-counts by up to 6 B (the wake-up unit plus CR LF past the limit).
- In plain-VT mode, Alt+] or Alt+P sends `ESC ]`/`ESC P`, which swallows up to 4 KiB of later typing. Needs a conhost comparison.

## Checks performed
I ran these on Linux in a minimal scratch workspace at the head, using the pinned 1.98.1 toolchain. The full workspace couldn't resolve dependencies offline because crates.io is denied.

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` | 93 passed, 0 failed |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` | clean (Linux cfg only) |
| `python3 .github/scripts/changes.py check` | "539 fragment(s) ok", exit 0 |
| Head's fixtures against the base crate | `synthetic-input.txt` fails at line 5, so it is red without the change |
| Seven probe tests (scratch only, nothing written to the reviewed tree) | reproduced F1–F6 |

Other results:
- The new unit tests live in modules this PR adds, so none of them can pass at base.
- `VkKeyScanW` is already admitted (§11.1).
- `THIRD_PARTY_NOTICES.md` lists every ported file, and each carries the MIT notice.
- The scope matches the amended §10 row 3.

## Validation gaps
- **No conhost source.** The sandbox denies github.com, so I couldn't clone microsoft/terminal at `09beddc6`. No API in this diff has a verified `path:line` citation. That covers ReadConsole (cooked and raw), RAW_READ, RAW_FLUSH, GetConsoleInput, GetNumberOfInputEvents, FlushConsoleInputBuffer, GenerateConsoleCtrlEvent, Ctrl+C/Ctrl+Break handling, the lone-ESC rule, focus handling and surrogate handling. The parity review needs redoing with source access.
- **No Windows checks.** I couldn't see the PR body, the D4 `workflow_dispatch` run at `197fae02`, Windows clippy, or the `own_pty`/runtime tests (api.github.com is denied too).
- **Probes to propose,** each run under ConPTY and under our host:
  - a cooked ReadConsoleW interrupted by Ctrl+C: status, bytes and `^C` echo;
  - focus in and out with a VT-input client that never enabled `?1004h`;
  - pair records sent to a VT-input reader;
  - a 20 KiB backlog of win32 records while output is stalled;
  - PeekConsoleInput/GetNumberOfInputEvents after Ctrl+C: is the key-up queued?
