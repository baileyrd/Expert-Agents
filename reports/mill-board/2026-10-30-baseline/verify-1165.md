# Verification notes: mill-board/1165-r1, baseline runs 1-5

Head `453dd9f21`, base `c4a0d031`, origin/main `ad1c3e371` (fetched), fix `f680de284` (an ancestor of main).
Worktrees were checked out from ~/dev/rusty_mill and removed afterwards. Nothing was built, because every claim
below was settled by a source trace, and K3's ordering is plain sequential code.

## Key matches

- **K1** (all five runs, graded P0 against the key's P1): main.rs:197-204 at head. `MILL_BOARD_STORE` is no longer
  declared, so with no tick URL the binary picks `MemStore::default()`. Every run names the in-memory board that loses
  writes at restart. Caught.
- **K2** (all five runs, P2): every run names the deleted
  `the_server_reports_health_keeps_its_first_credential_off_stderr_and_stops_on_sigterm`, including the first-admin
  token and SIGTERM checks. Caught. f680de28 restores the test as tests/process.rs.

## Non-key claims

### `parts()` re-read fix has no deterministic race test (runs 1-5, P2): fixed_since

- At head, tests/fake_tick/mod.rs has no hook between `GET /tasks/{id}` and `GET /docs/board`.
- The re-read at tick.rs:487-507 is guarded only by the 8-thread barrier test in tests/execution.rs, which hits the
  window by chance. The `READ_ATTEMPTS` → `Conflict` path has no test.
- The human review raised the same point as non-blocking ("deterministic interleaving/exhaustion coverage would
  strengthen it"). That is why it is not in the key.
- f680de28 adds `FakeTick::race_after` and two tests:
  - `a_write_between_the_task_and_its_document_is_read_again_not_mixed`
  - `a_card_that_keeps_moving_under_its_reads_is_a_conflict`

  On main both are at tests/tick_store.rs:509 and :524.

### main.rs module doc describes the removed store's health (runs 1-4, P2; run 5 non-blocking): real

- main.rs:12-13 says `/health` returns "503 once it needs a restart".
- The only adapters are `MemStore`, whose health is always `Ok`, and `TickStore::health` (tick.rs:739-748), which
  returns 503 only while rusty_tick is unreachable. That is also what the README and deploy/README now say.
- Still at main.rs:13 on main. This is the same verdict as frozen1 #1165-F4.
- Run 4 also cites store.rs:37 and board.rs:204 ("must be reopened"). Both are stale in the same way, and both are
  still on main at store.rs:43 and board.rs:394.

### Live `cp` of `settings/` has no consistency step (run 2 F5, P2): real, narrowly

- deploy/README.md:48-49 at head (:71 on main) tells the operator to back up `settings/` with no stop or lock.
- At base, `Settings::snapshot*` copied the files under the settings lock. The PR removed it.
- The run says one settings change "writes the agent file and the role file one after the other". That is wrong: each
  op writes one file atomically (settings.rs:375, :455, :475).
- A torn copy still needs only two consecutive admin ops during the copy, for example `roles_set` adding a role and
  then `agent_update` assigning it. Then `Credentials::load` → `parse` refuses the restored identities with
  "unknown role" (identities.rs:115).
- The failure scenario holds (it fails closed, and the backup is unusable). The mechanism is misstated, and the
  trigger is narrow.

## Key K3

The key says: "Store docs claim a card and its event become visible atomically; the tick HTTP commit precedes local
event recording." Cited file: `crates/apps/rusty_mill_board/src/store.rs`.

- **The human review is the source.** Review 5669285044 (CHANGES_REQUESTED on 453dd9f2) says: "Correct Store docs
  claiming cards/events become visible atomically/commit order: Tick HTTP commit precedes local event recording."
  The round-2 review on f680de28 confirms "Event docs ... corrected". The PR has no inline comments.
- **The claim is in the head.** store.rs:12-15, on the `Store` trait doc, says: "...and the card and its event become
  visible together. The store assigns the event's id inside the commit, so event ids are in commit order."
  - The PR rewrote this paragraph. The base said "A commit is atomic: ... durable and visible together".
  - The PR kept the "visible together" and "commit order" promises while making `TickStore` the only real adapter.
- **The order is commit, then record.** In tick.rs, `insert` POSTs `/tasks` (and `put_doc` when there is an import
  document), and only then calls `self.record(event, version)`. `replace` runs `patch_task`/`put_doc`/`put_comment`,
  then `record`.
  - `record` (tick.rs:571-578) takes the local events lock and assigns the id when it pushes.
  - Between the HTTP 201/200 and `record`, a `get` or `list` (which reads rusty_tick) already sees the card while
    `events_since` does not show its event.
  - A concurrent `observe` (tick.rs:581-605) can push a synthesized event first, so event order is not commit order.
    The frozen1 verify saw this as the `tick_store_never_skips_an_event` flake.
- **The fix agrees.** f680de28 rewrites exactly this paragraph: "[`crate::tick::TickStore`] writes the card to rusty_tick
  first and records the event after it: a reader can see the card before its event".
- **The cited file is right.** The doc is in store.rs. The behaviour it misdescribes is in tick.rs, but the defect is
  the doc.

**Conclusion: the key is accurate. No edit.** The 0/5 is a true miss, not a key error.

**Mentions in any section:** none of the five runs names the doc claim against the commit-then-record order.

- Run 4's invariant table lists "`Store` trait docs (`store.rs:10-21`) | They describe what the adapters actually do",
  but no finding follows from it.
- Run 1's table says "`Store` trait doc ... Commits stay compare-and-swap", and its failure-class notes say
  `tick_store_never_skips_an_event` was not run to look for flakiness.
- Run 3 says "The fenced document write is atomic in rusty_tick". That is about rusty_tick, not the board's
  card/event pair.

None of these meets caught_if, even as partial. Frozen1 run 1 caught K3 by naming both the flaky test and the Store
doc's claim. No baseline run did either.

## Uncertain

- The live-`cp` claim (run 2 F5) is graded real on a corrected mechanism. A stricter grader could call it a narrow
  operator concern.
- The `parts()` test gap is a missing test, not a runtime defect. It is graded fixed_since because the human reviewer
  agreed and f680de28 added the tests.
