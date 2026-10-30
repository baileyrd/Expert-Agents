<!-- conhost/1246-r2 run 2, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 284 s, 1852276 tokens, $1.62 -->

# Review of Rusty-Mill/rusty_mill PR 1246: changes required

All three prior findings are fixed at this head. The cooked-echo fix (K1) brings in a new liveness defect, which a probe in a scratch copy confirmed, and one divergence that the design doesn't record.

- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Reviewed head:** `8ec54b85a08dc6e0a57e2bafde91952c839dcf18`. `git fetch origin pull/1246/head` gives `FETCH_HEAD` = `8ec54b85…`, so the pin is confirmed.

## Prior findings at this head

| # | Status | Evidence |
|---|---|---|
| K1 (P1) cooked echo bypasses backpressure | **Fixed** | `console/read.rs:481-489` checks `out.len() + echo.len() > ECHO_ABOVE` before every character. Escape now erases one character per step (`cooked.rs` `erase_one`), so one step adds at most 4 bytes. `OutputQueue::len` is the shared queue plus this call's new bytes (`console.rs:229-235`, `runtime.rs:610-611`). A held read always gets a drain wake: the queue was over 80 KiB when the read was held, and `read()` sets `output_drained` whenever it drains from above 64 KiB (`runtime.rs:216`). `cooked_echo_stays_within_the_host_reserve_and_resumes_on_drain` checks the high-water mark is ≤ 80 KiB + 16 B. The fix creates finding 1 below. |
| K2 (P2) trailing ESC at a slice boundary | **Fixed** | `escape()` (`input.rs:244-248`) treats a lone ESC as the Escape key only when `end` is true. `take_input` (`runtime.rs:250-256`) sets `end` only when the lane is empty and `writing == 0`. I traced `write()`'s `writing` counter on every exit path: normal end, empty buffer, hang-up or closed, and waiting for room. It is balanced. When input is cut short, `poll` (`sched.rs:92`) makes sure the next turn takes the rest. |
| K3 (P2) surrogates converted one unit at a time | **Fixed** | `Utf8Units` (`input.rs:604-645`) is used for cooked echo, the `record_utf8` ANSI record conversion, and `vt_for`. ANSI raw reads use `next_text` with `held_unit`. Backspace on a high half that hasn't been shown yet erases nothing. |

## Blocking findings

### 1. P1 (liveness): removing a held cooked read strands the reads waiting behind it, with their input queued

- **Where:** `crates/libs/ui/rusty_conhost/src/console/read.rs:193-215` (`end_reads`), called from `console.rs:598-604` (DISCONNECT) and `console.rs:612-614` (CLOSE_OBJECT).
- **Trigger:** a cooked read is held back by its echo, and its handle closes or its client disconnects while another read waits behind it.
- **Cause:** `end_reads` removes the held read and recomputes `echo_held`, but nothing re-runs `serve_reads`.
  - The reads behind the held one were queued without ever being tried (`read.rs:407-410`, `446-448`), so they stay parked.
  - Their input stays queued, and the input-available event stays signalled (`console.rs:552`).
  - They move again only on the next terminal input or `OutputDrained`, and neither event has anything to do with these reads.
- **Failure scenario (*executed*, with a probe test in a scratch copy of the crate):**
  1. Process 1 has two input handles. Four cooked ReadConsoleW calls, each prefilled with 8,192 units, wait on handle A.
  2. Input: `{ESC win32 record, "x", CR} × 4`. Each Escape-erase echoes 24 KiB, so the fourth read passes 80 KiB and is held. Output: `completed on input: 3`.
  3. A raw ReadConsoleW on handle B waits: `echo_held true`, 3 records queued.
  4. CLOSE_OBJECT on A. The held read completes with STATUS_ALERTED. Output: `echo_held false input 3 waiting 1`. The raw read on B is not completed.

  The assertion `raw read stranded with input queued` failed. A DISCONNECT goes through the same `end_reads` path (*source-traced*).
