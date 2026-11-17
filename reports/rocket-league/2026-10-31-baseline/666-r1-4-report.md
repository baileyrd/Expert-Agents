<!-- rocket-league/666-r1 run 4, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 135 s, 268641 tokens, $0.49 -->

# Review of PR 666, Rusty-Mill/rusty_mill: changes required

- **Base:** f3d03a936e7d9497b5ef315a5be8b30755aef689
- **Reviewed head:** fc3a6b0554faabb3baa647cdf2418a388ffb9b89. I fetched `pull/666/head` and it equals this SHA. It is one commit, `fc3a6b055`.
- **Area:** the game bridge (`rb_rl_game_bridge`). The PR adds `GameListener::wait_for_game` and `MAX_CANDIDATES`, and `rb_rl_bridge` now uses them.

The fix stops a stray connection from taking the game's place when it arrives *before* the game. But it opens the reverse case: the real game connects early and stays silent, and stray connections arriving after it push it out. I reproduced that in a probe.

## Blocking findings

### 1. P0: silent connections that arrive after the game push it out, and the bridge then waits forever
**Where:** `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/link.rs:204-207` (drops the oldest waiting connection) and `:233-236` (the timeout is paused while any connection waits).

**Trigger:** 8 or more local connections to the game port, opened after the game dials and before its first message.

**Why the game is always the oldest connection:**
- The README says "The game dials it once, at launch" (`README.md:76`).
- The PR itself says the game "says nothing until a map loads".
- `rb_rl_bridge.rs:173-178` launches the game before calling `wait_for_game`.

So the game is normally the first, oldest and silent connection, which is exactly the one the PR drops first.

**What happens (executed in a scratch copy):**
1. The game connects and says nothing.
2. After 50 ms, `MAX_CANDIDATES` (8) silent connections arrive.
3. The game then sends a real GameSpawn first message.
4. `wait_for_game` is running with a 1 s timeout.

Result: after 3 s the wait had returned nothing (the probe's 3 s wait timed out), and the notes show `"dropped the oldest waiting connection: too many"`.

- The game's socket was closed, and the game never dials again.
- The 1 s timeout never fires, because the squatters keep the list non-empty.
- `rb_rl_bridge` hangs with no limit while the game is gone.

At base, `wait_for_connection` took the first arrival, so this order worked. The PR makes a hang caused by a local socket peer reachable, which the rubric grades P0 whatever the trigger.

The new test `silent_connections_cannot_fill_the_places_and_keep_the_game_out` only covers squatters that arrive *before* the game.

**Required correction:** dropping a connection must not be able to remove the game. Options:
- never evict the oldest connection (the launched game dials first);
- or rank connections by something other than arrival order, such as the owner of the peer socket;
- or, failing either, never let the timeout pause indefinitely and report that the game's connection may have been dropped.

**Regression test:** in `tests/game_finding.rs`, connect the game, then `MAX_CANDIDATES` silent connections, then have the game send `game_message()`. Assert that `wait_for_game` returns that stream. It goes red at the head.

### 2. P2: a connection that fails during accept stops the wait, contrary to the stated behaviour
**Where:** `link.rs:201-202` and `:214`.

**Trigger (source-traced):** a stray peer whose connection is reset before or just after it is accepted. Examples:
- `accept` returns `ConnectionReset` or `ConnectionAborted` (a reset before accept can surface as `WSAECONNRESET` on Windows, where the game runs);
- or `set_nonblocking` / `set_nodelay` fails on a socket that is already reset.

**Result:** the `?` / `return Err(e)` ends `wait_for_game`, and `rb_rl_bridge` exits before the game is found.

**Why it is a defect:** the change fragment and README say "A connection that closes … is dropped and the wait goes on". `first_message` already treats per-connection errors as `NotTheGame`, but the accept path does not. Base had the same `return Err(e)`. This PR, though, deliberately keeps accepting stray connections, so a stray peer can now reach this path.

**Required correction:** treat per-connection accept or setsockopt errors as dropping that connection (note it, then continue). Return only listener-level errors.

**Validation gap:** this needs a Windows run with a peer that sends RST before accept.

### 3. P2: the accept loop has no per-pass budget
**Where:** `link.rs:198-216`.

**Trigger (source-traced):** a local peer connecting continuously.

The inner `loop` only exits on `WouldBlock`, so a steady stream of connections keeps it accepting and dropping, and the `first_message` pass never runs. Memory stays bounded at 8 connections, but the wait can be starved. Each arrival also prints a line through `println!`, so the log grows without limit.

**Required correction:** cap the number of accepts per pass (for example `MAX_CANDIDATES`) before checking the waiting connections, and rate-limit the notes.

### 4. P2: the documentation states a guarantee the code does not keep
**Where:**
- the change fragment `changes/2026-10-13-bridge-finds-the-game-by-its-first-message.md` ("silent connections cannot fill the places");
- `README.md:79-86` ("the oldest dropped first so silent connections cannot keep the game out");
- the doc comment at `link.rs:181-189`.

Finding 1 shows silent connections *can* keep the game out: they evict it.

The fragment's mutation claims ("no eviction" fails the tests) are about the order the tests cover. The order in finding 1 is not covered.

**Required correction:** fix the claim together with the code.

## Design questions
- The five-minute limit is now paused whenever any connection is waiting. One leftover silent process means `rb_rl_bridge` waits forever, and the user never sees "the game did not connect within five minutes". This follows the PR's stated intent, but the owner may want a hard upper limit, or a periodic "still waiting on N silent connections" message.
- "Decodes as the game's" is a weak test. In a probe, a 12-byte empty FlatBuffers table (`04 00 00 00 fc ff ff ff 04 00 04 00`) decodes `Ok` with zero events, so any process sending that is taken for the game. The PR says this is not authentication, so this is not a finding. Requiring a non-empty bundle, or a known first event, would raise the bar.

## Optional improvements
- `first_message` allocates a 64 KiB buffer for every waiting connection on every 5 ms pass, which is up to about 100 MB/s of allocation churn while waiting. Reuse one buffer.
- `candidates.remove(0)` combined with `swap_remove` elsewhere means the list is not kept in arrival order, so "oldest" is only approximate once anything has been removed with `swap_remove`. This matters for any correction to finding 1 that relies on arrival order.

## Checks
All were run on a `git archive` of the head in `$TMPDIR`, with its own target directory.

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rb_rl_game_bridge --all-targets -- -D warnings` | pass |
| `cargo test -p rb_rl_game_bridge` | pass: all suites, including the 7 new tests in `game_finding.rs`. Those tests do not compile at base (`wait_for_game` is new), so they guard the new API. None goes red for the finding 1 order. |
| `python3 .github/scripts/changes.py check` | "70 fragment(s) ok" |
| Probe `probe_game_first_then_squatters` (executed) | Reproduces finding 1: no game returned after 3 s, oldest-connection dropped note recorded. |
| Probe `probe_tiny_payloads` (executed) | An empty 12-byte table decodes `Ok`. |

Not run:
- Physics, golden-capture and tape-bot suites: the diff touches only the bridge.
- Private corpus: not needed here.

## Validation gaps
- Real Rocket League: whether its first message is always accepted by the tolerant decoder, and whether it ever dials again after its connection is closed.
- Windows accept/RST behaviour (finding 2).

## Verdict: **changes required**
