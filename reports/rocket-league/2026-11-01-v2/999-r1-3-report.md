<!-- rocket-league/999-r1 run 3, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 147 s, 708739 tokens, $0.83 -->

I found three problems, so the verdict is **changes required**. The worst one is that a crafted replay of about 440 KB crashes the whole process instead of returning an error.

**Repository:** Rusty-Mill/rusty_mill, PR 999
**Base:** `712c1c2275f6898009357365175a2498f291dadb`
**Reviewed head:** `fd33e2baa3e8862cefdac4e670fa3475d0f2f4e2`. I fetched `pull/999/head`, it resolves to the same SHA, and the PR has one commit.

## Blocking findings

### 1. P0: a crafted header crashes the process through unbounded recursion
- **Where:** `crates/apps/rocket_league/crates/rl_replay/src/header.rs:122–133` (`properties`), `:155` (`ArrayProperty` calls `properties` once per row) and `:172–176` (`StructProperty` calls `properties`).
- **Cause:** the property tree is parsed recursively, and nothing limits how deep it can go. The file controls the depth.
- **Cost per level:** one nested `StructProperty` costs 44 bytes: an empty key (4), the kind name (19), the size (8), an empty struct name (4) and the `None` terminator that closes it (9). One `ArrayProperty` level costs about 43 bytes.
- **Trigger and results (executed):** I wrote a probe in `$TMPDIR/probe` that calls the public `rl_replay::parse` on a file with valid CRCs. Each nesting depth below aborted with `fatal runtime error: stack overflow, aborting` (exit 134):

| File size | Nesting depth | Build | Thread stack |
|---|---|---|---|
| 440 KB | 10,000 | debug | 8 MB main thread |
| 440 KB | 10,000 | release | 2 MB spawned thread |
| 4.4 MB | 100,000 | release | main thread |
| 4.3 MB | 100,000 (nested arrays) | release | main thread |

- **Why it matters:** a stack overflow cannot be caught. It is not an `Err` and not even a panic, so it takes down whatever process parses the replay (analyzer, web app, corpus runner). Real replays are 1–3 MB, so these files are normal size. The parser is meant to "fail closed", and it should return an `Err` here.
- **Notes:**
  - boxcars 0.11.5 `header.rs` `parse_rdict` has the same recursion, so the "equals boxcars" test cannot catch this.
  - The 25,000 cap on array length does not help, because it limits width, not depth.
  - Real headers nest at most about 3 levels: `PlayerStats`/`Goals` arrays, rows and structs.
- **Fix:** pass a `depth` through `properties` and `property`. Return a new `Error::TooDeep { offset }` (or a `BadLength`) once depth passes a small limit such as 16. Alternatively, rewrite the walk with an explicit stack.
- **Regression test:** build a header with 64 nested `StructProperty` levels and assert `Err`. Add the 10,000-level case on a 2 MB thread, where today's code aborts.

### 2. P2: the docs say every error has a byte offset, but three don't
- **Where:**
  - `error.rs:1–2` says "Each one names what was being read and the byte offset into the file".
  - `lib.rs:10–11` and the change fragment `changes/2026-10-24-rl-replay-parser-p1.md:7` say errors "name what was being read and the byte offset".
- **Problem (source-traced):** three error variants carry neither the offset nor what was being read:
  - `Error::Crc` (`error.rs:22–29`) has a section name but no offset.
  - `Error::UnsupportedVersion` (`:52–59`) has no offset.
  - `Error::TrailingHeaderData` (`:61–64`) has a byte count but no offset.
- **Example:** a file with 1 extra byte after the header's `None` gives "1 bytes left after the header properties", with no position. The fragment promises that position.
- **Fix:** add `offset` to these three variants (the reader already has it at each call site), or narrow the wording in all three places. A test should assert the offset on `TrailingHeaderData` and on a header `Crc` error.

