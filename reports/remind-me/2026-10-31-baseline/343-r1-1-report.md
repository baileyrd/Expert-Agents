<!-- remind-me/343-r1 run 1, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 347 s, 1906373 tokens, $1.45 -->

I'm requesting changes on PR 343. The new rules have defects that panic or stall the write path, and they miss card numbers in a common paste format. Two findings are new problems; the other two are gaps in the new rules.

**Pinned:** Rusty-Mill/rusty-remind-me PR 343, base `d5528b1347054f348b3dac96f54619e603f4594c`, head `abdd0b76add2f3c5cf61cb1533f225405d53f931`. The fetched `pull/343/head` equals the head SHA. The PR is one commit and changes `crates/remind_me_core/src/redact.rs`, `README.md` and `RELEASE_NOTES.md`.

## What it touches and what must hold
- **`redact::redact` / `guard`.** These run on every path that stores text: `add_memory` (`db/queries.rs:56`), both capture halves, the transcript archive (`transcript.rs:336`), the importers, decompose, project backfill and proposals, and the project-claim check (`project.rs:962`). Invariant (ADR-0026 §7, ADR-0029 §1): every secret shape is replaced before storage, plain text is left alone, and a scrub never fails a write.
- **`metadata.redactions` and the `redacted` tag.** These only gain new kind strings. Peers already store this metadata as plain JSON, so nothing changes on the sync wire or in the hub tables.
- **README and release notes** claim the new shapes are covered.

## Blocking findings

### 1. P1: Some token shapes now crash redaction on the write path (executed)
- **Where:** `redact.rs:136-149` (`prefixed`), `:155-166` (GitHub), `:168-170` (GitLab), `:184-186` (`sk-`), with the crash in `replace` at `redact.rs:89`.
- **Cause:** a later prefix can start inside an earlier match, because a `-` or `_` before it counts as a boundary. The two matches overlap, and `replace` slices `text[at..start]` with `at > start`, which panics. The JWT and generic rules already guard against this with a `floor`; the new helper doesn't.
- **Inputs that panic at head:**
  - `sk-aB3d-sk-` followed by 32 characters
  - `glpat-aB3d-glrt-` followed by 20 characters
  - `github_pat_` + 30 characters + `_ghp_` + 36 characters. At base this one did not panic: it redacted only the `ghp_` part.
  - The Slack form `xoxb-x-xoxp-…` panics at base too. The PR rewrites Slack onto the same helper, so one fix covers all four.
- **Who is hurt:**
  - **MCP:** `add_memory` panics. MCP stdio catches it and returns -32603, so the agent's memory is neither stored nor refused with a reason.
  - **REST:** `remind_me_api` has no `catch_unwind`.
  - **Transcript capture:** the dialog keeps only the last 200,000 characters, and tool results are dropped (ADR-0029). So a session whose earlier lines or tool output contain such a string passes the dialog scrub, and `write_capture` commits. Then `scrub_jsonl` panics at `transcript.rs:336`. The caller gets a panic instead of its result, and the archive is never written. Later watcher runs return `Held` before reaching the archive, so `remind_me_source` never gets that transcript.
  - **Project claims:** `looks_secret` panics instead of refusing the value.
- **Fix:** skip anchors that start before the end of the previous accepted range, across all prefixes in a rule. Also make `replace` merge overlapping ranges rather than slicing backwards.
- **Regression test:** a `nested_prefixes_do_not_panic` test over the four inputs above. It should assert one redaction and no panic.

### 2. P1: One caller can stall the database for every other caller (executed)
- **Where:** `redact.rs:136-149` and `google_api_keys` at `:204-210`.
- **Cause:** for every anchor, the scan runs to the end of the character run, so repeated anchors make it quadratic.
- **Timings (release build):**

| Input | Size | Time |
|---|---|---|
| repeated `sk-` | 100 KB | 0.66 s |
| repeated `sk-` | 200 KB | 2.6 s |
| repeated `sk-` | 400 KB | 10.4 s |
| repeated `-AIza` | 200 KB | 1.37 s |

- **Who is hurt:** `add_memory` runs `scrub` while holding the shared store lock, and the REST body limit is 8 MiB (`http.rs:22`). Extrapolating, one authenticated write would hold the database for an hour or more, then hit the panic in finding 1. Every other caller and agent waits until then.
- **Fix:** move the next anchor search past the end of the current run, whether or not it matched (a later anchor inside the run only sees a shorter piece of the same run). For the Google rule, stop the scan at 36 characters.
- **Regression test:** a size-scaling test on 1 MB of `sk-` and of `-AIza`, bounded by step count or a generous time limit.

