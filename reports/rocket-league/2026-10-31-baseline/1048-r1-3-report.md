<!-- rocket-league/1048-r1 run 3, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 375 s, 1696836 tokens, $1.46 -->

# Review of Rusty-Mill/rusty_mill PR 1048: ball sleep calibration and the start-of-tick ball cap

- **Base:** `aa414575c7272aa99f81b510aece522eac07a492`
- **Reviewed head:** `9a29bffdbc778999e4f4d2d00fe069612a53229d` (fetched `pull/1048/head`; it matches)
- **Commit:** 9a29bffdb, "Calibrate ball sleep to the game and cap a set ball before it moves"

**Verdict: changes required.** The code changes hold up against the recordings. Two of the PR's evidence claims do not, and they are written into the spec, the code comments, the fragment and the bounds tool.

## What I checked the diff against

- **Ball sleep (FR-037).** The new values are 40 uu/s, 1.0 rad/s and 2.0 s. These are Bullet's defaults scaled at 50 uu per metre: 0.8 m/s, 1.0 rad/s and `gDeactivationTime` of 2.0 s. The units are right.
  - The game freezes rest_10, rest_20 and rest_40 2.017 s after the state set, all on the same tick. I reproduced this with `fixture_checks.py ball-rest`.
  - The port's timer uses `>=` and adds 1/120 s per tick, which is consistent with that.
- **Start-of-tick cap (FR-061 amendment).**
  - **Executed:** on its first tick the port now takes the 6500 uu/s ball to 5996.581 uu/s, the same as the game's 5996.58.
  - The end-of-tick cap still runs after the move, which matches RocketSim's `_FinishPhysicsTick`. The extra cap at the start is a deliberate difference from RocketSim, and the FR amendment records it.
- **Determinism:** neither change touches iteration order or adds any state.
- **Bounds:** the new golden bounds are the measured error plus about a quarter. The two raised `wob` bounds come with a stated reason.

## Blocking findings

### 1. P2: the "linear threshold bracketed to (40.0, 68.5] uu/s" claim does not follow from the captures

**Where:**
- `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/fixture_checks.py:216,223-225`
- `rb_physics_bullet/src/body.rs:98-101`
- `RB-PHYSICS-001-physics-core-port.md:2206-2209`
- `changes/2026-10-25-rl-ball-sleep-calibration.md:11`

**What is wrong (source-traced and executed on the fixtures).** `ball_rest` takes the upper bound from rest_80's *slowest* speed (68.5 uu/s at the very end of the capture) and assigns it to the linear threshold alone. Two things are wrong with that:

1. **Timer.** A ball only freezes after 2.0 s under the thresholds. 2.0 s before the end of the capture, rest_80 was still at **72.9 uu/s** (and 0.80 rad/s). A linear threshold of 70 would still never have frozen it, yet the doc rules 70 out.
2. **Both thresholds share the result.** The rest probes roll without sliding (spin = v / 91.25), so linear and angular speed move together. A ball freezes only when both are under their thresholds. So rest_80 staying awake only shows that the linear threshold is ≤ ~72.9 uu/s **or** the angular threshold is ≤ ~0.80 rad/s. An angular threshold anywhere in (0.44, 0.80] rad/s explains rest_80 just as well. That means "the angular one only bounded below" is also too strong.

The new rest_50 and rest_60 probes (spin 0.55 and 0.66 rad/s) have the same coupling, so they cannot "narrow the linear threshold" as `body.rs:100` and `gen_probes.py` say.

There is also a small mismatch: the tool's lower bound is 39.98, but the docs write "(40.0", which shuts out the 40.0 the code actually uses.

**Required correction:**
- In `ball_rest`, take the bound for a ball that never froze from its speed and spin at the last frame minus the timer (2.017 s).
- Report that bound as a joint condition: linear ≤ v *or* angular ≤ ω.
- Restate the bracket in `body.rs`, FR-037 and the fragment.
- Add a probe that separates the two thresholds. For example, a ball resting in place spinning only about the vertical axis at 0.6–0.95 rad/s; Bullet has no spinning friction to couple that to linear speed.

**Test that goes red without the fix:** a `test_fixture_checks.py` case where the speed is above the threshold 2 s before the end and below it only at the end. It should expect the bound from the earlier frame, not the final minimum.

### 2. P2: the reason given for rest_40's remaining 11 uu error is wrong

