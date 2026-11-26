<!-- remind-me/344-r1 run 5, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 719 s, 3572134 tokens, $2.12 -->

I'm requesting changes on PR 344. The new evidence count overwrites a caller's own `metadata.evidence` value, the boosted weight never reaches peers, and the count goes wrong after a crash or a later metadata edit. Four of the five blocking findings were reproduced in throwaway tests run against a separate copy of the repository; the fifth is traced from the code.

**Repository:** Rusty-Mill/rusty-remind-me, PR #344
**Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
**Head reviewed:** `95528f017e2db1caef65aeee2154995333588067`. The fetched PR head matches this SHA. The diff is one commit across `consolidation.rs`, `vitality.rs`, `consolidate_test.rs` and `RELEASE_NOTES.md`.

## What the change touches, and what must hold
- **Memory row, canonical of a merge** (`metadata`, `tags`, `content`, `access_count`, `base_weight`, `vitality`, `updated_at`): one merge must land whole or not at all. The count must equal the number of memories the canonical stands for, and the PR's own claim is that the boost "does not compound".
- **Outbox payload and the receiving peer** (ADR-0004: last write wins on `updated_at`, `metadata` is shallow-merged): the payload queued for a write must be the row as it ends up, so every node holds the same record.
- **Caller-supplied `metadata`**: the tool schema calls it "Free-form JSON stored with the memory" (`remind_me_mcp/src/lib.rs:668`). The system must not overwrite a caller's keys.
- **The `remind_me_consolidate` tool**: its schema and errors don't change. Its description and the docs must still describe what a merge writes.

## Blocking findings

### 1. P0: a merge overwrites a caller's own `metadata.evidence` value
- **Where:** `consolidation.rs:503`
- **Trigger:** an agent saves a decision with `metadata: {"evidence": "https://tickets/42"}`, or a list of ids. That memory is later chosen as the canonical of a merge.
- **What happens (executed):** after the merge, `metadata.evidence` is `2`. The probe printed `caller evidence after merge: 2`.
- **Who is hurt:** every agent reading that memory, and every peer.
  - The original value is gone: consolidation records no revision (`history_test.rs:1-8`: only the update path records).
  - The new value then syncs out.
- **Second effect:** because the key is free-form and caller-writable, a caller can also set the boost.
  - A cluster member written with `{"evidence": 1000}` makes `after = 1001`, so one merge applies the full 1.5× cap.
  - The value `"4"` (a string) silently counts as 1.
- **Fix:** keep the count under a reserved key the system owns, the way claims use `metadata.project_claim` (`project.rs:349`). For example `metadata.consolidation.evidence`, ignored on caller input or refused there. The count must never replace an existing caller key.
- **Regression test:** in `consolidate_test.rs`, a canonical stored with `metadata.evidence = "https://…"` still has that value after a merge.

### 2. P1: the boosted `base_weight` never reaches the outbox, so peers diverge
- **Where:** `consolidation.rs:506-526`
- **Cause:** `set_merged` (`:507`) stamps `updated_at`, which queues an outbox payload snapshotted at that moment (`engine/memories.rs:499-502`). That payload includes `base_weight` (`:281`). `set_importance` runs afterwards (`:526`) and does not stamp `updated_at` (`engine/memories.rs:816-831`), so the new weight is never queued.
- **What happens (executed):** the probe decoded the payload the real write produced, using the same decoding as `push.rs:68`, and applied it with `upsert_record` on a second database.
  ```
  ROW  evidence=2 base_weight=1.0693147180559945
  WIRE evidence=2 base_weight=1
  PEER evidence=2 base_weight=1
  ```
- **Who is hurt:** the peer holds `evidence: 2` without the boost. Its next merge divides by `factor(2)` and lands at about 1.038 instead of 1.110. When that peer's later record wins last-write-wins, the origin's boost is erased too. Nodes rank the same memory differently, and the "only the change since the last merge" rule is wrong everywhere except the node that did the merge.
- **Fix:** compute the new `base_weight` and vitality first and write them before or inside the `updated_at`-stamping write, for example by extending `set_merged`. The code comment at `:504-505` should then state the true ordering.
- **Regression test:** with sync on (own test binary, like `outbox_test.rs`), merge, decode the canonical's last outbox payload, and assert its `base_weight` and `metadata.evidence` equal the stored row's.

### 3. P1: a half-finished merge double-counts evidence when it is re-run
- **Where:** `consolidation.rs:491-530`
- **Cause:** `apply_merges` makes four or more separate commits per cluster: metadata, merge, importance, then each supersede. It doesn't use `Store::transaction` (`db/store.rs:63`), which `project.rs:762/881` already use. The metadata write uses `Origin::Sync` (`engine/memories.rs:589`), so it is never queued for sync.
- **What happens (executed, crash simulated):** the process dies after `set_importance` and before `set_superseded_by(member)`, simulated by clearing `superseded_by`. The same `consolidate` call is then re-run. The canonical ends at `evidence=3` and a 1.110× boost while it stands for 2 memories.
- **Earlier crash point:** a crash right after `:506` leaves a local-only `evidence=2` with the content not yet merged. The next run counts 3.
- **Who is hurt:** agents shown an inflated count and rank. Peers never receive the metadata-only write.
- **Fix:** wrap each cluster's writes in `store.transaction(|page| …)`.
- **Regression test:** a cluster whose supersede fails partway, through an injected error or a failing `transaction` test hook, leaves the canonical's metadata and weight unchanged. A re-run then yields `evidence: 2`.

