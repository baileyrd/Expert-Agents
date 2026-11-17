<!-- rocket-league/973-r1 run 4, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 206 s, 1058232 tokens, $1.00 -->

**Changes required.** PR 973 adds three tape-bot probes. The two speed-cap probes are sound, but `probe_ball_rest` can't do what it and the change fragment say it does: the ball doesn't stop within its 10 s tape.

- **Repository:** Rusty-Mill/rusty_mill, PR #973
- **Base:** 15296ea5bb670d14acb22df4f856fadec3e8280a
- **Head:** 1f2ec4af1f652400ea4dda8e5093a121cdb86646. I fetched `pull/973/head` and it matches the head SHA.
- **What the diff touches:** round 6 of `gen_probes.py`, three experiment files (`probe_ball_fast_4500`, `probe_ball_fast_6500`, `probe_ball_rest`) and the change fragment. No Rust code, physics code or wire format changes.

## Blocking findings

### P2: `probe_ball_rest` can't measure the ball coming to rest, and the fragment says it will (*executed* and *source-traced*)

**Where:**
- `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/gen_probes.py:166-167`
- `.../experiments/probe_ball_rest.json:30-43`
- `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:7` ("a 600 uu/s roll held for 10 s so the ball stops")

**What the scenario does:** it starts the ball at (0, −2000, 93.15) moving 600 uu/s along +y with no spin. It runs 60 settle ticks plus 1200 tape ticks, about 10.5 s at 120 Hz.

**What the port predicts (executed):** I ran the head's `rb_verify_cli::simulate_scenario` from a scratch crate. The ball:
- slides from 600 to about 418 uu/s by tick 120;
- then rolls and loses speed only to drag;
- is still moving at **343.7 uu/s at tick 1200** and **339.9 uu/s at the last frame**, at y ≈ 2075.

It never gets near the port's own sleep threshold of 20 uu/s (`PHYSICS-PARITY-VS-ROCKETSIM.md:198`).

**What the game shows:**
- **Rolling slows only by drag.** In the committed capture `fixtures/probe_ball_roll.jsonl`, frames 448 to 466 are flat floor (z 93.15) with pure rolling. Speed falls 547.40 → 544.90 uu/s in 0.15 s. That is 16.68 uu/s², against 16.64 uu/s² from drag alone (`BALL_DRAG` 0.03, `v *= 0.97^t`). So the game shows no measurable rolling resistance.
- **The ball still won't stop in 10 s.** With drag alone, a ball rolling at about 428 uu/s after the slide is still well above 300 uu/s after 10 s. It would take about 78 s to fall below the video caption's 40 uu/s threshold (`ln(428/40) / -ln 0.97`).
- **600 uu/s isn't a roll.** `PHYSICS-PARITY-VS-ROCKETSIM.md:71,90` records that pure rolling is capped at 6 rad/s × 91.25 = 547.5 uu/s. The "600 uu/s roll" therefore starts as a slide.

**Impact:** a run on the game machine would spend its capture without measuring the sleep thresholds that board b19 and `PHYSICS-PARITY-VS-ROCKETSIM.md:78` list as open. The fragment and the scenario description claim more than the port or the committed capture support.

**Required correction:**
1. Start the ball already rolling, below the thresholds under test. Set `angular_velocity` to `[v/91.25, 0, 0]` for velocity `[0, v, 0]`.
   - For example, v ≈ 35 uu/s held for at least 5 s tests the caption's 40 uu/s / 2.5 s rule.
   - A second probe at about 15 uu/s tests the port's 20 uu/s / 0.5 s placeholder.
   - Alternatively, make the tape long enough for drag to reach the threshold, about 80 s from 428 uu/s.
2. Fix the scenario description and the fragment so they say what the probe can show.
3. **Check:** `rb-verify --scenario experiments/probe_ball_rest.json` (or the `simulate_scenario` ball trace) should predict the ball reaching sleep within the tape. It goes red on the current file, where the ball ends at 339.9 uu/s.

## Design questions

None.

## Optional improvements

- **Make the cap probe readable from frame one.** The README notes 2 to 7 dropped ticks at the state-set frame. If the 6500 run's first recorded frame already shows about 6000 uu/s, you can't tell "the game clamped the state-set" from "the first physics tick clamped it". The 4500 control helps. Recording each run's first frame and the gap before it in the results would settle it.
- **Parse every experiment file in a test.** Nothing in `rb_verify_cli/tests` reads every file in `experiments/*.json`. I confirmed the three new files parse through `Scenario::from_json`.
- **Generator float drift (existing before this PR, not counted against it).** Re-running `gen_probes.py` rewrites the last float digit in `wallg_45_{700,1200,1800}.json`. Round 6's three files regenerate byte-identical.

## What I checked and found sound

- **Cap probes:** they start at 4500 and 6500 uu/s against `BALL_MAX_SPEED = 6000` (`rb_physics_bullet/src/world.rs:28`, RocketSim `RLConst.h:44`), in uu/s like the cap.
  - Port prediction for 6500: clamped to 6000.0 on tick 1, about 145 ticks of capped travel, wall at about tick 146.
  - Port prediction for 4500: never clamped, reaches the wall after tick 180.
  - The ball's path (y = 0) never meets the parked car at (−3500, −4000).
- **Rest height:** 93.15 uu matches `BALL_REST_Z`.
- **"3,541 uu/s":** matches `PHYSICS-PARITY-VS-ROCKETSIM.md:78,91`.
- **Failure classes:** I tried each class from the review checklist against the diff. The only one with a trace is the claim-beyond-evidence finding above.
  - Units are consistent.
  - Tick order, solve order and geometry are untouched.
  - Alignment, framing, hostile bytes and stale frames don't apply to static scenario data.
  - No captures were overwritten and the new names don't collide with existing ones.
- **Tests:** the PR changes no code, so no test can be red at the base. The capture-backed check for the rest probe is the one in the required correction.

## Checks run

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `python3 .github/scripts/changes.py check` | 282 fragments ok |
| `python3 -I -m unittest discover -s .../rb_tape_bot` | 15 tests, OK |
| `cargo build --release -p rb_verify_cli` (scratch target and cargo home) | built |
| Port ball trace for all three new probes (scratch crate calling `simulate_scenario`) | numbers above |
| Rolling deceleration measured from committed `probe_ball_roll.jsonl` | matches drag only |
| `gen_probes.py` regenerated in scratch and diffed | round 6 identical |

**Not run:** clippy and `cargo test` for the physics crates, and the golden captures. No Rust changed, so they don't exercise this diff. I didn't need the private corpus.

**Validation gaps** (only the game machine can settle these):
- whether the game accepts a 6500 uu/s state-set;
- the real cap behaviour;
- the real sleep thresholds.

PowerShell (`run_batch.ps1`) and the BakkesMod plugin weren't exercised.

## Verdict

**Changes required.**
