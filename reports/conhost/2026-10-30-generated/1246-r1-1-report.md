<!-- conhost/1246-r1 run 1, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 591 s, 5081365 tokens, $3.59 -->

# Review of PR 1246: rusty_conhost console input (host PR 3)

- **Repository:** Rusty-Mill/rusty_mill, PR **1246**
- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Head:** `197fae02607fa8c4428614aef7a4e7ac1b5bca7f`. The fetched `pull/1246/head` matches this SHA.

**Verdict: changes required.** I found one P0, five P1 and five P2 defects. I confirmed the P0, three of the P1s and two of the P2s by running probes; the rest come from reading the code.

**Two process notes:**
- **No conhost source.** GitHub egress is blocked in this session, so I couldn't clone microsoft/terminal at `09beddc6`. Where a finding depends on what conhost does, that comes from memory and is listed under validation gaps with a probe that would settle it.
- **Shared scratch directory.** Another process is writing into `scratch/`. It added its own probe tests to my first extracted copy of the head, so I threw that test run away. Every result below comes from a private directory (`scratch/rv-NykEdG`), whose crate and platform-windows files I checked against the git blobs. I wrote and ran all probes myself and did not use the other process's results.

## Blocking findings

### P0: Two cooked reads on one handle lose a line's leftover text
- **Where:** `src/console/read.rs:497-502` and `649-653`.
- **Trigger:** two cooked reads waiting on the same input handle, each with a buffer smaller than its line.
- **Trace (executed, RV10):**
  1. ReadConsoleW A, then B, on handle H, each with 2 bytes of room. Both wait.
  2. The terminal sends `abc\rdef\r`.
  3. A returns `"a"` and keeps the rest `"bc\r\n"` on H.
  4. B is retried in the same `serve_reads` pass. It reads `def\r` from the queue, returns `"d"`, and `keep_pending` replaces H's kept text with `"ef\r\n"`.
  5. Later reads return only `"ef\r\n"`. `"bc\r\n"` is gone without any log line. The cap check at line 498 counts H's old text as still held even though it is about to be overwritten.
- **Fix:** add to the handle's kept text instead of replacing it, and charge the addition against `MAX_PENDING_UNITS`. Alternatively, have a waiting read take the handle's kept text first.
- **Regression test:** the RV10 sequence; asserting that `"bc\r\n"` comes back.
- If the Windows probe shows conhost loses this text too, record it as a divergence in §5.10 instead.

### P1: Cooked-read echo ignores the output caps, so the queue grows without bound
- **Where:** `src/console/read.rs:462-464` and `492-494`, `src/cooked.rs:87-94` and `109`.
- **Design:** §7.4 (`design.md:606-607`) gives host output a 16 KiB reserve and caps one emission step at 64 KiB, for a total of 144 KiB. Escape and Backspace echo `\b \b` for every unit, including ReadConsole's initial data, and nothing checks either cap. ReadConsole packets are never held back for a full queue.
- **Executed:**
  - **RV1:** a ReadConsoleW with 32,000 initial units waits; the queue holds 80 KiB; one Escape key arrives. That echoes 96,000 bytes in one step, and the high-water mark reaches 177,920 bytes against the 147,456-byte cap.
  - **RV1b:** an Escape is already queued and the queue holds 128 KiB because the reader has stalled. A ReadConsoleW packet then pushes the queue to 227,072 bytes.
  - **RV9:** 1,000 queued Escape+Enter pairs, then 1,000 ReadConsoleW calls with initial data, with the reader stalled. The output queue reaches **96,002,000 bytes**.
- **Fix:** charge echo to the host reserve and pause the read when the reserve is full, keeping its progress the way a parked write keeps its slice. Or bound echo per step and fall back to a redraw.
- **Regression test:** the RV1, RV1b and RV9 sequences in a core test, asserting a high-water mark of at most 144 KiB.

