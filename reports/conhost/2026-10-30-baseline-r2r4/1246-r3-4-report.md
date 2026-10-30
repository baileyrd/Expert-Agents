<!-- conhost/1246-r3 run 4, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 337 s, 1717863 tokens, $1.63 -->

# Review of Rusty-Mill/rusty_mill PR 1246 at head 615e0824: changes required

Both prior findings, K1 and K2, are fixed at this head. The fix commit adds three new P2 defects: a new memory store missing from the cap table, a stale engine-loop description, and no test for read order across a budget cut.

- **Repository:** Rusty-Mill/rusty_mill
- **PR:** 1246
- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Reviewed head:** `615e082460958ae36fbe19aaf705bc6898e70f5c`. The fetched `pull/1246/head` equals this SHA, and no tracked files were modified.

## Prior findings at this head

**K1 (P1), all full read replies built before any is completed: fixed.** *Source-traced.*
- `serve_reads` (`read.rs:449-478`) always serves the first waiting read. After that it stops before any read where bytes already built plus that read's capacity would pass 1 MiB. It then sets `reads_cut` and keeps the remaining reads in their original order.
- Every read's capacity is capped at `READ_REPLY_CAP` (`read.rs:320-323`, `384-386`), so no single reply passes 1 MiB.
- No other event builds more than one large reply in a single core call:
  - a packet builds at most one reply;
  - `OutputDrained` completes parked writes (small replies), then runs one budgeted `serve_reads`;
  - `Input` and `ServeReads` each run one budgeted `serve_reads`.
- The runtime completes each call's replies before the next call (`runtime.rs:654-660`).
- The scheduler serves owed reads before taking new input, and polls meanwhile (`sched.rs:87-98`). New reads and GetConsoleInput wait behind them while `reads_cut` is set (`read.rs:325, 421`). `close` clears the flag.
- Progress is guaranteed: each turn either completes the first owed read or clears the flag.
- **Leftover:** a reply held for the next READ_IO can coexist with the next turn's 1 MiB build. When reads are waiting, though, an immediate reply can only come from a line rest of at most 64 KiB, and the packet-read slot isn't live then, so the 2,400 KiB total still holds.
- **Regression test:** `one_call_builds_at_most_one_reply_budget_and_the_rest_follows` would fail at 8ec54b85, because one call there completes all 8 reads.

**K2 (P2), write boundaries lost when completed writes coalesce: fixed.** *Executed* on a line-for-line copy of `write` and `take_input`.
- Writes `\x1b`, `x`, `\x1b[A` now take as `([1b], end=true)` and then `("x\x1b[A", end=true)`. At 8ec54b85, `take_input` returned all of it as one slice.
- Bookkeeping holds: `taken` and `esc_ends` only increase, and the lane is never cleared, so `at - taken` can't underflow.
- **Regression test:** `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce` lives in `runtime.rs`, which only builds on Windows, so I couldn't run it (validation gap).

## Blocking findings

**F1 (P2): the new `esc_ends` store is not in the §7.4 cap table.**
- **Where:** `runtime.rs:101-107` and `171-176`; `design.md:621` and `632`.
- **Trigger:** terminal writes that each end in ESC pile up while the engine isn't taking input, for example because output is above 80 KiB.
- **Failure scenario** (*executed*):
  - 65,536 one-byte `\x1b` writes fill the 64 KiB lane and give `esc_ends` 65,536 entries with capacity 65,536, which is **524,288 B**.
  - After the lane drains (65,536 turns, one per cut) the capacity stays at 65,536. `VecDeque` never shrinks, so the 512 KiB is held for the console's lifetime.
  - §7.4 says "Any store a later PR adds names its cap in one of these tables in the same PR." The table doesn't list this store, and the "3.3 MiB in all" total (`design.md:621`) leaves it out.
- **Correction:** either
  - add a row ("input-lane write ends: one per queued byte, ≤ 64 Ki × 8 B") and update the total, or
  - bound it more tightly, e.g. merge adjacent ESC-ended writes, use `u32` offsets relative to `taken`, or `shrink_to_fit` once empty.
- **Test:** a `runtime` test that writes `INPUT_LANE_CAP` single ESC bytes and asserts `esc_ends.capacity()` is within the documented cap.

**F2 (P2): the §7.2 engine loop no longer matches `sched::turn`.**
- **Where:** `design.md:534-544` (loop) and `554` ("Bounded turn length"); `runtime.rs:522` says "One turn of design §7.2's loop".
- **Failure scenario** (*source-traced*):
  - The loop lacks the new step that handles `ServeReads` (runs after exit, before input).
  - Step 4 still says input is taken whenever output is ≤ 80 KiB. The code also requires `!reads_ready` (`sched.rs:87`).
  - Step 6's "until = now" rule leaves out `reads_ready` (`sched.rs:98`).
  - The "Bounded turn length" bullet leaves out the up-to-1 MiB `ServeReads` step.
  - Only the §7.4 table row mentions any of this.
