<!-- rocket-league/999-r1 run 4, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 183 s, 1000925 tokens, $1.03 -->

I found one P0 and four P2s, so the verdict on PR 999 is **changes required**. A replay of about 880 KB with a valid CRC crashes the whole process through unbounded recursion; I ran that myself in a scratch probe.

**PR:** Rusty-Mill/rusty_mill #999. Base `712c1c2275f6898009357365175a2498f291dadb`. I fetched `pull/999/head`, and it equals the reviewed head `fd33e2baa3e8862cefdac4e670fa3475d0f2f4e2`. It has one commit: "rl: first-party replay parser, slice P1 (container, CRC, header)".

## Blocking findings

**1. P0: a crafted header crashes the process through unbounded recursion** (`rl_replay/src/header.rs:121`, `:154`, `:173`)
- **Trigger:** any replay given to `rl_replay::parse`. Replay files are untrusted input.
- **Cause:** `properties()` calls `property()`. For a `StructProperty` (`:173`) or `ArrayProperty` (`:154`), `property()` calls `properties()` again, with no depth limit.
  - Each level costs about 43–44 bytes of input: an empty key (`n=0`), the kind, the size, and either an empty struct name or `count=1`.
  - The header CRC is checked first, but the algorithm is public (`rl_replay::crc`), so an attacker can always supply a valid CRC.
  - The `MAX_ARRAY_LEN` cap limits how wide an array is, not how deep the nesting goes.
- **Executed:** a scratch binary outside the tree, calling the public `parse`, on the 8 MB main-thread stack:

  | Build | Nested `StructProperty` | Nested `ArrayProperty` |
  |---|---|---|
  | Debug | aborts at about 1,815 levels (≈77 KB file) | — |
  | Release | 19,331 levels parse; 19,406 levels (≈833 KB) give `stack overflow, aborting`, exit 134 | 20,000 levels (860 KB) also abort |

  - The cost is the same in debug: an 880 KB nested-struct file aborts there too.
  - Worker threads with a 2 MB stack, such as test or tokio threads, fail at about a quarter of that depth.
  - Rust cannot catch a stack overflow, so the change fragment's claim that "errors fail closed" is false here.
- **Oracle note:** boxcars 0.11.5's `parse_rdict`/`array_property` (`header.rs:81–164`) recurses the same way. Matching the oracle does not excuse it.
- **Fix:** pass a depth through `properties` and `property`. Fail with a new `Error` variant that carries the offset once the depth passes a small cap; real headers nest about two levels (an array of rows holding scalar properties).
- **Regression test:** go through the public `parse` with a CRC-valid header nested one level past the cap, once with `Struct` and once with `Array`, and assert `Err`. At head this returns `Ok`, so the test is red. A second test at about 20,000 levels must return `Err`; at head it aborts the test binary.

**2. P2: the `MIN_VERSION` doc comment misstates the boxcars gates** (`header.rs:7–10`)
- The comment says 868.23.8 is "the newest encoding change boxcars gates below it".
- boxcars 0.11.5 has newer encoding gates above that floor:
  - `VersionTriplet(868, 24, 10)` at `network/frame_decoder.rs:408` (the network trailer word).
  - `net_version < 10` at `network/attributes.rs:1704` (24 extra PsyNet-ID bytes).
- So 868.23.8 is only the newest gate at or below the floor; it is not the newest gate overall. (Source-traced.)
- **Fix:** reword the comment to name the gates that sit above the floor.

**3. P2: the scope doc's encoding-risk statement is now false** (`replay-parser-scope.md:121–122`)
- The doc says "The version scope above keeps us to the current ones".
- This diff lowers the floor from net 10 to 868.23.8 (`:53–57`). The supported range therefore now crosses both gates from finding 2. `419a` (868.23.8) needs the net<10 PsyNet-ID layout and the pre-868.24.10 framing.
- **Fix:** amend the bullet, or the P2 row of the slice table, to say P2 must decode both encodings.

**4. P2: malformed strings are accepted** (`reader.rs:85–90`, `:97–113`)
- `name()` and `text()` drop the last byte (or the last UTF-16 unit) without checking that it is NUL. `name()` also accepts `n=0`.
- *Source-traced:* a key with length 9 and bytes `TeamSizeX` decodes as `TeamSize`. Any string whose terminator is not NUL is silently cut by one character. The format doc (§3, §6) specifies a NUL-terminated string.
- boxcars' `decode_str` behaves the same way, so the oracle test cannot catch this.
- **Fix:** require the final byte (or unit) to be 0, otherwise return an error. Keep allowing `text` length 0, since boxcars' `parse_text_zero_size` tests show it is legitimate.
- **Test:** `reader.rs` unit tests with crafted bytes in both encodings. They are red at head.

