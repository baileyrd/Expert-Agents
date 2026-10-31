<!-- remind-me/344-r1 run 3, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 310 s, 1736132 tokens, $1.52 -->

# Review of PR 344 (Rusty-Mill/rusty-remind-me): changes required

The PR adds `metadata.evidence` to the canonical memory of a merge and boosts its `base_weight` by that count. I found six blocking defects. The worst is that a merge overwrites any `evidence` value an agent already stored on the memory, with no revision kept. The boosted weight also never reaches other nodes, and the merge is still several separate writes, so a crash or retry miscounts.

I could not build anything, so every finding below is traced through the source, not executed. `cargo fmt` is the only check that ran.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head reviewed:** `95528f017e2db1caef65aeee2154995333588067`. I fetched `pull/344/head` and it equals the head SHA.
- **Commit:** one, `95528f017 consolidate: count the evidence behind merged memories`.
- **Files changed:** `RELEASE_NOTES.md`, `consolidation.rs`, `vitality.rs`, `tests/consolidate_test.rs`.

## Invariants (step 1)

| Touched | Invariant |
|---|---|
| Canonical row: `metadata` | A merge adds facts. It never destroys a key an agent wrote. `metadata` is "Free-form JSON" in the tool schema (`remind_me_mcp/src/lib.rs:668`). |
| Canonical row: `base_weight` | Every node holds the same weight for the same row and evidence. Under ADR-0004 the pushed row is the record the peer stores. |
| `metadata.evidence` | Equals the number of memories the canonical stands for, on every node, after crashes, retries, edits and reverts. |
| Merge write set: metadata, content, weight, member supersession | All of it lands or none of it does. `Store::transaction` exists for this (`db/store.rs:63`) and is already used for claims (`project.rs:762`, `881`). |
| Outbox payload | The payload queued for a row is the row as committed. Push sends the snapshot taken at queue time (`sync/push.rs:90-103`). |

## Blocking findings

### 1. P0: a merge overwrites the canonical's own `metadata.evidence`
- **Where:** `crates/remind_me_core/src/consolidation.rs:294-300` and `:503`.
- **Trigger:** an agent stores a memory with metadata like `{"evidence": ["PR #12", "https://…"]}`. The schema calls metadata free-form, and ADR-0027 uses "evidence" for exactly this kind of provenance.
- **Trace:**
  - Consolidation picks that memory as canonical.
  - `evidence_of` returns 1 for anything that is not an integer of at least 1.
  - Line 503 then replaces the key with `json!(after)`.
  - Consolidation captures no revision (`queries.rs` comment above `capture_revision`), so nothing can restore it.
  - `set_merged` queues the row. Peers shallow-merge it and the winner's key replaces theirs (`sync/record.rs:351-352`), so the list is lost on every node.
- **Who is hurt:** an agent reading the memory loses the record of what the fact rests on.
- **Fix:** use a reserved, namespaced key (for example `metadata.consolidation.evidence`), or refuse to overwrite an `evidence` key that is not a valid count.
- **Regression test:** merge a canonical whose `metadata.evidence` is a list, then assert the list is still there.

### 2. P1: the boosted weight never leaves the node, so peers diverge
- **Where:** `consolidation.rs:506-526`, together with `db/engine/memories.rs:499-503`, `518-531` and `818-831`.
- **Trace:**
  - `set_tags_and_metadata` writes with `Origin::Sync`, so nothing is queued.
  - `set_merged` stamps `updated_at`, which queues the row as it stands at that moment: `evidence: 2`, but the old `base_weight`.
  - `set_importance` (the call that writes the boost) does not stamp `updated_at`, so it queues nothing.
  - Peer B therefore stores `evidence: 2` with the unboosted weight 1.0. Node A holds 1.0693.
  - Later B merges C with M3. It divides by f(2) a weight that never carried f(2), giving 1.038 where 1.110 was intended.
  - B's row wins LWW back on A and carries B's unboosted pre-merge weight, so A loses its boost too.
- **Who is hurt:** other nodes and every agent ranking against them. The release note's "a repeat merge does not compound / only the change is applied" is false on every node that did not perform the merge.
- **Fix:** write the weight and vitality before the stamped write, or in the same stamped write. Better still, apply the evidence factor at scoring time from `metadata.evidence`, so no ratio against a stored weight is needed.
- **Regression test:** sync enabled; merge; decode the last outbox payload for the canonical; assert `payload.base_weight` equals the stored `base_weight`. Then apply it through `MockHub` to a second node and compare. No current test decodes a payload the real write path produced.

### 3. P1: the merge is not atomic, and the new counter is not safe to retry
- **Where:** `consolidation.rs:491-530`. Each call takes its own lock and commits (`engine/memories.rs:513`), and `apply_merges` uses no `store.transaction`.
- **Trigger A, crash or error:**
  - The process dies, or `set_merged` / `set_importance` / `set_superseded_by` returns `Err`, after line 506 has committed `evidence: 2`.
  - Nothing is superseded, so the next consolidate finds the same cluster: before = 2, after = 3.
  - Result: `evidence: 3` for two memories, and weight 1.038 instead of 1.069.
  - If line 507 also committed, `evidence: 2` has already been queued to peers while no member is superseded.
