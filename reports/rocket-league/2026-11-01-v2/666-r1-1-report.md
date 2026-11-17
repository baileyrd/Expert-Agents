<!-- rocket-league/666-r1 run 1, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 198 s, 446100 tokens, $0.74 -->

# Review of PR 666 (Rusty-Mill/rusty_mill): changes required

The PR's goal is that a stray connection to the game port can no longer take the game's place. It now fails that goal in the normal launch order, and a peer that has already left can stop the timeout for good. Both were reproduced in a scratch copy.

- **Repository:** Rusty-Mill/rusty_mill, PR 666
- **Base:** `f3d03a936e7d9497b5ef315a5be8b30755aef689`
- **Head reviewed:** `fc3a6b0554faabb3baa647cdf2418a388ffb9b89` (fetched `pull/666/head`; it matches)
- **Commit:** one, `fc3a6b055` "rb_rl_bridge: the game is the connection that speaks like it"

## Blocking findings

### 1. P0: a peer that sends part of a frame and then closes is never dropped, so the bridge waits forever
- **Where:** `src/link.rs:143-155` (`first_message`) and `:231-236` (timeout).
- **Trigger:** any local client that writes a few bytes and closes, such as a health check or HTTP probe.
- **Scenario (executed, `probe_partial_then_close_held_forever`):**
  - A client connects, writes `GET / HTTP/1.0\r\n\r\n` (18 bytes) and closes.
  - `peek` after the close still returns the 18 buffered bytes, never 0, so the "closed without a word" branch is never reached.
  - The length is read as `0x4745` (18245 bytes). Since `n < 2 + len`, the result is `Waiting` on every pass.
  - The connection stays a candidate, so `idle_since` keeps resetting and the timeout never fires.
  - With a 100 ms timeout and the peer gone, `wait_for_game` had still not returned after 2 s. If the game never connects (for example a failed `--launch`), `rb_rl_bridge` hangs instead of stopping at five minutes.
- **What the docs say:** the doc comment ("Connections that close … are dropped"), the README and the change fragment all say a closed connection is dropped. For a peer that sent a partial frame, that is false.
- **Fix:**
  - When `n < 2 + len`, tell a closed peer apart from a slow one. One way: keep a per-candidate count of bytes seen plus a deadline for completing a frame once its first byte has arrived. The game does send whole frames.
  - Or bound the wait in some other way that does not depend on what the peer says.
- **Regression test:** a client writes a partial frame and closes; `wait_for_game(100 ms)` must return `Ok(None)` within a bounded time.

### 2. P1: the game itself is the connection that eviction drops; it dials once, so the bridge never gets it
- **Where:** `src/link.rs:203-208`.
- **Background:** the README (lines 76-78 at head) says "The game dials it once, at launch", and the PR says the game stays silent until a map loads. So in the normal order the game is the oldest silent candidate, which is exactly the one "oldest dropped first" evicts.
- **Scenario (executed, `probe_silent_game_then_eight_strays`):**
  1. The game connects and stays silent.
  2. 8 strays connect, so 9 have arrived. The game is evicted (note: "dropped the oldest waiting connection: too many").
  3. The strays close and are dropped.
  4. The game then writes a valid `GameSpawn`. Its socket is already closed on the bridge side.
  5. `wait_for_game` returns `Ok(None)`, and the bridge exits with "the game did not connect".
- **Why it matters:** if the strays stay open instead, the bridge waits forever on a game it has already dropped. The fragment's claim that the oldest is "dropped first so silent connections cannot fill the places" holds only when the game arrives last, and that is the only case the test checks (`silent_connections_cannot_fill_the_places_and_keep_the_game_out`).
- **Fix:** choose an eviction policy that cannot drop a silent game that dialled first. Options:
  - Refuse or close the newest connection when the house is full.
  - Evict only connections that have sent bytes that are not yet a frame.
  - Raise the cap, since accepting a connection costs nothing.
  - Or document and test that an early game can be evicted, and get the owner's decision on it.
- **Regression test:** the probe above. The game connects first and stays silent, 8 or more strays follow, then the game speaks; it must be found.

