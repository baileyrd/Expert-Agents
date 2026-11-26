<!-- remind-me/343-r2 run 1, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 885 s, 1941522 tokens, $1.45 -->

# Review of Rusty-Mill/rusty-remind-me PR 343 (base `d5528b13`, head `9ef2ec3a`)

**Verdict: changes required.** K1 is fixed. K2 is only partly fixed, and adding a card rule opens one new gap: card numbers stored as JSON numbers in archived transcripts are never redacted.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `9ef2ec3a6eaf1fd7fe8b6edd6da5bad0f77337bb`. I fetched `pull/343/head` and it matches this SHA.
- **Diff:** `crates/remind_me_core/src/redact.rs`, `README.md`, `RELEASE_NOTES.md`, in two commits (`abdd0b76`, then the fix `9ef2ec3a`).

## Prior findings at this head
- **K1 (P1) Overlapping prefixed-token matches panic in `replace()`: fixed.** `coalesce` (`redact.rs:90-100`) now sorts and merges each rule's ranges before `replace` (`:52`).
  - I ran it on nested `sk-…-sk-…`, `github_pat_…_ghp_…` and `glpat-…-glpat-…`: each gives a single redaction and no panic.
  - `nested_and_repeated_prefixes_redact_without_panicking` panics at `abdd0b76` and passes at the head.
- **K2 (P2) A card number next to other digits escapes redaction: partly fixed.** Cards next to expiry dates, reference numbers or a second card are now caught, and `cards_next_to_other_numbers_are_still_redacted` goes red at `abdd0b76`. One case remains, finding 2 below.

## Blocking findings

### 1. P1: A card in a JSON number survives the transcript archive even though the same capture redacts it elsewhere
- **Where:** `crates/remind_me_core/src/boundary.rs:58-75`. `redact_strings` only redacts `Value::String`; numbers fall through to `_ => false` at `:74`.
- **Why this PR makes it reachable:** `card_number` is the first rule that can match a plain JSON number.
- **What breaks:** ADR-0029 §1 (lines 40-43) promises that "neither the dialog nor its source returns a secret the capture redacted".
- **Scenario (executed against the real crate in a scratch worktree):**
  1. Transcript line: `{"type":"assistant",…,"content":[{"type":"text","text":"charging 4111111111111111"},{"type":"tool_use",…,"input":{"amount":10,"card":4111111111111111}}]}`.
  2. The dialog is redacted correctly: `**assistant:** charging [REDACTED:card_number]`.
  3. `scrub_jsonl` output: `…"text":"charging [REDACTED:card_number]"…"input":{"amount":10,"card":4111111111111111}…`.
  - With `REMIND_ME_ARCHIVE_DIR` set, `remind_me_source` returns that card to any agent that reads the session. Anyone reading that output is affected.
- **Correction:** redact card-shaped number tokens too, for example by replacing a matching `Value::Number` with the string `"[REDACTED:card_number]"`.
  - Work from the raw token text, not from `Number::to_string()`. A 19-digit card above `u64::MAX` parses as `f64` and loses digits.
- **Regression test:** in `boundary.rs` tests, `scrub_jsonl("{\"card\":4111111111111111}\n")` must not contain `4111111111111111` and must still parse as JSON. It fails at this head.

### 2. P2 (K2 residue): A card escapes when a later separated digit group runs into a letter
- **Where:** `crates/remind_me_core/src/redact.rs:249`. The glued-to-a-letter check looks at the end of the whole space- or hyphen-joined run. If that run ends against a letter, `cards_in` is never called for any window in it.
- **Scenario (executed):** these inputs all come back unchanged with no hits:
  - `card 4111111111111111 2nd try`
  - `4111 1111 1111 1111 3pm`
  - `pay 4111-1111-1111-1111 1x`
  - `4111111111111111 12 30x`
- **Who is hurt:** the card is stored and synced in plain text, while `RELEASE_NOTES.md` and the fix commit say a card next to other numbers is redacted.
- **Grade:** P2, the same as K2, because it is the same defect and the base did not redact cards at all.
- **Correction:** apply the glued check per window. A glued last group should only rule out windows that include that group (and likewise a glued first group), not the whole run.
- **Regression test:** add `("card 4111111111111111 2nd try", "card [REDACTED:card_number] 2nd try")` and `("4111 1111 1111 1111 3pm", "[REDACTED:card_number] 3pm")` to `cards_next_to_other_numbers_are_still_redacted`.

