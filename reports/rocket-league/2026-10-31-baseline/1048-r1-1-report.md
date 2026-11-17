<!-- rocket-league/1048-r1 run 1, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 410 s, 1986486 tokens, $1.68 -->

# Review: PR 1048, Rusty-Mill/rusty_mill. Ball sleep calibration and the start-of-tick ball cap

- **Base:** `aa414575c7272aa99f81b510aece522eac07a492`
- **Reviewed head:** `9a29bffdbc778999e4f4d2d00fe069612a53229d`. I fetched `pull/1048/head`, it matches, and it is a single commit.

**Verdict: changes required.** The two code changes are supported by the captures:
- **Ball sleep:** 40 uu/s and 2.0 s is Bullet's 0.8 m/s and `gDeactivationTime` at 50 uu per metre, and the four `probe_ball_rest_*` captures support it.
- **Start-of-tick cap:** `probe_ball_fast_6500` supports it. The game moves 49.97 uu on the first tick at 5996.58 uu/s.

Both are guarded by checks that fail at base. The problems are in what the PR claims beyond those captures, plus one small timing divergence.

## Blocking findings

### F1 (P2): the PR says no capture measures car sleep, but its own captures show the game's car never sleeps

- **Where:** `crates/rb_physics_bullet/src/body.rs:101-102`, RB-PHYSICS-001 FR-037 Calibration at lines 2214-2215, the change fragment at line 11, and `PHYSICS-PARITY-VS-ROCKETSIM.md:229`.
- **Claim in the PR:** "The game's cars are assumed to share them … no car capture measures it yet."
- **What the captures show (traced from the fixtures):** every new rest probe also records a parked car at (-3500, -4000, 17).
  - Over the 5.02 s captures, the car's speed stays at or below 1.84 uu/s, its spin at or below 0.107 rad/s, and z stays at 17.000. It reports a steady velocity of (0, 0, 0.371).
  - Velocity and spin are both exactly zero in 0 frames after the state-set frames, across `probe_ball_rest_{10,20,40,80}` and `probe_ball_fast_4500`.
  - So the car sits under both thresholds for about 4.8 s and the game never freezes it, while the ball next to it freezes 2.017 s in.
  - This matches RocketSim keeping cars `ACTIVE_TAG`, as the parity table's own RocketSim column says.
- **What the port does (executed in a scratch probe calling `simulate_scenario` on `probe_ball_rest_10`/`_40`):** the car's velocity and spin are zeroed from frame 241 on, for 361 frames. The game shows 0 such frames.
- **Why it matters:**
  - The golden check compares positions only, so this divergence is invisible to it.
  - The PR widens the car-freeze range from under 20 uu/s to under 40 uu/s.
  - The port has no islands (`body.rs:85-86`), so a ball touching a sleeping car is not kept awake the way Bullet would keep it.
- **Required correction:** either exempt cars from sleep (the game and RocketSim agree on this), or keep car sleep as a recorded divergence with the doc fixed to quote this evidence. In all four places, remove "no car capture measures it".
- **Regression check:** in the rest probes, assert that the simulated car is not frozen after tick 241, or that its velocity tracks the capture's. Today `golden_captures.rs` has no velocity channel that could catch this.

### F2 (P2): the stated cause of the remaining rest_40 error is wrong

- **Where:** FR-037 Calibration at RB-PHYSICS-001 lines 2212-2214, the fragment at line 11, and `golden_captures.rs:177-178`.
- **Claim in the PR:** rolling decay (2.1%/s against 3.0%/s) "delays its drop under 40 uu/s by about 0.25 s".
- **What actually happens (executed):**
  - In the port, the ball set at 40.0 uu/s with spin 0.4384 jumps to **40.23 uu/s** on tick 1, and its spin drops to 0.4319. The new v/ω is about 93.15, the ball's centre height, not the 91.25 contact radius.
  - It then stays at or above 40 uu/s until frame 32, and freezes at frame 273.
  - The game goes 40.0 → 39.981 on tick 1 and freezes at frame 242.
- **Conclusion:** both decay rates fall from 40, so decay alone would cross below 40 on tick 1. The 0.27 s delay comes from the first-tick transfer of spin into speed; slower decay only stretches it.
- **Why it matters:** the PR points the next fix at rolling friction when the cause is the rolling radius or the first contact. The jump itself predates this PR; the misattribution is new.
- **Required correction:** reword all three places.
- **Regression check:** a scenario assertion that the port's ball speed in `probe_ball_rest_40` does not rise above its set speed on tick 1.

### F3 (P2): the port freezes the ball one tick before the game and Bullet

