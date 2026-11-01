<!-- remind-me/343-r2 run 2, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 640 s, 677645 tokens, $0.83 -->

# Review of PR 343: hand-rolled redaction still lets some card numbers through (changes required)

- **Repository:** Rusty-Mill/rusty-remind-me, PR 343
- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `9ef2ec3a6eaf1fd7fe8b6edd6da5bad0f77337bb`. I fetched `pull/343/head` and it matches this SHA.
- **Scope:** `crates/remind_me_core/src/redact.rs`, `README.md` and `RELEASE_NOTES.md`. Commits are abdd0b76 (new rules) and 9ef2ec3a (fixes for K1 and K2).

## What the diff touches and what must hold

- **Records:** memory content, titles and the session archive. All go through `boundary::scrub`, `scrub_field` and `scrub_jsonl` before storage (`boundary.rs:1-6`), and `metadata.redactions` records which kinds fired.
- **Invariant:** no secret shape the README lists is stored on any write path. It must not sync to peers or come back in a prompt either, because a stored secret replays forever (`redact.rs:1-7`). The README now lists "payment card numbers".
- **Not touched:** tools, wire formats, sync, the outbox and the hub. Replication, retrieval and caller-identity checks don't apply. Redaction is a pure function, so there is no concurrency risk. The new rules run in sequence over the rewritten text, so no rule's ranges can cross another's.

## Prior findings, re-checked at this head

- **K1 (P1, panic on overlapping matches): fixed.** `coalesce` (`redact.rs:90`) sorts the ranges and merges any that overlap or touch, so `replace` always walks disjoint ranges in order. I ran the PR's new test `nested_and_repeated_prefixes_redact_without_panicking` at abdd0b76 and it panics at the slice in `replace` (`redact.rs:89` in that version). At the head it passes. A probe with `sk-<33>-sk-<35>` gives a single placeholder.
- **K2 (P2, card next to other digits escapes): partly fixed.** The cases in the test pass, and `cards_next_to_other_numbers_are_still_redacted` fails at abdd0b76 and passes at the head. The same bypass still happens when the trailing group is glued to a letter: see F1.

## Blocking findings

### F1 (P0): a card followed by a group glued to a letter is not redacted (rest of K2)

- **Where:** `redact.rs:249-250`.
- **Trigger:** any card written before a token like `1st`, `3DS` or `2x`.
- **Scenario (executed):** `redact("card 4111111111111111 1st try")` returns the text unchanged with no hits. So do `"card 4111 1111 1111 1111 3DS ok"` and `"4111111111111111 2x"`.
- **Cause:** `digit_groups` joins the card to the next digit group. That group is followed by a letter, so `bytes.get(end)` is alphanumeric and the whole run is thrown away. The card is lost along with the glued group.
- **The code is lopsided:** a group glued to a letter on the *left* is skipped on its own (lines 243-246) and the card after it is still found. Only a glued group on the right throws away the whole run.
- **Who is hurt:** the person whose card number is stored in plain text, and every agent and peer that later reads or syncs it. The README claim is false for this input.
- **Fix:** when the run's end is glued to an alphanumeric, call `cards_in` on `groups[..groups.len()-1]` instead of skipping the run. Keep skipping only when the run is a single group.
- **Regression test:** add `"card 4111111111111111 1st try"` → `"card [REDACTED:card_number] 1st try"` and `"4111 1111 1111 1111 3DS"` to `cards_next_to_other_numbers_are_still_redacted`. Both go red at the head.

### F2 (P0): a card number written as a JSON number is kept in the session archive

