<!-- conhost/1246-r3 run 1, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 252 s, 1109460 tokens, $1.17 -->

# Review of PR 1246 (Rusty-Mill/rusty_mill): console input, re-review at 615e0824

**Verdict: changes required.** Both earlier findings (K1 and K2) are fixed. Two new P2 findings in the design doc remain.

- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Reviewed head:** `615e082460958ae36fbe19aaf705bc6898e70f5c`. The fetched `pull/1246/head` matches this SHA.
- **Since the last review (8ec54b85):** one commit, 615e08246, "budget reply building per call; cut slices after ESC writes".

## Earlier findings at this head

**K1 (P1), all full read replies built before any is completed: fixed.**
- **The caps:**
  - A read's capacity is now capped at `READ_REPLY_CAP` (1 MiB), at `console/read.rs:320-323` and `:384-386`.
  - `serve_reads` builds at most 1 MiB of replies per call (`read.rs:456-467`). The first read is always tried, and each later read must fit in what is left of the 1 MiB.
- **Memory:** each `handle` call's effects are applied before the next call (`runtime.rs:655-661`), so one call holds at most 1 MiB of replies.
- **Progress:** `reads_cut` is set only after at least one read has completed (`built > 0`). Every ServeReads turn therefore finishes a read or clears the flag, so it can't spin hot. HangUp clears the flag (`console.rs:592-593`).
- **Order:** new reads and GetConsoleInput queue behind the owed reads (`read.rs:325`, `:421`). sched holds back new input while reads are owed (`sched.rs:87`).
- **Tests:** I disabled the cut in a scratch copy. Both `one_call_builds_at_most_one_reply_budget_and_the_rest_follows` and `reads_behind_the_budget_keep_their_order_and_a_hang_up_ends_them` then failed (*executed*).

**K2 (P2), write boundaries lost when completed writes coalesce: fixed** (*source-traced*).
- **Why tracking only ESC is enough:** the decoder uses `end` for one thing only, a lone ESC at the end of a slice (`input.rs:240-248`).
- **Positions are safe:** each recorded end is placed under the lane lock after the write's last chunk (`runtime.rs:171-176`), and the positions only increase. `take_input` never takes past the front entry (`runtime.rs:268-274`), so `at - taken` can't underflow.
- **Validation gap:** the test `a_write_ending_in_esc_stays_its_own_slice_when_writes_coalesce` lives in `runtime.rs`, which is built only on Windows. It didn't run here.

## Blocking findings

**F1 (P2): the new `esc_ends` store has no cap in §7.4.**
- **Where:** `crates/libs/ui/rusty_conhost/src/runtime.rs:101-107` and `:172-176`.
- **What the design requires:** §7.4 says: "Any store a later PR adds names its cap in one of these tables in the same PR." `esc_ends` holds one `u64` for each queued write that ended with ESC. Nothing in §7.4 names it, and the 2,400 KiB total doesn't count it.
- **Trigger** (*source-traced*):
  1. The output queue goes over 80 KiB, so the engine stops taking input.
  2. The terminal makes 65,536 one-byte `write(b"\x1b")` calls (one per Escape key press) before the lane fills at 64 KiB.
  3. `esc_ends` then holds 65,536 × 8 B = 512 KiB, eight times the lane it indexes. A `VecDeque` keeps its capacity after it drains, so the console holds that memory for the rest of its life.
- **Fix:** either name the cap in §7.4 and add it to the total, or shrink the store. For example, a bitset over the 64 KiB lane costs 8 KiB, or `u16` offsets from the lane head cost at most 128 KiB.
- **Regression test** (Windows runtime layer): write 65,536 single ESCs with the engine stopped, and assert that the store stays within its stated cap.

**F2 (P2): §7.2's loop and fairness text no longer match the engine.**
- **Where:** `docs/projects/term/conhost/design.md:533-555`, against `sched.rs:86-99` and `runtime.rs:549-551`.
- **What changed in the code:**
  - The turn has a new step, ServeReads, between child exit and input.
  - Step 4 now also skips input while `reads_ready` is set.
  - `poll` is now set while reads are owed.
  - A turn can build up to 1 MiB of replies besides its one packet.
- **What §7.2 still says:** step 4 takes input whenever "the output queue is ≤ 80 KiB". `until = now` is set only when "steps 2–4 left work". "Bounded turn length" counts only one packet, 16 KiB of input and the control lane. Only the §7.4 row was updated.
- **Fix:** add the step to the §7.2 pseudo-code, its `until` rule and the bounded-turn-length paragraph.
- **Test:** `sched::tests::reads_owed_input_are_served_before_new_input_and_after_control` already covers the ordering, so this is a doc fix.

## Design question

§7.4 decides that reads left over at the 1 MiB budget are served "the next turns … before taking new input". It doesn't say what happens to packets that arrive in between. In conhost, all waiting reads are served together when input arrives, before the next API message is handled.

A probe in my scratch copy (*executed*) showed the difference:
1. Four raw 1 MiB W reads are waiting; 513 repeat-65535 records arrive.
2. After the first read completes, `GetNumberOfInputEvents` returns 505 (`[249,1,0,0]`).
3. A `FlushConsoleInputBuffer` from any client then throws away input that the other three reads were owed.
4. Only 1 of the 4 reads is served, and 3 are still waiting.

PeekConsoleInput with NOWAIT returns 0 records in that window. The trigger needs more than 1 MiB of replies at once, and I found no real client that hits it. The owner should decide whether this goes into §5.10, or whether a call that changes the input (Flush) or reports on it (Peek, GetNumberOfInputEvents) should wait behind the owed reads.

## Optional improvements

- `serve_reads` compares `built` (the bytes actually produced) with `r.capacity` (the size the client declared). A read with a large declared buffer ends the pass even when little input is left, and the next turn's input waits one extra turn. It's harmless, but checking whether any input remains would avoid that turn.

## Checks

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | 107 passed, 0 failed |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` | clean |
| `python3 .github/scripts/changes.py check` | 539 fragments ok |
| Scratch copy with the budget cut disabled | both new K1 tests failed as they should |
| Scratch probe: Flush between budget turns | ran; result under the design question |

The scratch worktree has been removed, and the reviewed checkout is unchanged at the head SHA.

**Validation gaps:**
- **Windows-only tests:** nothing in `runtime.rs` runs on Linux, including the K2 test and the engine's ServeReads wiring, and no Windows target is installed. A Windows `cargo test -p rusty_conhost` log is needed.
- **Windows probe:** I couldn't run the `own_pty` integration tests. A probe through `examples/conhost_probe.rs` should check that Escape followed by typing decodes the same when the terminal's writes coalesce.
- **CI:** I haven't seen the PR's CI results myself.