- **Correction:** add the step to the §7.2 loop and update step 4, the poll rule and the fairness bullet.
- **Test:** none needed beyond `sched::tests::reads_owed_input_are_served_before_new_input_and_after_control`, which already pins the order. This is a docs fix.

**F3 (P2): read order across a budget cut is untested, though a test name and the commit message claim it.**
- **Where:** `read.rs:456-460` and `421`; the test at `read.rs:1119`.
- **Failure scenario** (*executed*, mutations in a scratch copy): all 107 tests still pass under either mutation.
  - **M1:** put the cut read behind the rest (`kept.append(&mut reads); kept.push_back(r)`). Reads are then served out of arrival order. conhost's `ConsoleWaitQueue::NotifyWaiters` serves waiters in order, so this would be a regression.
  - **M2:** make `read_console` check only `echo_held`, not `reads_behind()`. A new ReadConsole then takes input owed to earlier reads.
- `reads_behind_the_budget_keep_their_order…` only checks that a NOWAIT GetConsoleInput returns 0 and that hang-up clears the reads.
- **Correction:** add core `Event` tests that fail under each mutation:
  - reads A, B, C wait, with A filling the budget; check that B then C complete across `ServeReads`, in order and with the expected data;
  - a ReadConsole that arrives while `reads_ready()` is set waits, and completes after the owed reads.

## Design questions
None found that hurt a real client. The 1 MiB per-read cap is recorded in §7.4 as a design decision, not a divergence. GetConsoleInput can't hit it: the queue holds at most 4,096 records, about 12 K after ANSI expansion, against a cap of 52 K.

## Optional improvements
- **Transient peek result:** while `reads_cut` is set, a NOWAIT PeekConsoleInput returns 0 records while GetNumberOfConsoleInputEvents reports N. conhost serves all waiters at once and then shows the remainder. In practice this needs win32-input-mode repeat counts large enough to fill 1 MiB, so I'm not counting it as a finding. A §7.4 note would make it explicit.
- **Unused fixture step:** the `serve-reads` step was added to the fixture grammar, but no fixture file uses it. A step file would also cover the event path through replay.

## Failure classes for this commit
- **Containment:** no new `?` paths. `ServeReads` with nothing owed does nothing, including after `close`.
- **Retained memory:** the reply budget holds (K1). `esc_ends` is the gap (F1).
- **Amplification:** one input slice now completes at most 1 MiB of replies per call. The reads still owed don't hold the cut flag forever (see Progress).
- **Boundaries:** ESC at a write's end is now kept as its own slice. A 16 KiB cut before an ESC end gives `InputMore`, and the next take cuts at the ESC.
- **Custody:** unchanged. `HangUp` clears `reads_cut`, and dying or teardown ends the loop because `server` is `None`.
- **Progress:** each `ServeReads` call either completes one read or clears the flag.

## Checks performed
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost --offline` (fresh target directory) | pass: 107 unit tests, integration tests 0 on Linux |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` | pass, no warnings |
| `python3 .github/scripts/changes.py check` | pass, "539 fragment(s) ok" |
| Lane probe (copy of `write` and `take_input`) | K2 case correct; `esc_ends` reaches 524,288 B and keeps it after draining |
| Mutations M1 and M2 | both survive the full suite |

My first test run used a shared scratch `target/` and ran a stale binary from an earlier scratch copy (`prev/`), whose `probe_one_call_budget` test failed. That test isn't in the reviewed tree. I discarded the run and redid it in a clean directory; the results above are from that rerun.

## Validation gaps
- **Windows-only tests not run:** everything under `runtime.rs` (including the K2 test) and `tests/own_pty.rs`.
- **conhost citations unchecked:** network access to github.com was denied, so I couldn't clone microsoft/terminal at `09beddc6`. Two parity points rest on my recollection of these functions, without verified line numbers:
  - in-order serving of waiting reads (`src/host/inputBuffer.cpp` `WakeUpReadersWaitingForData` → `src/server/WaitQueue.cpp` `ConsoleWaitQueue::NotifyWaiters`);
  - a lone ESC at the end of a string becoming the Escape key (`src/terminal/parser/stateMachine.cpp` `ProcessString`, input engine).
- **Proposed Windows probe:** `conhost_probe` with two threads doing ReadConsoleW with 2 MiB buffers, fed win32-input-mode records with repeat counts. It would confirm the replies arrive in order and that process memory stays within the cap.

## Verdict: **changes required**
F1, F2 and F3, all P2.
