<!-- remind-me/343-r1 run 4, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 161 s, 628512 tokens, $0.72 -->

I found two blocking defects in PR 343: a write-path panic that the PR makes reachable for its new token shapes, and a card-number rule that misses common layouts. Changes are required. I couldn't build the workspace, so fmt is the only repository check that ran.

**Repository:** Rusty-Mill/rusty-remind-me, PR #343
**Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
**Head:** `abdd0b76add2f3c5cf61cb1533f225405d53f931`. I fetched `pull/343/head` and it matches this SHA.
**Diff:** one commit touching `crates/remind_me_core/src/redact.rs`, `README.md` and `RELEASE_NOTES.md`.

## What the change touches
- **Records:** the `content` text of every stored memory, its `redacted` tag and `metadata.redactions`. The session archive JSONL and title or summary fields go through the same rules (`boundary.rs:19-75`).
- **Write paths:** `add_memory` (`db/queries.rs:56`), both halves of capture (`capture.rs:110,382`), the importer (`importer.rs:1412`), project claims, backfill and proposals, and transcript capture and archive (`transcript.rs:141,336,482`).
- **Rule that must hold:** redaction runs on the write path, so it must never panic. It must also catch every shape the README and release notes say it catches, because what it misses gets stored, synced to peers and read back into prompts.
- **Unchanged:** no tables, wire formats, tools or sync code. The new redaction kinds are only free-form strings inside `metadata.redactions`.

## Blocking findings

### 1. P1: a panic on every write path when two tokens of one kind are joined by `-` or `_` (executed)
- **Where:** `redact.rs:136-149` (`prefixed`) and `redact.rs:85-95` (`replace`).
- **Cause:** `anchors` treats a match as a token start whenever the previous byte is not alphanumeric, and `-` and `_` are not alphanumeric. So a second token inside the first token's base64url run gets its own range, which overlaps the first. `replace` then slices `text[at..start]` with `start < at` and panics.
- **Trace:** `redact("key sk-<32×A>-sk-<32×B> end")` panics with "byte range starts at 75 but ends at 40". I got the same panic for `sk-proj-…_sk-…` and `glpat-…-glpat-…`.
- **New in this PR:** the same bug already existed for `xoxb-…-xoxp-…` at base. Moving Slack into the shared `prefixed()` helper carries it into the new `sk-` and GitLab rules, so the PR makes it reachable for those shapes. An OpenAI or Anthropic key whose random body happens to contain `-sk-` or `_sk-` triggers it too.
- **Who is hurt:**
  - An agent calling `remind_me_add` gets an error instead of a stored memory. Stdio MCP turns the panic into an error response (`remind_me_mcp/src/lib.rs:3632`).
  - A transcript or import containing such a string fails capture every time it is retried, so that session's memories are never stored.
- **Fix:** make `prefixed` drop any range that starts inside the previous one, the way `generic_secrets` does with its `floor` (`redact.rs:404-410`). Also make `replace` skip or merge overlapping ranges, so no future rule can bring the panic back.
- **Regression test:** `redact` on `sk-{32}-sk-{32}`, `glpat-{20}-glpat-{20}` and `xoxb-{10}-xoxp-{10}` should return a single replacement each. Each of these goes red (panics) at head.

### 2. P0 by impact (a secret stored and synced): the card rule misses a card next to another number (executed)
- **Where:** `redact.rs:216-249`.
- **Cause:** `digit_span_end` keeps joining digit groups separated by one space or hyphen. If a nearby number is joined to the card, the total length or the Luhn check fails, and the card is not tried on its own.
- **Missed, and stored as-is:**
  - `card 4111 1111 1111 1111 12/30` (card then expiry)
  - `card 4111 1111 1111 1111 123` (card then CVV)
  - `qty 2 4111111111111111`
  - `2026-10-31 4111111111111111`
  - `5555-5555-5555-4444 06/28`
- **Caught:** `4111111111111111 exp 12/30` is redacted. The PR's test only uses `, exp 12/30`, where the comma stops the join, which is why it passes.
- **Impact:** a card number pasted in the most common layout is stored, synced to every peer and read back into prompts. Meanwhile `RELEASE_NOTES.md:8` and `README.md:267` say card numbers are redacted. This is not a regression from base, but the docs claim is false for these shapes.
- **Fix:** test each run of whole groups inside the joined span (start at every group boundary, extend up to 19 digits) and redact any run that passes `is_card`.
- **Regression test:** the five inputs above should each produce `[REDACTED:card_number]`, with the surrounding text kept.

### 3. P2: missing tests
- No test covers overlapping or adjacent tokens (finding 1) or a card with digits on either side (finding 2). The tests named above belong in `redact.rs` `mod tests`.

## Design questions
- A 13, 16 or 19-digit id starting with 4, or a 16 to 19-digit id starting with 6011, 65 or 644–649, that passes Luhn (about 1 in 10) will now be overwritten for good. Order and invoice numbers are the likely victims. Should these redactions be kept somewhere recoverable, or should the rule need a nearby word like "card" or "visa"?

## Optional improvements
- The `github_pat_` comment says 82 characters but the code accepts 60 or more. Make them agree.
- `google_api_keys` needs exactly 35 characters, so a key followed directly by `-` or `_` is missed.
- Newer GitLab routable tokens contain `.` segments; only the part before the first dot is redacted.

## Checks and validation gaps
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | Passed |
| `cargo clippy` / `cargo test --locked` (six packages) | **Not run.** `~/.cargo` is read-only in the sandbox, so the `rusty_mill` git dependency can't be checked out. |
| PR's unit tests on a scratch copy of `redact.rs` (serde-only code removed), at head | 8 of 8 passed |
| Same tests at base | `provider_tokens_are_redacted` and `card_numbers_…` fail, so they guard the change. `connection_strings_lose_their_password` passes at base as intended. `new_shapes_leave_ordinary_text_alone` passes at base, as any no-false-positive test would. |
| Scratch probes | Produced the panics in finding 1 and the missed cards in finding 2 |

**Still to settle:** the full workspace clippy and test run, which needs a writable cargo home or a pre-fetched `rusty_mill` at the `libs-2026.10.0` tag. I also haven't checked how the REST server and folder watcher handle a panic from a write, because I couldn't find their threading model quickly. A REST `POST` with `sk-{32}-sk-{32}` would show whether the connection or worker dies.

## Verdict
**Changes required.**
