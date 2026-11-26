<!-- remind-me/343-r1 run 3, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 843 s, 1138054 tokens, $1.10 -->

# Review of Rusty-Mill/rusty-remind-me PR 343: changes required

The PR adds new redaction rules for provider API keys and card numbers. Two of them can make `redact()` panic, and the card rule misses card numbers in the forms people usually paste them.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `abdd0b76add2f3c5cf61cb1533f225405d53f931` (fetched `pull/343/head`; it matches the head SHA)
- **Diff:** `crates/remind_me_core/src/redact.rs`, `README.md`, `RELEASE_NOTES.md`; one commit.

## What the change touches, and what must hold
- **`redact::RULES`**: every write path calls these rules through `boundary::scrub`, `scrub_field` or `scrub_jsonl` (`queries.rs:56`, `capture.rs:110/382`, `importer.rs:1412`, `transcript.rs:141/336/482`, `project*.rs`). They must never panic. Each secret shape they claim to cover must be replaced before the insert, and ordinary text must be left alone, because redaction can't be undone and the result is copied to every peer.
- **`metadata.redactions` and the `redacted` tag**: the new kind strings are plain strings inside metadata JSON. Older peers decode them unchanged, so there is no wire or schema change. No tables, tools, outbox or hub code are touched.
- **README and release notes**: every claim must match the code.

## Blocking findings

### 1. P1: overlapping matches make `redact()` panic on the write path (executed)
- **Where:** `redact.rs:136-149` (`prefixed`). The panic happens at `redact.rs:89` (`replace`).
- **Cause:** the token body for `sk-`, `glpat-`/`gldt-`/`glrt-`, `github_pat_` and `pypi-` may contain `-` or `_`. Another prefix inside that body therefore also counts as a match start, and both matches end at the same byte. `prefixed` keeps both. `replace` then slices `text[end..inner_start]` and panics.
- **Probe results:**
  - `sk-<40>-sk-<40>` panics: "byte range starts at 87 but ends at 44".
  - `github_pat_<30>_ghp_<36>` panics.
  - `glpat-<20>-glrt-<20>` panics.
  - The Slack form `xoxb-…-xoxp-…` panics the same way at base. The PR moves Slack into `prefixed` and adds four new ways to hit the bug.
- **Who is hurt:**
  - Stdio MCP catches the panic (`remind_me_mcp/src/lib.rs:3632`), so the agent's write fails with an error.
  - REST, CLI, hook capture and the importers have no `catch_unwind`, so the thread or process dies.
  - `scrub_jsonl` redacts every string in a transcript, so one such string makes that session's capture panic on every retry.
- **Fix:** in `prefixed`, drop any match that starts inside an earlier one, using the `floor` approach already in `jwts` and `generic_secrets`. Also make `replace` skip a range where `start < at`, as a backstop.
- **Regression test:** `overlapping_prefixed_tokens_do_not_panic`. Feed it the four strings above and assert each becomes a single `[REDACTED:<kind>]`.

### 2. P0 by impact (a secret is stored): card numbers in common forms are kept (executed)
- **Where:** `redact.rs:216-249` (`card_numbers`, `digit_span_end`).
- **Cause:** `digit_span_end` joins every digit group separated by a single space or hyphen. If the joined run is not a valid card, the scanner jumps past the whole run (`i = end`) and never checks the card inside it.
- **Inputs stored unchanged in the probe:**
  - `card 4111 1111 1111 1111 12/30 cvv 123` (expiry after the number)
  - `card 4111111111111111 123` (CVV after the number)
  - `paid 2 4111111111111111 today`
  - `2026-11-01 4111-1111-1111-1111`
- **Why it matters:** the PR's test only covers the form `card {card}, exp 12/30`, where a comma breaks the run. README line 267 and the release note now tell users card numbers are redacted, so a person may paste a card into a memory. It is then stored, given to agents and synced to every peer.
- **Severity:** before this PR no card number was caught at all. I grade it P0 because the impact is a stored secret, and the new docs promise this case is covered.
- **Fix:** when the joined run is not a card, also test each window of consecutive groups and each bare digit run inside it. Alternatively, stop joining once a group would push the length past 19.
- **Regression test:** the four strings above, each expected to contain `[REDACTED:card_number]`.

