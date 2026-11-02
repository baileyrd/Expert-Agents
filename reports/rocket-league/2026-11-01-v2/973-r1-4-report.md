<!-- rocket-league/973-r1 run 4, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 202 s, 1203046 tokens, $1.08 -->

I'm requesting changes, with two P2 findings. Nothing here is a crash or a physics regression: the PR adds three probe scenarios, the generator round that writes them, and a change fragment. No Rust code changed. The problem is that `probe_ball_rest` can't do what it is meant to do, and the fragment says something the repo already contradicts.

- **Repository / PR:** Rusty-Mill/rusty_mill #973
- **Base:** `15296ea5bb670d14acb22df4f856fadec3e8280a`
- **Head:** `1f2ec4af1f652400ea4dda8e5093a121cdb86646` (`FETCH_HEAD` of `pull/973/head` matches)

## Blocking findings

### 1. P2: `probe_ball_rest` cannot show a ball stopping, so it can't measure the sleep thresholds it was built for
- **Where:** `tools/rb_tape_bot/gen_probes.py:165-166`, `experiments/probe_ball_rest.json`, and the fragment line "a 600 uu/s roll held for 10 s so the ball stops".
- **Trigger:** the ball is set to (0, −2000, 93.15) at 600 uu/s with no spin. It gets 60 settle ticks plus 1200 tape ticks, about 10.5 s, and the runner records 180 more frames.
- **What the game shows (from the committed fixture):** in `fixtures/probe_ball_roll.jsonl`, once the ball is rolling it loses only air drag, about 3% a second. Frames 448 to 465 go from 547.4 to 545.7 uu/s in 0.142 s. Sliding friction is 228 uu/s² (`fixture_checks.py ball-roll`).
  - Starting at 600 uu/s with no spin, the ball should slide until about 0.75 s and start rolling at about 428 uu/s (5/7 of 600).
  - It then loses about 3% a second, so it is still at about 320 uu/s when the tape ends.
- **What the port predicts (I ran it):** `rb-verify --scenario experiments/probe_ball_rest.json`, scored against a made-up capture, puts the ball at y = 2072 at tick 1260. It is still moving 2.85 uu per tick, about 340 uu/s.
- **The gap:** the caption's 40 uu/s threshold is about 78 s of rolling away. The port's own 20 uu/s / 0.5 s sleep rule (`PHYSICS-PARITY-VS-ROCKETSIM.md:198,229`) is about 100 s away. The game run therefore can't settle the "ball's sleep thresholds" item left open at `PHYSICS-PARITY…:78`.
- **Fix:** start the ball near the thresholds instead. For example, use rolling starts at about 60 and 30 uu/s with matching spin (speed / 91.25). Add one variant with spin at or above 1.047 rad/s (10 RPM) and one below. Hold each at least 4 s past the 2.5 s window. The alternative is a tape of about 90 s or more.
- **Check that goes red without it:** the port's prediction for the new probe (`rb-verify --scenario …`) must show the ball falling below 20 uu/s within the tape. Today it ends at about 340 uu/s.

### 2. P2: the fragment says the rolling threshold was never measured; the repo already has a game measurement of it
- **Where:** `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:7` ("neither the cap nor the rolling and sleep thresholds have been measured in the real game"). The same claim is in the probe's description at `gen_probes.py:165` and the `name` field of `probe_ball_rest.json`.
- **Evidence:** `PHYSICS-PARITY-VS-ROCKETSIM.md:71` and `:90` record a game-backed result from `probe_ball_roll`: the ball starts rolling at 547.5 uu/s (6 × 91.25), not the 565 uu/s from the video captions. Running `fixture_checks.py ball-roll` reproduces it: frame 448, v = 547.4, v / spin = 91.3.
- **Also:** this probe wouldn't measure a "rolling threshold" anyway. With no starting spin, the change to rolling happens where speed = spin × radius, at about 428 uu/s, not at a fixed speed.
- **Fix:**
  - Say that only the 6000 uu/s cap and the sleep thresholds are unmeasured, and cite the existing 547.5 uu/s result.
  - Rename the probe's purpose (to the sleep thresholds, once finding 1 is fixed, or to "where a spinless slide turns into rolling").

## Coverage (all five changed files)

| File | What I checked |
|---|---|
| `gen_probes.py` | Units: uu/s, 120 Hz ticks, z = 93.15 is RocketSim's `BALL_REST_Z`. Geometry: the paths miss the parked car; the fast balls reach the side wall at x ≈ 4005 within the run; the rest ball stays on the floor and away from the goal. Regenerated in a scratch copy: the three new files come out byte-identical. The only other mismatch is `wallg_45_*` (last digit of a float), already there at base. |
| `probe_ball_fast_4500.json`, `probe_ball_fast_6500.json` | Claims match the evidence (the captures top out at 3,541 uu/s, `PARITY:78,91`). The port caps the 6500 ball at 6000 and it climbs the wall. The runner's start detection (car position, same as the other ball probes) and its recording deadline (scales with the tape, `rb_run_tapes.rs:540-545`) both handle these. |
| `probe_ball_rest.json` | Findings 1 and 2. |
| `changes/2026-10-24-…md` | Finding 2. The "rest" sentence also falls under finding 1. Format check passes. |

Not applicable: solve order, socket framing, unbounded work, stale frames, untrusted bytes. The diff has no solver, wire or parser code. No recordings are overwritten, and the new names don't clash with existing ones.

## Design questions
None.

## Optional improvements
- None of the ball probes, old or new, set the ball's spin. It is "left as the game has it", so the "600 uu/s roll" actually starts as a spinless slide. Setting the spin explicitly would make the start fully defined and the description accurate.
- The 6500 cap probe runs into O009 (2 to 7 ticks are dropped right at the state-set frame). It's worth noting in the scenario that the first frames may be missing.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | Passed |
| `python3 .github/scripts/changes.py check` | Passed (282 fragments) |
| `python3 -I -m unittest discover -s …/rb_tape_bot` | Passed (15 tests) |
| `cargo test --release -p rb_verify_cli` | Passed (50 + 3) |
| Port predictions for the three probes (`rb-verify --scenario`, made-up captures in `$TMPDIR`) | Ran; numbers above |
| `fixture_checks.py ball-roll` | Ran; reproduces the 547.4 uu/s result |
| Regenerating `gen_probes.py` in scratch | Ran; matches as described above |
| Clippy | Not run: no Rust crate is in the diff |

**Validation gaps:**
- The probes themselves only mean something once run on the game machine, which I can't do here. That includes whether the game accepts a 6500 uu/s state-set.
- I didn't run `run_batch.ps1` or the BakkesMod plugin.
- I didn't check whether any `rb_verify_cli` tests skip without the private corpus.

## Verdict
**Changes required**
