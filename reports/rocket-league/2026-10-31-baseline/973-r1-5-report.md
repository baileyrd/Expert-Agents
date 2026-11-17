<!-- rocket-league/973-r1 run 5, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 244 s, 1390277 tokens, $1.10 -->

# Review of PR 973: ball speed-cap and rest probes (Rusty-Mill/rusty_mill)

- **Base:** `15296ea5bb670d14acb22df4f856fadec3e8280a`
- **Reviewed head:** `1f2ec4af1f652400ea4dda8e5093a121cdb86646`. I fetched `pull/973/head` and it matches this SHA.
- **Diff:** one commit, 5 files, +153 lines. It adds round 6 to `gen_probes.py`, three scenario JSONs under `tools/rb_tape_bot/experiments/`, and the change fragment `changes/2026-10-24-rl-ball-cap-and-rest-probes.md`. No Rust changed.

The two speed-cap probes are sound. The rest probe can't do what it says: by the repo's own evidence the ball will still be rolling at about 340 uu/s when the tape ends. That is one P2 finding, so the verdict is **changes required**.

## What I checked first
- **Ball speed cap:** 6000 uu/s. This matches `BALL_MAX_SPEED` in FR-031 and the parity doc at `PHYSICS-PARITY-VS-ROCKETSIM.md:91`, and the doc says it is untested in the game ("captures reach 3,541").
- **Ball sleep:** the parity doc (line 198) gives three different rules:
  - Caption claim (CC): the ball stops when it stays under 40 uu/s for 2.5 s with spin under 10 RPM.
  - The port: 20 uu/s and 0.5 rad/s for 0.5 s, its own uncalibrated placeholders.
  - RocketSim: the ball sleeps only at exactly zero velocity.

  Line 78 says "a ball rolling to a stop" capture would settle this.
- **Ball drag:** 0.03 per second, `v *= (1−d)^dt` (line 89).
- **Units:** 120 Hz ticks, and the settle ticks run before the tape starts (tape-bot README). The ball starts at z = 93.15, the ball radius from ADR-0034/0036. Arena x walls are at ±4096. All of these check out.

## Blocking findings

### P2: `probe_ball_rest` cannot bring the ball to rest, so it cannot measure the sleep threshold it targets, and the fragment says it will
**Where:**
- `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/gen_probes.py:165-166`
- `.../experiments/probe_ball_rest.json:2,33-40,41-43`
- `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:3,7` ("a 600 uu/s roll held for 10 s so the ball stops", and "rolling and sleep thresholds")

**Trigger:** any run of `probe_ball_rest` on the game machine.

**Failure scenario (executed and source-traced):**

1. **The port matches the game on this kind of roll.** The committed game capture `fixtures/probe_ball_roll.jsonl` (1500 uu/s along the floor) shows:
   - deceleration of about 265 uu/s² while the ball slides;
   - spin capped at 6.0 rad/s from frame 120;
   - the ball at x = 3910.2 on frame 482.

   I ran the port's `simulate_scenario` on `experiments/probe_ball_roll.json` from a scratch crate outside the tree. It gives x = 3911.9 on frame 481 and 6.00 rad/s by frame 300. So on a sliding floor roll the port agrees with the game to within about 2 uu.

2. **The port's prediction for `probe_ball_rest`:**

   | Frame | Ball speed | Spin | y position |
   |---|---|---|---|
   | 0 | 600 uu/s | 0 | -2000 |
   | 300 | 404.6 uu/s | 4.34 rad/s | |
   | 1200 | 343.7 uu/s | | |
   | 1261 (end) | 339.9 uu/s | | +2075 |

   No frame ever reaches zero speed. The ball slides for about 0.7 s until it rolls at about 405 uu/s; after that, drag alone (3% per second) is all that slows it.

3. **What stopping would require.** To fall under the CC's 40 uu/s and stay there 2.5 s inside the 10.5 s window, the game would need a rolling resistance of at least about 45 uu/s². Nothing in the repo shows one:
   - the only game capture of a floor roll slides the whole time (its speed never falls below the 558 uu/s that the 6 rad/s spin cap allows for pure rolling);
   - RocketSim has no rolling resistance.

   So the expected result is a ball still rolling at about 340 uu/s. The scenario name, and the fragment's "so the ball stops", claim more than the cited evidence shows.

   The run would still measure free-rolling deceleration, which is useful. It would leave the parity doc's "ball's sleep thresholds" open (§3 item 2 and line 198), and a reader of the fragment would expect them closed.

