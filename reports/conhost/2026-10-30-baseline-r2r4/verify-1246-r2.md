# conhost/1246-r2 (baseline variant, r2r4 batch): claim verification

I graded these runs blind against `bench/cases/conhost/1246-r2/answer_key.toml`. The code came from the scratch checkouts at head `8ec54b85` and main `ebb92e28`. Only blocking findings are graded. Every run reports r1's K1, K2 and K3 as fixed and makes no still-open claim, so nothing from the prior round is graded.

## Key matches

| Key | Runs | Notes |
|---|---|---|
| K1 | none | No run names reply buffers piling up across the reads served in one `handle` call. |
| K2 | none | No run names two completed writes coalescing. Every run traces the `writing` counter and says r1 K2 is fixed. |

## Probes

I added a temporary `grader_probes` module inside `console::read::tests`. It ran at 8ec54b85 and at main with `cargo test -p rusty_conhost --lib --offline grader_probes`, and both gave the same results. I restored `read.rs` from a copy afterwards, and `git status` is clean in both trees.

| Probe | Result (head = main) |
|---|---|
| `probe_echo_off_lone_high` | Mode `LINE_INPUT \| PROCESSED_INPUT` (no echo), a waiting ReadConsoleW, then a win32 key-down for 55357 followed by CR. The terminal VT is `ef bf bd 0d 0a`. |
| `probe_cache_five` | Key-downs for 0xD83D and then U+20AC, then an ANSI GetConsoleInput with count 1. It returns 1 record, and `cached_records.len()` is 5. |
| `probe_raw_behind_held` | 4 prefilled cooked reads are held, then `set_mode(RAW)`, then ReadConsoleW. The read waits (held). |
| `probe_alt_bracket` | `ESC [` and then `A`, in separate Inputs, give VK 0x26 down/up. `ESC O` and then `P` give VK 0x70. |
| guard mutation | At head, `if self.echo_held` became `if false && ...` (read.rs:407). At main, `reads_behind()` became `reads_cut` (read.rs:440). All original lib tests still pass (104 at head, 165 at main). `probe_raw_behind_held` then returns `information=4`, bytes `78 00 0d 00`. |

## Non-key claims (all real and still on main)

- **ANSI record cache is 3 records, but the doc says 2.** Runs: r1 F1, r2 F2, r4 F2, r5 F3. **Real.** This is the same claim as generated r1 F1 / r3 N2 / r5 N4, and I reused that verdict (`probe_cache`).
  - Run 3 F1 makes the same doc claim with a different trigger: a lone high surrogate followed by a 3-byte character leaves 5 cached records. I confirmed that by execution.
  - Run 4 F2 adds that `held_unit`, `record_utf8` and `vt_utf8` are in no §7.4 table, although design.md:632 requires every new store to be listed there. A grep of main's design.md finds none of them.
- **Design §3.3 / §7.2 omit `Event::InputMore`.** Runs: r1 F2, r2 D3. **Real.**
  - The Event enum at design.md:166-174 lists only `Input`, and §7.2 step 4 (:540, main :546) says `handle(Input)`.
  - `InputMore` exists in the code at console.rs:120 (main :135), and runtime.rs sends it.
  - design.md on main mentions neither `InputMore` nor the rule that a write's end decides what a trailing ESC means.
  - Run 3 raises this only under optional improvements, so it is not graded there.
- **The packet-path `echo_held` guard is untested.** Run: r2 F1. **Real (a test gap).** I executed the mutation and the probe; see the table above.
  - `a_read_behind_held_echo_does_not_take_its_input` issues a cooked `read_w`, and that read's own echo check holds it. So the test passes without the guard, and its comment "a raw read waits behind them" is wrong.
  - The same is true on main for the `echo_held` half of `reads_behind()`.
- **With echo off, a dangling high surrogate is echoed as U+FFFD.** Run: r4 F1. **Real**, executed.
  - `Line::finish` flushes `utf8` into `newline` (cooked.rs:143, the same on main).
  - `finish_line` pushes `newline` without the echo gate (read.rs:531-536).
  - The doc comment allows only `\r\n` to be shown without echo.
- **Held ANSI unit is invisible and delivered out of order.** Run: r5 F1. **Real.** This is the same claim as generated r3 N1 / r4 F1 / r5 N3, and I reused `probe_held_unit`.
- **Alt+[ / Alt+O at the end of a write wait for the next key.** Run: r5 F2. **Real**, executed at head and main.
  - It is the same defect as the generated r1 grading's r2 F9, which was graded real with conhost `stateMachine.cpp:2036-2072`.
  - The new `end` flag covers only a lone ESC.

## Judgement calls

- **r2 F1 graded real, not excluded as a validation gap.** The "validation gaps are not graded" rule covers the reviewer's own unchecked areas. This finding is a defect in the PR's tests: the guard is provably untested, and the test's comment is false. The r1 gradings set the precedent by grading missing tests as real. A stricter grader might leave test-only findings out of the bug count; it is not a runtime bug.
- **Doc-only findings (cache row, InputMore) graded real.** This follows the earlier gradings, which graded the stale Appendix A text and the §7.4 row as real.
- **r3 F1 (5 records) shares a verdict with the 3-record claim.** It is the same doc defect, and its stronger figure is also correct.
- **No partial key credit.** No run's blocking section touches reply accumulation or write coalescing. Run 1's optional item and run 5's F2 concern ESC sequences at a write's end, not two completed writes merging.
- **Not graded (non-blocking):** every run's design question that the held-echo count/event disagrees with peek. The generated variant graded the same issue as a blocking finding (real).
