<!-- conhost/1246-r3 run 5, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 281 s, 1136512 tokens, $1.17 -->

I'm requesting changes. K1 and K2 are both fixed at this head, but the K1 fix adds three P2 defects.

**Repository:** Rusty-Mill/rusty_mill, PR #1246
**Base:** 66f4bc38c8181fd669c58f8e34df385a6afa895e
**Reviewed head:** 615e082460958ae36fbe19aaf705bc6898e70f5c. `git fetch origin pull/1246/head` returned exactly this SHA.

## Prior findings at this head

- **K1 (P1), all full read replies built before any is completed: fixed.** `serve_reads` (`src/console/read.rs:449-478`) now stops building replies at 1 MiB per call. Reads it doesn't reach are flagged and served on later turns before new input (`sched.rs:84-99`, `runtime.rs:549-551`). Each read's capacity is also capped at 1 MiB (`read.rs:320-323`, `384-386`).
  - At 8ec54b85, an adapted copy of the new test fails: one Input call built 67,238,910 bytes of replies. At the head it passes (*executed*).
  - Every turn that hits the limit still completes at least one read, so the loop can't spin without progress.
- **K2 (P2), write boundaries lost when completed writes coalesce: fixed in logic.** `write` records where a write ending in ESC ended (`runtime.rs:170-177`), and `take_input` cuts its slice there (`runtime.rs:264-279`). I copied this logic into a standalone program and ran it. It cuts correctly when two writes coalesce, when a write blocks on a full lane, and when the engine takes less than the distance to the cut (*executed*).
  - Nothing else drains the lane, so the recorded positions can't go stale.
  - The new test `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce` is in the Windows-only `runtime` module. I could not run it, so it's listed as a validation gap.

## Blocking findings

**F1 (P2): a few bytes of typing can trip the reply limit, and while the remaining reads wait, other calls see the wrong input state. This divergence from conhost is not in §5.10.**
- **Where:** `src/console/read.rs:456`, `325`, `421`, and `271`.
- **Trigger:** two reads are waiting, and the second has a large buffer (for example a ReadFile with a 1 MiB buffer).
- **Cause:** the check `built > 0 && built + r.capacity > READ_REPLY_CAP` adds the second read's *declared* buffer size to what's already built, not the data it would actually get.
- **Trace (*executed*, a core test added to a scratch copy of the head):**
  1. Raw mode. Read 1 is a ReadConsoleW with a 2-byte buffer; read 2 has a 1 MiB buffer.
  2. The terminal sends `ab`. Read 1 completes with `a` (2 bytes built), and read 2 is left for a later turn with `b` queued.
  3. Results: `completes=1 ready=true`, then `GetNumberOfInputEvents=1`, then a no-wait peek returns 0 records.
  4. Read 2 gets `b` only on the following `ServeReads` turn.
- **Reach:** in the engine, one packet is handled in the same turn after the input step (`runtime.rs:552-592`), so a peek or GetNumberOfInputEvents in that turn sees this state. The input-available event stays set while peeks return nothing, so a WaitForSingleObject-plus-PeekConsoleInput loop wakes spuriously for that turn.
- **conhost, by function name, not line-checked against the pinned commit:** `InputBuffer::WriteInputBuffer` wakes every waiting read in the same notify (`ConsoleWaitQueue::NotifyWaiters`). Read 2 takes `b` at once, and the count reads 0.
- **Docs:** the §7.4 row says a call stops "that reaches the 1 MiB", but here the code stops after building 2 bytes.
- **Correction:** check the limit against data actually built, so a read is set aside only when the bytes already built would push it past 1 MiB. Otherwise, record the deferral as a §5.10 divergence and correct the §7.4 wording.
- **Regression test:** the trace above as a core test in `read.rs`. It should assert that read 2 completes in the same call, or the documented value if the divergence is recorded instead.

**F2 (P2): the §7.2 engine loop in the design no longer matches the code.**
- **Where:** `docs/projects/term/conhost/design.md:534-547`, against `sched.rs:84-99` and `runtime.rs:549-551`.
- **Mismatch:** the loop still lists steps 1–8 with no step that serves set-aside reads. Its step 4 takes input whenever the output queue is ≤ 80 KiB, but the code also skips input while reads are set aside (`!v.reads_ready`). The rule for when `wait` returns at once (`until = now`) also leaves out reads being set aside.
- **Correction:** add the step between steps 3 and 4 and update step 4 and the wait rule.
- **Test:** none needed; this is a docs fix.

**F3 (P2): the new list of ESC write ends is not counted in §7.4's memory bound.**
- **Where:** `runtime.rs:101-107`, `170-177`.
- **Size:** each write ending in ESC adds a 64-bit position, at most one per queued byte. With 64 KiB of single-ESC writes queued, for example while input is paused above 80 KiB of output, that is 65,536 × 8 B = 512 KiB. The `VecDeque` keeps that capacity after it empties.
- **Docs:** §7.4 says the named buffers total "at most 2,400 KiB … 3.3 MiB in all", and this list isn't among them.
- **Correction:** add the list to §7.4 with its bound, or keep only one cut (which is enough, since each `take_input` uses only the first) and shrink the list once it empties.
- **Test:** a test in the `runtime` tests (Windows-only) asserting the bound, if the code changes.

## Design questions

None.

## Optional improvements

- The new `serve-reads` fixture step isn't used by any file in `tests/fixtures/`. A step file for the set-aside reads would cover the console-level behaviour.

## Checks

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | 107 passed, 0 failed |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` | clean; only an unrelated `rusty_tls` manifest warning |
| `python3 .github/scripts/changes.py check` | 539 fragments ok |
| K1 test adapted to 8ec54b85 | fails (67,238,910 bytes built); passes at head |
| K2 standalone program of the slicing logic | correct cuts in all cases above |
| F1 probe at head | trace above |

## Validation gaps

- The `runtime` tests, including the new K2 test, and `tests/own_pty.rs` are Windows-only. I did not run them or see a Windows run log.
- §7.2's Windows integration tests (input event at quiescence, hang-up with a pending read) are not run.
- The F1 conhost behaviour should be confirmed by a Windows probe in `examples/conhost_probe.rs` under ConPTY: two waiting reads with buffers of 2 bytes and 1 MiB, type `ab`, then call GetNumberOfInputEvents. 0 is expected.
- I didn't clone microsoft/terminal, so the conhost citation is by function name only.

**Verdict: changes required** (F1–F3, all P2)
