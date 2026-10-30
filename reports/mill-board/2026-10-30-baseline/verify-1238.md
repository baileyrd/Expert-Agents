# 1238-r1 claim verification (baseline runs 1-5)

Worktrees: head 43e072095 and origin/main ad1c3e371, under the session scratchpad (removed afterwards).
Fix commit for the key: 22a35d8f0 ("reserve card numbers durably in rusty_tick"), on main.
All verdicts below are source traces; the "already numbered" race was also executed by the frozen1
grader (probe_f4.rs, see ../2026-10-30-frozen1/verify.md) and the trace is unchanged, so I reuse it.

## Key matches
- K1 (cross-process allocation): every run's F1 names two boards on one rusty_tick both taking max+1. Caught x5.
- K2 (reuse after trash/untag/restore): runs 1 F2, 2 F2, 3 F3, 5 F2 caught. Run 4 has no blocking K2 finding:
  its Design questions names reuse after delete (not scored), and its F1 body names "get_number picks one of
  two #6 cards; refuse instead" (K2's fail-closed clause) but F1 is scored against K1. One match per finding,
  so run 4 misses K2. Flagged as a grading-format edge, not a key problem.

## Non-key claims

### "card is already numbered" when two writers number the same stray (r1 F3, r2 F3, r4 F2, r5 F3): fixed_since
Head: number_strays `?` on apply (board.rs:338); apply re-reads after 412 and Card::numbered errors
(domain.rs:522-524). Main: Card::numbered returns the card unchanged (domain.rs:521-530), comment
"Another board may number the card first" in number_strays. Fixed by 22a35d8f0.

### Any failure numbering a stray fails every card_add (r3 F2): real
Same `?` as above. Trigger A is fixed since, but trigger B holds at head and on main: rusty_tick
`clean_tags` (service.rs:785-802, used on create :340 and update :399) refuses more than MAX_TAGS=20.
A task the owner tagged `agent` plus 18 others (19) is rewritten by numbering as agent + board:backlog +
number:N + 18 = 21, so the PATCH gets 400; main board.rs:356-359 still propagates it out of
insert_numbered, so every card_add/import fails until the stray is edited. Graded on the headline.

### #n lets a scoped credential enumerate out-of-scope project names (r2 F4): real
card_ref (api.rs:340-346, main :348-355) resolves a number through Board::get_number over the unscoped
store.list(); card_get then returns OutOfScope(project) (api.rs:487-489), Display
`project "x" is outside your scope` (domain.rs:393), versus Invalid("no card has that number") for gaps.
Numbers are sequential, so a loop over #1.. reveals every hidden project name and its card count. The
api.rs test (:1011) asserts "a limited credential hears of no other project". Not documented as intended.
Unchanged on main. Severity P1 is arguably high (names only, no card content); P2 seems fair.

### Pre-existing user `number:` tags dropped or reinterpreted (r1 F4, r5 F5): real
Base reserved_tag (domain.rs:203-206) did not include number:, so card_update and label import could store
them. Head card_of (tick.rs:306-316) takes the first number: tag that parses as the number, filters every
number: tag out of card.tags as reserved; task_tags (tick.rs:279) rewrites tags on the numbering PATCH,
deleting e.g. `number:abc`, and two `number:1` tags give two #1 cards. The change fragment only says
`number:` is now reserved. Unchanged on main (tick.rs:320-330); main's get_number now refuses duplicates,
which softens the wrong-card part but not the silent drop.

### README reserved-tag list / storage mapping omit number: (r1 F5, r2 F5, r3 F4, r4 F3, r5 F4): real
README.md:30-32 lists agent/board:/claim: only, README.md:73 maps column and claim only; domain.rs:203-209
reserves number:. Unchanged on main. (ADR-0012:245-246 tag budget also not updated, as in frozen1.)

### Missing tests (r1 F6): real; (r2 F6): fixed_since
Head: tests/numbers.rs is single-board; only card_get is exercised by number (api.rs:660-671).
Main adds two-board, leave-the-board and duplicate-refusal tests (tests/numbers.rs:128-199), so r2's
"no concurrency tests" is fixed since. r1 also names card_take/card_plan by #n: still untested on main
(only api.rs:688-693 uses "#n"), so r1 F6 stays real.

### Sub-claim inside r4 F1 (not graded separately)
`map_or(1, |n| n + 1)` on a `number:18446744073709551615` tag overflows (board.rs:331). Plausible at head;
main computes the next number via reserve_number instead. Not scored separately since it sits in a K1 finding.
