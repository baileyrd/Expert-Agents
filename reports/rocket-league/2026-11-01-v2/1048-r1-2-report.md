<!-- rocket-league/1048-r1 run 2, variant baseline, prompt 034d2ab7cdb5d0e7cbaf6671c79de028048aec2b, model claude-opus-5-5, 2.1.316 (Claude Code); 438 s, 2819842 tokens, $2.10 -->

# Review: Rusty-Mill/rusty_mill PR 1048

- **Base:** `aa414575c7272aa99f81b510aece522eac07a492`
- **Reviewed head:** `9a29bffdbc778999e4f4d2d00fe069612a53229d`. `git fetch origin pull/1048/head` returned this SHA, so the review is pinned. It is one commit, "Calibrate ball sleep to the game and cap a set ball before it moves".

The two code changes hold up. The new sleep values (40 uu/s, 1.0 rad/s, 2.0 s) are consistent with the game's captures, and capping the ball at the start of a tick matches the game: it moves the 6500 uu/s ball by 5995.9 uu/s on its first tick. All three blocking findings are about what the PR claims the captures show.

## Blocking findings

### F1 (P2): The "upper bound" on the linear threshold ignores the 2.0 s timer
**Where:** `tools/rb_tape_bot/fixture_checks.py:216,223`. The same number is repeated in `body.rs:99-100`, `RB-PHYSICS-001…md:2208`, the change fragment line 11, and the parity doc line 229.

**Scenario (executed):**
- `ball_rest` takes the upper bound from the slowest speed anywhere in a capture where the ball never freezes. For `probe_ball_rest_80` that is 68.5 uu/s at the end of the capture (t = 5.025 s).
- A freeze needs 2.017 s under the thresholds, so it must start by t = 3.008 s to show up in the capture. At that moment the game ball is at 72.93 uu/s and 0.799 rad/s.
- So any linear threshold up to about 72.9 uu/s would also leave rest_80 rolling to the end of the capture. The stated bracket (40.0, 68.5] wrongly rules out (68.5, 72.9].

**Fix:** compute the bound from the speed at `t_end − timer`, and correct the docs.

**Test:** add a `test_fixture_checks.py` case where a ball is under a candidate threshold for less than the timer before the capture ends. It must not narrow the bound.

### F2 (P2): rest_80 only shows that at least one threshold is low, so it doesn't bound the linear one
**Where:** the same sites as F1, plus `gen_probes.py:171-173` ("50 and 60 narrow the linear threshold") and the parity doc line 229 ("after 2.0 s under 40 uu/s").

