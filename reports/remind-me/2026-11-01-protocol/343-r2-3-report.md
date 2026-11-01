<!-- remind-me/343-r2 run 3, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 1927 s, 957925 tokens, $1.05 -->

## Review of PR 343 (Rusty-Mill/rusty-remind-me): changes required

- **Base:** d5528b1347054f348b3dac96f54619e603f4594c
- **Head:** 9ef2ec3a6eaf1fd7fe8b6edd6da5bad0f77337bb. I fetched it from `pull/343/head` and it matches the head you gave me.
- **Diff:** two commits (abdd0b76 adds the rules, 9ef2ec3a fixes the earlier review). Three files change: `crates/remind_me_core/src/redact.rs`, `README.md` and `RELEASE_NOTES.md`.

K1 is fixed. K2 is only partly fixed: a card number followed by a token like `2nd` or `10am` is still stored unredacted. I also found one new leak, so the verdict is **changes required**.

### What the diff touches, and the rule each part must keep
- **The `redact()` / `guard()` result.** This is the one place every write path removes secrets: `add_memory`, both capture halves, decompose, the importers, `scrub_field` for titles, and `scrub_jsonl` for the session archive (`boundary.rs:19-76`). ADR-0026 §7 says secrets are replaced before anything is stored. ADR-0029 adds that this holds on every path, recapture included. Whatever is redacted here is also what the outbox sends and what peers and the hub hold.
- **The rule this PR must keep:** every shape the release note says is covered gets fully replaced. No fragment of the secret survives, and redaction never panics. A panic here fails the write.
- **Not affected:** sync, the wire format, the schema and tools are untouched. Redacted text goes through sync unchanged, so the peer and wire-format checks don't apply.

### Prior findings at this head
- **K1 (P1), overlapping prefixed matches panic in `replace()`: fixed.** `coalesce` (`redact.rs:90-100`) now sorts and merges ranges before `replace`, for every rule.
  - At abdd0b76, `x sk-<32a>-sk-<32b> y` panicked ("byte range starts at 73 but ends at 38"). At head it redacts to one marker.
  - Guarding tests: `nested_and_repeated_prefixes_redact_without_panicking` and `coalesce_merges_nested_overlapping_and_touching_ranges`.
- **K2 (P2), a card next to other digits escapes redaction: partly fixed, still open (see B1).** `4111111111111111 12/30` leaked at abdd0b76 and is redacted at head. The same class still leaks when the trailing token mixes digits and letters.

### Blocking findings

**B1 (P2): K2 still open. A card is skipped when the last number after it runs into letters.**
- **Where:** `redact.rs:247-250`.
- **Cause:** `card_numbers` builds the whole run of digit groups joined by spaces or hyphens. It then throws away every card in the run if the run's last group runs straight into a letter. The check `bytes.get(end).is_none_or(|&b| !is_alnum(b))` applies to the whole run, not just the last group.
- **Trace (executed in a scratch crate built from the head `redact.rs`):** each of these inputs comes back unchanged with no hits:
  - `4111111111111111 2nd try`
  - `4111 1111 1111 1111 10am`
  - `card 4111-1111-1111-1111-3x`
  - `4111111111111111 1 2nd`

  A user who writes "paid with 4111 1111 1111 1111 2nd time" gets the full card number stored in the memory. It then goes into the outbox, the hub and every peer, and is replayed into agents' prompts. Meanwhile the release note says card numbers "optionally grouped by spaces or hyphens" are redacted.
- **Grade:** P2, carried over from K2. The base redacts no cards at all, so nothing regresses, but the claimed coverage is wrong.
- **Fix:** when the run's last group is glued to a letter, drop only that group. Still pass the remaining groups to `cards_in`.
- **Regression test:** add to `cards_next_to_other_numbers_are_still_redacted`:
  - `("4111111111111111 2nd try", "[REDACTED:card_number] 2nd try")`
  - `("4111 1111 1111 1111 10am", "[REDACTED:card_number] 10am")`

  Keep `4111111111111111y` and `41111111111111119` unmatched. Both new cases fail at head today.

**B2 (P2): two glued tokens of a new letters-and-digits-only rule leave the second token's body in clear.**
- **Where:** `redact.rs:124-129` (`anchors`) and `redact.rs:153-166` (`prefixed`). The comment at `redact.rs:85-89` says merging covers nested matches "without leaking either"; that is now false.
- **Cause:** the `npm_`, Stripe `sk_live_`/`sk_test_`/`rk_*` and `whsec_` rules take only letters and digits as the token body.
  - The first token's body run swallows the next prefix's letters and stops at its `_`.
  - The second prefix is then dropped by `anchors`, because a letter or digit comes right before it.
  - So the second token's body never matches any rule.