### P1: An Escape at the end of an input slice becomes the Escape key
- **Where:** `src/input.rs:238-242`, together with `src/runtime.rs:510-512` and `sched.rs:14`.
- **Cause:** the engine takes at most 16 KiB of input per turn and cuts at any byte. The decoder treats an Escape byte that ends the buffer as the Escape key, on the comment's assumption that "keys come whole". That holds for the terminal's writes, but not for the engine's slices.
- **Executed (RV2), with a cooked read waiting:**
  - `ab\x1b[A\r` in one slice reads as `"ab\r\n"`.
  - Split as `ab\x1b` | `[A\r`, the line is erased (`ab\b \b\b \b` echoed) and the read returns `"[A\r\n"`.
  - In win32-input-mode, a cut just after a record's Escape erases `a` and types the literal text `[66;48;98;1;0;1_`.
- **Reached by:** any time the input lane holds more than 16 KiB, for example keys typed while input is paused above 80 KiB of output.
- **Fix:** keep a trailing Escape pending while the lane still holds bytes, so it is only decided once the lane is drained, or end slices on a sequence boundary.
- **Regression test:** the same bytes fed in one event and split at every offset must give the same records.

### P1: Ctrl+C in the same slice as typed text lets the cancelled text reach the next read
- **Where:** `src/console/read.rs:91-113` and `147-150`.
- **Cause:** waiting reads are retried only after the whole slice has been decoded, so Ctrl+C ends the cooked read before that read has taken the text in front of it.
- **Executed (RV3b):** `del x`, then `\x03` as separate events: the next line is `"\r\n"`. The same bytes as one event `del x\x03`: the next line is **`"del x\r\n"`**, so the cancelled command runs on the next Enter.
- This breaks the rule that bytes must not mean different things depending on slice boundaries. My understanding is that conhost wakes readers after every key it writes, which would discard the text; that is from memory (see gaps).
- **Fix:** call `serve_reads` before `intercept` acts on a Ctrl+C or Ctrl+Break.
- **Regression test:** the one-slice case asserts the queue is empty after Ctrl+C.

