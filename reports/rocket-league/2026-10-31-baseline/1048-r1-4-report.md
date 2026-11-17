<!-- rocket-league/1048-r1 run 4, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 466 s, 2481889 tokens, $1.80 -->

# PR review: Rusty-Mill/rusty_mill #1048

- **PR:** Rusty-Mill/rusty_mill #1048, "Calibrate ball sleep to the game and cap a set ball before it moves"
- **Base:** `aa414575c7272aa99f81b510aece522eac07a492`
- **Reviewed head:** `9a29bffdbc778999e4f4d2d00fe069612a53229d`. It matches the fetched `pull/1048/head`.
- **Verdict: changes required.** There are four P2 findings, all in evidence or documentation claims. I found no defect in the physics code itself.

## What holds up

- **New constants and their source:** 40 uu/s, 1.0 rad/s and 2.0 s are Bullet's defaults scaled at 50 uu per Bullet unit (0.8 m/s × 50 = 40 uu/s; `gDeactivationTime` = 2.0 s). They sit inside what the captures show. I ran `fixture_checks.py ball-rest` and got: rest_10, rest_20 and rest_40 freeze 2.017 s in; rest_80 never freezes and bottoms out at 68.5 uu/s; the linear threshold is in (39.98, 68.5]; the angular threshold is at least 0.438 rad/s.
- **The extra cap at the start of the tick** (`world.rs:1284`) matches the capture. In `probe_ball_fast_6500` the game's first tick takes the ball from 6500 to 5996.58 uu/s and moves it 5996.4/120 uu. That is the cap, then drag, then the move. The difference from RocketSim is recorded as an amendment to FR-061.
- **Raised wob bounds:** the reason given is correct (traced in the old and new code with a scratch probe). In the old code the car freezes at tick 89 on top of a ball still moving at 15.5 uu/s, and the ball freezes at tick 107/111. The game freezes neither (`wob_0_*` ends with the ball at 120–140 uu/s). The new bounds are the measured error × 1.25, as the harness header says.
- **Freeze tick:** once the recordings are aligned, the port and the game stop on the same tick in rest_10.
- **No determinism, wire-format or byte-parsing surface is touched.**

## Blocking findings

### 1. P2: the explanation for rest_40's remaining error is wrong

**Where:** `RB-PHYSICS-001-physics-core-port.md:2212-2214`, `golden_captures.rs:177-178`, change fragment line 11, and the commit message.

**What the PR says:** the remaining rest_40 error is rolling decay. The port's ball slows 2.1%/s against the game's 3.0%/s, so it drops under 40 uu/s about 0.25 s late.

**What actually happens (run with a scratch probe that calls `simulate_scenario`):**
- **The port speeds up on tick 1.** The ball goes from 40.000 to 40.2307 uu/s and its spin drops from 0.4384 to 0.4319 rad/s. The scenario sets spin = v / 91.25 (`gen_probes.py:162`, the contact radius). The port's ball rolls on 93.15, so friction trades spin for speed: 0.4319 × 93.15 = 40.23.
- **It then stays at or above 40.0 for 31 ticks.** It is still at 38.30 at tick 272 and freezes at tick 273.
- **The game slows instead.** It goes from 40 to 39.981 on its first tick and freezes about 30 ticks earlier.
- **Decay alone would not cause the delay.** Starting at 40.0 and slowing 2.1%/s, the port would be under 40 after one tick and freeze on the game's tick. At 3.0%/s, the 0.23 uu/s excess would still take about 0.19 s to lose.

**Cause:** the delay comes from the radius difference at the state set (93.15 vs 91.25, ADR-0034), combined with a threshold placed exactly at the lower edge of the bracket.

**Correction:** restate the cause in the FR-037 Calibration section, the golden-case comment and the change fragment. Optionally say that a threshold inside the bracket but above 40.23 would freeze rest_40 on time, which belongs with the open question about the threshold's upper bound. The rest_40 golden bound (8.1 / 13.8) already guards the behaviour, so no new test is needed for a wording fix.

### 2. P2: the reported numbers don't match the code, and fast_6500's max error got worse without a mention

**Where:** the change fragment (lines 7–8 and 11), `RB-PHYSICS-001-physics-core-port.md:2211-2212` and `:4008`, and the commit message.

I ran `rb-verify --scenario … --against … --recorded-inputs` (release builds) on the old and new code:

