<!-- conhost/1246-r3 run 1, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 290 s, 1159361 tokens, $1.40 -->

# Review of PR 1246 (Rusty-Mill/rusty_mill) at head 615e0824

Both earlier findings are fixed, but the fix commit adds three P2 defects (documentation and one unrecorded divergence), so the verdict is **changes required**.

- **Repository:** Rusty-Mill/rusty_mill, PR 1246
- **Base:** 66f4bc38c8181fd669c58f8e34df385a6afa895e
- **Reviewed head:** 615e082460958ae36fbe19aaf705bc6898e70f5c. I fetched `pull/1246/head`, and its tip equals this SHA.

## Earlier findings at this head

**K1 (P1), all full read replies built before any is completed: resolved, confirmed by running it.**
- A read's capacity is now capped at `READ_REPLY_CAP` = 1 MiB (`console/read.rs:320-323, 384-386`).
- `serve_reads` (`read.rs:449-478`) stops once the replies it has built plus the next read's capacity would pass 1 MiB. It sets `reads_cut`, and `sched` then serves the remaining reads on the next turns before taking new input (`sched.rs:85-99`, `runtime.rs:549-551`).
- Every reply in one `handle` call is completed inside that call (`runtime.rs:657-660, 684`), so 1 MiB really is the peak.
- Each pass completes at least one read (the `built > 0` guard), so the deferral always makes progress and can't spin.
- `close()` clears `reads_cut`.
- **Run at the old head:** I ported the scenario to 8ec54b85 (8 raw reads declaring `u32::MAX-64`, plus 513 win32 records each repeated 65535 times). It **fails there, building 67,238,910 B in one call**. The equivalent in-tree test `one_call_builds_at_most_one_reply_budget_and_the_rest_follows` passes at the head.

**K2 (P2), write boundaries lost when completed writes coalesce: resolved by reading the code; the test was not run.**
- `write` records where each write ending in ESC ends (`runtime.rs:170-176`), and `take_input` cuts its slice there (`runtime.rs:264-279`).
- The offset arithmetic can't underflow: `take_input` is the only thing that drains the lane, and `n` never passes the first recorded end.
- A write split by a full lane only records its end once its last byte is in, so the position is still right if another thread's write lands in between.
- `runtime.rs` is `cfg(windows)`, so the new test `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce` doesn't compile on Linux. I could not run it here or at the old head.

## Blocking findings

**F1 (P2): the design's normative loop in §7.2 doesn't describe the new serve-reads step.**
- **Where:** `docs/projects/term/conhost/design.md:533-554`, against `sched.rs:85-99` and `runtime.rs:549-551`.
- **Trigger:** any turn where `reads_ready` is true.
- **Problem (traced in the source):**
  - §7.2's 8-step loop has no "serve the reads left at the reply budget" step between steps 3 and 4.
  - It doesn't say that input is skipped while reads are owed, or that `until = now` while `reads_ready` holds.
  - "Bounded turn length" still lists only one packet, 16 KiB of input and the control lane. A turn can now also build up to 1 MiB of read replies in `OutputDrained` and 1 MiB more in `ServeReads`/`Input`.
  - Only the §7.4 table row was updated.
- **Correction:** add the step, the input skip and the poll condition to the loop, and the read-reply work to the turn bound.
- **Test:** the code side is already pinned by `reads_owed_input_are_served_before_new_input_and_after_control`. This is a documentation correction only.

**F2 (P2): `esc_ends` is a new store with no cap in §7.4.**
- **Where:** `runtime.rs:101-107, 173-176`.
- **Trigger:** the engine stops draining the lane, so the output is past 80 KiB with a stalled reader. Meanwhile the terminal keeps writing single ESC bytes, for example the user holding Escape on auto-repeat.
- **Problem (traced in the source):**
  - Each 1-byte write pushes one `u64`. The 64 KiB lane allows 65,536 entries.
  - A `VecDeque<u64>` holding that many is 524,288 B (512 KiB), and it never shrinks.
  - This store isn't in either §7.4 table, and the "2,400 KiB … 3.3 MiB in all" total leaves it out. §7.4:632 says "Any store a later PR adds names its cap in one of these tables in the same PR."
- **Correction:** shrink the store, for example to `u16` offsets relative to `taken` (the lane is at most 64 KiB) or an 8 KiB bitmap over lane positions, and/or add it to §7.4 and the total.
- **Test:** a runtime test doing 65,536 one-byte `\x1b` writes, asserting the store's bytes are within the documented cap.
  - Better: move the `Lanes` logic out of `cfg(windows)` so this test and K2's run on Linux.

**F3 (P2): unrecorded divergence; a non-waiting GetConsoleInput (peek) returns 0 records while input is queued.**
- **Where:** `read.rs:325` (`reads_behind()` includes `reads_cut`) and `read.rs:271`.
- **Trigger:** waiting reads that need more than 1 MiB of replies in one call, then another client calls PeekConsoleInput (`READ_NOWAIT|READ_NOREMOVE`) or a non-waiting GetConsoleInput before the deferred reads are served.
- **What happens:**
  - The non-waiting GetConsoleInput answers `NumRecords = 0`; the in-tree test `reads_behind_the_budget_keep_their_order_and_a_hang_up_ends_them` asserts exactly this, and it passes.
  - In the same state, GetNumberOfInputEvents reports `input.len() > 0` (`read.rs:271`; traced, not run).
- **Why it's a divergence:** conhost satisfies waiting reads as soon as input arrives, so it never has queued input alongside starved readers. A polling client such as .NET `Console.KeyAvailable`, which uses PeekConsoleInput, sees "no key" while the count says otherwise.
- **Not documented:** §7.4's row only says the next turns serve the rest "before taking new input". It doesn't cover new reads or peeks being answered as if the queue were empty, and §5.10 doesn't list it.
- **Correction:** record it in the §7.4 row or as a §5.10 entry. Changing the behaviour would reorder input, so documenting it is the right fix.
- **Test:** the existing test already pins the behaviour.

## Design questions
None.

## Optional improvements
- Moving `Lanes`/`take_input` into a platform-independent module would let the K2 logic be tested on Linux.

## Checks performed

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | 107 passed, 0 failed; `own_pty` has 0 tests on Linux |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux) | clean, but `cfg(windows)` code, including `runtime.rs`, isn't compiled on Linux |
| `python3 .github/scripts/changes.py check` | 539 fragments ok |
| K1 probe at 8ec54b85 (scratch worktree, since removed) | fails, 67,238,910 B built in one call |
| New tests at base 66f4bc38 | can't compile there: `read.rs`, `ServeReads` and `View::reads_ready` don't exist, so they guard by construction |

**Validation gaps**
- **No conhost source citations.** Cloning microsoft/terminal was blocked: the sandbox denied network access to github.com. So I couldn't confirm against conhost's own code that an ESC at the end of a read chunk is taken as the Escape key (the parity basis for K2), or that a peek never sees queued input while reads are starved (F3). Opening github.com to the sandbox would let me settle both.
- **Windows tests not run.** That includes `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce` and the rest of the `runtime.rs` tests, Windows clippy, and `tests/own_pty.rs`. The ci.yml Windows matrix should cover them, but I haven't seen its result for this head.
- **Windows probe for F3:** a probe client parks a ReadConsoleW with a buffer over 1 MiB, the terminal feeds repeat-count win32 records, and a second client calls PeekConsoleInput and GetNumberOfInputEvents in the same turn.

## Verdict
**Changes required** (F1, F2, F3, all P2).
