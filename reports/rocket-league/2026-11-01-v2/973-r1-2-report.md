<!-- rocket-league/973-r1 run 2, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 239 s, 1448977 tokens, $1.15 -->

I'm requesting changes on PR 973. The three new scenarios parse and run, and the two speed-cap probes are sound. But `probe_ball_rest` can't produce what it is for: by the port's own prediction and by the game's committed recording, the ball is still rolling at about 340 uu/s when the tape ends. The change fragment then states the opposite.

**Repository:** Rusty-Mill/rusty_mill, PR 973
**Base:** `15296ea5bb670d14acb22df4f856fadec3e8280a`
**Reviewed head:** `1f2ec4af1f652400ea4dda8e5093a121cdb86646` (I fetched `pull/973/head` and confirmed it matches; one commit, five files, +153 lines)

## Blocking findings

### F1 (P2): `probe_ball_rest` ends with the ball still moving, so it cannot measure the sleep thresholds it is named for
- **Where:** `rusty_bullet/tools/rb_tape_bot/gen_probes.py:165-166` and `experiments/probe_ball_rest.json:30-42` (ball starts at (0, −2000, 93.15) moving at 600 uu/s in +y, tape is 1200 ticks).
- **Trigger:** every run of this scenario.
- **What the port predicts (executed):** I ran the port on the head scenario with a scratch program around `rb_verify_cli::simulate_scenario`. The ball slides to about 418 uu/s by tick 120, then just loses speed slowly: 383 uu/s at tick 600 and 339.9 uu/s at tick 1261, at y = 2075. It never gets near the repo's own sleep rule (`LINEAR_SLEEP_VELOCITY_THRESHOLD` 20 uu/s for 0.5 s, `rb_physics_bullet/src/body.rs:105-109`).
- **What the game shows (executed, against the committed capture `fixtures/probe_ball_roll.jsonl`):** once the ball rolls on the flat floor (frames 448–465), it loses only about 12.0 uu/s². Drag alone (0.97 per second) would account for 16.7 uu/s² at that speed, so there is no sign of extra rolling resistance. That window is short (18 frames), so this is an indication, not a proof.
- **What the probe would need:** the ball starts with no spin, so it slides until it rolls at about 5/7 of 600 ≈ 428 uu/s, around t = 0.75 s. The video-caption rule is "under 40 uu/s for 2.5 s"; the game recording stops after 10.5 s of tape plus 1.5 s of tail. Meeting that rule needs an average slow-down of about 52 uu/s². That is roughly four times what the game recording shows.
- **Why a longer tape doesn't fix it:** with drag only, getting from 428 to 40 uu/s takes about ln(10.7)/0.0305 ≈ 78 s and about 14,000 uu of travel. Along x = 0 heading +y, the ball would reach the goal at y = 5120 long before then.
- **Required correction:** start slow enough that the ball rolls below the thresholds within the tape. For example, about 50 uu/s (rolls at about 36) to test the caption's 40 uu/s and 2.5 s, and about 25 uu/s to test the port's 20 uu/s and 0.5 s. Hold each for at least 5 s, aimed along x so it can't reach a goal. If 600 uu/s is kept, rename and re-describe the probe as a rolling-deceleration probe.
- **Check that goes red without the fix:** run `rb-verify --scenario` (or an equivalent trace) on each rest probe and confirm the predicted ball falls below the sleep threshold well before the tape ends. On the head scenario it ends at 339.9 uu/s.

### F2 (P2): the change fragment and the generator's description say the ball stops; the evidence says it won't
- **Where:**
  - `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:7` says "a 600 uu/s roll held for 10 s so the ball stops".
  - `gen_probes.py:165` and the scenario name in `probe_ball_rest.json:2` say "until it stops (rolling and sleep thresholds)".
- **Why it's wrong (source-traced and executed, as in F1):** neither the port nor the game recording supports a ball stopping in this tape (port: 339.9 uu/s at the end; game: about 12 uu/s² of slow-down). The fragment also calls the start "a roll", but the ball starts with no spin, so it slides first (rolling begins around 428 uu/s).
- **Required correction:** reword the fragment and the descriptions to match whatever F1 settles on, and describe the start as sliding.

