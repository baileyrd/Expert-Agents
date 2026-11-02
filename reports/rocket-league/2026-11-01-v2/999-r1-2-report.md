<!-- rocket-league/999-r1 run 2, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 156 s, 796562 tokens, $0.95 -->

# Review of PR 999: first-party replay parser `rl_replay`, slice P1

- **Repository:** Rusty-Mill/rusty_mill, PR 999
- **Base:** `712c1c2275f6898009357365175a2498f291dadb`
- **Reviewed head:** `fd33e2baa3e8862cefdac4e670fa3475d0f2f4e2`. `git fetch origin pull/999/head` returned this same SHA. The range holds one commit.

**Verdict: changes required.** A crafted replay of under 1 MB crashes the parser (P0). Two smaller findings are about docs that promise more than the code does.

## Blocking findings

### 1. P0: a crafted header with deeply nested properties overflows the stack and kills the process
- **Where:** `crates/apps/rocket_league/crates/rl_replay/src/header.rs:405-416` (`properties`), `:438` (each `ArrayProperty` row calls `properties`), `:455-458` (`StructProperty` calls `properties`).
- **Trigger:** a replay whose header nests `StructProperty` (or `ArrayProperty` with length 1) inside itself. Nothing limits how deep the nesting goes. Each level costs about 48 bytes of file and one recursive call. `MAX_ARRAY_LEN` limits how wide an array can be, not how deep the nesting goes.
- **What happens (executed):** I built a scratch probe outside the tree that calls the public `rl_replay::parse` in a release build, on the main thread with its 8 MB stack. Both sections had valid CRCs.
  - 10,000 levels (480 KB file): parses.
  - 20,000 levels (960 KB file): `thread 'main' has overflowed its stack — fatal runtime error: stack overflow, aborting`.
  - `ArrayProperty` nesting fails the same way.
  - The file is smaller than the committed samples (870 KB to 1.5 MB). A stack overflow aborts the process, so `catch_unwind` cannot recover. Any service that parses uploaded replays (the planned P3 `replay-analyzer`, then `rleval-app`) can be killed by one file. Test threads have 2 MB stacks, so the limit there is roughly four times lower.
- **What the scope requires:** malformed input returns an `Error`. The crate itself says "Errors fail closed" (`lib.rs:11`). boxcars has the same flaw (`header.rs:141`, `:162`), so the oracle is no defence.
- **Fix:** pass a depth counter through `properties` and `property`, with a small cap. Real headers nest about three levels (Array → row → Struct). Beyond the cap, return a new `Error` variant (or `BadLength { what: "property nesting", offset, value: depth }`).
- **Test that goes red without the fix:** a public-API test in `tests/` or `lib.rs` that builds 100,000 nested `StructProperty` levels and 100,000 nested `ArrayProperty` levels, and asserts `parse` returns `Err`.

### 2. P2: docs say every error names what was read and the byte offset; three error types do not
- **Where:** `src/error.rs:1-2` ("Each one names what was being read and the byte offset into the file"); `lib.rs:11-12`; the change fragment `changes/2026-10-24-rl-replay-parser-p1.md:7` ("Errors fail closed and name what was being read and the byte offset").
- **The gap (source-traced):**
  - `Error::Crc` (`error.rs:184-191`) has no offset.
  - `Error::UnsupportedVersion` (`:215-222`) has neither an offset nor a `what`.
  - `Error::TrailingHeaderData` (`:224-227`) has neither. `header.rs:390-393` has the offset available (`r.offset()`) and drops it.
  - Example: a header with one stray byte after `None` reports "1 bytes left after the header properties" with no position.
