<!-- rocket-league/1048-r1 run 5, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 345 s, 1629535 tokens, $1.52 -->

I found five defects, so the verdict is **changes required**. All five are P2: the core changes (ball sleep at 40 uu/s, 1.0 rad/s, 2.0 s, and the ball speed cap at the start of each tick) are backed by the new captures, but cars were given the ball's new sleep values without any car evidence, and several recorded claims don't match what the code produces.

**Repository:** Rusty-Mill/rusty_mill, PR 1048
**Base:** `aa414575c7272aa99f81b510aece522eac07a492`
**Head reviewed:** `9a29bffdbc778999e4f4d2d00fe069612a53229d` (the fetched `pull/1048/head` matches; one commit)

## Blocking findings

### F1 (P2): cars now sleep on the ball's new thresholds without evidence; one car capture got 73% worse and its bound was raised to absorb it
- **Where:** `rb_physics_bullet/src/body.rs:103-107` (constants shared by ball and cars); `world.rs:1528-1530` (`car.update_sleep_state`); `golden_captures.rs:81` (`wob_0_500` car bound raised from (4.4, 30.0) to (9.0, 41.7)).
- **Trigger:** any car with no input that stays under 40 uu/s and 1 rad/s for 2 s. Before this PR the band was 20 uu/s and 0.5 rad/s for 0.5 s.
- **Trace (executed):** in a scratch copy I kept the new ball thresholds and gave cars the old ones.
  - `wob_0_500` car: 4.15 / 27.79 uu (mean / max), against 7.19 / 33.39 at head.
  - `wob_0_500` ball: 25.93 / 90.72 in both runs.
  - `demo_4` car: 2.93 / 6.12 with old car thresholds, 2.84 / 5.06 at head. This is the only other car case that moves by more than 0.01 uu.
  - So the whole `wob_0_500` car regression comes from the car thresholds, which no capture measures.
- **Why it's a defect:** RocketSim keeps cars `ACTIVE_TAG` every tick (parity table, same row). The PR explains the bound rise as "the old 0.5 s timer froze those non-parity drops early", which hides that the change is to an unmeasured car mechanic. The parity table also marks the whole Sleeping row ✅ "calibrated".
- **Fix:** give cars their own constants at the old values until a car capture exists, and restore the `wob_0_500` car bound to (4.4, 30.0). That bound goes red if cars take the ball's values again. Mark the parity row as ball-only.

### F2 (P2): the stated cause of the rest_40 residual is wrong
- **Where:** FR-037 **Calibration**, the change fragment's known limitations, `golden_captures.rs:177-178`, and the commit message. All say rest_40 freezes late because "the port's rolling ball slows 2.1%/s, the game's 3.0%/s".
- **Trace (executed):** I stepped `standard_arena` with the probe's state set (ball at 40 uu/s, spin -40/91.25).
  - The port's speed jumps to **40.231** on step 1. The game shows 39.981 at frame 1.
  - The port first drops below 40 at step 33 (0.275 s) and freezes at step 273. The game freezes at tick 242.
  - The jump is ×1.0058, which matches (5 + 2·93.15/91.25)/7 times one tick of drag. That is the roll-matching of a spin set for radius 91.25 on the port's 93.15 uu ball.
  - A slower decay cannot keep a ball that starts at 40.0 above 40, so decay rate is not the cause.
- **Fix:** correct the four texts. The roll-matching itself is listed under design questions below.