### 3. P2: The doc comment claims Diners support that the code never delivers
- **Where:** `redact.rs:303-306` says the `4-6-4` layout covers "Amex/Diners", and `card_shaped` accepts it at `:309`.
- **The problem:** `is_card` (`:323-333`) rejects every 14-digit number and the 30/36/38 prefixes, so the `4-6-4` branch can never match a card.
- **Executed:** `diners 3056 930902 5904` and `30569309025904` are not redacted.
- **Correction:** either add Diners (36, 300-305, 38-39, length 14) to `is_card` with a test such as `"3056 930902 5904"` → `[REDACTED:card_number]`, or remove "Diners" and the `4 | ` alternative from the comment and pattern.

## Design questions
- Redaction happens only when a node writes (ADR-0026). Older nodes and the hub do not re-redact rows they receive by sync, so cards written on a node that hasn't upgraded spread unredacted. This is settled behaviour; I raise it only in case the owner wants the card rule applied on apply or import.

## Optional improvements
- `llm_api_keys` matches any `sk-` preceded by a non-alphanumeric character and followed by at least 32 base64url characters. That includes kebab-case slugs: `x-sk-kebab-case-slug-name-for-a-long-thing` becomes `x-[REDACTED:llm_api_key]`, which changes a stored fact. Requiring a digit, or mixed case, in the run would avoid this.
- JCB (`3530111333300000`) and UnionPay (`62…`) cards are not matched, which I ran. The release note's "payment card numbers" could name the networks that are covered.
- The release note says cards may be "grouped by spaces or hyphens". The code requires single separators in a 4-4-4-4(-n) or 4-6-5 layout. Saying so would set the right expectation.

## Failure classes
- **Half a change:** no transactions in the diff. `redact` is a pure function; the single panic risk was K1, which is fixed.
- **Hidden rows surface, Time as text, Concurrency, Retrieval admission, Who is calling:** not touched by the diff.
- **Wire versus row:** no new fields or sync changes. Redacted content syncs like any other content.
- **Status that lies:** `hits` and `metadata.redactions` reflect what fired. The coalesce change does not drop a kind.
- **Tool surface:** no tool or schema changes. `project.rs:965` now also refuses claims containing a card, which is intended.
- **Boundary skipped:** finding 1.
- **Protocol subset:** not applicable; no parser of a spec'd protocol was touched.
- **Silent success:** findings 1 and 2, where redaction reports nothing and stores the card.
- **Char boundaries:** every new rule anchors on ASCII bytes, so slicing stays on valid boundaries.
- **Tests:** the K1 and K2 tests are red at `abdd0b76` and green at the head. All new shape tests depend on functions this PR adds, so they could not pass at the base.

## Checks and validation gaps

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | Passed (exit 0). Cargo printed noise about template `Cargo.toml` files in the rusty_mill git checkout. |
| `cargo clippy --locked` on the six packages, `--all-targets -D warnings` | Passed (exit 0). |
| `cargo test --locked --no-fail-fast` on the six packages | 2483 passed, 3 failed. |
| `redact`-filtered lib tests, fresh target dir | 17 of 17 passed. |
| Scratch probes (standalone copy of `redact.rs`, plus a scratch worktree for `scrub_jsonl` and dialog) | Results as quoted in the findings. |

- **The 3 test failures are environmental:** the `peer_discovery_test` tests fail because `UnixListener::bind` is denied in this sandbox, and the PR doesn't touch that code.
- **`remind_me_api` import tests:** these create scratch dirs in `$HOME`, which is read-only here. They passed once I pointed `HOME` at a scratch directory.
- **Discarded run:** my first test run used a stale binary from a shared target dir. It contained `probe` tests that are not in this head, so I threw it away and rebuilt from scratch.
- **Gaps:** I did not run Postgres hub tests or the `MockHub` path, since redaction never runs on the sync path. The scratch worktree has been removed and the reviewed tree is unchanged.
