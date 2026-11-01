<!-- remind-me/343-r1 run 2, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 646 s, 2341369 tokens, $1.74 -->

# Review of Rusty-Mill/rusty-remind-me PR 343: redaction catches more secret shapes

**Verdict: changes required.** One new mistake in a shared parser can crash write paths, and the new card-number rule misses common shapes and redacts ordinary numbers. All three are shown by running code, not just by reading it.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head reviewed:** `abdd0b76add2f3c5cf61cb1533f225405d53f931`. I fetched `pull/343/head` and it matches.
- **Changed:** `crates/remind_me_core/src/redact.rs`, `README.md`, `RELEASE_NOTES.md`. That is one commit.

## What must hold
- **Every write path, including the importers, capture, the transcript archive (`scrub_jsonl`) and the claim and session checks (`looks_secret`, `project.rs:962`):**
  - A secret is replaced before anything is stored.
  - Text that is not a secret is stored unchanged.
  - Redacting never fails the write.
- **Nothing on the wire changes.** Redaction happens only on the node that writes, so the outbox, hub and sync logic are untouched. No tool or schema changed.

## Blocking findings

### 1. [P1] Overlapping matches crash the write; four new rules can now trigger this (executed)
**Where:** `redact.rs:85–95`, with the crash at `:89`. The newly reachable rules are `prefixed` (`:136`) as used by `llm_api_keys` (`:184`), `gitlab_tokens` (`:168`), `github_tokens` (`:155`, `github_pat_` and `ghp_` together) and `pypi_tokens` (`:199`).

**Cause:** `replace` assumes the ranges it is given never overlap. A rule whose match body includes `-` or `_` can match a second prefix inside its own match.

**Scenario:** I ran each input in a scratch crate.

| Input | Result |
|---|---|
| `sk-<10>-sk-<40>` | panic: "byte range starts at 61 but ends at 18" |
| `glpat-<4>-glrt-<20>` | panic |
| `github_pat_<30>_ghp_<36>` | panic |
| `xoxb-…-xoxp-…` at base | already panicked; the PR spreads the pattern to four more rules |

Real OpenAI and Anthropic key bodies contain `-` and `_`, so this can also happen naturally, though rarely (roughly 1 in 10⁵ keys).

**Who is hurt:**
- **REST:** `rusty-remind-me api` runs `ApiServer::run` (`remind_me_api/src/lib.rs:128–139`, called at `remind_me_cli/src/main.rs:821`) with no panic guard. One authenticated `POST` with such content ends the process.
- **Folder watcher:** the watcher thread (`watcher.rs:788–808`) has no `catch_unwind`, so it dies. The bad file is still there on the next start, so ingestion stops again.
- **CLI:** `add` crashes.
- **stdio MCP:** survives; it catches the panic (`remind_me_mcp/src/lib.rs:3632`) but the write fails.
- In every case the content is not stored.

**Fix:** In `replace`, or in every rule's output, sort the ranges and merge overlapping or nested ones before splicing.

**Regression test:** each of the four inputs above, plus the Slack one, returns a single `[REDACTED:<kind>]` without panicking. Each test should go red at the head.

### 2. [P1] A card number next to another number is stored unredacted, so the new docs claim too much (executed)
**Where:** `redact.rs:216–249` (`card_numbers`, `digit_span_end`, and `i = end.max(i + 1)` at `:235`).

**Cause:** `digit_span_end` keeps joining digit groups across any single space or hyphen. If the joined total fails the length or Luhn check, the whole run is skipped and no shorter part of it is tried.

**Scenario:** These are all stored verbatim:
- `card 4111 1111 1111 1111 123 cvv`
- `card 4111111111111111 2030`
- `qty 2 4111111111111111`
- `2026-10-29 4111111111111111 charged`

The card is then synced to peers and replayed into prompts. Meanwhile the README (`:267`) and the release note now say payment card numbers are replaced.

**Severity:** I graded this P1, not P0. Base never redacted cards, so this is not a regression. The harm is that the new docs tell people it is safe to paste a card in exactly the format people usually use.

**Fix:** Look for a valid card inside the joined run, or stop joining after a grouping that is already card-shaped.

**Regression test:** the four inputs above are redacted and the trailing `123`, `2030` and so on survive.

### 3. [P1] Ordinary number lists are permanently replaced with `[REDACTED:card_number]` (executed)
**Where:** `redact.rs:216–271`.