### 3. P2: ordinary text is permanently over-redacted (executed)
- **Where:** `redact.rs:184-186` (`sk-` rule) and `redact.rs:241-249` (card span).
- **Probe results:**
  - `row 4 0 0 0 0 0 0 0 0 0 0 0 0 0 0 2 ok` becomes `row [REDACTED:card_number] ok`. Sixteen single-digit groups are joined into one number that passes Luhn.
  - The kebab-case slug `branch sk-add-redaction-for-provider-keys-and-cards done` becomes `[REDACTED:llm_api_key]`.
- **Who is hurt:** an agent reading that memory finds the fact gone, and the memory is wrongly tagged `redacted`.
- **Fix:**
  - Card rule: require each separated group to have at least 3 digits, or match the standard layouts (4-4-4-4, 4-6-5, 4-4-4-4-3).
  - `sk-` rule: require mixed case plus a digit in the body.
- **Regression test:** add both strings to `new_shapes_leave_ordinary_text_alone`.

### 4. P2: the README claim about connection strings is wider than the code (executed)
- **Where:** `README.md:267`, which says "passwords in URLs and connection strings".
- **What the code does:** only URL-form connection strings are covered. The probe stored these unchanged:
  - `Server=db;User Id=sa;Pwd=Sup3rS3cret!;` (ADO.NET style)
  - `host=db user=app password=hunter2` (libpq keyword form, with a password under 8 characters)
- **Fix:** either say "URL-form connection strings", or add a keyword/value rule with a test for it.

## Design questions
- Memories written before the upgrade are not rescanned with the new rules. The release note could say so, or a backfill could be considered.

## Optional improvements
- `google_api_keys` requires a body of exactly 35 characters, so a key followed directly by `_` or `-` is missed (`AIza<35>_old` was stored unchanged).
- The `github_pat_` comment says 82 characters, but the rule accepts 60 or more. It's harmless, but worth one consistent sentence.

## Failure classes checked
- **Applies:**
  - *Boundary skipped* and *Silent success*: findings 2 and 4.
  - Panic on a write path: finding 1.
  - *Memory truth* (over-redaction): finding 3.
- **Ruled out, because the diff changes no records, tables, wire formats, tools or locks and adds no timestamps:**
  - Wire versus row
  - Concurrency
  - Half a change
  - Time as text
  - Retrieval admission
  - Who is calling
  - Protocol subset
  - Tool surface
- **Hidden rows surface:** not applicable.

## Tests
- The new `provider_tokens_are_redacted` and `card_numbers_…_are_redacted` fail at base and pass at head, so they guard the change.
- `connection_strings_lose_their_password` and `new_shapes_leave_ordinary_text_alone` also pass at base. The first only pins behaviour that already existed; the second only checks that ordinary text is left alone.
- I found no test for overlapping tokens, or for cards with an expiry, CVV or a number next to them. Those are the regression tests listed under findings 1–3.

## Checks run
- `cargo fmt --all -- --check`: **passed**.
- `cargo clippy --locked -p` (all six packages) `--all-targets -- -D warnings`: **passed**. I ran it offline with `CARGO_TARGET_DIR=$TMPDIR/target`, because the configured target directory is read-only in this sandbox.
- `cargo test --locked -p remind_me_core --lib redact`: **13/13 passed**.
- `cargo test --locked --no-fail-fast` over the six packages:
  - 156 test binaries passed.
  - 111 tests failed across 11 binaries (`import_export_test`, `archive_test`, `dbs_import_test`, `importer_test`, watcher tests, among others). All 111 panics are `ReadOnlyFilesystem` or `PermissionDenied` errors from the sandbox's read-only `$HOME`.
- Probes were run only in a scratch copy made from `git archive HEAD` under `$TMPDIR`.

## Validation gaps
- **The 111 tests that write under `$HOME`:** re-run on a machine where `$HOME` is writable.
- **What a REST or hook-capture panic does to a live daemon:** run the regression strings through `POST /api/memories` and `capture-transcript` against a running daemon.

## Verdict
**Changes required.**
