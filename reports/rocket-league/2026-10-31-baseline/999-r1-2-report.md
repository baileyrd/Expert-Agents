<!-- rocket-league/999-r1 run 2, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 142 s, 1112016 tokens, $0.96 -->

I'm wrapping up. All the checks I could run are done and the crash is confirmed by running a probe.

# Review: Rusty-Mill/rusty_mill PR 999 — `rl_replay` slice P1 (container, CRC, header)

- **Base:** `712c1c2275f6898009357365175a2498f291dadb`
- **Head reviewed:** `fd33e2baa3e8862cefdac4e670fa3475d0f2f4e2` (fetched `pull/999/head` and confirmed it equals the given head; one commit, `fd33e2baa`)
- **Verdict: changes required.** A crafted replay file under 1 MB kills the whole process.

## What I checked for
The diff only touches the replay format. The input is hostile bytes, so every length, count or loop that the file controls must be bounded before use. Malformed input must return an `Error`, never panic or abort. The PR claims this itself: "Errors fail closed" (`lib.rs:10`, and the change fragment).

I compared each decoder against boxcars 0.11.5, the version the workspace locks:
- **CRC:** polynomial, initial value and final XOR match boxcars' `single_byte_test`.
- **Version field:** read under the same `>865 && >17` condition as boxcars `header.rs:48`.
- **Text:** the ±10 000 limit and NUL dropping match boxcars `core_parser.rs:116` and `parsing_utils.rs`.
- **Windows-1252:** the five unassigned bytes map to C1 controls, the same as encoding_rs.
- **Arrays:** the 25 000 cap matches boxcars `CoreParser::repeat`.
- **Byte properties:** the `ByteProperty`/`None` padding is handled the same way.

Everything that is bounded by bytes is fine. `take` checks the remaining length before slicing, and `len` rejects negative values. Nesting depth is the one thing with no bound.

## Blocking findings

### P0 — Deeply nested header properties overflow the stack and abort the process
- **Where:** `crates/apps/rocket_league/crates/rl_replay/src/header.rs:121-133` (`properties` → `property`), with the recursion at `:154` (`ArrayProperty` → `properties`) and `:173-174` (`StructProperty` → `properties`). There is no depth limit.
- **Trigger:** each nesting level costs about 39 bytes. A level is a key `"k"`, the kind `"StructProperty"`, a u64 size and a struct name `"s"`; or for arrays, `ArrayProperty` with a length of 1. The attacker writes the matching header CRC, which takes one call to the public `rl_replay::crc`, so checking the CRC first gives no protection.
- **Executed:** I built a scratch probe outside the tree that depends on `rl_replay` by path, ran in release mode on an 8 MB main-thread stack, and called `rl_replay::parse`:
  - 1 000 and 10 000 `StructProperty` levels returned `Err(Truncated …)` as they should.
  - 20 000 levels (a 780 KB file) printed `thread 'main' has overflowed its stack / fatal runtime error: stack overflow, aborting`. 40 000, 60 000, 100 000 and 1 000 000 levels did the same.
  - Nested `ArrayProperty` at 40 000 and 100 000 levels aborted the same way.
  - A stack overflow aborts the process. `catch_unwind` cannot catch it, so one uploaded or corpus replay kills the whole host. On a 2 MB worker thread (a test thread or a tokio worker) the limit is roughly four times lower.
  - Real headers nest two or three levels deep: a `PlayerStats` array of structs.
- **Not a mitigation:** boxcars has the same unbounded `parse_rdict` recursion. That does not lower the grade. This crate is first-party, its job is to replace boxcars, and it claims to fail closed.
- **Required correction:** pass a depth counter through `properties`/`property`, or make the parse iterative with an explicit stack. Cap the depth at a small constant, for example 16, and return a new `Error` variant (or `BadLength { what: "property nesting", .. }`) with the offset.
- **Regression test:** a unit test through `rl_replay::parse` on a crafted file nested well past the cap (for example 100 000 `StructProperty` levels) must return `Err`. Run it in a thread with a deliberately small stack (`std::thread::Builder::new().stack_size(256 * 1024)`) so that removing the guard aborts the test.

### P2 — The "fail closed" claim is false at this head
- **Where:** `lib.rs:10-11`, `error.rs:1-2` and the change fragment `changes/2026-10-24-rl-replay-parser-p1.md:8` ("Errors fail closed and name what was being read and the byte offset").
- **Problem:** the P0 trace above contradicts all three. No test feeds the parser hostile nesting. The tests do cover truncation, oversized text, a negative length and a CRC flip, but not depth.
- **Required correction:** fixing the P0 makes the claim true. The test above is the coverage that is missing today.

## Design questions
None. The new version floor of 868.23.8 matches the oldest committed sample, and boxcars treats a missing net version as 0 the same way (`network/mod.rs:43`).

## Optional improvements
- **Version gate:** `header.rs:97` compares `(engine, licensee, net.unwrap_or(0))` as a tuple. It therefore accepts triples the game never produced, such as `(869, 10, None)` or `(868, 27, 0)`. That is harmless for the header, but P2 should decide whether to accept them.
- **Trailing NUL:** `Reader::name` and `Reader::text` drop the last byte without checking that it is a NUL. This is the same as boxcars, so it keeps oracle parity, but a strict parser could reject it.
- **P2 scope:** the version range now includes boxcars' `VersionTriplet(868, 24, 10)` gate on the network-frame trailer (`frame_decoder.rs:408`). `419a` is on one side of it and `42f2` on the other. Worth noting in the P2 slice; the vector and quaternion gates (net ≥ 7) are all below the floor.
- **Credit:** `THIRD_PARTY_NOTICES.md` does not yet credit boxcars, which the scope doc requires where we follow it closely. That can wait for P2's bit encodings.

## Checks performed
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rl_replay --all-targets -- -D warnings` | pass (offline, using a cached cargo home and a target dir under `$TMPDIR`; the default cargo home and target are read-only here) |
| `cargo test -p rl_replay` | pass: 20 unit tests, 3 oracle tests (all three committed samples equal boxcars), 1 doc test |
| Red at base | The crate is new, so every test is absent at base. None of them guards the P0. |
| `python3 .github/scripts/changes.py check` | pass (307 fragments ok) |
| `check_workspace_deps.py` | **not run**: `cargo metadata` for the whole workspace needs crates that are not cached offline, and the sandbox cargo home is read-only. The manifest declares no normal or build dependencies, which matches the `"rl_replay": {}` pin. |
| Probe (executed) | Stack-overflow abort at ≥ 20 000 nested `StructProperty` or `ArrayProperty` levels, as described above |

**Validation gaps:** I did not test against the private corpus, which is not present here. No physics, bridge or tape-bot code is touched.

**Verdict: changes required.**
