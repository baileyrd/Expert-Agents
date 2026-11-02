<!-- rocket-league/666-r1 run 2, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 257 s, 1155385 tokens, $1.11 -->

# Review of PR 666, Rusty-Mill/rusty_mill: `rb_rl_bridge` takes the game to be the first connection that speaks like it

- **Base:** `f3d03a936e7d9497b5ef315a5be8b30755aef689`
- **Head reviewed:** `fc3a6b0554faabb3baa647cdf2418a388ffb9b89`. I fetched `pull/666/head` and it matches this SHA. The range is one commit, `fc3a6b055`.

**Verdict: changes required.** There are three P1 findings and one P2.

The repo's own premise matters for everything below: the game dials the bridge once, at launch, and sends nothing until a map loads. The README says so at `rb_rl_game_bridge/README.md:76-78`, and so do the PR's own comments. The live census in `RL-LIVE-TEST-2026-10-12.md:112` agrees: 3 `GameSpawn` messages over 3 maps, none at the menu. So **the real game is the oldest silent connection**, and this change treats that kind of connection badly.

## Blocking findings

### F1 (P1): The live-test runner can no longer start a session
- **Where:** `src/bin/rb_rl_bridge.rs:177-179`, together with `rusty_bullet/tools/rl_live_test/rl-lib.ps1:83-87` and `run-live-test.ps1:86-94`.
- **Trigger:** every run of `run-live-test.ps1`.
- **Trace (source-traced, with the binary half executed):**
  1. `Start-RlSession` starts the bridge and launches the game.
  2. It then polls `bridge.out.log` for `serving bots on` for up to 100 × 3 s. If the line never appears it throws `'the game did not connect to the bridge'`.
  3. The menu clicks that load a map (`Start-ExhibitionMatch`) only run after `Start-RlSession` returns.
  4. At the base, `serving bots on` (printed by `Event::Listening` at `rb_rlbot_core/src/server.rs:234`) came right after the TCP connect.
  5. At the head, `Core::run` is only reached after `wait_for_game` returns, which needs the game's first message, which needs a map, which needs `Start-RlSession` to have returned.
  6. The result is a deadlock: every live run now throws after about 300 s.
- **Executed:** I ran the head binary with `--game-port 47811 --port 47812 --no-spawn x/y:0` and held a silent connection open for 4 s. The log showed only `waiting for the game…` and `a connection … is waiting to speak`. `serving bots on` never appeared.
- **Docs:** the fragment says it was verified with a fake game that sent its first message straight away. That does not exercise the real launch order.
- **Fix:** do one of these:
  - Print a readiness line once the bots' listener is up, before `wait_for_game`, and change `rl-lib.ps1:83` to wait for it.
  - Or move the script's wait to after the map loads.

  Add a test that runs the binary against a connection that stays silent and checks the line the script waits for.

### F2 (P1): Eight later connections evict the silent real game, which never redials
- **Where:** `src/link.rs:203-207`, evicting with `candidates.remove(0)`.
- **Trigger:** 8 or more local connections to the game port between the game launching and a map loading. The menu phase lasts as long as the menus are open.
- **Trace (executed):** in `probe_silent_strays_evict_the_game`, the "game" connects, then 8 silent connections arrive.
  - The bridge closes the game's socket: a read returns 0.
  - When the game then sends its first message, `wait_for_game` does not return within 2 s.
  - After the strays leave it returns `Ok(None)`.
  - With the real game, which dials only once, the bridge would lose the game for the whole session.
- **Docs:** the README at `README.md:83` and the fragment at line 7 say the oldest is dropped "so silent connections cannot keep the game out". By the repo's own launch order this policy drops the game first. Those claims are false.
- **Fix:** don't let later arrivals displace an earlier connection that is still open and silent. For example:
  - raise the cap and refuse (close) new arrivals once it is full;
  - or evict the newest silent connection instead of the oldest.

  Correct the README and fragment to match. Add a regression test: game connects first and stays silent, `MAX_CANDIDATES` strays arrive, then the game speaks. It must be found. It fails at the head.

### F3 (P1): Connections that have already closed still count toward the cap and evict live ones
- **Where:** `src/link.rs:197-212`. The accept loop drains the whole backlog and evicts at line 206 before any candidate is checked at lines 217-230.
- **Trigger:** one burst of 8 connect-then-close connections (a port scan or health check) inside one 5 ms pass while the game is waiting.
- **Trace (executed):** in `probe_already_closed_strays_evict_the_game`, the game connects, then 8 connections connect and drop.
  - The notes show `dropped the oldest waiting connection: too many`, followed by eight `closed without a word`.
  - The game's socket is closed, and the call ends `found=false`.
  - Connections that were already dead pushed out the one live one. This is independent of F2: it happens under any eviction policy that runs before dead connections are removed.
