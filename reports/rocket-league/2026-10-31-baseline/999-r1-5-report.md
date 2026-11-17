<!-- rocket-league/999-r1 run 5, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 121 s, 871782 tokens, $0.80 -->

I found one blocking defect: a crafted replay with valid CRCs crashes the whole process instead of returning an error. I reproduced it with a probe in scratch space. Verdict: **changes required**.

**Pinned review**
- Repository: Rusty-Mill/rusty_mill, PR #999
- Base: `712c1c2275f6898009357365175a2498f291dadb`
- Head: `fd33e2baa3e8862cefdac4e670fa3475d0f2f4e2`. The fetched `pull/999/head` is this commit and so is the checkout.
- Scope: the new crate `rl_replay` (the container, both section CRCs and the header), its boxcars differential test, the change fragment, `replay-parser-scope.md`, `check_workspace_deps.py` and `WORKSPACE-MAP.md`.

## Blocking findings

### P0 — Deeply nested header properties overflow the stack and abort the process
**Where:** `crates/apps/rocket_league/crates/rl_replay/src/header.rs:121-132` and `:137-184`. `properties()` calls `property()`. `property()` calls `properties()` again for each `StructProperty` (line 173) and for each row of an `ArrayProperty` (line 154). Nothing limits how deep this goes.

**Failure scenario (executed):** my scratch probe calls only the public API (`rl_replay::crc`, `rl_replay::parse`). It builds a header of version 868.27.10 with N levels of `k: StructProperty "S" { … }`, adds the N+1 `None` terminators, and computes both CRCs correctly. Computing the CRC is trivial for an attacker, since the algorithm is public and exported.

| Build | Depth | File size | Result |
|---|---|---|---|
| release, 8 MB main stack | 10,000 | ~0.5 MB | `Ok` |
| release, 8 MB main stack | 20,000 | ~1 MB | `fatal runtime error: stack overflow, aborting`, exit 134 |
| release, 8 MB main stack | 50,000 | 2.4 MB | abort, exit 134 |
| debug | 2,000 | ~100 KB | abort |

The same input built from `ArrayProperty` rows of length 1 aborts at the same depths.

A stack overflow is an abort, not a panic, so `catch_unwind` cannot contain it. It takes down the whole host process, and P3 plans to move `replay-analyzer` and the web app onto this parser.

**What the evidence requires:** the change fragment and the doc comment at `lib.rs:10-11` say "Errors fail closed". That requires an `Err` here. Real headers nest only a few levels: `PlayerStats` rows and `Goals` rows are maps of scalars. boxcars 0.11.5 (`header.rs` `parse_rdict`) has the same unbounded recursion, so "equal to boxcars" does not settle the hostile-bytes requirement.

**Required correction:**
1. Thread a depth counter through `properties()` and `property()`.
2. Refuse anything past a small cap (for example 32) with a new error that carries the offset, such as `Error::TooDeep { offset, depth }`.

**Regression test:** a unit test through `rl_replay::parse` with nesting past the cap, for both `StructProperty` and `ArrayProperty`, that expects `Err`. At the head this test aborts the test binary, so it goes red. A test at depth around 100,000 also proves the check runs before the recursion.

## Design questions
None.

## Optional improvements
- `Reader::name` and `text` drop the last byte without checking that it is a NUL. This matches boxcars' `decode_str`, `decode_windows1252` and `decode_utf16` exactly, so it is not a defect. A strict check would turn a malformed length into an error rather than silent truncation, but would need confirming against the samples.
- `a_cut_sample_fails_cleanly_at_every_header_length` only cuts every 97th byte. A step of 1 is still cheap and would cover every boundary.
- Once nesting is capped, a small public-API fuzz-style loop would harden the header path: random single-byte mutations of a sample with the CRC recomputed, asserting no panic.

## Things I checked and ruled out
- **Lengths and counts from the file:** every one goes through `take()` before any slicing. A negative `i32` length becomes `BadLength`. Text length is limited to ±10,000, and `units*2` cannot overflow. A huge section size gives `Truncated`.
- **Array length:** capped at 25,000, as in boxcars. `collect::<Result<Vec>>` does not preallocate, so the total work is limited by the file's own bytes.
- **Version gate:** it matches boxcars' net-version condition (`> 865 && > 17`). 868.23.8 is indeed boxcars' newest version gate below the oldest sample (`VersionTriplet(868, 23, 8)`), so the `MIN_VERSION` doc comment is accurate.
- **CRC:** the table is correct, and it matches boxcars' single-byte vector.
- **Windows-1252 and UTF-16 decoding:** consistent with the encoding_rs output that boxcars uses, and the oracle test confirms it on all three samples.
- **Docs:** the `replay-parser-scope.md` version-floor change agrees with the code and the oracle test.

## Checks performed

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rl_replay --all-targets -- -D warnings` | pass |
| `cargo test -p rl_replay` | pass: 20 unit, 3 boxcars oracle, 1 doctest |
| `python3 .github/scripts/changes.py check` | pass (307 fragments ok) |
| `check_workspace_deps.py` on fresh `cargo metadata` | pass (exit 0) |
| Nesting-depth probe through the public API, release and debug | stack-overflow abort, as in the table above |

- **Cargo setup:** I used a scratch `CARGO_HOME` and target directory under `$TMPDIR`, because the default cargo cache is read-only in this sandbox. I fetched crates.io for boxcars and its dependencies. I read the boxcars 0.11.5 source there.
- **Red at base:** every test is new along with the crate, so all of them are absent at the base and nothing passes there.

## Validation gaps
- I did not run the private corpus, and no test in this slice uses it.
- The game, PowerShell and the plugin are not involved in this change.

## Verdict
**Changes required**
