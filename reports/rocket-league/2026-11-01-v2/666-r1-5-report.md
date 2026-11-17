<!-- rocket-league/666-r1 run 5, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 187 s, 534234 tokens, $0.71 -->

I found two ways a stray connection can make the bridge lose the real game. Both are confirmed by probe tests I ran. The verdict is **changes required**.

**Pinned:** Rusty-Mill/rusty_mill PR #666, base `f3d03a936e7d9497b5ef315a5be8b30755aef689`, head `fc3a6b0554faabb3baa647cdf2418a388ffb9b89`. The fetched `pull/666/head` matches the head SHA. The PR is one commit, `fc3a6b055`.

## Blocking findings

**1. P1: The real game gets evicted as "oldest" by 8 later silent connections.** `src/link.rs:204-207` (in `rb_rl_game_bridge`)
- **Why it happens:** the README says the game dials once at launch and sends nothing until a map loads (`README.md:76`, `link.rs:186`). In the 2026-10-12 live run the first `PlayerSpawn` came at game time 64.7 s, so the game sat silent for about a minute. In that window the game is the oldest waiting connection, and `candidates.remove(0)` drops it as soon as `MAX_CANDIDATES` (8) other connections arrive.
- **What follows:** the game never dials again. The 8 strays are still waiting, so `idle_since` keeps being reset (`link.rs:233-236`) and the five-minute limit never fires. The bridge waits forever.
- **Executed:** probe `probe_silent_game_then_eight_strays` in a scratch copy. The game connects and stays silent, 8 strays connect, then the game sends a valid first message. The result is `Err(Timeout)` after 2 s, so the game was never taken. The evidence requires it to be taken.
- **Fix:** the eviction rule has to fit how the game actually connects: it is early, silent and dials once. Since the bridge must start before the game, one option is to keep the earliest connection and refuse or close later arrivals past the cap. Another is to tell the connections apart some other way. The README, change fragment and doc comment should then say which connections can still keep the game out.
- **Regression test:** the probe above, with the game connecting first and staying silent until the strays have arrived.

**2. P1: `swap_remove` breaks the age order, so "oldest" can be the newest.** `src/link.rs:221` and `:228`
- **Why it happens:** when a junk or closed connection is dropped, `swap_remove` moves the newest connection into the dropped one's slot. If that slot is index 0, the next eviction drops the newest connection, not the oldest.
- **Executed:** probe `probe_swap_remove_makes_newest_oldest`. The connections are junk, then 6 silent strays, then the silent game (newest). The junk is dropped, then 2 more strays connect. The game is evicted ahead of 6 older strays, and the result is `Err(Timeout)`.
- **Impact:** the same as finding 1 (game lost, bridge waits forever), from a different trigger. Fixing finding 1's rule does not fix this unless the ordering is kept.
- **Fix:** remove with `candidates.remove(index)` or use a `VecDeque`, so the list stays in arrival order.
- **Regression test:** the probe above.

**3. P2: A documented guarantee and its test do not match how the game connects.**
- **Where:** `README.md:82-84`, the change fragment ("the oldest dropped first so silent connections cannot fill the places"), the doc comment at `link.rs:183-185` and the comment at `link.rs:204-205`. All of them say silent connections cannot keep the game out.
- **The problem:** findings 1 and 2 show that this is false for the game as the same README describes it.
- **The test gap:** `silent_connections_cannot_fill_the_places_and_keep_the_game_out` (`tests/game_finding.rs:159`) models a game that arrives last and speaks at once. The real game arrives early and stays silent, so the test does not cover the case that matters.
- **Fix:** add a test where the game connects first and stays silent. Correct the claim in the README, change fragment and comments to match whatever rule is chosen.

**4. P2: The accept loop has no per-pass limit.** `src/link.rs:198-215`
- **Source-traced, not executed:** the inner `loop` only exits on `WouldBlock`. Each accepted connection costs two setsockopt calls, a `format!` and a `println!` (slow on a Windows console).
- **Trigger:** a local process connecting faster than that keeps the function in the accept loop. The candidates are never checked, so even a game that has already spoken is never seen.
- **Fix:** cap how many connections are accepted per pass (for example `MAX_CANDIDATES`), then go on to check the candidates.
- **Regression test:** a thread that connects in a tight loop while the game has already sent its first message. The game must still be found within a bound.

## Coverage
| File | What I checked |
|---|---|
| `changes/2026-10-13-…md` | Claims checked against code and probes (finding 3). "Three mutations" and "run by hand" can't be verified here. |
| `rb_rl_game_bridge/README.md` | Checked against code (finding 3). |
| `src/bin/rb_rl_bridge.rs` | Only caller of `wait_for_game`. Returned stream handling and timeout message checked. |
| `src/lib.rs` | Re-export only. |
| `src/link.rs` | Framing (peek leaves the message unread, partial frames wait, length ≤ 65535), bounded memory (≤8 candidates), loops (finding 4), stale or closed connections, error paths, eviction (findings 1–2). |
| `tests/game_finding.rs` | Coverage (finding 3). It cannot compile at base because `wait_for_game` and `MAX_CANDIDATES` don't exist there; I traced this from source and did not build base. |

Physics, verification and replay areas are not touched by this PR.

## Design questions
- **No deadline while a connection is open.** The five-minute limit only runs while nothing is connected. One stray connection that stays open makes the bridge wait forever with no error. A junk connection every few minutes also keeps restarting the timer (`link.rs:233-236`). This is documented, but it hides a failed launch.
- **No authentication.** A local process that sends a game-format message first is taken for the game. This is a stated limitation.

## Optional improvements
- `link.rs:142` allocates and zeroes a 64 KiB buffer on every check of every candidate, every 5 ms. With 8 candidates that is about 100 MB/s of zeroing. Peeking the 2-byte header first and sizing the buffer from it, or reusing one buffer, would avoid this.

## Checks run (in a scratch copy of the head, separate target directory)
- `cargo fmt --all -- --check`: passed.
- `cargo clippy -p rb_rl_game_bridge --all-targets -- -D warnings`: passed.
- `cargo test -p rb_rl_game_bridge`: all passed, including the 7 new tests in `game_finding`.
- `python3 .github/scripts/changes.py check`: 70 fragments ok.
- Two probe tests: both reproduce the game being lost (findings 1 and 2).
- Not run: the physics crates and tape-bot Python tests, which this PR doesn't touch.

## Validation gaps
- **Windows accept errors.** If a connection resets before it is accepted, Windows may report an error from `accept` or `set_nodelay`. The code at `link.rs:201-202,214` returns that error, which ends the bridge. `wait_for_connection` already behaved this way at base. Only a Windows run with a peer that resets right after connecting can settle it.
- **Large first message.** The peek needs the whole first message (up to 65,537 bytes) in the receive buffer. If the game's first message is bigger than that buffer, the bridge would wait forever. The size of the real first message hasn't been measured; a live capture would settle it.
- **Real game's first message.** Whether it passes the decoder hasn't been checked against the game; the change fragment says so itself.

**Verdict: changes required.**
