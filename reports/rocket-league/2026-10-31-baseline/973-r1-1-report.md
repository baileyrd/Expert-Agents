<!-- rocket-league/973-r1 run 1, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 183 s, 1162272 tokens, $1.05 -->

# Review of PR 973 (Rusty-Mill/rusty_mill): ball speed-cap and rest probes

**Verdict: changes required.** The two speed-cap probes are sound. The rest probe can't measure what it was written for, and the change fragment says more than the evidence supports.

- **Base:** `15296ea5bb670d14acb22df4f856fadec3e8280a`
- **Reviewed head:** `1f2ec4af1f652400ea4dda8e5093a121cdb86646`. The fetched `pull/973/head` equals this SHA, so the review is pinned.
- **Diff:** round 6 of `gen_probes.py`, three new experiment scenarios (`probe_ball_fast_4500`, `probe_ball_fast_6500`, `probe_ball_rest`) and the change fragment. There are no Rust changes.

## What must hold (predicted before reading the PR text)

- **Speed cap:** the ball's speed is clamped to 6000 uu/s (`BALL_MAX_SPEED`, `rb_physics_bullet/src/world.rs:28`, RocketSim `RLConst.h:44`). A probe can only show the cap if the ball starts above 6000 uu/s and stays clear of the walls long enough to read the clamp.
- **Sleep:** the ball goes to sleep only after 0.5 s (`SLEEP_TIME_THRESHOLD`) below 20 uu/s (`LINEAR_SLEEP_VELOCITY_THRESHOLD`) and below 0.5 rad/s (`body.rs:105-109`, FR-037). A probe can only show this if the ball actually gets below 20 uu/s during the tape.
- **Constants:** the tape length is in ticks at 120 Hz (tape-bot README). 93.15 is RocketSim's `BALL_REST_Z`, the same value used by the other ball probes.

## Blocking findings

### 1. P2: `probe_ball_rest` cannot show a ball at rest, so it cannot measure the sleep threshold it targets

- **Where:** `tools/rb_tape_bot/gen_probes.py:165-166`, `experiments/probe_ball_rest.json:2,31-41`, `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:7`.
- **Executed, port:** I ran `simulate_scenario` on the committed JSON from a scratch crate.
  - The ball slides from 600 to about 420 uu/s in 0.75 s. That is the expected loss to 5/7 of the speed as it starts rolling.
  - It then rolls on, losing about 2.2 % of its speed per second.
  - At tick 1261 (10.5 s) it is still moving at **339.9 uu/s**. The lowest speed in the whole run is also 339.9 uu/s.
  - The sleep threshold needs **< 20 uu/s for 0.5 s**. At this decay rate the ball would need about 140 s to get there.
- **Executed, recorded game:** the committed real-game fixture `fixtures/probe_ball_roll.jsonl` shows the same behaviour.
  - Once the ball rolls (frames 443 to 452), it goes from 547.7 to 545.8 uu/s in 0.15 s, a decay of about 2.3 % per second. This agrees with the port.
  - From about 420 uu/s, the game would also need well over 100 s to fall below 20 uu/s.
- **Effect:** the fragment says the ball is held "for 10 s so the ball stops", and the scenario name says "until it stops". Neither the port nor the game supports that. A run on the game machine will record a ball still rolling at about 340 uu/s, and the sleep threshold will stay unmeasured.
- **Required correction:** start the ball already rolling just above the threshold. For example, use velocity `[0, v, 0]` with matching spin `angular_velocity = [v/91.25, 0, 0]`, at a few speeds that bracket 20 uu/s (such as 15, 25 and 40 uu/s). Make each tape longer than the time to decay below 20 uu/s plus the 0.5 s sleep timer; at 40 uu/s and 2.3 %/s that is about 30 s, roughly 3700 ticks. Alternatively, keep the 600 uu/s probe but rename and re-describe it as a slide-to-roll transition probe.
- **Check that goes red without the fix:** a `gen_probes`/fixture check, or an `rb-verify --scenario` assertion, that the port's predicted ball speed drops below `LINEAR_SLEEP_VELOCITY_THRESHOLD` for at least `SLEEP_TIME_THRESHOLD` before the tape ends. The current file fails that check (it never drops below 339.9 uu/s).

### 2. P2: the fragment says the rolling threshold has never been measured in the real game, which the parity doc contradicts

- **Where:** `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:7`: "neither the cap nor the rolling and sleep thresholds have been measured in the real game".
- **Source-traced:** `docs/research/PHYSICS-PARITY-VS-ROCKETSIM.md:71` records the ball rolling threshold as measured from the real-game capture `probe_ball_roll`: "rolling begins near frame 448 at v = 547.4 = 6 × 91.25". My fixture analysis above confirms it, with rolling starting at about 547.5 uu/s around t = 3.88 s.
- **The new probe can't measure it anyway:** a 600 uu/s slide becomes a roll at 5/7 × 600 ≈ 428.6 uu/s, which is below 547.5, so the spin cap never comes into play.
- **Required correction:** reword the fragment so it says only the 6000 uu/s cap and the sleep thresholds are unmeasured. Make the same change to the scenario description and to the `gen_probes.py:158` comment.

## Design questions

None.

## Optional improvements

- `probe_ball_fast_4500`: in the port the ball reaches the side wall around tick 200 of 241 and climbs it, so the last ~40 ticks are wall contact. That's harmless for a control run, but a 150-tick tape, or a start at x = −3500, would keep the whole run on the floor.
- The two cap probes look well designed. In the port the 6500 run is clamped to 6000.0 uu/s on tick 1. After that, a capped game and an uncapped one would stay about 500 uu/s apart, so a capture can tell them apart even if the state-set frame is missing (README, O009).
- Running `gen_probes.py` regenerates the three new files byte for byte. It also rewrites three round-5 files (`wallg_45_*`) with last-digit float differences. That already happens at base, so this PR didn't cause it.

## Checks run

| Check | Result |
|---|---|
| Pin: `git fetch origin pull/973/head` matches the head SHA | pass |
| `cargo fmt --all -- --check` | pass (exit 0) |
| `cargo test --release -p rb_tape_bot -p rb_scenario` | pass (all suites green) |
| `python3 -I -m unittest discover -s …/rb_tape_bot` | pass (15 tests) |
| `python3 .github/scripts/changes.py check` | pass (282 fragments ok) |
| `gen_probes.py` regenerated in a scratch copy | the 3 new files are identical; the round-5 float differences already exist at base |
| Scratch-crate port prediction (`simulate_scenario`) for all 3 probes | traces quoted above |
| Analysis of the real-game fixture `probe_ball_roll.jsonl` | rolling decay about 2.3 %/s |
| `cargo clippy`; golden captures in `rb_verify_cli` | not run: the diff changes no Rust and no golden bounds |

- **Red-at-base:** the diff changes no behaviour, so a red-at-base test doesn't apply. Finding 1 names the check that should be added.
- **Builds:** cargo ran with a scratch `CARGO_HOME` and `CARGO_TARGET_DIR`, because the default locations are read-only in this sandbox.

## Validation gaps

These need a run on the game machine:

- whether the game accepts a 6500 uu/s state-set or clamps it first, as the fragment already notes;
- the real cap behaviour;
- the real sleep threshold, which needs a corrected rest probe (finding 1).

PowerShell, BakkesMod and the capture plugin were not exercised here.
