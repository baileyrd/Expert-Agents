<!-- remind-me/344-r1 run 2, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 914 s, 3308076 tokens, $1.97 -->

# Review of PR 344 (Rusty-Mill/rusty-remind-me): changes required

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `95528f017e2db1caef65aeee2154995333588067`. `git fetch origin pull/344/head` returned this SHA, so the review is pinned.
- **Diff:** one commit (`consolidate: count the evidence behind merged memories`) touching `consolidation.rs`, `vitality.rs`, `tests/consolidate_test.rs` and `RELEASE_NOTES.md`.

The weight boost doesn't replicate: peers receive `evidence: 2` with the old `base_weight`. The "no compounding, capped at 1.5×" claim also breaks after an ordinary metadata edit. Both were confirmed by running a probe.

## What the change touches, and what must hold

- **`memories.metadata.evidence` (new key) and `memories.base_weight`.** Both columns travel in the outbox payload (`engine/memories.rs:240-290`). ADR-0004 says a winning record "updates every column except `created_at`", and `sync/record.rs:375` applies `record.base_weight` as sent. So every node must end up with the same `(evidence, base_weight)` pair, and the queued payload must carry both. The PR's own design adds one more rule: `base_weight` must already include `factor(evidence)`, because the next merge only applies the change in factor.
- **The canonical's merge write** (content, tags, metadata, importance) and superseding the members. All of it should land or none of it, so `evidence` never counts a member that is still live.
- **`remind_me_consolidate`'s contract.** The tool description and schema are unchanged. The `consolidate` doc comment says what a merge writes.

## Blocking findings

### 1. P1: peers get the evidence count but not the weight boost, so nodes disagree (executed)

**Where:** `consolidation.rs:506-526`

**Cause:**
- The outbox payload is a snapshot of the row taken when a write moves `updated_at` (`engine/memories.rs:496-500`, `queued_operation`).
- `set_tags_and_metadata` (`Origin::Sync`, never queued) and then `set_merged` (stamps `updated_at`) queue the row with `evidence: 2`.
- The boosted weight is written afterwards by `Feedback::set_importance`. That call doesn't stamp `updated_at` (`engine/memories.rs:816-831`), so it is never queued.

**Probe result (scratch copy, sync env vars set, each outbox payload decoded the way `push::decode_payload` does, then applied with `upsert_record` to a second store):**
```
LOCAL  base_weight=1.0693147180559945
OUTBOX base_weight=1.0 metadata="{\"evidence\":2}"
PEER   base_weight=1   metadata={"evidence":2}
```

**What goes wrong:**
- **Peer B diverges:** it holds `evidence: 2` with an unboosted weight. If B merges a third memory into the same canonical, it applies only the step from factor(2) to factor(3), giving 1.038 where node A has 1.110. The difference never heals.
- **Node A can lose its boost too** (source-traced): any later edit to the memory on B stamps `updated_at` and ships B's `base_weight` of 1.0. Under LWW (ADR-0004) that record wins on A, so A falls back to 1.0 while keeping `evidence: 2`. The boost is then gone on every node.
- **Who is hurt:** every node, and agents whose search ranking now depends on which node they ask.

**Fix:** compute `base_weight` and vitality before the write that stamps `updated_at`, and store them in that same write. For example, extend `set_merged` to also take `metadata`, `base_weight` and `vitality`, so the canonical is one stamped row and one outbox entry. That also removes the need for the outbox-bypassing `Origin::Sync` helper on a local write.

**Regression test:** a test in its own binary that turns sync on with `test_env::set_var` and runs a real merge. It should decode the last outbox payload for the canonical (as `decode_payload` does), check that its `base_weight` equals the stored one, then apply it with `upsert_record` to a second `Database` and check both `metadata.evidence` and `base_weight` match. This test fails at head.

### 2. P2: a metadata edit drops `evidence`, so the boost compounds and the count is wrong (executed)

**Where:** `consolidation.rs:294-301` and `:497-518`, together with `queries.rs:224-243` / `engine/memories.rs:743-744`

