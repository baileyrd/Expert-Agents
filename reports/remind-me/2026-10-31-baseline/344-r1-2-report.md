<!-- remind-me/344-r1 run 2, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 206 s, 1229055 tokens, $1.08 -->

I couldn't build or run anything, so this review is from reading the source only. There are four blocking defects; the verdict is **changes required**.

## PR 344 · Rusty-Mill/rusty-remind-me
- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head reviewed:** `95528f017e2db1caef65aeee2154995333588067`. The fetched `pull/344/head` matched this SHA, and so did the checkout.
- **Diff:** one commit touching `consolidation.rs`, `vitality.rs`, `consolidate_test.rs` and `RELEASE_NOTES.md`.

### What the diff touches, and what must hold
- **Canonical memory row, `metadata.evidence`:** the number of memories the canonical stands for. Under ADR‑0004 metadata syncs by shallow merge, so every peer should hold the same count, and the count should only include memories that actually back the fact.
- **Canonical row, `base_weight`:** the PR's own rule is that the weight already carries the boost for the old evidence count, and a merge applies only the change. That rule only holds where the weight and the count travel together. `base_weight` is part of the sync payload (`db/engine/memories.rs:281`), and sync applies it on last-write-wins (`sync/record.rs:375`).
- **Outbox:** an entry is queued only by a local write that changes `updated_at`, and it carries a snapshot of the row at that moment (`db/engine/memories.rs:499-503`, `518-530`).
- **The merge itself:** content, metadata, weight and member supersession should land together or not at all. `Store::transaction` exists for this (`db/store.rs:63`).

## Blocking findings

**1. P0: a client's own `metadata.evidence` is overwritten and lost**
- **Where:** `consolidation.rs:491-506` (`evidence_of` at `:294`).
- **Trigger:** `metadata` is free-form on remember and update (`remind_me_mcp/src/lib.rs:668`, `:742`), and no key is reserved.
- **Scenario (source-traced):**
  1. An agent stores a decision with `metadata: {"rationale": "...", "evidence": "https://ci/run/812"}`. Something like `{"evidence": ["mem_a", "mem_b"]}` is just as likely, since "what it rests on" is exactly what agents record.
  2. A later consolidation picks that memory as canonical.
  3. `evidence_of` treats the string or array as malformed and counts it as 1, then `canonical_metadata["evidence"] = json!(2)` replaces the value.
  4. `set_tags_and_metadata` writes with `Origin::Sync`, so no revision records the old value.
  5. The new value syncs to every peer.
- **Who is hurt:** the user loses the provenance they wrote, and every agent reading the memory sees "2" where a source used to be.
- **Second effect:** a client can also write `{"evidence": 1000}` and get the 1.5× cap on its next merge without anything backing it.
- **Required correction:** keep the count under a key the system owns (for example a nested `consolidation: {evidence: n}` block, the way `project_claim` is nested), or refuse/strip that key on every client write path and on sync apply.
- **Regression test:** canonical with `metadata.evidence = "url"` → after a merge, the URL is still there and the count is stored separately.

**2. P1: peers end up with the count but not the weight boost**
- **Where:** `consolidation.rs:504-526`.
- **Scenario (source-traced):**
  1. Sync is enabled. Node A merges C (weight 1.0) with M.
  2. `set_tags_and_metadata` (`Origin::Sync`) queues nothing.
  3. `set_merged` stamps `updated_at` and queues a snapshot with `evidence: 2` and `base_weight: 1.0`.
  4. `Feedback::set_importance` then writes 1.0693 but leaves `updated_at` alone, so nothing is queued (`engine/memories.rs:818-831`).
  5. Peer B applies the snapshot and holds `evidence: 2` with weight 1.0.
  6. A later merge on B uses before = 2, after = 3, so B gets 1.0·f(3)/f(2) = 1.040 while A has 1.110. The f(2) boost is lost on B for good.
  7. In reverse, any stamped edit on B (resolve, update) ships weight 1.0 back to A. A keeps `evidence: 2` but loses its boost.
- **Who is hurt:** other nodes, and agents whose ranking differs from node to node.
- **Docs this makes false:** the code comment "this metadata included, in the sync outbox" suggests the merge is fully replicated, and the release note says "a repeat merge does not compound." Neither holds once a peer is involved.
- **Required correction:** set the boosted `base_weight` (and vitality) inside the same `updated_at`-stamped write as `set_merged`, or stamp the importance write, so one outbox snapshot carries both the count and the weight.
- **Regression test:** with sync on (under the env lock, as in `graph_sync_test.rs`), merge, then decode the canonical's newest outbox payload produced by the real write path. Assert `payload.base_weight == row.base_weight` and `metadata.evidence == 2`. Then apply it to a `MockNode` peer and compare.

