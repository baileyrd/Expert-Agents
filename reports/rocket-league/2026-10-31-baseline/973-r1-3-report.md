<!-- rocket-league/973-r1 run 3, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 209 s, 990883 tokens, $0.92 -->

**PR 973 review: Rusty-Mill/rusty_mill.** The probe for a ball at rest can't do its job. I'm asking for changes; the two speed-cap probes are fine.

- **Base:** `15296ea5bb670d14acb22df4f856fadec3e8280a`
- **Reviewed head:** `1f2ec4af1f652400ea4dda8e5093a121cdb86646`. `git fetch origin pull/973/head` gives the same SHA.
- **Diff:** one commit. It adds round 6 to `gen_probes.py`, three scenario files under `experiments/` and one change fragment. No Rust code changes.

### What the diff touches, and what must hold
- **Ball speed cap.** Expected to be 6000 uu/s (RocketSim `RLConst.h:44`); the game has never been measured above 3,541 uu/s (parity doc pass 2). The port caps on the first step.
- **Scenario timing.** The bot sets the start state, runs `settle_ticks` neutral ticks, then the tape, at 120 Hz (tape-bot README:43-44). So the fast probes run 240 ticks (2.0 s) and the rest probe runs 1,260 ticks (10.5 s).
- **Ball on the floor.**
  - Sliding friction is about 228 uu/s² and the spin cap is 6 rad/s; the game measured both.
  - Air drag is 0.03 per second, so speed decays exponentially.
  - The game's rolling threshold is already measured at 547.5 uu/s (`PHYSICS-PARITY-VS-ROCKETSIM.md:71`).
  - The sleep thresholds (40 uu/s, 2.5 s, 10 RPM) are unmeasured figures from a video.
- **New constants:** 4500, 6500 and 600 uu/s; z = 93.15 (the ball's rest height, `RLConst.h:43`); 180 and 1,200 ticks at 120 Hz. All are in the right units.

### Blocking findings

**1. P2: `probe_ball_rest` cannot bring the ball to rest, so it cannot measure the sleep thresholds it is named for.**
- **Where:** `tools/rb_tape_bot/gen_probes.py:165-166` and `experiments/probe_ball_rest.json:2,41`. The changelog at `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:3,7` says the roll is "held for 10 s so the ball stops".
- **From the game recording** (*source-traced*):
  - In `fixtures/probe_ball_roll.jsonl`, frames 448–465, the ball is rolling at about 547 uu/s and loses exactly 0.10 uu/s per tick, which is 12.0 uu/s² or a decay rate of 0.0219 per second. That matches drag reduced by the rolling factor: (5/7)·ln(1/0.97).
  - A ball started at 600 uu/s with no spin slides until it rolls at about 5/7·600 ≈ 429 uu/s (around 0.75 s, spin 4.7 rad/s, below the cap).
  - It then decays to roughly 429·e^(−0.0219·9.75) ≈ 346 uu/s by the end of the tape.
- **From the port** (*executed*): I ran `rb_verify_cli::simulate_scenario` on this file from a scratch crate. The ball speed is 418 uu/s at tick 120, 343.7 at tick 1,200 and **339.9 at tick 1,261**. It ends at y = 2075 and is never close to 40 uu/s.
- **What the evidence requires:** to fall below the hypothesised 40 uu/s it would need about 108 s and about 19,000 uu of travel, so it would hit walls long before.
- **Consequence:** a run on the game machine would come back without the result this probe exists to get.
- **Required fix:**
  - Start slow enough that the rolling speed (5/7 of the launch speed) brackets 40 uu/s, for example launches of 42, 56 and 84 uu/s, which roll at about 30, 40 and 60 uu/s.
  - Hold each for at least 2.5 s plus a margin, for example 600 ticks.
  - Correct the description and the changelog line.
- **Regression check:** run `simulate_scenario` on each rest probe and assert that the port's rolling speed passes below 40 uu/s with at least 2.5 s of tape left. That assertion fails on the current file.

**2. P2: the change fragment says the rolling threshold is unmeasured, but the repository already measured it in the game.**
- **Where:** `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:7`, "neither the cap nor the rolling and sleep thresholds have been measured in the real game". The same claim appears in the probe's name at `gen_probes.py:165`.
- **Trace** (*source-traced*):
  - Parity doc pass 2 (`PHYSICS-PARITY-VS-ROCKETSIM.md:71`, plus its ball-friction row) records the rolling threshold from the game as 547.5 uu/s = 6 rad/s × 91.25. The capture above confirms it: spin leaves the cap at frame 448, when speed is 547.4.
  - The list of untested items at line 78 names only the 6000 cap and the sleep thresholds.
  - This probe cannot measure 547.5 anyway: starting at 600 uu/s with no spin, the ball starts rolling at about 429 uu/s, well below the cap. The port shows the same.
- **Required fix:** limit the claim to the cap and the sleep thresholds, and drop "rolling" from the probe's name.

### Design questions
None.

### Optional improvements
- **Generator output differs here.** Re-running `gen_probes.py` in scratch reproduces the three new files byte for byte. It also rewrites three older round-5 files (`wallg_45_{700,1200,1800}.json`) with a different last digit in a float. The same generator code exists at base, so this PR didn't cause it. Rounding those velocities would make re-runs stable.
- **Stale docstring.** The `gen_probes.py` docstring still describes only rounds 1–2.
- **Expected results.** `probe_ball_fast_6500` could record what the port predicts: 6000.00 after the first tick and 5599 at tick 120, against 4140.9 for the 4500 probe at the same tick. That gives the game run something to check against.

### Checks run
- `git fetch origin pull/973/head`: the fetched head equals the head SHA.
- `python3 .github/scripts/changes.py check`: passes (282 fragments ok).
- `python3 -I -m unittest discover -s …/rb_tape_bot`: 15 tests, OK.
- `cargo fmt --all -- --check`: passes.
- `gen_probes.py` re-run in scratch: the round-6 files match the committed ones.
- `cargo build --release -p rb_verify_cli`, with target and cargo home both in scratch: builds.
- Port runs of all three scenarios with a scratch probe: they parse, and the results are as given above.
  - 4500 probe: hits the side wall around tick 200; top speed 4497.
  - 6500 probe: capped to exactly 6000 on tick 1.
  - Rest probe: as in finding 1.
- Not run: clippy and `cargo test`. The diff changes no Rust crate, and the private corpus wasn't needed.

### Validation gaps (need the game machine)
- **6500 uu/s probe:** whether the game accepts a 6500 uu/s state-set or clamps it there. The capture also drops 2–7 ticks at the state-set frame, which may hide the first frame after the set. The port predicts about 5800 uu/s at the end of settle if the game caps, and about 6080 if it doesn't, so the run can tell the two apart.
- **Sleep thresholds:** the actual values stay unmeasured until a redesigned rest probe runs in the game.

### Verdict
**Changes required.**
