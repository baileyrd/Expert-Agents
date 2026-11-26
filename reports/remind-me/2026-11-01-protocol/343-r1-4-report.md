<!-- remind-me/343-r1 run 4, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 815 s, 912133 tokens, $0.99 -->

I found one bug that crashes the write path and two kinds of secrets the new rules miss, so this PR needs changes.

**Rusty-Mill/rusty-remind-me PR 343** · base `d5528b1347054f348b3dac96f54619e603f4594c` · head `abdd0b76add2f3c5cf61cb1533f225405d53f931` (the fetched `pull/343/head` matches it)

The diff touches `crates/remind_me_core/src/redact.rs`, `RELEASE_NOTES.md` and one `README.md` line.

## What must hold
- **Redaction** (ADR-0026 §7, ADR-0029 item 40): `redact()` runs on every write path through `boundary::scrub`, `scrub_field` and `scrub_jsonl` (`capture.rs:110`, `db/queries.rs:56`, `importer.rs:1412`, `transcript.rs:336,482`, `project*.rs`). It must never panic, must catch every shape it claims to catch, and must leave ordinary text alone.
- **Release notes and README**: their claims must be true of the code.
- **Sync, tool schemas, retrieval, auth**: not touched. Redaction runs before the insert, so peers only ever see text that was already scrubbed. No wire format, tool or table changes.

## Blocking findings

### 1. P1: certain input makes redaction panic, so the write fails (*executed*)
- **Where:** `redact.rs:136-149` (`prefixed`) and `replace` at `redact.rs:85-95`.
- **Cause:** `anchors` only rejects a prefix that comes right after a letter or digit, while the `sk-`, `glpat-`/`gldt-`/`glrt-` and `xox?-` rules let a token's body run over `-` and `_`. A second prefix inside that run is found as a new token whose range sits inside the first. The ranges are sorted but overlaps are never removed, so `replace` hits `text[at..start]` with `at > start` and panics.
- **Inputs that panicked in my probe:**
  - `"k sk-<40 chars>-sk-<40 chars> x"`
  - the same with `_` instead of `-`
  - `"glpat-<20>-glrt-<20>"`
- **Who is hurt:**
  - Any agent or person writing that text: the stdio MCP loop catches the panic (`remind_me_mcp/src/lib.rs:3632`) and the write is refused with an internal error.
  - Transcript capture: a session log containing it panics inside `scrub_jsonl` (`transcript.rs:336`) on every retry, so that session is never captured.
  - Imports panic the same way. I did not trace REST, the CLI or the watcher beyond that.
- **Already at base for Slack:** `"xoxb-…-xoxp-…"` panics at base too (*executed*). This PR adds the `sk-` and GitLab triggers and moves the faulty pattern into a shared helper.
- **Fix:** in `replace`, or at the end of `prefixed`, drop any range that starts before the previous one ends, as `generic_secrets` already does with `floor`.
- **Regression test:** for each of the three inputs, `redact` returns a single `[REDACTED:<kind>]` and does not panic.

### 2. P0: a card number or Google key next to ordinary text is stored unredacted (*executed*)
I graded this by impact: the secret is stored and synced to peers. It is not a regression, since base stores it too. What the PR adds is a release note and README that say these are redacted.
- **Card numbers** (`card_numbers` / `digit_span_end`, `redact.rs:219-249`):
  - The digit span is greedy and never backs off. Any card followed or preceded by another group of digits, joined by a space or hyphen, becomes 17–20 digits, fails the length check, and nothing is redacted.
  - Left unredacted: `"card 4111111111111111 123"` (card then CVV), `"Visa 4111 1111 1111 1111 05 27"`, `"4111111111111111 12/30"`, `"order 7 4111111111111111"`. This is one of the commonest ways a card is pasted.
  - The PR's test only passes because its fixture has a comma after the card (`"card {card}, exp 12/30"`).
