<!-- conhost/1246-r3 run 3, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 288 s, 1596327 tokens, $1.41 -->

# Review of Rusty-Mill/rusty_mill PR 1246 (re-review at 615e0824)

**Verdict: changes required.** K1 and K2 are both fixed. The fix commit adds two P2 defects of its own: an unbudgeted ESC-end table in the input lane, and §7.2 docs that no longer match the turn loop.

- **Base:** 66f4bc38c8181fd669c58f8e34df385a6afa895e
- **Head reviewed:** 615e082460958ae36fbe19aaf705bc6898e70f5c. `git fetch origin pull/1246/head` gives FETCH_HEAD = HEAD = 615e0824, so the review is pinned.
- **Delta since the last review:** one commit, 615e08246, on top of 8ec54b85. I also read the surrounding read path, the runtime lanes and the design rows at the head.

## Prior findings, re-checked

**K1 (P1): all full read replies were built before any was completed. Fixed.**
- **Capacity cap:** a read's capacity is now capped at `READ_REPLY_CAP` = 1 MiB (`console/read.rs` 320–323 and 384–386).
- **Per-call budget:** `serve_reads` (`console/read.rs` 449–478) always tries the first read. It stops before any later read when `built + capacity > 1 MiB`, sets `reads_cut`, and `sched::turn` then runs `Event::ServeReads` before taking new input.
- **Progress:** every cut follows at least one completed read, so the loop always moves forward.
- **Ordering:** `HangUp` clears `reads_cut`. New reads and GetConsoleInput queue behind the remaining reads through `reads_behind()`.
- **Executed:** I added the new test's scenario to a scratch worktree at 8ec54b85: 8 raw `ReadConsoleW` calls declaring `u32::MAX-64`, fed 513 records with repeat count 65535. One call built 67,238,910 B for one completion (assertion red). At the head, `one_call_builds_at_most_one_reply_budget_and_the_rest_follows` passes. So the test guards the fix.
- **Residual ruled out:** I checked whether the held `Engine::reply` (built in step 8 of turn N) can be up to 1 MiB while turn N+1's `ServeReads` or `Input` builds another 1 MiB. It can't, because:
  - Waiting reads exist with input queued only under `echo_held` or `reads_cut`.
  - In both states a new read waits, and a direct reply comes only from `take_pending`. That is one line's rest, at most 32 Ki units, about 64 KiB.
  - Record replies are at most about 4,096 × 3 × 20 B.

**K2 (P2): write boundaries were lost when completed writes coalesced. Fixed (source-traced).**
- **Recording:** `Shared::write` (`runtime.rs` 170–176) records the lane offset where a write ending in ESC ended.
- **Cutting:** `take_input` (`runtime.rs` 264–279) cuts the slice there and returns `end = true`.
- **Invariant:** `at >= taken` always holds, because any drain that would pass `at` is shortened to `at` and pops the entry, so the `u64` subtraction can't underflow.
- **Large writes:** a write longer than 16 KiB is first sliced with `end=false`, then cut at its ESC.
- **Test:** `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce` would return `"\x1bx\x1b[A"` in one slice at 8ec54b85, so it goes red there. It is Windows-only (`runtime` is `cfg(windows)`), and I could not run it.

## Blocking findings

