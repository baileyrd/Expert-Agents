<!-- remind-me/343-r2 run 4, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 307 s, 1398760 tokens, $1.27 -->

## Review of PR 343 (Rusty-Mill/rusty-remind-me): changes required

One prior finding is fixed (K1). The other (K2) still leaks a card number when the card is followed by a word that starts with a digit. Measuring the shared prefix scanner also turned up a new defect: certain inputs take quadratic time.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Reviewed head:** `9ef2ec3a6eaf1fd7fe8b6edd6da5bad0f77337bb`. The fetched `pull/343/head` matches.
- **Diff:** `crates/remind_me_core/src/redact.rs`, `README.md` and `RELEASE_NOTES.md` (commits `abdd0b76a` and `9ef2ec3a6`).

### What must hold
- **Memory content and the `redacted` tag / `metadata.redactions`:** every write path goes through `boundary::scrub`, `scrub_field` or `scrub_jsonl` before the insert (`boundary.rs:3-6`). A secret of a shape the docs list never reaches the row, the outbox or a peer. Because redaction happens before the outbox, the sync format does not change; peers just receive the already-redacted content.
- **`redact()`:** it must return for any UTF-8 input without panicking, in time roughly proportional to the input. Placeholders must cover the whole secret.
- **`project::looks_secret` (`project.rs:962`):** it now also refuses claim values, `cwd` and `git_remote` that match the new rules. It must not reject ordinary names.
- **README and release note:** they must match what the code actually catches.

No tools, schemas, sync formats, timestamps or tables change, so I ruled out those failure classes for this diff. The redaction functions are pure, so the concurrency class doesn't apply either.

### Prior findings at this head
- **K1 (P1), overlap panic in `replace()`: fixed.** `coalesce` (`redact.rs:90-100`) sorts and merges every rule's ranges before `replace` (`redact.rs:52`). I ran nested and repeated `sk-`, `glpat-`, `sk-ant-…-sk-`, `xoxb-` and `github_pat_` inputs, and none panicked. The regression test `nested_and_repeated_prefixes_redact_without_panicking` fails (red) when run against `abdd0b76`'s `redact.rs`. The fix also removes a panic that already existed at base: `"xoxb-".repeat(1000)` panics at base `redact.rs:82`.
- **K2 (P2), a card next to other digits escapes: partly fixed, still open.** See B1 below. Cards followed by an expiry date, a reference number or a second card are now caught, and the test `cards_next_to_other_numbers_are_still_redacted` fails at `abdd0b76`.

### Blocking findings

**B1 (P2): a card number followed by a space and a word starting with a digit is stored unredacted.** This is K2 in a new form.
- **Where:** `redact.rs:248-251`. The doc comment at `redact.rs:234-237` describes behaviour the code doesn't have.
- **Why it happens:** `card_numbers` gathers the whole run of space- or hyphen-joined digit groups. If the byte after the *last* group is a letter or digit, it skips `cards_in` for the entire run. So one glued trailing token disqualifies every window, including a complete card at the start of the run.
- **Scenario (executed):**
  - An agent stores `"Visa 4111111111111111 2nd attempt failed"`. The digit groups are `[16, 1]`, the group ends at `n`, and the card is stored verbatim.
  - The same happens for `"card 4111111111111111 1st try"`, `"4111 1111 1111 1111 2nd card"`, `"4111111111111111 3pm"`, `"pay 5555555555554444 2x"` and `"4111 1111 1111 1111-1a"`. All come back unchanged with no hits.
  - The row is then synced to every peer and replayed into briefs.
  - This contradicts the README (`README.md:267`) and the release note, which say payment card numbers are redacted.
- **Grade:** base stores every card, so this PR makes nothing worse than before; the harm is that the new protection it advertises has a gap. I kept K2's P2. If you grade "a secret stored" as P0 regardless of what base did, raise it.
- **Fix:** don't skip the whole run. Pass `glued = bytes.get(end).is_some_and(|&b| is_alnum(b))` into `cards_in`, and reject only windows whose last group is the run's final group when `glued` is true. Then fix the doc comment.
- **Regression test** (in `redact.rs` tests, beside `cards_next_to_other_numbers_are_still_redacted`):
  - `"card 4111111111111111 1st try"` → `"card [REDACTED:card_number] 1st try"`
  - `"4111 1111 1111 1111 2nd"` → `"[REDACTED:card_number] 2nd"`
  - Keep the existing `a_card_glued_into_a_longer_number_is_not_matched` cases green.