- **Trace (executed):**
  - `npm_<36a>npm_<36b>` → `[REDACTED:npm_token]_bbbb…` (36 secret characters stored)
  - `whsec_<32a>whsec_<32b>` → `[REDACTED:stripe_key]_bbbb…`
  - `sk_live_<32a>sk_live_<32b>` → `[REDACTED:stripe_key]_live_bbbb…`

  It takes two credentials pasted with no separator, for example a dump whose newlines were lost. The second secret's body is then stored and synced.
- **Already at base:** `ghp_` behaves the same way; I reproduced it there, so it isn't new. The PR copies the pattern into four new rules and adds a test and comment claiming glued pairs are covered. That test only exercises `sk-`, which works because `-` counts as part of the body.
- **Fix:** in `prefixed`, a prefix that appears inside an earlier match must still count as a match and extend that match, even though a letter or digit precedes it. Coalescing then covers both tokens. An alternative is to stop each body at the next occurrence of its own prefix.
- **Regression test:** for `npm_`, `whsec_` and `sk_live_`, `format!("x {p}{a}{p}{b} y")` must not contain `bbbb`. Extend `nested_and_repeated_prefixes_redact_without_panicking` with these; it fails at head today.

### Other failure classes checked
- **Half a change, wire versus row, concurrency, retrieval, who is calling, protocol subset:** not applicable. The change is one pure function, applied before the insert, and sync carries its output.
- **Boundary skipped:** every writer goes through `boundary.rs`, which calls `redact`. `scrub_jsonl` applies the new rules to every string in the session archive; JSON numbers aren't redacted.
- **Panic paths:** with `coalesce` in place, `replace` always gets sorted ranges that don't overlap. The index arithmetic in `aws_keys`, `google_api_keys` and `prefixed` stays within the text, because each starts at a needle that was found.
- **Rules interacting:**
  - A later rule can't match inside an earlier `[REDACTED:…]` marker. Keywords like `api_key]` and `token]` are followed by `]`, not `:` or `=`.
  - `password=<card>` is caught by the card rule, and the generic rule then skips the marker.
- **Status that lies:** `hits` and `metadata.redactions` list exactly the rules that fired.
- **Docs:** the README and RELEASE_NOTES entries match the code, apart from the coverage claims that B1 and B2 make false. ADR-0026 accepts that redaction misses secrets with no recognisable shape; B1 and B2 are shapes the rules say they cover.

### Design questions
- **The `llm_api_key` rule removes ordinary paths and slugs.** The floor of 32 characters after `sk-`, with `-` allowed in the body, means `see src/sk-very-long-module-name-for-the-thing.rs` is stored as `see src/[REDACTED:llm_api_key].rs` (executed). Redaction can't be undone, so the memory loses that path for good.
  - Should the rule require at least one digit?
  - Or should it require a known form like `sk-proj-` / `sk-ant-`, or a body with no long runs of lowercase words?

### Optional improvements
- `card_shaped` accepts any 13–19-digit run with no separators. That's fine for now; just note that a 19-digit Visa-shaped number can also swallow a short group that follows it (more is redacted than needed, nothing leaks).

### Checks
- **`cargo fmt --all -- --check`:** passed.
- **`cargo clippy --locked … --all-targets -- -D warnings`** over the six packages: exit 0. It printed two `{{plugin-name}}` manifest errors from a template `Cargo.toml` inside the rusty_mill git checkout; they don't fail the run.
- **`cargo test --locked --no-fail-fast`** over the six packages, with `HOME` pointed at a scratch directory: 2483 passed, 3 failed. All 17 redact-related unit tests pass.
  - The 3 failures are all in `remind_me_core --test peer_discovery_test`, which fails on `UnixListener::bind` with "Operation not permitted". The sandbox blocks that socket, and this PR doesn't touch the peer code.
  - With the real `HOME`, `remind_me_api --test import_export_test` fails with "Read-only file system" because it writes under `$HOME`. It passes with the scratch `HOME`.
- **Probes against the redaction code at base, abdd0b76 and head:**
  - K1 panics at abdd0b76 and is fixed at head.
  - K2's `12/30` case leaks at abdd0b76 and is fixed at head.
  - B1 and B2 reproduce at head.
  - The `ghp_` form of B2 also reproduces at base.
- **New tests at the base:** I didn't run the PR's new tests against the base. Except `coalesce_merges_…`, they assert redaction of shapes the base doesn't handle and would fail there; `coalesce_merges_…` calls a function the base lacks and wouldn't compile. The K1 and K2 probes against abdd0b76 show the two fix tests go red without the fix.

### Validation gaps
- `peer_discovery_test` needs Unix-socket bind permission. Rerun it outside the sandbox to confirm it passes; this PR doesn't change that code.
- There is no Postgres here, but the hub store isn't affected by this diff.

### Verdict
**Changes required.** B1 (the remaining part of K2) and B2 must be fixed, each with the regression test named above.