## Coverage (all five changed files)
- **`changes/2026-10-24-rl-ball-cap-and-rest-probes.md`:** claims checked against the evidence.
  - "3,541 uu/s" is correct: `fixture_checks.py caps` reports a maximum of 3541.2 in `probe_ball_wall.jsonl`.
  - "Never show a ball coming to rest" is correct: I scanned every fixture. The only one with a low-speed stretch, `wob_0_500`, is a resting ball being pushed up to speed by a car.
  - "Rest" claim: F2.
  - The known-limitations line is accurate.
  - `changes.py check` passes.
- **`experiments/probe_ball_fast_4500.json`:**
  - Units are uu and uu/s at 120 Hz ticks. Resting height 93.15 matches RocketSim `BALL_REST_Z` and the parity doc row.
  - Nothing is in the ball's path: the car is at (−3500, −4000), the ball travels along y = 0, and no goal lies on that line.
  - The ball runs freely during the 60 settle ticks (README:42-44). The port reaches the side wall around tick 240, and the recording runs `total_ticks + 1 + 180` frames (`rb_run_tapes.rs:540`).
  - Fine as a below-cap control.
- **`experiments/probe_ball_fast_6500.json`:**
  - Same checks as the 4500 probe.
  - The port clamps the speed to exactly 6000.00 on tick 1 (executed), consistent with `BALL_MAX_SPEED` 6000.
  - Whether the game accepts a 6500 uu/s state-set is called out in the fragment as unmeasured.
- **`experiments/probe_ball_rest.json`:** F1. The path stays clear: x = 0 and it ends at y ≈ 2075, so no goal and no car.
- **`gen_probes.py`:**
  - Regenerated in scratch: all three round-6 files match the committed ones byte for byte.
  - The script writes only into `experiments/` and overwrites only files with the same names; no committed capture or fixture is touched.
- **Failure classes:**
  - *Order within a tick, solve order, geometry, framing, unbounded work, stale frames:* ruled out. The PR is scenario data plus a generator; it adds no physics, wire or parser code.
  - *Alignment:* fine. The tape length is within `rb_run_tapes`' timeout (1441 frames / 60 + 30 = 54 s).
  - *Ground truth:* the new names don't collide with existing ones.

## Design questions
None.

## Optional improvements
- In a capture, the frame at the state-set may show the requested 6500 uu/s rather than what the game applied (README:236-238 notes missing ticks at the state-set frame). When this capture lands in `fixtures/`, `fixture_checks.py caps` would report "max ball speed 6500" from that seeded frame. It already separates seeded from dynamic plateaus for cars; doing the same for ball speed would keep the cap reading honest.
- Not introduced by this PR: regenerating on this Linux machine changes the last digit of three round-5 files (`wallg_45_{700,1200,1800}.json`, `sin`/`cos` rounding). Rounding the generated velocities would make the generator reproducible.
- The `gen_probes.py` docstring still mentions only rounds 1 and 2.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `git diff --check` over the range | clean |
| `python3 -I -m unittest discover -s …/rb_tape_bot` | 15 tests OK |
| `python3 .github/scripts/changes.py check` | 282 fragments ok |
| `python3 -I fixture_checks.py caps` and `ball-roll` | ran; numbers quoted above |
| `cargo build --release -p rb_verify_cli` (scratch target dir) | ok |
| `rb-verify --scenario` plus the scratch ball-trace program on all three probes | ran; numbers above |
| Generator regenerated in scratch and diffed | round 6 identical |

- **Not run:** `cargo clippy` and `cargo test` for the Rust crates, because the diff changes no Rust.
- **Red-at-base tests:** don't apply, since no behaviour changed. The only checks that matter are the scenario-level ones in F1.
- **Private corpus:** not needed.

## Validation gaps (only the game machine can settle these)
- Whether RLBot or the game accepts a 6500 uu/s ball state-set, and whether the 6000 cap applies on the first physics tick.
- The game's actual rolling deceleration over a long roll; the committed data covers only 0.14 s.
- The real sleep thresholds (caption: 40 uu/s, 2.5 s, 10 RPM), once a probe that can reach them exists.
- `run_batch.ps1` and the BakkesMod plugin weren't run.

## Verdict
**Changes required**
