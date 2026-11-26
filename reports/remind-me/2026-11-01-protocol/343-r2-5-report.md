<!-- remind-me/343-r2 run 5, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 637 s, 1052339 tokens, $1.05 -->

K1 is fixed, but K2 is only partly fixed: a card number followed by a short digit-and-letter word like "3x" or "2nd" is still stored unredacted. Verdict: **changes required**.

**Pinned:** Rusty-Mill/rusty-remind-me PR 343. Base `d5528b1347054f348b3dac96f54619e603f4594c`, head `9ef2ec3a6eaf1fd7fe8b6edd6da5bad0f77337bb`. The fetched PR head matches the head SHA. Two commits: abdd0b76 adds the rules, 9ef2ec3a fixes K1 and K2. The PR changes only `redact.rs`, the README and RELEASE_NOTES.

## Earlier findings at this head
- **K1 (P1): fixed (executed).** Every rule's matches now pass through `coalesce` (`redact.rs:52`, `:90-100`) before `replace`, so they reach it sorted with overlaps merged. I fuzzed 300,000 inputs built from every prefix, separator, marker and multi-byte character, with no panics. The regression test `nested_and_repeated_prefixes_redact_without_panicking` fails against the abdd0b76 code and passes at head.
- **K2 (P2): partly fixed.** The cases in the tests now work: `4111111111111111 12/30`, `… 12 30`, `ref 2026 4111 1111 1111 1111` and two cards side by side. One variant still gets through (B1 below).

## Blocking findings

**B1 (P2): a card followed by a group that runs into letters is not redacted.** `redact.rs:247-252`
- **Scenario (executed in the scratch copy):** an agent stores "tried 4111111111111111 3x, declined", "card 4111 1111 1111 1111 2nd attempt", "4111111111111111 12pm" or "pay 5555555555554444 1st". The text is stored unchanged with no hits.
- **Cause:** `digit_groups` treats the trailing `3`, `2`, `12` or `1` as one more group of the same run. That group runs straight into a letter, so the check at `:249` throws away every window of the run, including the valid card window at the start.
- **Impact:** the card number is stored in plain text, synced to every peer and read back into prompts. The release note's card claim is false for these inputs.
- **Grade:** P2, the same as K2, because it is the same class of miss and nothing is worse than at base, where no card was redacted. Graded on impact alone, a stored card number would be P0 under "a secret stored"; the owner may want to raise it.
- **Correction:** when the byte after the run is a letter or digit, drop only the last group and still look for cards in the rest. "4111111111111111y" must still not match.
- **Regression test:** add these to `cards_next_to_other_numbers_are_still_redacted`:
  - `"tried 4111111111111111 3x, declined"` → `"tried [REDACTED:card_number] 3x, declined"`
  - `"4111 1111 1111 1111 2nd"`
  - `"5555555555554444 1st"`

  They go red at head today.

**B2 (P2): the release note overstates the separators.** `RELEASE_NOTES.md:8` says cards may be "optionally grouped by spaces or hyphens". The code accepts only a single space or hyphen (the doc comment at `redact.rs:230` says so). `4111  1111  1111  1111` with double spaces, tab-separated groups and `4111.1111.1111.1111` all pass through unredacted (executed).
- **Correction:** say "single spaces or hyphens", or accept those separators and add a test for each.

## Design questions
- A card glued to letters (`PAN4111111111111111`, `x4111…`) is deliberately left alone, and a test pins that. It keeps ids out, but a card written as "cc4111…" is stored as is.
- `looks_secret` (`project.rs:962`) uses the same rules to refuse claim values, session `cwd` and `git_remote`. Every new rule widens what those paths refuse. The refusal is explicit, so it is not a silent failure.

## Optional improvements
- The `sk-` rule redacts long kebab-case names. `sk-learn-compatible-transformer-pipeline-config` became `[REDACTED:llm_api_key]` (executed). That loses the content, and the same name would be refused as a `cwd` or remote.
- The AWS rule runs before the `sk-` rule. A key containing `-AKIA<16 upper-case letters or digits>` gets split: the AWS rule redacts the middle and the rest of the key escapes (executed). Real keys rarely contain that; running the long vendor-prefix rules first would close it.
- Already true before this PR: `url_credentials` reads a `[REDACTED:…]` marker that follows `://` as `user:pass`, so redacting a second time changes the output. Fuzzing found no repeat-redaction differences caused by the new rules.

## Failure classes checked
- **Wire versus row:** the new kind names are plain strings in `metadata.redactions`, and nothing decodes them against a fixed list (searched the workspace).
- **Boundary skipped:** every write path still goes through `boundary::scrub` or `redact`.
- **Not applicable:** the PR touches no transaction, tool schema, retrieval stage, concurrency, auth, timestamp or hand-rolled protocol code.

## Checks
- `cargo fmt --all -- --check`: passed.
- `cargo clippy --locked` on the six packages, `--all-targets -D warnings`: passed. I ran it with `--offline` and a scratch target directory, because the default target path is read-only here.
- `cargo test --locked -p remind_me_core --lib redact`: 17 passed.
- Full `cargo test --locked --no-fail-fast` on the six packages, with a scratch `HOME`:
  - 166 test binaries passed.
  - 3 `peer_discovery_test` tests failed because the sandbox refuses `UnixListener::bind`. That is unrelated to this diff.
  - On the first run, `import_export_test` failed because `HOME` was read-only; it passed with the scratch `HOME`.
- I ran the head's tests against the abdd0b76 code. The three tests added by the fix commit fail there, so they do guard K1 and K2. The other new tests can't compile at base because the functions they call don't exist there.

**Validation gaps:**
- The peer-discovery tests need a sandbox that allows Unix sockets.
- I couldn't check the rules against current provider token formats here. For example, newer GitLab tokens may contain `.` and `whsec_` secrets may contain `+` or `/`, which would cut the match short. Checking needs real sample tokens from each provider's documentation.
- The Postgres hub wasn't run; the PR changes nothing on the wire.

## Verdict
**Changes required** (B1, B2).
