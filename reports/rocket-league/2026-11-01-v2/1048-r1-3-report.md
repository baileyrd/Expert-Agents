<!-- rocket-league/1048-r1 run 3, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 356 s, 1814132 tokens, $1.66 -->

# Review of PR 1048: ball sleep calibration and capping a ball before its first tick moves it

- **Repository:** Rusty-Mill/rusty_mill
- **PR:** 1048
- **Base:** `aa414575c7272aa99f81b510aece522eac07a492`
- **Head:** `9a29bffdbc778999e4f4d2d00fe069612a53229d` (one commit). `FETCH_HEAD` matches the given head SHA.

**Verdict: changes required.** I found five P2 defects and no P0 or P1 defects. The two fixes in the PR are real and both are guarded by tests that fail at base. Five things are wrong:
- One sleep effect, executed: a ball that has fallen asleep can no longer be pushed by a car moving at 35–45 uu/s.
- The threshold "bracket" the PR records is wrong.
- The sleep timer freezes the ball one tick before the game does.
- Four of the new golden bounds were measured at the wrong alignment.
- The `fixture_checks.py` tool computes its upper bound wrongly.

## Blocking findings

### F1 (P2): a car under 40 uu/s can no longer wake a sleeping ball, and the ball acts as a fixed wall
- **Where:** `rb_physics_bullet/src/body.rs:103` (now `40.0`), together with `body.rs:470-486`. The port has no island or contact wake. The world.rs test at 2414 says that waking relies only on the ball's own speed after the contact.
- **Trigger:** a ball that has rested for at least 2 s is touched by a car moving under about 45 uu/s, for example a coasting nudge. This is *executed*: I added a scratch test to a copy of the tree, with the ball resting for 300 ticks at 120 Hz and a car placed against it.

  | Car speed | Head | Base |
  |---|---|---|
  | 35 uu/s | ball never wakes, moves 0.00 uu | ball wakes, rolls 413.9 uu |
  | 45 uu/s | ball never wakes, moves 0.11 uu | ball wakes, rolls 437.4 uu |
  | 60 uu/s | ball wakes | ball wakes |

  The contact's impulse leaves the ball under 40 uu/s, so `update_sleep_state` sets its velocity back to zero every tick. In Bullet, an active body touching a sleeping one joins its island and the island is activated (`btSimulationIslandManager::buildIslands`). In the game, a car touching a resting ball moves it.
- **Required correction:** wake a sleeping body when it is in contact with an awake dynamic body. This is Bullet's island rule in its simplest form. Record it in FR-037.
- **Test:** add a test in `world.rs` where a car at 35 uu/s against a sleeping ball wakes it. It is red at head.

### F2 (P2): the "(40.0, 68.5] uu/s" linear bracket and "angular bounded only below" claim more than the captures show
- **Where:** `body.rs:97-101`, the FR-037 Calibration section (`RB-PHYSICS-001…md:2207-2209`), the change fragment at line 11, `PHYSICS-PARITY-VS-ROCKETSIM.md` (the Sleeping row and §229, which says the game freezes a ball "after 2.0 s under 40 uu/s"), and `gen_probes.py:172` ("50 and 60 narrow the linear threshold").
- **Trace:** *source-traced* from the fixtures. Every `rest_*` probe sets spin equal to v / r, so the linear and angular speeds move together.
  - `rest_80` at t = 3.008 s, which is the latest start from which 2.017 s would still finish inside the capture, is at 72.93 uu/s and 0.799 rad/s.
  - So "never frozen" only rules out the case where the linear threshold is above 72.9 **and** the angular threshold is above 0.80.
  - A linear threshold of 100 uu/s with an angular threshold of 0.5 rad/s fits all four captures. 40 uu/s and 1.0 rad/s are Bullet's defaults; the captures are consistent with them but do not pin them.
  - The `rest_50` and `rest_60` probes are also coupled (0.548 and 0.658 rad/s), so they cannot narrow the linear threshold alone.
  - The lower bound in the evidence is 39.98 uu/s, not 40.0: `fixture_checks.py ball-rest` prints `(39.98, …`.
- **Required correction:**
  - State the constraint as joint: linear below about 72.9 or angular below about 0.80.
  - Label 40 uu/s and 1.0 rad/s as Bullet defaults that the captures do not contradict.
  - Change `rest_50` and `_60` to separate the two thresholds, for example sliding with no spin, or spin without travel.
  - Change the Sleeping row in the parity doc from ✔/✅ to "consistent".

### F3 (P2): `fixture_checks.py ball-rest` computes the upper bound as the slowest speed, ignoring the timer
- **Where:** `fixture_checks.py:216` and `:223`.
- **Trace:** *executed*. The script prints `68.5]`, the speed at the end of the capture. But a ball that dips under a threshold T only freezes 2.017 s later. Any T up to the speed at t_end − 2.017 s (72.93 uu/s) also leaves `rest_80` rolling.
- **Required correction:** take the upper bound from the speed at `last_time − max(freeze secs)`. Add a `Freeze`-style unit test in `test_fixture_checks.py` with a ball that decays below T less than the timer's length before the end; it must not tighten the bound.

### F4 (P2): the port freezes the ball one tick before the game, while the docs say the timer is calibrated
- **Where:** `body.rs:470-486` and `world.rs:1527`, where sleep is evaluated before `integrate_transform`, plus `>=` at `body.rs:475`.
- **Trace:** *executed*, at forced alignment `RB_FORCE_ALIGN=0,0`, which is the true alignment (the game ball moves on frame 1 in every `rest_*` capture, and `fast_6500` matches to 0.003 uu at tick 1).
  - On `rest_10` and `rest_20` the port freezes at tick **241** and the game at tick **242**.
  - In `rest_10` the game moves from y = −1981.49 to −1981.42 on frame 241 and holds from 242.
  - Bullet's `updateActivationState` runs after `integrateTransforms` and sets `WANTS_DEACTIVATION` once the timer is strictly greater than 2.0. The island goes to sleep and stops moving on the next step, which is 242 and matches the game.
  - FR-037 deliberately freezes in the same step, so the "calibrated, 2.017 s" claim is one tick off.
