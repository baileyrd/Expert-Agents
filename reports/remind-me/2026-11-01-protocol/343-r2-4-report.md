<!-- remind-me/343-r2 run 4, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 692 s, 1936806 tokens, $1.66 -->

# Review of PR 343 (Rusty-Mill/rusty-remind-me): redaction catches more secret shapes

The PR head matches 9ef2ec3a6eaf1fd7fe8b6edd6da5bad0f77337bb, so the review is pinned. Base is d5528b1347054f348b3dac96f54619e603f4594c. The diff touches `redact.rs`, `README.md` and `RELEASE_NOTES.md`.

**Verdict: changes required.** K1 is fixed. K2 is only partly fixed. I found four blocking defects: one P1 and three P2.

## Prior findings at this head
- **K1, the panic on overlapping token matches: fixed.** `coalesce` (redact.rs:90) sorts and merges every rule's ranges before `replace`. I ran the new nested-prefix test against the pre-fix `redact.rs` (abdd0b76) and it panics at the old redact.rs:89. At head it passes, and 200,000 fuzzed inputs produced no panic.
- **K2, a card next to other digits escaping: partly fixed.** The windowing handles `<card> 12 30`, `ref 2026 <card>` and two cards side by side; the new test fails on the pre-fix code. One shape still escapes (finding 2).

## What must hold
- **Stored content and titles** (`boundary::scrub` and `scrub_field`): no secret of a covered shape is stored. `metadata.redactions` names exactly the kinds that fired (ADR-0026 §7).
- **Session archive** (`scrub_jsonl`): "neither the dialog nor its source returns a secret the capture redacted" (ADR-0029 §1).
- **Cloud hook:** it scrubs "with the server's secret shapes" (ADR-0029 §2).
- **Sync and tools:** the PR changes no column, wire format or tool schema. Content is redacted before the insert and replicates as stored.

## Blocking findings

**1. P1: the archive keeps a card number that its dialog reports as redacted.** Executed.
- **Where:** `boundary.rs:58-75`. `redact_strings` only touches `Value::String`.
- **Trigger:**
  1. An agent calls a tool with `{"amount":12,"pan":4111111111111111}`.
  2. No string `command`/`path`/... key is present, so `summarize_tool_input` (`importer.rs:205-209`) puts the whole input into the dialog as JSON text.
  3. The new `card_numbers` rule redacts the PAN in that text, and the dialog gets `metadata.redactions = ["card_number"]`.
  4. `scrub_jsonl` leaves the same PAN as a JSON number. `remind_me_source` returns `"pan":4111111111111111`.
- **Who is hurt:** the cardholder, and any agent that calls `remind_me_source`.
- **Why P1 and not P0:** before this PR the archive also stored the PAN. What the PR introduces is the broken ADR-0029 §1 guarantee, plus metadata that claims a redaction the source does not honour.
- **Fix:** in `redact_strings`, run `card_numbers` over each `Value::Number`'s text. On a hit, replace the value with the string `"[REDACTED:card_number]"` and return true.
- **Regression test:** add `an_archived_session_keeps_no_card_its_dialog_redacted` to `archive_test.rs`, with a numeric PAN in `tool_use.input`.

**2. P2: K2 is still open when the card is followed by a word that starts with a digit.** Executed.
- **Where:** `redact.rs:247-251`.
- **Inputs that are stored unredacted:**
  - `card 4111 1111 1111 1111 3DS challenge failed`
  - `visa 4111111111111111 2nd attempt`
  - `4111-1111-1111-1111 1st try`
- **Why:** `digit_groups` joins the `3`/`2`/`1` onto the run. That group is glued to a letter, so the whole run is dropped, including the clean card. The left edge already restarts per byte; the right edge does not.
- **Why P2:** before the PR no card was redacted at all. The new release-note claim is false for these inputs.
- **Fix:** when `bytes[end]` is alphanumeric, pop only the last group and still scan the rest. `4111111111111111y` keeps passing, because popping leaves nothing.
- **Regression test:** add those three inputs to `cards_next_to_other_numbers_are_still_redacted`.

