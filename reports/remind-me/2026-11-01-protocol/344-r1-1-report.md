<!-- remind-me/344-r1 run 1, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 638 s, 2858695 tokens, $1.85 -->

# Review of PR 344 (Rusty-Mill/rusty-remind-me): changes required

The PR counts how many memories a merged ("canonical") memory stands for, stores that count as `metadata.evidence`, and raises the memory's `base_weight` from it. The trouble is that the weight boost is applied only once and assumes the count and the weight always move together. They don't: sync, ordinary updates and retries change one without the other. I ran probes that show peers ending up with different weights, counts that are wrong, and a boost that compounds. One input also panics the merge.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head reviewed:** `95528f017e2db1caef65aeee2154995333588067`. `git fetch origin pull/344/head` returned this SHA.
- **Diff:** one commit touching `consolidation.rs`, `vitality.rs`, `consolidate_test.rs` and `RELEASE_NOTES.md`.

## What must stay true
- **Count matches reality:** the canonical's `metadata.evidence` equals the number of memories it currently stands for.
- **Weight matches count:** `base_weight` carries exactly `factor(evidence)`, because `evidence_weight` divides by `factor(before)` and assumes the boost is already in the stored weight.
- **Peers agree:** every node holds the same `metadata` and `base_weight` for the canonical (ADR-0004: LWW on `updated_at`, `metadata` shallow-merged).
- **Merges are all-or-nothing:** a merge lands completely or not at all, and retrying it changes nothing.
- **Client input is checked:** values a client or peer supplies are validated where they enter.

## Blocking findings

### 1. P1: the weight boost never reaches the outbox, so peers diverge and a peer's edit erases the boost for good (executed)
- **Where:** `consolidation.rs:506–526`; `db/engine/memories.rs:818` (`set_importance` writes without stamping `updated_at`).
- **Cause:** `set_merged` (`:507`) stamps `updated_at` and queues an outbox snapshot that already has `evidence: 2` but still has the old `base_weight`. The boosted weight is written afterwards by `set_importance` (`:526`), which never queues anything.
- **Probe:**
  - Node A's row has `base_weight=1.0693, evidence=2`. Its queued payload has `base_weight=1.0, evidence:2`.
  - Peer B applies that payload with the real `upsert_record` and ends up with `base_weight=1`. The peers now hold different records.
  - B then edits the content and syncs back. B's record wins LWW and A drops to `base_weight=1`. The shallow merge keeps `evidence: 2`.
  - A third merge on A then gives `1.038`, not the single-merge `1.110`. A is under-weighted for good.
- **Also false:** the release note's claim that "a repeat merge does not compound / only the change is applied" does not hold once sync is involved.
- **Who is hurt:** every node's ranking, and so every agent reading it.
- **Fix:** don't store state in `base_weight` that is derived from a key other writers change independently. Either:
  - apply `factor(evidence)` at scoring time (preferred), or
  - write `base_weight` and `metadata` in the single stamped `Origin::Local` update, and also make every path that changes the count update the weight.
- **Regression test:** run a real `consolidate` with sync on, decode its outbox payload the way the hub returns it, apply it with `upsert_record` to a second store, and assert both `base_weight` and `evidence` match. Add a second case where a peer edit comes back and the boost must survive.

### 2. P1: a metadata update resets the count while the boost stays, so the next merge compounds (executed)
- **Where:** `consolidation.rs:497–518`. `update_memory` replaces the whole metadata object (`db/engine/memories.rs:743`).
- **Probe:** merge 2 memories, call `remind_me_update` with `metadata: {"note": …}`, then merge a third duplicate. Result: `evidence=2` for 3 memories, and `base_weight=1.1434`, above the non-compounded `1.1099`.
- **Effect:** each update-then-merge cycle boosts again, up to `BASE_WEIGHT_MAX` (3.0). The 1.5× cap in the release note does not hold.
- **Fix:** the same as finding 1, or keep the count somewhere client updates cannot overwrite (see finding 4).
- **Regression test:** exactly that sequence, asserting `evidence == 3` and `base_weight == 1 + 0.1·ln 3`.

### 3. P1: a merge cut short and retried counts the same memories twice (executed by reproducing the partial state)
- **Where:** `consolidation.rs:506–529`.
- **Cause:** there is no `store.transaction` around a cluster's merge. Each write commits its own journal batch (`engine/core.rs:355`). The new `set_tags_and_metadata` commits `evidence` first, using `Origin::Sync`, so nothing is queued for sync.
- **Trigger:** a kill, or an `Err` from `set_merged`, `set_importance` or `set_superseded_by`, leaves the count committed while the member is still live.
- **Probe:** I recreated that state, then a retry stored `evidence=3` for two memories with `base_weight=1.038`.
- **More effects:**
  - The `Origin::Sync` write never reaches peers if a later write fails.
  - Two clients consolidating at once (read-then-write with no lock) double-count the same way.
