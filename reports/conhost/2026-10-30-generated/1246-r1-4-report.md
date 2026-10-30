<!-- conhost/1246-r1 run 4, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 682 s, 4616307 tokens, $3.70 -->

# Review of PR #1246 (rusty_conhost: console input, host PR 3)

- **Repository:** Rusty-Mill/rusty_mill, PR #1246
- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Reviewed head:** `197fae02607fa8c4428614aef7a4e7ac1b5bca7f` (fetched `pull/1246/head`; it matches)
- **Verdict: changes required.** There are four P1 and five P2 findings.

I couldn't check conhost's source. The sandbox denied github.com, so I couldn't clone microsoft/terminal at `09beddc6`. There is a copy under another project's temp directory (`dev-Expert-Agents/.../grade-conhost`), but it isn't part of this task, so I didn't use it. Where a finding depends on conhost's behaviour, that behaviour is from memory and is marked as needing a check. Each finding below also stands on in-repo evidence or a run probe.

## Blocking findings

### 1. P1: one Esc key can echo about 96 KB in a single call, breaking the §7.4 output limits
- **Where:** `src/cooked.rs:87-94` and `:109`, `src/console/read.rs:449-464`.
- **What happens:**
  - A cooked read's line limit is its initial data when that is longer than 8,192 units (`cooked.rs:44`). The payload allows about 32,000 units.
  - Esc deletes the whole line, writing `\b \b` (3 bytes) for each unit.
  - Nothing counts or limits this echo: `OutputQueue::push` has no bound.
- **Trigger:** any client sends ReadConsoleW with 64,000 bytes of initial data, then the user presses Esc.
- **Executed probe:**
  - One Esc produced **96,000 bytes** of echo in one `handle` call.
  - With 4 such reads waiting, a 132-byte input slice (Enter, Esc, repeated) produced **288,008 bytes**.
  - Admission allows about 15 such reads, so roughly 1.4 MiB can come from one call.
- **Design limits this breaks:**
  - §7.4: "One emission step | 64 KiB".
  - §7.4: "the output queue therefore never exceeds 64 + 16 + 64 = 144 KiB". Input is admitted at 80 KiB held, so one Esc reaches about 176 KiB.
- **Fix:** count echo against the host reserve while it is produced. When the reserve is used up, stop taking records for the cooked read and call `serve_reads` again on `OutputDrained`. Alternatively, make erasing a line cost a bounded number of bytes.
- **Regression test:** the §7.4 stalled-reader test for cooked echo, which is missing (see finding 7). Use reads with maximum initial data plus Esc, and assert `high_water ≤ 144 KiB`.

### 2. P1: cooked echo is inserted inside a client's unfinished escape sequence
- **Where:** `src/console/read.rs:462-463` and `:493`.
- **What happens:** echo goes straight into the stream. Nothing tracks whether the client's last write ended partway through a sequence. Echo is new in this PR; before it, the host only added the start-up preamble.
- **Executed probe:**
  1. RAW_WRITE `ESC[3`
  2. user types `a` into a waiting cooked read
  3. RAW_WRITE `1mred`
  - The terminal receives `ESC[3a1mred`. That is HPR 3 (move the cursor forward 3 columns), followed by `1mred` printed as plain text.
- **Real trigger:** a background writer (for example a `start /b` job, or any CRT program using WriteFile/RAW_WRITE) flushes a buffer in the middle of a sequence while cmd waits at its prompt.
- **What it breaks:** the stream-fidelity rule that a client's bytes reach the terminal as the shared parser would read them, whatever the write boundaries.
- **Fix:** keep host-originated bytes out of an unfinished client sequence. For example, track the client stream's parser state and hold echo while it isn't in ground state.
- **Regression test:** the probe above, as a core test asserting the stream equals `ESC[31mred` followed by the echo.

### 3. P1: an ESC at the end of a 16 KiB input slice becomes the Escape key, and the rest of the record is typed as text
- **Where:**
  - `src/input.rs:240-242` treats a lone ESC at the end of a feed as the Escape key ("keys come whole").
  - `runtime.rs` `take_input` (`:236-242`) and `OwnPtyMaster::write` (`:138-157`) split input at arbitrary bytes.
  - `sched.rs:14` sets `INPUT_SLICE` to 16 KiB.