- **Where:** `body.rs:470-485`, which freezes the ball in the same tick its timer reaches 2.0 s.
- **Game and Bullet (traced from the source and the captures):**
  - In f32, the sum of 1/120 steps first exceeds 2.0 at tick 241 (2.00833).
  - In Bullet, `updateActivationState` runs at the end of that tick and marks the body `WANTS_DEACTIVATION`.
  - On tick 242, `buildIslands` puts the island to sleep, `integrateTransforms` skips the body, and its velocity is zeroed.
  - The game's zero velocity lands at 2.017 s, which is tick 242.
- **The port (executed):** freezes `probe_ball_rest_10` at frame 241 (t = 2.008 s), one tick early.
- **Impact:** a position offset of v/120, which is 0.08 uu at 10 uu/s and 0.33 uu at 40 uu/s.
- **Required correction:** add Bullet's one-tick lag, or record the difference in FR-037.
- **Regression check:** assert the frozen frame's timestamp in `fixture_checks`/golden against the capture's 2.017 s.

### F4 (P2): quoted numbers do not match the cited golden check

I ran `golden_captures` at head with the errors printed:

| Quantity | PR claims | Measured |
|---|---|---|
| rest_20 ball max | 1.6 | 1.44 |
| fast_6500 ball mean at head | 5.4 | 5.68 |
| fast_6500 ball mean at base | 10.3 | 10.57 |
| fast_6500 max | "55" | 56.8 |

- **Where:** the fragment at lines 7-8 and 11, and RB-PHYSICS-001 at lines 2212 and 4008.
- **Required correction:** quote the numbers the golden harness produces, or name the run that produced the PR's numbers.

## Design questions

- **Sleep without islands:** Bullet keeps a body awake while it touches an active body, and the port has no such islands (`body.rs:85-86`). A ball resting on a parked car, or pushed slowly for 2 s, can freeze. Once it is asleep, gravity's 5.4 uu/s per tick stays under 40 uu/s, so it never wakes on its own. This predates the PR, but calling the mechanic "✅ calibrated" is only true for an isolated ball.
- **Start-of-tick cap and RocketSim:** the cap now diverges from RocketSim's end-of-tick-only placement. The FR-061 amendment records this, which is fine. The angular part of the start-of-tick cap is untested: the fast probes have zero spin.

## Optional improvements

- **Stale `step` doc comment** (`world.rs:1211-1215`): it still says the cap runs "before sleep evaluation and transform integration". The code caps after integration (`world.rs:1537`). This predates the PR, but the PR edited that sentence and left it wrong.
- **Two cases don't guard this PR:** `probe_ball_rest_80` and `probe_ball_fast_4500` pass at base. That's fine for new fixtures, but they don't test this change.

## Tests: do the new checks fail at base?

I ran head's golden cases and fixtures against base code in a scratch copy:
- rest_10 fails: 10.22 / 13.58 uu
- rest_20 fails: 16.44 / 22.63 uu
- rest_40 fails: 34.53 / 112.40 uu
- fast_6500's mean fails: 10.57 uu

The new cap unit test fails at base by source trace: the ball moves about 54.1 uu on the first tick, over the 50.001 uu limit.

The raised bounds on `wob_0_200`/`wob_0_500` have a stated reason that is consistent with the captures: they are 1.5 s long, so a 2.0 s timer can't fire. Measured errors are 18.4 / 70.2 and 7.19 / 33.39 uu, within the new bounds with about 25% headroom.

## Checks run

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rb_physics_bullet -p rb_env -p rb_verify_cli --all-targets -- -D warnings` | pass (1) |
| `cargo test --release` on the same three crates | pass: rb_physics_bullet 519, rb_env 28+3+1, rb_verify_cli 52, golden 3, human_captures 5, write_candidate 7 |
| Tape-bot Python unittest | 18 tests pass |
| `changes.py check` | 348 fragments ok |
| `fixture_checks.py ball-rest` | reproduces the 2.017 s freeze and the (39.98, 68.5] bracket |

(1) The read-only `~/.cargo` and the default target directory blocked the first runs. I re-ran offline with `CARGO_HOME` and `CARGO_TARGET_DIR` pointed at `$TMPDIR` copies.

I did not confirm whether `human_captures` skipped any private-corpus cases.

## Validation gaps (need the live game)

- **Linear threshold:** `probe_ball_rest_50`/`_60` would narrow the 40.0–68.5 uu/s bracket.
- **Angular threshold:** needs a ball with spin of at least 1 rad/s at low speed.
- **Angular cap on state-set:** needs a ball state-set with spin over 6 rad/s.
- **Car sleep under island contact:** needs a car resting on the ball for more than 2 s.

PowerShell and the BakkesMod plugin were not exercised.
