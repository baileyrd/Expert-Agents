# conhost, 2026-10-30, generated against hand-written

The generated prompt is `orchestrator/examples/conhost/` (prompt.md, codex.md, expert.toml) as merged in
PR #3. The bench copied it over `experts/conhost/` in a local commit, `127573c5`, which was never pushed;
that commit is the `prompt_sha` in the run files. The hand-written prompt is `experts/conhost/` at
`6f8b297a`. Its r2 to r4 numbers come from new runs in
[2026-10-30-baseline-r2r4/](2026-10-30-baseline-r2r4/). Its 1246-r1 numbers come from the 2026-10-30
baseline (`2026-10-30-baseline.md` and its run directory), which is not committed yet: it lands with the
baseline PR. Until then the hand-written r1 row, the four-case totals, and the generated r1 verdicts
reused from that baseline's verification cannot be reproduced from this repository. The r2 to r4
comparison below is complete on its own.

Every case was run the same way:
- `python3 -m bench.runner`, Agent SDK 0.3.299 and promptfoo 0.124.1, on the Claude Code login with no
  API key.
- Repeat 5, concurrency 4, target `~/dev/rusty_mill`.
- No `--allow-domain`, so there was no network. The reviewers could not fetch crates.io or
  microsoft/terminal.
- The repeats of a case share one checkout and one scratch directory. They are not isolated from
  each other: generated r1 run 1 reports that another run wrote probe tests into its copy of the head
  (`2026-10-30-generated/1246-r1-1-report.md`). It discarded that test run and used a private
  directory, but it had seen the other run's probes. Every run in both arms, and the 2026-10-30
  baseline, ran this way, so no hit rate here comes from isolated repeats. The runner gives each run
  its own checkout and scratch from a074bbf on the baseline branch.

Each run was graded by a verifier that saw the report, the key and the code at the head and at the
rusty_mill main `ebb92e28`, and that probed claims with `#[cfg(test)]` tests where `cargo` could run
them (`runtime.rs` is Windows-only, so claims there are traced from source). Notes are in each
directory's `verify-*.md`. The generated variant was graded first, and the hand-written r2 to r4
graders reused its verdicts on shared claims so that both use one standard.

```
python3 -m bench.scorer bench/cases/conhost/<case> reports/conhost/2026-10-30-generated/<case>-*.toml
python3 -m bench.scorer bench/cases/conhost/<case> reports/conhost/2026-10-30-baseline-r2r4/<case>-*.toml
```

## Per case

| Case | Variant | Recall | Precision | Severity vs key | Graded findings | Wall, tokens |
|---|---|---|---|---|---|---|
| 1246-r1 | generated | 0.53 (0.33–0.67) | 1.00 | 3 agree, 5 over | 8 key, 29 real, 6 fixed since, 2 DQ | 14m56s, 19.5M |
| | hand-written | **0.73** (0.67–0.83) | 1.00 | 7 agree, 5 over | 12 key, 13 real, 1 fixed since, 1 DQ | 29m27s, 24.9M |
| 1246-r2 | generated | 0.00 | 1.00 | — | 12 real | 13m20s, 9.0M |
| | hand-written | 0.00 | 1.00 | — | 11 real | 11m44s, 10.9M |
| 1246-r3 | generated | **0.60** (0–1) | 1.00 | 3 over | 3 key, 7 real | 7m34s, 5.1M |
| | hand-written | 0.40 (0–1) | 1.00 | 2 over | 2 key, 13 real | 8m30s, 7.2M |
| 1246-r4 | generated | 0.00 | 1.00 | — | 2 real | 3m04s, 2.4M |
| | hand-written | **0.20** (0–1) | 1.00 | 1 over | 1 key, 8 real | 8m03s, 5.4M |
| **r2 to r4** | generated | 0.20 (mean of cases) | 1.00 | 3 over | 3 key, 21 real | 23m58s, 16.5M |
| | hand-written | 0.20 | 1.00 | 3 over | 3 key, 32 real | 28m17s, 23.5M |
| **All 4** | generated | 0.28 (mean of cases) | 1.00 | 3 agree, 8 over | 11 key, 50 real | 38m54s, 36.0M |
| | hand-written | **0.33** (r1 from the uncommitted baseline) | 1.00 | 7 agree, 8 over | 15 key, 45 real | 57m44s, 48.4M |

Wall is the promptfoo eval duration for 5 runs at concurrency 4. Neither variant had a false positive.
"Over" means the reviewer rated a key finding above the key's grade, almost always P1 against P2.

## Per key finding (hit rate over 5 runs)

| Case | Key | Generated | Hand-written |
|---|---|---|---|
| r1 | K1 cooked echo bypasses output backpressure (P1) | 0.60 | 0.80 |
| r1 | K2 trailing ESC at a slice boundary (P2) | 1.00 | 1.00 |
| r1 | K3 surrogate pairs converted per unit (P2) | **0.00** | 0.40 |
| r2 | K1 all read replies built before any completes (P1) | 0.00 | 0.00 |
| r2 | K2 write boundaries lost when writes coalesce (P2) | 0.00 | 0.00 |
| r3 | K1 wait uses a poll plan made before the events (P2) | 0.60 | 0.40 |
| r3 | K1 also as the `reads_ready` case | 0 of 3 hits | 0 of 2 hits |
| r4 | K1 admission not rearmed when input frees capacity (P2) | **0.00** | 0.20 |
| | Key findings with any hit | 3 of 7 | 5 of 7 |

The keys hold 7 findings: 3 in r1, 2 in r2, 1 in r3, 1 in r4. The `reads_ready` row is a second
trigger of r3 K1, not another finding.

## Reading