**5. P2: `BoolProperty` accepts any byte** (`header.rs:156`)
- `r.u8(..)? == 1` turns any value of 2–255 into `false`, but the format doc says `u8 (0/1)`.
- **Fix:** reject values other than 0 or 1.
- **Test:** a crafted `BoolProperty` with value 2 must return `Err`.

**6. P2: several new fail-closed paths have no test**
- No test exercises:
  - an `ArrayProperty` count above `MAX_ARRAY_LEN`, or a negative count (`BadLength`);
  - `InvalidUtf8`;
  - a header-section CRC mismatch (only the content CRC is tested);
  - a header `size` larger than the file.
- Each needs a crafted-bytes test through `parse`.

## Coverage
Every changed file, and the defect classes I checked in it:
- **`crc.rs`:** units and parameters against `replay-file-format.md` §1 (poly, init, XorOut, non-reflected); matches the samples through the oracle. Clean.
- **`reader.rs`:**
  - Bounds: `take` checks before slicing; `len` rejects negatives; `text` range-checks before `unsigned_abs`, so `i32::MIN` is safe; the largest allocation is bounded by the remaining input.
  - Encodings match `encoding_rs` (Windows-1252 C1 holes, U+FFFD for unpaired surrogates).
  - Finding 4.
- **`header.rs`:**
  - Recursion and unbounded work (finding 1).
  - Version gate: the tuple order is correct, and a missing net version compares as 0.
  - `ByteProperty` "None" handling matches boxcars `header.rs:105–111`.
  - Findings 2 and 5.
- **`lib.rs`:** section framing (u32 size, truncation instead of a panic, absolute offsets); both CRCs are always verified before any data is returned. Clean apart from finding 1, which is reachable through it.
- **`error.rs`:** `Display` and the variants. Clean.
- **`tests/boxcars_oracle.rs`:** a real differential test on all three samples. The test name `a_cut_sample_fails_cleanly_at_every_header_length` overstates it, since it steps by 97 bytes (see optional improvements).
- **`Cargo.toml` (crate):** no normal or build dependencies; boxcars is dev-only, locked at 0.11.5. Clean.
- **Root `Cargo.toml`, `Cargo.lock`:** one member added; the lock adds only `rl_replay` → `boxcars`. Clean.
- **`check_workspace_deps.py`:** the `{}` pin forbids external normal and build dependencies and exempts dev-dependencies; consistent with ADR-0002 Tier S. Clean.
- **`changes/2026-10-24-rl-replay-parser-p1.md`:** the "fail closed" claim is contradicted by finding 1. Its other claims are verified.
- **`replay-parser-scope.md`:** finding 3. The new version-scope text matches the samples (868.23.8, 868.27.10 and 868.32.11 all pass `every_sample_is_a_supported_version`).
- **`docs/WORKSPACE-MAP.md`:** the row matches the crate description.

## Design questions
- `TrailingHeaderData` is stricter than boxcars, which does not check for trailing bytes. It holds on the three samples. Whether it holds on the private corpus is a validation gap: run the P3 corpus check on the game machine.

## Optional improvements
- Credit boxcars in a `THIRD_PARTY_NOTICES.md` for the limits and header logic that mirror it (the scope doc at `:123–124` promises this for the parts that follow boxcars closely).
- Scope doc `:34–35` says "Windows-1252 from a 128-entry table"; the code uses a 32-entry table. This text predates the PR.
- Rename the cut-sample test, or test every length.

## Checks
- `cargo fmt --all -- --check`: passed.
- `cargo clippy -p rl_replay --all-targets -- -D warnings`: passed. I used `CARGO_TARGET_DIR=$TMPDIR/target` because the configured target directory is read-only in the sandbox.
- `cargo test -p rl_replay`: 20 unit, 3 oracle and 1 doctest, all passed.
- `python3 .github/scripts/changes.py check`: 307 fragments ok.
- `check_workspace_deps.py` on fresh `cargo metadata`: exit 0. Its unit tests: 25 passed.
- Recursion probe: built and run in `$TMPDIR/probe` as above.
- No physics or tape-bot code is touched, so none of those checks apply.
- **Validation gap:** the private replay corpus was not run.

**Verdict: changes required.**