- **Who it hurts:** cmd is killed (Ctrl+Break, or `taskkill`) during a held `set /p` while a background `start /b` program's read waits. That program keeps waiting for as long as the terminal reader stalls. A client waiting on the input handle wakes and finds its read still parked.
- **Required correction:** in the DISCONNECT and CLOSE_OBJECT arms, call `self.serve_reads(out, fx)` after `end_reads` whenever reads remain, or from `end_reads` itself, passing `out` through.
- **Regression test:** the probe above, in core form: close the held read's handle, then assert the raw read on the other handle completes with `"x\r"`. Add the same test with DISCONNECT of a second process.

### 2. P2 (divergence missing from §5.10): while echo is held, GetConsoleInput returns nothing but the input event and the event count say input is there

- **Where:** `read.rs:313-317`, together with `console.rs:552` and `read.rs:255-265`.
- **Trigger:** a client peeks or reads input records while a cooked read is held back by its echo.
- **Cause:** while `echo_held` is set:
  - PeekConsoleInput (NOREMOVE|NOWAIT) answers 0 records;
  - GetNumberOfInputEvents reports N;
  - the input-available event stays signalled.
- **Effect (*source-traced*; the PR's own test `read.rs:1044` asserts the 0):** a client that loops on waiting for the input handle and then peeking spins hot until the terminal drains.
- **conhost:** its cooked read would already have taken those records into the line, so it reports 0 events and resets the event. *Inferred:* I couldn't open the conhost source to check this (see validation gaps).
- **Docs:** neither §7.4 nor §5.10 records this. §7.4 says only that the input stays queued.
- **Required correction:** while `echo_held`, report the held input as unavailable: hold the event reset and count 0, consistent with the peek. Otherwise, record the behaviour as a §5.10 row with its reason.
- **Regression test:** extend `a_read_behind_held_echo_does_not_take_its_input` to assert GetNumberOfInputEvents = 0 and that the last `SetInputAvailable` was `false` while held.

## Design questions

None.

## Optional improvements

- `held_unit` (a high surrogate held by an ANSI raw read) is invisible to GetConsoleInput and GetNumberOfInputEvents. If a client switches from ReadConsoleA to ReadConsoleInput in the middle of a pair, the held half is prepended to a later ReadConsole. The trigger is narrow.
- §7.4's cooked-echo row could say that the reserve can be overshot by at most one character plus CR LF per finished read. That is what the test's `+16` allows.

## Checks

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux, head) | **Passed:** 104 unit tests. `tests/own_pty.rs` ran 0 tests because they are Windows-only. |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux) | **Passed**, no warnings. `runtime.rs` and `pty.rs` are `cfg(windows)` and weren't linted. |
| `python3 .github/scripts/changes.py check` | **Passed:** 539 fragments OK. |
| Probe for finding 1 (scratch copy, `scratch/probe/rusty_conhost`) | **Failed as predicted:** the raw read was stranded. |
| New tests at the base | Not applicable: the input module, the read APIs and `Event::InputMore` don't exist at `66f4bc38`, so none of the new tests compile there. |

## Validation gaps

- **conhost source:** cloning microsoft/terminal at `09beddc6` was refused by the sandbox (`github.com:443` denied), so the parity claims are unchecked against `readDataCooked.cpp` and `inputBuffer.cpp`. Two need a Windows probe that compares ConPTY with our host (`examples/conhost_probe.rs`):
  - whether ReadConsoleInputA splits a surrogate pair's UTF-8 across the low half's records, or gives `?` per unit;
  - whether a cooked read with ECHO_INPUT off still emits CR LF at Enter (`finish_line`, `read.rs:533-536`).
- **Windows-only code:** the K2 lane logic (`Shared::write` and `take_input`, `runtime.rs:140-256`) and its tests ("the lane reports a cut inside a write") build only on Windows. I didn't compile, lint or run them. A Windows CI log for this head is needed.
- I saw no Windows integration run of `own_pty.rs` for this head.

## Verdict: **changes required**
