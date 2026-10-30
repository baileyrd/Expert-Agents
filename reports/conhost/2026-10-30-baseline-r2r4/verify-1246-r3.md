# conhost/1246-r3 (baseline variant, r2r4 batch): claim verification

I graded these runs blind against `bench/cases/conhost/1246-r3/answer_key.toml`. The code came from the scratch checkouts at head `615e0824` and main `ebb92e28`. Only blocking findings are graded. Every run reports r2's K1 fixed. Runs 1, 3 and 4 report r2's K2 fixed, and runs 2 and 5 say its decoding is fixed but the fix adds a stall. None of those statements is graded. Where a claim is the same as one in the generated variant's grading (`ea-gen/reports/conhost/2026-10-30-generated/verify-1246-r3.md`), I reused that verdict and its reason.

## Key match

| Key | Runs | Notes |
|---|---|---|
| K1 | 2 (B1), 5 (B1) | Both runs name `poll` planned from the lane length before `take_input` (`sched.rs:98`). They show the ESC cut leaving `x` in the lane, then a wait with no deadline after the auto-reset waker was consumed (`runtime.rs:552-574`). That is the key's leftover-input case, so both get `caught`. Both use the `1b`, `x` trigger, which is valid when both writes land before the previous wait returns; the generated run 4 was graded the same way. Neither names the `reads_ready` case. Runs 1, 3 and 4 do not touch K1. |

## Non-key claims (all real and still on main)

- **`esc_ends` has no cap in §7.4.** Runs: r1 F2, r2 B2, r3 F1, r4 F1, r5 B2. Real. This is the same claim as the generated runs' claim, and I reused that source trace (`runtime.rs:101-107`, `:171-176`; `design.md:600-621`, `:632`).
  - r5 B2 is rated P1, and its title says the list "grows with no limit". Its own trace bounds the list at 512 KiB, so I graded it on the uncounted store.
- **§7.2's loop no longer matches the engine.** Runs: r1 F1, r2 B3, r3 F2, r4 F2, r5 B3. Real, same as the generated grading's verdict.
  - r2 B3 also says the §3.3 `Event` enum lacks `ServeReads` and `InputMore`. That is confirmed at `design.md:166-174` and is the same on main.
  - r3 F2 also says the `sched.rs:5-9` module doc lacks the serve-reads step. That is confirmed, and main's doc still lacks it.
- **While `reads_cut` is set, a NOWAIT peek returns 0 but the count reports the queue, and this is not recorded.** Runs: r1 F3, r2 B4. Real, executed (probe below).
  - `get_console_input` returns nothing behind `reads_behind()` (`read.rs:325-326`), while `get_number_of_input_events` reports `input.len()` (`read.rs:271`).
  - Neither the §7.4 row (`design.md:615`) nor §5.10 mentions peeks or the count. The generated r2 grading graded the `echo_held` form of this real for the same reason. The `reads_cut` form is new in 615e0824.
- **The order of reads across a budget cut is untested.** Run: r4 F3. Real, executed (mutations below). The commit message says "Tests: ... order and hang-up while reads are owed input", but `reads_behind_the_budget_keep_their_order_and_a_hang_up_ends_them` checks only that a NOWAIT GetConsoleInput returns 0 and that a hang-up clears the reads.

## Probes

I made every change in `src/console/read.rs` and restored the file from a copy afterwards. `git status` is clean in both trees.

| Probe | Head 615e0824 | Main ebb92e28 |
|---|---|---|
| `grader_probe_peek_count`: `large_reads(4, 1 MiB)`, then GetNumberOfInputEvents and a NOWAIT\|NOREMOVE peek | count 505, peek 0 | count 505, peek 0 |
| M1: `serve_reads` puts the cut read behind the rest (`kept.append(&mut reads); kept.push_back(r)`) | 107/107 pass | 165/165 pass |
| M2: `read_console` waits on `echo_held` only, not `reads_behind()` | 107/107 pass | 165/165 pass |

K1 was not executed, because `runtime.rs` is Windows-only. Run 2 B1 and run 5 B1 match the source at `sched.rs:98` and `runtime.rs:529-576`, and the key's fix `2dfc3a6c` targets the same chain.

## Judgement calls

- **Peek/count (r1 F3, r2 B4): real, not design_question.**
  - The generated r3 grading described §7.4 as accepting that peeks queue behind owed reads. It never had to triage that claim, because its runs filed it only as a design question; its run 5 F1, graded real, reaches the same disagreement by a different trigger (the cut firing on a small build).
  - The §7.4 text says only that "the next turns serve the rest before taking new input". It says nothing about peeks or the count, so the behaviour is not documented as intended.
  - The impact is transient, about one turn per cut.
- **r4 F3, a test gap, graded real.** This follows the r2 baseline precedent (r2 F1, an untested guard, graded real). A stricter grader could leave test-only findings out of the bug count.
- **Doc findings graded real,** as in the generated grading: §7.2 is normative, and §7.4:632 requires new stores to name their cap.
- **Severity:** runs 2 and 5 rate K1 P1 against the key's P2. I kept the reviewers' severities, so the scorer counts 2 over.
- **Not graded:** r3's design question on peek/count, r4's optional note on the same issue, and r5's design question that every ESC at a write's end is now treated as the Escape key. None of them touches K1.