**Required correction:**
- Add a slow-ball ladder that brackets both candidate rules. For example, floor rolls set at 15, 30 and 60 uu/s, each held at least 4 s:
  - 15 and 30 uu/s are under both 20 and 40 uu/s, with spin well under 10 RPM (1.05 rad/s, which is about 97 uu/s of rolling speed);
  - 60 uu/s sits above both rules and serves as the control.

  These can tell the port's 20 uu/s / 0.5 s rule apart from the CC's 40 uu/s / 2.5 s rule and from RocketSim's zero-only rule.
- Keep the 600 uu/s run if you want it, but rename it as a rolling-deceleration probe.
- Fix the fragment and the scenario text so they don't promise a stop.

**Check that fails without the fix:** the scratch `simulate_scenario` trace above, which should show the ball's speed going under 40 uu/s inside the tape for the slow starts. A test that fails today would assert that every `probe_ball_rest*` scenario's port prediction drops under 40 uu/s before `total_ticks − 300`. The 600 uu/s version fails it.

## Other failure classes I tried (none found)
- **Units, rates and order within a tick:**
  - The velocities are uu/s and the tick counts are 120 Hz packets.
  - The settle ticks run with the ball already moving. With that included, both speed-cap probes reach the side wall inside the window: the port has the 6500 run at x ≈ 3665 and z ≈ 1939 after the bounce, and the 4500 run at x ≈ 3987 and z ≈ 836. So "toward the side wall" holds.
  - The port integrates the first tick at the uncapped 6500 uu/s (Δx = 54.1 uu) and then clamps to 6000. This matches RocketSim's clamp after the step, so the capture can tell clamp-before-integration apart from clamp-after.
- **Car in the way:** the car sits at (-3500, -4000), well clear of every ball path.
- **Rest probe reaching the goal:** it ends at y ≈ 2075, well short of the goal line at 5120.
- **Recording window:** `rb_run_tapes.rs:540-545` records to frame `total_ticks + 1 + 180` and sets its timeout from that, so the 1,260-tick tape fits.
- **Ground truth:** no capture or fixture is overwritten; the PR only adds files.
- **Reproducibility:** `gen_probes.py` run in a scratch directory produces the three new JSONs byte for byte. The `wallg_45_*` files also differ from the committed ones, but only in float formatting, and they differ the same way at base, so this PR didn't cause it.

## Design questions
None.

## Optional improvements
- `rb_scenario`'s `every_shipped_scenario_parses` test (`crates/rb_scenario/src/lib.rs:475`) only reads `scenarios/`, not `experiments/`. I checked that the three new files parse with `Scenario::from_json`, but nothing in CI does. Extending the test to cover `experiments/` would protect all 80-odd probes.
- The fragment says "a 600 uu/s roll", but a ball set with no spin slides first and settles into rolling at about 405 uu/s. Saying so would help whoever scores the run.

## Checks
| Check | Result |
|---|---|
| `python3 .github/scripts/changes.py check` | pass (282 fragments ok, rc 0) |
| `cargo fmt --all -- --check` | pass |
| `python3 -I -m unittest discover -s …/rb_tape_bot` | pass (15 tests) |
| `cargo test -p rb_scenario` | pass (14) |
| `cargo test -p rb_tape_bot` | pass (8 + 10 + 3) |
| Clippy, and tests for `rb_physics_bullet`, `rb_env` and `rb_verify_cli` | not run: no Rust changed |
| Scratch `simulate_scenario` on the 3 new probes and the `probe_ball_roll` control | executed, results above |
| Regenerating with `gen_probes.py` in scratch | new files identical |

I built with a scratch `CARGO_HOME` and target directory because `~/.cargo` is read-only in the sandbox. Golden and human capture tests weren't affected and weren't run. The private corpus isn't available here.

**Validation gaps** (only the game machine can settle these):
- whether the game accepts a 6500 uu/s state-set, or clamps it on input;
- whether the game has a rolling resistance the port lacks;
- PowerShell, `run_batch.ps1` and the BakkesMod plugin.

The game-machine run this PR exists to enable will settle the first two.

## Verdict
**Changes required.**
