<!-- rocket-league/999-r1 run 1, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 109 s, 499648 tokens, $0.55 -->

# PR 999 review: rl_replay slice P1 (container, CRC, header)

**Verdict: changes required.** A crafted replay of about 220 KB crashes any program that parses it (P0), and malformed names are accepted (P2).

- **Repository:** Rusty-Mill/rusty_mill, PR #999
- **Base:** `712c1c2275f6898009357365175a2498f291dadb`
- **Head:** `fd33e2baa3e8862cefdac4e670fa3475d0f2f4e2`. `git fetch origin pull/999/head` returned this same SHA.
- **Scope:** one commit adding the new crate `rl_replay` (container, two section CRCs, header property tree), a differential test against boxcars, the scope doc edit, a workspace dependency pin and the change fragment. It touches no physics, bridge or tape-bot code.

## Blocking findings

### P0: no limit on how deeply header properties nest, so a crafted replay crashes the process (executed)

- **Where:** `crates/apps/rocket_league/crates/rl_replay/src/header.rs:121-132` (`properties`), `:154` (`ArrayProperty`) and `:171-175` (`StructProperty`).
- **Cause:** `properties → property → properties` recurses once per nested `StructProperty` or `ArrayProperty` row. The only limits are the header size, which is any `u32`, and `MAX_ARRAY_LEN`, which caps how many rows an array has, not how deep they go. Each `StructProperty` level costs 35 bytes: an empty key (`len=0`), the kind name, an 8-byte size and an empty struct name (`len=0`). The attacker can compute a matching CRC, so the CRC check does not stop this.
- **What I ran:** a scratch copy of the crate with a probe test in `$TMPDIR/rlprobe` that calls the public `rl_replay::parse` on a valid container with correct CRCs, a header at version 868.27.10, and N nested `StructProperty` levels:

  | Depth | File size | Release | Debug |
  |---|---|---|---|
  | 1,000 | 44 KB | `Ok` | not run |
  | 5,000 | 220 KB | `fatal runtime error: stack overflow, aborting` (SIGABRT) | same |
  | 10k–1M | 0.44–44 MB | same abort | not run |

  Stack overflow aborts the process. It is not a panic, so `catch_unwind` cannot stop it.
- **Impact:** a 220 KB file, smaller than a normal replay, kills the process. The trigger is any `.replay` passed to P3's `RlReplayParser` or to `replay-analyzer`. The required behaviour is an `Err`.
- **Fix:** give `properties` and `property` a depth argument and add a cap (`MAX_DEPTH`). Real headers nest only a few levels, for example `PlayerStats` → row → `Struct`. Return `Error::BadLength { what: "property nesting depth", .. }` or a new `Error::TooDeep { offset }` when the cap is passed. A loop with an explicit stack would also work.
- **Regression test:** a unit test in `lib.rs` that builds 100,000 nested `StructProperty` levels, plus a second one nesting single-row `ArrayProperty`s, and asserts `parse` returns `Err`. Without the fix the test binary aborts.

### P2: names are accepted without a length of at least 1 or a final NUL (source-traced)

- **Where:** `crates/apps/rocket_league/crates/rl_replay/src/reader.rs:85-93` (`Reader::name`). `text` at `:97-115` drops the last byte the same way without checking it.
- **The gap:** the doc comment says a name is the "byte length (including a trailing NUL), then the bytes", but the code checks neither part:
  - A length of 0 decodes to `""`, and an empty key is accepted. My P0 probe relies on this.
  - Bytes `N o n e X` with length 5 decode to `"None"`, so a wrong final byte silently ends a property map.
- **Required behaviour:** reject malformed input, as the scope doc requires ("fails with a clear … error rather than a guess"). I recall that boxcars rejects a zero-length string (`ParseError::ZeroSize`), but I could not open its source here because the crate is not cached. Treat that as unverified.
- **Fix:** in `name` and `text`, return an error when the length is 0 for a name or the final unit is not NUL.
- **Tests:** byte-level unit tests for a zero-length name and for a name without a final NUL, each expecting `Err`.

## Design questions

None.

## Optional improvements

- `tests/boxcars_oracle.rs`: `a_cut_sample_fails_cleanly_at_every_header_length` uses `step_by(97)`, so it does not test every header length. Rename it, or test every cut; there are only a few thousand.
- The version gate accepts combinations the game never wrote, for example `(869, 10, None)`. That is harmless now, but a `MIN_VERSION` check that needs a net version for engine ≥ 868 would be stricter.
- A `cargo fuzz` target or a fixed corpus of mutated headers would have caught the P0 and should come before P2's bit reader.

## Checks and validation gaps

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | Passes |
| `python3 .github/scripts/changes.py check` | Passes ("307 fragment(s) ok") |
| `cargo test -p rl_replay` in the workspace | **Could not run.** The `boxcars` dev-dependency (and `bitter`) is not cached, and the sandbox has no writable cargo home or registry access. |
| Unit tests, scratch copy without the dev-dependency | 20 of 20 pass |
| `cargo clippy --all-targets -D warnings`, scratch copy with the crate's lint table | Clean |
| Probe for nesting depth | Aborts from 5,000 levels up (P0 above) |
| `check_workspace_deps.py`, workspace-map check | **Not run.** `cargo metadata --offline` fails because the same crates are missing. |
| Python tape-bot tests | Not run: this PR does not touch tape-bot. |

Gaps that need network access or the private corpus:

- **Boxcars comparison:** the change fragment says all three headers equal boxcars' decode, and that the scope-doc version floor is now 868.23.8 because `419a.replay` is that version. I could not check either claim. Run `cargo test -p rl_replay --test boxcars_oracle` with network access.
- **Private corpus:** not available here and not used by this crate yet.

All new tests come with the new crate, so none of them exist at the base. No test covers nesting depth or name framing.

**Verdict: changes required.**
