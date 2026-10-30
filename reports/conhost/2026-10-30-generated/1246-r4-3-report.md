<!-- conhost/1246-r4 run 3, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 109 s, 531729 tokens, $0.59 -->

**Verdict: changes required.** K1 is fixed in the code. One P2 coverage gap remains: no test fails if the fix is undone.

**Repository:** Rusty-Mill/rusty_mill, PR 1246
**Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
**Reviewed head:** `2dfc3a6cd2136d98065b031cf0f65601568dcbd7`. `git fetch origin pull/1246/head` returned this SHA, which is also the checkout's `HEAD`.

**Scope of this round:** I re-checked K1 and reviewed what changed since the last review (615e0824 → 2dfc3a6, one commit touching `runtime.rs`, `sched.rs`, `console.rs` and `console/read.rs`). I read it against design §7.2 and §7.4. I did not repeat the full prediction pass over the whole PR.

## K1 (P2, wait decided before the turn's events): resolved (source-traced)

- **What the fix does.** `Engine::turn` (`runtime.rs:527`) now runs `work()` first (`runtime.rs:558`). Only then does it compute `until = self.must_poll().then(Instant::now)` (`runtime.rs:530`). `must_poll` (`runtime.rs:606`) builds a fresh `View` from the current state: `reads_ready()`, `input_len()` and `output_len()`. It then calls `sched::must_poll` (`sched.rs:102`): `reads_ready || (input > 0 && output <= INPUT_PAUSE_ABOVE)`. That matches design §7.2 step 6: "until = now when steps 2–4 left work they could still do".
- **No hot spin.** `must_poll` asks for exactly what the next turn's plan will do:
  - When `reads_ready` is true, `serve_reads` runs. It sets `reads_cut` (`read.rs:461`) only after building at least one reply (`built > 0`), so every polled turn makes progress.
  - When input is waiting, output is at most 80 KiB and no reads are owed, `turn()` takes `min(input, INPUT_SLICE) > 0` bytes.
  - When output is over 80 KiB, it doesn't poll. Draining that output sends a wake, because the drain wake fires above 64 KiB (`runtime.rs:39`, `runtime.rs:229`) and echo holds above 80 KiB.
- **Echo hold.** An echo hold doesn't need a poll: it is released by `OutputDrained`, which the reader signals by waking the engine.
- **Packets served after the wait.** These are planned at the start of the next turn's `work()`, so they need no poll either.
- **Both scenarios from the original finding now poll:**
  - A final slice that leaves reads ready gives `reads_ready = true`.
  - An ESC write-end cut leaves bytes in the lane, so `input_len > 0`.
- **Old code really did block.** I traced the parent commit: `"\x1b"` then `"x"` gave a plan input of 2 against a lane of 2, so `plan.poll` was false. The engine would then block with 1 byte still queued.

## Blocking findings

**F1 (P2, coverage): no test goes red if `turn()` goes back to deciding its wait before `work()`**
- **Where:** `crates/libs/ui/rusty_conhost/src/runtime.rs:934–969`, testing `runtime.rs:527–530`.
- **Trigger (source-traced):** move `let until = self.must_poll().then(Instant::now);` above `self.work()?;`. That is exactly K1's defect. Every test still passes:
  - The helper `turns_without_another_wake` (`runtime.rs:934`) calls `engine.work()` and `engine.must_poll()` itself. No test calls `Engine::turn` (grep: the only caller is `run`, at `runtime.rs:518`).
  - The two new engine tests therefore check the predicate and the console state, but not where `turn()` evaluates them.
  - The `sched` test `the_wait_polls_on_what_the_turn_left_not_on_its_plan` only checks the pure predicate.
- **Required correction:** add a Windows test in `runtime.rs` that drives the real `turn()`:
  - Use the same setups: `owing_large_reads(8, …)`, and ESC then `x`.
  - Make no wake after the writers' first, and run `turn()` on a worker thread.
  - Assert that the lane empties and `waiting() == 0` within a short deadline.
  - With the ordering reverted, the wait blocks with nothing to wake it, so the deadline expires and the test fails.
  - Alternatively, split out `fn wait_until(&self) -> Option<Instant>`, call it after `work()` in `turn()`, and make the helper use the same sequence.

## Design questions
None.

## Optional improvements
- `View.closed` is always `false` (`runtime.rs:620`). A one-line comment on why would help.

## Checks performed
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | 108 passed, 0 failed |
| `cargo test -p rusty_conhost -- sched` | 8 passed, including the new `the_wait_polls_on_what_the_turn_left_not_on_its_plan` |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux) | clean |
| `python3 .github/scripts/changes.py check` | 539 fragments ok |
| Docs agree with code | design §7.2 step 6 matches the new code; no remaining references to `Turn.poll` / `plan.poll` |

## Validation gaps
- **Windows-only code wasn't compiled or run.** `runtime.rs` is `#[cfg(windows)]` (`lib.rs:34`), and only `x86_64-unknown-linux-gnu` is installed here. So:
  - The Linux clippy and test runs above did not cover the code K1 changed.
  - The new tests `reads_a_slice_leaves_ready_are_served_without_another_wake` and `input_left_behind_an_esc_cut_is_taken_without_another_wake` were not run.
  - To close this, run `cargo test -p rusty_conhost` and `cargo clippy --all-targets -- -D warnings` on Windows CI for this head, and read the results.
- **Neither new Windows test can be run at the parent commit.** Their helper calls `must_poll`, which doesn't exist there. Their claim to guard K1 is source-traced only.