**B2 (P2): `prefixed()` is quadratic on repeated prefixes, so one write can tie up a thread for tens of minutes.**
- **Where:** `redact.rs:153-166`, reached through `llm_api_keys` (`:202`), `gitlab_tokens` (`:186`), `slack_tokens` and the others.
- **Why it happens:** every anchor scans `run()` to the end of the base64url run, even when the anchor lies inside a match already found. With `"sk-"` repeated, every occurrence is an anchor (each is preceded by `-`), so the work is O(n²).
- **Scenario (executed, release build):**

  | Input | 64 KB | 256 KB |
  |---|---|---|
  | `"sk-"` repeated | 0.27 s | 3.76 s |
  | `"glpat-"` repeated | 0.13 s | 2.05 s |
  | `"xoxb-"` repeated | 0.15 s | 2.37 s |
  | plain text | 0.17 ms | 0.64 ms |

  - `add_memory_with` has no length cap before `scrub` (`queries.rs:56`), and REST accepts 8 MiB bodies (`remind_me_api/src/http.rs:22`).
  - At 8 MiB, extrapolating the measured growth (32× the size, about 1,000× the time) gives roughly an hour of CPU on the caller's daemon thread. Each daemon connection runs on its own thread (`server.rs:162`).
- **Who is hurt:** the store's availability, and any client waiting on the write.
- **Is it new?** The `xoxb-` rule was already quadratic at base, but it panicked right after the scan. This PR adds new quadratic prefixes and, by fixing the panic, makes the slow path run to completion.
- **Fix:** skip any anchor that falls before the end of the previous match, as `jwts` already does with `floor` (`redact.rs:383-391`). Merge all of a rule's prefixes in sorted order so each byte is scanned once.
- **Regression test:** a structural test, not a timing one. For example, assert that `prefixed("sk-".repeat(10_000), &["sk-"], is_b64url, 32)` returns exactly one range, which shows anchors inside a match are skipped.

### Design questions
- **`sk-` with a 32-character minimum matches ordinary names (executed).**
  - `git@github.com:acme/sk-learn-feature-extraction-pipeline.git` becomes `…/[REDACTED:llm_api_key].git`.
  - `/home/me/src/sk-image-segmentation-benchmarks-2026` is redacted too.
  - Through `looks_secret`, that means a project claim or a session `cwd`/`git_remote` under such a name is refused or skipped, so the session loses its project anchor.
  - ADR-0026 accepts that pattern-based redaction is imperfect. Whether `sk-` should also require a digit or mixed case is for you to decide.
- **Cards inside JSON numbers are not redacted.** `scrub_jsonl` only redacts strings (`boundary.rs:58-75`). A card held as a JSON number in a raw session transcript (e.g. `{"card": 4111111111111111}`) would be archived verbatim. This was already the case before the PR, and ADR-0029 says "every string" is redacted.

### Optional improvements
- `google_api_keys` needs exactly 35 characters after `AIza`, so a key glued to `_`/`-` (e.g. `AIza…_old`) is missed (executed). Treating `-`/`_` after the key as a boundary would catch it.
- Three of the new tests also pass at base. Two (`new_shapes_leave_ordinary_text_alone`, `a_card_glued_into_a_longer_number_is_not_matched`) guard against false positives, and one (`connection_strings_lose_their_password`) pins existing behaviour, as the release note says. That's fine, just noting it.

### Checks run
- **Pinning:** `git fetch origin pull/343/head` gives `FETCH_HEAD` = `9ef2ec3a6…`.
- **`cargo fmt --all -- --check`:** passed.
- **`redact.rs` built on its own** (head copy in a scratch crate, only dependency `serde_json`):
  - The module's 14 unit tests pass.
  - `cargo clippy --lib --tests -- -D warnings` is clean.
  - The new APIs (`u32::is_multiple_of`, `Option::is_none_or`) fit the crate's minimum Rust version of 1.89.
- **Each new test run against older code:**
  - K1 and K2 regression tests fail at `abdd0b76`.
  - Of the PR's 7 new public-API tests run at base `d5528b1`, the 4 that test new behaviour fail and the 3 above pass.
- **Probes (executed):** the K1, K2, B1, B2, `sk-`-slug and Google-suffix cases quoted above.

### Not run
- **Workspace clippy and `cargo test --locked` over the six packages:** not run. The pinned `rusty_mill` dependencies (`libs-2026.10.0`) aren't cached, and the sandbox denied github.com. To close this gap, run both commands from the instructions somewhere with GitHub access. Of particular interest are `boundary_test.rs`, `archive_test.rs`, and the `transcript.rs`/`project.rs` tests that check `[REDACTED:…]` output.

### Verdict: **changes required** (B1, B2)
