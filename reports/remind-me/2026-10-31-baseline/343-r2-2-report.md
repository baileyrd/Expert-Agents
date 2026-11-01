<!-- remind-me/343-r2 run 2, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 401 s, 674739 tokens, $0.89 -->

# PR 343 review: Rusty-Mill/rusty-remind-me

**Pinned:** base `d5528b1347054f348b3dac96f54619e603f4594c`, head `9ef2ec3a6eaf1fd7fe8b6edd6da5bad0f77337bb`. `git fetch origin pull/343/head` gives FETCH_HEAD equal to the head SHA. The PR has two commits: `abdd0b76` adds the new rules and `9ef2ec3a` fixes K1 and K2.

**Verdict: changes required.** K1 is fixed. K2 is only partly fixed: a card number followed by a word that starts with a digit is still stored as plain text.

## What the PR touches and the rules that must hold
- **`redact::redact` / `guard`** (`redact.rs`). This runs on every write path through `boundary::scrub`, `scrub_field` and `scrub_jsonl`: `add_memory`, capture, importer, project facts, backfill, proposals, and the transcript archive. The rules: every shape the docs list is replaced before anything is stored (README:267, RELEASE_NOTES:8). The function never panics. Ordinary text is left alone.
- **`project::looks_secret`** (`project.rs:962`). This refuses claim values and session `cwd` or `git_remote` that any rule would rewrite. So every new rule also widens what gets refused.
- **No change** to tables, sync wire format, outbox or tools. Redacted content goes into the row and replicates as ordinary content, so a secret that gets past redaction reaches every peer and the hub.

## Prior findings at this head
- **K1 (overlapping matches panic in `replace()`): fixed.** `coalesce` (`redact.rs:90`) sorts the ranges and merges overlapping or touching ones before `replace` runs. I ran `nested_and_repeated_prefixes_redact_without_panicking` against `abdd0b76`'s `redact.rs`: it panics at the `replace` slice (`redact.rs:89` there). At the head it passes. My extra probe `sk-<40>-sk-<40>` gives a single `[REDACTED:llm_api_key]`.
- **K2 (card next to other digits escapes redaction): partly fixed.** Leading digits, trailing short groups and two cards side by side are now caught (`cards_next_to_other_numbers_are_still_redacted` fails at `abdd0b76` and passes at the head). One related case is still open; see F1.

## Blocking findings

### F1 (P0, a secret stored and replicated): a card followed by a word that starts with a digit is never redacted
- **Where:** `crates/remind_me_core/src/redact.rs:249`.
- **Trigger:** a card number followed by a space or hyphen and then a word that starts with a digit.
- **Cause:** `digit_groups` collects the whole run of number groups. If the byte after the last group is alphanumeric, line 249 throws away the entire run, not just the last group. The leading check (line 241) rejects only the first group, so the two ends are treated differently.
- **Scenario (executed):** an agent writes `card 4111 1111 1111 1111 1st try`.
  - The groups are `[4,4,4,4,1]`, and the `1` is followed by `s`.
  - `cards_in` is never called, so the full card number is stored, tagged as clean, and synced to every peer and the hub.
  - Other inputs I ran that came back unchanged: `4111111111111111 2nd attempt`, `4111111111111111 3DS failed`, `pan 5555555555554444 30d hold`, `4111-1111-1111-1111 2FA`, `4111 1111 1111 1111-2a`.
  - For comparison, `4111 1111 1111 1111 1` is redacted.
- **Who is hurt:** the person whose card it is, and every node and agent that reads the memory.
- **Grade:** README:267 and RELEASE_NOTES:8 promise card numbers are redacted, and these inputs break that promise. The rubric grades a stored secret P0 however narrow the trigger. The earlier K2 was graded P2 for the same class; I graded this one by impact.
- **Fix:** if the byte after the run is alphanumeric, drop only the last group and still pass the rest to `cards_in`. That mirrors the leading check, and `4111111111111111x` stays unmatched because a group that is glued to a word is the one dropped.
- **Regression test:** add `("4111 1111 1111 1111 1st try", "[REDACTED:card_number] 1st try")` and `("4111111111111111 3DS", "[REDACTED:card_number] 3DS")` to `cards_next_to_other_numbers_are_still_redacted`. Both fail at this head.