**3. P1: a merge that fails partway counts the evidence twice on retry**
- **Where:** `consolidation.rs:504-530`. There are four separate commits and no `store.transaction`, even though `project.rs:762` uses one for the same reason.
- **Scenario (source-traced):**
  1. Metadata is written with `evidence: 2`.
  2. `set_merged`, `set_importance` or `set_superseded_by` then returns `Err` (the `?` stops the loop), or the process dies.
  3. M is not superseded, so the next `consolidate` finds the same cluster again.
  4. Now before = 2 and after = 2 + 1 = 3. Two memories are recorded as three, and the weight is scaled by f(3)/f(2) from a weight that was never boosted.
- **Who is hurt:** agents read a count that is wrong, and the weight no longer matches f(evidence). The existing access-count summing has the same weakness, but this PR adds a new count that can't be safely retried and makes it the first write.
- **Required correction:** run each cluster's metadata, merge, importance and supersede writes inside one `store.transaction`, with the re-embed outside it.
- **Regression test:** make `set_superseded_by` fail (for example, delete the member between the read and the write through a test hook), run consolidate again, and assert `evidence == 2`.

**4. P1: a reverted or abandoned decision counts as support**
- **Where:** `consolidation.rs:498`.
- **Why it happens:** `consolidation_candidates` only filters on `status == "active"` and liveness (`engine/vectors.rs:255`). `resolve` halves the weight but leaves the status `active` (`resolve.rs:114-123`).
- **Scenario (source-traced):**
  1. D1 "we will use X" is resolved as `reverted`.
  2. D2 "use X for the hub" is still live, and the two are similar enough to cluster.
  3. D2 becomes canonical because its vitality is higher. D1's 1 is added: `evidence: 2`, and D2 gains about 6.9%.
- **Who is hurt:** agents. A decision that was backed out now makes the one that replaced it rank higher, the opposite of what ADR‑0031 and the resolve demotion are for.
- **Required correction:** only count members whose `outcome` is not `reverted` or `abandoned` (or leave such memories out of candidates).
- **Regression test:** a cluster with a reverted member → `evidence == 1` and the weight unchanged.

**5. P2: no tests cover the changed sync and failure behaviour**
- **What is covered:** the new unit and integration tests would fail at base. The integration tests check `metadata["evidence"]`, and the unit tests call functions that don't exist there.
- **What is missing:** a test that decodes a payload produced by the real write path (#2), a test for a failure partway through a merge (#3), a test that a client's own `evidence` key survives (#1), and a test that resolved members are not counted (#4).
- **Where they belong:** `crates/remind_me_core/tests/consolidate_test.rs`, plus a sync test binary that runs under its own env lock.

## Design questions
- **Concurrent merges on two nodes:** A merges C+M1 and B merges C+M2. ADR‑0004's shallow merge keeps one node's `evidence: 2`, even though C now stands for three memories. That outcome is what the ADR decided, but it means the count can never be exact across nodes. The owner should decide whether the count is worth syncing, or whether it should be derived from the `superseded_by` links pointing at C.

## Optional improvements
- `vitality.rs`: the new constants sit between `BASE_WEIGHT_MAX` and `BASE_WEIGHT_MIN`, which splits the "ceiling and floor" doc comment from the floor constant. `EVIDENCE_FACTOR_MAX` has no doc comment.
- The `remind_me_consolidate` tool description doesn't mention that a merge now changes `base_weight` and `metadata.evidence`, which agents will see in recall results.

## Checks and validation gaps
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | **Passed** (exit 0) |
| `cargo test -p remind_me_core --test consolidate_test` | **Could not run.** `~/.cargo` is read-only, and with a scratch `CARGO_HOME` the git dependency `Rusty-Mill/rusty_mill@libs-2026.10.0` couldn't be fetched: the sandbox denies `github.com:443` |
| `cargo clippy …` (six packages) | **Not run**, for the same reason |
| Running the new tests at base | **Not run**, for the same reason |
| Executed probes (outbox payload after a merge, retry after a partial merge) | **Not run**, for the same reason |

All five findings are traced from the source; none were executed. To settle them, allow `github.com` (or provide a vendored rusty_mill), then run the clippy and test commands over the six packages plus the probes described in #2 and #3. Postgres hub storage of the metadata was not exercised.

## Verdict: **changes required**
