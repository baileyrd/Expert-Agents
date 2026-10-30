<!-- conhost/1246-r4 run 5, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 106 s, 562963 tokens, $0.70 -->

I re-checked K1 at this head and it's fixed; I found nothing blocking in the change. One caveat up front: the engine code that holds the fix only compiles on Windows, so on this Linux machine it was never built, linted or run. My conclusion comes from reading the code, not from running it.

**Pin**
- Repository Rusty-Mill/rusty_mill, PR #1246.
- Base `66f4bc38c8181fd669c58f8e34df385a6afa895e`, head `2dfc3a6cd2136d98065b031cf0f65601568dcbd7`. Fetching `pull/1246/head` returned exactly that head.
- The only commit since the reviewed `615e0824` is `2dfc3a6` ("decide the engine's wait on what the turn left"). That commit is what I reviewed in depth; I did not re-review the rest of the PR.

## K1 (P2): the engine waited on a decision made before the turn's events — fixed

- **The fix:** the decision to keep working instead of waiting used to come from the turn's plan, made before its events. `Engine::turn` (`runtime.rs:527-554`) now does the turn's work first. It then asks `sched::must_poll` (`sched.rs:102-104`) using a fresh look at the state: reads still ready, input left in the lane, and output under the 80 KiB pause.
- **Both original cases are now caught** (source-traced):
  - *Reads left ready:* one input slice leaves reads cut at the 1 MiB reply limit. That shows up after the work, so the next turn serves them at once instead of blocking.
  - *Input left behind an ESC write:* say ESC and then `x` arrive as two writes. The input slice stops after the ESC (`runtime.rs:265-271`) and leaves 1 byte, which the fresh look now sees.
- **No hot loop.** Every time `must_poll` says "keep working", the next turn can actually make progress:
  - Its input rule matches the planner's exactly (`sched.rs:84` vs `sched.rs:103`).
  - Serving waiting reads always finishes at least one read (`read.rs:456`, since nothing is built yet when the first read is tried), so the "reads ready" flag can't stay set while nothing happens.
  - A cooked read whose echo is waiting on a full output queue doesn't count as "ready" (`console.rs:522`). It resumes when the reader drains output and wakes the engine (`runtime.rs:229-232`).
  - If the console panics, it shuts down the server (`runtime.rs:748`), so the loop ends rather than spinning.
- **Nothing lost before the wait.** The check runs on the engine thread just before `wait`. Anything a caller thread does in between (a write, a resize, a hang-up, a drain) also signals the waker.
- **The doc matches:** design §7.2 step 6 ("until = now when steps 2–4 left work they could still do") now describes what the code does.
- **The tests would catch a regression** (source-traced against the parent commit's planning rule): both new engine tests at `runtime.rs:944-969` would fail on the old code. In the ESC test the old rule saw all input taken, so it didn't poll. In the reads test the "reads ready" flag only became true after the plan. The new `sched` unit test can't be run against the base because `must_poll` doesn't exist there; it only checks the formula itself.

## Blocking findings
None.

## Design questions
None.

## Optional improvement
`turns_without_another_wake` (`runtime.rs:932-941`) calls `work()` and `must_poll()` directly instead of going through `turn()`. If someone later moved the poll decision in `turn()` back before the work, these tests would still pass. A small test seam that runs `turn()` itself, with a wait that never blocks, would guard the order too.

## Checks
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | **pass**: 108 unit tests, including fixture replay and the new `sched` test; `own_pty.rs` has 0 tests on Linux |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux) | **pass** |
| `python3 .github/scripts/changes.py check` | **pass** (539 fragments ok) |

## Validation gaps
- **Windows code not compiled here.** `runtime.rs`, where the fix lives, only builds on Windows (`lib.rs:33-35`), and only the Linux target is installed. So none of the fix or its tests were compiled, linted or run.
- **Two new engine tests not run:** `reads_a_slice_leaves_ready_are_served_without_another_wake` and `input_left_behind_an_esc_cut_is_taken_without_another_wake`. They need a passing Windows CI run, and Windows clippy with `-D warnings`. I did not see either result myself.
- **The interactive tests in `tests/own_pty.rs` were not run** either; they also need Windows.

## Verdict
**No blocking findings.** This doesn't replace the Windows CI result above, which I couldn't see myself.