**Scenario (source-traced, using executed values):**
- Sleep needs both speed and spin under their thresholds (`body.rs:471-472`; Bullet's `updateDeactivation` works the same way).
- rest_80 not freezing only shows that the linear threshold is at most about 72.9 uu/s, **or** the angular threshold is at most about 0.80 rad/s. A linear threshold of 100 uu/s with an angular one of 0.75 rad/s fits every capture.
- The new rest_50 and rest_60 probes keep spin tied to speed (v / 91.25 = 0.55 and 0.66 rad/s), so they can't separate the two thresholds either.
- The parity doc states "under 40 uu/s" as a fact. What the captures support is a linear threshold above 39.98 uu/s, with only a joint upper bound.

**Fix:**
- Restate the evidence as a joint condition. Label the upper end of the linear threshold as unmeasured.
- Design a probe that varies speed and spin independently, for example a sliding ball with zero spin.

### F3 (P2): The rest_40 error is blamed on the wrong cause
**Where:** `RB-PHYSICS-001…md:2212-2214`, `golden_captures.rs:178`, change fragment line 11.

**Scenario (executed; scratch probe through `compare_scenario_recorded`):**
- The docs say the port's slower rolling decay (2.1%/s against 3.0%/s) keeps the ball above 40 uu/s for about 0.25 s. But a ball set at 40.000 and decaying at 2.1%/s would be under 40 after one tick.
- What actually happens: the port's first tick *raises* the speed of a rolling ball, while the game's lowers it.

  | Capture | Port, tick 0 → tick 1 | Game, first tick |
  |---|---|---|
  | rest_40 | 40.000 → 40.231 | 39.981 → 39.971 |
  | rest_10 | 10.000 → 10.058 | — |
  | rest_80 | 80.000 → 80.461 | — |

- In rest_40 the port first drops below 40 at tick 33 and freezes at tick 273. The game freezes at tick 241.
- The stated cause sends the follow-up to the wrong mechanic, the decay rate, and records the first-tick speed gain nowhere.

**Fix:** document the first-tick +0.58% speed gain as the main residual.

**Check:** a one-step check on rest_40, asserting that the port's tick-1 speed is not above the speed it was set at.

### F4 (P2): Quoted numbers don't match the scoring the new bounds were derived from
**Where:** change fragment lines 7, 8 and 11; `RB-PHYSICS-001…md:2211-2212,4008`.

**Executed:** the golden harness in a scratch copy, with the per-case errors printed.

| Quantity | Quoted | Measured |
|---|---|---|
| rest_20 max error at head | 1.6 uu | 1.44 uu |
| fast_6500 mean error, base → head | 10.3 → 5.4 uu | 10.57 → 5.68 uu |
| fast_6500 max error at head | "55 uu" | 56.84 uu |
| rest_40 max error at base | 113.0 uu | 112.40 uu |

The bounds in `golden_captures.rs` (1.8; 7.1/71.1) are exactly 1.25 × the measured values, so the code and the prose come from different runs.

The fast_6500 maximum also got *worse*, from 52.95 to 56.84 uu. Nothing mentions this.

**Fix:** quote the measured numbers and note the worse maximum.

## Coverage

- **`body.rs`:**
  - Units: Bullet's 0.8 m/s × 50 = 40 uu/s; 1.0 rad/s; `gDeactivationTime` 2.0 s.
  - Order within a tick: sleep is still evaluated before the transform moves.
  - Doc claims: F1, F2.
- **`world.rs`:**
  - Order within a tick: the start-of-tick cap only changes anything when state was set between ticks, because the end-of-tick cap already bounds the hit velocity (`world.rs:1536-1537`).
  - Determinism: unaffected.
  - Other callers: kickoff and `snap_body`.
  - The new test is red at base (54.17 > 50.0 uu).
  - The `settle_steps` change in the wake test is correct.
- **`golden_captures.rs`:**
  - Bounds: 1.25 × measured, with the existing floor of 0.5/1.5.
  - Raised wob bounds: justified. The game's wob captures are 1.525 s long and never freeze the ball, so the old 0.5 s timer froze the port where the game did not.
  - Red at base: rest_10, rest_20, rest_40 and fast_6500 all go red.
  - Comment: F3.
- **Six fixtures and two experiments:**
  - Alignment: timestamps are increasing, and their only gaps are single 1/60 s holes.
  - Ground truth: the fixtures are new files and overwrite nothing.
  - The first-tick 6500 claim is confirmed.
- **`fixture_checks.py`:**
  - `freeze` uses timestamps rather than row numbers, and its edge cases are tested.
  - The bound logic is wrong: F1 and F2.
- **`test_fixture_checks.py`:** red at base (3 errors). It does not cover F1.
- **`gen_probes.py`:** F2.
- **Docs:**
  - PARITY doc, `RB-PHYSICS-001` (FR-037 calibration, FR-061 amendment), `SPEC-REGISTRY` (version bump) and the tape-bot README (fixture count 79 matches the files on disk): F1–F4.
  - The FR-061 amendment correctly records the departure from RocketSim's end-of-tick-only cap.
- **Change fragment:** F1–F4.
- **Not applicable:** hostile-bytes and framing checks. The diff has no parser or socket code.

## Design questions

- **Slow pushes against a sleeping ball.** FR-037's non-goal says a ball wakes only through its velocity (no Bullet islands) and calls this "matching real Bullet". But Bullet's `buildIslands` wakes a sleeping body that touches an active one.
  - Raising the threshold from 20 to 40 widens the range of slow pushes that leave the ball frozen.
  - Executed probe: a sleeping ball and a car at 30 uu/s. At head the ball stays asleep (moves 36 uu, then refreezes). At base it wakes and travels 667 uu.
  - That probe started with the car overlapping the ball, so treat it as an indication rather than a measurement.
- **Cars sleeping.** Cars now sleep at 40 uu/s for 2 s, while RocketSim forces cars active every tick. This is labelled unmeasured; whether it is acceptable is the owner's decision.
- **Prediction scoring never reaches the timer.** `snap_body` wakes the body on every snap, so one-step and k-step scoring with k < 240 never sees the 2 s timer.

## Optional improvements

- The `step` doc, and the earlier sentence in `clamp_ball_velocity`'s doc, still say the end-of-tick cap is "before sleep evaluation and transform integration". In the code it runs after the transform moves (`world.rs:1532-1537`). This wording predates the PR, but the PR edits that sentence.
- In the aligned comparison, the port freezes rest_10 and rest_20 two ticks after the game (tick 241 against 239). That is about 0.17 uu of error and below the bounds.

## Checks

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rb_physics_bullet -p rb_verify_cli -p rb_env --all-targets -- -D warnings` | pass |
| `cargo test --release -p rb_physics_bullet` | 519 passed |
| `cargo test --release -p rb_env` | pass |
| `cargo test --release -p rb_verify_cli` | lib 52, golden 3, human 5, write_candidate 7: all pass |
| Tape-bot Python unit tests | 18 OK |
| `changes.py check` | 348 fragments ok |

- One run of `rb_verify_cli` failed `seed_first_frame_starts_every_self_mode_at_the_first_frame` with a missing temp file. It passed when rerun on its own and in the full rerun. It looks like a temp-file race in existing code, unrelated to this diff.
- The private corpus wasn't present; human captures that need it skip.
- I ran the golden harness, the probes and the new tests against both base and head in scratch copies under `$TMPDIR`. The reviewed tree was not modified.

**Validation gaps:**
- Live-game captures are needed to settle the upper end of the linear threshold and the angular threshold. Use sliding (zero-spin) and spinning-in-place probes, not the rest_50/rest_60 ladder.
- No car capture measures car sleep yet.

## Verdict: **changes required**