- **Trigger:** more than 16 KiB of win32-input-mode records pile up in the input lane, and a slice ends right after an ESC. Records pile up while input is paused because output is past 80 KiB and the user keeps typing. Each record is about 20 bytes and starts with ESC.
- **Executed probe:** with a cooked line holding `a`, feeding `ESC` and then `[66;48;98;1;0;1_` erases the `a`. The line returned is `"[66;48;98;1;0;1_\r\n"`, so the next Enter runs that junk as a command.
- **Fix:** keep a trailing ESC pending. Treat it as the Escape key only when the lane is empty after the turn: pass an "input lane drained" flag with `Event::Input`, or flush on an idle turn.
- **Regression test:** a decoder or core test that feeds a record split right after its ESC, and asserts one Key record and no Escape key.

### 4. P1: VT-input clients get Backspace as 0x08 and Ctrl+Backspace as 0x7f
- **Where:** `src/input.rs:544-553`. `vt_for` passes `uChar` through unchanged.
- **Executed probe:** with VT input on, Backspace (`vk 8, ch 8`) and Ctrl+Backspace (`ch 0x7f`) records reach the client as `[08, 7f]`.
- **In-repo comparison:**
  - rusty_term's own encoder sends DEL for Backspace (`rusty_term/src/gui/input.rs:246`).
  - It sends a record with uChar 0x08 only in win32-input-mode (`:513`).
  - So the host's own `?9001h` preamble changes the byte a VT client receives for Backspace.
