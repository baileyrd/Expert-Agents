<!-- rocket-league/666-r1 run 3, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 171 s, 581073 tokens, $0.70 -->

**PR 666 review: Rusty-Mill/rusty_mill.** The PR makes `rb_rl_bridge` take a connection as the game only once its first message decodes as a game message. Changes are required: one blocking finding and two smaller ones.

- **Base:** `f3d03a936e7d9497b5ef315a5be8b30755aef689`
- **Head:** `fc3a6b0554faabb3baa647cdf2418a388ffb9b89`. I fetched `pull/666/head` and it matches this SHA. The PR is one commit.
- **Scope:** wire and bridge only (`rb_rl_game_bridge`: `link.rs`, the bridge binary, README, new `tests/game_finding.rs`, one change fragment). No physics, verification or replay code changes.

## What must hold (predicted before reading the PR text)
- **Game socket transport** (`RL-GAME-SOCKET-FORMAT.md:13-18`): one connection, framed as a big-endian u16 length then up to 65535 bytes. Whatever chooses the game must not read away its first frame, must not leave a half-read stream, and must bound all work driven by the peer.
- **Lifecycle** (README "Start `rb_rl_bridge` before the game"): "The game dials it once, at launch." So any rule that closes a connection must never close the real game's, because it will not dial again.
- **New constant `MAX_CANDIDATES = 8`** is a count of connections. There is no recording or reference behind it; it is a design choice.

## Blocking findings

### 1. P0: a silent real game is closed as "the oldest", and the bridge then waits forever
**Where:** `src/link.rs:203-207` (`candidates.remove(0)`), with `src/link.rs:233-236`. The claim is repeated at `src/link.rs:184-186`, `README.md:83-84` and the change fragment.

**Trigger:** the order the README tells users to follow. The bridge starts, then the game dials once at launch. By the PR's own account the game then stays silent until a map loads. If 8 more local connections reach the game port during that silence (a port scanner, health checks, a leftover process reconnecting), the code closes the oldest, which is the game. The game never dials again.

Nothing ever ends the wait: `idle_since` is reset on every pass while any candidate is waiting (line 234), so the five-minute limit never runs. `rb_rl_bridge` hangs with no error.

**Executed** (probe in a scratch copy, not the reviewed tree):
1. Ran `wait_for_game(300 s)` in a thread.
2. The game connected, then 100 ms later `MAX_CANDIDATES` strays connected.
3. 100 ms after that, the game wrote a valid framed `GameSpawn`. The write returned `Ok(())`.
4. Result: `wait_for_game still waiting 3 s after the game spoke; the game was dropped`.

At the base, `wait_for_connection` returned the first arrival, so in this order the game was found. This is a regression, and it is reachable in the documented startup order. The claim that silent connections "cannot keep the game out" holds only when the game arrives last. The single eviction test (`silent_connections_cannot_fill_the_places_and_keep_the_game_out`) covers only that order.

**Required correction:** use a rule that keeps the game in both orders. For example, pin the earliest connection and rotate only the others, or don't close silent connections at all and bound by a much larger cap. Also restore some limit on the overall wait while strays are holding it. Then fix the doc comment, README and fragment to say which arrival orders are covered and which are not.

**Regression test:** add the probe above to `tests/game_finding.rs` as `a_silent_game_that_arrived_first_survives_strays` (game connects, then `MAX_CANDIDATES` silent strays, then the game speaks, and `wait_for_game` must return it). It is red at the head. Keep the existing "game arrives last" test green.

### 2. P2: the core premise ("the game says nothing until a map loads") has no evidence, and the committed capture points the other way
**Where:** `src/link.rs:186`, `src/bin/rb_rl_bridge.rs:175-176`, `README.md:82-83`, change fragment.

**Source-traced:** the only live recording of the game's opening traffic is `tests/fixtures/live_freeplay_slice.bin`. Its first frames measure 36, 36, then 2016 bytes. `tests/live_capture.rs:38` describes them as "The two idle menu messages, then the map loads in one big message". So the game does send messages with no map loaded. The slice starts "just before the map loaded", so no capture shows whether the game is silent from launch.

This premise justifies both the unlimited wait for silent connections and the eviction design in finding 1. The fragment's line "Not run against the real game" covers the decoder, not this premise.

**Required correction:** describe the silence as unmeasured in all four places, or cite a capture. Settling it needs a live run: record with `rb_rl_game_probe --record` from game launch through the menu, noting when the first frame arrives.

### 3. P2: the fragment and README claim more than the code does
The fragment says "the oldest dropped first so silent connections cannot fill the places", and `README.md:83-85` says "cannot keep the game out". Finding 1 shows that is false whenever the game connects before the strays. Correct both together with finding 1.

## Design questions
- **No time limit while any connection waits.** With `--launch`, if the game crashes or never dials while one stray connection stays open (even one left over from an earlier run), the bridge waits forever and never prints "did not connect within five minutes". This is documented, but it replaces a failure the user can see with a silent hang. Should there be an overall cap, at least when `--launch` is used?
- **Not authentication, by design.** Anything that sends a frame the tolerant decoder accepts (for example an empty `GamePacket`) is taken as the game. The PR documents this. Checking the peer process's owner, or the PID when the bridge launched the game itself, would close the gap.

## Optional improvements
- `first_message` allocates and zeroes a 65,537-byte buffer for every candidate on every 5 ms pass. With 8 candidates that is about 100 MB/s of memset. Allocate one buffer per `wait_for_game` call and reuse it.
- A first frame larger than the socket receive buffer (up to 65,537 bytes, near Windows' default window) could never be peeked whole, so that connection would stay "waiting" forever. Live first frames so far are at most 2,016 bytes, so this is only a note, not a finding.
- One `set_nonblocking` or `set_nodelay` failure on a stray's accepted socket aborts the whole wait (`?` at lines 201-202). Dropping that candidate would be more robust.

## Checks run
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` (scratch copy of head) | pass |
| `cargo clippy -p rb_rl_game_bridge --all-targets -- -D warnings` (re-run after touching the crate so it was actually rechecked) | pass |
| `cargo test -p rb_rl_game_bridge` | pass: lib 25, bins 3/5/3, bridge 7, census 7, game_finding 7, golden 7, live_capture 11, real_core 2, real_link 15, robustness 12 |
| `python3 .github/scripts/changes.py check` | pass (70 fragments ok) |
| Eviction probe (finding 1), scratch copy | reproduces the hang |
| Fixture frame sizes (`live_*.bin`) | 36, 36, 2016 …; max 2016 |

- **New tests at the base:** `tests/game_finding.rs` can't compile there because `wait_for_game` and `MAX_CANDIDATES` don't exist yet, so all seven are red at the base. None of them covers the game-first order from finding 1.
- **Not run:** the fragment's "three mutations" and the by-hand binary run. I did not reproduce them.
- **Not applicable:** the physics, verification and tape-bot suites; the diff doesn't touch them. Nothing touched here needs the private corpus.

## Validation gaps
These need the live game, Windows or the plugin:
- Whether the game sends anything between dialling at launch and loading a map.
- Whether the game's real first frame decodes with `decode_game_packet`. Recorded menu and map frames do decode, but the very first frame after launch is not in any capture.

## Verdict
**Changes required.**
