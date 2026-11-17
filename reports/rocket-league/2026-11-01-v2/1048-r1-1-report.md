<!-- rocket-league/1048-r1 run 1, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 536 s, 3453089 tokens, $2.33 -->

# Review of PR 1048, Rusty-Mill/rusty_mill

- **Base:** `aa414575c7272aa99f81b510aece522eac07a492`
- **Reviewed head:** `9a29bffdbc778999e4f4d2d00fe069612a53229d` (the fetched `pull/1048/head` matches it)
- **Commit:** one, "Calibrate ball sleep to the game and cap a set ball before it moves"

## Verdict: **changes required**

The core of the PR holds up. The new captures show the game freezes a rolling ball on a timer, and the Bullet defaults at 50 uu per metre (40 uu/s, 1.0 rad/s, 2.0 s) fit them. The start-of-tick ball cap is what `probe_ball_fast_6500` shows: the first tick moves 49.97 uu (5996.6 / 120), and the error stays within 0.3 uu until tick 149. I traced that myself. The golden gate goes red at base on rest_10, rest_20, rest_40 and fast_6500, and the new unit test goes red at base too. What blocks the merge is one unmeasured car behaviour change, which also regresses a recorded car, plus several written claims the captures don't support. There are no hostile-input surfaces in this diff.

## Blocking findings

**1. P2: The ball's calibration was also applied to cars, with no evidence, and it makes a recorded car worse. The golden bound was raised to hide it.**
- **Where:** `rb_physics_bullet/src/world.rs:1528-1530` (`car.update_sleep_state(dt)` reads the shared constants at `body.rs:103-107`) and `golden_captures.rs:81` (`car: (9.0, 41.7)`).
- **Trigger:** any car slower than 40 uu/s and spinning under 1.0 rad/s, for 2.0 s.
- **Trace (executed):** in a scratch copy I kept the new ball thresholds and put cars back on 20 / 0.5 / 0.5. Over all 79 cases:
  - wob_0_500 car error goes from 7.19 / 33.39 back to 4.15 / 27.79 uu (base was 4.15 / 28.07).
  - Every ball number is unchanged.
  - demo_4 is the only case the car change helps (2.93 / 6.12 → 2.84 / 5.06).
- **Evidence against the shared rule:** the PR's own fixtures already show the game's car does not follow the ball's rule. In `probe_ball_rest_*` the car stops moving about 0.4 s in, then reports a constant 0.371 uu/s in z for 4.6 s. The ball, by contrast, is zeroed exactly at 2.017 s.
- **Docs:** the fragment and FR-037 do say cars are "unmeasured". But the parity §1 "Sleeping" row is marked ✅ for a mechanism whose car half differs from RocketSim (cars forced ACTIVE every tick) and has no capture behind it.
- **Required:** either keep car sleep on its old values or a separate constant until a car capture calibrates it, or record the decision in an FR or ADR and say plainly that the wob_0_500 car bound rose because of the car thresholds. Regression check: the wob_0_500 car bound back at (4.4, 30.0).

**2. P2: The "(40.0, 68.5] uu/s" bracket for the linear threshold, and "angular only bounded below", don't follow from the captures.**
- **Where:**
  - `fixture_checks.py:216,223`
  - `body.rs:99-101`
  - FR-037 at `RB-PHYSICS-001…md:2208`
  - fragment line 11
  - `gen_probes.py:172`
- **Trace (source-traced and executed):**
  - A ball only freezes after 2.0 s under the threshold. So rest_80 never freezing bounds the threshold by its speed at the end minus 2 s, which is 72.93 uu/s, not by its slowest speed (68.5).
  - The probes set spin to v / 91.25, so linear speed and spin move together. rest_80 still spins at 0.80 rad/s then. If the angular threshold were anywhere in (0.438, 0.80], rest_80 would stay awake whatever the linear threshold is.
  - What the evidence actually supports is "linear ≤ 72.9 uu/s **or** angular ≤ 0.80 rad/s".
  - For the same reason, rest_50 and rest_60 (spin 0.548 and 0.657 rad/s) can't "narrow the linear threshold" alone.
