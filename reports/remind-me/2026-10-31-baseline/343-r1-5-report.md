<!-- remind-me/343-r1 run 5, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 232 s, 772193 tokens, $0.91 -->

# Review: Rusty-Mill/rusty-remind-me PR #343 ("redact: catch provider API keys, Stripe, npm, PyPI, GitLab, Google keys and card numbers")

**Verdict: changes required.** One P1 crash, one P1 detection gap, and two P2 false-positive defects.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `abdd0b76add2f3c5cf61cb1533f225405d53f931`. I fetched `pull/343/head` and it resolves to this SHA. It is one commit and touches three files: `redact.rs`, `README.md` and `RELEASE_NOTES.md`.

## What the diff touches, and the rule each part must keep

- **Write-time redaction, `redact::redact` and `guard`.** Every path that stores text goes through `boundary::scrub`, `scrub_field` or `scrub_jsonl`: `add_memory`, both capture halves, decompose, the importers, project facts and proposals, and the transcript archive. They must never panic. They must replace every secret shape they claim to cover. They must leave other text unchanged, because redaction can't be undone and is not re-run on sync.
- **`project::looks_secret`** refuses claim objects and backfill paths, and skips session `cwd` and `git_remote` evidence, whenever `scrub` would change the text. Any new false positive here becomes a refusal.
- **Sync.** Redacted content is ordinary content, and the hub path does not call `redact`. The outbox, the hub and peer apply have no new fields to carry, so the "wire versus row" check does not apply.
- **Docs.** `README.md` and `RELEASE_NOTES.md` describe what is now covered, so each claim must match the code.

## Blocking findings

### 1. P1: A second token prefix inside a match crashes every write path (executed)

- **Where:** `crates/remind_me_core/src/redact.rs:136-147` (`prefixed`), with the crash at `replace`, `redact.rs:85-95`.
- **Cause:** `anchors` only rejects a prefix that follows a letter or digit. A second `sk-`, `glpat-`, `github_pat_` or `pypi-AgEI…` that follows `-` or `_` inside the first match's run is accepted. The rule then returns a second range nested inside the first. `replace` slices `text[at..start]` with `start < at` and panics.
- **Probe output:**
  - `"x sk-<40 chars>-sk-<40 chars> y"` gives `byte range starts at 89 but ends at 46`.
  - Joined tokens of the form `glpat-…_glpat-…`, `github_pat_…_github_pat_…` and `pypi-…-pypi-…` panic the same way.
  - At base, these four inputs return `ok []`. Only `xoxb-…-xoxb-…` panicked at base: that was a slack bug, which this PR moves into the shared helper without fixing.
- **Trigger:** two keys pasted together (for example an env dump), or a slug such as `sk-…-sk-<32+ chars>`.
- **Who is hurt:**
  - Any agent writing a memory.
  - The importer and transcript capture abort. A watched transcript that contains such a string fails again on every rescan.
  - The MCP stdio handler catches the panic (`remind_me_mcp/src/lib.rs:3632`) and returns an error. Every other caller gets a raw panic.
- **Fix:** in `prefixed`, skip any anchor before the end of the previous match (as `jwts` already does with `floor`). Better still, have `replace` merge or ignore overlapping ranges so no rule can panic it.
- **Regression test:** `redact` must not panic on these, and must return one range for each:
  - `format!("sk-{b}-sk-{b}")`
  - `glpat-{b}_glpat-{b}`
  - `github_pat_{b}_github_pat_{b}`
  - `xoxb-{b}-xoxb-{b}`

### 2. P1: Common pasted card formats are not redacted, although the release note and README now say cards are (executed)

- **Where:** `redact.rs:216-262` (`card_numbers`, `digit_span_end`, and the skip at line 235).
- **Cause:** `digit_span_end` joins every following group separated by a space or hyphen. The whole group then has to pass the length and Luhn checks. If it fails, the scanner skips the entire span (`i = end.max(i + 1)`) and never tries the card on its own.
- **Probe output** (each stored unchanged, no hits):
  - `card 4111 1111 1111 1111 12/30 123`: the `12` joins, giving 18 digits.
  - `card 4111111111111111 123`: the CVV joins, giving 19 digits and failing Luhn.
  - `item 2 4111111111111111`: the span starts at `2`.
  - `4111 1111 1111 1111 2030`
- **Impact:** a card number is stored, sent to the hub and replayed to every agent. `RELEASE_NOTES.md:8` and `README.md:267` now promise this shape is redacted. I graded this P1 rather than P0 because base stored every card. The PR adds no new exposure, but it does make a false coverage claim.
- **Fix:** match the real card groupings with one consistent separator: 4-4-4-4, 4-4-4-4-3, 4-6-5 and 4-6-4, plus ungrouped 13–19 digit runs. Try each grouping from the start, without spreading into neighbouring numbers. Do not swallow the whole number run.
- **Regression test:** add the four strings above to `card_numbers_with_a_network_prefix_and_luhn_are_redacted`.

### 3. P2: Lists of numbers are destroyed as card numbers (executed)