### F2 (P2, a code comment that disagrees with the code): Diners `4-6-4` is described but can never match
- **Where:** `redact.rs:305` and `redact.rs:325`.
- **Cause:** the `card_shaped` doc comment and its match arm `[4, 6, 4 | 5]` say Amex/Diners `4-6-4` is handled. But `is_card` accepts a leading `3` only for 34/37 with 15 digits, so every 14-digit Diners number fails.
- **Scenario (executed):** `diners 30569309025904 x` and `diners 3056 930902 5904 x` are both returned unchanged.
- **Grade:** this is P2 because only the comment claims Diners coverage; the release note only says "a known network prefix". If Diners is meant to be covered, this becomes the same grade as F1.
- **Fix:** either add Diners (36, 300–305 and 38–39, 14 digits; 16–19 for the 36 range as issued now) to `is_card`, or remove `4-6-4` and "Diners" from `card_shaped` and its comment.
- **Regression test:** add the two Diners inputs above to `card_numbers_with_a_network_prefix_and_luhn_are_redacted`, or add a test that pins the decision to leave Diners out.

### F3 (P2, a write that takes very long on hostile input): repeated prefixes make the new rules slow
- **Where:** `redact.rs:153–160` (`prefixed`).
- **Cause:** every anchor scans to the end of its run, even when it sits inside a match already found. The new prefixes are `sk-`, `glpat-`/`gldt-`/`glrt-`, and `github_pat_` (whose run includes `_`). Their run predicates include the prefix's own last character, so text like `sk-sk-sk-…` is O(n²).
- **Pre-existing part:** `xoxb-` already behaved this way at the base. This PR adds three more rules like it, and all of them run on every write and on every string in an archived transcript.
- **Measured (executed, debug build, 200 KB of one repeated prefix):**

| Prefix | Time |
|---|---|
| `xoxb-` | 45 s (already at base) |
| `sk-` | 68 s |
| `glpat-` | 36 s |
| `github_pat_` | 21 s |

- **Who is hurt:** any client that can write can stall the write path. The same applies to a transcript holding such text.
- **Fix:** in `prefixed`, skip anchors that start before the end of the previous match, the way `jwts` already does with `floor`. With a single predicate per prefix, a nested anchor's run ends where the outer run ends, so nothing is lost.
- **Regression test:** a `redact` call on `"sk-".repeat(70_000)` that must finish within a time limit, or better, a count of `run` calls.

## Design questions
- **Card networks not covered:** JCB (`3530111333300000`) and UnionPay (`6200000000000005`) were returned unchanged (executed), but the README says "payment card numbers" with no limit. Should the README name the networks covered, or should the networks be added?
- **`sk-` matches ordinary paths:** `sk-` plus 32 or more characters from `[A-Za-z0-9_-]` matches ordinary kebab-case names. For example `/home/u/src/sk-learn-pipeline-hyperparameter-search-tool` becomes `/home/u/src/[REDACTED:llm_api_key]` (executed). Through `looks_secret`, a session `cwd` or claim value like that is now refused. Is that acceptable, or should the rule require at least one digit, or upper and lower case mixed?

## Optional improvements
- `google_api_keys` requires a run of exactly 35 characters, so a key followed directly by `-` or `_` is missed (executed: `AIza<35>-` is returned unchanged).
- `coalesce` also merges ranges that only touch, so two secrets right next to each other become one `[REDACTED]` marker. Nothing leaks; this is noted only in case the marker count matters to anyone.

## Checks
- `cargo fmt --all -- --check`: **passed** (exit 0).
- **`redact.rs` built on its own:** I compiled the head's `redact.rs` in a scratch crate with `serde_json`, offline. This is not the workspace.
  - Clippy with `--all-targets -D warnings` and the workspace `clippy.toml`: **passed**.
  - The 14 tests in the module: **all passed**.
- The two tests added by the fix commit, run against `abdd0b76`'s `redact.rs`: **both fail** (a panic at the `replace` slice, and a failed card assertion). So they do guard K1 and K2.
- The tests added for the new rules can't compile at the base, because the functions they call don't exist there, so they would also fail without this change.
- Probes for F1, F2 and F3 and the design questions were run in a scratch copy; the results are quoted above.
- **Not run (validation gap):** workspace-wide `cargo clippy` and `cargo test --locked` over the six packages. The git dependency `rusty_mill` (`libs-2026.10.0`) is not cached locally, `~/.cargo` is read-only, and network access to github.com was denied in this sandbox. To close the gap, run both commands on a machine where that dependency can be fetched. The diff only touches `redact.rs`, which depends on nothing else in the crate except `serde_json`, so the standalone run covers the changed code. The call sites (`boundary.rs`, `project.rs:962`) were read but not executed.

## Verdict
**Changes required.** F1 (P0), F2 (P2) and F3 (P2) are open. K1 is fixed. K2 is only partly fixed; what remains of it is F1.