### F1 (P2): the new `esc_ends` table is a store with no row in the §7.4 caps, and it can hold 512 KiB beyond the 64 KiB lane cap
- **Where:** `crates/libs/ui/rusty_conhost/src/runtime.rs:101-107` and `:172-176`; `docs/projects/term/conhost/design.md:612` (input lane row) and `:621` (memory total).
- **Trigger:** the engine stops draining the lane (output above 80 KiB because the terminal's reader stalls, or `reads_ready`). Meanwhile the terminal makes many separate `write`s that each end in ESC: held-down Escape autorepeat, or a mux forwarding single-byte `\x1b` packets.
- **Failure (source-traced):**
  - Each such write pushes one `u64`. Entries are bounded only by queued bytes, so at most 65,536 × 8 B = 524,288 B.
  - `VecDeque` never shrinks, so that capacity stays allocated for the life of the console.
  - §7.4 caps the input lane at "64 KiB of pending bytes". The per-console total at line 621 (3.3 MiB) does not include this table.
- **Grading:** terminal input is not hostile but must stay within the caps. I graded this P2 rather than P1 because the excess is bounded and the defect is an unlisted store, not an unbounded one.
- **Correction:** store ESC ends at no more than the lane's own scale and add a §7.4 row. Options:
  - a bitset parallel to the lane: 64 Ki bits = 8 KiB;
  - `u16` offsets relative to `taken`;
  - or keep the `u64` deque but `shrink_to_fit` once it empties, and document 512 KiB in §7.4.
- **Regression test** (Windows `runtime` test): perform 65,536 single-byte `write(b"\x1b")` calls without draining, then assert the table's footprint (for example `esc_ends.capacity() * size_of::<u64>()`, or the bitset length) is ≤ the budget documented in §7.4.

### F2 (P2): §7.2 and the `sched` module doc no longer describe the turn
- **Where:**
  - `docs/projects/term/conhost/design.md:540`: step 4 takes input only while output ≤ 80 KiB, and doesn't mention `!reads_ready`.
  - `docs/projects/term/conhost/design.md:543`: the `until = now` condition doesn't include `reads_ready`.
  - `docs/projects/term/conhost/design.md:554`: "Bounded turn length … at most one packet … plus 16 KiB of input and the constant-size control lane".
  - `crates/libs/ui/rusty_conhost/src/sched.rs:5-9`: "take the control lane, handle a child exit, take at most one slice of terminal input…".
- **What the code does (source-traced):**
  - `sched::turn` (`sched.rs` 86–99) and `Engine::turn` (`runtime.rs` 549–551) add a `ServeReads` step between child exit and input.
  - That step suppresses input while reads are owed input and forces `poll`.
  - A turn can now build up to 1 MiB of replies in `ServeReads`, and up to 1 MiB more in each of the `OutputDrained` and `Input` serves. That is still bounded, but it is not what line 554 says.
  - `take_input` also now cuts at ESC-ending writes, which no design text records.
- **Impact:** docs disagree with the code. The §7.4 row at line 614 was updated; §7.2, which defines the turn order the `sched` tests claim to enforce, was not.
- **Correction:** in the §7.2 pseudo-code, add step 3½ `if reads_ready: handle(ServeReads)` and add the `!reads_ready` guard to step 4. Restate the turn bound to include up to 1 MiB of reply data per `handle`. Record the ESC-end slice rule in §7.2 step 4 or §5.5. Update the `sched.rs` module doc to match.
- **Regression test:** the existing `reads_owed_input_are_served_before_new_input_and_after_control` already pins the code. This finding is for the docs.

## Design questions
- **Inconsistent answers while reads are owed input:** while `reads_cut` or `echo_held` is set, a `PeekConsoleInput` or `ReadConsoleInput` with NOWAIT returns 0 records while `GetNumberOfInputEvents` reports records. It lasts one turn under `reads_cut` and until the reader drains under `echo_held`. A client polling with `Console.KeyAvailable`-style peeks sees a brief false. This is recorded in §7.4, so it is not a defect. I don't expect a real client to be hurt, but it is not conhost behaviour.

## Optional improvements
- `reads_cut` stays set when `end_reads` (handle close or disconnect) removes every remaining read. That costs one extra polled turn with input held back. Clearing it in `end_reads` when `reads` is empty would be tidier.
- The fixture grammar gained `serve-reads`, but no `tests/fixtures/*.txt` step file uses it. A small fixture would give the budget path recorded-trace coverage.

## Checks performed
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | ran, 107 passed, 0 failed, including the two new `console::read` tests and the new `sched` test |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux) | ran, no warnings or errors. It finished in 0.21 s, so it reused cached results |
| `python3 .github/scripts/changes.py check` | ran, "539 fragment(s) ok", exit 0 |
| K1 probe at 8ec54b85 (scratch worktree, since removed) | executed, red: 67,238,910 B built in one call; the same scenario passes at the head |

## Validation gaps
- **Windows code not built or run:** `runtime.rs` (the K2 fix, the `ServeReads` wiring, and the new `take_input` test) and `tests/own_pty.rs` are `cfg(windows)`. Only the Linux target is installed here, so they were neither compiled nor run, and I did not see their Windows CI or clippy results.
- **conhost source not consulted:** cloning microsoft/terminal at `09beddc6` was refused by the sandbox (github.com 403), so this pass has no conhost citations. One parity question is open: the new per-write ESC cut is stronger than conhost, which, as I understand it, flushes a trailing ESC per `ReadFile` chunk in `VtInputThread` and `StateMachine` when it processes a string. It would differ only for a terminal that splits `ESC` from its CSI across two writes. rusty_term's paste and key paths write whole sequences, so I found no real client affected. Two things would settle it:
  - read `src/terminal/parser/StateMachine.cpp`'s end-of-string flush and `InputStateMachineEngine::FlushAtEndOfString` at `09beddc6`;
  - add a probe to `examples/conhost_probe.rs` under ConPTY that writes `"x\x1b"` and then `"[A"` as two writes in quick succession, and reads the resulting records.

**Verdict: changes required** (F1 and F2, both P2).
