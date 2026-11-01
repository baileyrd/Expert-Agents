<!-- rocket-league/666-r1 run 2, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 202 s, 510280 tokens, $0.66 -->

I found one blocking defect in PR 666: if Rocket League connects to the bridge first, eight later connections push it out, and the bridge then waits on them indefinitely. I reproduced it in a scratch copy. **Verdict: changes required.**

- **Repository:** Rusty-Mill/rusty_mill, PR 666
- **Base:** `f3d03a936e7d9497b5ef315a5be8b30755aef689`
- **Head reviewed:** `fc3a6b0554faabb3baa647cdf2418a388ffb9b89`. I fetched `pull/666/head` and it matches this SHA.
- **Scope:** one commit (`rb_rl_bridge: the game is the connection that speaks like it`) across six files in `rb_rl_game_bridge` plus a change fragment.

## Blocking findings

### 1. P0: When the game connects first, later connections push it out and the bridge waits indefinitely
- **Where:** `crates/rb_rl_game_bridge/src/link.rs:204-209` (drops the oldest connection when the list is full) and `:233-238` (the timeout only runs when no connection is waiting).
- **Trigger:** eight or more connections to the game port after the game has connected but before its first message, which only comes once a map loads. They can be silent and held open, or arrive in one burst. The README says to start the bridge before the game, so in normal use the game is the oldest connection.
- **What happens (executed):** the bridge starts, the game connects and stays silent, then `MAX_CANDIDATES` (8) silent connections arrive. Waiting connections are capped at 8, so the 8th new one makes the bridge close the oldest, which is the game. The note printed is `dropped the oldest waiting connection: too many`. The game's first message then goes to a closed socket; its next write fails with `BrokenPipe`. `wait_for_game` did not return within 5 s, even though its timeout was 100 ms, because the timeout doesn't run while connections are waiting.
  - In `rb_rl_bridge` this means the bridge waits as long as those connections stay open.
  - After they close, it reports "the game did not connect within five minutes" while the game is running.
  - The README says the game connects only once, at launch, so it never reconnects.
  - **At base**, `wait_for_connection` took the first connection, so the game connecting first always worked. This is a regression in the bridge's normal startup order.
- **Fix needed:** choose a rule that doesn't trade one ordering for the other. Options:
  - Never drop a connection just because it is old. Use a much larger, still bounded cap and refuse new connections past it.
  - Or, with `--launch`, prefer the connection that came first after the child process started.
  - If a fixed cap must stay, document that it can be defeated in both directions.
- **Regression test:** the game connects first and stays silent, `MAX_CANDIDATES` silent connections follow, then the game sends `GameSpawn`. `wait_for_game` must return the game's stream. This fails at head. The existing `silent_connections_cannot_fill_the_places_and_keep_the_game_out` only covers squatters arriving before the game.

### 2. P2: The change fragment and README claim more than the code does
- **Where:** `changes/2026-10-13-bridge-finds-the-game-by-its-first-message.md:7`, `crates/rb_rl_game_bridge/README.md:83-85`, and the doc comment at `link.rs:183-185`.
- **Claim:** "the oldest dropped first so silent connections cannot fill the places" / "cannot keep the game out".
- **Problem (executed, see finding 1):** silent connections arriving after the game do keep it out, and that is the order the README itself recommends. The fragment's "Verified" line tests only the squatters-first order.
- **Fix needed:** correct the wording to match whatever rule finding 1 lands on, and state the ordering the tests cover.

## Design questions
- **No timeout while any connection waits.** A single silent stray connection now keeps `rb_rl_bridge` waiting forever, even if the game never starts (for example, the `--launch` child crashes). The fragment says this is deliberate, but it removes the fail-closed five-minute limit the bridge had at base. The owner should confirm this, or add a much longer overall limit.

## Optional improvements
- `first_message` allocates and zeroes a new 64 KiB buffer for every waiting connection on every 5 ms pass. With 8 connections that is about 100 MB/s of churn. It's bounded, but one reused buffer would avoid it, or peeking the 2-byte length first.
- When the game is found, the other waiting connections are dropped without a note, unlike every other drop.

## Ruled out (with reasons)
- **Junk accepted as the game:** I ran the decoder on frames of zeros (8, 12, 16 and 64 bytes), `\0\0\0\0hello world` and `[4,0,0,0,0,0,0,0]`. All were rejected (`BadVtable`). An empty but well-formed message would pass, but the fragment's "Known limitations" says so.
- **Bounds on peer-supplied sizes:** the length comes from a `u16`, the peek buffer is `2 + u16::MAX`, and the slice is checked against the bytes actually peeked. No overflow or unbounded allocation.
- **First message lost:** peeking never consumes bytes, and the `read_first` tests confirm the message is still there. A message split across writes is waited for (tested).
- **Connections reset before accept (executed, Linux):** I connected and reset three times (`SO_LINGER` 0). The bridge dropped them with "it failed: Connection reset by peer" and still found the game.
- **Physics, verification and replay axes:** the diff touches only the game socket's accept path.

## Checks (run in a clean `git archive` export of each SHA, with a scratch target directory)
- `cargo fmt --all -- --check`: pass.
- `cargo clippy -p rb_rl_game_bridge --all-targets -- -D warnings`: pass.
- `cargo test -p rb_rl_game_bridge`: 104 passed, 0 failed, including the 7 new `game_finding` tests.
  - None of the 7 can compile at base because the API is new, so they all fail there by construction.
  - No test covers the bin's switch to `wait_for_game`.
- `python3 .github/scripts/changes.py check`: 70 fragments ok.
- **Probes (scratch only, not committed):** game first then 8 squatters (failed as in finding 1), how lenient the decoder is, and connections reset before accept.

My first scratch folder already held untracked files from an earlier session; one of them, `probe_sleep.rs`, made `cargo fmt` fail. I discarded those results and reran everything above in a fresh export.

Tape-bot Python and physics crates weren't run because the diff doesn't touch them. Nothing here needs the private corpus.

## Validation gaps
- **Real game not tested:** that its first message decodes with the tolerant decoder, and that it never reconnects after its socket is reset. A live run with `rb_rl_bridge --launch` and a squatter started after launch would settle both.
- **Windows behaviour not tested:** `peek` on Windows may return less than what is buffered, and receive buffers are smaller there. Large first messages could then wait forever.
- **PowerShell and the BakkesMod plugin:** not applicable to this diff and not exercised.

**Verdict: changes required.**