- **Where:** `boundary.rs:60-74` (`redact_strings` only rewrites `Value::String`; numbers hit `_ => false`), reached from `transcript.rs:336` and `boundary.rs:160`.
- **Trigger:** a captured transcript line with the card as a bare JSON number.
- **Scenario (executed):** `scrub_jsonl(r#"{"type":"tool_use","input":{"card":4111111111111111,"note":"pay"}}"#)` returns the line unchanged. The same card as a JSON string is redacted.
- **Why this PR makes it reachable:** card numbers are the first secret kind that can appear as a JSON number. Every earlier kind contains letters.
- **Consequence:** the archive keeps the card. `transcript.rs:333-335` promises the archive is "scrubbed like the dialog, so the source never returns what the dialog redacted", and the README says card numbers are replaced before storage. Both are now false for this case.
- **Fix:** in `redact_strings`, check a `Value::Number` that is an integer with 13-19 digits using `is_card`. On a match, replace it with the string `"[REDACTED:card_number]"` and report a hit.
- **Regression test:** a `boundary` unit test that runs `scrub_jsonl` on a line with a numeric card, then asserts that `4111111111111111` is gone and the line still parses.

### F3 (P2): the card-shape comment and branch claim Diners support, but Diners cards never match

- **Where:** `redact.rs:305` and `:309` against `:325`.
- **Problem:** `card_shaped` accepts `4-6-4` and its comment calls that the Diners layout. But `is_card` only accepts a leading 3 when it is 34/37 with 15 digits, so a 14-digit Diners card can never match.
- **Scenario (executed):** `"diners 3056 930902 5904"` and `"diners 30569309025904"` both come back unredacted.
- **Fix:** either add Diners (300-305, 36, 38-39; 14 digits) to `is_card`, or drop the `4-6-4` branch and remove Diners from the comment.
- **Regression test:** if Diners is added, put `"3056 930902 5904"` in `card_numbers_with_a_network_prefix_and_luhn_are_redacted`.

## Design questions

- **`sk-` false positives destroy content.** `llm_api_keys` (`redact.rs:201`) accepts any 32+ characters of letters, digits, `-` and `_` after `sk-`. So `sk-loading-spinner-container-wrapper-large` is replaced with `[REDACTED:llm_api_key]` and tagged `redacted` (executed). Redaction can't be undone, so this deletes part of a fact. Should the rule require a digit or mixed case in the run?
- **Card separators.** Only single spaces or hyphens join groups. `4111  1111 1111 1111` (two spaces) and `4111.1111.1111.1111` are not redacted (executed). This is documented, but cards pasted from forms often look like that.

## Optional improvements

- **Older bug, not introduced here:** two `ghp_` tokens glued together (`ghp_<40>ghp_<40>`) leave the second token's body unredacted. The first run eats `ghp` and the second anchor is then preceded by a letter, so it is skipped. The base behaves the same way.

## Checks

- **`cargo fmt --all -- --check`:** passed.
- **`cargo clippy --locked -p remind_me_core -p remind_me_mcp -p remind_me_api -p rusty-remind-me -p remind_me_remote -p remind_me_hub --all-targets -- -D warnings`:** passed. I ran it offline with a scratch target directory.
- **`cargo test --locked --no-fail-fast` over the same six packages:** 2483 passed and 3 failed.
  - The 3 failures are in `peer_discovery_test`. The sandbox refuses the `UnixListener::bind` at `peer_discovery_test.rs:45`, and these tests don't touch the diff.
  - The first run, with the real read-only `$HOME`, had 111 failures, all from tests that write under `$HOME`. They passed once `HOME` pointed at scratch space.
- **The 17 redaction-related unit tests in `remind_me_core`:** passed at the head.
- **New tests at abdd0b76:** `nested_and_repeated_prefixes_redact_without_panicking` and `cards_next_to_other_numbers_are_still_redacted` both fail there, so they guard the fixes. The PR's new tests can't compile at the base because `coalesce` and the new kinds don't exist there.
- **Probes:** I wrote probe tests in a scratch copy for F1, F2, F3 and the design questions; their results are quoted above. The scratch copy has been removed.

**Validation gaps:**
- The 3 `peer_discovery_test` tests need a sandbox that allows Unix sockets. They are unrelated to this diff.
- I didn't run any Postgres or hub tests. This PR doesn't touch sync.

## Verdict

**Changes required.** K1 is fixed. K2 still has a gap (F1), the new card rule can be bypassed through the JSONL archive (F2), and a comment and dead branch claim Diners support that doesn't exist (F3).