- **Google keys** (`google_api_keys`, `redact.rs:211-217`): the code requires the run of key characters to be exactly 35 long, where the comment says the key is "`AIza` + exactly 35 base64url characters". A key followed by `-` or `_` (for example markdown `_AIza…_`, or `AIza…-x`) is missed.
- **Fix:**
  - Cards: when the whole span fails, test the sub-spans that start and end on group boundaries.
  - Google: take exactly 35 characters, then require the next byte not to be a letter or digit (as `aws_keys` does), or accept a run of 35 or more and cut it at 35.
- **Regression test:** the four card inputs and `AIza<35>-x` must be redacted.

### 3. P2: kebab-case names that start with `sk-` are rewritten as LLM keys (*executed*)
- **Where:** `llm_api_keys`, `redact.rs:183-185`.
- **Trace:** `"branch sk-add-redaction-for-provider-keys-and-cards done"` becomes `"branch [REDACTED:llm_api_key] done"`. Any slug or branch name of 32+ characters after `sk-` is lost from the stored memory and tagged `redacted`, so the agent reading the memory loses the fact.
- **Fix:** require something no slug has, such as at least one digit and one uppercase letter, or no run of lowercase-plus-hyphen words.
- **Regression test:** add that string to `new_shapes_leave_ordinary_text_alone`.

### 4. P2: tests and docs claim more than the code delivers
- The new tests have no case with two tokens joined by `-` or `_`, and no card followed by more digits. Those are the tests named in findings 1 and 2.
- `RELEASE_NOTES.md` ("optionally grouped by spaces or hyphens") and the README claim ("payment card numbers") are false for the inputs in finding 2. Fix them along with the code.

## Design questions
None. ADR-0026 accepts that pattern-based redaction will miss secrets that have no recognisable shape. The misses above do have a recognisable shape, which is why they are findings.

## Optional improvements
- JCB (3528–3589), Diners (36, 38, 300–305) and UnionPay (62) cards are not covered; my probe confirms all three are stored as-is. The release note's "known network prefix" wording is accurate, but the README's "payment card numbers" reads as complete.
- Newer GitLab tokens of the form `glpat-<payload>.01.<suffix>` keep their `.01.<suffix>` tail after the payload is redacted. It is probably not secret; I have not confirmed this against GitLab's token format docs.
- Existing behaviour: in `postgres://app:p@ss@db`, the URL rule cuts at the first `@`, so `ss@db` is stored. A raw `@` in a password is not valid in a URL, so this is a note, not a finding.

## Checks run
- `cargo fmt --all -- --check`: passed.
- `cargo clippy --locked` on the six packages, `--all-targets -D warnings`: passed. The `{{plugin-name}}` errors come from template manifests inside the rusty_mill git checkout, not from this repo.
- `cargo test --locked -p remind_me_core --lib redact`: 13/13 passed at head.
- Full `cargo test --locked --no-fail-fast` on the six packages, with `HOME` pointed at scratch space because the sandbox makes `$HOME` read-only: 165 test binaries passed. The failures:
  - **`peer_discovery_test` (3 tests):** the sandbox refused to bind a Unix socket (`EPERM`). Environmental.
  - **`webhook_test::stopping_joins_the_serving_thread_before_the_database_could_close`:** failed once under the full parallel run and passed 3 out of 3 times on its own. Unrelated to this diff; most likely a timing-sensitive test.
- **The PR's new tests at base:** `provider_tokens_are_redacted` and `card_numbers_…` fail there, so they guard the new rules. `new_shapes_leave_ordinary_text_alone` and `connection_strings_lose_their_password` pass there, which is expected: one only checks for false positives, the other documents existing behaviour.
- **Probes** ran in scratch copies of the head and base under `$TMPDIR`; the reviewed tree was not modified.

## Validation gaps
- **Not run in this sandbox:** `peer_discovery_test` (Unix sockets are blocked) and the hub's Postgres store tests (no Postgres). The diff touches neither.
- **Not traced:** how REST, the CLI importer and the folder watcher behave after the finding 1 panic. A run of `remind_me_api`'s `POST /api/memories` with the `sk-…-sk-…` input would settle it.

## Verdict: **changes required**