| Capture | Value | PR says | Measured, old → new |
|---|---|---|---|
| fast_6500 | ball mean | 10.3 → 5.4 | 10.6 → 5.7 |
| fast_6500 | ball max | 55 | 53.0 → 56.8 |
| rest_20 | ball max | 1.6 | 1.4 |
| rest_40 | ball max, old code | 113.0 | 112.4 |

- **The new bounds in `golden_captures.rs` come from my numbers, not the PR's:** 5.7 × 1.25 = 7.1, 56.8 × 1.25 = 71.1 and 1.4 × 1.25 = 1.8.
- **The cap change made fast_6500's max error 3.8 uu worse.** That error is after the side-wall rebound, so its size depends on how long the tape runs. Nothing in the PR says so.

**Correction:** update the numbers in the FR, the fragment and the commit text, and state the max regression with its cause. No test is needed.

### 3. P2: the stated bracket excludes the chosen value

**Where:** `body.rs:99-100`, `RB-PHYSICS-001-physics-core-port.md:2208` and fragment line 11.

- The PR states the linear threshold is "bracketed to (40.0, 68.5] uu/s". That open interval excludes the chosen constant, 40.0.
- The tool the PR cites prints (39.98, 68.5]. In the game's rest_40 capture the fastest stepped frame is 39.98 uu/s.

**Correction:** write (39.98, 68.5].

### 4. P2: the parity doc contradicts itself on the ball speed cap

**Where:** `PHYSICS-PARITY-VS-ROCKETSIM.md:91`.

- **The ball-cap row is stale.** It still says "the 6000 uu/s ball speed cap is untested (the captures reach 3,541)", while line 78 of the same file now says the cap is settled.
- **The row omits the new difference from RocketSim.** The port now also caps at the start of the tick. RocketSim caps only in `Ball::_FinishPhysicsTick`, as `world.rs:1530-1533` itself notes.

**Correction:** add the V2 evidence from fast_6500/4500 to row 91 and record the start-of-tick cap there.

## Design questions

- **Car sleep thresholds:** they doubled without any car capture, 20 → 40 uu/s and 0.5 → 1.0 rad/s. The PR states this openly, but car sleep differs from RocketSim (which keeps cars always active), and this change widens that difference. A car coasting below 40 uu/s with no input now freezes after 2 s. The owner should decide whether to keep velocity-based car sleep at all until a car capture measures it.

## Optional improvements

- Set the rest-ladder spin with the port's rolling radius, or record the radius difference beside `BALL_R` in `gen_probes.py`. The PR adds the rest_50/60 scenarios with the same mismatch.

## Checks run

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rb_physics_bullet -p rb_verify_cli -p rb_env --all-targets -- -D warnings` | pass |
| `cargo test --release -p rb_physics_bullet -p rb_env -p rb_verify_cli` | pass: 519 + 28 + 3 + 1 + 52 + golden 3 + human 5 + write_candidate 7. The 519 matches the FR's count. |
| New test `a_ball_set_over_the_cap_is_capped_before_its_first_step_moves_it` on the old code | **fails there as it should**: got 54.17 uu, expected ≤ 50.0 |
| Sleep calibration on the old code | the new golden bounds would fail: rest_10/20/40 ball max 13.6 / 22.6 / 112.4 against bounds 1.4 / 1.8 / 13.8 |
| `python3 -I -m unittest discover -s …/rb_tape_bot` | 18 tests OK |
| `python3 .github/scripts/changes.py check` | 348 fragments OK |
| `rb-verify` comparisons, old vs new code, on the 6 new fixtures and 2 wob fixtures | numbers in Finding 2 |
| Scratch probes of ball and car speed per tick (outside the reviewed tree) | traces in Finding 1 and "What holds up" |

**How the checks ran:**
- The cargo registry is on a read-only filesystem, so I used a cargo home and build directories under `$TMPDIR`.
- I built the old code in a scratch copy under `$TMPDIR`. The reviewed tree is unmodified.
- None of these tests needed the private corpus.

## Validation gaps

These need the game:
- **Upper bound of the linear threshold:** run `probe_ball_rest_50` and `_60`.
- **Angular threshold:** needs a ball spinning above 0.44 rad/s while moving below 40 uu/s.
- **Car sleep:** needs a capture of a car slowing below 40 uu/s with no input for more than 2 s.
- **Why the game caps on state set:** the in-game cause can't be checked from here.