- **Required:** compute the upper bound from the speed at (end − timer), print the joint condition, and fix the four texts. Test: a `Freeze`/`ball_rest` unit test where a never-frozen ball's bound is read 2 s before the end of the capture.

**3. P2: The stated cause of rest_40's remaining error is wrong.**
- **Where:** FR-037 at `md:2212-2214`, `golden_captures.rs:178`, fragment line 11.
- **Claim:** "the port's rolling ball slows 2.1%/s against the game's 3.0%/s, so it drops under 40 uu/s about 0.25 s late."
- **Why it can't be right:** a ball that starts at exactly 40 and decays at any rate is under 40 after one tick.
- **Trace (executed, `simulate_scenario`):** the port's ball goes from 40.000 to **40.231 uu/s on tick 1**. It stays at or above 40 until tick 33 and freezes at tick 273, while the game froze at tick 241 and never exceeded 39.98. rest_10, rest_20 and rest_80 jump the same way (+0.58%). The likely reason is that the port's ball radius is 93.15, so a spin of v / 91.25 means a surface speed of 1.021·v, and friction speeds the ball up.
- **Required:** state the real cause (the first-tick spin-up) and record it as an open divergence.

**4. P2: The quoted error numbers come from tape mode, not the recorded-input gate, which is what bounds the fixtures.**
- **Where:** fragment lines 7-8, FR-037 `md:2211-2212`, FR-061 amendment `md:4008`, and the commit message.
- **Trace (executed):**

| Quantity | Docs say | Recorded-input gate / `--recorded-inputs` |
|---|---|---|
| rest_20 max | 1.6 | 1.44 |
| rest_40 max, base | 113.0 | 112.40 |
| fast_6500 mean | 10.3 → 5.4 | 10.57 → 5.68 |
| fast_6500 max | "55 uu" | 56.84 |

  Without `--recorded-inputs` the CLI gives 1.6 / 5.4 / 55.2, which is where the docs' numbers come from. The README says recorded inputs are what isolate physics (FR-016, ADR-0066), and the bounds in `golden_captures.rs` are those numbers × 1.25.
- **Required:** quote the recorded-input numbers, or label the numbers as tape mode.

**5. P2: Doc comments the PR edited still put the end-of-tick cap before the move. The code does it after.**
- **Where:**
  - `world.rs:1209-1215` (step doc: "after contact resolution, before sleep evaluation and transform integration")
  - `world.rs:271-276` (`clamp_ball_velocity` doc)
  - the inline comment at `world.rs:1520-1523`
- **What the code does:** `integrate_transform_and_refresh_inertia(&mut self.ball)` at `world.rs:1532` runs before `clamp_ball_velocity` at `:1537`.
- **Why it matters here:** the PR added sentences to both doc comments. Read as written, they say the move was already capped, which contradicts the FR-061 amendment's own reason for the change (a 6496.6 / 120 move). The placement text was already wrong before this PR, but the PR's edits to these lines leave it false.
- **Required:** correct the placement in all three places.

**6. P2: PHYSICS-PARITY contradicts itself on the ball cap.**
- **Where:** `PHYSICS-PARITY-VS-ROCKETSIM.md`, line 78 versus line 91.
- **Detail:** the edited line 78 says `probe_ball_fast_*` "settled the ball cap (6000 uu/s, capped before the tick moves the ball)". The row at line 91 still says "the 6000 uu/s ball speed cap is untested (the captures reach 3,541)". It also doesn't record that the new start-of-tick placement differs from RocketSim, which caps only in `_FinishPhysicsTick`.
- **Required:** update the row with the V2 evidence and the placement difference.

## Coverage