**Where:**
- `golden_captures.rs:177-179`
- `RB-PHYSICS-001-physics-core-port.md:2212-2214`
- `changes/…sleep-calibration.md:11`

**What the PR says:** the port's ball slows by 2.1%/s against the game's 3.0%/s, so it drops under 40 uu/s about 0.25 s late.

**Why that cannot be the cause:** a ball set at exactly 40.0 uu/s that only decays is under 40 after one tick, whatever the decay rate.

**What actually happens (executed, scratch probe through `rb_verify_cli::simulate_scenario`):**
- On its first tick the port's ball goes from **40.000 to 40.231 uu/s**, and its spin drops from 0.4384 to 0.4319 rad/s.
  - The ratio 40.231 / 0.4319 = 93.15, so the port rolls the ball at `BALL_RADIUS` 93.15.
  - The probe sets spin for 91.25 (`gen_probes.py:162`), so the port sees too much spin, and the first tick turns it into speed.
- The game slows to 39.981 uu/s at 0.43811 rad/s on that tick, which is rolling at 91.26.
- From 40.231 uu/s at about 0.88 uu/s², the port takes about 0.26 s to get under 40.
  - It freezes at tick ~272; the game freezes at tick 242.
  - That 30-tick gap accounts for all of the 11.1 uu maximum error.

**Required correction:**
- Credit the residual to the rolling-radius mismatch (port 93.15, game ~91.25), as the cause of the first-tick spin-to-speed jump.
- Keep the decay-rate difference as a separate, smaller effect.
- The next fix will be aimed at the wrong mechanic if this stays as written.

## Design question

The port rolls the ball at a radius of 93.15 while the game rolls at about 91.25. The game's rest captures show v/ω = 91.26 on every frame. This was already there before this PR and isn't caused by it. But it changes when every slow rolling ball freezes, and it is the real reason for the rest_40 and rest_80 residuals. It needs an owner decision against ADR-0034 / FR-127.

## Optional improvements

- The `step()` doc comment (`world.rs:1209-1215`) and the inline comment at about `world.rs:1517` still say the end-of-tick cap runs "before sleep evaluation and transform integration". The code runs it after `integrate_transform_and_refresh_inertia` (`world.rs:1532-1537`). This predates the PR, but the PR rewrote that same sentence and left it wrong.
- In `PHYSICS-PARITY-VS-ROCKETSIM.md`, the Sleeping row is now marked ✅ for the whole mechanism. Cars now freeze below 40 uu/s after 2.0 s instead of 20 uu/s after 0.5 s, and that remains unmeasured (RocketSim keeps cars active every tick). A ✅ for the ball and a ⚠ for cars would be more accurate.
- fast_6500's maximum error got slightly worse, from 52.95 to 55.2 uu (the wall-seam rebound). The mean improved, as the fragment says.

## Checks run, and validation gaps

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rb_physics_bullet / rb_env / rb_verify_cli --all-targets -- -D warnings` | all clean |
| `cargo test --release -p rb_physics_bullet -p rb_env -p rb_verify_cli` | pass: 519 physics unit tests, 28 env + 3 determinism + 1 full-match tests, `golden_captures` 3/3, `human_captures` 5/5 (committed fixtures; nothing skipped) |
| Tape-bot Python unittest | 18 tests OK |
| `python3 .github/scripts/changes.py check` | 348 fragments ok |
| **Red at base:** head's golden cases on base code | fail on rest_10 (13.58), rest_20 (22.63), rest_40 (112.40) and fast_6500 (mean 10.57) |
| **Red at base:** head's `world.rs` with the start-of-tick cap removed | `a_ball_set_over_the_cap_is_capped_before_its_first_step_moves_it` fails: 54.17 uu moved against at most 50 |
| `rb-verify --scenario … --against` on the six new probes and both `wob` cases | ball errors match the fragment to within 0.3 uu, except `wob_0_200`'s ball max, which reads 69.1 here against the fragment's 70.2 |

**Validation gaps** (these need the game):
- Run rest_50 and rest_60, plus a vertical-spin-only probe, to pin the two thresholds separately.
- A car capture is needed to tell whether the game's cars sleep at all.

Builds used a cargo home and target directory under `$TMPDIR` because `~/.cargo` is read-only. The probes ran in scratch copies outside the reviewed tree.

**Verdict: changes required.**
