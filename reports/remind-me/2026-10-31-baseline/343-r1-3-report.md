<!-- remind-me/343-r1 run 3, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 173 s, 561177 tokens, $0.74 -->

# Review of PR 343: Rusty-Mill/rusty-remind-me

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head reviewed:** `abdd0b76add2f3c5cf61cb1533f225405d53f931` (I fetched `pull/343/head`, and it matches)
- **Diff:** `crates/remind_me_core/src/redact.rs`, `README.md`, `RELEASE_NOTES.md`. It is one commit: "redact: catch provider API keys, Stripe, npm, PyPI, GitLab, Google keys and card numbers".

**Verdict: changes required.** The new redaction rules can crash a write on two ordinary-looking key shapes, and the card-number rule misses the most common way people paste a card.

## What has to stay true

- **Memory content:** no secret listed in the README or release notes is stored on any write path. Every caller of `boundary::scrub`, `scrub_field` or `scrub_jsonl` goes through the same rules: `queries.rs:56`, `capture.rs:110/382`, `importer.rs:1412`, `transcript.rs:482`, `project.rs:998`, `project_backfill.rs:162`, `project_proposals.rs:309`, and the session archive.
- **`redact()`:** it never panics, and it leaves ordinary text alone. If it panics, the write fails. If it over-matches, the stored fact is wrong and gets tagged `redacted`.
- **Sync:** redaction only happens on the writing node, so whatever it lets through is copied to every peer unchanged (ADR-0004). The PR changes no wire format, table or tool.

## Blocking findings

### 1. P0: card numbers followed or preceded by other digits are stored in full
*Executed against a scratch copy.*

- **Where:** `redact.rs:216–249` (`card_numbers` and `digit_span_end`).
- **Why it happens:** `digit_span_end` joins every run of digits separated by single spaces or hyphens, however long, and only then checks length and Luhn. A real card next to another number becomes one long span that fails the check, so nothing is redacted.
- **Probe results** (input → stored output, hits):
  - `card 4111 1111 1111 1111 12/30` → unchanged, `[]` (the card plus a space-separated expiry)
  - `card 4111111111111111 123` → unchanged, `[]` (the card plus a CVV)
  - `paid 2024 4111 1111 1111 1111` → unchanged, `[]`
- **Who is hurt:** a person pastes a card number and expiry into a note or chat that gets captured. The README and release notes now promise card numbers are redacted. The full number is stored, synced to every peer and served in briefs and search.
- **Fix:** don't judge only the whole joined span. Check candidates that end at group boundaries (and start at them too), and redact the longest one that has a valid network prefix, length and Luhn digit.
- **Regression test:** add cases to `card_numbers_with_a_network_prefix_and_luhn_are_redacted` for `"{card} 12/30"`, `"{card} 123"` and `"2024 {card}"`. All three fail at the head.

### 2. P1: a second key prefix inside a key's body panics the write path
*Executed against a scratch copy.*

- **Where:** `redact.rs:136–149` (`prefixed`), with the panic at `replace` `redact.rs:89`. Reached through `llm_api_keys` (`:184`), `gitlab_tokens` (`:168`) and `pypi_tokens` (`:199`).
- **Why it happens:**
  - `anchors` only refuses a prefix that follows a letter or digit. The body match `is_b64url` accepts `-` and `_`, so a second `sk-` after `-` or `_` inside a match is anchored too.
  - That produces nested ranges, `(i, e)` and `(k, e)` with `i < k < e`.
  - `replace` then slices `text[e..k]`, which panics.
- **Probe results:**
  - `key sk-proj-<40 chars>_sk-<40 chars> end` panics with "byte range starts at 96 but ends at 53".
  - `glpat-<40>-glpat-<40>` panics the same way.
  - A real `sk-proj-` body can itself contain `-sk-` or `_sk-`, and two keys joined by `_` or `-` trigger it every time.
- **Impact:**
  - The stdio MCP server catches the panic (`remind_me_mcp/src/lib.rs:3632`) and returns an error, so the memory is not written.
  - The CLI `capture-transcript`, the importer, project backfill and the session archive (`scrub_jsonl`) have no `catch_unwind`. The whole capture or import dies, and it dies again on every retry of the same transcript.
- **Already broken at base:** Slack had the same bug (`xoxb-…-xoxb-…`). This PR moves Slack into `prefixed()` without fixing it and adds three new rules that inherit it.
- **Fix:** in `prefixed()`, sort the ranges and then merge or drop overlaps. Use a floor the way `jwts` and `generic_secrets` already do. Separately, `replace` could refuse to slice when `start < at`.
- **Regression test:** `nested_prefix_does_not_panic`, using `sk-{a}_sk-{a}`, `glpat-{a}-glpat-{a}` and `xoxb-…-xoxb-…`. It should expect a single redaction and no panic.

