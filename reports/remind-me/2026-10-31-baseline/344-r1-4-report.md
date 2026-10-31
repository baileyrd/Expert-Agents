<!-- remind-me/344-r1 run 4, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 289 s, 1765376 tokens, $1.42 -->

I'm requesting changes on PR 344: I found two P1 defects (one sync, one partial-write) and three P2 defects. None of this was executed. I couldn't build the workspace because github.com is blocked in this session, so every trace below comes from reading the code.

**Repository:** Rusty-Mill/rusty-remind-me · **PR:** 344 · **Base:** `d5528b1347054f348b3dac96f54619e603f4594c` · **Head:** `95528f017e2db1caef65aeee2154995333588067`. The fetched PR head matches the head SHA. The PR is one commit, "consolidate: count the evidence behind merged memories".

## What the PR touches, and what must stay true
- **The merged ("canonical") memory's `metadata.evidence` and `base_weight`.** The count must equal how many memories the row stands for. The weight must carry exactly the boost for that count, on every node.
- **The sync payload.** Per ADR-0004 the whole row is last-write-wins on `updated_at`, `base_weight` included (`sync/record.rs:375`); only tags and metadata merge. So what a node stores and what it queues must be the same record.
- **The merge sequence.** It must land whole or not at all; a half-done merge must not make the next run count twice.
- **Metadata from clients.** It is free-form in the tool schema (`remind_me_mcp/src/lib.rs:668,742`). Any key the code depends on has to be checked where it enters.

## Blocking findings

### P1-1: The boosted weight never reaches the sync queue, so peers end up with different weights (source-traced)
`crates/remind_me_core/src/consolidation.rs:503-526`
- The queue entry copies the whole row at the moment of the write (`db/engine/memories.rs:499-502`, `row.payload()`).
- `set_tags_and_metadata` (marked as a sync write, so it queues nothing) writes `evidence`. Then `set_merged` stamps `updated_at` and queues the row: `evidence: 2`, but the **old** `base_weight`.
- `set_importance` then writes the boosted weight without stamping `updated_at`, so the boost is never queued (`db/engine/memories.rs:818-831`).

**Failure scenario:**
1. Node A merges X and Y. A holds `evidence: 2` and `base_weight` 1.069.
2. Peer B applies the queued payload and holds `evidence: 2` with `base_weight` 1.0.
3. An agent on B edits X's tags. B's payload wins on `updated_at` and A takes `base_weight` 1.0 (`record.rs:375`). A's boost is gone and `evidence` still says 2.
4. Any later merge on either node only applies f(3)/f(2) (`evidence_weight`). The f(2) part is lost for good.

Every node ranks X lower than the release note promises, and until step 3 the nodes disagree.

The comment at `:502-503` says the ordering puts "the row" in the queue. That is true of the metadata, but the queued row still has the old `base_weight`.

**Correction:** compute `base_weight` and vitality first. Write them with `set_importance` before `set_merged`, or fold them into the stamped write, so the queued row matches the stored row.

**Regression test:** in a sync-enabled binary (env setup as in `outbox_test.rs`), run `consolidate`. Decode the canonical's last queued payload as a `SyncRecord` and assert `base_weight` equals the stored value and `metadata.evidence == 2`. Better: `upsert_record` it into a second `Database` and compare the two rows.

### P1-2: A half-applied merge counts its evidence twice on retry (source-traced)
`consolidation.rs:503-530`
- Each write commits on its own; `apply_merges` does not use `Store::transaction`. That function exists and the project-claim writes use it (`project.rs:762,881`).
- The PR's first write persists `evidence = after`.
- If `set_merged`, `set_importance` or a `set_superseded_by` fails or the process dies, the members are still live and `evidence` is already 2. That `evidence` write was marked as a sync write, so it was never queued either.

**Failure scenario:** the next `consolidate` finds the same cluster. It computes before=2 and after=2+1=3, stores `evidence: 3` for two memories, and moves `base_weight` to f(3) instead of f(2). An agent reading the memory sees a count that is wrong and a weight to match.

**Correction:** wrap each cluster's writes in `store.transaction(...)`.

**Regression test:** `a_failed_merge_leaves_no_evidence_behind`. Inject a failure after the first write, using a test hook in `remind_me_core::testing`, and assert that `metadata.evidence` is absent and `base_weight` is unchanged.

