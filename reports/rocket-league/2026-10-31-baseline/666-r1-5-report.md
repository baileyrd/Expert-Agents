<!-- rocket-league/666-r1 run 5, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 157 s, 417213 tokens, $0.61 -->

I'm requesting changes on PR 666 of Rusty-Mill/rusty_mill. The fetched head matches the given SHA. The change is meant to stop stray connections from keeping the game out, but two probes I ran show they still can, and one documented claim is false.

**Repository:** Rusty-Mill/rusty_mill · **PR:** #666
**Base:** `f3d03a936e7d9497b5ef315a5be8b30755aef689` · **Reviewed head:** `fc3a6b0554faabb3baa647cdf2418a388ffb9b89` (`git fetch origin pull/666/head` = head SHA)

**Scope:** one commit. It adds `GameListener::wait_for_game` and `MAX_CANDIDATES` in `rb_rl_game_bridge/src/link.rs`, makes `rb_rl_bridge` use it, and adds `tests/game_finding.rs`, a README section and a change fragment. It touches no physics or verification code. The axes that apply are hostile bytes and connections on the game socket, and whether the PR's claims hold.

## What must hold
- The game opens **one connection** and stays silent until a map loads (`RL-GAME-SOCKET-FORMAT.md:13-14`; README "Start `rb_rl_bridge` before the game. The game dials it once, at launch"). So a game connection that gets dropped while it waits is never seen again.
- Bytes on the game port are untrusted. Each wait must be bounded, and a malformed or closed peer must be dropped rather than held.

## Blocking findings

### 1. P1: Later connections evict the waiting game, and the bridge then waits forever
- **Where:** `link.rs:203-209`, together with the timeout being suspended at `link.rs:233-236`.
- **Trigger:** the game dials in at launch and stays silent while it loads. Then 8 more local connections arrive, for example a port scanner, a health check or a retrying tool. The 9th arrival triggers `candidates.remove(0)`, which closes the game's socket.
  - The game never dials again, so it is never found.
  - While any stray stays open, `idle_since` keeps being reset, so the five-minute error never fires. `rb_rl_bridge` waits forever.
- **At base:** `wait_for_connection` took the first arrival. With `--launch` that is normally the game, so this run would have worked.
- **Executed (scratch probe):** game connects, then `MAX_CANDIDATES` strays, then the game writes a real `GameSpawn` frame. Notes show `"dropped the oldest waiting connection: too many"`. The game's write succeeds into a closed socket, and `wait_for_game(300 ms)` had not returned after 3 s. The game was not found.
- **False claims:** the doc comment, the README ("the oldest dropped first so silent connections cannot keep the game out") and the fragment all say the opposite. The only full-house test (`silent_connections_cannot_fill_the_places_and_keep_the_game_out`) puts the game *last*, which is the one order the policy handles.
- **Required correction:** a cap that evicts by age alone cannot both keep strays out and keep the game in. Options:
  - Tell connections apart by who owns the peer socket (the limitation the fragment names). When `--launch` spawned the game, match the child's PID.
  - Or never evict and refuse new connections past the cap, with an overall deadline that keeps running.
  - Either way, correct the docs and fragment. Add a regression test where the game connects first and stays silent, `MAX_CANDIDATES` strays follow, then the game speaks and must be returned (or the wait must end with an error). That is my probe, and it fails at head.

### 2. P2: "Drop the oldest" drops whichever connection happens to be first after any earlier drop
- **Where:** `link.rs:222` and `link.rs:228` use `swap_remove`, which moves the newest candidate into index `index`. After that, `remove(0)` at `link.rs:205` no longer drops the oldest.
- **Executed (scratch probe):** c0 connects, then c1…c7 (silent). c0 closes, so `swap_remove(0)` moves c7 to the front. Two more arrivals push the list past the cap. The eviction then dropped c7, one of the *newest* connections. When c7 wrote a valid game frame, the wait timed out (`Err(Timeout)`).
- **Impact:** after any junk or closed peer, the eviction order the PR documents (and that finding 1's argument rests on) is arbitrary.
- **Required correction:** keep arrival order, e.g. `remove(index)`, `VecDeque`, or `retain` with a found flag. Add a regression test of a drop followed by an overflow, checking that the oldest survivor is the one evicted.

### 3. P2: A peer that sends part of a frame and then closes is never dropped
- **Where:** `link.rs:143-157`.
- **Trace (source-traced):** a peer sends one byte, or a header announcing N bytes followed by fewer, then sends FIN. `peek` keeps returning the buffered bytes (n ≥ 1) and never `Ok(0)`, so `first_message` returns `Waiting` on every pass.
  - That connection is held until it is evicted.
  - While it is held, the timeout is suspended.
  - The fragment's claim "A connection that closes … is dropped" is false for this case.
- **Not new as a hang:** at base, adopting such a peer also blocked the link, so this does not newly make a hang reachable. It is a false claim plus malformed input that is held rather than rejected.
- **Required correction:** detect EOF behind a partial frame and drop the peer. One way: when `n < 2 + len`, check whether the peer has half-closed, e.g. by peeking into a larger buffer than what is already queued. Or put a per-connection deadline on finishing a frame once the first byte arrives; this is consistent with "silent ones wait", because the game is not silent once it starts a frame. Add a test: send 1 byte, shut down writing, then expect a "dropped" note.

## Design questions (for the owner)
- **No overall bound on the wait:** while any connection is waiting, the timeout does not run, so one silent local peer keeps `rb_rl_bridge` waiting forever with no message. This is documented in the fragment as intended, because the game is silent until a map loads. But it means the "did not connect within five minutes" failure now depends on nothing else touching the port. Should there be an overall ceiling, or a periodic "still waiting on N silent connections" note?

## Optional improvements
- `first_message` allocates a 65,537-byte `Vec` per candidate per 5 ms poll, about 100 MB/s of churn with 8 candidates. Allocate it once in `wait_for_game` and reuse it.
- The eviction note doesn't name the dropped peer. Including `peer_addr` would make live diagnosis easier.
- An error from `set_nonblocking` or `set_nodelay` on a stray connection that resets immediately ends the whole wait with `Err` (`link.rs:201-202`). Ignoring the failure and dropping that connection would be more robust.

## Tests
- All 7 new tests exercise the new API, so they can't compile at base. None of them covers the game-first ordering (finding 1) or eviction order after a drop (finding 2). Both of my probes fail at head; they are the missing regression tests.
- The fragment says "three mutations each fail the tests that matter". I did not re-run those mutations. The mutations in findings 1 and 2 pass every committed test.

## Checks run (in a scratch export of head)

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rb_rl_game_bridge --all-targets -- -D warnings` | pass |
| `cargo test -p rb_rl_game_bridge` | pass: unit 25 + 3 + 5 + 3; integration bridge 7, census 7, `game_finding` 7, golden 7, `live_capture` 11, `real_core` 2, `real_link` 15, robustness 12 |
| `python3 -I .github/scripts/changes.py check` | 70 fragments ok |
| Scratch probes (`tests/zz_probe.rs`, removed afterwards) | both fail at head, as described in findings 1 and 2 |

No physics crates changed, so the physics, verification and tape-bot checks don't apply. The private corpus was not needed.

## Validation gaps
- **Real game's first message:** whether it decodes with the tolerant decoder is unproven, as the fragment admits. The committed fixtures are mid-stream slices, not the first message after connecting. The settling evidence is a live `--record` run from launch, checking that the first framed message decodes.
- **Windows:** how sockets that reset immediately behave under `set_nodelay` and `peek` is unchecked.

## Verdict: **changes required**
