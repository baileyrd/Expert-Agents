<!-- remind-me/343-r2 run 1, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 156 s, 561819 tokens, $0.64 -->

# PR review: Rusty-Mill/rusty-remind-me #343

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Reviewed head:** `9ef2ec3a6eaf1fd7fe8b6edd6da5bad0f77337bb` (`git fetch origin pull/343/head` gives the same SHA)
- **Commits:** `abdd0b76a` (new secret shapes), `9ef2ec3a6` (fix for K1 and K2)
- **Files changed:** `crates/remind_me_core/src/redact.rs`, `README.md`, `RELEASE_NOTES.md`

**Verdict: changes required.** The K1 panic is fixed. K2 is only partly fixed, and the card rule's comment and docs claim more than the code does.

## Re-check of the earlier findings
- **K1 (P1, a panic in `replace()` when prefixed-token matches overlap): fixed.** `coalesce` (redact.rs:90) sorts each rule's ranges and merges any that overlap or touch before `replace` runs. Each rule runs on the output of the one before, so ranges from different rules can't overlap. In my scratch copy, 300,000 fuzzed inputs mixing every prefix with digits, `é`, `token=`, `eyJ` and URL credentials ran without a panic. `nested_and_repeated_prefixes_redact_without_panicking` fails against abdd0b76 (panic at `redact.rs:89`), so the test guards the fix. *Executed.*
- **K2 (P2, a card next to other digits escapes): partly fixed.** It is still open as B1 below.

## Blocking findings

### B1 (P2). A card number followed by a group of digits glued to letters is stored unredacted (K2 still open)
- **Where:** `crates/remind_me_core/src/redact.rs:249`
- **Cause:** `card_numbers` collects the whole run of separator-joined digit groups. If the byte after the **last** group is a letter or digit, it drops the **entire** run, including a valid card at the front. The guard should only stop the last group from being part of a match.
- **Executed traces** (scratch copy of the head `redact.rs`):
  - `"card 4111111111111111 3rd attempt"` → unchanged, hits `[]`
  - `"4111111111111111 1st"` → unchanged
  - `"4111 1111 1111 1111 2x"` → unchanged
  - `"paid 4111-1111-1111-1111-2FA"` → unchanged
- **Who is hurt:** a person whose card number an agent pastes with a note like "3rd attempt" or "2FA". The number is stored in clear, read back into prompts and synced to every peer and the hub.
- **Why P2 and not higher:** before this PR no card number was redacted, so nothing leaks that didn't already. The defect is a new rule that misses the very case it was fixed for.
- **Fix:** when the last group is glued, check the windows of every group except that one. Change the `cards_in` call to something like `let usable = if glued { &groups[..groups.len()-1] } else { &groups[..] }`.
- **Regression test:** add these cases to `cards_next_to_other_numbers_are_still_redacted`:
  - `("card 4111111111111111 3rd", "card [REDACTED:card_number] 3rd")`
  - `("4111-1111-1111-1111-2FA", "[REDACTED:card_number]-2FA")`
  
  Keep `"4111111111111111y"` and `"41111111111111119"` in the "glued, not matched" test.

### B2 (P2). The card rule claims Diners support it doesn't have, and the docs say "payment card numbers" without the limits
- **Where:** `redact.rs:305` (doc comment for `card_shaped`) and `redact.rs:325` (`is_card`); `README.md:267`; `RELEASE_NOTES.md:8`
- **Cause:** the comment says `card_shaped` accepts "the Amex/Diners `4-6-5`/`4-6-4`". But `is_card` only accepts a leading 3 as `34|37` with 15 digits, so a 14-digit Diners number fails. JCB (`35`) and UnionPay (`62`) aren't networks the code knows either.
- **Executed traces:**
  - `"diners 30569309025904"` (a standard Diners test number) → unchanged
  - `"diners 3056 930902 5904"` → unchanged
  - `"jcb 3530111333300000"` → unchanged
  - `"unionpay 6200000000000005"` → unchanged
- **Who is hurt:** a person or agent who trusts the README ("payment card numbers") and the comment, and pastes such a card.
- **Fix:** either add Diners (`300–305`, `36`, `38–39`, 14 digits), JCB (`3528–3589`, 16–19 digits) and UnionPay (`62`, 16–19 digits), or correct the comment, README and release note to list the networks actually covered.
- **Regression test:** add the Diners and JCB numbers above to `card_numbers_with_a_network_prefix_and_luhn_are_redacted`, or, if the scope is narrowed instead, to a test that pins which networks are out of scope.

## Design questions
- **The `sk-` rule can destroy ordinary hyphenated text.** `llm_api_keys` (`redact.rs:201`) matches `sk-` followed by 32 or more characters from `[A-Za-z0-9_-]`, and hyphens are allowed. So `"branch sk-1234-fix-the-login-redirect-loop-on-safari"` becomes `"branch [REDACTED:llm_api_key]"` (*executed*). Redaction can't be undone, so the slug is lost from the memory. The doc comment says the length floor spares "prose", and it doesn't spare hyphenated slugs. One option is to require at least one long unbroken run of letters and digits after `sk-`. This is a precision trade-off for the owner to decide.
- **Card false positives.** Any 16-digit number starting with 4 has about a 1 in 10 chance of passing Luhn, so some order or trace ids will be redacted. That is the accepted design in the release note, but it is worth knowing.

## Optional improvements
- `card_shaped` accepts `[16, 1..=4]` and similar shapes because `head.iter().all(|&n| n == 4)` is only applied when there is more than one group. That's harmless here, but a single-group check would make the intent clearer.

## Other failure classes
- **Boundary skipped:** every write path reaches the redactor through `boundary.rs:20/31/49/61`. The PR doesn't change any path, so the new rules apply everywhere the old ones did.
- **Ordering between rules:** placeholders such as `[REDACTED:google_api_key]` contain `api_key` followed by `]`, so the generic rule doesn't fire on them. A card inside `password: <pan>` is taken by the card rule first, and the generic rule then skips it via its `[REDACTED:` check.
- **Not touched by this PR:** wire format, sync, tools, concurrency, timestamps and retrieval are all unchanged. The diff is one pure function plus docs.
- **Release notes:** an entry is present (`RELEASE_NOTES.md:5`); it is inaccurate as described in B2.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | **passed** (exit 0) |
| `cargo test --locked -p remind_me_core` | **could not run**: the git dependency `rusty_mill@libs-2026.10.0` can't be fetched (`~/.cargo` is read-only and there's no cached git db, `--offline` fails) |
| `cargo clippy …` over the six packages | **could not run**, same reason |
| `redact.rs` alone in a scratch crate (head file, serde_json from the local cache) | all 14 module tests **pass**; `cargo clippy --all-targets -D warnings` **clean** |
| New tests against abdd0b76 | `nested_and_repeated…`, `cards_next_to_other_numbers…` and `a_card_glued…` all **fail**, so they guard the fix. At base `d5528b13` the new tests don't compile because the functions don't exist |
| Fuzz, 300k inputs | no panic |

**Validation gaps:** I couldn't run the workspace tests, the six-package clippy, or the boundary integration tests that call `guard`. Running `cargo test --locked -p remind_me_core` with access to the `rusty_mill` git tag would settle them.

**Verdict: changes required** (B1, B2).