### 3. P0: Card numbers followed by expiry or CVV digits are stored unredacted (executed)
- **Where:** `redact.rs:241-249` (`digit_span_end`) and `:225-235`.
- **Cause:** the span takes every digit group joined by a space or hyphen, then tests the whole span once. It never retries the shorter 13–19-digit prefix.
- **Inputs that come back with no hits:**
  - `card 4111 1111 1111 1111 123 exp`
  - `4111 1111 1111 1111 12/30`
  - `visa 4111111111111111 2030`
- **Who is hurt:** a person pasting card details as usually written gets the number stored verbatim. It then syncs to the hub and every peer and is replayed into briefs. Meanwhile the README and the release note say card numbers "optionally grouped by spaces or hyphens" are caught.
- **Severity:** this is not a regression, since base stored these too. I graded it P0 because the rubric grades by impact alone, and a secret stored counts as P0.
- **Fix:** when the full span fails, try each prefix of the span that ends at a group boundary and is 13–19 digits long, and redact the longest that passes.
- **Regression test:** add those three inputs to `card_numbers_with_a_network_prefix_and_luhn_are_redacted`.

### 4. P2: The `sk-` rule rewrites ordinary kebab-case text (executed)
- **Where:** `redact.rs:184-186`.
- **Cause:** the rule accepts any `sk-` at a boundary followed by 32 or more letters, digits, `-` or `_`.
- **Example:** `feature/sk-1234-implement-the-new-sync-protocol-for-hub` is stored as `feature/[REDACTED:llm_api_key]`. The memory is tagged `redacted`, and `project::looks_secret` refuses a claim value of this shape.
- **Who is hurt:** an agent loses the branch or slug and gets a false "redacted" signal.
- **Fix:** require a key-like body. For example, use a known subprefix (`proj-`, `ant-`, `svcacct-`, `admin-`) or a 48-character body with no `-`, or require mixed case plus digits.
- **Regression test:** add that branch name to `new_shapes_leave_ordinary_text_alone`.

## Design questions
- **Cloud hook parity.** ADR-0029 says the cloud hook redacts "with the server's secret shapes", but the hook lives in rusty_mill. After this PR, OpenAI, Anthropic, Stripe and other keys may leave a cloud container unredacted until that hook is updated.
- **Existing data.** Rows stored before this PR still hold secrets of the new shapes, and the PR doesn't re-scrub them. Peers on older versions also sync such rows unredacted, because the sync apply path doesn't call `scrub`. The release note doesn't mention either.

## Optional improvements
- A card number stored as a JSON number in an archived transcript is not scrubbed, because `scrub_jsonl` only redacts strings.
- A Google key immediately followed by `-` or `_` is missed, because the rule requires exactly 35 characters.
- The `github_pat_` comment says 82 characters, but the rule accepts 60 or more.

## Checks
- **`rustfmt --check` on `redact.rs`:** passed.
- **Clippy (`--all-targets -D warnings`, MSRV 1.89, repo `clippy.toml`):** passed. I ran it on `redact.rs` compiled as a standalone crate in scratch space; it depends only on `serde_json`.
- **Tests on the standalone copy:** all 10 passed.
- **The PR's new tests against base rules:**
  - `provider_tokens_are_redacted` and the card test fail at base, so they guard the change.
  - `new_shapes_leave_ordinary_text_alone` and `connection_strings_lose_their_password` pass at base. That is expected for a false-positive guard and for documenting rules that already existed.
- **Probes:** the panics, card misses, `sk-` false positive and timings above all come from probes run in that scratch copy.
- **Existing fixtures:** a grep found none in the repo matching the new shapes.

## Validation gaps
- **Workspace checks.** I could not run workspace-wide `cargo fmt`, `cargo clippy` or `cargo test` over the six packages. The rusty_mill git dependencies are not in the cache, `~/.cargo` is read-only, and access to github.com was refused. Running those three commands with network access would settle it.
- **REST panic behaviour.** Whether a REST handler panic drops only the connection or the whole server needs a run against `remind_me_api`.
- **Cloud hook parity** needs a check in the rusty_mill repository.

**Verdict: changes required.**