- **Fix:** before evicting for a new arrival, run `first_message` over the current candidates and remove the closed and junk ones, or check each new connection before it is counted. The test is the probe above: the game must be found.

### F4 (P2): No test covers the launch order the README documents
- **Where:** `tests/game_finding.rs`.
- **Problem:** every test connects the game last, or has it speak at once. None has the game connect first and stay silent while other connections arrive, which is the real order (`README.md:76`). That is why F2 and F3 pass CI.
- `game_finding.rs` doesn't compile at the base, so it is trivially red there. The fragment says three mutations fail these tests, but none of them covers eviction direction.
- **Fix:** add the F2 and F3 regression tests, plus the binary test from F1.

## Coverage (all 6 changed files)

| File | Classes checked |
|---|---|
| `src/link.rs` | **Framing:** peek of `2 + u16::MAX` with a u16 big-endian length matches `rb_rlbot_wire::frame`, and nothing is consumed, so `RocketLeagueLink` reads the first frame intact. **Decoder:** the check uses the same tolerant `decode_game_packet` the link uses (`rl_link.rs:587`). **Partial reads:** waited for, and tested. **Unbounded work:** see optional improvements. **Stale state:** nothing is cached. **Errors:** see validation gaps. **Eviction:** F2, F3. |
| `src/bin/rb_rl_bridge.rs` | Change of output contract (F1). Timeout message wording (optional improvements). Non-blocking handoff is fine: `set_nonblocking(false)` then `true`. |
| `src/lib.rs` | Re-export only; fine. |
| `tests/game_finding.rs` | F4. Also checked for leaks or ports colliding between tests: binds use port 0, so they don't collide. |
| `README.md` | False claim about oldest-first eviction (in F2). Silence before a map loads matches the repo's evidence. |
| `changes/2026-10-13-…md` | Same false claim (F2). "Run by hand" used the wrong order (F1). The known limitations are stated honestly. |
| `rl-lib.ps1` / `run-live-test.ps1` (unchanged, but call the binary) | Neighbour search found F1. |

Physics, units and replay classes don't apply: there is no physics or replay code in the diff.

## Design question for the owner
- While any connection is waiting, the timeout never runs (`link.rs:233-237`). One stray connection that stays silent, or a game that never loads a map, now keeps `rb_rl_bridge` waiting forever instead of failing after five minutes. This is documented and tested deliberately, but it stops the bridge failing on its own. Is an outer bound wanted?

## Optional improvements
- `first_message` allocates and zeroes 64 KiB per candidate on every 5 ms pass (`link.rs:142`) for as long as the game sits in the menu. Reuse one buffer.
- The inner accept loop (`link.rs:197-215`) has no per-pass budget, so a sustained connect flood starves the check on existing candidates. I don't count it as a finding: memory stays bounded and the loop ends when the flood ends. A budget would still be cleaner.
- The eviction note doesn't say which peer was dropped, so a dropped game is not visible in the log.
- `"the game did not connect within five minutes"` (`rb_rl_bridge.rs:179`) is now printed only after five minutes with nothing connected, possibly long after start, and also after the game was evicted. Rewording it would help.

## Checks performed
- `cargo fmt --all -- --check`: passed.
- `cargo clippy -p rb_rl_game_bridge --all-targets -- -D warnings`: passed. I ran it with `CARGO_TARGET_DIR=$TMPDIR/target` because the repo's `target/` is read-only in this sandbox.
- `cargo test -p rb_rl_game_bridge`: all passed, 104 tests including the 7 in `game_finding`.
- `python3 .github/scripts/changes.py check`: `70 fragment(s) ok`.
- Scratch probes on a `git archive` copy of the head (`tests/zz_probe.rs`): both reproduce F2 and F3. The head binary run reproduces F1.
- Not run, because the diff touches no physics or tape-bot code: physics crates, golden captures and the tape-bot unittest suite.

## Validation gaps
- **Windows-only behaviour:** on Windows, `accept` can return `WSAECONNRESET` for a connection reset before it is accepted. `link.rs:214` and `set_nodelay(...)?` at line 202 would then end `wait_for_game` with `Err`, and the bridge would exit, so one stray connection could still take the bridge down. On Linux, a reset connection in the backlog is accepted normally. This needs a run on Windows with a connect-then-RST client.
- **The game's first message:** it is assumed to decode with the tolerant decoder, and the game is assumed to send nothing at the main menu. A `--record-in` capture timed against the connect, taken from the real game, would settle both. F1 rests on the PR's own statement of the second assumption.
- **Not run:** PowerShell, the game and the plugin. The scripts were read and traced from source.
