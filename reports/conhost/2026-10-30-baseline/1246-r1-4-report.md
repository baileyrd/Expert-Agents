<!-- conhost/1246-r1 run 4, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 487 s, 3160718 tokens, $2.67 -->

I found 2 P1 defects and 2 P2 defects in PR 1246, so the verdict is **changes required**. Two things limited the review. github.com and crates.io were blocked, so I couldn't read the pinned conhost source. And the test suite only ran in a stripped-down copy of the crate, not the real workspace.

**Repository:** Rusty-Mill/rusty_mill · **PR:** 1246 · **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e` · **Reviewed head:** `197fae02607fa8c4428614aef7a4e7ac1b5bca7f`. `git fetch origin pull/1246/head` returned exactly the head SHA.

## Blocking findings

### F1 · P1 · Cooked-read echo breaks the 144 KiB output cap: one Escape key makes about 96 KiB of output
- **Where:** `src/console/read.rs:462-464` writes the echo straight into the output queue with no limit. `src/cooked.rs:87-94`: Escape erases the line one unit at a time, emitting 3 bytes (backspace, space, backspace) per UTF-16 unit, initial data included. `src/cooked.rs:42-50`: the line's limit grows to fit initial data of any length. `runtime.rs:608` adds the result to the shared queue unconditionally.
- **Trigger (executed):** a client sends a ReadConsoleW in line mode with the largest initial data one packet can carry, (65,536 − 8 − 20) / 2 = 32,754 units. The user then presses Escape once.
  - One read: 32,754 × 3 = **98,262 B** of output in one call. The engine still takes input while the queue holds 80 KiB, so the queue reaches 81,920 + 98,262 = 180,182 B against the 147,456 B cap.
  - Many reads: admission lets 16 such reads wait. One 1,056-byte input slice of Esc, Enter, Esc, Enter… in win32-input-mode produced **1,572,224 B** of output in a single call.
  - Both numbers come from probe tests I ran in a scratch copy.
- **Docs made false:** design §7.4 says the output queue "never exceeds 64 + 16 + 64 = 144 KiB" and that past 80 KiB "echo stops" (design.md:606 and 621).
- **Required correction:** limit echo where it is produced. Options:
  - send echo through the same 64 KiB slice-and-park path writes use, carrying the rest over;
  - make erasing a line cost a fixed number of bytes (move the cursor back, clear to end of line) instead of 3 bytes per unit;
  - stop reads from starting while the queue is above 80 KiB.
- **Regression test (core `Event` test):** a waiting cooked read with 32,754 initial units, the queue already holding 80 KiB, then one Escape key. Assert the queue's high-water mark is ≤ 144 KiB. Add the same check with 16 reads and Esc/Enter pairs.

### F2 · P1 · An ESC that ends the engine's 16 KiB input slice becomes a phantom Escape key, and the rest of the sequence is typed as text
- **Where:** `src/input.rs:237-242` treats a lone ESC at the end of the input as the Escape key, with the comment "keys come whole". But the engine cuts input at arbitrary bytes. `runtime.rs:510-512` takes at most `INPUT_SLICE` (16 KiB) per turn, and `Shared::write` (`runtime.rs:138-160`) splits a terminal write when the input lane is full. So keys do not arrive whole.
- **Trigger (executed):**
  1. A cooked read is waiting.
  2. The terminal sends a little over 16 KiB of win32-input-mode key records, with byte 16,383 being the ESC that starts a record.
  3. That ESC becomes an Escape key, which clears the typed line. The next slice's `[65;30;97;1;0;1_` is then typed in as text.
  4. The read returns `"[65;30;97;1;0;1_\r\n"` instead of what the user typed.
- **Who hits it:** any interactive cmd user, and any client using input records. The input lane holds more than 16 KiB whenever input stalls behind more than 80 KiB of output, or a large paste arrives. With win32-input-mode on, which the host itself requests, every record starts with ESC, so about 1 in 18 slice cuts lands on one.
- **Conhost:** I recall that conhost keeps an unfinished sequence once it has seen win32-input-mode input, rather than flushing ESC as a key (`InputStateMachineEngine` / `FlushAtEndOfString`). I could not confirm this because the source was unreachable. The defect stands on this crate's own invariant either way.
- **Required correction:** treat a trailing ESC as the Escape key only when the terminal has nothing more pending. For example, have `take_input` report whether bytes remain, keep the ESC for the next feed when they do, and flush it only on a turn that finds the input lane empty. Alternatively, cut slices before a trailing ESC or an unfinished sequence.
- **Regression test:** a core test that feeds a slice ending in ESC with "more follows", then the rest, and expects one decoded record and no Escape. Add a fixture step for the same split.

### F3 · P2 · The design and the code disagree about Ctrl+C
- **Where:** design §5.5 (design.md:403) says of Ctrl+C under PROCESSED_INPUT: "Nothing is queued."
- **What the code does:** it queues the Ctrl+C key-up record and turns the input-available event on. Both `ctrl_c_goes_to_every_client_and_ends_cooked_reads` ("Only the key-up is queued") and `synthetic-input.txt` (`expect input-available true` after `input 03`) assert this.
- **Correction:** state in the design that the key-up is queued (I believe that matches conhost's `HandleGenericKeyEvent`, which filters only key-downs), or change the code.

### F4 · P2 · A stalled-reader test the design requires is missing
- §7.4's list of stalled-reader tests includes "cooked echo under sustained terminal input", asserting a high-water mark ≤ 144 KiB. No such test exists; the only high-water assertions are the existing write tests at console.rs:1754 and 1766. This test would have caught F1.
- **Layer:** core `Event` test.

## Design questions (recorded behaviour that may hurt a real client)
1. **Focus reports reach every VT-input client.** The startup preamble always turns on focus reports (`?1004h`), and with VIRTUAL_TERMINAL_INPUT the focus sequences pass through raw (probe executed: the client read `"\x1b[I"`). That follows §5.5's "VT passed through raw" rule. As I recall, conhost (`InputBuffer::WriteFocusEvent`) forwards them only to a client that asked for `?1004h`. A client that relays raw input, such as OpenSSH's ssh.exe, would send them to a remote shell that never asked for them.
   - **Probe:** a VT-input client that logs raw reads, focus toggled, run under ConPTY and under our host.
2. **V7's reason may not hold.** The design says "our clients always carry their group". But conhost's fallback in `ServerGenerateConsoleCtrlEvent` may exist for a process created with CREATE_NEW_PROCESS_GROUP that hasn't connected yet. If so, Python's `Popen(creationflags=CREATE_NEW_PROCESS_GROUP)` followed at once by `send_signal(CTRL_BREAK_EVENT)` would get E_INVALIDARG here.
   - **Settle by:** the conhost source and a Windows probe of that race.

## Optional improvements
- An OSC or DCS reply longer than 4,096 bytes is dropped only up to the cap; its tail is decoded as typed keys for clients that use input records. A "discard until the terminator" state would drop it whole, as §7.4 describes.
- `keep_pending` overwrites a handle's leftover line text. Two cooked reads waiting on the same object could lose the first one's leftover.
- An ANSI read without LINE_INPUT converts one UTF-16 unit at a time, so a character outside the BMP comes back as two U+FFFD. This is probably the same in conhost (`ReadCharacterInput`); confirm it on Windows.

## Checks performed
- **Pin:** fetched head equals `197fae02…`. Pass.
- **`cargo test -p rusty_conhost` in the real workspace:** could not run. Workspace resolution fails offline because `boxcars` isn't cached, and crates.io is blocked by the sandbox.
- **Tests in a scratch workspace:** I copied `rusty_conhost`, `platform` and `rusty_wire` at each SHA with the repo's `Cargo.lock`. I dropped the Windows-only `platform-windows` dependency, which isn't compiled on Linux. Rust 1.98.1, the pinned version.
  - Head: **93 passed**, 0 failed.
  - Base: **51 passed**.
- **Clippy** (`--all-targets -D warnings`, head, Linux target, same scratch workspace): **pass**.
- **Change fragments** (`python3 .github/scripts/changes.py check`): **"539 fragment(s) ok"**.
- **Do the new tests guard anything?** The head fixtures fail at base (`synthetic-apis.txt` after line 52, `synthetic-input.txt` after line 5), so they guard the change. The new unit tests use APIs that don't exist at base, so they can't build there.
- **Probes (scratch only, executed):** the single-read and 16-read Escape echo (F1), the split at the 16 KiB slice (F2), and focus passthrough (design question 1).

## Validation gaps
- **conhost source and history:** cloning microsoft/terminal at `09beddc6` was blocked, so every conhost citation above is from memory and unverified. That covers Ctrl+Break flushing the queue, RAW_READ with a zero-size buffer returning STATUS_BUFFER_TOO_SMALL, the waiting-peek removal, focus handling, and V7.
- **Windows-only checks not run:** the interactive tests in `tests/own_pty.rs` (0 ran on Linux), the `key_for` test in `platform-windows`, the runtime tests under `cfg(windows)`, and clippy for the Windows target.
- **Required CI results:** not seen.

## Verdict: **changes required**
