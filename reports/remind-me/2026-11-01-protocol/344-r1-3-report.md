<!-- remind-me/344-r1 run 3, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 679 s, 2354538 tokens, $1.64 -->

# Review: Rusty-Mill/rusty-remind-me PR 344, "consolidate: count the evidence behind merged memories"

**Verdict: changes required.** I confirmed four of the five blocking defects by running probes, including the P0. The PR's own tests pass, but none of them covers sync, edits to metadata, reverting a merge, or metadata that isn't a JSON object.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head reviewed:** `95528f017e2db1caef65aeee2154995333588067`. The fetched `pull/344/head` matches it.
- **Diff:** one commit touching `consolidation.rs`, `vitality.rs`, `tests/consolidate_test.rs` and `RELEASE_NOTES.md`.

## What the change touches, and what must hold
- **The canonical memory's `metadata`** (new `evidence` key). It must keep every key the user stored. The count must equal the number of memories superseded into it, plus one for itself. The value must survive an edit, a revert and a merge that only half lands. Under ADR-0004 it syncs in the memory record and shallow-merges per key.
- **The canonical memory's `base_weight`.** It must carry exactly `f(evidence)` on this node and on every peer. The update formula `w·f(after)/f(before)` is only correct if that already holds.
- **The outbox payload.** It snapshots the row at the moment `set_merged` stamps `updated_at` (`engine/memories.rs:499-503`). A receiving node takes `base_weight` from that payload (`sync/record.rs:375`).
- **`remind_me_consolidate`** keeps the same tool, schema and response shape (ADR-0028 and ADR-0030). No issue there.

## Blocking findings

### 1. P0: merging deletes a canonical's metadata when it isn't a JSON object (data loss)
- **Where:** `consolidation.rs:499-506`
- **What happens (executed):** a canonical memory has `metadata` stored as an array, `["source: standup notes","owner: kim"]`.
  - The core accepts non-object metadata. MCP builds the input with `from_value::<MemoryAddInput>` (`remind_me_mcp/src/lib.rs:1750`) and REST passes `body.get("metadata")` straight through (`routes.rs:446`). The schema says `object`, but nothing enforces it.
  - After the merge, the stored metadata is `{"evidence":2}`. The user's data is gone, the report still says success, and the loss syncs to every peer.
  - Before this PR, a merge never touched metadata.
- **Who is hurt:** the person who stored the metadata, and every agent that later reads it.
- **Fix:** don't replace metadata that isn't an object. Skip the `evidence` write and the boost for that cluster, and say so in the report. Separately, refuse non-object metadata where it enters (add, update, import, sync).
- **Regression test** (`consolidate_test.rs`): give the canonical array metadata, merge, and assert the metadata is unchanged.

### 2. P1: peers get the evidence count but not the weight boost, so they diverge
- **Where:** `consolidation.rs:506-526`
- **What happens (executed with the real outbox payload, decoded and applied through `upsert_record`):**
  - `set_tags_and_metadata` (an `Origin::Sync` write) and `set_merged` write `evidence: 2`, and `set_merged` stamps `updated_at`, which queues the row with `base_weight = 1.0`.
  - `set_importance` then writes the boosted `1.0693…` without stamping, so nothing new is queued.
  - Result: node A holds `base_weight 1.0693`; node B holds `evidence: 2` with `base_weight 1.0`.
  - If B later merges a third memory into this canonical, it computes `1.0·f(3)/f(2) ≈ 1.038`, where A would hold `1.110`. Its weight now sits below what three pieces of evidence call for.
  - The two nodes only converge if some unrelated later edit on A happens to stamp `updated_at` again.
- **Who is hurt:** other nodes, and agents ranking on them. The comment at `consolidation.rs:503-505` mentions only metadata and is silent on this.
- **Fix:** write `base_weight` in the same write that stamps `updated_at` (set it before `set_merged`, or pass it in). Alternatively, apply the boost when scoring, from `evidence`, rather than baking it into `base_weight`.
- **Regression test:** in its own binary with sync enabled, like `reminders_sync_test.rs`. Merge, take the last outbox payload for the canonical, decode it, `upsert_record` it into a second database, and assert both nodes hold the same `base_weight`.

### 3. P1: `evidence` is a stored counter taken from editable metadata, so it gets miscounted and the boost compounds
- **Where:** `consolidation.rs:294-300` and `497-498`, plus the release-note claim that "a repeat merge does not compound".
- **Three ways it goes wrong (all executed):**
  - **An update drops the count.** `update_memory` replaces metadata wholesale (`queries.rs:224,242`). Merge (evidence 2, weight 1.0693), then update with `{"note":"checked"}`, then merge a third member. Result: `evidence = 2`, weight `1.1434`. The correct values are 3 and `1.1099`, so the boost compounded.
  - **A revert double-counts.** Merge, un-supersede the member with `clear_superseded`, then consolidate again. Result: `evidence = 3` for two memories.
  - **A client can forge it.** Add a member with `metadata: {"evidence": 1000000}` and merge it. Result: `evidence = 1000001` and the canonical sits at the 1.5× cap. Nothing in `fields.validate` reserves the key.
