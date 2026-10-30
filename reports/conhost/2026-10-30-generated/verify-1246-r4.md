# conhost/1246-r4 (generated variant): claim verification

I graded these runs against `bench/cases/conhost/1246-r4/answer_key.toml`. The code came from the scratch checkouts:
- the head, `2dfc3a6c`;
- the key's fix, `b5b1dda4`;
- current main, `ebb92e28`.

Only blocking findings are graded. All five runs report r3's K1 (the stale pre-event poll plan) as fixed. Those statements are not graded.

## Key match

No run catches K1. No report mentions READ_IO admission, `submit_read`, `MAX_WAITING` or re-arming the driver read. I checked this with a grep over all five reports.

## Non-key claim (real, still on main)

- **Nothing goes red if the r3 fix's ordering in `turn()` is reverted.** Runs: r2 R1, r3 F1. Runs 4 and 5 raise the same point as an optional improvement, so it is not graded for them. Verdict: real, by source trace.
  - At the head, `Engine::turn` (`runtime.rs:527-530`) has one caller, `run` (`runtime.rs:518`). I confirmed this with a grep.
  - Both new engine tests go through `turns_without_another_wake` (`runtime.rs:934-941`). That helper calls `work()` and then `must_poll()` itself, so it rebuilds the fixed ordering instead of testing `turn()`.
  - The new `sched` test (`sched.rs:274-292`) checks only the pure predicate.
  - `tests/own_pty.rs` writes only command lines, Ctrl+C and `x`. It never writes an ESC-ended write followed by more input, and it never leaves large reads owed.
  - So moving `runtime.rs:530` above `self.work()?` fails no test.
  - Main is the same. `turn` (`runtime.rs:581-584`) now goes through `grace.until`. Some tests call `engine.turn()` (`runtime.rs:1072`, `:1162`), but none checks the wait deadline, and the grace test (`:1211`) recomputes `grace.until(must_poll())` itself.
  - This follows earlier precedent, where missing-test findings verified this way were graded real (mux 1251-r1 F4, mill-board 1187-r1 F5, 1246-r1 F11).

## Probes

I added none. `runtime.rs` is Windows-only, so I verified by source trace and grep at 2dfc3a6c and on main. Both trees are untouched.

## Judgement calls

- **R1/F1 graded real, not as a validation gap.**
  - The reviewers filed it as a blocking defect in the PR's tests, not as something they themselves failed to run. That is the "no test goes red" class, which earlier rounds graded real.
  - Its impact is low: it is a coverage hole, and the code at the head is correct for r3's K1.
- **No near-misses on K1.**
  - Run 1 is the closest. Its liveness pass lists the paths that could block (output above the pause, echo held, closed console) but never admission.
  - Runs 4 and 5 argue "no hot spin" and "nothing lost before the wait". They cover only the waker and reads_ready, not client requests that need READ_IO armed.
  - Run 2's optional note mentions a READ_IO left in flight in the test helper. That is unrelated to admission.
- **Runs 1, 4 and 5 have no blocking findings and get no `[[finding]]` tables.** Their header comments say why.