- **Trigger B, concurrent write:**
  - The sync worker writes through a `SecondarySource` that locks per call (`ARCHITECTURE.md` §1 tenet 4, §6).
  - If it applies a peer's metadata edit to C between `get_many` (line 493) and line 506, consolidation writes its stale copy over it.
  - The newer `updated_at` then pushes that stale copy everywhere, so the peer's edit is lost on all nodes.
- **Fix:** run each cluster's merge, from reads through supersession, inside `store.transaction`.
- **Regression tests:** an error injected after the metadata write leaves the row unchanged; running consolidate twice over a half-applied cluster yields `evidence: 2`.

### 4. P2: replacing or reverting metadata drops `evidence` but keeps the boost
- **Where:** `engine/memories.rs:743-744` (an update replaces `metadata` wholesale) and `history.rs:159-178` (revert restores `metadata`, but `base_weight` is not tracked).
- **Trace:** merge (`evidence 2`, weight 1.0693) → `remind_me_update` with `metadata: {"note":"x"}` → merge a third memory. Now before = 1, after = 2, weight = 1.0693² = 1.143, against 1.110 for a single three-way merge. `evidence` reads 2 while the canonical stands for 3.
- **Fix:** carry the reserved key across metadata replace and revert, or remove the dependence on a stored ratio (see finding 2).
- **Regression test:** the sequence above, asserting weight 1 + 0.1·ln 3 and `evidence: 3`.

### 5. P2: `evidence` from any writer is trusted, and the sum can overflow
- **Where:** `consolidation.rs:294-300` and `:498`.
- **Trace:**
  - `remind_me_remember`, or a peer, stores `metadata.evidence: 1000000` on a member. One merge gives the canonical the full 1.5× cap.
  - The same key on a canonical suppresses its boost.
  - Two members carrying `u64::MAX` overflow the `sum()`. In debug builds that panics on the write path. In release it wraps (no `overflow-checks` profile is set), e.g. 1 + MAX stores `evidence: 0`.
- **Fix:** validate or strip the reserved key at the add, update and sync-apply boundaries, and use `saturating_add`.
- **Regression test:** a member with a forged or huge count produces a bounded, correct count.

### 6. P2: docs disagree with the code
- `consolidation.rs:373`: the `consolidate` doc says a merge updates "content/access_count/tags/vitality". It now also rewrites `metadata` and `base_weight`.
- `vitality.rs:107-117`: the new constants were inserted between `BASE_WEIGHT_MAX` and `BASE_WEIGHT_MIN`. The "Ceiling and floor" doc now covers only the max, and `BASE_WEIGHT_MIN` and `EVIDENCE_FACTOR_MAX` have no doc.
- The `remind_me_consolidate` tool description (`remind_me_mcp/src/lib.rs:1543`) does not say a merge changes weight.
- The release note's no-compounding claim is false (findings 2–4).
- **Fix:** correct the comments, tool text and release note.

## Failure classes ruled out
- **Hidden rows:** only a number crosses from sensitive or other-project members; no text is copied.
- **Time as text:** not involved.
- **Retrieval admission:** no stage changed.
- **Tool surface:** no new arguments.
- **Wire versus row:** an f64 round-trips through serde_json, and the hub stores metadata as text. There is no hub or schema change.
- **Who is calling:** not involved.
- **Silent success:** the report lists the clusters that were merged.

## Design question
Two nodes merging into the same canonical at the same time each write `evidence: 2`. LWW (ADR-0004, which is settled) keeps 2, while 3 rows point `superseded_by` at C. Should the count be derived from the replicated supersession graph rather than kept as a mutable counter? That would also remove findings 3 and 4.

## Optional improvement
`evidence_weight` clamps at `BASE_WEIGHT_MAX`, so a weight near 3.0 combined with a later demotion gives order-dependent results. Consider documenting this.

## Tests
- The new unit and integration tests reference symbols and fields that do not exist at the base. They would fail to compile there or go red, so they do guard the change. I traced this rather than ran it.
- Missing coverage, belonging in `tests/consolidate_test.rs` and a sync-enabled binary:
  - the outbox payload decode (finding 2)
  - a half-applied merge followed by a retry (finding 3)
  - metadata replace or revert, then a merge (finding 4)
  - preserving a non-integer `evidence` value (finding 1)
  - forged or overflowing counts (finding 5)

## Checks and validation gaps
- **`cargo fmt --all -- --check`:** ran, passed.
- **`cargo clippy …` and `cargo test --locked …`:** not run. `~/.cargo` is read-only, so I retried with a scratch `CARGO_HOME`. That needs the pinned `rusty_mill` git dependency (`libs-2026.10.0`), which is neither vendored nor cached, and the sandbox denied `github.com`. To settle this, run the six-package clippy and test commands, plus the probes I wrote, on a machine with network access.
- **Probes:** written to `$TMPDIR/pr344-review-45zRRO/src/crates/remind_me_core/tests/pr344_probe.rs`. They cover the outbox payload, a half-applied retry, metadata replace and forged evidence. They never ran.
- **Hub Postgres and `MockHub`:** not run.

**Scratch-space mistake you should know about:** `$TMPDIR` is shared with other sessions. My first `mkdir -p $TMPDIR/probe $TMPDIR/cargo-home` reused directories that already existed. I extracted the head tree over `$TMPDIR/probe` and copied the cargo registry into `$TMPDIR/cargo-home`, overwriting matching files that may belong to another session. I did not clean them up. Everything after that went into the unique `pr344-review-45zRRO` directory.

**Verdict: changes required.**