- **conhost (from memory, not checked):** `TerminalInput` sends DEL for Backspace and BS for Ctrl+Backspace.
- **Who breaks:** VT-input editors such as microsoft/edit, where Backspace would act as Ctrl+Backspace.
- **Related:** `vt_for` also ignores DECCKM (the client's `?1h`). The same root cause applies, though that one is less clearly harmful.
- **Fix:** map VK_BACK to 0x7f, and to 0x08 with Ctrl, as TerminalInput does.
- **Regression test:** extend `vt_for_turns_records_back_into_vt`.

### 5. P2: echo writes typed C0 controls raw, and Backspace erases one column whatever the character's width
- **Where:** `src/cooked.rs:79-80` and `:109`.
- **Executed probe:** typing Ctrl+N then Ctrl+G sends raw `0e 07` to the terminal.
  - In the shared parser, SO sets GL to G1 (`rusty_term/core/src/parser.rs:1039`) and BEL rings the bell (`:1010`).
- **conhost (from memory, not checked):** echoes these as `^N` and `^G`.
- **Display effects:**
  - Backspace over a `^X` or a double-width character erases the wrong columns.
  - Backspace at a wrapped edge doesn't move up a row.
- **Fix:**
  - Echo C0 controls as `^X`.
  - Make Backspace erase as many columns as the character occupied.
- **Regression test:** a core test asserting the echo bytes for Ctrl+A and for a CJK character followed by Backspace.

### 6. P2: a Ctrl event is queued before the read it ends is completed
- **Where:** `src/console/read.rs:148-149` (and `:153-155`): `ctrl_event` runs before `terminate_reads`.
- **What happens:**
  - The effects come out as `[Ctrl, Complete(STATUS_ALERTED)]`.
  - The engine pushes the Ctrl event to the worker, which can call `ConsoleControl` on its own thread before `COMPLETE_IO` runs.
- **conhost (from memory, not checked):** `HandleCtrlEvent` only sets `CtrlFlags`, and delivery happens at `UnlockConsole`, after `TerminateRead` has completed the read.
- **Rule broken:** a reply goes before the effects it carries.
- **Fix:** run `terminate_reads` first.
- **Regression test:** assert the effect order in `ctrl_c_goes_to_every_client_and_ends_cooked_reads`.

### 7. P2: tests the design requires for input are missing
- §7.4 lists a stalled-reader test for "cooked echo under sustained terminal input". None exists, and it would have caught finding 1.
- §7.2's core interleavings are incomplete: "ChildExited racing a pending cooked read" and "a second HangUp" are missing. `the_input_event_always_matches_the_queue` covers only one HangUp.
- §7.4's "repeated Ctrl with a stalled worker" exists only as a test of the queue alone (`a_full_ctrl_queue_drops_new_events`). There is no engine-level version.
- §7.2's Windows test (a probe looping Peek/ReadConsoleInput, checking the input event matches the queue) is missing.

### 8. P2: Appendix A was not updated
- `design.md:970` still says the numbers for GetNumberOfConsoleInputEvents and FlushConsoleInputBuffer are "pinned in PR 3", but this PR doesn't pin them.
- The values in the code, `0x01000003` and `0x02000003`, look right. The doc just needs them.

### 9. P2: the recorder keeps typed input, which the design says the sanitiser scrubs
- §8.2 (`design.md:676`) says "A sanitiser scrubs CONNECT fields, paths, environment and user history."
- The recorder now writes terminal input raw, and `fixture.rs:24` says so: "typed passwords included".
- Either scrub typed input, or change §8.2 to state the new rule.

## Design questions
- **Focus reports to VT-input clients that never asked for them.** The preamble turns on `?1004`, and §5.5's "VT … passed through raw" row then passes `CSI I` / `CSI O` to every VT-input client, including those that never wrote `?1004h` themselves. conhost (from memory) sends them only when the client asked. A raw-mode client would show `^[[I` on every focus change. Should the host forward focus reports only to clients that requested them?
- **No Ctrl+S pause.** Under LINE_INPUT, conhost's `IsPauseKey` treats Ctrl+S as a pause key. This PR doesn't, and §5.10 doesn't record it as a divergence.

## Optional improvements
- After `close()`, `sync_input_available` emits `SetInputAvailable(false)` after `CloseServer`, so the engine logs "dropped an effect after CloseServer".
- A `Chars` read's `retained` counts `initial` bytes it doesn't keep. A wakeup character can push the line 1 unit past `limit`, and `finish` adds 2 more, so the line can exceed its reservation by 4 bytes.
- `read_records` gives cached ANSI byte records to a later Unicode read, whereas `consume_cached` drops cached bytes for Unicode reads.
- In non-win32 VT input, an `ESC O` or `ESC [` at the end of input waits for the next key and can swallow it.

## Checks
- `cargo test -p rusty_conhost` (Linux): **93 passed**, 0 failed. `tests/own_pty.rs` is Windows-only, so it ran 0 tests.
- `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux): clean.
- `python3 .github/scripts/changes.py check`: "539 fragment(s) ok".
- Probes in a scratch copy of the head tree (`scratch/head`, not the reviewed checkout), all executed:
  - Esc echo size (finding 1)
  - echo inside a client's sequence (finding 2)
  - record split after ESC (finding 3)
  - VT Backspace (finding 4)
  - C0 echo (finding 5)
  - two reads on one handle (validation gap below)
- **Tests at base:** `read.rs`, `input.rs` and `synthetic-input.txt` don't build or parse at base, so the new tests trivially fail there. `raw_write_passes_bytes_through` was changed and would fail at base on the RAW_FLUSH handle check.

## Validation gaps
- **Every conhost comparison is from memory.** Settling them needs the pinned source, or a ConPTY parity probe in `examples/conhost_probe.rs`.
- **Ctrl delivery order.** The code delivers oldest client first (`read.rs:166`, sorted by cookie). Earlier reviews of this crate say conhost goes newest first. Probe: two clients that log the order Ctrl+C arrives.
- **Two cooked reads on one handle.** Executed: the second read keeps waiting even though the first left the rest of a line on the handle. When it finishes, `keep_pending` overwrites that rest, and `cdef\r\n` was lost. Whether conhost does the same or refuses a second cooked read needs a probe.
- **RAW_READ with output size 0.** It now returns `STATUS_BUFFER_TOO_SMALL` (the `synthetic-apis.txt` change). conhost's answer is unchecked.
- **Windows-only checks I couldn't run:**
  - `own_pty.rs`, including `interactive_cmd_runs_typed_lines` and `ctrl_c_stops_the_running_command_not_the_shell`
  - Windows clippy
  - the D4 manual `workflow_dispatch` run at `197fae02`, which I didn't see