**Cause:**
- `update_memory` replaces `metadata` wholesale, and no key is reserved (`kinds.rs:281-303` validates only decision and action_item fields).
- An agent that sets `metadata: {"note":"x"}` on a merged canonical erases `evidence`, while `base_weight` keeps its boost.
- At the next merge `evidence_of` returns 1, so the full factor is applied again.

**Probe result:** merge (1.069) → update metadata → merge a third memory gives `base_weight=1.1434 metadata={"evidence":2,"note":"x"}`. A single merge of three would give 1.1099 and `evidence: 3`.

**What goes wrong:**
- The stored count is wrong (2 for 3 memories).
- The boost compounds, and repeated edit-and-merge cycles can push it past the advertised 1.5× up to `BASE_WEIGHT_MAX` (3.0).
- The other direction also works: a client can write `metadata.evidence: 1000000` on a memory through add, update or import. Merging it in then gives its canonical the full 1.5×.
- This makes two release-note claims false: "a repeat merge does not compound" and "capped at 1.5×".

**Fix:** either derive the prior boost from something clients cannot write, or reserve `evidence`:
- **Reserve it:** keep the stored value on update unless explicitly refused, and refuse or strip it on add, update and import.

**Regression tests:**
- Merge, update metadata without `evidence`, merge again: assert `evidence == 3` and `base_weight == w0·factor(3)`.
- Add with a client-supplied `evidence`: assert it is refused or ignored.

### 3. P2: a merge can land in parts, leaving `evidence` and the weight out of step permanently (source-traced)

**Where:** `consolidation.rs:506-530`

**Cause:**
- Each call (`set_tags_and_metadata`, `set_merged`, `set_importance`, then one `set_superseded_by` per member) is its own journal batch.
- `apply_merges` does not use `Store::transaction` (`store.rs:63`), which `project.rs:762/881` and the importers do use.

**Failure scenarios:**
- **(a)** The process dies or a write errors after `set_merged` but before `set_importance`. `evidence: 2` is stored and queued, but the weight is unboosted. Because every later merge applies only the change since `before`, the missing boost is never applied.
- **(b)** A `set_superseded_by` fails partway through. The members stay live, a re-run clusters them again, and `after = 2 + 1`, so the same member is counted twice.

Before this PR, non-atomic merges already risked re-summing `access_count`. This PR adds a stored fact whose correctness depends on atomicity.

**Fix:** run each cluster's writes inside `store.transaction(|page| …)`, and re-read the canonical's metadata and `base_weight` inside it.

**Regression test:** use the `store.rs:207` pattern (an error injected inside a transaction). Assert that a failed merge leaves `metadata`, `base_weight` and `superseded_by` all unchanged.

### 4. P2: the merge overwrites `base_weight` from a stale snapshot (source-traced)

**Where:** `consolidation.rs:518`

**Cause:**
- `canonical.base_weight` comes from `fetch_candidates` (`:387`) at the start of the call.
- Clusters are processed one after another, each doing a synchronous re-embed (network I/O).
- The sync worker writes through a `SecondarySource` whose engine tables lock per call (ARCHITECTURE §1 tenet 4, §6). So a pulled record, or a feedback event, can update the canonical's `base_weight` in between.
- `set_importance` then writes `evidence_weight(stale)`, silently discarding that update.

Before this PR the merge wrote only `vitality`, never `base_weight`.

**Fix:** read `base_weight` when it is written (folded into the transaction in finding 3).

**Regression test:** change `base_weight` (for example with `testing::set_memory_column`) after candidates are fetched and before the merge. This is easiest by splitting `apply_merges` so the test can call it with prebuilt clusters. Assert the result is built on the new value.

### 5. P2: the `consolidate` doc comment no longer matches the code

**Where:** `consolidation.rs:373`

It still says "A merge updates the canonical's content/access_count/tags/vitality". It now also rewrites `metadata` and `base_weight`. Update the comment.

## Failure classes checked

