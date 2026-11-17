<!-- rocket-league/999-r1 run 1, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 137 s, 691385 tokens, $0.79 -->

I'm requesting changes on PR 999 because of one blocking defect: a crafted replay header crashes the whole process by overflowing the stack.

**Pinned review:** Rusty-Mill/rusty_mill, PR 999. Base `712c1c2275f6898009357365175a2498f291dadb`, reviewed head `fd33e2baa3e8862cefdac4e670fa3475d0f2f4e2`. The fetched `pull/999/head` equals that head. The diff is one commit, `fd33e2baa`.

## Blocking findings

### 1. P0: deep nesting in the header crashes the process (stack overflow, aborts the process)
- **Where:** `crates/apps/rocket_league/crates/rl_replay/src/header.rs:121-132` (`properties`) and `:137-184` (`property`). The recursion happens at `:154` (each row of an `ArrayProperty`) and `:173` (each `StructProperty`), and nothing limits how deep it goes.
- **Trigger:** any caller of the public `rl_replay::parse`. Nothing in the tree calls it yet; P3 of the scope note wires it into `replay-analyzer` and the app. Anyone who can hand over a `.replay` file can cause it.
- **Failure scenario (executed):**
  - In a scratch copy outside the tree, a probe built a header of `868.27.10`, then N levels of `key "a"` + `StructProperty` + 8-byte size + empty struct name, then N+1 `"None"` keys.
  - It used valid CRCs from `rl_replay::crc` and called `rl_replay::parse` on the main thread (8 MiB stack).
  - Release build: N = 10,000 returns `Ok`. N = 20,000 (a 940 KB file) aborts with `fatal runtime error: stack overflow` and exit 134.
  - Release build, `ArrayProperty` with a length of 1 per level: N = 100,000 (a 4.5 MB file) also aborts. No depth between 10,000 and 100,000 was tried for this variant.
  - Debug build: 2,000 levels (a 94 KB file) already aborts.
  - Default spawned threads have 2 MiB of stack, so the limit there is lower again.
  - A stack overflow cannot be caught. The required behaviour is an `Err`; the code produces an abort.
- **Why the oracle misses it:** boxcars 0.11.5 has the same unbounded recursion (`header.rs:124,139-141`). The differential test against boxcars therefore cannot catch this.
- **Required correction:** pass a `depth` into `properties`/`property`. Return a new error such as `Error::TooDeep { offset }` (or `BadLength`) above a small limit; real headers nest about 3 deep (`PlayerStats` → row → `OnlineID` struct), so a limit like 32 is plenty.
- **Regression test:** a unit test through `rl_replay::parse` with about 100,000 nested `StructProperty` levels, and a second one for nested `ArrayProperty`, both asserting `Err`. Both abort the test binary at this head.

### 2. P2: "Errors fail closed" is false as written
- **Where:** `src/lib.rs:10-11` ("Errors fail closed and name what was being read and the byte offset") and `changes/2026-10-24-rl-replay-parser-p1.md:7` ("Errors fail closed…").
- **Failure scenario (executed):** the input from finding 1 aborts the process instead of returning an `Error`.
- **Required correction:** keep the claim only once finding 1 is fixed and its regression test is in place; otherwise qualify it.

## Coverage (every changed file)

**`.github/scripts/check_workspace_deps.py`**
- Pins `"rl_replay": {}`, meaning no external normal or build dependencies. The script passes on fresh `cargo metadata`, and boxcars is only a dev-dependency.

**`Cargo.lock`, `Cargo.toml`**
- New workspace member; its only lock entry is boxcars, a dev-dependency at the locked 0.11.5. No issues.

**`changes/…-p1.md`**
- The "equal to boxcars on every committed sample" claim is confirmed by the oracle test, which ran green.
- The "fail closed" claim is false: finding 2.
- The version-floor change matches the code (`MIN_VERSION = (868, 23, 8)`).

**`rl_replay/Cargo.toml`**
- Lints and dependencies match Tier S. No issues.

