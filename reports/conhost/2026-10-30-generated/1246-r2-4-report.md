<!-- conhost/1246-r2 run 4, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 262 s, 1156571 tokens, $1.25 -->

# Review of Rusty-Mill/rusty_mill PR #1246

- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Reviewed head:** `8ec54b85a08dc6e0a57e2bafde91952c839dcf18`. `git fetch origin pull/1246/head` returned this SHA, so the review is pinned.

**Verdict: changes required.** The three earlier findings are fixed, but the surrogate fix (K3) adds a new defect (F1, P1), and there is one narrower one (F2, P2). Clippy, the crate's tests and the change-fragment check all pass. I could not reach GitHub, so nothing below is checked against conhost source.

## Earlier findings at this head

- **K1 (P1) cooked echo bypasses output backpressure: fixed.**
  - `attempt` now checks `out.len() + echo.len() > ECHO_ABOVE` (80 KiB) before each character (`read.rs`, `ReadKind::Line` arm).
  - Escape now erases one character per step (`cooked.rs` `erase_one`), so one step adds at most 6 bytes of echo. The line's final `\r\n` adds a few more.
  - A held read keeps its place, and the reads behind it are not served (`serve_reads`, `read.rs:436-449`).
  - It continues on `OutputDrained`. That event always comes: a held read means more than 80 KiB is queued, which is above `DRAIN_WAKE_ABOVE` (64 KiB, `runtime.rs:39,216`).
  - If the held read's client leaves, `echo_held` can stay set until the next drain. That still makes progress.
  - Test: `cooked_echo_stays_within_the_host_reserve_and_resumes_on_drain` (high water at most the reserve plus 16).
- **K2 (P2) trailing ESC at a slice boundary: fixed.**
  - `take_input` reports `end` only when the input lane is empty and `writing == 0` (`runtime.rs`). Otherwise the engine sends `InputMore`, and `escape()` keeps the ESC pending.
  - I traced the `writing` counter on every path: a write that finishes, an empty write, a hang-up while blocked, and a write that blocks on a full lane. It always goes back down.
  - Tests: `a_split_anywhere_decodes_as_the_whole`, `input_ends_where_a_write_ended_not_where_the_engine_cut`, and the `input-more` fixture step.
- **K3 (P2) surrogates converted one unit at a time: fixed.** `Utf8Units` now does the conversion in cooked echo, ANSI raw reads, ANSI records and `vt_for`. But the raw-read part brings in F1.

## Blocking findings

### F1 (P1, liveness): an ANSI raw read takes a key out of the queue and hides it
- **Where:** `src/console/read.rs:566-580` (`next_text`), `:594` (`next_char`), and `src/console.rs:552`.
- **Trigger:** a lone high surrogate followed by another character. This needs malformed terminal input, such as a win32-input-mode record with only a high half.
- **Cause:** to decide whether a high surrogate has a low half, `next_text` pops the next unit off the queue. If that unit is not a low half, it goes into `held_unit`, outside the queue. The same happens to a high surrogate that arrives alone.
- **Effect:** none of the following look at `held_unit`:
  - the input-available event (`!self.input.is_empty()`),
  - `GetNumberOfConsoleInputEvents` (`read.rs:262`),
  - `GetConsoleInput` and peek.
- **Trace (*executed*, probe `probe_lone_high_then_char_strands_the_char` in a scratch copy):**
  1. Raw mode. Input is `ESC[0;0;55357;1;0;1_` then `a`.
  2. `ReadConsoleA` with a 3-byte buffer returns `EF BF BD` (U+FFFD).
  3. Now `held_unit = Some('a')`, the queue is empty, `input_available = false`, the event count is 0 and a peek returns 0 records.
  4. The next `ReadConsoleA` returns `a`.
- **What breaks:**
  - A client that waits on the input handle before reading (WaitForSingleObject then ReadConsoleA) never wakes for `a` until another key arrives.
  - A `ReadConsoleInput` client sees later keys before `a`.
- **Required correction:** look at the next unit without popping it (through `input.iter()`), or push it back to the front of the queue. A high half still waiting for its partner should also stay counted as queued input.
- **Regression test:** the probe above, asserting that after the first read the input stays available, the event count is 1 and a peek returns `a`'s records.

### F2 (P2): during held echo, the event count and the input event disagree with GetConsoleInput
- **Where:** `src/console/read.rs:313` against `:262` and `console.rs:552`.
- **Trigger:** a second reader on the console while a cooked read waits for the terminal to drain output. The window lasts until the terminal reads its output.
- **Trace (*executed*, probe `probe_held_echo_peek_vs_count`):**
  1. 12 prefilled cooked reads, then Escape and Enter for each.
  2. `echo_held` becomes true with 9 reads still waiting.
  3. `GetNumberOfConsoleInputEvents` returns 26 and the input event stays signalled.
  4. `PeekConsoleInput` with NOWAIT returns 0 records.
- **What breaks:** a client that loops on WaitForSingleObject then peek (Console.KeyAvailable-style polling) spins hot for the whole window. It is told input is ready, but every peek comes back empty.
- **Required correction:** while echo is held, either leave the held read's input out of the count and the input event, or let GetConsoleInput return it. Either way the three must agree.
- **Regression test:** the probe, asserting the count matches what a peek returns.

## Design questions
- **Lone ESC at the end of a write:** this is decided at the terminal's write boundary, while ConPTY decides at its own pipe-read boundary. I think the new rule is at least as good, but whether conhost at `09beddc6` treats ESC at the end of a string as the Escape key needs `StateMachine::ProcessString` in that source. That source is listed under validation gaps.

## Optional improvements
- `vt_utf8` is not flushed when a plain-text token goes through in VT input mode. A pending high half then comes out as U+FFFD after that text instead of before it. It is cosmetic.

## Checks
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` | passed: 104 tests |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` | passed (exit 0) |
| `python3 .github/scripts/changes.py check` | passed: 539 fragments ok |
| Probes in scratch copy (`probe_lone_high_then_char_strands_the_char`, `probe_held_echo_peek_vs_count`) | ran and printed the traces above |

The new tests call `Event::InputMore` and `feed(_, end)`, which don't exist in the code before the fix commit. So without the fixes they fail to compile and cannot pass. Base `66f4bc38` has no input code at all.

## Validation gaps
- **No conhost source:** the clone of microsoft/terminal at `09beddc6` was blocked (github.com denied by the sandbox). Not checked against conhost:
  - ANSI `InputBuffer::Read`: whether conhost keeps surrogate pairs whole in ReadConsoleInputA, or converts unit by unit as `WideCharToMultiByte` would.
  - The input `StateMachine`'s handling of an ESC at the end of a string.
  - Whether conhost lets a cooked read hold input back like this.
- **Windows-only tests were not run:** `tests/own_pty.rs` (interactive cmd, Ctrl+C) needs a Windows run.
- **Probe to settle the surrogate question:** in `examples/conhost_probe.rs`, inject one win32 key record with 0xD83D, then `a`, then call ReadConsoleA with a 3-byte buffer under real conhost/ConPTY. Record `GetNumberOfConsoleInputEvents` and a wait on the handle afterwards.
