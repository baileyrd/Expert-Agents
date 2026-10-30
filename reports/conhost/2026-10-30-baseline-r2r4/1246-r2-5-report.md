<!-- conhost/1246-r2 run 5, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 427 s, 2703531 tokens, $2.35 -->

I reviewed PR 1246 and the verdict is **changes required**: the three prior findings are fixed, but I found three new P2 defects, all confirmed by running probe tests. I could not reach GitHub, so every conhost comparison below is from memory and unverified.

**Pin:** Rusty-Mill/rusty_mill, base `66f4bc38c8181fd669c58f8e34df385a6afa895e`, head `8ec54b85a08dc6e0a57e2bafde91952c839dcf18`. The fetched `pull/1246/head` matches the head. The diff runs from base to head (5 commits). The fix commit 8ec54b85 is the new part of this round.

## Prior findings at this head

| # | Status | Evidence |
|---|---|---|
| K1 (P1) cooked echo bypassed output backpressure | **Resolved** | `read.rs:485` checks the 80 KiB reserve before every echoed character. Escape now erases one character per step (`cooked.rs:111`). A read stopped this way keeps its input queued and resumes when the reader drains, and that drain signal does fire (`runtime.rs:216`). The only unchecked output left is the line's ending `\r\n`, at most 2 bytes × 64 reads = 128 B. Removing the check turns 3 tests red, including `cooked_echo_stays_within_the_host_reserve_and_resumes_on_drain`. |
| K2 (P2) a trailing ESC at a slice boundary was decided as Escape | **Resolved for a lone ESC** | `input.rs:244-249` decides Escape only where the terminal's write ended; the runtime tracks writes in progress (`runtime.rs:254`). Making a lone ESC always Escape turns 4 tests red. The longer prefixes `ESC [` and `ESC O` are still wrong; see F2. |
| K3 (P2) surrogate pairs were converted one unit at a time | **Resolved**, with a leftover in the same code (F1) | `Utf8Units` (`input.rs:603`) is used by cooked echo, ANSI reads, ANSI GetConsoleInput and the conversion back to VT. Converting per unit again turns 4 tests red. |

## Blocking findings

**F1 (P2): a character held back by an ANSI read is invisible to the input event, the event count and GetConsoleInput, and is delivered out of order.**
- **Where:** `src/console/read.rs:573-575` (`next_text`, the `Some(other)` branch). The parked unit sits in `held_unit` (`read.rs:594`), which `sync_input_available` (`console.rs:552`), `get_number_of_input_events` (`read.rs:262`) and `read_records` (`read.rs:652`) never look at.
- **Trigger (executed):** in raw mode, terminal input is a win32-input-mode high surrogate `ESC[0;0;55357;1;0;1_` followed by `a`, then an ANSI ReadConsole with a 3-byte buffer.
  - The read returns U+FFFD and leaves `held_unit = Some('a')`.
  - The input event is cleared, GetNumberOfInputEvents is 0 and PeekConsoleInput returns 0 records, yet `a` is still waiting to be read.
  - After a later `b`, ReadConsoleInput returns `b`, and only the next ReadConsoleW returns `a`.
- **Impact:** a client that waits on the input handle stalls until the next key, and keys arrive out of order. It needs an unpaired surrogate (or a surrogate record with repeat count above 1) in terminal input.
- **Fix:** put the look-ahead unit back at the front of the queue as a record, or count `held_unit` in the event, the count and record reads.
- **Regression test:** a core `Event` test built from the probe, asserting the input event stays true, the count is 1 and record order is `a` then `b`.

