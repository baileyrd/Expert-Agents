# conhost/1246-r2 (generated variant): claim verification

I graded these runs against `bench/cases/conhost/1246-r2/answer_key.toml`. The code came from the scratch checkouts:
- the head, `8ec54b85`;
- current main, `ebb92e28`;
- `615e0824`, `2dfc3a6c` and `b5b1dda4`, read to see what later rounds changed.

Only blocking findings are graded. Every graded claim is new in the fix commit 8ec54b85. None of them was in the r1 grading or the baseline grading.

## Key matches

| Key | Runs | Notes |
|---|---|---|
| K1 | none | No run names reply buffers piling up across the reads served in one `handle` call. Every run checks the cooked-echo reserve (r1 K1) and reports it fixed. |
| K2 | none | No run names two completed writes coalescing. Runs 1–5 trace the `writing` counter and report r1 K2 fixed. Run 4's design question about a lone ESC at the terminal's write boundary is close to K2, but it does not describe coalescing, and design questions are not graded. |

## Probes

I added one temporary module, `grader_probes`, at the end of `console::tests` in `src/console.rs`. It ran at 8ec54b85 and again at main ebb92e28 with `cargo test -p rusty_conhost --lib --offline grader_probes`. Afterwards I restored the file from a copy, and `git status` shows both trees clean. Every probe gave the same result at both commits:

| Probe | Result |
|---|---|
| `probe_cache` | ANSI GetConsoleInput with count 1 on U+1F600 returns 1 record, and `cached_records.len()` is 3. |
| `probe_held_unit` | Raw mode. A win32 record with a lone high surrogate (55357), then `a`, then ReadConsoleA with a 3-byte buffer. The read returns `EF BF BD`, `held_unit` is `a`, `input_available` is false, the event count is 0 and a peek returns 0. A waiting ReadConsoleInputW then completes with the later `y`. |
| `probe_held_echo_count` | Four prefilled cooked reads, then Escape+`x`+CR four times in one slice. 3 reads complete, `echo_held` is true, `input_available` is true, the count is 3 and a peek returns 0. |
| `probe_end_reads_strand` | The same held echo on handle A, then a raw ReadConsoleW waiting on a created handle B. CLOSE_OBJECT on A ends the held read with 257 (STATUS_ALERTED) and clears `echo_held`. The raw read on B stays waiting with 3 records queued, and completes only after the next drain. |
| `probe_evict` | Queue held at 80 KiB − 500, then 4,000 `a`. The read is held with 3,499 records left. A 1 KiB drain plus OutputDrained leaves 2,475. A slice of 3,000 `b` plus CR then fills the queue to 4,096, and InputDropped is logged. The line returns 2,619 `a` and 3,000 `b`. |

## Non-key claims (all real and still on main)

- **ANSI record cache is 3 records, but the doc says 2.** Runs: r1 F1, r3 N2, r5 N4. Verdict: real, executed (`probe_cache`).
  - Since K3, `read_records` gives the low half's record the pair's 4 UTF-8 bytes (`read.rs:651-676`). `split_off(1)` then leaves 3 records.
  - design.md:614 (main :620) still says "3 bytes, 2 records".
- **`held_unit` hides a key.** Runs: r3 N1, r4 F1, r5 N3. Verdict: real, executed (`probe_held_unit`).
  - `next_text` (`read.rs:565-580`) pops the next unit into `held_unit`.
  - Three things ignore `held_unit`: `sync_input_available` (`console.rs:551-552`), the event count (`read.rs:262`) and `read_records`.
  - The trigger needs malformed input, a lone high surrogate. That is narrow, but the reviewers' traces are right.
  - Run 1 raises the same point only under optional improvements, so it is not graded there.
- **Held echo: the input event and the count disagree with the peek.** Runs: r2 F2, r3 N3, r4 F2. Verdict: real, executed (`probe_held_echo_count`).
  - While `echo_held` is set, GetConsoleInput returns nothing (`read.rs:313`). The count and the event still report the queue.
  - design.md §7.4 says only that the input stays queued, and §5.10 has no row for this, so it is not documented behaviour.
  - On main, `reads_behind` (`read.rs:345`) also covers `reads_cut`, but the count and the event are unchanged.
- **`end_reads` strands the reads behind a held read.** Run: r2 F1 (P1). Verdict: real, executed (`probe_end_reads_strand`).
  - `end_reads` (`read.rs:193-215`) never calls `serve_reads`. The DISCONNECT and CLOSE_OBJECT arms are the same on main (`console.rs:734-751`).
  - Impact is lower than P1. The strand lasts until the next OutputDrained, and a hold normally means a drain is coming (r5 N1 is the exception).
- **A held read stalls when the reader drains during the handle call.** Run: r5 N1. Verdict: real, by source trace. The race is in `runtime.rs`, which is Windows-only and outside the Host harness.
  - The hold is decided against `held`, a snapshot of the queue taken at the start of the call (`runtime.rs:610-611`, `OutputQueue::len`).
  - A drain raises `output_drained` only when the read starts above 64 KiB (`runtime.rs:216`).
  - With 61,440 bytes held and at least 17,921 drained during the call, the queue after `push_output` is at most 64,000. No later read then raises the flag.
  - Only OutputDrained and Input call `serve_reads`. On main, `drains_itself` covers `echo_held` only once the master is detached, and `must_poll` does not cover it.
  - This contradicts design.md:606, "goes on when the reader drains".
- **Later slices evict a held read's own input.** Run: r5 N2. Verdict: real, executed (`probe_evict`). The numbers match the run exactly.
  - `sched::turn` takes input whenever the output is at most 80 KiB (head `sched.rs:82`). Main's gate (`sched.rs:96`) also checks `reads_ready`, which is `reads_cut` only and not `echo_held`.

## Judgement calls

- **r5 N2: real, not design_question.** The baseline graded "a single paste over 4,096 records drops its start" as a design_question, because §6 sizes the cap for paste. Here each slice is under 4,096 records. The loss comes only from the new hold keeping records across turns while more input is admitted. §7.4 describes a held read that "goes on" with its input queued, and lane backpressure that blocks writes, not eviction.
- **r2 F1: real at the reviewer's P1, though the impact is a bounded delay.** The trace is exact, and reads that could complete now wait for an unrelated drain.
- **r5 N1: real by source trace, without execution.** The two thresholds and the stale snapshot are unambiguous in the code.
- **No key credit:** I found no partial K2 credit in any run's blocking section.
