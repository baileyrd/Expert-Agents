<!-- remind-me/343-r2 run 3, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 151 s, 559939 tokens, $0.67 -->

# Review of PR 343 (Rusty-Mill/rusty-remind-me): changes required

The overlap panic (K1) is fixed. The card bypass (K2) is fixed only for the cases its tests cover. I ran a probe that found two other inputs where a card number is still stored, fully or in part.

**Pinned:** base `d5528b1347054f348b3dac96f54619e603f4594c`, head `9ef2ec3a6eaf1fd7fe8b6edd6da5bad0f77337bb`. The fetched `pull/343/head` matches the head SHA. Commits: `abdd0b76a` (the new rules) and `9ef2ec3a6` (the fix for the last review).

## What the diff touches, and what must hold

- **`redact::redact` / `guard` (`redact.rs`).** Every write path calls them through `boundary::scrub`, `scrub_field` and `scrub_jsonl`: `add_memory`, capture, decompose, the importers and the session archive. Invariant: no secret shape this function claims to cover reaches a row, the outbox, the hub or a peer. Redaction is the only containment point; once text is stored it syncs as-is, so anything missed here reaches every peer.
- **The `replace` contract.** Ranges must be sorted, non-overlapping and on character boundaries, or a write panics. `coalesce` now enforces this for every rule. All anchors and runs are ASCII, so boundaries hold.
- **No wire, schema, tool or sync change.** The `[REDACTED:<kind>]` text and `metadata.redactions` are existing formats; the new kinds are just new strings in them. The ADRs on sync, the outbox, tools and the client contract (0004, 0007, 0028, 0030) are not touched.

## Prior findings, re-checked at this head

- **K1 (P1) — fixed.** `coalesce` (`redact.rs:90`) sorts and merges overlapping and touching ranges before `replace`. `nested_and_repeated_prefixes_redact_without_panicking` covers `sk-…-sk-…`, `glpat-`, `github_pat_` and the glued case. At base this test does not compile (`coalesce` and the new rules are missing), so it guards the fix.
- **K2 (P2) — partly fixed.** Cards next to other digit groups now go through `digit_groups` and `cards_in`. Two related bypasses remain (F1 and F2 below).

## Blocking findings

### F1 (P2) — A card followed by a word that starts with a digit is stored unredacted
- **Where:** `crates/remind_me_core/src/redact.rs:249`
- **How (executed in the probe):** `card_numbers` collects the whole run of separated digit groups. It then throws away the entire run if the last group is directly followed by a letter or digit. So when the text after the card begins with digits, the card goes with it. Probe results:
  - `"4111 1111 1111 1111 3rd attempt"` → unchanged, no hits
  - `"4111-1111-1111-1111 2FA"` → unchanged
  - `"card 4111111111111111 1st"` → unchanged
  - `"4111111111111111 2x"` → unchanged
- **Who is hurt:** the cardholder, and every agent and peer that reads or syncs the memory. The full card number is stored, written to the outbox and replicated.
- **Severity:** graded P2 to match K2, which is the same class. By impact alone (a secret stored) the owner may want P0.
- **Fix:** when the last group is glued to a following letter or digit, drop only that group and still pass the remaining groups to `cards_in`. Treat a single glued group as the code does now. The glued-number cases in `a_card_glued_into_a_longer_number_is_not_matched` stay unmatched, because there the glued part is inside the one group.
- **Regression test:** add `("4111 1111 1111 1111 3rd", "[REDACTED:card_number] 3rd")` and `("4111111111111111 2FA", "[REDACTED:card_number] 2FA")` to `cards_next_to_other_numbers_are_still_redacted`.

### F2 (P2) — A digit group before a card can push the match left and leave the card's tail visible
- **Where:** `crates/remind_me_core/src/redact.rs:275-299` (`cards_in`, which tries the earliest group first and moves past the match at `first = last + 1`)
- **How (executed in the probe):** `cards_in` checks windows starting from the earliest group. If leading groups plus the card's first groups happen to form a valid card, that window is redacted and the card's remaining groups are left as plain text. Roughly 1 in 10 such leading groups passes the checksum. Probe results:
  - `"ref 4008 4111 1111 1111 1111"` → `"ref [REDACTED:card_number] 1111"`
  - `"ref 4000 0000 0000 4111 1111 1111 1111"` → `"ref [REDACTED:card_number] 1111 1111 1111"`, which leaves the card's last 12 digits in the stored text.
- **Who is hurt:** same as F1. This is the class K2 named (a card next to other digits) in a shape the new test does not cover.
- **Fix:** when windows overlap, redact all of them rather than choosing one greedily. Collect every valid window in the run and merge them with `coalesce`. This over-redacts the leading group, which is harmless. Picking the right-most window instead would only move the leak to the other side.
- **Regression test:** add `("ref 4000 0000 0000 4111 1111 1111 1111", …)` and assert that no `1111` survives, and the same for `"ref 4008 4111 1111 1111 1111"`.

## Design questions (not blocking)

- **Other separators.** Cards written with double spaces, dots or tabs (`4111  1111  1111  1111`, `4111.1111.1111.1111`) are not caught (probed). The comment says "single spaces or hyphens", but the release note says "grouped by spaces or hyphens". Either widen the rule or tighten the note.
- **`sk-` rule hits long kebab-case names.** A path segment like `/sk-<32+ letters, digits, - or _>` is redacted as `llm_api_key` (probed). Because `scrub_jsonl` runs every rule on every string in the session archive, both this and card matches on 16-digit string ids with a valid checksum will change archived transcripts. That is probably acceptable, but worth a conscious decision.
- **Google key followed by `-` or `_`.** `google_api_keys` requires the run after `AIza` to be exactly 35 characters, so a key immediately followed by `-` or `_` is missed (probed: `AIza…-v`). The generic `key=` rule only catches it when there is an assignment.

## Optional improvements

- `github_tokens` (`redact.rs:181`) still calls `out.sort_unstable()`, which `coalesce` now makes redundant.

## Checks and validation gaps

| Check | Result |
|---|---|
| `rustfmt --edition 2021 --check crates/remind_me_core/src/redact.rs` | passes |
| `cargo fmt --all -- --check` | printed no diff, but its exit status was hidden behind a pipe, so not confirmed |
| `redact.rs` built as a standalone crate (only `guard` and its two tests removed, because `serde_json` could not be fetched): `cargo test` | all 11 module tests plus my probes pass |
| Same standalone crate: `cargo clippy --all-targets -D warnings` | clean |
| Workspace `cargo clippy` / `cargo test` over the six packages | **not run** |

- **Why the workspace checks could not run:** the `rusty_mill` git dependencies at `libs-2026.10.0` are not cached, `~/.cargo` is read-only in the sandbox, and in offline mode the dependency cannot be checked out. Running the six-package clippy and test commands with network and a writable `CARGO_HOME` would settle this, including the two `guard` tests.
- **Tests at base:** every new test refers to functions that do not exist at base, so none can pass there.
- **No sync test needed:** nothing on the wire changed.

## Verdict: **changes required** (F1, F2)