### 3. P2: the hostile-input checks have no tests
These rejection paths are new code with no test that fails without them:
- **Array cap:** `MAX_ARRAY_LEN` at `header.rs:14,148–154`. If the check is deleted, every test still passes.
- **Header CRC:** `section(…, "header")` at `lib.rs:69–86`. Only a content-section CRC mismatch is tested (`a_flipped_content_byte_is_a_crc_error`).
- **Invalid UTF-8:** `InvalidUtf8` at `reader.rs:86–94`.

**Fix:** add crafted-byte tests through `rl_replay::parse`:
- an array length of 25,001 gives `BadLength`;
- one flipped header byte gives `Crc { section: "header" }`;
- a key containing `0xFF` gives `InvalidUtf8` with its offset.

## Coverage
Every changed file from `git diff --stat` was read. Of the four review axes, only hostile bytes and evidence apply here: the PR touches no physics code.

| File | What I checked |
|---|---|
| `src/reader.rs` | Bounds before slicing; `i32` lengths (negative values, `i32::MIN`, the ±10,000 text limit, `units*2` cannot overflow); no allocation before a bounds check; the Windows-1252 table against WHATWG; UTF-16 surrogates. All fine. |
| `src/header.rs` | Matches boxcars `parse_rdict` field by field (64-bit size skipped, `Bool == 1`, `ByteProperty "None"` padding byte, array cap). The net-version rule matches boxcars `header.rs:48`. The version-floor tuple comparison is correct. Recursion is finding 1. |
| `src/lib.rs` | `size as usize` is bounds-checked by `take`; the header CRC is checked before the header is parsed; absolute offsets are correct. |
| `src/crc.rs` | Initial value `0x10340DFE` is `!0xefcbf201`, as in boxcars; same polynomial; not reflected. The sample CRCs pass against boxcars with `always_check_crc`. |
| `src/error.rs` | Finding 2. |
| `tests/boxcars_oracle.rs` | Covers all three committed `.replay` files (`git ls-files '*.replay'`). The comparison of the whole property tree really compares it. |
| `Cargo.toml` (crate) | No normal dependencies; boxcars is a dev-dependency only. |
| Root `Cargo.toml`, `Cargo.lock` | New workspace member only. |
| `.github/scripts/check_workspace_deps.py` | The empty pin for `rl_replay` works: the script passes on fresh metadata. |
| `docs/WORKSPACE-MAP.md` | Identical to the output of `generate_workspace_map.py`. |
| `replay-parser-scope.md` | Version floor 868.23.8 checked against the samples and against boxcars' gates (`attributes.rs:492`, which gates at 868.23.8). |
| Change fragment | The "equal to boxcars on every committed sample" claim holds. The error-offset claim is finding 2. |

## Design questions
None.

## Optional improvements
- `Reader::name` and `text` drop the last byte without checking it is NUL. That is what boxcars does, but a stricter check would reject malformed names such as `"NoneX"`, which is currently read as `None`.
- Bytes after the content section are accepted silently.
- `crates/apps/rocket_league/README.md`'s "Crates:" list does not include `rl_replay`. That list was already incomplete before this PR.
- When P2 brings in the boxcars bit encodings, add the `THIRD_PARTY_NOTICES` credit the scope document promises.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rl_replay --all-targets -- -D warnings` (target dir in `$TMPDIR`; the default target dir is read-only here) | pass |
| `cargo test -p rl_replay` | pass: 20 unit tests, 3 oracle tests, 1 doctest (compiled, not run) |
| `python3 .github/scripts/changes.py check` | 307 fragments ok |
| `check_workspace_deps.py` on fresh `cargo metadata` | pass |
| `generate_workspace_map.py` | output identical to the committed map |
| Executed recursion probe | stack overflow (finding 1) |

- **Tests at base:** the crate does not exist at base, so every new test fails there.
- **Validation gap:** the private-corpus differential (`corpus_check`) needs the private repo and was not run, so headers have only been checked against the three committed samples.

## Verdict
**Changes required.**