**Scenario:**
- `ports 5432 5433 6379 6380` is stored as `ports [REDACTED:card_number]`.
- In a synthetic sweep of number lists starting with a 4xxx value (`4000 4096 8192 1024` and similar), 1,120 of 11,200, about 10%, were replaced. That is just the Luhn pass rate on numbers with a valid network prefix.

**Who is hurt:**
- An agent loses the ports, sizes or IDs for good, because redaction happens before the write.
- The same rule runs on the transcript archive (`scrub_jsonl`).
- Through `looks_secret`, it can also refuse a project name or claim value containing such a run (`project.rs:962–979`).
- The release note says ordinary numbers are left alone.

**Fix:** For grouped or long numeric runs, require context such as a nearby `card`, `visa`, `cc` or `exp` within a few words, or accept only card-shaped grouping plus context.

**Regression test:** `ports 5432 5433 6379 6380` and `4000 4096 8192 1024` are left untouched, while the existing card cases are still redacted.

### 4. [P2] The OpenAI/Anthropic key rule redacts ordinary kebab-case names (executed)
**Where:** `redact.rs:184`.

**Cause:** `anchors` accepts a `/`, `-` or `_` before `sk-`, and the match body allows `-`.

**Scenario:**
- `https://example.com/sk-learn-classification-pipeline-v2-final` is stored as `https://example.com/[REDACTED:llm_api_key]`.
- A repository URL with a long `sk-…` path segment is refused at the claim and backfill boundary (`project.rs:971`, `project_backfill.rs:64`).

**Fix:** Do not anchor after `/`, and require the body to look random, for example at least one digit and mixed case.

**Regression test:** the URL above is left untouched.

## Design question
Redaction rewrites content before it is stored and cannot be undone. A probabilistic rule like Luhn turns every false positive into permanent data loss. Should card hits flag or refuse the write instead, which is the deferred `REMIND_ME_REDACT=block` idea, rather than rewriting? That is for the owner to decide.

## Optional improvements
- **More card networks:** JCB (35…), Diners (36 and 300–305), UnionPay (62) and Maestro (6759…) are not matched. I confirmed this by running them. Either add them or name only the covered networks in the release note.
- **Google keys:** `google_api_keys` requires the body to be exactly 35 characters, so a key immediately followed by `-` or `_` is missed.
- **Existing memories:** the release note could say that memories stored before this change are not re-scrubbed.

## Checks
- **Rules with no finding:** the failure classes about time, sync, retrieval, the tool surface and caller identity don't apply here. The change is a pure function on the write path, with no new fields, sync payloads, tools or timestamps.
- **Stripe, npm and Google rules:** match bodies are letters and digits only, so the overlap crash can't happen. I found no defects in these rules.
- **`cargo fmt --all -- --check`:** passes.
- **`cargo clippy --locked -p remind_me_core --all-targets -D warnings`, and the same for all six packages:** both clean.
  - The six-package run finished in 12.7s with nothing printed, which is fast enough that you may want to re-run it in CI to be sure.
- **`cargo test -p remind_me_core --lib redact`, fresh build:** 13 of 13 pass.
- **Full `remind_me_core` library tests:** 527 pass, 1 fails.
- **`remind_me_core` integration tests:** 111 test binaries pass. 104 tests across 10 binaries fail, all during fixture setup: `create_dir_all` under `$HOME`, plus one `UnixListener::bind`. The sandbox blocks both, and the PR doesn't touch them.
  - This includes the one library failure (`dbs_import.rs:499`).
  - `boundary_test` (8 of 8) passes.
- **Stale build discarded:** my first test run reused an old binary from a shared `$TMPDIR/tgt` that had been built from a different `redact.rs`. I threw those results away and rebuilt in a new, empty directory.
- **Do the new tests guard anything?** I ran them against base:
  - `provider_tokens_are_redacted` and `card_numbers_…` go red, as they should.
  - `connection_strings_lose_their_password` passes, as expected, since the PR says it only pins existing behaviour.
  - `new_shapes_leave_ordinary_text_alone` passes, as expected for a test that only checks nothing is redacted.
- **Probes:** I ran them in a scratch crate using a copy of `redact.rs` whose hash matches HEAD.

**Not run:**
- `cargo test` for the other five packages (`remind_me_mcp`, `remind_me_api`, `rusty-remind-me`, `remind_me_remote`, `remind_me_hub`); these need a run outside the sandbox, with a writable `$HOME` and socket binding.
- An end-to-end crash through the REST and watcher entry points. Those effects are traced from the code; only the panic inside `redact` itself was executed.
