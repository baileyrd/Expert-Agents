<!-- conhost/1246-r2 run 1, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 303 s, 1451606 tokens, $1.52 -->

# Review of PR #1246 (Rusty-Mill/rusty_mill) at head 8ec54b85: changes required

All three earlier findings (K1, K2, K3) are fixed. One new P2 remains: a design-doc cap that the K3 fix made false.

- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Head:** `8ec54b85a08dc6e0a57e2bafde91952c839dcf18`. I fetched `pull/1246/head` and it matches this SHA.
- **Scope:** I reviewed the commit added since the last review, 8ec54b85 ("bound cooked echo, keep split ESC, keep surrogate pairs"), and re-checked K1–K3 against the surrounding code at the head. The rest of the diff is unchanged since the review of 197fae02.

## The earlier findings, re-checked

**K1 (P1), cooked echo bypasses output backpressure: fixed.**
- **The fix:** `attempt` now checks `out.len() + echo.len() > ECHO_ABOVE` (80 KiB) before every character (`src/console/read.rs:476-492`). Escape erases one character per step and adds at most 3 bytes of echo (`src/cooked.rs:96-112`).
- **Order is kept:** a held read keeps its input queued. Reads behind it wait at `read.rs:404`. GetConsoleInput returns nothing while the hold lasts (`read.rs:313`), so input stays in order.
- **Liveness:** a held read always has more than 80 KiB queued at the moment it is held. Every terminal read that starts above `DRAIN_WAKE_ABOVE` (64 KiB, `runtime.rs:39`, `216`) raises `output_drained`. The `OutputDrained` handler then calls `serve_reads` (`console.rs:539-543`). A drain therefore always follows a hold.
- **Clearing the hold:** `end_reads` and `clear_input` only clear `echo_held`. `serve_reads` recomputes it on every pass.
- **Tests:** the new test `cooked_echo_stays_within_the_host_reserve_and_resumes_on_drain` and two others go red without the check (mutation M1: 3 failures, *executed*).

**K2 (P2), a trailing ESC at a slice boundary is taken as the Escape key: fixed.**
- **The fix:** the input lane counts `write` calls still in progress (`runtime.rs:142-170`). `take_input` reports `end` only when the lane is empty and no write is in progress. Every early return decrements the counter: hang-up, close, and an empty buffer.
- **Cut points:** for a write that fits the lane, the extend, the decrement and the wake all happen under one lock. The engine therefore never sees a fitting write half done. A 16 KiB slice cut, or a write larger than the lane, arrives as `InputMore`, and the decoder keeps the ESC (`input.rs:240-249`).
- **Tests:** mutation M2, which makes a trailing ESC always the Escape key, turns 4 tests red (*executed*). Mutation M3, which forces the lane's `end` to `true`, turns nothing red on Linux. Its guard, `runtime::tests::input_ends_where_a_write_ended_not_where_the_engine_cut`, builds only on Windows; see validation gaps.

**K3 (P2), UTF-16 surrogate pairs converted one unit at a time: fixed.**
- **The fix:** `Utf8Units` (`input.rs:603-647`) is used in four places:
  - cooked echo, where Backspace drops an unshown high surrogate without writing echo;
  - `vt_for`, where Alt goes out once with the high surrogate and key-ups and modifier keys leave it waiting;
  - ANSI GetConsoleInput, with separate state for key-down and key-up records and none saved on a peek;
  - ANSI raw reads, through `next_text` and `held_unit`.
- **Clearing:** `clear_input` resets all of this state.
- **Tests:** mutation M4, which restores per-unit lossy conversion in `read_records`, turns 1 test red (*executed*).

## Blocking findings