### 3. P2: the `sk-` rule redacts ordinary hyphenated slugs and breaks the release note's promise
*Executed against a scratch copy.*

- **Where:** `redact.rs:184`. The rule is `sk-` plus 32 or more characters from `[A-Za-z0-9_-]`, with nothing else required.
- **Probe results:**
  - `branch feature/sk-1234-implement-the-new-billing-flow-for-teams merged` → `branch feature/[REDACTED:llm_api_key] merged`
  - `see sk-learn-compatible-estimator-interface-design` → fully redacted
- **Who is hurt:** an agent later reads a memory whose branch name or reference is gone for good, and which carries a false `redacted` tag. The release note says `ask-me`-style text stays untouched, but the test only covers short words.
- **Fix:** tighten the shape. For example, bare `sk-` followed by alphanumerics only (the legacy 48-character keys), or `sk-proj-`, `sk-ant-`, `sk-svcacct-` and `sk-admin-` followed by base64url. Or require a body that mixes letters and digits and contains no run of lowercase words joined by hyphens.
- **Regression test:** add the two strings above to `new_shapes_leave_ordinary_text_alone`.

## Failure classes ruled out

- **Wire versus row, sync and concurrency:** not applicable. There is no schema, wire, outbox or threading change.
- **Boundary skipped:** every write path already goes through `boundary::scrub*`, and the new rules reach all of them. That is also why finding 2 reaches the importer, capture and archive. Rows that arrive by sync are not redacted again, but that was already true and is not changed here.
- **Rule order and idempotence:** each rule runs on the previous rule's output. Placeholders like `[REDACTED:google_api_key]` contain `api_key`, but a `]` follows it, so the generic rule doesn't fire. Values that start with `[REDACTED:` are skipped.
- **Google rule:** a key nested inside another can't produce overlapping ranges, because the outer match requires exactly 35 characters.
- **Card scanning:** works byte by byte on ASCII digits, so it never cuts a multi-byte character.
- **Time as text:** no timestamps are touched. The fixtures don't depend on today's date.
- **Docs:** the README and the release note match the code except where findings 1 and 3 make them wrong.

## Tests

- **Fail without the change:** I ran the new tests against the base implementation. `provider_tokens_are_redacted` and `card_numbers_with_a_network_prefix_and_luhn_are_redacted` fail there, so they guard the new rules.
- **Pass at base:** `connection_strings_lose_their_password` passes at base, which the PR says on purpose (it pins existing behaviour). `new_shapes_leave_ordinary_text_alone` also passes at base, which is expected because it only checks for false positives.
- **Missing coverage:** nested or adjacent prefixes (finding 2), cards next to other numbers (finding 1), and long hyphenated `sk-` slugs (finding 3). All three belong in `redact.rs`'s `tests` module.

## Design questions

- Redaction runs only on the writing node. Peers running an older version still store and sync secrets of the new kinds unredacted. That is decided by ADR, not a defect, but whoever owns sync should know the new rules only protect writes made on upgraded nodes.

## Optional improvements

- The PyPI rule only matches tokens from `pypi.org`. TestPyPI tokens (`pypi-AgENdGVzdC5weXBpLm9yZw…`) are missed. Either add them or narrow the release-note wording.
- A Google key immediately followed by `-` or `_` is missed, because the rule requires exactly 35 characters after `AIza`.
- `replace()` could check that its ranges never overlap, in debug builds or always, so a future rule can't bring this panic back.

## Checks run

- **`cargo fmt --all -- --check`:** passed (exit 0).
- **Workspace `cargo clippy` and `cargo test`:** not run. Cargo needs to fetch the git dependency `rusty_mill` at tag `libs-2026.10.0`, and `~/.cargo` is read-only in this sandbox, so it couldn't fetch it ("Read-only file system").
- **Scratch crate:** I built `redact.rs` at the head on its own with `serde_json 1.0.151`, using a copy of the local registry.
  - The module's own unit tests: 10 passed.
  - `cargo clippy --all-targets -D warnings`: clean.
  - The probe test, which produced findings 1–3.
  - Head tests against the base implementation: 8 passed, 2 failed, as described above.
- **Validation gaps:**
  - The full six-package clippy and test run. Rerun it with a writable `CARGO_HOME` and network access to github.com.
  - End-to-end confirmation that a `capture-transcript` or import run aborts on the finding 2 input. I traced that from the source: none of those paths has a `catch_unwind`.
