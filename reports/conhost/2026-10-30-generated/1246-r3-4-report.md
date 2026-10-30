<!-- conhost/1246-r3 run 4, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 223 s, 1044080 tokens, $1.14 -->

# Review of PR 1246: rusty_conhost console input (host PR 3)

**Repository:** Rusty-Mill/rusty_mill, PR 1246
**Base:** 66f4bc38c8181fd669c58f8e34df385a6afa895e
**Reviewed head:** 615e082460958ae36fbe19aaf705bc6898e70f5c. `git fetch origin pull/1246/head` gave the same SHA, so the review is pinned.

**Verdict: changes required.** K1 is fixed, but the K2 fix brings in a P1 liveness defect, and there is one P2 memory-bound doc defect.

This round re-checked the prior findings against the new commit 615e08246 and went through the new code it adds: the reply budget, `ServeReads`, `reads_cut` and `esc_ends`.

## Prior findings
- **K1 (P1), all full read replies built before any is completed: fixed.**
  - `serve_reads` (`src/console/read.rs:449-480`) always tries the first read. It stops before any later read whose capacity would take the call past `READ_REPLY_CAP`, sets `reads_cut`, and leaves the rest queued in order.
  - Every read's capacity is capped at 1 MiB: `read.rs:320-323` for GetConsoleInput and `read.rs:384-386` for ReadConsole and RAW_READ. So one call builds at most 1 MiB.
  - While `reads_cut` is set, new reads and GetConsoleInput queue behind it (`reads_behind`, `read.rs:223`).
  - `sched::turn` serves the remaining reads on the next turn, ahead of new input, and polls (`sched.rs:87-98`).
  - `reads_cut` is only set after a read completes, so it can't spin.
  - Effects are applied after each `handle`, so replies built by `OutputDrained`, `ServeReads` and `Input` in one turn don't pile up.
  - `HangUp` clears the flag (`console.rs:592-593`).
  - The test `one_call_builds_at_most_one_reply_budget_and_the_rest_follows` uses `reads_ready`, which doesn't exist at 8ec54b85, so it fails there.
- **K2 (P2), write boundaries lost when completed writes coalesce: fixed for decoding, but the fix brings in F1 below.**

## Blocking findings

### F1 (P1, liveness): an ESC cut strands the rest of the lane until something unrelated happens
**Location:** `crates/libs/ui/rusty_conhost/src/runtime.rs:264-280` (`take_input`), `runtime.rs:552-574`, `sched.rs:98`

**Trigger:** the terminal makes two writes, the first ending in ESC, before the engine takes input, and the lane holds 16 KiB or less. That is K2's own scenario: Escape followed by typing in vim, a shell or PSReadLine.

**Trace** (*source-traced*; the sched half is *executed*):
1. The terminal calls `write(b"\x1b")` then `write(b"x")`. Each call does `waker.wake()`. The waker is an **auto-reset** event (`platform-windows/src/condrv.rs:101-102`, `sys/condrv.rs:250-252`), so the two wakes collapse into one signal.
2. `server.wait` returns, which consumes that signal.
3. In the turn, `take_control` reports `input = 2`. `sched::turn` plans `input = 2` with `poll = (2 < 2) = false`. I ran this in a scratch probe: `plan input=2 poll=false`.
4. `take_input(2)` cuts at `esc_ends.front() == 1` and returns `("\x1b", true)`, leaving `"x"` in the lane. The PR's own test `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce` asserts exactly this cut.
5. `until = plan.poll.then(Instant::now)` is `None` (`runtime.rs:574`), so `wait` blocks with no signal pending.

**Effect:** the `x` (or `:`, `j`, an arrow key's `\x1b[A`) stays in the lane until the next keystroke, packet, output drain or child exit. Any waiting ReadConsole or GetConsoleInput isn't completed on the turn that could have done it.

**Design it breaks:** §7.2 step 6: "until = now when steps 2–4 left work they could still do". The commit made `take_input` take less than the plan, but neither the plan nor the turn accounts for the shortfall.

**Required correction:**
- Have `take_input` report whether bytes remain, or re-read the lane length after taking.
- If the lane is still non-empty and the output is at or below `INPUT_PAUSE_ABOVE`, poll this turn: either set `until = now` or call `waker.wake()`.

**Regression test** (Windows runtime layer, since `runtime` is `cfg(windows)`):
- Write `b"\x1b"` and `b"x"`, then run one `turn()`.
- Assert that the following `wait` returns immediately and that the next turn hands `"x"` to the console.
- A core alternative: give `sched::turn` a field for "bytes left after the cut", and assert `poll` when it is non-zero.

### F2 (P2, docs and memory bound): `esc_ends` isn't counted in §7.4's memory bound
**Location:** `runtime.rs:101-107`, `runtime.rs:171-175`; `docs/projects/term/conhost/design.md:621`

**Trace** (*source-traced*):
- Each `write` that ends in ESC pushes one `u64`. The field comment admits up to "one per queued byte", and the lane holds 64 KiB.
- So when input is paused (output above 80 KiB, or `reads_ready`), 65,536 one-byte `"\x1b"` writes fill the lane and leave `esc_ends` holding 65,536 × 8 B = **512 KiB**.
- `VecDeque` never shrinks, so that allocation lasts for the console's lifetime.
- §7.4 says the named buffers total "2,400 KiB … 64 KiB of input lane" and says nothing about this side table. The bound the commit's doc edit presents is therefore false.

**Required correction:** either bound the table or document it.
- Bounding it: for example, store `u32` offsets relative to `taken` and coalesce, or cap the entries and drop the cut past the cap with one log line.
- Documenting it: add the table to §7.4's table and total.

**Regression test:** a runtime test that fills the lane with single-ESC writes and asserts `esc_ends.len()` stays within the documented bound.

## Design questions
None.

## Optional improvements
- While `reads_cut` is set, GetNumberOfInputEvents reports N while a PeekConsoleInput with NOWAIT returns 0, for one turn. That is consistent with conhost having already handed the input to the earlier reads, but a short note in §5.10 or §7.4 would explain the brief disagreement.
- `clear_input` resets `echo_held` but not `reads_cut`. That is harmless, because the next `ServeReads` clears it, but resetting both would make the state easier to reason about.

## Checks
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost --offline` (Linux) | 107 passed, 0 failed. `tests/own_pty.rs` has 0 tests on Linux. |
| `cargo clippy --offline -p rusty_conhost --all-targets -- -D warnings` (Linux) | Clean |
| `python3 .github/scripts/changes.py check` | "539 fragment(s) ok", exit 0 |
| Scratch probe (`scratch/probe`, test `probe_k2_strand` added to `sched.rs`) | Passed, printing `plan input=2 poll=false`, which confirms F1's sched half |

## Validation gaps
- **`runtime.rs` is never compiled on Linux** (it is `cfg(windows)`), so I couldn't run its tests: the K2 regression test `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce` and the engine turn. I also couldn't confirm that the K2 test fails at 8ec54b85. Reading the code says it would, because `take_input` there doesn't cut.
- **Clippy for the Windows target couldn't run.** The `x86_64-pc-windows-msvc` target isn't installed, and offline mode can't fetch `windows-sys 0.59`. Windows-only code is therefore not lint-checked.
- **`tests/own_pty.rs` interactive tests and the ConDrv paths weren't run.** They need Windows.
- **F1 needs a Windows probe to confirm end to end:** on the own-pty host, write `"\x1b"` then `"x"` back to back while a raw ReadConsole waits, and check that the read completes without any further input.
