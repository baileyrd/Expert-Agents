<!-- rocket-league/973-r1 run 3, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 159 s, 620245 tokens, $0.71 -->

I'm requesting changes on PR 973. The three probe files are well-formed and come out of the generator byte for byte, but the PR says the rest probe will show a ball stopping. The port says it won't, and the captures we have don't show it either.

**Pinned:** Rusty-Mill/rusty_mill PR 973, base `15296ea5bb670d14acb22df4f856fadec3e8280a`, head `1f2ec4af1f652400ea4dda8e5093a121cdb86646`. The fetched `pull/973/head` matches the head SHA. One commit, 5 files, +153 lines.

## Blocking findings

### 1. P2: the rest probe claims "the ball stops", but neither the port nor the captures support that
- **Where:** `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:7` ("a 600 uu/s roll held for 10 s so the ball stops"), `gen_probes.py:165` and the `name` in `experiments/probe_ball_rest.json:2` ("…until it stops (rolling and sleep thresholds)").
- **Trace (I ran it):** I built a small program outside the repo that runs the scenario through `rb_verify_cli::simulate_scenario` and prints the ball. With friction and the 0.03 drag, the ball drops from 600 to about 420 uu/s by 0.75 s. After that it loses only about 2.2 % per second: 418 uu/s at 1 s, 343.7 uu/s at tick 1200, and still 339.9 uu/s when the recording ends (tick 1261, y = 2075).
- **What would be needed:** the port's sleep threshold is `LINEAR_SLEEP_VELOCITY_THRESHOLD = 20.0` (`rb_physics_bullet/src/body.rs:105`). The ball ends the tape about 17 times faster than that. At this decay rate it would take well over a minute to get under 20 uu/s.
- **What the game evidence shows:** the committed `fixtures/probe_ball_roll.jsonl` hits the wall at about 4.2 s, right after rolling starts at frame 448 (547.4 uu/s). So no capture shows how a rolling ball slows down. The fragment uses "the ball stops" as the reason for the probe, but no evidence backs it.
- **Why it matters:** if the game behaves like the port, this tape will measure roll decay and nothing else. The open item in `PHYSICS-PARITY-VS-ROCKETSIM.md:78` ("a ball rolling to a stop would settle…") would stay open, even though the fragment says this probe covers it.
- **Fix, either one:**
  - Make the probe actually stop. Start slow enough to drop under 20 uu/s within the tape (port-predicted below about 25 uu/s once rolling), or lengthen the tape. Check the port's prediction with a trace before committing.
  - Or reword the fragment and scenario name: this measures rolling decay, and a stop is expected only if the game has rolling resistance the port lacks.

  A golden or scenario check can only follow once the capture exists.

### 2. P2: a 600 uu/s "roll" can't happen under the measured 6 rad/s spin cap
- **Where:** fragment line 7 ("a 600 uu/s roll"), `gen_probes.py:165` and `probe_ball_rest.json:2` ("ball rolling on the floor at 600 uu/s").
- **Trace (from the source plus the committed capture):** the ball is set with no spin, so it starts out sliding. Rolling at 600 uu/s would need 600 / 91.25 = 6.58 rad/s. But the game caps spin at 6.0 rad/s, which the parity doc table (`PHYSICS-PARITY-VS-ROCKETSIM.md:91`) confirms, and `fixture_checks.py ball-roll` on the committed capture shows rolling only starts at 547.4 uu/s. In the port the ball slides down to about 420 uu/s before it rolls.
- **Fix:** describe it as a ball sliding at 600 uu/s that settles into a roll at about 430 uu/s. Or, if a true roll from the start is wanted, start at or below 547 uu/s with matching spin.

## Coverage
| File | What I checked |
|---|---|
| `changes/2026-10-24-…md` | Claims against the evidence (findings 1 and 2); the "3,541 uu/s" figure matches `PHYSICS-PARITY-VS-ROCKETSIM.md:78,91`; the fragment format check passes. |
| `experiments/probe_ball_fast_4500.json` | Units are uu, uu/s and 120 Hz ticks; the ball's z of 93.15 matches the other probes; the ball path along y=0 never meets the car at (−3500, −4000); the port shows the wall hit at about tick 200 and the ride up the wall inside the tape. No defect. |
| `experiments/probe_ball_fast_6500.json` | Same checks. The port caps the ball at 6000 on the first step, then 5901 uu/s at 0.25 s, and it hits the wall at about tick 145. The fragment already says the game might clamp or reject a 6500 state-set; that is a validation gap, not a finding. |
| `experiments/probe_ball_rest.json` | Findings 1 and 2. 1200 ticks at 120 Hz is 10 s, which is right. The ball ends at y ≈ 2075, nowhere near the goal; no car interaction. |
| `gen_probes.py` | Running it in a copy outside the repo gives the three round-6 files byte for byte. The settle and state-set order matches the README (lines 42–44) and the earlier ball probes. Not relevant here: the hostile-bytes, framing, solve-order and determinism checks, since no Rust or wire code changed. |

Not from this PR: running the generator also gives slightly different floats in `wallg_45_{700,1200,1800}.json` (`494.97474683058323` vs `494.9747468305833`). Those files came from commit `3ed59c93f`.

## Design questions
None.

## Optional improvements
- Give the scenario JSON a way to set ball spin, so ball probes can start in a true roll without the slide.
- The two fast probes start with no spin, so they slide across the floor at about 228 uu/s². The 6000 cap only shows in the first few ticks. Say that in the fragment so whoever analyses the capture reads the first-tick speed, not the speed at the wall.

## Checks run
- `cargo fmt --all -- --check`: passes.
- `python3 .github/scripts/changes.py check`: passes (282 fragments ok).
- Tape-bot unit tests (`python3 -I -m unittest discover -s …/rb_tape_bot`): 15 tests, all pass.
- Regenerating with `gen_probes.py` outside the repo: the round-6 files match exactly.
- Built `rb-verify` in release and ran my trace program on all three probes; the numbers are above.
- Not run: `cargo clippy` and `cargo test` for the physics crates, because the diff changes no Rust. There is no red-at-base test to name, because the PR changes no behaviour: it adds scenario files and generator lines. Each probe's real check is a capture that doesn't exist yet.

## Validation gaps (need the game machine)
- Whether the game accepts, clamps or rejects a 6500 uu/s ball state-set.
- The game's 6000 uu/s cap itself.
- Whether a rolling ball in the game slows faster than the port's drag-only decay, and whether it ever stops.

## Verdict
**Changes required**, for the two P2 findings above.
