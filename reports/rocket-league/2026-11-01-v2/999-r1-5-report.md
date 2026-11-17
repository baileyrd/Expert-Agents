<!-- rocket-league/999-r1 run 5, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 153 s, 857244 tokens, $0.88 -->

I found one P0 and one P2 in PR 999, so the verdict is **changes required**. The PR adds the first-party replay parser's first slice: the file container, both section checksums (CRCs) and the header. Replay files are untrusted input, so the parser must turn malformed bytes into errors. It does that everywhere except the two cases below.

**Repository:** Rusty-Mill/rusty_mill · **PR:** 999 · **Base:** `712c1c2275f6898009357365175a2498f291dadb` · **Reviewed head:** `fd33e2baa3e8862cefdac4e670fa3475d0f2f4e2` (the fetched `pull/999/head` matches this SHA).

## Blocking findings

### 1. P0: a crafted header crashes the whole process with a stack overflow
- **Where:** `crates/apps/rocket_league/crates/rl_replay/src/header.rs:121-132` (`properties`), `:154` (array rows) and `:171-175` (struct fields).
- **Cause:** `properties → property → properties` recurses once per nested `StructProperty` or `ArrayProperty`, with no depth limit. Each level costs only about 44 bytes:
  - an empty key (`0i32`);
  - the kind name `"StructProperty\0"`;
  - the 8-byte size;
  - an empty struct name.

  An array level with `count = 1` is about the same size. The section CRC gives no protection: the attacker computes it with the public `rl_replay::crc`. `MAX_ARRAY_LEN` bounds an array's width, not its depth.
- **Failure scenario (executed** with a scratch probe crate through the public `rl_replay::parse`):
  - Release build on the default 8 MiB main-thread stack: 10,000 nested structs parse; 20,000 (an ~880 KB file) end in `fatal runtime error: stack overflow, aborting`, exit 134.
  - Debug build: 50,000 nested structs (2.2 MB) abort, and so do 50,000 nested `ArrayProperty` levels with `count = 1` (2.15 MB).
  - Real replays are 0.9–1.5 MB, so a hostile file fits in a normal replay's size.
  - This is an abort, not a panic. `catch_unwind` cannot contain it, and it kills every caller in the process.
  - The required behaviour is an `Err`. The crate docs (`lib.rs:10-11`, "Errors fail closed") and the change fragment say errors fail closed; this defect makes both claims false.
  - boxcars 0.11.5 recurses the same way (`header.rs` `parse_rdict`), so the differential oracle cannot catch it.
- **Required correction:** pass a depth counter through `properties` and `property`. Refuse anything past a small limit, such as 16 levels (real headers nest only array → row, about 2–3 levels), with an error that names the offset. Add a test that builds a header with, say, 100,000 nested structs and asserts `parse` returns `Err`. The same test for nested arrays should also go red today. Both abort at this head.

### 2. P2: bytes after the content section are silently accepted
- **Where:** `crates/apps/rocket_league/crates/rl_replay/src/lib.rs:55-63`.
- **Cause:** after `section(&mut r, "content")`, `parse` never checks `r.remaining() == 0`. `replay-file-format.md` §1 says a replay is exactly two sections "laid out back to back".
- **Failure scenario (executed):** a valid file with `b"garbage after content"` appended returns `Ok` with `content.len() == 4`. It should be an error.
  - This is inconsistent with the header, which refuses trailing bytes (`header.rs:106-110`, `TrailingHeaderData`), and with the "fail closed" claim.
  - All three committed samples end exactly at `16 + header_size + content_size` (1319893, 1533918 and 871685 bytes), so a strict check breaks none of them.
- **Required correction:** after the content section, return an error naming the trailing bytes and their offset if any remain. Add a unit test that appends one byte to the `file(...)` fixture in `lib.rs` tests and expects that error.

## Coverage (every changed file; the replay-parser axis is "hostile bytes", so mechanics and physics classes don't apply)

| File | What I checked |
|---|---|
| `rl_replay/src/lib.rs` | Framing, section sizes (u32 to usize, bounded by `take`), CRC verified before decode, absolute error offsets, trailing data (finding 2) |
| `rl_replay/src/reader.rs` | Every length is checked before slicing; `len` refuses negatives; `text` stays within ±10,000 characters with no `abs` overflow; no allocation driven by input before the bounds check; UTF-16 and Windows-1252 decoding match boxcars' `encoding_rs` behaviour, including the five unassigned bytes mapping to C1 controls |
| `rl_replay/src/crc.rs` | Polynomial, initial value and final XOR match format doc §1 and boxcars; table built at compile time |
| `rl_replay/src/header.rs` | Version gate (`net` read when engine > 865 and licensee > 17, same as boxcars `header.rs:48`); tuple comparison against `MIN_VERSION`; array width bound; collecting rows does not preallocate; recursion depth (finding 1); Byte/`None` padding; Bool `== 1` (same as boxcars) |
| `rl_replay/src/error.rs` | Every variant carries what was being read and the offset, as documented; `Display` is correct |
| `rl_replay/tests/boxcars_oracle.rs` | Compares the full property tree and the container fields; the cut-file test only cuts every 97 bytes, which is acceptable |
| `rl_replay/Cargo.toml` | No normal or build dependencies; boxcars 0.11 as a dev-dependency only, matching the locked 0.11.5 |
| `check_workspace_deps.py` | An empty `{}` entry is not `None`, so the crate really is pinned to zero external dependencies; the script passes |
| `Cargo.toml`, `Cargo.lock`, `WORKSPACE-MAP.md` | Workspace member added; lock entry is boxcars only; the map passes `--verify` |
| `replay-parser-scope.md` | The new 868.23.8 floor is backed: I ran the samples and got 42f2 = 868.27.10, 419a = 868.23.8, rb_replay_ingest sample = 868.32.11. The `MIN_VERSION` doc claim holds: boxcars gates the network decode at `VersionTriplet(868,23,8)` (`attributes.rs:492`); its next gate up, 868.24.10, falls inside the supported range and is P2's job. |
| `changes/2026-10-24-rl-replay-parser-p1.md` | "Errors fail closed" is false until finding 1 is fixed; the other claims match the code and tests |

Tests red at base: the whole crate is new, so every test fails at base. No test exercises nesting depth or trailing file bytes; the two regression tests above fill those gaps.

## Design questions
- None.

## Optional improvements
- `Reader::name` doesn't check that the last byte is actually NUL; it drops whatever is there. boxcars does the same, so this is harmless today.
- The test that cuts the sample every 97 bytes could also cut at every offset inside the first 200 bytes, where the framing fields are.

## Checks performed
- `cargo fmt --all -- --check`: pass.
- `cargo clippy -p rl_replay --all-targets --offline -- -D warnings`: pass. It needed `CARGO_TARGET_DIR=$TMPDIR/target` because the configured target directory is on a read-only file system.
- `cargo test -p rl_replay --offline`: pass (20 unit, 3 oracle and 1 doc test).
- `python3 .github/scripts/changes.py check`: 307 fragments ok.
- `check_workspace_deps.py` on fresh `cargo metadata`: pass. Its unit tests: 25 OK.
- `generate_workspace_map.py --verify docs/WORKSPACE-MAP.md`: pass.
- Probes in a scratch crate outside the tree: the stack overflow in finding 1, the accepted trailing bytes in finding 2, and the sample version triples and file lengths.
- **Validation gaps:** the private corpus wasn't available, so I couldn't check whether real ranked replays parse under the new floor. That needs the game machine's `corpus_check` run, which arrives with P3/P4.

**Verdict: changes required**
