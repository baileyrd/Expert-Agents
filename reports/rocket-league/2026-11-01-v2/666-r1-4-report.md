<!-- rocket-league/666-r1 run 4, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 274 s, 1070220 tokens, $1.16 -->

I'm requesting changes on PR 666. Three defects, each shown by a probe test I ran: the new code can close the real game's connection, and the README and change fragment promise behaviour the code does not deliver.

- **Repository:** Rusty-Mill/rusty_mill, PR 666
- **Base:** `f3d03a936e7d9497b5ef315a5be8b30755aef689`
- **Head reviewed:** `fc3a6b0554faabb3baa647cdf2418a388ffb9b89`. The fetched `pull/666/head` matches it; the PR is one commit.

## Blocking findings

### 1. P1: eight later connections push out a silent game, and the bridge closes it
**Where:** `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/link.rs:203-207`. The claim it breaks is at `README.md:83-85` and in the change fragment, line 7.

**Trigger:** the game connects at launch, which is the documented order (the game dials once, at launch: `README.md:76`, `rb_rl_bridge.rs:15`). It stays silent until a map loads. During that time, `MAX_CANDIDATES` (8) other connections arrive.

**What happens (executed, `probe_silent_game_evicted_by_later_strays`):**
- The 8th newcomer reaches `candidates.len() == MAX_CANDIDATES`, and `candidates.remove(0)` closes the game's socket. The game then reads `Ok(0)`, meaning the bridge hung up.
- When the map loads, the game's message goes nowhere, and `wait_for_game` does not return within 3 s.
- While the strays stay connected, `idle_since` keeps resetting (`link.rs:233-234`), so the bridge waits with no limit. Once they leave, it prints "the game did not connect within five minutes" even though the game did connect.
- At base, `wait_for_connection` took the game on arrival, so this failure is new. The case most likely in real use (the game first, strays after) is the one that now fails.
- The README and the fragment say "silent connections cannot keep the game out", which is false here.

**Required correction:** do not let later arrivals evict a connection without limit. Options:
- Cap newcomers instead of evicting the oldest.
- Never evict the oldest silent connection, which is the likeliest to be the game since it dials at launch.
- Bound eviction by connection age.

Then correct the README and the fragment.

**Regression test:** the game connects silent, then 8 strays connect, then the game sends `GameSpawn`. It must be found. It fails at head.

### 2. P2: a game that has already sent its message is evicted before it is checked
**Where:** `link.rs:198-216`. The accept loop drains every pending connection, with no per-pass limit, before any connection is checked.

**Trigger:** the game connects and sends its first message, and 8 more connections queue before the next 5 ms poll (or before `wait_for_game` starts).

**What happens (executed, `probe_spoken_game_evicted_in_same_accept_drain`):**
- The game is evicted during the accept loop. It is never peeked, even though a complete, valid `GameSpawn` is waiting in its buffer. The result is `Err(Timeout)`.
- The control with 7 strays finds the game (`control_spoken_game_with_one_fewer_stray_is_found`).
- A burst of strays can therefore discard a game that has already identified itself. The unbounded accept loop also starves the peek pass for as long as connections keep arriving.

**Required correction:** peek a connection before evicting it, or limit the accept loop to a fixed number of connections per pass and run the peek pass between batches.

**Regression test:** the game connects and writes its message, then `MAX_CANDIDATES` strays connect, then `wait_for_game` runs. The game must be found.

### 3. P2: `swap_remove` breaks the "oldest dropped first" order
**Where:** `link.rs:221`, `link.rs:228`, against `link.rs:206` and the documented order (`link.rs:127`, `link.rs:184`, `README.md:83`, fragment line 7).

**Trigger:** any connection that sends junk or closes. `swap_remove(index)` moves the newest connection into that slot, so `remove(0)` no longer removes the oldest.

**What happens (executed, `probe_swap_remove_breaks_eviction_order`):**
- The list is [S1, J, S2…S6, G], where J sends junk and G is the silent game, the newest.
- J is dropped, which gives [S1, G, S2…S6].
- Three silent newcomers arrive. The second evicts S1 and the third evicts G, while the older S2…S6 survive.
- The game's later message is never seen (`Err(Timeout)`).
- The same sequence without junk keeps the game (`control_same_order_without_junk_keeps_the_game`).

