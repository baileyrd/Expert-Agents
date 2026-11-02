<!-- rocket-league/973-r1 run 1, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 193 s, 1082791 tokens, $0.98 -->

I found two blocking defects, both on the ball-at-rest probe and the change fragment that describes it. The two fast-ball probes are sound.

**Repository:** Rusty-Mill/rusty_mill, PR 973
**Base:** `15296ea5bb670d14acb22df4f856fadec3e8280a`
**Reviewed head:** `1f2ec4af1f652400ea4dda8e5093a121cdb86646`. I fetched `pull/973/head` and its SHA matches. The range is one commit.

## Blocking findings

### 1. P2: `probe_ball_rest` cannot bring the ball to rest, so it cannot measure the sleep threshold it was written for
- **Where:** `tools/rb_tape_bot/gen_probes.py:165-166` and `experiments/probe_ball_rest.json:30-41`. The scenario starts the ball at (0, −2000, 93.15) at 600 uu/s along +y, then runs `st(1200)` after 60 settle ticks.
- **Trigger:** every run of the probe.
- **Evidence from the game:** the committed capture `fixtures/probe_ball_roll.jsonl` shows how a rolling ball slows down.
  - Frames 0–3: the ball loses 273 uu/s² of speed and gains 6.23 rad/s² of spin. That fits a solid sphere: friction 0.35 × 650 plus drag, with spin gaining 2.5·μg/r. So a ball set at 600 uu/s with no spin starts rolling at about 5/7 × 600 ≈ 429 uu/s.
  - Frames 449–464: once the ball rolls, it loses 0.3 uu/s every 3 ticks, about 12 uu/s². That equals drag alone shared with the spin: 5/7 × (−ln 0.97) × 547 = 11.9. There is no sign of extra rolling resistance.
  - Under that model the ball needs about 100 s to fall below the 40 uu/s caption threshold, and would cover about 19,000 uu doing it, much farther than the 5,120 uu from midfield to the back wall.
- **Executed:** I wrote a scratch program outside the tree that runs this scenario through `rb_env::Env`. The port's ball is at 417.9 uu/s at tick 120 and 343.6 uu/s at tick 1260, the last tick, at y = 2075. It never gets near rest.
- **Effect:** both the port and the only capture of a rolling ball predict that a game-machine run would record a ball still rolling at about 340 uu/s. The sleep threshold this probe was written for stays unmeasured.
- **Required correction:**
  - Start much slower. For example, about 50–60 uu/s with matching rolling spin (`angular_velocity` is already accepted, see `probe_ball_drop_spin`).
  - Make the tape long enough to fall below 40 uu/s and then hold for 2.5 s with margin: about 15–20 s from 50 uu/s.
  - Before committing, check with `rb-verify --scenario` that the port's ball goes below about 20 uu/s inside the tape and stays clear of the walls.
  - Otherwise, rename the probe and its description to what it can show: rolling onset below the spin cap, plus rolling deceleration.

### 2. P2: The change fragment's claims about the rolling threshold and the rest probe are false
- **Where:** `changes/2026-10-24-rl-ball-cap-and-rest-probes.md:7`. The same claims are in the `changelog:` line on line 3 and in the `probe_ball_rest` description string.
- **Claim 1:** the fragment says "neither the cap nor the rolling and sleep thresholds have been measured in the real game."
  - `docs/research/PHYSICS-PARITY-VS-ROCKETSIM.md:71`, real-game pass 2, records the rolling threshold as measured: "547.5 uu/s … rolling begins near frame 448 at v = 547.4 = 6 × 91.25", from `probe_ball_roll`.
  - Line 78 lists only the 6000 cap and the sleep thresholds as untested.
  - The fragment therefore contradicts the repository's own evidence record.
- **Claim 2:** the fragment says the 600 uu/s roll is "held for 10 s so the ball stops". Finding 1 shows it will not stop.
- **Required correction:** limit the fragment to what is unmeasured, which is the 6000 cap and the sleep thresholds. Describe the rest probe by what it will actually show, or fix the probe as in finding 1.

## Coverage
- **`changes/2026-10-24-rl-ball-cap-and-rest-probes.md`**
  - Format: `changes.py check` passes.
  - Claims beyond evidence: findings 1 and 2.
  - Known limitations: it correctly marks the 6500 state-set as something only a game run can settle.
- **`experiments/probe_ball_fast_4500.json` and `probe_ball_fast_6500.json`**
  - Units are uu and uu/s. The rest height 93.15 matches `BALL_REST_Z`, and the 6000 cap matches FR / `RLConst.h:44`.
  - Ticks: per the tape-bot README (line 44), the state is set once and the ball moves through settle plus steps, 240 ticks in all. The port runs the same way.
  - Geometry: the car at (−3500, −4000) is out of the ball's path.
  - Executed: the port clamps 6500 to 6000.00 on tick 0. That ball reaches the +x wall near tick 150 and climbs it; the 4500 ball reaches it near tick 190. The ball is at the wall only after the cap has been visible for more than 100 ticks, so the wall does not confound the cap reading.
  - Ground truth is untouched: these are new scenario ids, and no capture was overwritten.
- **`experiments/probe_ball_rest.json`**: same checks as the fast probes, plus finding 1. Its path along x = 0 from y −2000 to about 2075 stays clear of the goal within the tape.
- **`gen_probes.py`**
  - Determinism of the generator: regenerated in scratch, the three new files are byte-identical to the committed ones.
  - Neighbours: `wallg_45_*` differ in the last float digit, but that predates this PR and the PR does not touch them.
  - No input-parsing or framing surface is added, so the hostile-bytes, framing, unbounded-work and stale-frame checks do not apply.

## Design questions
None.

## Optional improvements
- The `gen_probes.py` docstring still describes only rounds 1–2.
- Running the generator rewrites the `wallg_45_*` files because of float formatting. Pinning the float output, or not re-emitting those files, would keep a re-run clean.

## Checks and validation gaps
- `python3 .github/scripts/changes.py check`: 282 fragments OK.
- Tape-bot Python unittest discover: 15 tests OK.
- `cargo fmt --all -- --check`: clean.
- I built `rb-verify` (release), ran `--scenario` on all three probes, and ran the scratch `rb_env` ball-trace program described above.
- I did not run clippy or `cargo test`, because no Rust crate changed.
- No test guards these scenarios. That is expected for scenario-only probes awaiting a capture, so I am not raising it as a finding.
- **Validation gaps**, which only a run on the game machine can settle:
  - whether the game, BakkesMod or RLBot accepts or clamps a 6500 uu/s state-set;
  - whether the game's clamp falls at the same point in the tick as RocketSim's `_FinishPhysicsTick`;
  - the actual sleep thresholds, which need the corrected rest probe.

## Verdict
**Changes required.**
