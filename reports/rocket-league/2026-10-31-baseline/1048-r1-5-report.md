<!-- rocket-league/1048-r1 run 5, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 389 s, 1491896 tokens, $1.51 -->

## Review: Rusty-Mill/rusty_mill PR #1048

- **Base:** `aa414575c7272aa99f81b510aece522eac07a492`
- **Reviewed head:** `9a29bffdbc778999e4f4d2d00fe069612a53229d` (`pull/1048/head` was fetched and matches)
- **Scope:** one commit. The ball sleep constants change to 40 uu/s, 1.0 rad/s and 2.0 s. The ball is now also capped at the start of each tick. Six ball captures become golden fixtures, and FR-037 and FR-061 change.

**Verdict: changes required.** The core mechanics hold up against the captures. The blocking issues are the claims about what the captures prove and some figures that don't match the code.

### What holds up
- **Golden fixtures (executed).** I ran the head's fixtures and bounds against the base physics. At base, rest_10, rest_20, rest_40 and fast_6500 all go over their bounds (ball max error 13.58, 22.63 and 112.40 uu; fast_6500 mean 10.57). At head they all pass. Each new bound is the error measured at head plus a quarter.
- **The start-of-tick cap is right.** The port's fast_6500 velocities match the game to about 0.01 uu/s each tick: 6500, then 5996.581 against the game's 5996.58, then 5993.163 against 5993.16. The new unit test `a_ball_set_over_the_cap_is_capped_before_its_first_step_moves_it` fails at base (54.17 uu moved, limit 50.0) and passes at head.
- **The timer is right.** The game freezes rest_10, rest_20 and rest_40 at the same timestamp, 2.017 s after the state set. 40 uu/s with a strict `<` fits the game's 39.98 uu/s that still froze. Units are correct: Bullet's 0.8 m/s × 50 = 40 uu/s.

### Blocking findings

**1. P2: the claimed bracket on the linear threshold, (40.0, 68.5] uu/s, does not follow from the captures. The rest_50 and rest_60 probes cannot narrow it either.**
- **Where:**
  - `tools/rb_tape_bot/fixture_checks.py:216,223` computes and prints the bracket.
  - The claim is repeated at `rb_physics_bullet/src/body.rs:99-101`, `RB-PHYSICS-001…md:2207-2209`, `changes/2026-10-25-rl-ball-sleep-calibration.md:11` and `gen_probes.py:172`.
- **Failure (executed against the fixtures):**
  - rest_80 never freezes, but its spin stays between 0.75 and 0.88 rad/s the whole time. The angular threshold is only known to be at least 0.438 rad/s. So "rolls on" fits either a linear threshold at or below the ball's speed or an angular threshold at or below its spin. The capture gives no upper bound on the linear threshold by itself.
  - Even assuming the angular threshold is high enough, the upper bound is wrong. A ball is frozen 2.017 s after it drops under the threshold. Not frozen by 5.0 s therefore means it was still at or above the threshold at about 2.98 s. The capture shows 72.95 uu/s at 3.0 s. `ball_rest` uses the slowest speed in the file instead (`min(r[1] …)` = 68.5) and ignores the timer.
  - rest_50 and rest_60 start with matching spin (0.548 and 0.658 rad/s). If either keeps rolling, that still cannot tell the linear threshold from the angular one.
- **Required correction:**
  - State the result as a joint constraint: linear threshold ≤ ~73 uu/s **or** angular threshold ≤ ~0.80 rad/s.
  - In `ball_rest`, take the bound from the speed one timer-length before the last frame, and report spin alongside it.
  - Add probes that separate the two thresholds, for example spin with no linear speed, or speed with spin held well under 0.44 rad/s.
  - Add a `test_fixture_checks.py` case: a rolling series whose speed is above the bound at end minus 2 s but below it at the end. It should fail against the current `ball_rest`.

**2. P2: the stated cause of rest_40's 11 uu residual is false.**
- **Where:**
  - `RB-PHYSICS-001…md:2212-2214`
  - `golden_captures.rs:177-178`
  - the change fragment, line 11
