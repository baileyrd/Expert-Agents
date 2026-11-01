<!-- remind-me/344-r1 run 4, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 368 s, 2098451 tokens, $1.54 -->

# Review of PR 344 (Rusty-Mill/rusty-remind-me): "consolidate: count the evidence behind merged memories"

**Verdict: changes required.** The evidence count syncs to peers, but the weight boost it describes does not, so nodes end up disagreeing. A merge interrupted partway leaves a count that a retry then overstates. I ran probes for both.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `95528f017e2db1caef65aeee2154995333588067`. The fetched `pull/344/head` matches it.
- **Diff:** `consolidation.rs`, `vitality.rs`, `tests/consolidate_test.rs`, `RELEASE_NOTES.md` (one commit).

## What must hold for each touched item
- **The canonical memory row (`metadata.evidence`, `base_weight`, content, tags):** `metadata.evidence` must equal the number of memories the canonical stands for. `base_weight` must carry exactly the boost for that count, so each merge only applies the change in boost. This has to hold on every node, not just the one that merged.
- **The outbox entry:** what it queues is what peers apply. The receiver takes `base_weight` and `metadata` straight from the record (`sync/record.rs:375`), and metadata is merged key by key as ADR-0004 specifies.
- **The merge as a whole:** it either lands completely or not at all. ADR-0027 sets that standard for writes that depend on each other, and `Store::transaction` (`db/store.rs:63`) exists for it.

## Blocking findings

### 1. P1: Peers receive the evidence count without the weight boost and diverge permanently (executed)
- **Where:** `consolidation.rs:504-526`, with `engine/memories.rs:589` and `:818`.
- **Why:** The four writes behave differently:
  - `set_tags_and_metadata` writes `evidence` as `Origin::Sync`, so it is never queued.
  - `set_merged` stamps `updated_at`, which queues a copy of the row as it stands at that moment.
  - `set_importance` then writes the boosted `base_weight` without a stamp, so it is never queued.
  
  The queued record therefore carries the new `evidence` with the old `base_weight`. The comment at :504 says the metadata is included, which is true, but its pair is not.
- **Probe result** (sync enabled on node A; the queued payload decoded the way push decodes it, then applied on node B with `upsert_record`):
  ```
  A row:   evidence=2 base_weight=1.0693…
  A queue: op=update metadata={"evidence":2} base_weight=1.0
  B apply: Applied evidence=2 base_weight=1
  third duplicate merged: on A -> 1.1099, on B -> 1.0379
  ```
- **Effect:** B believes the boost is already applied when it isn't. Every later merge on B applies only the increment, so B's boost stays too low for good. If B ever edits that memory, A pulls the edit and loses its own boost while still showing `evidence: 2`.
- **Who is hurt:** agents on every other node get a different ranking for the same fact, and the release note's claim that "a repeat merge does not compound" is false on peers.
- **Fix:** compute the boosted weight first and write it together with `metadata` before the stamped `set_merged`, or have `set_merged` take `base_weight` and `metadata`. That puts the evidence count and the weight in the same outbox record.
- **Regression test:** with sync enabled, merge on A, decode the real outbox payload, apply it to a second database, and assert both nodes hold the same `base_weight` and `evidence`. This fails at head today.

### 2. P1: An interrupted merge leaves a count that a retry inflates (executed)
- **Where:** `consolidation.rs:506-529`.
- **Why:** the merge is four separate commits with no transaction around them. The first one writes the new `evidence`. If the process dies or `set_merged`/`set_importance` returns an error after that write, the cluster is still found on retry. The retry reads the already-raised count as `before` and adds the member again.
- **Probe result** (the first write lands, then consolidate is re-run): two memories end up with `evidence=3` and `base_weight=1.0379`. A clean merge gives `evidence=2` and `1.0693`. Both the count and the weight are wrong, and both get queued to peers.
- **Context:** base already summed `access_count` across these same non-atomic writes. This PR adds a counter that is not safe to retry, so the gap now corrupts a fact that agents read.
- **Fix:** run each cluster's writes (metadata, `set_merged`, importance, each `set_superseded_by`) inside one `store.transaction(...)`. Keep the re-embed outside it, because it does network I/O.
- **Regression test:** inject an error after the first write (for example, the canonical deleted inside a test hook), retry, and assert `evidence == 2` and that `base_weight` matches a single merge.

### 3. P2: No sync test covers the new field
The new tests only read the local row. Nothing decodes a payload produced by the real outbox, which is why Finding 1 went unnoticed. The test described in Finding 1 belongs in `tests/consolidate_test.rs` under the env lock, or in its own binary, as `outbox_test.rs` does.

## Design question
`evidence` is a counter, but ADR-0004 resolves `metadata` last-write-wins, one key at a time. Suppose two nodes each merge a different duplicate into the same canonical at the same time. One `evidence: 2` overwrites the other, while three memories now point at the canonical. Under the ADR that is settled behaviour, but if the count is meant to be accurate, the owner should decide whether it should be recomputed from `superseded_by` pointers instead.

## Optional improvements
- `apply_merges` re-reads `metadata` (via `get_many`) but takes `base_weight` from the candidate snapshot made before clustering and before earlier clusters' re-embeds. Reading both from the same `get_many` closes a lost-update window with the sync worker thread.
- The doc comment on `consolidate()` lists the fields a merge updates and should add `base_weight` and `metadata.evidence`. In `vitality.rs`, the new constants now sit between `BASE_WEIGHT_MAX` and `BASE_WEIGHT_MIN`, separating the "Ceiling and floor" comment from the floor. `EVIDENCE_FACTOR_MAX` has no doc comment.

## Failure classes ruled out
- **Hidden rows:** `get_many` only covers live candidates.
- **Time as text:** no timestamps added.
- **Retrieval admission:** the boost is capped at 1.5× and within the `base_weight` bounds.
- **Tool surface:** no new arguments; the tool description makes no field-level promise.
- **Scrubbing boundaries and caller identity:** not touched.
- **Hand-rolled protocol parsing:** none touched.
- **Hub float round-trip:** `base_weight` round-trips as a JSON float.
- **Silent success:** none found, apart from Finding 2's error path.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | Pass |
| `cargo clippy --locked …` (six packages, `--all-targets -D warnings`) | Pass |
| `-p remind_me_core --lib consolidation` | 18 passed |
| `--test consolidate_test` | 11 passed |
| `--test outbox_test` | 13 passed |
| `-p remind_me_core --lib` (full) | 527 passed, 1 failed (see below) |
| New integration tests run against base code | Both fail, so they do guard the change. New unit tests can't compile at base. |
| Scratch probes | Results above |

- **The one lib failure:** `dbs_import::a_dbs_page_imports_and_a_rerun_updates_what_changed` hits a read-only file system. It writes under the home directory, which is read-only in this sandbox, and is unrelated to the diff.
- **Build setup:** the environment's `CARGO_TARGET_DIR` is read-only, so I built in `$TMPDIR/target`.
- **Stale result I caught:** the first base run reused the head build through the shared target directory and wrongly passed. I re-ran it with its own target directory, and the deleted copy was my own scratch.

**Not run:**
- The full `cargo test` across all six packages, because the sandbox's read-only home breaks tests that write there.
- The hub's Postgres store, since there is no Postgres here. The diff makes no schema change, but whether `metadata.evidence` survives a round trip through the hub needs a `MockHub` push/pull test or a live hub.
