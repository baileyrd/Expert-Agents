<!-- conhost/1246-r1 run 5, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 517 s, 2899238 tokens, $2.82 -->

I found seven defects; probe tests reproduce the first five, and one of those is a P1 (focus reports, #2). Verdict: **changes required**.

## Review: PR 1246, Rusty-Mill/rusty_mill

- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Head reviewed:** `197fae02607fa8c4428614aef7a4e7ac1b5bca7f`. `git fetch origin pull/1246/head` gives the same SHA.

**About the conhost citations.** The sandbox blocked GitHub, so I couldn't clone microsoft/terminal at `09beddc6`. The only local copy was in another project's scratch space, which I didn't use. Every conhost reference below therefore comes from what I know of the source, not from the pinned tree. Each one names the probe or file that would confirm it.

## Blocking findings

**1. P1: a terminal sequence split right after its ESC becomes an Escape key and literal text (stream fidelity)**
- **Where:** `src/input.rs:240-242`. A lone ESC at the end of a `feed` becomes `Token::Control('\x1b')` (the Escape key).
- **What causes the split:**
  - The engine takes the input lane in 16 KiB slices at arbitrary byte offsets (`sched.rs:14`, `runtime.rs:510-512`).
  - `Shared::write` also cuts a write wherever the lane has room (`runtime.rs:153`).
  - rusty_term sends every key as a win32-input-mode record of about 18 bytes, so this happens whenever more than 16 KiB of input is queued. For example, the user types while the output queue is past 80 KiB, input pauses, and a backlog builds. It also happens when a full lane cuts a record.
- **Trace (executed, `probe_esc_split_at_slice_boundary`):**
  - A cooked read waits, holding `dir`.
  - Slice 1 ends in `\x1b`; slice 2 is `[88;45;120;1;0;1_\r`.
  - Result: the echo is `dir` followed by three `\b \b` erasures, and the read returns `"[88;45;120;1;0;1_\r\n"`. The correct result is `"dirx\r\n"`.
  - The same split gives PSReadLine or any other record reader a spurious Escape (RevertLine) followed by garbage text.
- **Fix:** the engine tells the core whether more input is waiting (`plan.input < view.input`). The decoder treats a trailing ESC as the Escape key only when nothing else is waiting; otherwise it keeps it pending, as it already does for `ESC [`.
- **Regression test:** a core test that feeds `…\x1b` then `[…_` with "more queued", plus a `tests/fixtures/` step file for the same split.

**2. P1: focus reports go to every VT-input client, including ones that never asked for them (parity)**
- **Where:** `console/read.rs:100-104` passes `Token::Focus` through as raw bytes. Because `PREAMBLE` (`console.rs:35`) turns on `?1004h` for the whole console, rusty_term sends `ESC[I`/`ESC[O` on every focus change (`rusty_term/src/gui/window.rs:4975-4985`).
- **Trace (executed, `probe_focus_report_reaches_vt_client`):** with `VIRTUAL_TERMINAL_INPUT` set, `Event::Input(b"\x1b[I")` followed by ReadConsole returns `"\x1b[I"`.
- **conhost:** `InputBuffer` hands focus to `TerminalInput::HandleFocus`, which produces output only when the client itself enabled focus-event mode (`?1004`) through its output.
- **Who breaks:** wsl.exe, ssh.exe and edit.exe receive `ESC [ I`. In vim insert mode under WSL, ESC leaves insert mode and `[I` then runs as a command.
- **Not covered by the design:** §5.10 doesn't record this divergence. §5.5's "VT passed through raw" row doesn't cover reports the host itself asked for.
- **Fix:** track DECSET 1004 from client output and forward focus to a VT client only while it is set.
- **Regression test:** a core test: a VT client that never set 1004 reads nothing after `ESC[I`.

**3. P1: Backspace reaches VT-input clients as 0x08 over win32-input-mode, but as 0x7F over plain VT (parity)**
- **Where:** `input.rs:544-552` (`vt_for`) emits `k.ch`, and rusty_term's Backspace record carries Uc=8 (`rusty_term/src/gui/input.rs`, `win32_uc`).
- **Trace (executed, `probe_backspace_win32_to_vt_client`):** the record `\x1b[8;14;8;1;0;1_` reads back as `[8]`, while plain `\x7f` reads back as `[127]`.
- **conhost:** `TerminalInput` sends DEL for Backspace while DECBKM is reset (the default), and BS only for Ctrl+Backspace.
- **Who breaks:**
  - WSL: `stty erase` is `^?`, so in canonical-mode prompts (sudo or ssh passwords, `read`) Backspace inserts `^H` instead of erasing.
  - emacs -nw opens help on Backspace.
- **Fix:** map `VK_BACK` to 0x7F, and Ctrl+Backspace to 0x08, in `vt_for`.
- **Regression test:** add both cases to `vt_for_turns_records_back_into_vt`.

**4. P2: a finished cooked read overwrites another read's kept line rest**
- **Where:** `console/read.rs:501` → `keep_pending` (`:649-653`) assigns `o.pending = rest` even when `rest` is empty.
- **Trace (executed, `probe_two_cooked_reads_lose_rest`):**
  - Two cooked reads wait on one handle, with capacities 4 and 64.
  - Input `abcdef\r`: A gets `"ab"` and `"cdef\r\n"` is kept.
  - Input `xy\r`: B gets `"xy\r\n"`, and the kept text becomes `""`.
  - A third read finds nothing and waits. `cdef\r\n` is lost.
- **Trigger:** two threads or processes doing cooked reads on one handle with lines longer than the buffer. It's narrow, but typed input is silently lost.
- **Fix:** append to (or don't touch) existing kept text, and have a waiting read take kept text before reading from the queue.
- **Regression test:** the probe trace as a core test.

**5. P2: cooked echo sends typed control characters raw, so the terminal executes them (parity, stream fidelity)**
- **Where:** `cooked.rs:79-81` with `push_units`. `next_char` only excludes Escape when `vk == VK_ESCAPE` (`read.rs:535`).
- **Trace (executed, `probe_control_echo`):** typing `a`, Ctrl+G, Ctrl+[ (rusty_term record with vk 0xDB, uc 0x1B), then `[2J` echoes `a\x07\x1b[2J`. That rings the bell and clears the terminal screen.
- **conhost:** cooked echo shows control characters as `^X`, which is how cmd displays `^A`. In the same area, Backspace erases one column whatever the character's width, so after a wide character or a Tab the display drifts out of step with the line.
- **Fix:** echo controls (below 0x20) as `^` followed by `ch + '@'`, and erase by display width.
- **Regression test:** a core test asserting the echo bytes.

**6. P2: teardown never undoes the preamble**
- **Where:** `runtime.rs:474` emits `?9001h?1004h`. `teardown` (`runtime.rs:687-701`) emits no `?9001l?1004l`.
- **Effect:** if a terminal pane outlives the console (hold-open or respawn), it stays in win32-input-mode and focus-report mode for whatever runs next.
- **Validation needed:** a ConPTY capture after the child exits would confirm whether conhost resets these.
- **Fix:** emit the reset before `Eof` on every teardown path.
- **Regression test:** a test asserting the reset bytes appear before EOF.

**7. P2: test coverage the design requires for these behaviours is missing**
- §7.4 lists a stalled-reader test for "cooked echo under sustained terminal input" (high-water ≤ 144 KiB). There is none; `console.rs:1736` covers writes only.
- §7.2's core interleavings are missing: "ChildExited racing a pending cooked read", and "a second HangUp" with a waiting read. `the_input_event_always_matches_the_queue` (`read.rs:1150`) covers neither.
- §7.4's "repeated Ctrl with a stalled worker" exists only as a queue-level test (`runtime.rs`, `a_full_ctrl_queue_drops_new_events`). It doesn't drive the engine with the worker blocked.
- The §7.2 Windows integration test (an input-event loop over PeekConsoleInput/ReadConsoleInput) is absent from `tests/own_pty.rs`.

## Design questions
- Should §5.5's "VT passed through raw" row exclude focus reports and other reports the host asked for itself? This decides whether finding 2 belongs in §5.10 or gets fixed.

## Optional improvements
- `vt_for` ignores DECCKM and DECKPAM, and the record repeat count.
- A lone surrogate becomes U+FFFD in `vt_for` and in ANSI record expansion. rusty_term only sends the first UTF-16 unit of a non-BMP character, which is a rusty_term issue outside this PR.
- Records cached by an ANSI GetConsoleInput (`cached_records`) don't count towards the input-available event or `GetNumberOfInputEvents`.

## Checks
- **`cargo test -p rusty_conhost`** (Linux): 93 passed, 0 failed. `own_pty` has 0 tests here because it is Windows-only.
- **`cargo clippy -p rusty_conhost --all-targets -- -D warnings`** (Linux): clean.
- **`python3 .github/scripts/changes.py check`:** 539 fragments OK.
- **Probes:** five probe tests in a scratch copy (`scratch/probe`), all run, output quoted above.
- **New tests at base:** the PR's tests live in modules that don't exist at the base, so they can't pass there.

## Validation gaps
- **Windows-only code:** `runtime.rs`, `condrv::key_for`/`key_scan` and the `own_pty` interactive and Ctrl+C tests were never compiled or run here. No Windows target is installed, and I didn't see the PR's Windows CI results.
- **conhost source:** every parity citation needs confirming at `09beddc6`. In particular `inputBuffer.cpp` `_WriteBuffer` → `TerminalInput::HandleKey`/`HandleFocus`, the DECBKM default, cooked `^X` echo, and `VtIo` teardown.
- **Ctrl delivery order:** `ctrl_event` (`read.rs:164-169`) sends oldest client first, by cookie. The brief's checklist expects newest first. Settle it with `ProcessList.cpp` (`AllocProcessData`, `GetTerminationRecordsByGroupId`), or with a Windows probe: two attached clients timestamp their CTRL_C handlers, compared under ConPTY and under our host.

## Verdict
**changes required**