### P1: Cooked echo sends raw control characters to the terminal
- **Where:** `src/cooked.rs:78-81` and `126-128`, `src/console/read.rs:530-536`.
- **Executed (RV5):** typing Ctrl+N in plain VT, then Ctrl+[ followed by `c` in win32-input-mode, sends bytes `[14, 27, 99]` to the terminal. That is SO (switch to the G1 character set), then `ESC c`, which is RIS: a full reset that also turns off the `?9001`/`?1004` modes this PR enables.
- **Reached by:** cmd at its prompt.
- **Fix:** echo control characters as `^X`, which is how I remember conhost showing them (unconfirmed here).
- **Regression test:** the RV5 bytes must not put any byte below 0x20 other than `\b`, `\r`, `\n` or `\t` into the echo.

### P1: Teardown never undoes the startup preamble
- **Where:** `src/runtime.rs:474` sends `\x1b[?9001h\x1b[?1004h`. `teardown` (`runtime.rs:687-700`), `close` and `die` send nothing, and the crate contains no `?9001l` or `?1004l` (source-traced).
- **Impact:** any terminal or mux pane that outlives the console keeps sending win32-input-mode records and focus reports to whatever reads it next.
- **Fix:** queue `\x1b[?9001l\x1b[?1004l` before `Eof` on every teardown path, including after a panic.
- **Regression test:** a Windows test in `own_pty` that reads the stream to EOF and finds the reset.

### P2: Ctrl events go to the oldest client first
- **Where:** `src/console/read.rs:164-169` sorts recipients by ascending cookie, and cookies only increase (`console.rs:614-617`). The PR's own test asserts this order, `[(10, C), (11, C)]` at `read.rs:925`.
- The brief's checklist and my memory of conhost's `ProcessList.cpp` say newest first. This isn't recorded in §5.10.
- **Fix:** sort descending and update the test.

### P2: The Ctrl+C key-up is queued, which contradicts §5.5
- **Where:** `design.md:403` says "Nothing is queued." The code queues the key-up and raises the input event (`input.rs:477-480`, `read.rs:140-150`).
- **Executed (RV8):** the effects are `[Ctrl{1,C}, SetInputAvailable(true)]`, and one record is queued. The PR's test asserts this too (`read.rs:928-929`).
- **Fix:** change either the code or §5.5 so they agree.

### P2: An over-long unfinished sequence reaches VT-input clients
- **Where:** `src/input.rs:194-199` together with `read.rs:100-104`. The §7.4 row (`design.md:611`) says such a sequence is "given up as an unknown sequence and dropped".
- **Executed (RV7):** an unterminated 5,007-byte OSC fills the queue to 4,096 records for a VT-input client, but 0 for a win32 client.
- **Fix:** drop the `Unknown` piece produced by the 4,096-byte cap in VT mode as well.

### P2: Appendix A still says these API numbers will be pinned in PR 3
- **Where:** `design.md:970`. The code uses `0x01000003` and `0x02000003` (`msg.rs`), but Appendix A doesn't list them.

### P2: Missing tests for changed behaviour
- **Parity probes:** §8.1 (`design.md:654`) says each API PR adds parity probes. `examples/conhost_probe.rs` is unchanged, so GetConsoleInput, ReadConsole, RAW_READ, GetNumberOfInputEvents, FlushConsoleInputBuffer and GenerateConsoleCtrlEvent have none.
- **§7.2 Windows test:** the Peek/ReadConsoleInput loop that checks the input event at quiescence doesn't exist.
- **Preamble:** no test checks that it is emitted.
- **Ctrl worker:** there's no §7.4 stalled-worker test showing the engine keeps serving packets while `send_ctrl` blocks.

## Design question
- §7.4 counts waiting reads against the 64 waiting calls. With 64 reads waiting, no READ_IO is sent. That includes a killed client's DISCONNECT, so every client is stuck until terminal input completes one of those reads. This is a design decision, but it lets a single client stall all the others.

## Optional
- A non-line read still charges `initial.len()*2` to the parked total, even though it drops the initial data (`read.rs:367`).

## Checks run
| Check | Result |
|---|---|
| `cargo test --offline -p rusty_conhost` (Linux, verified head copy) | 93 passed; `own_pty` has 0 tests on Linux |
| `cargo clippy --offline -p rusty_conhost --all-targets -- -D warnings` (Linux) | exit 0 |
| `python3 .github/scripts/changes.py check` | 539 fragments ok |
| Review probes RV1–RV10 (private copy; the only change to the harness was making `queued`/`high_water` visible to the probe module) | results as quoted above |

The PR's new tests can't pass at base: there `Event::Input` carries no bytes, the `read` module doesn't exist, and `RAW_FLUSH` always succeeds, which the modified `raw_write_passes_bytes_through` now rejects. I worked this out from the code rather than compiling them at base.

## Validation gaps
- **Windows:**
  - The `own_pty` tests (`interactive_cmd_runs_typed_lines`, `ctrl_c_stops_the_running_command_not_the_shell`) didn't run.
  - Windows clippy (`runtime.rs`, `condrv::key_for`) didn't run either, because no Windows target is installed.
  - I couldn't see the D4 Windows CI run at the head commit.
- **conhost at `09beddc6`:** these are unconfirmed from source. Each can be settled by a `conhost_probe` run under ConPTY and under our host:
  - Ctrl delivery order, newest first (`ProcessList.cpp`).
  - Whether VtIo resets `?9001`/`?1004` on exit.
  - `^X` echo of control characters (`readDataCooked.cpp`).
  - Whether readers wake on every key (`inputBuffer.cpp`).
  - Whether the Ctrl+C key-up is queued (`input.cpp`).
  - How conhost handles two cooked reads on one handle.