### P2-1: `evidence` is a key any client can set or drop, which breaks the "no compounding" claim (source-traced)
`consolidation.rs:294-300, 490-499`; `db/queries.rs:242` and `db/engine/memories.rs:743-744` (an update replaces metadata wholesale).

**Failure scenario 1:**
1. Merge X and Y: `evidence: 2`, weight f(2).
2. An agent sends `remind_me_update` with `metadata: {"note": "…"}`, and `evidence` disappears.
3. The next merge reads before=1 and after=2 and applies f(2) again.

The weight compounds, which makes the release note's "a repeat merge does not compound" false.

**Failure scenario 2:** any client, or a peer through the metadata merge, stores `{"evidence": 150}` on a memory. Merging it gives the canonical the full 1.5× boost.

**Correction:** either derive the count from rows superseded into the canonical, or treat `evidence` as reserved: refuse or strip it on add and update, and carry it across a metadata replacement.

**Regression test:** merge, then update the metadata without `evidence`, then merge a third duplicate. Assert the weight equals original × f(3).

### P2-2: The evidence sum can overflow (source-traced)
`consolidation.rs:498`: `ids.iter().map(evidence_of).sum::<u64>()` adds client-controlled `u64` values with plain `+`. With `evidence: 18446744073709551615` on the canonical plus one member:
- **Debug builds** panic on this write path.
- **Release builds** (no `[profile]` section in `Cargo.toml`, so no overflow checks) wrap the sum to 0. The PR stores `evidence: 0`, and the weight is divided by 1.5.

**Correction:** add with `saturating_add` and cap the stored count.

**Regression test:** a unit test that merges `u64::MAX` with 1.

### P2-3: Doc comment disagrees with the code
`vitality.rs:111-117`:
- It says a canonical "that absorbed `n` merged duplicates scores `1 + 0.1·ln(n)`". In the code `n` includes the canonical itself: one absorbed duplicate gives ln 2.
- `EVIDENCE_FACTOR_MAX` has no doc comment.
- The new constants were inserted between `BASE_WEIGHT_MAX` and `BASE_WEIGHT_MIN`, which separates `BASE_WEIGHT_MIN` from the "Ceiling and floor" comment that covers it.

### P2-4: No test covers the sync behaviour this change depends on
No new test decodes a queue payload, even though `:502-503` relies on write order to sync the merge. The test in P1-1 belongs in a sync-enabled binary next to `outbox_test.rs`.

## Ruled out
- **Concurrency:** `Database::store()` lets one caller run at a time (ARCHITECTURE §1 tenet 4), so feedback cannot interleave with a consolidate run.
- **Other `base_weight` writers:** feedback and `resolve.rs` demotion both scale the weight by a factor, so dividing out the old evidence factor stays correct locally.
- **Project claims:** they are excluded from candidates (`engine/vectors.rs:257-262`), and their nested `project_claim.evidence` does not collide with the new key.
- **Sensitive rows:** nothing new reaches any surface an agent reads.
- **Time:** no new timestamp comparisons.

## Design questions
- If two nodes merge different duplicates into the same canonical, last-write-wins keeps only one count (and one content). That is settled by ADR-0004 and existed before this PR, but the count will be wrong.
- `metadata.evidence` is a number on memories and a list of facts on project claims (ADR-0027). Agents reading metadata may confuse the two.

## Optional improvements
- If the canonical's metadata is not a JSON object (unparseable text is read as `Null`), it is silently replaced with `{"evidence": n}`. I couldn't confirm whether any import path can store such metadata.
- The tags are written twice, by `set_tags_and_metadata` and again by `set_merged`.

## Checks and gaps
- `cargo fmt --all -- --check`: **passed** (exit 0).
- `cargo clippy` and `cargo test` (including `--test consolidate_test`): **not run**. Cargo's home is read-only and the pinned `rusty_mill` `libs-2026.10.0` git dependency isn't cached. Fetching it into a scratch cargo home was refused: github.com is denied in this sandbox. Allowing github.com would let these run.
- **New tests checked against the base by reading only:** the unit tests call functions that don't exist there, and the integration tests assert `evidence`, so both should fail at the base. Not executed.
- No hub or Postgres run, so the payload round-trip through the hub is unverified.

**Verdict: changes required.**