**3. P2: the Diners `4-6-4` shape can never match.** Executed.
- **Where:** `redact.rs:303-310` and `is_card` at :323-333.
- **Trigger:** `card_shaped` and its comment (and the fix commit message) accept the Diners `4-6-4` shape. But `is_card` only accepts a leading 3 when the number starts 34 or 37 and is 15 digits long, so no 14-digit Diners number is ever accepted.
- **Effect:** `3056 930902 5904` and `30569309025904` are stored as-is, and the comment is false.
- **Fix:** either add Diners (300–305, 36, 38/39, 14 digits) with a test case, or remove `[4, 6, 4]` and "Diners" from the code and comment.

**4. P2: the cloud-capture hook no longer uses the server's shapes, as ADR-0029 §2 requires.**
- **Where:** rusty_mill `.claude/hooks/remind-me-capture.py:110-126` at the pinned `libs-2026.10.0` (4e04d59). Its list is commented "The shapes remind_me_core::redact scrubs".
- **Trigger:** that list has none of `llm_api_key`, `stripe_key`, `gitlab_token`, `github_pat_`, `npm_token`, `pypi_token`, `google_api_key` or `card_number`. So in a cloud session, an `sk-…` key or a card leaves the container in the request to the remote server. ADR-0029 §2 says the hook exists to stop exactly that.
- **Fix:** add the matching shapes to the hook in rusty_mill and bump the libs tag. If that has to land later, say so in the release note.
- **Test:** a parity test in rusty_mill's hook tests that feeds each `redact.rs` fixture kind through the hook.

## Design questions (not blocking)
- **The `sk-` rule redacts kebab-case text.** Any 32-character or longer kebab-case run after `sk-` is replaced. `branch sk-add-card-number-redaction-and-tests merged` and `class sk-spinner-double-bounce-animation-delay` both become `[REDACTED:llm_api_key]`, and the stored text is lost for good. The doc comment says prose is left to the generic rule. Should the rule require a digit or mixed case, or one of the `proj-`/`ant-` sub-prefixes?
- **Cards glued to letters are deliberately left alone.** `cc4111111111111111` is not redacted, and a test asserts that. Is that the trade-off you want?

## Optional improvements
- The release note says "grouped by spaces or hyphens", but only single separators work (`4111  1111  1111  1111` and dotted groups are kept). Say "single".
- JCB (3528–3589) and Discover 622126–622925 are not covered.
- Svix-style `whsec_` secrets in base64 leak everything after the first `+`. Stripe's own secrets are alphanumeric, so they are covered.
- **`url_credentials` garbles placeholders on a second pass (existing bug, now easier to hit).** The `[REDACTED:` skip check fails when a placeholder sits inside `://…@`. Running `redact` twice turns `://[REDACTED:card_number]@` into `://[REDACTED:[REDACTED:url_credentials]@`. The fuzz found 91 such cases. No secret leaks.

## Test coverage
- The nested-prefix test and the cards-next-to-numbers test both fail without the fix (checked against abdd0b76).
- `provider_tokens_are_redacted`, `card_numbers_with_…` and `coalesce_merges_…` fail at base, because the rules are absent there (the `coalesce` unit test does not compile).
- `a_card_glued_into_a_longer_number_is_not_matched` passes at base trivially. It guards against the new windowing over-matching.
- Missing tests are named in findings 1–3.

## Checks run
- `cargo fmt --all -- --check`: passed.
- `cargo clippy --locked --offline` over the six packages, `--all-targets -D warnings`: passed. The `{{plugin-name}}` message is a non-fatal warning from a template manifest inside the rusty_mill git checkout.
- `cargo test --locked --offline --no-fail-fast` over the six packages: 2483 passed, 3 failed.
  - The 3 failures are in `peer_discovery_test.rs:45`: the sandbox refuses the Unix-socket bind (EPERM). This PR doesn't touch that file.
  - A first run without `--no-fail-fast` stopped when six `remind_me_api` import tests panicked: their helper creates a directory under the real `$HOME`, which the sandbox can't write. With `HOME=$TMPDIR/home`, those tests pass.
- The `redact` unit tests: 17 of 17 passed.
- Probes in a scratch copy: the card cases, the `sk-` prose cases, the JSONL number case, the end-to-end archive case, and the 200k-input fuzz.

## Validation gaps
- Peer discovery tests need an environment that allows the Unix-socket bind.
- I checked the hook only at the pinned tag, not at rusty_mill `main`.
- The hub's Postgres store was not run. The PR doesn't touch it.
