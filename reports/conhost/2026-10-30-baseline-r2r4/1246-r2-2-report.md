<!-- conhost/1246-r2 run 2, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 350 s, 2150670 tokens, $1.88 -->

I re-checked all three prior findings at this head: K1, K2 and K3 are fixed. I'm returning **changes required** for two new P2 defects. I also found one stale doc line (D3), and I couldn't fetch the conhost source, so the parity citations are not line-verified.

**Repository:** Rusty-Mill/rusty_mill, PR 1246
**Base:** 66f4bc38c8181fd669c58f8e34df385a6afa895e
**Reviewed head:** 8ec54b85a08dc6e0a57e2bafde91952c839dcf18. `git fetch origin pull/1246/head` returned this SHA, and the working tree has no tracked changes.

## Prior findings at this head

- **K1 (P1, cooked echo bypasses backpressure): fixed.** `read.rs:485` checks the full shared queue against 80 KiB before every character: `out.len()` counts what the shared queue held when the event started, set at `runtime.rs:610`. Escape now erases one character per step (`cooked.rs` `erase_one`). A held read returns `Attempt::Held` and the reads behind it wait. A held read always means the queue is over 80 KiB, which is above `DRAIN_WAKE_ABOVE` (64 KiB), so a drain always wakes it and no wake-up is lost. The 60-read scenario now peaks at ≤ 80 KiB + 16 B, against 1,474,680 B before. Removing the check turns 3 tests red (*executed*).
- **K2 (P2, trailing ESC at a slice boundary): fixed.** `take_input` reports a slice as the end of a write only when the lane is empty and no `write` is still going on (`runtime.rs:254`). Otherwise the slice goes in as `Event::InputMore` and the decoder keeps the ESC (`input.rs` `escape`). Making the decoder ignore `end` turns 4 tests red (*executed*). The lane's half of the fix is covered only by a test in `runtime.rs`, which is `#[cfg(windows)]` (gap below).
- **K3 (P3 in the fix commit; P2 in the prior review; surrogate pairs converted one unit at a time): fixed.** `Utf8Units` is used for cooked echo, ANSI raw reads (`next_text` plus `held_unit`), ANSI record reads and win32-to-VT conversion. Breaking either of the two conversions I mutated (`next_text`, ANSI records) turns 1–2 tests red each (*executed*).

## Blocking findings

**F1 (P2): the packet-path `echo_held` guard has no test, and the test comment claiming it is wrong.**
- **Where:** `console/read.rs:407-411` (the guard) and the test `a_read_behind_held_echo_does_not_take_its_input`, ~line 1044 (`assert!(held(&r), "a raw read waits behind them")`).
- **Trigger:** a client switches to raw mode, or turns echo off, while a cooked read is held. Then it calls ReadConsole.
- **Why the test misses it:** `read_w` sends a ReadConsole in the default mode, so it is a cooked read. A cooked read is held by its own echo check before it takes any character, so the test passes without the guard.
- **Mutation (*executed*):** changing `if self.echo_held` to `if false` leaves all 104 tests passing.
- **Probe (*executed*):** 4 held cooked reads, then `set_mode(RAW)`, then ReadConsoleW. At head the raw read waits. Without the guard it returns `information=4`, the bytes `x\0\r\0`. That is the held cooked read's input, taken out of order.
- **Fix:** add that probe as a core `Event` test. Its steps are: hold 4 cooked reads with Escape/`x`/Enter input, `set_mode(input, RAW)`, `read_w`, then assert `held`. Also correct the comment. The test goes red without the guard.

**F2 (P2): the §7.4 cap for the ANSI record cache is now false.**
- **Where:** `docs/projects/term/conhost/design.md:612`, the row "ANSI read caches … one character: 3 bytes, 2 records".
- **Cause:** since K3, a surrogate pair's whole 4-byte UTF-8 expands on its low half's record (`read.rs` `read_records`). Before, each half expanded to U+FFFD (3 bytes), so at most 2 extra records were cached.
- **Probe (*executed*):** win32 records for U+D83D and then U+DE00, followed by an ANSI GetConsoleInput for 1 record. The reply has 1 record and `cached_records.len()` is **3**.
- **Fix:** change the row to "4 bytes / 3 records". The byte cap can stay at 3, because only 3 bytes of a 4-byte character are ever cached. Add a core test asserting the cache is ≤ 3 after a pair read with a count of 1.

**D3 (P2, docs): the design doc doesn't mention the new event.** The `Event` sketch in §3.3 (`design.md:166-174`) and step 4 of the engine loop in §7.2 (`handle(Input)`) don't include the `Event::InputMore` this PR adds. §7.2 should say that a slice which doesn't end where the terminal's write ended goes in as `InputMore`. No test is needed.

## Design questions

- While a cooked read is held, GetNumberOfInputEvents still reports queued records and the input event stays set. Meanwhile PeekConsoleInput and ReadConsoleInput with NOWAIT return 0 records. A second client that polls on the event would spin until the terminal drains. The trigger is narrow: two clients on one console, with a stalled reader. Whether this should be recorded is the owner's decision.

## Optional improvements

- `held_unit` holds a high surrogate taken out of the queue for an ANSI raw read. GetConsoleInput, GetNumberOfInputEvents and the input event can't see it, so a client that switches to ReadConsoleInputW at that moment gets only the low half. Consider putting it back at the front of the queue instead.

## Checks performed

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux, fresh target dir) | 104 passed, 0 failed |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux) | clean |
| `python3 .github/scripts/changes.py check` | "539 fragment(s) ok" |
| Mutation runs (scratch copy) | 6 mutations; 4 caught, 2 survived (F1, and the Windows-only lane test) |
| Probe tests (scratch copy) | F1 and F2, both executed as described above |

My first test run reported 6 failures, but its panic line numbers didn't match the head source: the shared scratch target held stale artifacts from an earlier build. A fresh target dir is clean. The new tests can't compile at the base, since the whole input feature is absent there, so the mutation runs stand in for "run at base".

## Validation gaps

- **No conhost source.** The sandbox denied github.com, so I couldn't clone microsoft/terminal at `09beddc6`. The parity points below come from the PR's in-tree references and my recall, without verified `path:line` citations:
  - the lone-ESC decision at the end of each input chunk (`StateMachine::ProcessString` / `InputStateMachineEngine`);
  - ANSI expansion of surrogate pairs in `InputBuffer::Read`.

  Two Windows probes under ConPTY would settle the second one, and the cooked-echo question: ReadConsoleInputA at code page 65001 after a win32-input emoji, and the echo of a ReadConsoleA line containing an emoji.
- **Windows-only tests not run:**
  - `runtime.rs` `input_ends_where_a_write_ended_not_where_the_engine_cut`. This is the only test guarding the lane's `writing` count; removing `&& lanes.writing == 0` passes on Linux.
  - `tests/own_pty.rs`.
- **Not seen:** Windows clippy, the D4 `workflow_dispatch` run at 8ec54b85, and the PR body. The sandbox denied api.github.com, and D4 requires a new run after this push.

## Verdict

**Changes required** (F1, F2, D3; all P2).
