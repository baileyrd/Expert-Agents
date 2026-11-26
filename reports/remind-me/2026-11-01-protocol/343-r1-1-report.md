<!-- remind-me/343-r1 run 1, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 650 s, 740349 tokens, $0.93 -->

I'm requesting changes on PR 343. Two of the new redaction rules have defects: one makes the write path panic, and the other stores card numbers the docs now say are redacted.

**Repository:** Rusty-Mill/rusty-remind-me, PR #343
**Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
**Head reviewed:** `abdd0b76add2f3c5cf61cb1533f225405d53f931` (the fetched `pull/343/head` matches this SHA)
**Diff:** one commit touching `crates/remind_me_core/src/redact.rs`, `README.md` and `RELEASE_NOTES.md`.

## What the diff touches, and what must hold

- **`redact::redact` / `guard`.** Every write path goes through these via `boundary::scrub`, `scrub_field` and `scrub_jsonl`: `add_memory`, both capture halves, the importers, project facts, backfill and proposals. Two rules must hold:
  - No secret shape the docs list may be stored (ADR-0026 capture, README).
  - Redaction must not fail on any input, or the write fails.
- **`project::looks_secret` (`project.rs:962`).** This uses the same scrubber to refuse or skip claim values, cwd, git remotes and entity names. So any change to redaction also changes which identity values are accepted.
- **Records, sync, tools.** No schema, wire format or tool changes. `metadata.redactions` gains new kind strings, which are free text, and no doc or tool description lists the kinds (grep found none). The sync, retrieval and tool-contract axes are not affected. The PR does not change where redaction runs, so the "boundary skipped" class is unchanged.

## Blocking findings

### 1. P1 — Overlapping matches panic inside `replace`, so writes fail
- **Where:** `redact.rs:136-149` (`prefixed`) feeding `redact.rs:85-95` (`replace`).
- **Cause:** `prefixed` sorts its ranges but never drops nested ones. `anchors` only rejects a prefix preceded by a letter or digit. Several new rules use runs that can contain `-` or `_`, so a second prefix inside the first match's run is accepted. That gives a nested range, and `replace` then slices `text[at..start]` with `at > start`.
- **Inputs that can trigger it:**
  - `sk-…` (llm_api_key) and `glpat-`/`gldt-`/`glrt-` (gitlab_token), both confirmed by running them.
  - `pypi-…`, and `github_pat_…_ghp_<36 alnum>` (fine-grained PAT ranges merged with `ghp_` ranges), found by reading the code.
- **Executed in a scratch copy of the head:**
  - `redact("sk-" + "a"*32 + "-sk-" + "b"*32)` panics with `byte range starts at 71 but ends at 36` (redact.rs:89).
  - The `glpat-` version panics the same way.
- **Who is hurt:**
  - An agent or a transcript capture whose content contains such a string gets an internal error, and nothing is stored. `rusty_mcp_server` catches the panic and returns "internal error"; the daemon closes the worker's session.
  - A watched transcript containing the string fails on every re-capture.
  - `looks_secret` panics for a claim, cwd or remote with this shape.
  - Anyone who can put text into a captured transcript can make that capture fail every time.
- **Already in base:** the Slack rule (`xoxb-` with base64url runs) had the same flaw before this PR. Moving it onto `prefixed` keeps it, and the PR now spreads the pattern to four more rules.
- **Fix:** make `replace` (or `redact`, before it calls `replace`) merge or skip any range that starts before the previous range's end, the same way `generic_secrets` keeps a running end position (`floor`). Doing it in `replace` covers every rule.
- **Regression test:** for `sk-…-sk-…`, `glpat-…-glpat-…`, `github_pat_<body>_ghp_<36 alnum>` and `xoxb-…-xoxb-…`, assert one `[REDACTED:<kind>]` and no panic. It panics at head.

