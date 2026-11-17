<!-- rocket-league/1048-r1 run 2, variant baseline, prompt 2c25712ebd471ea9164c00c79d1d37f92128404c, model claude-opus-5-5, 2.1.314 (Claude Code); 494 s, 2671123 tokens, $2.05 -->

# Review: Rusty-Mill/rusty_mill PR #1048, "Calibrate ball sleep to the game and cap a set ball before it moves"

- **Base:** `aa414575c7272aa99f81b510aece522eac07a492`
- **Reviewed head:** `9a29bffdbc778999e4f4d2d00fe069612a53229d` (`FETCH_HEAD` of `pull/1048/head` matches it; one commit)

## Verdict: **changes required**

All four findings are P2: wrong or loose claims and docs, plus a small timing mismatch. Neither mechanic change is wrong. I ran these at head:
- **Constants:** 40 uu/s, 1.0 rad/s and 2.0 s are Bullet's defaults (0.8 m/s × 50, 1.0 rad/s, `gDeactivationTime`).
- **Bracket:** `fixture_checks.py ball-rest` gives (39.98, 68.5] uu/s, 2.017 s on every frozen probe.
- **The cap:** `probe_ball_fast_6500` moves x by 49.97 uu on its first tick at 5996.58 uu/s, so capping before the move is backed by the capture.
- **New tests:** the new unit test goes red at base (54.17 uu against the ≤ 50.0 limit). The new rest_10, rest_20, rest_40 and fast_6500 golden cases also go red at base (for example rest_10 at 10.22 / 13.58 against a 1.0 / 1.4 bound).
- **Golden bounds:** every new or raised bound equals the measured error × 1.25. Across all 124 golden lines, base against head shows no other change beyond about 1 uu.

## Blocking findings

**1. P2: the residual error on rest_40 is blamed on the wrong cause.**
- **Where:** `RB-PHYSICS-001-physics-core-port.md:2212-2214`, `golden_captures.rs:177-178`, `changes/2026-10-25-rl-ball-sleep-calibration.md:11`.
- **The claim:** the docs say the port's ball slows 2.1%/s against the game's 3.0%/s, so it drops under 40 uu/s about 0.25 s late.
- **The evidence (executed):** slower decay cannot do this. A ball set at 40 uu/s that decays at any rate is under 40 after one tick.
  - The port's rest_40 ball speeds up first: 40.00 → 40.23 uu/s, and stays at or above 40 until tick 33, then freezes on tick 273. The game's ball decays from the first tick (39.87 uu/s at 0.1 s).
  - The probe sets spin to `v / 91.25` (`gen_probes.py` `BALL_R`), but the port's contact radius is `BALL_RADIUS = 93.15` (`body.rs:112`). In the port the ball is therefore sliding at the start (surface speed 40.8 uu/s), and friction speeds it up.
  - With spin set to `40 / 93.15`, the same probe goes under 40 on tick 1 and freezes on tick 241.
  - The decay rates themselves are correct (port 2.15%/s, game 3.1%/s between 1 s and 2 s).
- **Fix:** attribute the delay to the spin-up caused by the 91.25 / 93.15 radius mismatch, and record that mismatch as an open item. A check that fails when the explanation is wrong: a unit test that sets rest_40's state and asserts the speed never rises above the set value.

**2. P2: the port freezes a ball one tick earlier than the game.**
- **Where:** `body.rs:473-477` (`sleep_timer >= SLEEP_TIME_THRESHOLD`, with velocity zeroed in the same call), called before the move at `world.rs:1527`.
- **The capture:** in `probe_ball_rest_10` the game moves the ball on frame 241 (y −1981.49 → −1981.42, v 8.591) and first holds it, at v = 0, on frame 242. rest_40 shows the same pattern (−1922.39 → −1922.08 on frame 241, v = 0 on frame 242).
- **The port (executed):** at 120 Hz the 10 and 20 uu/s balls are asleep after step 241, and step 241 does not move them. The port's last moving tick is 240; the game's is 241.
- **Why:** Bullet uses `m_deactivationTime > gDeactivationTime`, marks the body `WANTS_DEACTIVATION` after the move, and zeroes it on the next tick's island pass. That is one tick later than the port.
- **Impact:** small, about 0.07 uu at 10 uu/s and 0.31 uu at 38 uu/s. But this is the timing the PR claims to calibrate.
- **Fix:** use a strict `>` and zero velocity on the tick after the threshold is crossed, so a ball under the threshold from tick 1 still moves on tick 241 and is held from tick 242. Regression test: a 120 Hz unit test asserting exactly that.