### F1 (P2): the design doc understates the ANSI record cache cap
- **File:** `docs/projects/term/conhost/design.md:614`.
- **The doc says:** "ANSI read caches (a split UTF-8 character, records expanded past the count) | one character: 3 bytes, 2 records".
- **What the code does:** since the K3 fix, the low surrogate's record carries the pair's whole 4-byte UTF-8 (`read.rs:658-666`). For each byte, `read_records` emits one record. With `count = 1`, `out.split_off(1)` leaves **3** records in `cached_records`.
- **Trace (*executed*):**
  1. Terminal input `F0 9F 98 80` (😀).
  2. ANSI GetConsoleInput with an output size of 1 record.
  3. The call returns 1 record, and `cached_records.len() == 3`. I ran this as a scratch test in a worktree, then removed it.
- **Required correction:** change the row to "3 bytes, 3 records", meaning one 4-byte character. The 3.3 MiB total is unaffected.
- **Regression test:** assert `cached_records.len() <= 3` after a one-record ANSI GetConsoleInput of a supplementary character. This turns the doc's cap into a checked number.

## Design questions
None.

## Optional improvements (not blocking)
1. **A held key is invisible to the input event and to records.**
   - **When it happens:** in `next_text` (`read.rs:316-331`), a lone high surrogate followed by an ordinary unit leaves that unit in `held_unit`.
   - **Effect:** `sync_input_available` (`console.rs:552`), `GetNumberOfInputEvents` and `read_records` don't see it. A client that waits on the input handle before a ReadFile could block while a key is pending.
   - **Trigger:** only a malformed record (a lone surrogate), so this is not graded as a defect. A fix is to count `held_unit` as available input, or to leave the following unit in the queue.
2. **An inaccurate comment.** The `Attempt::Held` comment (`read.rs:44`) says "Input is queued". The check at the top of the loop also holds a read with nothing queued whenever the output queue is above 80 KiB. The behaviour is harmless; only the comment is inaccurate.
3. **Alt+[, Alt+O, Alt+], Alt+P at the end of a write.**
   - **Effect:** in plain VT input (no win32-input-mode), `ESC [`, `ESC O`, `ESC ]` and `ESC P` at the end of a write stay incomplete. `ESC ]` and `ESC P` swallow later typing until an ESC arrives or 4 KiB pass.
   - **Reach:** none today. The preamble turns on `?9001h` and rusty_term speaks it, so no current client hits this.
   - **Parity, unconfirmed:** from memory, conhost's input engine flushes an unfinished sequence as keys at the end of a string (`FlushAtEndOfString`). I could not confirm this at 09beddc6 (see validation gaps). Now that the decoder has `end`, the same treatment would be a small change.

## Checks performed
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | 104 passed, 0 failed. `tests/own_pty.rs` ran 0 tests (Windows-only) |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` | clean; only workspace manifest warnings from other crates |
| `python3 .github/scripts/changes.py check` | 539 fragments ok |
| Mutation M1–M4 at the head (scratch worktree, since removed) | M1, M2 and M4 turn tests red. M3 turns nothing red on Linux; its guard is Windows-only |
| Probe of the ANSI record cache size | 3 records (F1) |

On running the new tests at BASE: base 66f4bc38 has no input subsystem, so they cannot compile there. The mutations above stand in for that check.

## Validation gaps
- **Windows-only tests not run:** `runtime.rs` is `#[cfg(windows)]`, so `input_ends_where_a_write_ended_not_where_the_engine_cut`, the other runtime tests and `tests/own_pty.rs` (interactive cmd, Ctrl+C, Ctrl+Break) were not seen to pass. The PR's Windows CI log is needed.
- **conhost source unreachable:** the sandbox proxy denied `github.com`, so I could not cite microsoft/terminal at 09beddc6. Two parity points are unconfirmed:
  - how ANSI `InputBuffer::Read` splits a surrogate pair across records;
  - `StateMachine::ProcessString`'s end-of-string flush.
  
  A Windows probe would settle both. Run `examples/conhost_probe.rs` under OpenConsole in ConPTY mode and send:
  - 😀 as a win32-input pair, then ReadConsoleInputA with a 1-record buffer, repeated;
  - a bare `ESC ]` write, then `a`.

## Verdict
**Changes required**: F1 (P2, design doc row out of date).