### 3. P2: `swap_remove` breaks the age order that eviction relies on
- **Where:** `src/link.rs:228` together with `:206`.
- **Problem:** `remove(0)` assumes `candidates` is oldest-first, but dropping a junk candidate with `swap_remove(index)` moves the newest candidate into that slot.
- **Scenario (executed, `probe_swap_remove_breaks_oldest_first`):**
  1. Arrival order: junk, s1…s6, game (silent).
  2. The junk is dropped and the list becomes `[game, s1..s6]`.
  3. Two more strays arrive. The second evicts index 0, which is the game, while older s1…s6 stay.
  4. The game's later valid message is never seen.
- **Fix:** use `remove(index)` (the list holds at most 8, so the cost is trivial) or store each candidate's accept time.
- **Regression test:** the probe above, asserting the game is found.

### 4. P2: the accept loop has no per-pass limit, so a connect flood starves the first-message scan
- **Where:** `src/link.rs:198-216`.
- **Problem:** the inner `loop` keeps calling `accept` until it gets `WouldBlock`. While connections keep arriving faster than they are accepted, the scan at 217-230 never runs. A game that has already spoken is not recognised, and each new connection evicts another candidate (see finding 2). This was traced from the source, not executed.
- **Fix:** accept at most a fixed number per pass (for example `MAX_CANDIDATES`) before scanning.
- **Regression test:** a thread connecting in a tight loop while the game sends its message; the game must be found within a bound.

## Coverage
| File | Classes checked |
|---|---|
| `changes/2026-10-13-…first-message.md` | Claims beyond the evidence (findings 1-3 make claims false); the "seven tests" count is correct |
| `rb_rl_game_bridge/README.md` | Docs against code (findings 1 and 2) |
| `src/bin/rb_rl_bridge.rs` | Framing (the stream is returned unread, then set non-blocking; fine), stale frames (n/a), unbounded work (inherits findings 1 and 4) |
| `src/lib.rs` | Export only |
| `src/link.rs` | Framing: `u16` big-endian prefix plus FlatBuffer, matching `RL-GAME-SOCKET-FORMAT.md` and `rb_rlbot_wire::frame`; the peek buffer is bounded at 65537; length checks are correct. Unbounded work (findings 1 and 4). Partial reads (finding 1). Close and error handling. Eviction (findings 2 and 3). Decoder agreement: the tolerant decoder is the same one `rl_link.rs:587` uses, so nothing the link would accept is rejected here |
| `tests/game_finding.rs` | All 7 tests are red at base (`wait_for_game` does not exist there). No test covers a game that connects first, a partial frame then close, or reordering |

Physics, verification and replay classes (units, tick order, solve order, geometry, alignment) do not apply: the diff touches only the game socket listener.

## Design questions
- With any live connection open, the five-minute limit stops running, so one silent local client keeps the bridge waiting forever. This is documented and tested on purpose. The owner should confirm it fits ADR-0084's fail-closed intent, or add an overall upper limit.

## Optional improvements
- `first_message` allocates 64 KiB per candidate on every 5 ms pass (up to about 100 MB/s of churn). Allocate one buffer outside the loop.
- `rb_rl_bridge.rs:179` still says "did not connect within five minutes". The wait can now last much longer, and `None` can follow a game that did connect but was dropped.
- An error from `set_nonblocking` or `set_nodelay` on one stray connection, or a non-`WouldBlock` error from `accept`, ends the whole wait (`:201-202`, `:214`). Skipping that one connection would match the PR's aim of tolerating strays.

## Checks and validation gaps
- `cargo fmt --all -- --check`: passes.
- `cargo clippy -p rb_rl_game_bridge --all-targets -- -D warnings`: passes. It was run with `CARGO_TARGET_DIR` in `$TMPDIR` because the repo's `target/` is read-only here.
- `cargo test -p rb_rl_game_bridge`: all pass, including the 7 in `game_finding.rs`. None of its tests needed the private corpus.
- `python3 .github/scripts/changes.py check`: 70 fragments ok.
- Scratch probes (`$TMPDIR/scr`, `tests/probe_review.rs`): reproduced findings 1, 2 and 3.
- Physics crates, golden captures and the tape-bot were not run, because the diff does not touch them.

**Validation gaps:**
- I could not check against the real game that its first message after a map loads decodes with the tolerant decoder, and that it sends nothing earlier. That needs a live run of `rb_rl_bridge`, plus an inbound capture showing that the first frame is `GameSpawn` and decodes.
- How Windows reports `peek` and `accept` after a reset (WSAECONNRESET) is unverified here.
- The author's three mutation runs and the manual binary run were not repeated.

## Verdict
**Changes required.**