**3. P2: several numbers in the fragment and FR do not match what the head produces.**
- **Where:** fragment lines 7, 8 and 11; FR-037 Calibration (`RB-PHYSICS-001-physics-core-port.md:2211-2212`); FR-061 amendment (`RB-PHYSICS-001-physics-core-port.md:4008`).
- **The mismatches** (executed; golden harness and `rb-verify --recorded-inputs` agree):

  | Claim | Stated | Measured |
  |---|---|---|
  | fast_6500 ball mean, base → head | 10.3 → 5.4 uu | 10.57 → 5.68 uu |
  | fast_6500 ball max | "55 uu" | 56.84 uu (52.95 at base, so the max rose) |
  | rest_20 ball max at head | 1.6 uu | 1.44 uu |
  | rest_40 ball max at base | 113.0 uu | 112.40 uu |

- The golden bounds were evidently derived from the measured values (7.1 = 5.68 × 1.25, 1.8 = 1.44 × 1.25), so the text and the bounds disagree with each other.
- **Fix:** restate the measured numbers and say that fast_6500's max went up by 3.9 uu.

**4. P2: doc comments this PR edits still say the end-of-tick cap runs before the move.**
- **Where:** `world.rs:1210-1215` (`step` doc) and `world.rs:279-283` (`clamp_ball_velocity` doc). Both still say the clamp runs "before sleep evaluation and transform integration".
- **The code:** the end-of-tick clamp runs after `integrate_transform_and_refresh_inertia` (`world.rs:1532-1537`).
- **Why it matters:** the amendment's own reasoning ("the end-of-tick cap alone moved it 6496.6 / 120") only holds because the clamp runs after the move. The text the PR extends says the opposite, and so does the older comment at `world.rs:1517-1520`. The false wording predates this PR, but the PR rewrote these sentences and kept it.
- **Fix:** say the end-of-tick cap follows the move and the extra hit velocity, as RocketSim's `_FinishPhysicsTick` does.

## Design questions (for the owner)

- **A sleeping ball can only be woken by its own speed.** FR-037's non-goals settle this per-body design, so it is not a finding. But the PR doubles the range of pushes that get swallowed.
  - **Executed probe:** ball and car asleep and touching on the arena floor; the car then coasts in at 60 uu/s with no input.
    - Base: the ball moves 119 uu.
    - Head: the ball moves 0 uu, stays asleep, and the car stops dead.
    - At throttle 0.3 the ball starts moving on tick 19 at head, against tick 7 at base.
  - In Bullet, a sleeping body in contact with an active one is merged into the same island and woken, whatever the speed. FR-037's line that wake-only-from-impulse is "matching real Bullet's own behavior" is therefore inaccurate.
  - Worth deciding: wake a sleeping body on any contact with an awake car.
- **Cars now share the 40 uu/s / 2.0 s thresholds with no car evidence.** The parity doc previously noted that RocketSim keeps cars active. The PR labels this as unmeasured, which is honest. It is still a behaviour change, for example for a car parked without input on a slope.

## Optional improvements

- Frame 0 of the probe fixtures looks like the intended state rather than the state as applied: the car shows boost 0, then 100 one frame later. It does not change the cap conclusion, but it is worth confirming in the tape bot.
- Add a car-touch-on-sleeping-ball capture to the game run sheet; it would settle the first design question.

## Checks run

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy -p rb_physics_bullet --all-targets -- -D warnings` | pass |
| `cargo clippy -p rb_verify_cli --all-targets -- -D warnings` | pass |
| `cargo test --release -p rb_physics_bullet` | 519 passed |
| `cargo test --release -p rb_verify_cli` (golden captures included) | all pass |
| `cargo test --release -p rb_env` | all pass |
| Tape-bot Python `unittest discover` | 18 passed |
| `fixture_checks.py ball-rest` | reproduces the bracket and the 2.017 s timer |
| `python3 .github/scripts/changes.py check` | 348 fragments ok |
| Golden harness with bounds zeroed, base vs head (new fixtures copied into base) | all measured errors listed; new cases red at base |
| New cap unit test grafted into base | fails (54.17 against ≤ 50.0) |
| Scratch probes: sleeping-ball push, rest decay, spin and radius attribution | as quoted above |

All builds and probes ran in scratch copies under `$TMPDIR`. Dependencies needed a scratch `CARGO_HOME`, because `~/.cargo` is read-only in the sandbox. The reviewed tree was not modified.

## Validation gaps

- `probe_ball_rest_50` and `_60` have no captures yet; the linear threshold stays bracketed to (39.98, 68.5] uu/s.
- The angular threshold has only a lower bound (0.438 rad/s).
- The car sleep thresholds are unmeasured; a car coasting or parked without input needs capturing.
- Whether the game wakes a sleeping ball on a slow car touch needs a live capture.
- No private-corpus tests were involved. The game, BakkesMod and PowerShell were not exercised.