- **Where:** same code as finding 2, `redact.rs:241-249`.
- **Cause:** joining any single-space or hyphen groups turns a list of small numbers into a card candidate. About one in ten such lists with a valid network prefix passes Luhn.
- **Probe output:**
  - `PRs 4012 4013 4015 4028 merged` → `PRs [REDACTED:card_number] merged`
  - `Ran 4 1 2 3 4 5 6 7 8 9 1 2 3 4 5 0 items` is redacted the same way.
- **Who is hurt:** the original text is gone for good, so agents lose the PR numbers. The memory is also tagged `redacted`, and the same happens to strings inside `scrub_jsonl` transcript archives.
- **Fix:** the grouping restriction in finding 2 fixes this too.
- **Regression test:** add `"PRs 4012 4013 4015 4028 merged"` to `new_shapes_leave_ordinary_text_alone`.

### 4. P2: `sk-` plus 32 base64url characters matches long hyphenated slugs (executed)

- **Where:** `redact.rs:181-186` (`llm_api_keys`). The doc comment says the 32-character floor leaves prose alone, which is wrong for long slugs.
- **Cause:** `is_b64url` accepts `-`, so any long kebab-case word starting with `sk-` reaches 32 characters.
- **Probe output:**
  - `merged branch sk-fix-the-flaky-outbox-retry-test-on-ci today` → `merged branch [REDACTED:llm_api_key] today`. Branch names prefixed with the author's initials follow exactly this pattern.
  - `/home/u/src/sk-learn-experiments-for-production-models` is redacted as well.
- **Knock-on effect through `looks_secret`:**
  - a project claim whose object is such a name is refused with "looks like a secret";
  - `project_backfill.rs:80` refuses the crate path;
  - `project_proposals.rs:454` and `:474` drop that session's `git_remote` and `cwd` as evidence.
- **Fix:** require a key-like body, for example: at least 20 consecutive letters/digits containing both a digit and a letter, or one of the known forms (`sk-proj-`, `sk-ant-`, `sk-svcacct-`, or `sk-` followed by 48 letters/digits).
- **Regression test:** add both strings above to `new_shapes_leave_ordinary_text_alone`.

## Failure classes checked

| Class | Result |
|---|---|
| Half a change; concurrency | Ruled out. These are pure functions with no transaction or shared state. |
| Hidden rows surface; time as text | Not touched. The ISO timestamp `2026-10-29T…` is not joined, because the `T` that follows fails the boundary check. |
| Wire versus row | No new field. Redacted text syncs as ordinary content, and the hub path does not call `redact`. |
| Retrieval admission; who is calling | Not touched. |
| Status that lies | Findings 2 and 4. Coverage claims don't match behaviour, and the `llm_api_keys` comment is wrong. |
| Boundary skipped | Every caller uses the same rule set through `boundary.rs`. The new rules reach all paths, including `looks_secret` (finding 4). |
| Silent success | Finding 2: a card the PR claims to cover is stored unredacted with no hit. |
| Panic on a write path | Finding 1. |
| Tool surface | Unchanged. `metadata.redactions` simply gains new kind names. |
| Rule ordering | No double-hit. `api_key=sk-…` hits only `llm_api_key`, because the generic rule skips values that are already `[REDACTED:`. |

## Design questions

- Redaction can't be undone and the original is not kept, so every false positive here loses information. Should `card_number`, which can't be anchored on a prefix the way the token rules are, wait for the deferred `REMIND_ME_REDACT=block` mode?

## Optional improvements

- `google_api_keys` needs a run of exactly 35 characters, so `AIza<35>-prod` is missed. That is acceptable, but worth a comment.
- The `github_pat_` comment says 82 characters while the floor is 60. Say why the floor is lower.

## Checks run

- **Pinned head:** matches `abdd0b76`.
- **`rustfmt --edition 2021 --check crates/remind_me_core/src/redact.rs`:** passed.
- **Workspace commands did not run:** `cargo test --locked` and `cargo clippy --locked` stopped at resolution. `~/.cargo` is read-only in this sandbox, and the `rusty_mill` git dependency (`libs-2026.10.0`) is not cached.
- **Substitute scratch crate:** I copied `redact.rs` into a scratch crate with `serde_json` (current, not the locked version) and its own `CARGO_HOME`.
  - At head, `cargo test`: 10 passed.
  - `cargo clippy --all-targets -D warnings`: clean.
  - Head tests moved onto base `redact.rs`: `provider_tokens_are_redacted` and `card_numbers_with_a_network_prefix_and_luhn_are_redacted` fail, so they guard the change. `connection_strings_lose_their_password` passes, which is expected because it records existing behaviour. `new_shapes_leave_ordinary_text_alone` passes, also expected, since it is a negative guard.
- **Probes:** I ran standalone `rustc` probes against the head and base copies. All outputs quoted above come from them.
- **Validation gaps:**
  - Workspace-wide `cargo fmt --all`, clippy and tests across all six packages could not be run. Re-run them where the `rusty_mill` git dependency resolves.
  - I traced how REST, CLI and the importer handle a redaction panic from source only, not by execution.
