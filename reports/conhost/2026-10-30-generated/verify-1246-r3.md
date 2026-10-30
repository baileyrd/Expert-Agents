# conhost/1246-r3 (generated variant): claim verification

I graded these runs against `bench/cases/conhost/1246-r3/answer_key.toml`. The code came from the scratch checkouts:
- the head, `615e0824`;
- the key's fix, `2dfc3a6c`;
- the r4 fix, `b5b1dda4`;
- current main, `ebb92e28`.

Only blocking findings are graded. Every run reports both r2 key findings fixed (or, for K2, fixed in decoding). None of those statements is graded. Every graded non-key claim is about code or docs that are new in 615e0824, so none of them came up in the r1, r2 or baseline grading.

## Key match

| Key | Runs | Notes |
|---|---|---|
| K1 | 2 (F1), 3 (B1), 4 (F1) | Each run names the same chain: `poll` is planned from the lane length before `take_input` (`sched.rs:98`), and `until = plan.poll.then(..)` (`runtime.rs:574`) is never revised after the ESC cut leaves bytes in the lane. The auto-reset waker has already been consumed, so the wait blocks. That is the key's leftover-input case, so all three get `caught`. No run names the `reads_ready` case. |

Runs 2 and 3 use three writes (`1b 1b 78`). Run 4 uses two (`1b`, then `x`). Both triggers are valid. A turn runs `take_control`, then plans, takes, and waits (`runtime.rs:529-576`). If `1b` and `x` both land before the previous wait returns, there is one signal, and that wait consumes it. The next turn then plans `input=2, poll=false`, the cut returns only ESC, and the wait blocks. Runs 2 and 3 need the second ESC only because their writes land during the turn, before `take_control`.

## Non-key claims (all real and still on main)

- **`esc_ends` has no cap in §7.4.** Runs: r1 F1, r3 B2 (second half), r4 F2, r5 F3. Verdict: real, by source trace.
  - At the head, `Lanes::esc_ends` (`runtime.rs:101-107`) gets one `u64` for each write that ends in ESC (`runtime.rs:171-176`). Its own comment says that can be one per queued byte, so 64 Ki × 8 B = 512 KiB. A `VecDeque` keeps its capacity once it empties.
  - The §7.4 table and the total (`design.md:600-621`) don't mention the store. `design.md:632` says that any store a later PR adds names its cap in the same PR.
  - Main is the same (`runtime.rs:101-107`, `design.md:628`, `:639`).
- **§7.2's loop no longer matches the engine.** Runs: r1 F2, r3 B2 (first half), r5 F2. Verdict: real, by source trace.
  - The normative loop (`design.md:534-546`) has no ServeReads step. Its step 4 takes input whenever the output queue is ≤ 80 KiB, and `until = now` covers only work left by steps 2–4.
  - The code differs on each point. `sched.rs:85-98` skips input while `reads_ready` is set and polls on it, and `runtime.rs:549-551` handles ServeReads.
  - Only the §7.4 row was updated. Main's loop text is unchanged (`design.md:540-552`), though its `sched::must_poll` now also covers `reads_ready`.
- **Run 5 F1: the cut uses declared capacity, so a 2-byte build sets aside a 1 MiB read.** Verdict: real, executed (probe below). Impact is low.
  - The check is `built > 0 && built + r.capacity > READ_REPLY_CAP` (`read.rs:456`, main `:475`). It adds the next read's declared capacity, not the data it would get.
  - So `reads_cut` is set after only 2 bytes have been built. That contradicts §7.4's "a call that reaches the 1 MiB stops" (`design.md:618`).
  - For the rest of that turn, GetNumberOfInputEvents reports 1 while a NOWAIT peek returns 0. The next turn polls (`ServeReads`), so the delay is one turn.

## Probes

I added one temporary test, `grader_probe_small_cut`, before `one_call_builds_at_most_one_reply_budget_and_the_rest_follows` in `console/read.rs`'s tests. It ran with `cargo test -p rusty_conhost --lib --offline grader_probe -- --nocapture` at 615e0824 and again at main ebb92e28. Afterwards I restored the file from a copy, and `git status` is clean in both trees.

The probe sets raw mode, holds a ReadConsoleW with a 2-byte buffer and then one with a 1 MiB buffer, and sends `Event::Input(b"ab")`.

| Commit | Result |
|---|---|
| Head | 1 completion (`a`), `reads_ready` true, count 1, peek 0, then `Event::ServeReads` completes `b` |
| Main | the same first four values (main's copy of the probe stopped before ServeReads) |

I didn't probe K1 or the run 2–4 stall: `runtime.rs` is Windows-only. Their traces match the source at `runtime.rs:529-576`, `runtime.rs:264-279` and `sched.rs:98`. The key's fix 2dfc3a6c ("decide the engine's wait on what the turn left") addresses exactly this.

## Judgement calls

- **Run 5 F1: real, not design_question.**
  - §7.4 accepts that new reads and peeks queue behind owed reads after a 1 MiB cut, and runs 1 and 3 file that state only as a design question.
  - Run 5's point is different: the cut fires with almost nothing built, which the §7.4 wording does not allow. The probe reproduces it exactly.
  - The impact is a one-turn deferral plus a transient count/peek disagreement, so it is minor at the reviewer's P2.
  - Run 1 raises the same mechanism only under optional improvements, so it is not graded there.
- **The doc findings (§7.2, `esc_ends`): graded real.** §7.2 is normative, and §7.4 says new stores must name their cap, so both are documented requirements the commit breaks. No behaviour is wrong in either, beyond the uncounted 512 KiB.
- **Run 3 B2** bundles both doc defects into one finding. I graded it once, as real.
- **Severity:** runs 2–4 rate K1's stall P1 against the key's P2. The scorer reports this as over-severity, and I left the reviewers' severities unchanged.
