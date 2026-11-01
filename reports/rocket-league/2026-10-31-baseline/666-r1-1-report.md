<!-- rocket-league/666-r1 run 1, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 182 s, 409268 tokens, $0.64 -->

# Review of PR 666, Rusty-Mill/rusty_mill: changes required

- **Base:** `f3d03a936e7d9497b5ef315a5be8b30755aef689`
- **Reviewed head:** `fc3a6b0554faabb3baa647cdf2418a388ffb9b89`. I fetched `pull/666/head` and it matches this SHA.
- **Diff:** one commit, `rb_rl_bridge: the game is the connection that speaks like it`. It touches `link.rs`, `bin/rb_rl_bridge.rs`, `lib.rs`, the README, the new test `tests/game_finding.rs`, and the change fragment.

**Verdict: changes required.** The new eviction rule drops the real game's only connection when other connections arrive before the map loads (P0). Separately, "drop the oldest" does not drop the oldest connection (P2).

## What must hold (predicted before reading the PR text)

- **Who dials:** the game connects to the bridge once, at launch, and sends nothing until a map loads. Sources: the crate README ("The game dials it once, at launch") and `RLBOT-BRIDGE-DLL.md` §2 (`TcpMessenger.WaitForConnectionAsync`, one connection). The game cannot redial, so any rule that closes a silent connection can close the game for good.
- **Framing:** a `u16` big-endian length followed by a FlatBuffers payload (`rb_rlbot_wire/src/frame.rs:9-24`). Looking at the first frame must not consume it, so the link still reads it.
- **Hostile peers:** a count of silent connections must stay bounded, and it must not be able to destroy or starve the real game.
- **New constant:** `MAX_CANDIDATES = 8` is a count of connections. Nothing in a capture or reference backs it; it is a policy choice.

## Blocking findings

### 1. P0: silent connections that arrive after the silent game evict it permanently, and the bridge then waits forever
**Where:** `crates/rb_rl_game_bridge/src/link.rs:204-207`, plus the claims in `README.md:83-84` and the change fragment.

**Trigger:** the order the docs describe. The game dials at launch and stays silent while the map loads. Then 8 more local connections arrive: a port scanner, a health check, or a hostile local process. They can arrive at any time before the map loads.

**Failure (executed, probe `probe_silent_game_first_then_squatters`):**
- The game connects first and is silent. Eight silent connections follow.
- On the 9th connection, `candidates.remove(0)` closes the game's socket. The note reads "dropped the oldest waiting connection: too many".
- When the game later writes its first message, the write succeeds into a socket the bridge has already dropped.
- `wait_for_game` (timeout 1 s) had not returned after 3 s. It cannot return while the squatters stay connected, because the timeout only runs when nothing is waiting.
- The game never dials again, so the session is lost and `rb_rl_bridge` hangs.

**Regression from base:** at base, `wait_for_connection` took the first arrival, so a game that dialled first was always taken. This PR breaks that ordering.

**Second trigger (executed, probe `probe_spoken_game_evicted_in_one_accept_drain`):**
- The game has already sent a valid first message.
- Before the bridge's next pass, 8 more connections are queued.
- The accept loop (`link.rs:198-215`) drains every pending connection and evicts as it goes, before `first_message` ever looks at the game. The game is closed and the wait times out.

**Docs this makes false:**
- README:83-84 says "the oldest dropped first so silent connections cannot keep the game out".
- The fragment says "silent connections cannot fill the places".

**Test gap:** `silent_connections_cannot_fill_the_places_and_keep_the_game_out` only covers a game that arrives last and speaks at once. That is the one order the eviction rule favours.

**Required correction:**
- Never close a connection that might be the game because of a count. The game is known to dial early (the README says to start the bridge before the game).
  - Prefer refusing new arrivals once full, rather than evicting old ones.
  - Use a much larger cap, bounded by file descriptors.
  - In every case, classify every candidate with `first_message` before any eviction decision.
- Fix the README and the fragment to match.

**Regression tests that go red at head:**
- The game connects silently, then `MAX_CANDIDATES` or more silent connections arrive, then the game speaks. The game must be returned.
- The game speaks, then `MAX_CANDIDATES` connections queue before the first poll. The game must be returned.

### 2. P2: `swap_remove` breaks the age order, so "drop the oldest" drops whichever connection was moved to the front
**Where:** `crates/rb_rl_game_bridge/src/link.rs:228` (also 221), together with `link.rs:206`. The doc comment at `link.rs:181-187` and README:83 say the oldest is dropped.

**Failure (executed, probe `probe_swap_remove_breaks_oldest_first`):**
1. Connections J, S1–S6 and G (the game, newest) are waiting.
2. J sends a junk frame. `swap_remove(0)` moves G to index 0.
3. Two more silent connections arrive. On the second, `remove(0)` evicts G, even though S1–S6 are older.
4. G's first message is then lost and the wait times out.

**Required correction:** keep arrival order when removing, using `Vec::remove` or a `VecDeque` with `retain`. Or record each connection's accept time and evict by that.

**Regression test:** the sequence in steps 1–4 above, asserting that G is returned or that S1 is the connection evicted.

## Design questions (not counted as defects)

- **One idle connection still stops the five-minute limit.** It turns the timeout off indefinitely (executed: `probe_one_idle_connection_disables_timeout`, a 200 ms timeout had not fired after 3 s). The bridge then never prints "the game did not connect within five minutes" (`bin/rb_rl_bridge.rs:178-179`). This hang already existed at base: the squatter was adopted and polled forever. So the PR does not introduce it, but it does not close it either. If ADR-0084 ("live tests fail closed") is meant to apply here, the owner should choose a bound, for example a total deadline, or ending the wait when the spawned `GameLaunch` process exits. Note that ADR-0084 does not exist in this tree (`rusty_bullet/docs/adr` has no 0084 file).

## Optional improvements

- `first_message` (`link.rs:142`) allocates 64 KiB for each candidate on every 5 ms poll, up to about 100 MB/s of allocation churn with 8 candidates. Reuse one buffer.
- The accept drain (`link.rs:198-215`) has no per-pass budget, so a sustained localhost connection flood starves classification.
- `first_message` never returns `Err`, so the `io::Result` around it is noise.

## Checks run

All run in a scratch clone at the head, with `CARGO_TARGET_DIR` in `$TMPDIR`.

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rb_rl_game_bridge --all-targets -- -D warnings` | pass |
| `cargo test -p rb_rl_game_bridge` | pass, including the 7 new `game_finding` tests |
| `python3 .github/scripts/changes.py check` | pass, 70 fragments ok |
| Reviewer probes (`tests/review_probe.rs`, scratch only, then removed) | 4 probes, all reproduce the failures described above |

- **Red at base:** `tests/game_finding.rs` does not compile at base, because `wait_for_game` and `MAX_CANDIDATES` are new. None of these new tests covers either failure order in finding 1.
- **Not run:** physics, verify and tape-bot checks. The diff touches none of them.
- I did not read `AGENTS.md` or `CONTRIBUTING.md` in full for this bridge-only change.

## Validation gaps (need the live game or Windows)

- **First message decoding:** whether the real game's first message after map load decodes with the tolerant `decode_game_packet`. Settle it with a recorded first frame from a live game socket capture.
- **Windows `peek`:** the whole first frame (up to 65,537 bytes) must fit in the receive buffer, or `peek` never sees it complete and the connection stays "silent". Settle it by measuring the first-frame size from a live capture against Windows' default `SO_RCVBUF`.
- **The fragment's hand-run claims:** the manual run with the built binary and the "three mutations" result were not reproduced here.