| File | What I checked |
|---|---|
| `changes/2026-10-25-rl-ball-sleep-calibration.md` | Claims beyond evidence: findings 1-4. `changes.py check` OK. |
| `rb_physics_bullet/src/body.rs` | Units (Bullet 0.8 m/s × 50 = 40 uu/s; rad/s is not scaled; `gDeactivationTime` 2.0 s, all correct); order within the tick; claims (finding 2). |
| `rb_physics_bullet/src/world.rs` | Order within the tick (the start cap runs before damping and the move; the end cap is still after the hit velocity); solve order (unchanged); the cars' shared sleep (finding 1); docs (finding 5); the new test, red at base (moved 54.17 uu); the settle-loop change. |
| `rb_verify_cli/tests/golden_captures.rs` | New bounds equal the measured error × 1.25 (checked all six plus both wob bounds). The rest and fast cases are red at base, except rest_80 and fast_4500, which are pure regression guards. The raised wob bounds were traced (finding 1). Comments (finding 3). |
| `PHYSICS-PARITY-VS-ROCKETSIM.md` | Finding 6; the car half of the ✅ (finding 1). |
| `SPEC-REGISTRY.md` | Version 0.162.0; the test count of 519 matches. |
| `RB-PHYSICS-001…md` | FR-037 calibration (findings 1-4); FR-061 amendment (findings 4-5); change history. |
| `tools/rb_tape_bot/README.md` | Fixture count 79 matches the files on disk and the cases (`every_fixture_has_a_case` passes). |
| `experiments/probe_ball_rest_{50,60}.json` | Match `gen_probes.py`; spin = v / 91.25. |
| `fixture_checks.py` | `freeze()` edge cases: a ball set at rest, a dropped frame (rest_20, a 0.0167 s gap, still reports 2.017 s), a ball never frozen. Bracket logic (finding 2). `all` runs. |
| `gen_probes.py` | Finding 2 (the rungs couple speed and spin). |
| `test_fixture_checks.py` | Tests `freeze` only, not the bracket. |
| 6 new `fixtures/*.jsonl` | New files only, so no ground truth is overwritten and no ids collide. Timestamps are increasing (0 non-monotonic gaps). Freeze ticks re-derived independently. |

Not applicable to this diff: framing, unbounded work, stale frames and hostile bytes. It touches no parser, socket or bridge.

## Design questions

- The rest probes set spin to v / 91.25 while the port's ball radius is 93.15 (ADR-0034/0036/0045). Every "rolling without sliding" probe therefore starts the port with slip, so the port's first tick isn't the game's. Should the port's contact radius for rolling friction be revisited, or does the radius ADR accept this?

## Optional improvements

- The port freezes rest_10 and rest_20 at tick 241 (2.008 s); the game freezes at 2.017 s, which is 242 ticks. Switching `>=` to Bullet's `>` makes no difference (I tested it). The one-tick gap probably comes from Bullet evaluating sleep at the end of the step, with the island going to sleep the following step. It's under 0.1 uu at these speeds.
- Add a rest-ladder rung with zero spin (sliding) so the linear and angular thresholds can be measured separately.
- Mark the start-of-tick cap on **angular** velocity as unmeasured in the FR-061 amendment. No committed capture state-sets spin above 6 rad/s; `probe_ball_spin_roll` (−8 rad/s) has no fixture.

## Checks run

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass (exit 0) |
| `cargo clippy -p rb_physics_bullet -p rb_env -p rb_verify_cli --all-targets -- -D warnings` | pass |
| `cargo test --release -p rb_physics_bullet -p rb_env -p rb_verify_cli` | pass (rb_physics_bullet 519; golden_captures 3/3, all 81 cases) |
| Tape-bot Python `unittest discover` | 18 tests OK |
| `python3 .github/scripts/changes.py check` | 348 fragments OK |

**Executed probes** (in scratch worktrees under `$TMPDIR`, since removed):
- Per-case golden errors at head, and at base physics with head's fixtures.
- Head with cars on the old sleep thresholds (finding 1).
- `simulate_scenario` speed traces of the rest scenarios (finding 3).
- Strict `>` in the sleep comparison (no change).
- The new cap unit test run at base: fails as intended.
- `rb-verify` with and without `--recorded-inputs` (finding 4).

No test here needed the private corpus.

**Validation gaps:**
- The car sleep thresholds in the game need a car capture: a slow coast under 40 uu/s with no input.
- The angular sleep threshold needs a zero-spin or high-spin slow-ball capture.
- `probe_ball_rest_50` and `_60` haven't been run yet; they need the game.
- The game's angular cap placement on a state-set spin needs `probe_ball_spin_roll` captured.
- No PowerShell, game or plugin runs were possible here.