- **Their claim:** the port's ball slows 2.1%/s against the game's 3.0%/s, so it drops under 40 uu/s about 0.25 s late.
- **What actually happens (executed, port trace against the capture):**
  - A ball that starts at 40.0 and only decays is under 40 from its first tick, whatever the decay rate. Instead, the port's ball **speeds up** on its first tick: 40.000 → 40.231 uu/s, while the game goes to 39.981. It stays above 40 until somewhere between ticks 30 and 60, which keeps resetting the sleep timer.
  - Result: the port freezes at row 273 and the game at row 241. rest_10 shows the same jump (10.000 → 10.058).
  - The likely cause, from the source: the probe sets spin as v / 91.25 (`gen_probes.py`, `BALL_R`), but the port's collision radius is `BALL_RADIUS = 93.15` (`body.rs:112`). So ω·r = 40.85 uu/s, which is a sliding contact in the port, not rolling.
  - The 2.1%/s against 3.0%/s decay figures are correct, but they are not what delays the freeze.
- **Required correction:** restate the cause (the first-tick speed gain from the radius mismatch) in the FR, the fixture comment and the fragment. Add a rest_40 check of the first-tick speed, which goes red on that mismatch.

**3. P2: several error figures in the docs don't match what the head produces (executed).**
- **What differs:**
  - **rest_20 max:** the fragment and FR say 1.6 uu; the head measures 1.44 uu. The bound 1.8 equals 1.44 × 1.25, so the bound itself was set from 1.44.
  - **fast_6500 mean:** the docs say 10.3 → 5.4 uu; the measured values are 10.57 → 5.68 uu. The bound 7.1 equals 5.68 × 1.25.
  - **rest_40 base max:** the docs say 113.0 uu; the measured value is 112.40 uu.
- **Where:** `changes/2026-10-25-rl-ball-sleep-calibration.md:7-8`, `RB-PHYSICS-001…md` Calibration and Amendment entries.
- **Required correction:** quote the figures the bounds were derived from.

### Design question
**A sleeping ball can't be woken by a slow push, and this PR widens that range from about 20 to about 40 uu/s.**
- FR-037 settles per-body sleep with no islands, so I'm not counting this as a defect. But the PR adopts Bullet's thresholds without Bullet's wake rule. In Bullet, an island that contains an awake body clears `ISLAND_SLEEPING` on its members and resets their deactivation time (`btSimulationIslandManager` activation update).
- **Executed probe:** a resting ball sleeps; then a car held at a set speed and kept awake pushes into it for 1 s.

| Car speed | Base | Head |
|---|---|---|
| 15 uu/s | ball stays asleep | ball stays asleep |
| 30 uu/s | ball wakes, moves 26.0 uu | ball stays asleep, moves 11.7 uu |
| 38 uu/s | ball wakes, moves 31.5 uu | ball stays asleep, moves 16.3 uu |

- In the game the car would keep the ball awake.
- **Should the port wake a body on contact with an awake dynamic body?**

### Optional improvements
- Applying the ball's sleep thresholds to cars departs from RocketSim, which keeps cars always active. It is documented as unmeasured, but a car capture with a slow coast and no input would settle it.
- A sleeping ball's position still changes under a push (11.7 uu in the probe above), which shows penetration recovery moves frozen bodies.

### Checks run
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rb_physics_bullet -p rb_verify_cli -p rb_env --all-targets -- -D warnings` | pass |
| `cargo test -p rb_physics_bullet` | pass, 519 tests |
| `cargo test -p rb_env` | pass |
| `cargo test --release -p rb_verify_cli` (golden captures included) | pass |
| Head fixtures and bounds against base physics | red on rest_10, rest_20, rest_40 and fast_6500 |
| New cap unit test at base | red (54.17 uu > 50.0) |
| `python3 -I -m unittest discover -s …/rb_tape_bot` | 18 OK |
| `python3 .github/scripts/changes.py check` | 348 fragments ok |
| `fixture_checks.py ball-rest` | reproduces 2.017 s, 39.98 uu/s, 0.438 rad/s |

- **Sandbox workarounds:** cargo needed `CARGO_HOME` and `CARGO_TARGET_DIR` pointed at scratch directories because the defaults are read-only here. Base and head were built in separate scratch copies with separate target directories.
- **Private corpus:** not present. Tests that need it skip, and this diff doesn't touch them.
- **Validation gaps (need the live game):**
  - rest_50 and rest_60 captures, plus decoupled spin/speed probes, to find the real linear and angular thresholds.
  - A car capture with a slow coast and no input, to check car sleep.
  - A slow car pushing a resting ball, to check wake-on-contact.

### Verdict
**Changes required**, for findings 1–3.