**`src/crc.rs`**
- Polynomial, initial value and final XOR match the format doc and boxcars. The single-byte test value is the one from boxcars.
- It is not table-driven against untrusted lengths, and it runs only over a slice whose bounds are already checked. No issues.

**`src/error.rs`**
- Display text and offsets checked. No issues.

**`src/header.rs`**
- Compared field by field with boxcars `parse_rdict`:
  - The u64 size equals boxcars' u32 plus 4 ignored bytes.
  - Byte properties with an enum type of `None` are dropped, as in boxcars.
  - Bool is `== 1`, as in boxcars.
  - Name and Str values are decoded as text, as in boxcars.
- Array length is capped at 25,000. Each row costs at least 9 bytes, so wide input only grows memory in proportion to its size.
- The version gate matches boxcars' net-version condition. `MIN_VERSION` is boxcars' newest network gate at or below the oldest sample (`attributes.rs:492`).
- Defect: unbounded nesting depth (finding 1).

**`src/reader.rs`**
- `take` checks bounds before slicing, so there is no allocation sized by input before the check.
- Negative lengths are rejected through `try_from`.
- Text is limited to ±10,000 characters, matching boxcars.
- UTF-16 and Windows-1252 decoding are equivalent to encoding_rs (U+FFFD for bad surrogates; 0x81, 0x8D, 0x8F, 0x90 and 0x9D map to C1 controls).
- `i32::MIN` is handled by the range check before `unsigned_abs`. No issues.

**`src/lib.rs`**
- Section framing: the size is checked by `take`, the CRC is checked before decoding, and header errors carry absolute offsets (`base = 8`).
- Bytes after the content section are ignored, as in boxcars.
- The doc claim is finding 2.

**`tests/boxcars_oracle.rs`**
- Three samples, all present, equal boxcars.
- The cut test is weak (see optional improvements).

**`rleval/docs/replay-parser-scope.md`**
- The version floor (868.23.8) and the slice table (bit reader moved to P2) match the code.

**`docs/WORKSPACE-MAP.md`**
- The `rl_replay` row matches what `generate_workspace_map.py` produces. Other rows in my regeneration differ only in per-crate counts and are not touched by this PR.

The physics, verification, bridge and socket checks don't apply: the diff touches only the replay-format path. On that path I checked bounded lengths, bounded loops, input-driven allocation, partial input, malformed input and recursion depth.

**Tests at base:** the crate does not exist at base, so every new test is red there. No test covers nesting depth (finding 1).

## Design questions
None.

## Optional improvements
- `a_cut_sample_fails_cleanly_at_every_header_length` (`tests/boxcars_oracle.rs:91-98`) cuts every 97th byte, not every length. Every cut also fails at `section()`'s `take` before the header decoder runs, so it never exercises the decoder on short input. A better test would fix the section size and CRC around a truncated header body, or cut at every offset.
- `Reader::name` accepts a length of 0 and does not check the trailing NUL. This matches boxcars; a stricter check would catch misframing earlier.

## Checks run
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rl_replay --all-targets -- -D warnings` | pass |
| `cargo test -p rl_replay` | pass: 20 unit, 3 oracle, 1 doctest |
| `check_workspace_deps.py <cargo metadata>` | pass (exit 0) |
| `changes.py check` | "307 fragment(s) ok" |
| `generate_workspace_map.py` | `rl_replay` row identical to the committed map |
| Executed probe (scratch copy) | nested `StructProperty` aborts at 20,000 levels in release and 2,000 in debug; nested `ArrayProperty` aborts at 100,000 levels in release |

**Not run or not applicable:**
- No private corpus is involved; all three samples are committed.
- The physics, tape-bot and PowerShell checks do not apply to this diff.
- `test_check_workspace_deps.py` did not run: my `unittest` call used a file path and failed on module naming before any test loaded, and I did not retry it as `python3 -I -m unittest discover -s .github/scripts`.

**Validation gap:** the corpus run on the game machine is a P3 acceptance step, not P1.

## Verdict
**Changes required.**
