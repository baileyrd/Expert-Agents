<!-- rocket-league/973-r1 run 2, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 174 s, 1062502 tokens, $0.94 -->

# Review of PR 973: tape-bot probes for the 6000 uu/s ball cap and a ball at rest

- **Repository:** Rusty-Mill/rusty_mill, PR 973
- **Base:** `15296ea5bb670d14acb22df4f856fadec3e8280a`
- **Head reviewed:** `1f2ec4af1f652400ea4dda8e5093a121cdb86646`. The fetched `pull/973/head` matches it.
- **Diff:** one commit. It adds round 6 to `gen_probes.py`, three scenario files (`probe_ball_fast_4500`, `probe_ball_fast_6500`, `probe_ball_rest`) and the change fragment. No Rust code changes.

## Blocking findings

### P2: `probe_ball_rest` cannot show a ball coming to rest, but the PR says it does

**Where:**
- `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:7` says: "a 600 uu/s roll held for 10 s so the ball stops"
- `gen_probes.py:165` and `experiments/probe_ball_rest.json:2` say: "until it stops (rolling and sleep thresholds)"
- The commit message says the same.

**How it fails, traced from the source and confirmed by running it:**

1. **The game data.** In the recorded `fixtures/probe_ball_roll.jsonl`, the ball starts rolling at frame 448 (v = 547.4 uu/s; `PHYSICS-PARITY-VS-ROCKETSIM.md:90`). While it is still on the floor (frames 448–465, z = 93.15), it slows by only **12.0 uu/s²**. Drag alone at 3 %/s would give 16.7 uu/s² at that speed. So once the ball rolls, the game shows nothing beyond the 0.03 drag (`body.rs:289`).
2. **The 600 uu/s start.** The scenario sets no spin, so the ball first slides. With the measured 228 uu/s² slide friction and 6.228 rad/s² spin growth, it starts rolling after about 0.75 s at about 430 uu/s.
3. **The rest of the 10 s.** Drag then brings it to only about 320 uu/s by the end. Getting below the caption's 40 uu/s threshold (`PHYSICS-PARITY-VS-ROCKETSIM.md:198`) would take about ln(40/430)/ln(0.97) ≈ 78 s. That is far beyond the 1200 ticks the probe runs, let alone the extra 2.5 s the caption says the ball must stay slow before it stops.
4. **The port's own prediction.** I ran the scenario through `rb-verify --scenario … --against <synthetic capture>` with `RB_BALL=1`. The predicted ball is at y = 1554.2 at tick 1080 and y = 1901.5 at tick 1200, so it is still rolling at about **347 uu/s** when the probe ends. The port's 20 uu/s sleep threshold is never approached either.

**Why it matters:** this probe can measure the slide-to-roll transition again, but neither of the sleep thresholds it names. If someone runs it on the game machine as written, they get a recording that cannot settle the open "Sleeping" row (`PHYSICS-PARITY-VS-ROCKETSIM.md:46, 78, 198`). The fragment would also suggest that gap is being closed.

**Required correction:** pick one.
- **Redesign the probe.** Start balls already rolling (spin about x equal to −v/91.25 for motion along +y) at speeds that bracket the caption's threshold, for example 30 and 60 uu/s. Hold each for at least 5 s. Add one case with spin at or above 10 RPM (≥ 1.05 rad/s) to test the "never stops" clause.
- **Or narrow the wording.** Change the fragment, the description and the generator comment so the probe claims only the slide-to-roll transition at about 430 uu/s with no starting spin.

**Check that should go red today:** run `rb-verify --scenario experiments/probe_ball_rest.json --against <synthetic>` with `RB_BALL=1` and require the predicted ball speed in the last 2.5 s to be below 40 uu/s. Today's file fails it (about 347 uu/s). A redesigned file should pass.

## Things I checked that held up

- **Units.** All values are in uu and uu/s. 93.15 is the ball's resting height used by the existing probes. 6000 matches `BALL_MAX_SPEED` (`world.rs:28`, RocketSim `RLConst.h`).
- **Fast-probe paths.** The port caps the 6500 ball at 6000 on the first tick (1491.8 uu covered in 30 ticks, about 5967 uu/s). That ball reaches the +x wall around tick 140, and the 4500 ball around tick 200. Both cross the floor along y = 0, well away from the car at (−3500, −4000), and both show the cap or no cap within the first ticks.
- **Nothing in the toolchain clamps speed.** Neither the tape bot nor `rb_scenario` clamps velocities, so 6500 is sent as written. Whether the game accepts it is a live-game question (see validation gaps).
- **Generator output.** Rerunning the generator in scratch produces the three new files byte-identical to the committed ones. Three `wallg_45_*` files differ only in float formatting, and the same happens at the base, so this PR did not cause it.
- **No captures touched.** The PR adds scenario files only, so no recorded data is overwritten. No socket, replay or wire code changed, and there are no Rust code changes to test.

## Design questions

None.

## Optional improvements

- Since `probe_ball_rest` will slide first anyway, say in its description that it re-measures the slide-to-roll transition from a different starting speed than `probe_ball_roll` (1500 uu/s). That makes it a useful cross-check of the 547.5 uu/s figure (6 rad/s spin cap × 91.25 radius).

## Checks run

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | passes (exit 0) |
| `python3 .github/scripts/changes.py check` | passes ("282 fragment(s) ok") |
| `python3 -I -m unittest discover -s …/rb_tape_bot` | passes (15 tests OK) |
| Generator rerun in scratch, compared with committed files | new files identical; the `wallg` differences also occur at the base |
| `cargo build --release -p rb_verify_cli` (scratch target) | printed a read-only cargo registry error, but `rb-verify` was produced and loaded all three new scenarios |
| `rb-verify` on the three probes (synthetic `--against`, `RB_BALL=1`) | results as quoted above |
| Game-data analysis of `fixtures/probe_ball_roll.jsonl` | 12.0 uu/s² rolling slowdown, frames 448–465 |
| Clippy and `cargo test` | not run: no Rust code changed |

No test goes red at the base or head for this PR, because it only adds scenario files. The coverage gap is the check described under the finding.

## Validation gaps

These need the game machine:
- whether the game accepts a 6500 uu/s ball state-set, or clamps it, rejects it or ignores it;
- whether the game caps ball speed per tick at 6000;
- the actual sleep behaviour, once a probe that can reach it exists;
- running `run_batch.ps1` and the PowerShell steps.

## Verdict

**Changes required.** There is one P2 finding: the rest probe and the change fragment claim more than the probe can show.