- **The generated prompt is close to the hand-written one, but not equal.** Summed over the 7 key
  findings it catches 2.2 of them against the hand-written prompt's 2.8; on the committed r2 to r4
  cases alone the two are level (0.20 each). Precision is the same (1.00),
  and it costs about 25% fewer tokens and 33% less wall time.
- **It loses mostly on round 1 and on the narrow round-4 key.**
  - **r1, K3:** no generated run raised the per-unit surrogate conversion as blocking. Two came close
    in optional sections.
  - **r1, K1:** runs 3 and 5 named only the missing §7.4 test, not echo exceeding the cap.
  - **r4:** every generated run audited the r3 fix (lost wakes, spinning, reads_ready, held echo) and
    never looked at READ_IO admission. One hand-written run did.
- **It wins on r3:** 3 of 5 runs against 2 of 5. Even so, no run of either variant named the
  `reads_ready` half of the key.
- **Both variants miss r2 completely.** Each round-2 run checked the three round-1 findings as fixed,
  then went after what fix commit 8ec54b85 introduced. They found real bugs there (below), but none
  found reply buffers piling up within one `handle` call, or writes coalescing. These two keys look
  hard for this prompt family, not for one variant.
- **Re-reviews are cheaper with the generated prompt.** On r4 it spent 2.4M tokens against 5.4M. Three
  of its five runs returned "no blocking findings", against none of the hand-written runs.
- Five runs per variant is a small sample, and within-case spread is wide (0 to 1 on r3 and r4).
  Differences of 0.2 on one case are inside the noise.

## Real bugs outside the key, still on rusty_mill main `ebb92e28`

These are confirmed by a probe ("exec") or a source trace ("trace"). **G** means a generated run found
it, and **H** means a hand-written run did.

From round-1 code (head 197fae02):
1. Ctrl+C in the same input slice as typed text: the cancelled text survives into the next cooked
   read. `del x\x03` in one event returns `"del x\r\n"` (exec). **G** only.
2. A second cooked read on a handle overwrites the first read's kept line rest (exec). **G H**
3. Cooked echo writes C0 controls raw instead of `^X`, and does not expand Tab. **G H**
4. Focus reports reach VT-input clients that never set `?1004` (exec). **G H**
5. `vt_for` sends `k.ch` unchanged: Backspace arrives as 0x08 not DEL, Shift+Tab as TAB not CBT,
   and DECCKM is ignored. **G** only.
6. Backspace echo ignores wide characters and wrapped lines. **G** only.
7. Alt+[ / Alt+O (and Alt+] / Alt+P) at the end of a write wait for the next key and merge with it.
   With `ESC P` or `ESC ]`, the typing that follows is swallowed (exec). **G H**
8. Plain-VT text becomes key-downs with VK 0 and no key-up. **G** only.
9. An unfinished sequence over 4,096 bytes passes raw to VT clients, although §7.4 says it is
   dropped. **G** only.
10. The Ctrl+C key-up is queued, against §5.5 "nothing is queued". **G H**
11. Stale design text: Appendix A says "pinned in PR 3" with no numbers; there is no §8.2 interactive
    fixture; the recorder keeps typed input that §8.2 says is scrubbed. **G** only.
12. Tests required by §7.2 and §7.4 are missing. **G H**

From fix 8ec54b85 (round 2):

13. The design's ANSI record cache row says 2 records; the code holds 3, or 5 after a lone high
    surrogate (exec). **G H**
14. A unit parked in `held_unit` is invisible to the input event, the event count and peek, so a
    waiting ReadConsoleInput skips it (exec). **G H**
15. While echo is held, the event and the count report input that a peek returns as 0, so a client
    that polls spins (exec). **G** graded it real; **H** raised it only as a design question.
16. Closing a held read (CLOSE_OBJECT or DISCONNECT) leaves the reads behind it waiting until the
    next drain (exec). **G** only.
17. A held read can stall until an unrelated key: the hold is decided on a snapshot of the queue
    taken at the start of the call, and the drain wake fires only above 64 KiB (trace). **G** only.
18. Input admitted while echo is held evicts the held read's own records: 4,000 `a` came back as
    2,619 (exec). **G** only.
19. A paste of 5,000 characters into a waiting cooked read returns 4,096 units, because the slice is
    stored before `serve_reads` runs (exec). **H** only.
20. With echo off, a dangling high surrogate is printed as U+FFFD (exec). **H** only.
21. The `echo_held` guard on the packet path has no test, and its test comment is wrong. With the
    guard removed, all tests pass (exec). **H** only.
22. Design §3.3 and §7.2 never mention `Event::InputMore`. **H** only.

From 615e0824 and 2dfc3a6c (rounds 3 and 4):

23. `esc_ends` can hold up to 512 KiB, but it is missing from the §7.4 cap table and total. **G H**
24. The normative §7.2 loop has no ServeReads step, and its poll rule differs from `must_poll`.
    **G H**
25. GetNumberOfInputEvents and a NOWAIT peek disagree while `reads_cut` is set. The budget cut can
    trigger after 2 bytes, because it uses the declared capacity (exec). **G H**
26. The order of reads across a budget cut is untested: two mutations keep every test green (exec).
    **H** only.
27. No test goes red if `turn()` decides its wait before `work()` again: the tests call
    `work()` and `must_poll()` themselves (trace). **G H**

Two more were fixed on main after the case heads, so they count as fixed since, not open bugs: Ctrl
events going to the oldest client first, and teardown not sending `?9001l ?1004l`. Both were fixed in
`1eb6ec680`. Items 1, 5, 6, 8, 16, 17 and 18 are the generated prompt's own contribution. Items 19 to
22 and 26 are the hand-written prompt's.