**F2 (P2): Alt+[ or Alt+Shift+O at the end of a write is held, then merges with the next key.**
- **Where:** `src/input.rs:251-255`. `csi()` returns Incomplete at line 299 and SS3 at line 254, whatever the `end` flag says.
- **Trigger (executed):** `Input("\x1b[")` then `Input("A")` produces VK_UP (0x26) down and up, and Alt+[ is never delivered. `Input("\x1bO")` then `Input("P")` produces F1 (0x70).
- **Who hits it:** the plain-VT input path, which the mux and yirp consumers will feed (§10 PRs 11–12); rusty_term itself sends win32-input-mode.
- **Parity:** from memory, conhost's `StateMachine::ProcessString` (`src/terminal/parser/stateMachine.cpp`) handles exactly this case for its input engine: at the end of a read, a dangling `ESC [` / `ESC O` is flushed as Alt+[ / Alt+O. Unverified; it's a validation gap. The defect stands without it, since two keypresses become a different key.
- **Fix:** when `end` is true and the input is exactly `ESC [` or `ESC O`, return `Alt('[')` / `Alt('O')` consuming 2 bytes.
- **Regression tests:** in `input.rs`, `feed(b"\x1b[", true)` yields `Alt('[')`, while `feed(b"\x1b[", false)` keeps it pending. Add a fixture step: `input 1b5b`, then `input 41`.

**F3 (P2): the design doc's ANSI cache cap no longer matches the code.**
- **Where:** `docs/projects/term/conhost/design.md:614` says the ANSI read caches hold "one character: 3 bytes, 2 records".
- **Trigger (executed):** after K3, a supplementary character is 4 UTF-8 bytes. An ANSI GetConsoleInput with count 1 on "😀" leaves `cached_records.len() == 3`.
- **Fix:** say "3 bytes, 3 records" and pin it with a named constant.
- **Regression test:** assert `cached_records.len() <= 3` after a count-1 ANSI read of a supplementary character.

## Design questions
- **Peek and count disagree while echo is held.** While a cooked read is held back, `get_console_input` returns 0 records (`read.rs:313`), but GetNumberOfInputEvents reports N and the input event stays set. A second client polling Peek/KeyAvailable would spin for as long as the hold lasts. §7.4 records the hold but not this.
- **Alt+Shift+P / Alt+] swallow typing.** `ESC P` / `ESC ]` start a DCS/OSC reply that eats all further typing, Enter included, until an ESC or 4,096 bytes (`input.rs:275-289`). §5.6 records that replies and keys can share bytes, but these aren't complete replies. With the new `end` flag, they could be flushed as Alt keys.

## Optional improvements
- The `Attempt::Held` doc comment (`read.rs:44`) says input is queued, but the hold also triggers with an empty queue (the check at `read.rs:485` runs before the first `next_char`).
- In `vt_for`, Alt is dropped when a dangling high surrogate is pending (`input.rs:555`). This only matters for malformed input.

## Checks and validation gaps
- `cargo test -p rusty_conhost --offline`: 104 passed on Linux. The integration tests and the `runtime` module are Windows-only, so the new `input_ends_where_a_write_ended_not_where_the_engine_cut` test did not run.
- `cargo clippy -p rusty_conhost --all-targets -- -D warnings` on Linux: clean, but it was a cached build (`Finished` in 0.28 s). Windows clippy was not run.
- `python3 .github/scripts/changes.py check`: 539 fragments ok.
- Probes and mutation runs were done in a scratch copy: the three probes above, and reverting each of K1, K2 and K3 turned its tests red. All the new tests are new code against a base that has no input module, so running them at base guards nothing; the mutation runs are the meaningful check.
- **Gaps:**
  - **conhost source:** the clone of microsoft/terminal `09beddc6` was denied by the sandbox, so every conhost citation (cooked-echo control characters, ANSI surrogate handling, end-of-read Alt flushing) is unverified.
  - **D4 run:** I couldn't confirm a successful Windows CI run at `8ec54b85` or read the PR body (api.github.com denied).
  - **Proposed Windows probes, each run under ConPTY:**
    - ReadConsoleA with a 2-byte buffer, and GetConsoleInputA with count 1, after WriteConsoleInputW of U+1F600 as two records.
    - `\x1b[` then `A` written as two separate writes.
    - Cooked echo of a VT-mode ESC character.

**Verdict: changes required** (F1–F3, all P2).
