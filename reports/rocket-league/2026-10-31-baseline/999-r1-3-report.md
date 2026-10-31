<!-- rocket-league/999-r1 run 3, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 123 s, 854723 tokens, $0.81 -->

**Review of PR 999, Rusty-Mill/rusty_mill: the first replay parser slice (P1)**

- **Base:** `712c1c2275f6898009357365175a2498f291dadb`
- **Reviewed head:** `fd33e2baa3e8862cefdac4e670fa3475d0f2f4e2` (the fetched `pull/999/head` matches this SHA)

The PR adds the new crate `rl_replay`, which reads a replay's container, checks both section CRCs and decodes the header. It also changes the scope doc, the workspace dependency check, `Cargo.toml` and the workspace map, and adds a change fragment. It touches only the hostile-bytes axis; nothing in the physics, verification or bridge code changes.

### Blocking findings

**P0: a small crafted replay crashes the whole process through unbounded recursion while parsing the header.**
- **Where:** `crates/apps/rocket_league/crates/rl_replay/src/header.rs:387-398` (`properties`), `:410-420` (the `ArrayProperty` branch) and `:437-440` (the `StructProperty` branch).
- **Cause:** `properties` calls `property`, and `property` calls `properties` again for every struct and every array row. Nothing limits how deep that goes. Each extra level costs the attacker about 48 bytes. The CRC is no protection, because the attacker can compute it; `rl_replay::crc` is public.
- **Trigger (executed):** I wrote a scratch probe outside the tree that calls the public `rl_replay::parse` with a valid container and correct CRCs. It runs on a thread with a 2 MiB stack, which is the default for Rust spawned threads, test threads and tokio workers. Results:

  | Nesting | File size | Result |
  |---|---|---|
  | 1,000 `StructProperty` levels | 48 KB | `Ok` |
  | 5,000 `StructProperty` levels | 240 KB | `has overflowed its stack`, then `fatal runtime error: stack overflow, aborting`, exit 134 |
  | 100,000 `ArrayProperty` levels, one row each | 4.8 MB | the same abort |

  A stack overflow is not a Rust panic. It cannot be caught, so anything parsing the replay is killed, even if it wraps the call in `catch_unwind`. The crate's own promise that "Errors fail closed" (`lib.rs:10-11`, repeated in the change fragment) is false for this input. boxcars recurses the same way, but that does not excuse a parser whose stated purpose is to replace it.
- **Required fix:** pass a depth through `properties` and `property`, and return a new error (for example `Error::TooDeep { offset }`, or `BadLength` with `what: "property nesting"`) once the depth passes a small cap. Real headers nest about 3 levels (PlayerStats array, then row, then the struct inside the row), so a cap such as 32 is ample.
- **Test that should go red without it:** a unit test in `header.rs`, or an integration test through `rl_replay::parse`, that builds about 100,000 nested `StructProperty` levels with valid CRCs and asserts an `Err`. Run it on a spawned thread with a 2 MiB stack so that, without the fix, it aborts rather than passing by luck on an 8 MiB main stack. The crate does not exist at the base, so every test here is red at base by construction.

### Design questions
None.

### Optional improvements
- `tests/boxcars_oracle.rs:61-67` (`a_cut_sample_fails_cleanly_at_every_header_length`) only cuts inside the header section. Every cut fails at the section `take` in `lib.rs:659`, so the header decoder never sees a short buffer. A test that keeps a valid container but feeds a truncated or mutated header body, with the CRC recomputed, would actually exercise `header::parse`.
- Add a small mutation loop through the public API: flip bytes, recompute the CRC, assert it returns without a panic or abort. That covers the hostile-bytes promise generally. The recursion depth case above still needs its own test.

**Checked and ruled out** (each against boxcars 0.11.5, the locked version):
- **Header parsing matches boxcars.** Same net-version gate (`header.rs:358` vs boxcars `header.rs`, "engine > 865 and licensee > 17"); same ignored 8-byte size field; same handling of a `ByteProperty` whose type is `None` (one padding byte, then dropped); same UTF-8 name decoding and same 10,000-character text cap.
- **Text decoding matches.** UTF-16 and Windows-1252 match encoding_rs under the WHATWG mapping, including the five unassigned bytes in 0x80–0x9F.
- **Array length** is capped at 25,000, as in boxcars. `collect` into `Result<Vec>` does not pre-reserve space, so a large count cannot force a large allocation.
- **Reads are bounds-checked.** Every read goes through `Reader::take`, so nothing can index past the end. Negative lengths return `BadLength`. Loops are bounded by the input size: each array row needs at least 9 bytes.
- **The version floor is justified.** The floor 868.23.8 is boxcars' last encoding gate at or below it (`network/attributes.rs:492`). The scope doc's correction ("net 10 would have refused `419a`") agrees with the oracle test, which passes.

### Checks performed
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rl_replay --all-targets -- -D warnings` (cleaned first so it rebuilt) | pass |
| `cargo test -p rl_replay` | pass: 20 unit tests and 3 oracle tests on `42f2`, `419a` and the `rb_replay_ingest` sample |
| `python3 .github/scripts/changes.py check` | pass (307 fragments ok) |
| `check_workspace_deps.py` and `check_workspace_layers.py` on `cargo metadata --all-features`, as CI runs them | both exit 0 |
| Recursion probe (scratch, executed) | aborts at 5,000 levels (240 KB file), see the finding |

Cargo's default registry cache (`~/.cargo`) is read-only in this sandbox, so I pointed cargo at an existing writable registry. Every crate named in `Cargo.lock` resolved, and the checks ran against it.

**Validation gaps:** the private corpus was not run; P1 does not use it. No game, PowerShell or plugin paths are involved.

### Verdict
**Changes required.** The one blocking finding is P0: a crafted replay of about 240 KB crashes the parsing process with an uncatchable stack-overflow abort.
