# conhost/1246-r4 (baseline variant, r2r4 batch): claim verification

I graded these runs blind against `bench/cases/conhost/1246-r4/answer_key.toml`. The code came from the scratch checkouts at head `2dfc3a6c`, the key's fix `b5b1dda4` and main `ebb92e28`. Only blocking findings are graded. Every run reports r3's K1 (the stale pre-event poll plan) as fixed; those statements are not graded. Where a claim is the same as one already graded, I reused that verdict and reason: the generated variant's grading (`ea-gen/reports/conhost/2026-10-30-generated/verify-1246-r4.md`), and the r2 and r3 gradings in both directories.

## Key match

| Key | Runs | Notes |
|---|---|---|
| K1 | 2 (N1) | Graded `caught`. N1 names `submit_read` taken from the pre-event view (`sched.rs:94`). Its trace: 64 held reads, then `Input("a")` completes one, so `admits_read(63, ..)` is true while `must_poll` is false, and `wait` blocks with no READ_IO in flight. That is the key's trigger and mechanism. Its proposed fix (recompute admission in `work()` after the events) has the same shape as `b5b1dda4`. N1 is rated P1 against the key's P2, so the scorer counts it 1 over. |

No other run comes near K1:
- Run 1's K1 re-check lists the reader drain, held echo, a packet served after the wait and `reads_cut` spinning. It never mentions admission.
- Run 3's "no busy-spin" pass covers reads ready, queued input and output pause.
- Run 4's pass covers the same three, plus held echo, control flags and child exit.
- Run 5's pass covers reads ready and input left. It says "every work-creating event is now followed by the check", but only for packets served after the wait. It never mentions READ_IO admission.

## Non-key claims (all real; all still on main)

- **Tests bypass `Engine::turn`, so nothing goes red if the K1 (r3) ordering is reverted.**
  - Filed blocking by r1 F5, r4 F1 and r5 K2. Run 3 raises it only as an optional improvement, so it is not graded there.
  - Verdict: real. This is the same claim as generated r2 R1 / r3 F1, and I reused that source trace (`runtime.rs:527-530`, `:934-941`; `sched.rs:274-292`).
  - r5's mutant scenario (an `OutputDrained` wake consumed by the previous wait) is a valid way for the mutant to block. I did not need it for the verdict.
- **r1 F1: Alt+[ / Alt+Shift+O / Alt+] / Alt+Shift+P at the end of a plain-VT write are held.** Real, executed.
  - `escape()` returns `Incomplete` for `[`, `O`, `]` and `P` whatever `end` says (`input.rs:250-267`, `string()` at `:275-289`).
  - This is the same defect graded real in baseline r2 r5 F2 and generated r1 r2 F9, which cite conhost's flush at `stateMachine.cpp:2036-2072`.
  - r1 adds the `ESC P` / `ESC ]` cases, where the typing that follows is swallowed. Baseline r2 r5 filed those only as a design question. The probe confirms them.
- **r1 F2: a paste over 4,096 characters into a waiting cooked read loses its start.** Real, executed. This claim is new; no earlier grading covers it.
  - `terminal_input` stores every piece of the slice (`read.rs:98-118`) before `serve_reads` (`:120`).
  - `Queue::push` drops the oldest record past `MAX_RECORDS` (`input.rs:681-688`).
- **r1 F3: the §7.4 ANSI read cache row says "2 records".** Real, executed. This is the same claim as baseline r2 r1 F1 and generated r1 F1 (`design.md:614`, main `:620`).
- **r1 F4: `esc_ends` is missing from the §7.4 tables.** Real. This is the same claim graded real in both r3 gradings.
  - `2dfc3a6c` changes no doc (`git diff --stat 615e0824..2dfc3a6c`).
  - `runtime.rs:104-107` and `:171-176` are unchanged in substance.
- **r3 R1: the §7.2 loop omits ServeReads, and step 6's poll rule disagrees with `must_poll`.** Real. This is the same claim graded real in both r3 gradings.
  - At the head, `design.md:534-554` is unchanged.
  - `must_poll` (`sched.rs:102-104`) polls on `reads_ready`, which no step 2–4 produces.
  - Run 5's remark that step 6 "matches the code" is not a finding and is not graded.

## Probes

I added `grader_probe_r4` to the `console/read.rs` tests at `2dfc3a6c` and on main `ebb92e28`, ran it with `cargo test -p rusty_conhost --lib grader_probe_r4 -- --nocapture`, and restored the file from a copy. `git status` is clean in both trees.

| Case | Head 2dfc3a6c | Main ebb92e28 |
|---|---|---|
| Cooked `ReadConsoleW` waiting (64 KiB), one `Input` of 5,000 × `a` + CR | 1 completion, 4,096 units, `InputDropped` logged | same |
| Cooked read waiting, `Input("\x1bP")`, then `d`, `i`, `r`, `\r` as separate inputs | 0 completions, nothing echoed | same |
| `Input("\x1b[")` then `Input("a")`, NOWAIT GetConsoleInput | 0 records | same |
| `Input("\x1bO")` then `Input("A")` | 2 records, VK 38 (Up) | same |
| `Input("😀")`, ANSI GetConsoleInput count 1 | `cached_records.len() == 3` | same |

I did not execute K1 or the `turn()` test-gap claim, because `runtime.rs` is Windows-only. Both are graded by source trace.

## Judgement calls

- **r1 F2 graded real, not design_question.**
  - §7.4 (`design.md:499`, `:610`) documents the 4,096-record cap with the oldest dropped, sized for "about 1 s of fast typing or paste".
  - Here, though, a cooked read is already waiting and would take every record into its line, under the parked cap. The loss comes from storing the whole slice before serving reads. That ordering is not documented, and conhost loses nothing.
  - A stricter reading of the documented cap could call this a design question. Run 1's own design question 1, about pastes into one-record readers, is the general by-design case and is not graded.
- **The test-gap claim is graded real** for runs 1, 4 and 5, following the generated r4 grading and earlier "no test goes red" precedent.
- **Severities kept as filed.** r1 F1 and F2 are P1, and r2 N1 is P1 against the key's P2.
- **Run 1's F1 also covers `ESC P` / `ESC ]`.** I graded it as one finding with the already-real `ESC [` / `ESC O` defect, because they share one cause (`escape()` ignores `end` past a lone ESC).
