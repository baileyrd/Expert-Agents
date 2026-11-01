<!-- remind-me/343-r1 run 5, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 989 s, 978391 tokens, $0.97 -->

# Review of Rusty-Mill/rusty-remind-me PR #343: two blocking findings, changes required

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head reviewed:** `abdd0b76add2f3c5cf61cb1533f225405d53f931`. `git fetch origin pull/343/head` returned the same SHA.
- **Diff:** one commit, three files: `crates/remind_me_core/src/redact.rs`, `README.md` and `RELEASE_NOTES.md`.

The new rules mostly work. But the card rule misses a card whenever another number follows it, and the shared token helper can panic on input that trips the new rules.

## What the diff touches, and the rule each part must keep
- **`redact::redact` / `guard`.** Every write path calls them through `boundary::scrub`, `scrub_field` and `scrub_jsonl` (`capture.rs`, `queries.rs:56`, `importer.rs:1412`, `project.rs`, `project_backfill.rs`, `project_proposals.rs`, `transcript.rs`).
  - **Rule:** every recognisable secret is replaced before anything is stored (ADR-0026; ADR-0029 §"Write-time redaction holds on every path").
  - **Rule:** redaction is total. It must never fail or panic, because it sits in front of every insert.
  - **Rule:** each rule's ranges are sorted and don't overlap, because `replace` (`redact.rs:85`) assumes it.
- **`project::looks_secret`** (`project.rs:962`) refuses a claim or session value whenever the scrubber would change it. More rules therefore means more refusals. That is noted below as a design question, not a defect.
- **Out of scope:** no stored record, outbox entry, sync format, tool schema or caller identity changes. Supersession, merges and peer state are untouched. Every node stores whatever its own write path produced.

## Blocking findings

### 1. P0: a card number followed by another number is stored unredacted, despite the release note
- **Where:** `redact.rs:225` and `redact.rs:241-249`. `digit_span_end` keeps extending the span greedily across any single space or hyphen followed by a digit. `is_card` then judges the whole span, and `i = end` (`:235`) skips past it. A real card number is never tested on its own once a neighbouring number has been joined to it.
- **Scenario (executed in a scratch probe):** `redact()` left all of the following unchanged:
  - `"card 4111111111111111 12/30 cvv 123"` (18 digits joined)
  - `"4242 4242 4242 4242 123"` (card plus CVV)
  - `"card: 4111111111111111 123"`
  - `"qty 2 4111111111111111"`
  - `"card 4111-1111-1111-1111-2"`
- **Who is hurt:** a pasted card, expiry and CVV is the most common shape a card takes in a transcript. It is stored, synced to every peer and replayed into agent prompts. The release note says payment card numbers are now caught, "optionally grouped by spaces or hyphens". Base also stored these, so this is not a regression, but it is graded by impact.
- **Why the tests miss it:** the PR's own test always puts a comma straight after the card (`"card {card}, exp 12/30"`, `redact.rs:~526`), so this case is never exercised.
- **Fix:** split the span into its groups. Test each contiguous sequence of groups that adds up to 13–19 digits, or at least the leading and trailing windows. Alternatively, stop joining a group once a valid card has been reached.
- **Regression test:** add the five strings above to `card_numbers_with_a_network_prefix_and_luhn_are_redacted`, each expected to come out as `[REDACTED:card_number]` with its neighbours kept.

### 2. P1: overlapping matches panic in `replace` on a write path
- **Where:** `prefixed` (`redact.rs:136-149`) and its new callers:
  - `llm_api_keys` (`:185`, prefix `sk-` with an `is_b64url` body)
  - `gitlab_tokens` (`:169`)
  - `pypi_tokens` (`:200`)
  - `github_tokens` (`:155-166`), which merges `github_pat_` with an `is_word` body and `ghp_`
- **Cause:** a body that may contain `-` or `_` can swallow a second prefix. `anchors` accepts that second prefix because `-` and `_` are not alphanumeric. The result is two ranges with the same end, `(i1,e)` and `(i2,e)` where `i2 < e`. `replace` then slices `text[e..i2]` and panics (`redact.rs:89`).
- **Scenario (executed):** each of these panicked with `byte range starts at 87 but ends at 44` (or similar):
  - `redact("sk-<40 chars>-sk-<40 chars>")`
  - `"github_pat_<60>_ghp_<36>"`
  - `"glpat-<20>_glpat-<20>"`
