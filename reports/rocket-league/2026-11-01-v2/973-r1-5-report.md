<!-- rocket-league/973-r1 run 5, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 198 s, 1095162 tokens, $1.00 -->

# Review of PR 973 (Rusty-Mill/rusty_mill): new tape-bot ball probes

**Verdict: changes required.** The `probe_ball_rest` probe can't show what it is meant to measure, and the change fragment says something the repository already contradicts.

- **Base:** `15296ea5bb670d14acb22df4f856fadec3e8280a`
- **Head:** `1f2ec4af1f652400ea4dda8e5093a121cdb86646`. I fetched `pull/973/head` and it matches this SHA. The PR is one commit.

## Blocking findings

### 1. P2: `probe_ball_rest` cannot bring the ball to rest, so it can't measure the sleep threshold it targets
- **Where:** `gen_probes.py:165-167`, `experiments/probe_ball_rest.json:2,41`, and the fragment at `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:3,7`.
- **What the PR claims:** a 600 uu/s roll "held for 10 s so the ball stops", in order to measure the rolling and sleep thresholds. The probe runs 60 + 1200 ticks, about 10.5 s.
- **What the port predicts (executed):** I ran a scratch program that calls `rb_verify_cli::simulate_scenario` on the committed JSON.
  - The ball slides until v/ω = r, at about 418 uu/s (tick 120).
  - After that it rolls and loses only drag.
  - At the last frame (1261) it is still moving at **339.9 uu/s**. The port's sleep threshold is `LINEAR_SLEEP_VELOCITY_THRESHOLD = 20.0` (`rb_physics_bullet/src/body.rs:105`), 17 times lower.
- **What the game shows (executed, on committed captures):** I took rolling frames (z within 0.2 of 93.15, v/ω within 1 uu of 91.25, under the spin cap) from two captures:
  - `fixtures/probe_ball_bounce_car_side.jsonl`: 184 frames, 281.8 → 272.0 uu/s. Median d(ln v)/dt is −0.0219 per second (about −6 uu/s²).
  - `fixtures/wob_100_200.jsonl`: 127 frames, 90.8 → 88.2 uu/s. Median −0.0268 per second.
  - Both are close to the drag already confirmed as ln 0.97 = −0.0305 per second (`PHYSICS-PARITY-VS-ROCKETSIM.md:90`, V2), with no meaningful rolling resistance on top.
  - So in the game the ball also ends near 300 uu/s. Getting from about 418 uu/s down to 20 uu/s at that decay rate takes about 100 s, not 10.
- **Required correction:**
  - Redesign the probe so the ball can reach the 20 uu/s region inside the run. For example, start it already rolling (ω = v/91.25, so it skips sliding) at about 25–40 uu/s, and size the tick count from the measured ~3%/s decay plus the 0.5 s sleep time.
  - Otherwise, relabel the probe as a measurement of rolling deceleration and drop "until it stops", "sleep thresholds" and "so the ball stops" from the scenario name, the fragment and the changelog line.
- **Check that would catch it:** `rb-verify --scenario` (or the scratch program) on the scenario should show the port's ball speed falling below 20 uu/s before the final tick. Today it ends at 339.9.

### 2. P2: the fragment says the rolling threshold is unmeasured, but the repository already records a game measurement
- **Where:** `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:7`. It says "neither the cap nor the rolling and sleep thresholds have been measured in the real game."
- **The existing record:** `docs/research/PHYSICS-PARITY-VS-ROCKETSIM.md:90` already has a V2 game measurement from `probe_ball_roll`:
  - the ball switches from sliding to rolling near frame 448, at v = 547.4 uu/s;
  - v/ω there is 91.3, against a ball radius of 91.25;
  - the old 565 uu/s figure was found to be wrong.
- **What the fragment can actually support:** the cap is unmeasured (`:91`: "the 6000 uu/s ball speed cap is untested (the captures reach 3,541)"), and the sleep threshold is unmeasured. The rolling transition is not unmeasured.
- **Required correction:** say only that the cap and sleep are unmeasured. If the rest probe is meant to re-check the rolling transition, say so and cite `:90`.

## Coverage

| File | What I checked |
|---|---|
| `changes/2026-10-24-rl-ball-cap-and-rest-probes.md` | Claims against the evidence (findings 1 and 2); fragment format (`changes.py check` passes) |
| `experiments/probe_ball_fast_4500.json` | Units (uu/s, uu; ball rest height 93.15 per RB-PHYSICS-001); ticks at 120 Hz (180 ticks = 1.5 s); car out of the ball's path; matches the generator byte for byte. Port run: stays under the cap and hits the +x wall around tick 190, inside the recording window (`total_ticks` + 1 + 180 tail, `rb_run_tapes.rs:540`). Usable as a below-cap control. |
| `experiments/probe_ball_fast_6500.json` | Same checks. Port run: clamped to exactly 6000.0 on tick 1 (`world.rs:28`) and hits the wall around tick 150. The early frames measure the cap. No defect. |
| `experiments/probe_ball_rest.json` | Geometry: at y = 2075 when the run ends, so it stays clear of the goal at y = 5120. Duration and thresholds: finding 1. |
| `gen_probes.py` (round 6) | Regenerated in a scratch copy: the three round-6 files are byte-identical. Unrelated older `wallg_45_*` files differ in the last float digit; that predates this PR. |

The classes for hostile bytes, framing, unbounded work, solve order and stale frames don't apply: the diff has no parser, socket or physics code. Neither does ground-truth damage, since no capture is overwritten and the new names don't collide with existing ones.

**Tests:** no runtime behaviour changes, so there's no red-at-base test to ask for. `rb_scenario`'s `every_shipped_scenario_parses` only reads `scenarios/`, so nothing parses `experiments/` automatically. I parsed all three files through `Scenario::from_json` in my scratch program.

## Validation gaps (need a run on the game machine)
- Whether RLBot core and the game accept a ball state-set at 6500 uu/s, and whether the clamp shows up on the first recorded frame or one frame later. The fragment already flags this.
- Whether the game has any sleep or rest behaviour for a slow rolling ball. That needs a probe that actually gets near 20 uu/s (finding 1).

## Optional improvements
- Extend `every_shipped_scenario_parses`, or add a sibling test, to cover `experiments/`.
- The docstring in `gen_probes.py` still describes only rounds 1–2.
- The description "toward the side wall (6000 cap)" also fits the 4500 control; calling it a below-cap control would be clearer.

## Checks run
- `cargo fmt --all -- --check`: passes.
- `python3 .github/scripts/changes.py check`: passes (282 fragments ok).
- `cargo test -p rb_scenario`: passes (14 tests).
- Tape-bot Python `unittest discover`: passes (15 tests).
- `cargo build --release -p rb_verify_cli` plus a scratch `simulate_scenario` program on all three probes: results quoted above. The repo's own `target/` is read-only, so I built into `$TMPDIR`.
- Scratch regeneration with `gen_probes.py`: round 6 is identical to the committed files.
- Rolling-decay analysis on `fixtures/*.jsonl`: results quoted in finding 1.
- Not run: clippy and the golden captures. No Rust or physics code changed, so `rb_physics_bullet`, `rb_env` and `rb_verify_cli` tests aren't affected. PowerShell, the game and the plugin can't be run here.

## Design questions
None.
