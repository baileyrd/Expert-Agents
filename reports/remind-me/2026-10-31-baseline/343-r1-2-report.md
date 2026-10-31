<!-- remind-me/343-r1 run 2, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 201 s, 714529 tokens, $0.88 -->

I'm requesting changes to PR 343: two defects I reproduced by running probes, and a third that comes from the same digit-grouping step. The PR's own tests pass, but none of them exercise these cases. The workspace couldn't be built here, so they ran against a standalone copy of `redact.rs`.

**Pinned:** Rusty-Mill/rusty-remind-me PR 343. Base `d5528b1347054f348b3dac96f54619e603f4594c`. The fetched PR head equals the head SHA `abdd0b76add2f3c5cf61cb1533f225405d53f931`. The diff touches `crates/remind_me_core/src/redact.rs`, `README.md` and `RELEASE_NOTES.md`.

## What the change touches
`redact()` is the single scrub step on every write path. It covers `queries.rs:56` (add_memory), capture, transcript, the session-archive JSONL (`boundary.rs:38`), the importers, and project facts and proposals. It also drives `looks_secret` (`project.rs:962`), which refuses project names and repo values and skips session cwd and git remotes.

Because there is no un-redact, two invariants must hold:
1. A shape the docs say is redacted never reaches storage, sync or replay.
2. Text that is not a secret is stored exactly as written. Otherwise a redaction marker becomes the fact every peer holds.

Sync, outbox, the tool list and retrieval are not affected (no wire or schema change). The hidden-rows, time-as-text, concurrency and caller-identity failure classes are ruled out because the diff only changes a pure string function.

## Blocking findings

**1. P1: overlapping matches panic on a write path** (*executed*)
- **Where:** `redact.rs:136-149` (`prefixed`) feeding `replace` at `redact.rs:85-95`.
- **Cause:** `anchors` only rejects a prefix that follows a letter or digit. For prefixes whose body allows `-` or `_`, a second prefix inside the first match becomes a range nested in the first. `replace` then slices `text[at..start]` with `at > start` and panics.
- **Inputs that panic:**
  - `sk-<40 chars>-sk-<40 chars>` → "byte range starts at 91 but ends at 48"
  - `github_pat_<70>_github_pat_<70>`
  - `glpat-<25>-glpat-<25>`
- **Scope:** Slack `xoxb-…-xoxb-…` already panicked at base. This PR adds three more shapes and moved all of them into the shared helper without fixing it.
- **Who is hurt:** the memory, capture or JSONL archive holding that text cannot be written, and every retry panics again. Stdio MCP catches the panic (`remind_me_mcp/src/lib.rs:3632`) and the write still fails. I did not trace whether REST and the folder-watcher thread catch panics.
- **Fix:** drop any range that starts before the end of the previous one, in `prefixed` or (more safely) in `replace` for every rule.
- **Regression test:** the three inputs above plus the Slack one. Each should produce a single `[REDACTED:<kind>]` and not panic.

**2. P1: ordinary decimals are replaced with `[REDACTED:card_number]`** (*executed*)
- **Where:** `redact.rs:216-238`, `251-271`.
- **Cause:** a `.` before a digit run counts as a word boundary, so the digits after a decimal point are judged as a card.
- **Examples:**
  - `score 0.5555555555554444` → `score 0.[REDACTED:card_number]`
  - `{"lat": 40.4111111111111111}` → `40.[REDACTED:card_number]`
- **How often:** of 100,000 values written in Rust's default float format (`{}`), 1,222 (about 1.2%) were redacted.
- **Who is hurt:** a memory recording benchmark results, scores or coordinates loses the number for good. It is tagged `redacted` and synced, and an agent reading it acts on a gap. The same happens inside archived transcript JSONL.
- **Fix:** require that a card span is not preceded by `.` or `,` followed by a digit (decimals in either notation), and not followed by `.` and a digit.
- **Regression test:** the two examples above, added to `new_shapes_leave_ordinary_text_alone`.

