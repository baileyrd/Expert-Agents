<!-- remind-me/344-r1 run 5, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 196 s, 1154703 tokens, $0.99 -->

I couldn't run the probe or the test suite, but the source trace is enough for three blocking defects. Verdict: **changes required**.

**Rusty-Mill/rusty-remind-me PR #344**: "consolidate: count the evidence behind merged memories"
- Base: `d5528b1347054f348b3dac96f54619e603f4594c`
- Reviewed head: `95528f017e2db1caef65aeee2154995333588067` (the fetched `pull/344/head` matches)
- Diff: `consolidation.rs`, `vitality.rs`, `tests/consolidate_test.rs`, `RELEASE_NOTES.md`

## What the diff touches, and what must hold
- **The memory row** (`metadata.evidence`, `base_weight`): a merged memory's weight should equal its original weight times the evidence factor, and every node should hold the same values.
- **The outbox payload** (ADR-0004/0007): what a node queues is the record peers apply. `apply` writes `base_weight` from the payload (`sync/record.rs:375`).
- **`remind_me_consolidate`**: a merge either lands completely or not at all.
- **Client-writable `metadata`**: anything that drives ranking must not be something a client can forge or erase by accident.

## Blocking findings

**1. P1: peers receive the evidence count without the weight boost, so nodes diverge.** `consolidation.rs:506–526`, *source-traced*.
- **Trace:**
  - Node A merges canonical C with one member. `set_tags_and_metadata` (`:506`, `Origin::Sync`, no outbox entry) writes `evidence: 2`.
  - `set_merged` (`:507`) stamps `updated_at`. That queues the outbox payload, built from the row at that moment (`engine/memories.rs:499–502`), so it carries `evidence: 2` and the old `base_weight` (for example 1.0).
  - Only then does `Feedback::set_importance` (`:526`) write the boosted weight (1.069). That write does not stamp `updated_at` (`engine/memories.rs:818`), so it never reaches the outbox.
  - Peer B therefore stores `evidence: 2` with a weight of 1.0.
- **Consequences:**
  - When B later merges a third duplicate into C, it applies only the 2→3 step, so B's weight is 1.0 × f(3)/f(2), while A's is 1.0 × f(3).
  - If any node edits C, LWW sends 1.0 back to A as well. Every node then holds `evidence: 2` and an unboosted weight, and the "only the change since the last merge" rule means the 1→2 boost is never applied again.
- **Who is hurt:** the other nodes, and agents whose ranking on each node depends on which node did the merge.
- **Fix:** either compute the boost from `evidence` when ranking instead of storing it in `base_weight`, or write `base_weight` inside the stamped write (fold it into `set_merged`), so the queued payload matches the row.
- **Regression test:** a sync-enabled consolidate test in its own binary (like `outbox_test.rs`, which sets sync env vars) that decodes the canonical's last outbox payload and asserts its `base_weight` and `metadata.evidence` equal the stored row. Better still, apply it to a second node through `MockHub` and compare the two rows.

**2. P1: a merge is still five separate commits, and the new counter makes a partial merge inflate itself on retry.** `consolidation.rs:491–530`, *source-traced*.
- **Cause:** each repository call commits on its own (`engine/memories.rs:513`). `apply_merges` does not use `Store::transaction` (`db/store.rs:63`), although project claims and reverts do (`project.rs:762`, `:881`).
- **Trace:**
  - The process dies, or `set_merged` / `set_superseded_by` fails, after `:506` commits. C now holds `evidence: 2`, and with a crash after `:526` also the boosted weight, but the member is still live.
  - The next run clusters the same pair again: `before = 2`, `after = 2 + 1 = 3`.
  - C now claims to stand for 3 memories when it stands for 2, and the weight goes up a second time.
- **Who is hurt:** agents reading what a fact rests on. It also contradicts the release note's "a repeat merge does not compound".
- **Fix:** run each cluster's merge inside `store.transaction`.
- **Regression test:** inject a failure after `set_importance` (a test-only hook) and assert the canonical's metadata, weight and content are unchanged and the member is not superseded.

**3. P2: the evidence count lives in client-writable metadata, so it can be forged or erased.** `consolidation.rs:294`, `db/queries.rs:224,242`, *source-traced*.
- **Forged:** `remind_me_add` or `remind_me_update` accepts any metadata. A member stored with `{"evidence": 1000000}` gives the canonical the full 1.5× boost on its first merge.
- **Erased:** `remind_me_update` replaces metadata wholesale. Updating a merged canonical (`evidence: 2`, weight 1.069) with `{"note": "x"}` drops the count. The next merge then computes 1→2 again and multiplies on top of the existing boost, giving 1.143 where three pieces of evidence should give 1.110.
- **Effect:** the release-note claim "does not compound" is false on this path.
- **Fix:** reserve the key (strip it on add and import, carry it over on update), or keep it in a dedicated field.
- **Regression test:** update a merged canonical's metadata without `evidence`, merge a third duplicate, and assert the weight equals original × (1 + 0.1·ln 3).

**4. P2: docs and notes disagree with the code.**
- `consolidation.rs:373`: still says a merge updates "content/access_count/tags/vitality"; it now also writes `metadata.evidence` and `base_weight`.
- `vitality.rs:111–113`: says a canonical that "absorbed `n` merged duplicates" scores `1 + 0.1·ln(n)`. In the code, absorbing one duplicate gives ln 2, because `n` counts the canonical too.
- `vitality.rs:107–117`: the new constants sit between `BASE_WEIGHT_MAX` and `BASE_WEIGHT_MIN`, so `BASE_WEIGHT_MIN` loses its "ceiling and floor" doc.
- `RELEASE_NOTES.md`: the "does not compound" claim is false under findings 2 and 3.

**5. P2: no test covers the new behaviour in sync.** The tests check only the local row; nothing decodes a payload the real write path queued. That is how finding 1 got through. The test belongs in a sync-enabled binary, as described in finding 1.

**Coverage the PR does have:** reading the code, the two new integration tests should fail at base, because base never writes `metadata.evidence`. The new unit tests call functions that don't exist at base, so they wouldn't compile there. I could not run either.

## Ruled out
- **Hidden rows, retrieval, time as text, who is calling, secrets:** the diff adds no query, surface or text-based time comparison, and candidates are still live and unsuperseded only.
- **Tool contract:** no tool or schema change. The integer `evidence` survives JSON through the hub.
- **Locks and concurrency:** each write takes and releases the engine lock; nothing holds it across I/O.

## Design questions
- Two nodes that merge different duplicates into the same canonical lose one count under ADR-0004's shallow metadata merge. That is settled behaviour, but should a corroboration count be last-writer-wins at all?
- When a different memory becomes the canonical, it absorbs an earlier canonical's count, but that canonical's boosted weight is discarded. Is that intended?

## Optional improvements
- Give `EVIDENCE_FACTOR_MAX` its own doc line.
- Mention the evidence count in the `remind_me_consolidate` tool description, since agents read that metadata.

## Checks and validation gaps
- **Ran:** `cargo fmt --all -- --check` in a scratch copy of the head passed (exit 0), as did `rustfmt --check` on the three changed Rust files.
- **Not run:** clippy, `cargo test`, and my probe. They need the `rusty_mill` git dependency (`libs-2026.10.0`), which isn't cached, `~/.cargo` is read-only, and `github.com` is blocked in this sandbox.
  - The probe (deleted afterwards) asserts the outbox payload's `base_weight` against the stored row. Running it with that dependency available would confirm finding 1.
- **No Postgres hub run:** the change adds no column; `evidence` travels inside `metadata`.

## Verdict
**Changes required.**