- **Base already had this for Slack.** `xoxb-…-xoxb-…` panics at base too; the PR moved Slack into the same helper. The PR adds the far more common `sk-` and three more prefixes, which makes the panic reachable from much more ordinary input.
- **Effect (source-traced):**
  - Any `add_memory`, capture or import holding such a string panics instead of writing.
  - MCP turns the panic into an error (`remind_me_mcp/src/lib.rs:3632`). The CLI, REST and import paths have no `catch_unwind`.
  - Session capture (`transcript.rs:482`, then `scrub_jsonl` at `:336`) panics on the dialog. Every later recapture of that session panics again, so its later turns are never stored.
  - Claim writes through `looks_secret` panic as well.
- **Fix:** after `sort_unstable` in `prefixed` (and in `github_tokens` after `extend`), merge or drop overlapping ranges. Do the same in `replace` defensively, e.g. skip a range with `start < at`, as `generic_secrets` already does with its `floor`.
- **Regression test:** `redact` on `sk-`, `glpat-`, `github_pat_…_ghp_` and `xoxb-` doubles must return a single `[REDACTED:<kind>]` without panicking.

## Design questions (not defects)
- **Luhn-valid ids get redacted.** Any Luhn-valid 16-digit id starting with 4 or 51–55 now becomes `[REDACTED:card_number]`. My probe hit `"order 4000056655665556"`. About 1 in 10 such ids passes Luhn. The id is lost from the memory, and `looks_secret` refuses claims whose value contains it.
- **Long kebab slugs starting `sk-` get redacted.** A slug of 32 or more characters, such as a branch `feature/sk-…`, becomes `llm_api_key`. Session `cwd`/`git_remote` values containing one are then skipped. The owner should decide whether that trade is wanted.

## Optional improvements
- `github_tokens`' comment says 82 characters, but the floor is 60. Saying why would help.
- `google_api_keys` needs exactly 35 characters, so a key followed directly by `-` or `_` (as in a filename) is missed.

## Failure classes checked
- **Wire vs row, concurrency, time as text, retrieval, tool surface, caller identity, protocol subset:** ruled out. The diff is a pure string function with no new fields, I/O, threads or tools.
- **Hidden rows:** not applicable.
- **Boundary skipped:** the scrub call sites are unchanged. Redaction is not retroactive, and the PR does not claim it is.
- **Silent success:** `guard` still tags `redacted` and records `metadata.redactions`.
- **Half a change:** only through finding 2 (a capture that panics partway).

## Tests
- **Red at base:** I ran the PR's four new tests against the base code. `provider_tokens_are_redacted` and `card_numbers_with_a_network_prefix_and_luhn_are_redacted` fail there, so they guard the change.
- **Green at base:** `connection_strings_lose_their_password` passes at base, as the release note says. `new_shapes_leave_ordinary_text_alone` is a false-positive guard, so it passes at base by design.
- **Missing:** no test covers overlap or a card next to another number (findings 1 and 2).

## Checks performed
- `cargo fmt --all -- --check`: **pass**.
- `cargo clippy --locked -p remind_me_core -p remind_me_mcp -p remind_me_api -p rusty-remind-me -p remind_me_remote -p remind_me_hub --all-targets -- -D warnings` (offline, scratch target dir): **pass**.
- `cargo test -p remind_me_core --lib redact`: **13 passed**.
- `cargo test --locked --no-fail-fast` over the six packages: **2371 passed, 111 failed**. The failure output has 108 `ReadOnlyFilesystem` and 3 `PermissionDenied` errors. I checked samples (the `import_export_test.rs:13` group and core's `dbs_import.rs:499`): they come from tests writing under `$HOME`, which the sandbox mounts read-only. I didn't check every one; none of the failing tests I saw exercises the changed code.
- **Probes** (scratch copy outside the tree): the overlap panics and the card misses quoted above.

## Validation gaps
- The 111 tests that write under `$HOME` need a writable home to run. I expect them to pass but have not seen it.
- I didn't run the hub's Postgres store; there is no Postgres here, and the diff doesn't touch it.

## Verdict: **changes required**