- **Half a change:** findings 3 and 4.
- **Hidden rows:** candidate selection is unchanged. `get_many` reads only the cluster's own ids. Ruled out.
- **Time as text:** no new comparisons; the timestamp is still `now_iso`. Ruled out.
- **Wire versus row:** finding 1. The `evidence` value is a JSON integer that survives the JSON text column and `decode_payload`. `base_weight` is an f64 that does round-trip, but the wrong value is queued.
- **Concurrency:** finding 4.
- **Status that lies:** the reply doesn't mention evidence, but nothing is reported as succeeding when it failed. Ruled out.
- **Retrieval admission:** no search-stage change. The weight enters ranking only through `base_weight`. Ruled out.
- **Tool surface:** no new tool or argument; the description and schema are unchanged, and the change applies the same way to every client. Ruled out (ADR-0028/0030).
- **Boundary skipped:** the merged metadata comes from the stored row; no new content is stored. A non-object `metadata` becomes `{}`, which matches existing practice (`attachments.rs:113`, `code_refs.rs:190`, `sync/record.rs:300`). Ruled out.
- **Who is calling:** no auth path touched. Ruled out.
- **Protocol subset:** no parser or client touched. Ruled out.
- **Silent success:** finding 1 (the push carries a record that says something different from the stored row) and finding 2 (client-supplied `evidence` is trusted).

## Design questions

- With concurrent merges of the same canonical on two nodes, LWW keeps one summary and one `evidence`, while each node supersedes its own members into that canonical. The count then understates what the canonical stands for. ADR-0004 settles LWW, so this is for the owner: should `evidence` be derived from the `superseded_by` back-references rather than stored as a counter?

## Optional improvements

- **Constant docs:** `vitality.rs:107-117` now sits between the shared "Ceiling and floor" doc comment and `BASE_WEIGHT_MIN`, and `EVIDENCE_FACTOR_MAX` has no doc of its own. Keep the two `BASE_WEIGHT_*` constants together.
- **Duplicate tag write:** the merged tags are written twice (`:506` and `:507`).

## Tests: do the new ones guard the change?

- I copied the PR's `consolidate_test.rs` onto the base tree and ran it with its own target dir. `a_merge_records_evidence_and_raises_base_weight` and `evidence_accumulates_across_merges_without_compounding_the_boost` both **fail at base** (`left: Null, right: 2/3`), so they do guard the change. (A first run that shared the head's target dir reused the head's build and passed falsely; I discarded it.)
- The four new unit tests use new functions, so they don't compile at base.
- **Coverage gap:** no test decodes a merge's real outbox payload or applies it on a peer. That gap is why finding 1 went unnoticed. Finding 1's regression test belongs in its own binary, because it depends on environment variables.

## Checks run

All runs used a `git archive` of the head in scratch space, with `--offline` and `CARGO_TARGET_DIR` inside scratch (the inherited target dir is read-only).

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy --locked -p remind_me_core -p remind_me_mcp -p remind_me_api -p rusty-remind-me -p remind_me_remote -p remind_me_hub --all-targets -- -D warnings` | pass (forced a recheck; all five workspace crates it rebuilt were checked) |
| `cargo test --locked --no-fail-fast` over the same six packages, with `HOME` set to scratch | 166 test binaries OK, 2481 tests passed. 1 target failed: `remind_me_core --test peer_discovery_test`, 3 tests, all `EPERM` binding a Unix socket in the sandbox. Unrelated to this diff. |
| `consolidate_test` and `consolidation::` unit tests at head | 11/11 and 18/18 pass |
| Probe `zz_probe_evidence_sync` (scratch only, then deleted) | produced the outputs quoted in findings 1 and 2 |

## Validation gaps

- **`peer_discovery_test`:** couldn't run because the sandbox refuses the Unix-socket bind. Rerun on an unrestricted host.
- **Hub storage:** I didn't check the hub's Postgres store, since no Postgres was available. Run the `remind_me_hub` Postgres tests to confirm the `metadata.evidence` and `base_weight` round-trip.
- **Live round trip:** no live hub was available. Finding 1 was shown with `upsert_record` on a second store, not through `MockHub` push/pull. A `MockHub` round-trip test would confirm it end to end.
- **Environment workaround:** the first full run failed on `remind_me_api` import tests because `$HOME` is read-only here. That is environmental; they passed once `HOME` pointed at scratch.

## Verdict: changes required

I made no changes to the reviewed tree: no commits, comments or review posts.