### 2. P0 (graded by impact: a secret is stored and synced) — Card numbers next to other digits are not redacted, though README and release notes say they are
- **Where:** `redact.rs:216-241` (`card_numbers`), `redact.rs:243-252` (`digit_span_end`); README.md:267; RELEASE_NOTES.md (2026-10-29 entry).
- **Cause:** `digit_span_end` keeps joining digit groups across *any* single space or hyphen. `is_card` is then checked only on that whole span. The loop jumps to `end`, so no shorter span inside it is ever tried. Any digits next to the card push it past a valid length or fail the Luhn check.
- **Executed, all returned unchanged with no hits:**
  - `card 4111111111111111 12/30 cvv 123`
  - `card 4111 1111 1111 1111 12/30`
  - `card 4111-1111-1111-1111 123`
  - `qty 2 4111111111111111`
  - `cards 4111111111111111 5555555555554444`
- **Who is hurt:** a person pastes card details in the usual form (number, expiry, CVV). The card number is stored in clear, synced to the hub and peers, and replayed into prompts. README and the release notes say "payment card numbers… optionally grouped by spaces or hyphens" are redacted, so the user has been told otherwise.
- **Severity note:** this is not a regression, since base stored cards too. It is graded P0 by its impact and because the docs now make a false claim.
- **Fix:** if the joined span is not a card, also test each plain digit run on its own and each run of consecutive groups that is 13–19 digits long. Alternatively, accept only the standard groupings (4-4-4-4, 4-6-5, 4-4-4-4-3) and fall back to plain runs.
- **Regression test:** the five inputs above, each expecting `[REDACTED:card_number]` over the card digits only. All five fail at head.

## Design questions
None.

## Optional improvements (non-blocking)
- **The `sk-` rule catches kebab-case names.** Executed: `/home/u/src/sk-classifier-experiments-2026-baseline` and the branch `fix-sk-classifier-experiments-2026-baseline-run` both become `[REDACTED:llm_api_key]`. In memory content that rewrites the text and tags it `redacted`. Through `looks_secret`, the cwd would be skipped when proposals are built (`project_proposals.rs:474`). Requiring a known key marker, or checking the character mix of the body, would avoid this.
- **Google keys followed by `-` or `_` are missed.** `google_api_keys` needs the run to be exactly 35 characters, so `AIza<35>-x` or `AIza<35>_x` is not caught.
- **One new test guards nothing.** `new_shapes_leave_ordinary_text_alone` passes at base, as any negative test of a new rule would. Consider adding the probe inputs above as positive cases.

## Checks run
- `cargo fmt --all -- --check`: **passed**.
- `cargo clippy --locked` over the six packages with `--all-targets -D warnings` (run offline): **passed**.
- `cargo test --locked` over the six packages, run offline with `--no-fail-fast`:
  - The first run used the real home directory, which is read-only in this sandbox. Six `remind_me_api/tests/import_export_test.rs` tests failed with `ReadOnlyFilesystem`.
  - I re-ran with `HOME` pointed at scratch space: 167 test binaries, 2479 passed, 3 failed.
  - The 3 failures are in `remind_me_core/tests/peer_discovery_test.rs`, where `UnixListener::bind` in the temp directory panics in this sandbox. The PR does not touch that file, so this is a gap in the environment, not a finding.
- **New tests at base:** `provider_tokens_are_redacted` and `card_numbers_with_a_network_prefix_and_luhn_are_redacted` fail at base, so they guard the change. `connection_strings_lose_their_password` and `new_shapes_leave_ordinary_text_alone` pass at base; the first is meant to pin existing behaviour.
- **Probes:** run in a scratch copy, as noted for each finding.

## Validation gaps
- **Peer-discovery tests:** need a run where Unix sockets can be bound, such as CI.
- **Panic catching:** confirmed only in the cached `rusty_mill` checkout (`4e04d59`, `methods.rs:216`). I did not confirm that commit is the pinned `libs-2026.10.0` tag. This does not change finding 1, because the write fails either way.
- **No Postgres, hub or embedder checks:** none are relevant to this diff.

## Verdict
**Changes required.**