**Required correction:** use an order-preserving removal (`Vec::remove`, `retain`, or a `VecDeque`) for drops.

**Regression test:** the junk-then-newcomers sequence above.

## Coverage
Changed files, from `git diff --stat`:

| File | What I checked |
|---|---|
| `src/link.rs` | Framing against `RL-GAME-SOCKET-FORMAT.md:15` (u16 big-endian, matches `FrameDecoder`). Partial reads (peek waits for the whole frame; tested). Unbounded work (finding 2). Ordering and eviction (findings 1 and 3). Timeout and window (design question below). Malformed bytes go to the tolerant `decode_game_packet`, the same decoder the link uses (`rl_link.rs:587`); probes for empty, all-zero and HTTP payloads all return `Err`, with no panic. Hand-off: nothing is read before the link; the stream is set blocking, then the binary sets it non-blocking again. |
| `src/bin/rb_rl_bridge.rs` | Call site; timeout message (design question). |
| `src/lib.rs` | Re-export only. |
| `tests/game_finding.rs` | 7 tests, all red at base, because `wait_for_game` and `MAX_CANDIDATES` do not exist there. None covers a game that connects first, a game that speaks before a burst, or the eviction order. |
| `README.md`, `changes/2026-10-13-…md` | Claims checked against the code: findings 1 and 3. "Not authentication" is stated honestly. The "three mutations" claim was not re-run. |

The physics, units, solve-order, geometry and capture-alignment checks don't apply: the diff touches no physics, captures or fixtures.

## Design questions
- **No time limit while anything is connected** (`link.rs:232-237`). This is deliberate, because the game is silent before a map loads. But one stray connection that never speaks keeps the bridge waiting forever with no message, where it used to give up after five minutes. A separate overall deadline, or a periodic "still waiting on N silent connections" note, would make this visible.
- **A real game whose first message is rejected is dropped silently.** If the game's first message fails the tolerant decoder (a `WireError::State` on a spawn event), the connection is closed with a note. The bridge then reports "did not connect within five minutes" instead of failing right away with the decode error, as it did at base.

## Optional improvements
- `first_message` allocates and zeroes a 64 KiB buffer for each connection on every 5 ms pass (about 100 MB/s with 8 connections). One reusable buffer, or peeking 2 bytes and then `2 + len`, would avoid it.
- In the accept loop, per-connection errors from `set_nonblocking`/`set_nodelay` (`link.rs:201-202`), and accept errors other than `WouldBlock`/`Interrupted` (`link.rs:214`), abort the whole wait. Treating them as "drop this connection" would match what the PR is trying to do (see the Windows gap below).

## Checks
- `cargo fmt --all -- --check`: pass.
- `cargo clippy -p rb_rl_game_bridge --all-targets -- -D warnings`: pass. The configured target directory is on a read-only filesystem, so I used `CARGO_TARGET_DIR=$TMPDIR/target`.
- `cargo test -p rb_rl_game_bridge`: pass. All suites green, including `game_finding` (7/7) and `live_capture` (11/11). No corpus-gated tests were skipped in this crate.
- `python3 .github/scripts/changes.py check`: 70 fragments ok.
- Probes in a scratch copy (`$TMPDIR/scratch`, outside the reviewed tree): the 3 finding probes fail at head, the 2 controls pass, and the payload-looseness probe shows no panics.
- Not run: the physics crates (`rb_physics_bullet`, `rb_env`, `rb_verify_cli`) and the tape-bot Python suite, which the diff does not touch.

## Validation gaps (need Windows or the live game)
- **Windows `accept`:** Winsock documents `WSAECONNRESET` from `accept` when a peer resets before being accepted, as `nmap -sT` does. At `link.rs:214` that would end `rb_rl_bridge` with an error. Linux hands back the socket instead, so this was not reproducible here. Settle it with a connect-and-reset stray against the Windows build.
- **Windows `MSG_PEEK`:** on Windows, peek on a TCP stream may return only part of the queued data. A default `SO_RCVBUF` of 64 KiB is also below the 65,537-byte maximum frame. A first message split across segments, or one near the size limit, could then stay `Waiting` forever. Settle it with a Windows run using a split, large first frame, plus the real game's first message.
- **The game's first message:** that it decodes with the tolerant decoder is backed by the committed live slices, but `wait_for_game` has not been run against the game (as the fragment says).

## Verdict
**Changes required.**