- **Who is hurt:** agents reading `evidence` as what a memory rests on, and the ranking.
- **Fix:** derive the count from what the store records, for example the members whose `superseded_by` points at the canonical, followed transitively. Or reserve `evidence` so that add and update refuse or strip it and an update carries it forward. Either way, the weight should be recomputed from the derived count, not adjusted by a delta.
- **Regression tests:** "update metadata then merge gives evidence 3 and weight f(3)"; "un-supersede then re-merge gives evidence 2"; "a client-supplied evidence key is ignored or refused".

### 4. P2: a merge that half-lands leaves a count that a retry inflates
- **Where:** `consolidation.rs:506-529`
- **What happens (source-traced):**
  - Each write commits on its own: `set_tags_and_metadata`, `set_merged`, `set_importance`, then `set_superseded_by` once per member. `Store::transaction` exists (`store.rs:63`) but isn't used here.
  - If the process dies, or `set_merged` errors, after the first write commits, the canonical holds `evidence: 2` with unmerged content, an unboosted weight and a still-live member.
  - Because that first write is `Origin::Sync`, nothing was queued for peers.
  - A retry computes before=2 and after=3, giving evidence 3 and weight `1.038` for two memories.
- **Fix:** run each cluster's writes inside `store.transaction`.
- **Regression test:** if the engine has a fault hook, fail the write after the metadata update and assert the canonical is unchanged and a retry gives `evidence: 2`. If not, that missing hook is a test-infrastructure gap worth noting.

### 5. P2: the release note says things the code doesn't do
- **Where:** `RELEASE_NOTES.md:5-11`
- "A repeat merge does not compound" is false (finding 3).
- The entry says nothing about the boost not reaching peers (finding 2), and nothing about non-object metadata being overwritten (finding 1).
- **Fix:** correct the entry once findings 1–4 are fixed.

## Design questions (not defects)
- **Concurrent merges on two nodes.** ADR-0004 shallow-merges `metadata` per key under LWW. If node A and node B each merge a different member into the same canonical, both write `evidence: 2`. The winner's 2 then stands for 3 memories, while both supersessions still sync. A counter kept in an LWW key can't add up concurrent merges. Is the owner willing to accept that, or should the count be derived from supersession links (as finding 3 suggests)?
- **Is `base_weight` local or synced?** `set_importance` describes it as "a local score, not an edit", yet the payload carries it and the receiver overwrites its own value with it. The owner should decide which, and then decide whether the evidence boost follows the same rule.

## Optional improvements
- `vitality.rs:107-117`: the new constants sit between `BASE_WEIGHT_MAX` and `BASE_WEIGHT_MIN`, so the "Ceiling and floor" doc comment no longer covers `MIN`. `EVIDENCE_FACTOR_MAX` also has no doc comment of its own.
- `set_tags_and_metadata` writes `merged_tags`, and `set_merged` immediately writes them again.

## Coverage of the other failure classes
- **Hidden rows:** the change only reads metadata for cluster ids that are already candidates. No new path for sensitive or other-project rows.
- **Time as text:** no timestamps are compared.
- **Concurrency:** each write takes and releases the lock, with no I/O while it is held. The real exposure is the atomicity problem in finding 4.
- **Retrieval admission, protocol subset, who is calling:** this diff touches none of them.
- **Tool surface:** schema and response are unchanged, and the tool description makes no claim that the change contradicts.

## Tests
- **New unit tests in `consolidation.rs`:** they call `evidence_of` and `evidence_weight`, which don't exist at base, so they don't compile there.
- **New integration tests:** I ran the head's `consolidate_test.rs` against the base source. Both new tests (`a_merge_records_evidence…` and `evidence_accumulates…`) fail there and pass at head, so they guard the change.
- **Missing:** a sync test that decodes a real outbox payload (finding 2), and tests for update, revert, forged and non-object metadata (findings 1 and 3).

## Checks run
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy --locked -p … (6 packages) --all-targets -- -D warnings` | pass (exit 0) |
| `cargo test --locked -p remind_me_core --test consolidate_test` | 11/11 pass at head; the 2 new tests fail at base |
| `cargo test --locked --no-fail-fast` over the 6 packages | 156 test binaries passed, 11 failed. Every panic is `Read-only file system` or `Operation not permitted`: the tests write under `$HOME`, which the sandbox denies. No failure touches consolidation. |
| Probes in a scratch copy (`$TMPDIR/probe/.../evidence_probe.rs`) | Confirmed findings 1, 2 and 3 (5 probes) |

## Validation gaps
- **Environment:** cargo's configured target directory is read-only, so all builds used `CARGO_TARGET_DIR=$TMPDIR/target`, with `--offline`.
- **Tests not run here:** about 100 import, archive and watch tests. They need a writable `$HOME`; re-run them outside the sandbox.
- **Hub:** no live hub and no Postgres. This PR adds no new wire field (`metadata` and `base_weight` already exist), so the hub's storage is unlikely to be affected, but I didn't exercise it.
- **Crash between writes:** finding 4 is traced from the source, not executed, because there is no fault-injection hook.

## Verdict: changes required
