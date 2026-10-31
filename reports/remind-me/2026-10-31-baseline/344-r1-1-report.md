<!-- remind-me/344-r1 run 1, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 212 s, 1403644 tokens, $1.16 -->

I found four blocking defects (each P1) and one P2, so the verdict is **changes required**. I couldn't compile or run anything because github.com is blocked. Every finding below is traced through the source; none is executed.

**PR:** Rusty-Mill/rusty-remind-me #344, "consolidate: count the evidence behind merged memories"
**Base:** d5528b1347054f348b3dac96f54619e603f4594c
**Reviewed head:** 95528f017e2db1caef65aeee2154995333588067. The fetched `pull/344/head` matches it, and the PR is one commit.

## What the PR touches, and what must hold

| Item | Invariant |
|---|---|
| `memories.metadata.evidence` (new key) | It equals the number of memories the canonical stands for. It must stay true across retries, edits, imports and concurrent merges on different nodes, and still be true after sync. |
| `memories.base_weight` on the canonical | Every node holds the same value: `base_weight` is a synced column (`sync/record.rs:111`, `:375`; hub `record.rs:54`). The boost is applied exactly once per unit of evidence, and the value stays within `BASE_WEIGHT_MIN..=MAX`. |
| Outbox payload from a merge | It is the record the node stores. The payload is a snapshot taken when the write happens (`db/engine/memories.rs:501`), not re-read at push time (`sync/push.rs:90`). |
| `apply_merges` writes (metadata, content, importance, supersede) | All land or none do. `Store::transaction` exists for this (`db/store.rs:63`), and ADR-0027 already uses it. |
| `remind_me_consolidate` tool | Its schema doesn't change. The tool description doesn't mention base_weight, so it isn't made false. |

## Blocking findings

### F1 (P1): the boosted `base_weight` never reaches the outbox, so peers diverge
**Where:** `crates/remind_me_core/src/consolidation.rs:506-526`

**Trace (source-traced):**
1. With sync on, node A merges X and Y.
2. `set_tags_and_metadata` (`:506`) writes with `Origin::Sync`, so nothing is queued.
3. `set_merged` (`:507`) stamps `updated_at`. `write()` then snapshots `row.payload()` into the outbox (`engine/memories.rs:499-502`). That payload carries `evidence: 2` but the **old** `base_weight`.
4. `set_importance` (`:526`) writes the boosted weight. It doesn't stamp `updated_at` (`engine/memories.rs:818`), so nothing is queued.
5. Peer B applies the payload through `upsert_synced`. B now holds `evidence: 2` with the unboosted weight, and A holds the boosted one.

**Why it lasts:** B's next merge reads `before = 2` and applies only `factor(3)/factor(2)`, so B never gets the first boost. When B later edits X and pushes, A is overwritten with B's lower weight while A's `evidence` still says 2.

**Who is hurt:** every node ranks X differently, and agents on different surfaces get different recall.

**Fix:** write `base_weight` (and vitality) before the write that stamps `updated_at`, or fold them into that write, so the snapshot is the stored row.

**Regression test:** enable sync as in `outbox_test.rs` (`ensure_sync_enabled`), run a merge, and decode the last `testing::outbox_rows` payload for the canonical. Assert its `base_weight` equals the stored row's. It goes red at this head.

### F2 (P1): `evidence` is a caller-writable key, so the count and the "no compounding" claim break
**Where:** `consolidation.rs:497-498`

The key lives in free-form metadata. The add schema calls metadata "Free-form JSON stored with the memory" (`remind_me_mcp/src/lib.rs:668`), and `update_memory_with` replaces metadata wholesale (`db/queries.rs:224`, `:242`). Three traces, all source-traced:

- **Compounding:** a merge leaves X with `evidence: 2` and weight `w·1.069`. An agent then calls update with `metadata: {"rationale": "…"}`, which drops `evidence` but keeps `base_weight` (`queries.rs:202`). The next merge reads `before = 1` and applies the full boost again: `w·1.069²`. It also records `evidence: 2` for 3 memories. The release note says "a repeat merge does not compound", and this makes it false.
- **Inflation:** a caller, an import, or a peer writes `{"evidence": 150}` on a near-duplicate. One merge then lifts the canonical to the full 1.5× cap. The same number on the canonical instead suppresses any later boost.
- **Overflow:** two memories carry `{"evidence": 18446744073709551615}`. The `u64` `.sum()` at `:498` panics on a write path in debug builds. Release builds wrap; for example, `u64::MAX + 1` stores `evidence: 0`.

**Who is hurt:** agents reading ranked results, plus the whole store if consolidate panics.