- **Fix:** run each cluster's writes inside `store.transaction(...)`, and write the metadata with `Origin::Local` in the same stamped update.
- **Regression test:** add a fault-injection hook (for example, make `set_superseded_by` fail for one member), then assert the canonical's metadata, content and weight are unchanged and a retry gives `evidence == 2`.

### 4. P1: a client-written `evidence` value panics the merge or wraps around (executed)
- **Where:** `consolidation.rs:498`, where the sum is an unchecked `u64`.
- **Trigger:** `metadata.evidence` is not reserved. `remember`, `update`, capture metadata, imports and synced records can all set it.
- **Debug and test builds:** a canonical stored with `{"evidence": 18446744073709551615}` panics `consolidate` with "attempt to add with overflow".
- **Release builds:** there is no `[profile]` override, so the sum wraps to 0. The merge stores `evidence: 0` and divides the weight by 1.5. A merge then lowers the weight.
- **Fix:** use `saturating_add`, or cap counts where they are read. Treat the key as reserved (see finding 5).
- **Regression test:** the probe input above, asserting no panic and no reduction in weight.

### 5. P2: any client can set `metadata.evidence`, and merges trust it (executed)
- **Where:** `consolidation.rs:294` (`evidence_of`).
- **Probe:** merging two memories where the member carries `{"evidence": 500}` records `evidence=501` and the maximum `base_weight=1.5`.
- **Effect:** any agent, import or peer that uses the plain key `evidence` for its own purposes gets a false count and a ranking boost.
- **Fix:** move the count under a reserved namespace or into a column, and strip or refuse it on client write paths.
- **Regression test:** a client-supplied `evidence` is ignored by consolidation.

### 6. P2: un-superseding a member leaves the canonical's count and boost in place (source-traced)
- **Where:** `db/queries.rs:244`.
- **Trace:** `remind_me_update` with `clear_superseded=true` makes the member live again, but the canonical keeps `evidence: 2` and its boost. The member is now counted both on its own and inside the canonical.
- **Fix:** lower the canonical's count when a member is un-superseded, or derive the count from live `superseded_by` links.
- **Regression test:** merge, un-supersede the member, and assert the canonical's count is 1.

### 7. P2: the merge's doc comment is out of date
- **Where:** `consolidation.rs:373` says a merge updates "content/access_count/tags/vitality".
- **Fix:** it now also writes `metadata.evidence` and `base_weight`; add both.

## Tests the PR adds
- **Integration tests:** I ran `a_merge_records_evidence_and_raises_base_weight` and `evidence_accumulates_across_merges_without_compounding_the_boost` at the base in a fresh target dir, and both fail there, so they do guard the change. (An earlier "pass" at base came from a stale shared build cache.)
- **Unit tests:** they call `evidence_of` and `evidence_weight`, which don't exist at the base, so they cannot compile there.
- **Sync coverage missing:** none of the new tests decode a payload the real outbox produced, and CONTRIBUTING asks for that on sync behaviour. That gap is why finding 1 got through; its regression test above covers it.

## Design question
- Ranking by how many duplicates were merged rewards repetition. Whether a merge count belongs in `base_weight`, which ADR-0004 and `set_importance` already treat as a local score that isn't stamped, is for the owner to decide. Computing the factor at read time would avoid findings 1–3.

## Optional improvements
- In `vitality.rs:107–117`, the new constants split the "Ceiling and floor" comment from `BASE_WEIGHT_MIN`, and `EVIDENCE_FACTOR_MAX` has no doc comment.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | Passed |
| `cargo clippy --locked -p` (all six packages) `--all-targets -D warnings` | Passed. The only errors printed came from a template `Cargo.toml` inside the `rusty_mill` git checkout, not this workspace. |
| `consolidate_test` and the unit tests in `consolidation.rs` | 11/11 and 18/18 passed at the head |
| Full `cargo test --locked --no-fail-fast`, six packages, `HOME` redirected to scratch | 167 test binaries: 2481 passed, 3 failed |
| Probes in a scratch copy (`$TMPDIR/probe`) | The executed traces above |

The 3 failures are all in `peer_discovery_test`: the sandbox refuses `UnixListener::bind` (EPERM), in code this PR doesn't touch. The first full run had also stopped early, because `remind_me_api`'s import tests couldn't write to the read-only `$HOME`. Those pass (14/14) with `HOME` set to scratch space.

## Validation gaps
- `peer_discovery_test`: to settle it, run it outside the sandbox.
- Sync through the real hub or Postgres was not run; I simulated the hub by parsing the outbox payload the way it hands it back.
- A release-profile build was not run; the wrap-around in finding 4 is source-traced from the absent `overflow-checks` setting.

## Verdict
**Changes required.**