### F3 (P2): the port freezes a rolling ball one tick before the game
- **Where:** `body.rs:475` (`sleep_timer >= SLEEP_TIME_THRESHOLD`); `world.rs:1527-1532` (sleep check runs before the ball's position update).
- **Game (from capture):** `probe_ball_rest_10.jsonl` frame 241 still moves (y -1981.49 → -1981.42). Frame 242 is unchanged with velocity 0. rest_20 and rest_40 match this tick by timestamp.
- **Port (executed):** rest_10 and rest_20 have velocity 0 at step 241, zeroed before that step moves the ball, so the last move is step 240. That is 2.008 s against the game's 2.017 s.
- **Reference:** Bullet runs `updateDeactivation` after `integrateTransforms` and tests `m_deactivationTime > gDeactivationTime`. The body only stops moving on the next step, which reproduces the game's 242.
- **Fragility:** the port lands on 241 only because the f32 sum of 240 × dt falls just under 2.0. If it didn't, the freeze would be two ticks early.
- **Impact:** about 0.08 to 0.31 uu at these speeds, too small for the 1.4 / 1.8 uu golden bounds to catch.
- **Fix:** follow Bullet's order (strict `>`, freeze on the following step). Add a scenario test that rest_10's ball moves on step 241 and holds from step 242.

### F4 (P2): numbers in the fragment, FR and commit don't match what the head produces
Executed in release with the PR's golden harness. The PR's own bounds were derived from the executed values (7.1 = 5.68 × 1.25, 1.8 = 1.44 × 1.25), so the text also disagrees with the bounds.

| Case | Claimed | Executed |
|---|---|---|
| rest_20 ball max | 1.6 | 1.44 |
| fast_6500 ball mean (before → after) | 10.3 → 5.4 | 10.57 → 5.68 |
| fast_6500 ball max | "55" | 56.84 |
| old rest_40 ball max | 113.0 | 112.40 |

**Fix:** correct the text in the fragment, FR-037 **Calibration** and the FR-061 amendment (the commit message can't change without rewriting history).

### F5 (P2): edited doc comments contradict where the cap runs
- **Where:** `world.rs:1211-1216` (`step`) and `world.rs:272-279` (`clamp_ball_velocity`).
- **Problem:** both still say the end-of-tick cap runs "before sleep evaluation and transform integration", and the second says it is "Called once per step". The code caps after `integrate_transform_and_refresh_inertia` (`world.rs:1532-1537`, since dc594e978), and the cap now runs twice per step. The PR rewrote both paragraphs but left this text in place.
- **Why it matters:** if the cap really ran before the position update, the FR-061 amendment would be unnecessary.
- **Fix:** correct both comments, and FR-061's own placement text (spec around lines 3950-3953).

## Coverage
- **`body.rs`:** units (0.8 m/s × 50 = 40 uu/s; rad/s needs no scale), order within the tick (F3), claims (F1, F2).
- **`world.rs`:** order of the start-of-step cap, determinism (pure scaling), the new test, other callers of the cap and the sleep check (`snap_body` and the kickoff reset call `wake`), docs (F5).
- **`golden_captures.rs`:** bounds checked against executed values; every fixture has a case; raised bounds (F1).
- **FR spec, parity doc, `SPEC-REGISTRY.md`, fragment:** claims (F1, F2, F4, F5); the version bump is consistent.
- **Tape-bot README:** the count of 79 fixtures is correct.
- **`fixture_checks.py` and its tests:** the freeze detector tolerates dropped frames and returns `None` for a ball set at rest; the bracket output matches the captures (39.98, 68.5].
- **`gen_probes.py`, `probe_ball_rest_50.json`, `probe_ball_rest_60.json`:** spin = v/91.25, consistent with the existing rungs.
- **Six fixtures:** frame alignment and freeze ticks inspected. No timestamp issues; the captures are written as new files, so no existing ground truth is overwritten.
- **Not applicable:** hostile bytes and framing (no parser or socket code changed).

## Design questions
- The ball's 93.15 uu radius (ADR-0034) makes any ball state-set with spin = v/91.25 speed up by 0.58% on first contact. That caused F2's residual and will affect rest_50 and rest_60 the same way.
- `snap_body` calls `wake()`, so one-step and k-step predictions never carry a 2 s sleep timer across a snap. This predates the PR, but the longer timer makes it matter more.

## Optional improvements
- `fixture_checks.py ball-rest` prints the row index ("frame 240" for rest_20), which is tick 242 by timestamp. Printing ticks would be clearer.
- `test_fixture_checks.py` has a stray extra blank line before `class Freeze`.

## Checks performed
- `cargo fmt --all -- --check`: pass.
- `cargo clippy -p rb_physics_bullet -p rb_verify_cli --all-targets -- -D warnings`: pass (it also checks rb_env).
- `cargo test --release`: rb_physics_bullet 519 passed; rb_verify_cli all passed, including the golden captures; rb_env all passed.
- Tape-bot `unittest discover`: 18 tests OK.
- `changes.py check`: 348 fragments OK.
- I used a scratch target directory because the default one is read-only.
- **Red-at-base, run on variants of the head code in a scratch copy:**
  - Without the start-of-step cap, `a_ball_set_over_the_cap_is_capped_before_its_first_step_moves_it` fails (54.17 > 50.0) and fast_6500 shows mean 10.57 against its 7.1 bound.
  - With the old thresholds, rest_10 / 20 / 40 give 10.22 / 16.44 / 34.53 mean against bounds of 1.0 / 1.3 / 8.1.
- No private corpus was used.

## Validation gaps
These need the live game:
- The upper bound on the linear threshold (the rest_50 and rest_60 runs).
- The angular threshold (no rung has spin above 0.66 rad/s).
- Car sleep in the game.
- Whether the game caps spin over 6 rad/s at the start of the tick; no capture sets ball spin that high.
- Whether the game caps at the state-set or at the start of the tick; the captures can't tell these apart.

**Verdict: changes required.**