- **Required correction:** either move the sleep decision so that it takes effect on the step after the timer exceeds the threshold, as Bullet does, and amend FR-037; or document the one-tick lead.
- **Test:** an assertion on the freeze tick of `rest_10`. No test pins it today.

### F5 (P2): the new `rest_*` golden bounds were measured at a recording lag the captures do not have
- **Where:** `golden_captures.rs:181-208` and `rb_verify_cli/src/scenario.rs:593-601`.
- **Trace:** *executed*.
  - `start_speed` reads only `scenario.car.velocity`. These are ball-only probes with a parked car, so the lag search (0 to 3 ticks) runs freely against the ball's error over the whole run.
  - It picks lag 3 for `rest_10`, `rest_20` and `rest_80`, and lag 1 for `rest_40`, although the game ball moves on frame 1, so the true lag is 0.
  - That lag absorbs the documented rolling-decay error. At the true alignment, `rest_80` measures a ball mean of **3.93 uu, which is over its bound of 3.6**. `rest_20` measures 1.10 / 1.59 against the 1.02 / 1.44 used to set its bound.
- **Required correction:** treat a moving ball at the start as a fast start (no free lag), or pin the lag to 0 for ball-only probes. Then re-measure the bounds as measured value plus 25% (ADR-0066).

## Checked and holding
- **The pre-tick cap:** `fast_6500` tick 1 gives 5996.581 in the port against 5996.58 in the game. The cap really does apply before the move.
- **Bullet defaults:** 0.8 m/s × 50 = 40 uu/s; 1.0 rad/s needs no scale; `gDeactivationTime` is 2.0. The units are correct.
- **The new unit test** `a_ball_set_over_the_cap_is_capped_before_its_first_step_moves_it` fails at base, where the ball moves about 54 uu.
- **New golden cases at base:** with base physics they fail on `rest_10` (10.22 / 13.58), `rest_20` (16.44 / 22.63), `rest_40` (34.53 / 112.40) and `fast_6500` (mean 10.57). `rest_80` and `fast_4500` pass at base; they are regression-only.
- **Raised bounds:** `wob_0_200` ball and `wob_0_500` car measure 18.40 / 70.18 and 7.19 / 33.39. These match the fragment, and the reason for raising them is stated.
- **Small number drift:** the fragment's 1.6 for `rest_20` measures 1.44 (1.59 at lag 0), and its `fast_6500` mean "10.3 → 5.4" measures 10.57 → 5.68. These are close but not reproduced exactly.

## Coverage
| File | Classes checked |
|---|---|
| `body.rs` | units, order within a tick, claims (F1, F2, F4) |
| `world.rs` | order within a tick (both caps, sleep before integrate), determinism, test red at base, claims (F4) |
| `golden_captures.rs` | alignment and windows (F5), raised bounds and their reasons |
| `fixture_checks.py`, `test_fixture_checks.py` | estimator logic (F3), edge cases in `freeze`: set at rest, never stops |
| `gen_probes.py`, `experiments/probe_ball_rest_{50,60}.json` | probe design (F2) |
| Six new `fixtures/*.jsonl` | timestamps (gaps of 16.7 ms exist in `rest_20` and `rest_80`; the script uses time, not row index), frame-1 motion |
| FR-037 and FR-061 text, `SPEC-REGISTRY`, parity doc, tape-bot `README` (79 fixtures confirmed), change fragment | claims (F2); test count 519 confirmed |

Hostile bytes and framing do not apply: no parser, socket or wire code changed. Ground truth is not damaged: the six fixtures are new files and no capture is overwritten.

## Design questions
- **Cars sleep with the ball's thresholds.** RocketSim forces cars to `ACTIVE_TAG` every tick, and no ADR records this divergence. It existed at base, but this PR changes the car values too, and `wob_0_500`'s car error doubled. This needs an ADR or a car capture.
- **A sleeping ball held up by a car.** FR-037 has no islands. Because gravity adds only about 5.4 uu/s per tick, below the threshold, a ball asleep on a car's roof would hover once the car leaves. This also existed at base, and the fix for F1 should cover it.

## Optional improvements
- The `step()` doc comment at `world.rs:1210-1215` still says the end-of-tick cap runs "before sleep evaluation and transform integration", but it runs after both. This predates the PR, but the PR edited that sentence.
- The fragment should say that `fast_6500`'s max error rose from 52.95 at base to 56.84 at head.

## Checks run
- `cargo fmt --all -- --check`: pass.
- `cargo clippy -p rb_physics_bullet -p rb_verify_cli --all-targets -D warnings`: pass. `rb_env` clippy: pass.
- `cargo test -p rb_physics_bullet`: 519 passed.
- `cargo test -p rb_env`: pass (28 / 3 / 1).
- `cargo test --release -p rb_verify_cli`, golden captures included: pass.
- Tape-bot Python `unittest`: 18 passed.
- `changes.py check`: 348 fragments ok.
- Scratch probes (in a copy outside the tree): a test printing per-case errors and freeze ticks, run at head, at base, and with forced alignment; and the slow-push test, at head and base.
- No golden test needed the private corpus in this run.

**Validation gaps** (only a live game session can settle these):
- Captures that separate the linear and angular thresholds: sliding without spin, and spin in place.
- A car sleep capture.
- A slow car nudging a ball that has rested for 2 s, to confirm F1 against the game.