### 4. P1: a later metadata update erases the count, so the boost compounds
- **Where:** `consolidation.rs:496-503` together with `queries.rs` `update_memory`, which replaces `metadata` wholesale (`input.metadata … unwrap_or(&stored.metadata)`).
- **Trigger:** this is the normal agent flow `remind_me_update { metadata: {...} }` on a canonical, for example adding a `rationale` (`lib.rs:733`).
- **What happens (executed):** merge, then update the metadata, then merge one more duplicate. The result is `evidence=2` with a factor of 1.1434 (`f(2)²`), where 3 and 1.1099 were expected.
- **Who is hurt:** agents reading a count and weight that are both wrong. It also makes the RELEASE_NOTES claim "a repeat merge does not compound" false.
- **Fix:** the same as finding 1. Store the count in a system-owned field that the update path preserves, or as its own column, and test that `update_memory` keeps it.

### 5. P1: a stale `base_weight` overwrites a value the sync worker applied meanwhile (source-traced)
- **Where:** `consolidation.rs:518`
- **Cause:** `canonical.base_weight` comes from the `fetch_candidates` snapshot. That is taken before clustering, which compares up to 1,500 candidates pairwise, and before every earlier cluster's synchronous re-embed over the network.
- **Why another write can land in that window:** the sync worker is a background thread on a `SecondarySource` whose engine tables lock per call (`ARCHITECTURE.md` §1 tenet 4 and §6). It can apply a winning pulled record for the canonical inside that window (`upsert_synced`).
- **What happens:** `set_importance` then writes `stale × factor`, discarding the pulled value. Meanwhile the `set_merged` payload carries the pulled value, so this node and its peers disagree. Before this PR, consolidate never wrote `base_weight`.
- **Fix:** read `base_weight` (and `decay_rate`) from the fresh `get_many` row at `:493`, inside the transaction from finding 3.
- **Regression test:** change `base_weight` between `fetch_candidates` and the merge through a test hook, and assert the merge scales the current value.

### 6. P2: the docs no longer match the code
- **`vitality.rs:107-117`:** the new constants were inserted between `BASE_WEIGHT_MAX` and `BASE_WEIGHT_MIN`.
  - "Ceiling and floor `base_weight` is clamped to" now documents only the ceiling.
  - `BASE_WEIGHT_MIN` and `EVIDENCE_FACTOR_MAX` have no doc comment.
  - The `EVIDENCE_BOOST` comment says "absorbed `n` merged duplicates scores `1 + 0.1·ln(n)`", but `n` counts the canonical itself: absorbing one duplicate gives `n = 2`.
- **`consolidation.rs:373`:** still says "A merge updates the canonical's content/access_count/tags/vitality". It now also rewrites `metadata` and `base_weight`.
- **`remind_me_consolidate` description (`lib.rs:1543`):** doesn't tell agents that a merge raises ranking weight or writes `metadata.evidence`.
- **Fix:** correct each one. There is no test for this.

### 7. P2: no tests cover the sync path or caller-owned keys
- The two new integration tests do go red at base (confirmed below). But nothing decodes the payload a real merge produces, and nothing covers a caller-supplied `evidence` key.
- **Fix:** add the tests named in findings 1–4 to `consolidate_test.rs`. The payload test should live in a sync-enabled binary of its own.

## Design questions (not blocking)
- **Concurrent merges on two nodes:** node A merges X with Y and node B merges X with Z. A counter kept in a shallow-merged, last-write-wins map ends at `evidence: 2` everywhere, while X stands for 3 memories. ADR-0004 settles how metadata merges, so this is a question for the owner: can the count be derived from the memories that point at X through `superseded_by`, instead of being stored?
- **"No schema change":** the change does reserve a metadata key. ADR-0027 namespaced claim metadata under `project_claim`; should merge bookkeeping follow the same convention?

## Optional improvements
- `evidence_weight` clamps to the global `BASE_WEIGHT_MAX`. A weight already at the clamp stops tracking the ratio exactly. That's harmless, but worth a sentence in the doc comment.

## Checks run
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | Pass |
| `cargo clippy --locked -p … (six packages) --all-targets -- -D warnings` | Pass (offline build) |
| `cargo test --locked --no-fail-fast` over the six packages | 2475 passed, 3 failed across 167 test binaries |
| `consolidate_test` at head | 11 of 11 pass |
| Head's `consolidate_test.rs` run at base | The 2 new tests fail and the 9 existing ones pass, so the new tests do guard the change |
| Throwaway probe tests in a scratch copy (deleted afterwards) | All four probes failed, reproducing findings 1–4 as quoted above |

About the test run:
- The first full run, with the sandbox's read-only `HOME`, gave 63 `ReadOnlyFilesystem` failures across `remind_me_core`'s `dbs_import`, `export`, `importer` and `mempalace_import` tests. The same sandbox error stops `remind_me_api`'s `import_export_test`. With a scratch `HOME`, all five binaries passed (114 tests).
- The 3 remaining failures are in `peer_discovery_test` (`PermissionDenied` from the sandbox). They fail the same way at base, so they're unrelated to this PR.

Scratch work lived under `$TMPDIR` in temporary git worktrees, which I removed afterwards. The reviewed tree is unchanged at `95528f01`.

**Validation gaps:**
- A live hub and the hub's Postgres store weren't exercised. Finding 2 was shown by applying the real payload to a second node directly, not through `MockHub`.
- Finding 5's race wasn't executed, because it needs a test hook in the window.
- No embedding server was used, so the re-embed step was skipped.

## Verdict
**Changes required**: one P0, four P1 and two P2 findings.