**Fix:**
- Keep the count where callers can't write it: a reserved, namespaced key that is stripped or refused on add, update, import and sync, or a real column.
- Keep it on metadata replacement.
- Use `checked_add` or `saturating_add`, and cap the value when reading it.

**Regression tests:**
- Merge, update the metadata, merge again; assert `evidence == 3` and the weight equals `w·factor(3)`.
- A caller-supplied `evidence` is refused or ignored.
- Two `u64::MAX` counts don't panic.

### F3 (P1): a partly applied merge double-counts evidence when it is retried
**Where:** `consolidation.rs:506-530`

Every write runs in its own engine commit, and nothing wraps them in `Store::transaction`.

**Trace (source-traced):**
1. The process dies or errors after `:506` but before `set_superseded_by` (`:529`). X now has `evidence: 2`, but Y isn't superseded. If it died before `:526`, the boost was never applied either.
2. The next consolidate finds the same cluster: `before = 2`, `after = 3`. X records 3 for a merge of 2 memories, and the first boost may be lost.

Before this PR a retry was idempotent: content was rewritten and access counts were re-summed. The new counter isn't idempotent, so this PR is what makes the failure reachable.

**Fix:** run each cluster's writes in `store.transaction(|page| …)`. The best-effort re-embed can stay outside it.

**Regression test:** inject a failure between the importance write and the supersede (for example a supersede target that errors). Assert the canonical's metadata and `base_weight` are unchanged, then that a clean re-run gives `evidence: 2`.

### F4 (P1): concurrent merges on two nodes lose evidence
**Where:** `consolidation.rs:497-506`, together with the sync apply in `sync/record.rs:300-313`

`evidence` is a counter kept inside a last-write-wins (LWW) row. ADR-0004 makes metadata merge shallowly, with the LWW winner's value taking each key.

**Trace (source-traced):**
1. Node A merges X+Y and node B merges X+Z. Both write `evidence: 2`.
2. After sync, both Y and Z are `superseded_by` X everywhere, but X says `evidence: 2`, and the boost reflects 2.

The ADR's LWW rule is settled; the defect is choosing a counter that can't converge under it.

**Fix:** derive the count instead of storing it. Count the canonical plus the sum of `evidence` over rows `superseded_by` it. That converges, because each member's `superseded_by` syncs on its own.

**Regression test:** use two stores and run the real outbox payloads through `upsert_record` (or `MockHub`). Assert `evidence == 3`.

### F5 (P2): stale doc comment and missing sync coverage
- **Stale comment:** `consolidation.rs:373` still says a merge "updates the canonical's content/access_count/tags/vitality". It now also rewrites metadata and `base_weight`.
- **Missing tests:** none of the new tests decode a payload produced by a real merge write (§4 of the review steps requires one for sync behaviour). Nothing covers F1–F4. The tests belong in `crates/remind_me_core/tests/consolidate_test.rs`, plus a sync test that runs under the env lock or in its own binary.

**Tests that guard the change:** the four unit tests and the two integration tests don't compile at base: `evidence_of`, `evidence_weight` and `EVIDENCE_*` don't exist there, and the integration asserts need `metadata.evidence`. So they do guard the new behaviour, but only on a single node.

## Design questions
- ADR-0004's LWW is settled, so the question is whether a ranking boost should be stored at all, or computed at read time from the supersession graph. Computing it would remove F1, F3 and F4 together.
- The new `base_weight` is shared with feedback (`vitality.rs:215`) and demotion (`resolve.rs:116`). Once the weight hits the clamp, `evidence_weight`'s "divide by the old factor" is no longer exact. Should the boost be a separate term instead?

## Optional improvements
- In `vitality.rs:107-117` the new constants sit between `BASE_WEIGHT_MAX` and `BASE_WEIGHT_MIN`. As a result, the "Ceiling and floor" doc now attaches only to MAX, and MIN and `EVIDENCE_FACTOR_MAX` have no doc.

## Checks and validation gaps
- **Formatting:** `cargo fmt --all -- --check` and `rustfmt --check` on the three changed `.rs` files both passed, run on a scratch copy of the head.
- **Not run:**
  - **Blocked dependency:** `cargo clippy` and `cargo test` couldn't build. Every crate depends on `rusty_mill` at `libs-2026.10.0` from github.com. The proxy denied github.com, `~/.cargo` is read-only, and it has no git checkout.
  - **Probes, tests at base:** for the same reason, the probe tests I wrote for F1 and F2 didn't run, and I couldn't run the new tests at the base SHA.
  - **Hub and Postgres:** the hub's Postgres store wasn't tested. Not needed here: this PR adds no wire field.
- **To settle the gaps:** allow github.com, or provide a vendored `rusty_mill`. Then run clippy and test over the six packages, the F1 outbox-payload probe, and the F2 overflow probe.

## Verdict
**changes required**