**3. P0 by the impact rubric: real card numbers are missed when another number sits next to them** (*executed*)
- **Where:** `redact.rs:241-249` (`digit_span_end`), `235`.
- **Cause:** `digit_span_end` joins any run of digit groups separated by single spaces or hyphens, with no limit. A card followed or preceded by another number becomes one 18-to-20-digit span that fails the length check, and the card is skipped.
- **Inputs that leak unchanged:**
  - `card 4111111111111111 12/30 cvv 123`
  - `4111 1111 1111 1111 12/30`
  - `Visa 4111 1111 1111 1111 123`
  - `ref 12 4111111111111111`
- **Context:** base stored these too, so this is not a regression. But the release note and README now claim that cards "optionally grouped by spaces or hyphens" are redacted. These common layouts (expiry or CVV right after the card) reach storage, sync and replay.
- **Note on the test:** the PR's own test avoids this layout by putting a comma before "exp".
- **Fix:** look for a valid card window inside the digit span, along group boundaries, rather than judging the whole span.
- **Regression test:** the four inputs above, each expected to redact only the card.

**4. P1: `sk-` slugs are destroyed as `llm_api_key`** (*executed*)
- **Where:** `redact.rs:184-186`.
- **Cause:** any `sk-` followed by 32 or more characters from `[A-Za-z0-9_-]` matches.
- **Examples:**
  - `branch sk-1234-fix-the-login-redirect-loop-bug` → `branch [REDACTED:llm_api_key]`
  - `sk-learn-compatible-estimator-interface-design` → redacted the same way
- **Who is hurt:** the stored fact loses its branch or identifier. Through `looks_secret`, a project name or repo value of that shape is now refused, and a session cwd or git remote skipped.
- **Fix:** require at least one digit and one capital letter in the key body (real OpenAI and Anthropic keys have both), or a known sub-prefix (`proj-`, `ant-`, `svcacct-`) or a longer floor.
- **Regression test:** the two slugs above, added to `new_shapes_leave_ordinary_text_alone`.

**5. P2: missing tests for changed behaviour.** No test covers overlapping prefixes, cards next to other digits, decimals, or `sk-` slugs. Of the new tests, `connection_strings_lose_their_password` and `new_shapes_leave_ordinary_text_alone` both pass at base. The first only pins behaviour that already existed, as the release note says. The second protects the new rules only at head, which is fine for a negative test. Add the tests named in findings 1–4 to the `redact.rs` tests module.

## Design questions
- Should the card rule run on archived transcript JSONL at all? Tool output there is dense with long numbers.
- In `looks_secret`, a false positive now *refuses* a project or repo value rather than merely masking it. That widens the cost of every new heuristic.

## Optional improvements
- The comment at `redact.rs:162` says "82 word characters", but the floor is 60. Say it is a floor.
- `prefixed` is O(n²) on input like `sk-sk-sk-…`, as Slack already was at base. A floor that skips anchors inside an earlier match (the fix for finding 1) also removes this.
- A UUID whose first three groups are all digits can, rarely (about 1 in 100,000), match as a card.

## Checks
- `cargo fmt --all -- --check`: passed.
- The PR's unit tests (all of `redact.rs` except the two `guard_*` tests): passed at head (8 tests). I ran them in a standalone copy of `redact.rs` with `guard` removed, because the workspace would not build here.
- New tests at base: `card_numbers…` fails, as it should. `connection_strings…` and `new_shapes…` pass at base, as covered in finding 5. I did not run `provider_tokens…` at base; it cannot pass there because base has no such rules.
- Probe tests in scratch (`$TMPDIR/probe/tests/p.rs`): produced every result quoted above.
- Clippy on the copied module: clean.
- **Not run:** workspace clippy and `cargo test --locked` for the six packages. The `rusty_mill` git dependency (`libs-2026.10.0`) is not cached, and the sandbox makes `~/.cargo` read-only, so cargo could not fetch it. Run those in a normal environment, along with `boundary_test`, `archive_test` and the transcript tests.
- **Not traced:** whether the REST handler and the folder watcher catch the panic from finding 1.

**Verdict: changes required.**
