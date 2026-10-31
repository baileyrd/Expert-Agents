<!-- rocket-league/999-r1 run 4, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 119 s, 637513 tokens, $0.66 -->

I found one blocking defect in PR 999: a crafted replay header crashes the whole process. Changes are required.

**Pinned:** Rusty-Mill/rusty_mill PR 999. Base `712c1c2275f6898009357365175a2498f291dadb`. I fetched `pull/999/head` and it resolves to `fd33e2baa3e8862cefdac4e670fa3475d0f2f4e2`, the head given and the same as the checkout. It is one commit, "rl: first-party replay parser, slice P1 (container, CRC, header)".

## Blocking findings

**P0: deeply nested header properties overflow the stack and abort the process (executed)**
- **Where:** `crates/apps/rocket_league/crates/rl_replay/src/header.rs:154-159` and `:171-175`. `properties()` calls `property()`. For `StructProperty` and `ArrayProperty`, `property()` calls `properties()` again with no depth limit. Both section CRCs come from the public `rl_replay::crc`, so anyone can write a file that passes the CRC checks.
- **Trigger:** a header whose properties are a chain of `StructProperty` values, each holding the next. Each level costs 48 bytes (key `"k"`, the kind name, the u64 size, struct name `"s"`), followed by one `None` per level. An `ArrayProperty` chain with length 1 at each level behaves the same way.
- **Executed:** I ran a probe in a scratch copy that calls the public `rl_replay::parse` and spawns threads with fixed stack sizes:

  | Nesting depth | File size | 2 MiB stack | 8 MiB stack |
  |---|---|---|---|
  | 1,000 | 48 KB | `Ok` | `Ok` |
  | 5,000 | 240 KB | `fatal runtime error: stack overflow, aborting` (SIGABRT), release and debug builds | `Ok` |
  | 20,000 | 960 KB | abort | abort |
  | 100,000 struct or array levels | — | — | abort |

  Every crafted file is smaller than a normal replay (`42f2`'s content section alone is about 1.3 MB). A stack overflow is an abort, not a panic, so `catch_unwind` cannot contain it. It takes down whatever process parses the file.
- **What the code should do:** return an error. The change fragment says "Errors fail closed", and the review rules require malformed input to return an error rather than panic or hang. boxcars 0.11.5 has the same unbounded recursion (`boxcars-0.11.5/src/header.rs`, `parse_rdict` → `array_property` / `StructProperty`). Copying the oracle's limits elsewhere (`MAX_ARRAY_LEN`, `MAX_TEXT_CHARS`) doesn't excuse leaving this one out. Real headers nest only a few levels (Array → row → Struct).
- **Required correction:**
  - Pass a depth counter through `properties()` and `property()`, and reject anything past a small cap (for example 16) with a new error such as `Error::TooDeep { offset }` or a `BadLength { what: "property nesting" }`.
  - Add a public-API test with a crafted file that is CRC-valid and nested past the cap (for example 10,000 levels, about 480 KB). It must return `Err` on the default 2 MiB test-thread stack; at this head it aborts the test binary.
  - Add a second test that nests exactly up to the cap and still parses, so the limit doesn't refuse real replays.

## Checked and found sound
- **Bounds:** every read goes through `Reader::take`, which checks against `remaining()`. Name lengths are rejected when negative. Text lengths are capped at ±10,000 characters, so `unsigned_abs`/`i32::MIN` is safe. Arrays are capped at 25,000 entries and never preallocate from the declared count; memory grows only in proportion to the bytes actually read. The section size is checked by `take` before the CRC runs.
- **CRC:** polynomial, initial value, final XOR and table match boxcars, and boxcars' single-byte vector passes.
- **Text decoding:** Windows-1252 matches WHATWG, including the five C1 passthroughs.
- **Header layout:** the net-version gate (`> 865 && > 17`), dropping a `ByteProperty` whose enum type is `None`, and `Bool == 1` all match boxcars. The u64 property size equals boxcars' u32 plus 4 ignored bytes.
- **`MIN_VERSION = (868, 23, 8)`:** matches boxcars' own `VersionTriplet(868, 23, 8)` gate (`network/attributes.rs:492`) and the oldest sample. The scope-doc change correctly says the old net-10 floor would have refused `419a`.
- **Docs:** the change fragment, scope doc, `WORKSPACE-MAP` row and the dependency pin agree with the code, apart from the "fail closed" claim covered by the finding.

## Design questions
None.

## Optional improvements
- `parse` ignores any bytes after the content section, and no test documents that this is intended.
- `name()` accepts a length of 0 (an empty key). This is harmless, but a short test would pin the behaviour.

## Checks
- `cargo fmt --all -- --check`: passed.
- `cargo clippy --offline -p rl_replay --all-targets -- -D warnings`: passed.
- `cargo test --offline -p rl_replay`: passed (20 unit, 3 oracle and 1 doc test). The three oracle samples are committed, so nothing was skipped for lack of the private corpus.
- `python3 .github/scripts/changes.py check`: 307 fragments ok.
- `check_workspace_deps.py` on fresh `cargo metadata`: exit 0.
- **Environment:** `~/.cargo` and the default target directory are read-only in this sandbox, so cargo ran with `CARGO_HOME` and `CARGO_TARGET_DIR` under `$TMPDIR`, offline.
- **Red at base:** the crate is new, so every test is absent at base. None of them covers the nesting defect.
- **Not run:** the physics and tape-bot suites, because the diff touches no physics, bridge or tape-bot code.

## Validation gaps
None. This is a pure parser, and the evidence the slice needs (the committed samples) was available.

## Verdict
**Changes required.**