- **Fix:** add `offset` to `TrailingHeaderData` and `Crc` (the start of the section's data). Alternatively, narrow the doc and fragment wording to the error types that do carry an offset.
- **Test:** extend `trailing_bytes_are_refused` to assert the absolute offset.

### 3. P2: lowering the version floor makes the scope doc's version-risk statement false
- **Where:** `crates/apps/rocket_league/rleval/docs/replay-parser-scope.md`, under Costs and risks: "Bit-level encodings … changed across versions. The version scope above keeps us to the current ones". This PR edits the version-scope paragraph just above it (line ~53) without updating that bullet.
- **Trace (source-traced against boxcars 0.11.5):** the new floor, 868.23.8, sits below two encoding branches that boxcars has:
  - `network/frame_decoder.rs:408`: the frame trailer is only read from 868.24.10 on.
  - `network/attributes.rs:1704`: a PsyNet remote ID carries 24 extra bytes when `net_version < 10`.
  - `419a.replay` is 868.23.8 (I confirmed this from its header bytes), so it uses the older encoding in both places. P2 of the plan must therefore decode two encodings, not only "the current ones". The doc now understates P2's scope and its risk.
- **Fix:** update the risk bullet (and the P2 row's "Done when") to name both gates. Better still, add a P2 test that decodes `419a` through both branches.

## Coverage

| Changed file | What I checked |
|---|---|
| `.github/scripts/check_workspace_deps.py` | Traced `pinned_dependency_violations`. `{}` means no external normal or build dependencies, and dev-dependencies are exempt. Correct. |
| `Cargo.lock`, `Cargo.toml` | New workspace member; only `boxcars` 0.11.5 appears, as a dev-dependency. No problems. |
| `changes/2026-10-24-rl-replay-parser-p1.md` | Claims checked: "equal to boxcars on every sample" holds (oracle test green); the error/offset claim is false (finding 2). |
| `rl_replay/Cargo.toml` | Lints and dependency tier checked. No problems. |
| `src/crc.rs` | Polynomial, initial value and final XOR match the doc. The table has 256 entries and indexes from a `u8`. The 0xA0 test vector matches boxcars. No problems. |
| `src/error.rs` | Display text checked. Doc claim is false (finding 2). |
| `src/header.rs` | Unbounded recursion (finding 1). Checked against boxcars `header.rs` field by field, all equivalent: the 8-byte size field, `ByteProperty "None"` plus a padding byte being dropped, `Bool == 1`, and array length negative or above 25,000 being rejected. Version gate: the `unwrap_or(0)` comparison follows boxcars' tuple order. |
| `src/lib.rs` | Section sizes are checked against the data before any slice is taken. Both CRCs are checked. A truncated file returns `Truncated`. No panic paths. |
| `src/reader.rs` | `take` checks bounds before slicing. `len` rejects negative values. `text` is capped at ±10,000 characters before `unsigned_abs`, so there is no overflow. Unpaired UTF-16 surrogates become U+FFFD. The Windows-1252 high block matches WHATWG / encoding_rs. No problems. |
| `tests/boxcars_oracle.rs` | Compares all three samples against boxcars in full. The cut-file test only steps every 97 bytes, which is acceptable. No test covers nesting depth (part of finding 1). |
| `rleval/docs/replay-parser-scope.md` | Sample versions confirmed from file bytes: 868.23.8, 868.27.10 and 868.32.11. Risk bullet is now false (finding 3). |
| `docs/WORKSPACE-MAP.md` | Row matches the crate's description. |

**Failure classes:**
- **Unbounded work / hostile bytes:** found the depth issue (finding 1). Widths, lengths and allocations are all limited by the input size. `collect` into `Result<Vec>` reserves nothing up front.
- **Framing:** `header_size` sets the header's extent strictly, which is stricter than boxcars.
- **Not applicable:** units, order within a tick, solve order, geometry, alignment, stale frames and socket handling. The diff has no physics or bridge code.
- **Tests at base:** the crate does not exist at the base, so every test is red there.

## Design questions
- The PR lowers the version floor from net 10 to 868.23.8 inside the scope doc, which is still marked "proposed" and "does not authorize implementation". The reason is real (`419a` is a committed sample). But the change widens what P2 must decode, so the owner should confirm it rather than have it recorded as "P1 found that".

## Optional improvements
- `Reader::name` drops the last byte without checking that it is NUL. A key written as `"None"` plus any final byte becomes the terminator. boxcars does the same, but checking for the NUL would be cheap and stricter.
- The scope doc says Windows-1252 comes from "a 128-entry table". The code uses a 32-entry table for 0x80–0x9F, which is correct; only the wording differs.
- The scope doc's Licence bullet promises credit in `THIRD_PARTY_NOTICES.md` where we follow boxcars closely. There is no such file in the repo. P1 copies boxcars' limits, its CRC test vector and its header logic, so it may be worth starting that file now.

## Checks run
- `cargo fmt --all -- --check`: passed (exit 0).
- `cargo clippy --offline -p rl_replay --all-targets -- -D warnings`: passed. I used `CARGO_TARGET_DIR=$TMPDIR/target` because the configured target directory is read-only in this sandbox.
- `cargo test --offline -p rl_replay`: passed. That covers 20 unit tests, 3 oracle tests and 1 doctest (compile only). The oracle tests need no private corpus.
- `python3 .github/scripts/changes.py check`: "307 fragment(s) ok".
- Executed probe (scratch crate in `$TMPDIR/probe`, release build): the stack-overflow crash at 20,000 nesting levels, as described in finding 1.
- Not run: physics, tape-bot and live-game checks, because the diff touches none of them. Nothing here needs Windows, the game or the private corpus.

**Verdict: changes required.**
