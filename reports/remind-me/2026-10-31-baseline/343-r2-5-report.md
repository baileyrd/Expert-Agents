<!-- remind-me/343-r2 run 5, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 184 s, 844698 tokens, $0.86 -->

# Review of Rusty-Mill/rusty-remind-me PR 343: changes required

K1 is fixed. K2 is only partly fixed: a card number still goes into storage unredacted when a later number in the same run is glued to letters ("…1111 2nd try"). I ran that case.

- **Repository:** Rusty-Mill/rusty-remind-me, PR 343
- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Reviewed head:** `9ef2ec3a6eaf1fd7fe8b6edd6da5bad0f77337bb`. The fetched `pull/343/head` and `HEAD` both resolve to this SHA.
- **Diff:** `crates/remind_me_core/src/redact.rs` (+368/−13), `README.md`, `RELEASE_NOTES.md`. Two commits: `abdd0b76` and the fix commit `9ef2ec3a`.

## Prior findings at this head

- **K1 (P1), overlapping prefixed-token matches panic in `replace()`: fixed.** `redact()` now runs each rule's ranges through `coalesce()` (`redact.rs:52`, `:90`), which sorts them and merges any that overlap or touch. I ran these inputs: `sk-…-sk-…-sk-…`, `glpat-…glpat-…`, `ghp_…-github_pat_…` and `API_KEY=sk-…`. Each was fully redacted and none panicked. When I ran the new test `nested_and_repeated_prefixes_redact_without_panicking` against `abdd0b76`'s `redact.rs`, it panicked at the old `replace` slice. So the test guards the fix.
- **K2 (P2), a card number next to other digits escapes redaction: the listed cases are fixed, one variant is still open (F1).** The new `digit_groups` and `cards_in` code handles cards next to plain digit groups. `cards_next_to_other_numbers_are_still_redacted` and `a_card_glued_into_a_longer_number_is_not_matched` both fail at `abdd0b76` and pass here.

## Blocking findings

### F1 (P2): a trailing digit group glued to letters stops the whole card from being redacted
- **Where:** `crates/remind_me_core/src/redact.rs:249`
- **Trace (executed, standalone build of the head `redact.rs`):**

| Input | Result |
|---|---|
| `"4111 1111 1111 1111 2nd try"` | unchanged, `hits=[]` |
| `"4111111111111111 3x"` | unchanged |
| `"charged 4111-1111-1111-1111 10am"` | unchanged |

- **Cause:** `digit_groups` joins `2`/`3`/`10` onto the card as one more group. Then the guard `bytes.get(end).is_none_or(|&b| !is_alnum(b))` sees the letter after that last group and throws away the whole run, card included.
- **Who is hurt:** a person whose card number is stored in plain text, and every agent and peer that later reads or syncs that memory. The release note's claim that card numbers are redacted is false for these inputs.
- **Why P2:** same grade as K2. The base redacted no cards at all, so this is an incomplete new feature rather than a regression.
- **Fix:** when the run is followed by an alphanumeric, drop only the glued last group and still pass the remaining groups to `cards_in`. Only when the run is a single group should the whole run be skipped.
- **Regression test:** add the three inputs above to `cards_next_to_other_numbers_are_still_redacted`. Each should expect `[REDACTED:card_number] <suffix>`. `x4111111111111111` and `41111111111111119` should still not match.

### F2 (P2): a card stored as a JSON number survives the session archive scrub
- **Where:** `crates/remind_me_core/src/boundary.rs:60–74`
- **Trace (source-traced; `remind_me_core` would not build, see Checks):**
  1. `scrub_jsonl` → `scrub_line` parses each line and calls `redact_strings`.
  2. `redact_strings` only redacts `Value::String`. Numbers fall to `_ => false`.
  3. So a tool-call input like `{"pan":4111111111111111}` in a transcript line is kept byte for byte in the session archive. It is then served by `remind_me_source`.
- **Who is hurt:** the same people as F1. Before this PR no write path redacted cards. This PR documents card redaction on write (README.md:267), and the archive path skips it. That goes against ADR-0029 §1, which says "Write-time redaction holds on every path".
- **Fix:** in `redact_strings`, also check `Value::Number` values that are 13–19 digit integers with `card_numbers` on their decimal form, and replace a match with the redaction string.
- **Regression test:** a `boundary.rs` unit test where `scrub_jsonl` on that line returns valid JSON with `"[REDACTED:card_number]"` in place of the number.

## Failure classes ruled out

- **Half a change, concurrency, wire versus row, retrieval admission:** not applicable. The change is a pure function on text, run before the insert. No table, outbox, sync field, embedder or tool schema changes.
- **Boundary skipped:** every write path calls `boundary::scrub`, `scrub_field` or `scrub_jsonl`, so the new rules apply everywhere except F2. `project::looks_secret` (claim values, session `cwd`, `git_remote`) now also refuses values shaped like the new tokens or cards; that is intended.
- **Panic or char-boundary problems:** every new rule anchors on ASCII and matches ASCII runs. Every `run(bytes, i + prefix.len(), …)` starts at or before the end of the text. `card_numbers` always has at least one group.
- **Rules interacting:** a replacement string can't feed a later rule. `[REDACTED:llm_api_key]` contains `api_key`, but it is followed by `]`, not `:` or `=`. The existing `API_KEY=sk-live-12345678` case stays `secret` (it is below the 32-character floor). A long `API_KEY=sk-…` becomes `llm_api_key`, and the generic rule then skips it because it starts with `[REDACTED:`.
- **Time as text:** the probes confirm millisecond, microsecond and nanosecond timestamps start with `1`, which is not a card network prefix, so they are not matched.

## Design questions

- Cards written with a double space, dots or tabs between groups (`4111  1111 1111 1111`, `4111.1111.1111.1111`) and cards glued to a word (`cc4111…`) are not redacted. The code says this is deliberate ("single ` ` or `-`"). The release note's "grouped by spaces or hyphens" reads broader than that. The owner should decide whether to widen the rule or tighten the note.

## Optional improvements

- `cards_in` takes the longest valid card starting from the earliest group. If a made-up window starting one group early passes Luhn, it is redacted, and the real card's last group is left in plain text. This needs a 1-in-10 Luhn coincidence and leaks at most four digits.

## Checks run

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | passed, no output |
| `rustfmt --check` on `redact.rs` | passed |
| `clippy-driver -D warnings` on the head `redact.rs` (library part, `serde_json`/`guard` stubbed out) | passed |
| `rustc --test` on the head `redact.rs` (minus the two `guard` tests) plus my probe module | 13/13 passed; probe output as above |
| The fix commit's three regression tests against `abdd0b76`'s `redact.rs` | all 3 fail, so they guard K1 and K2 |
| Toolchain | 1.98.1 per `rust-toolchain.toml`, so `is_none_or` and `is_multiple_of` are fine; `is_multiple_of` is already used elsewhere |

## Validation gaps

The full workspace checks did not run. Cargo couldn't fetch the pinned `rusty_mill` git dependency (`libs-2026.10.0`) because `~/.cargo` is read-only and it wasn't cached. So these were not run:
- `cargo clippy --locked …` over the six packages
- `cargo test --locked` over the six packages
- the `guard_*` unit tests
- `boundary.rs` and `transcript.rs` tests

To close the gap, run both commands in an environment with that dependency cached or a writable `CARGO_HOME`.

The probes and tests ran in scratch copies under `$TMPDIR`; nothing in the repo was changed.

**Verdict: changes required** (F1 and F2, both P2).
