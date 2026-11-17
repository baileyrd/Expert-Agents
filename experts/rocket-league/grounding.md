# Grounding: rocket-league

Target: Rusty-Mill/rusty_mill, scope `crates/apps/rocket_league/`. Held out as bench
cases: PRs 1048, 973, 999 and 666 (cases under `bench/cases/rocket-league/`). `mine`
removed each case's head and fix commits; the later round-2 commit of PR 666
(`2e67e251db`) was removed by hand. Nothing from their reviews appears in the prompt.

Much of the physics history was imported from baileyrd/rusty_bullet with
`git filter-repo` (#541), so many fixes below answered audits and live captures rather
than PR reviews; their messages quote the evidence. The classes rest on those messages.

## Defect classes

**Mechanics parity** (the rarest trait)
1. **Bullet constants in the wrong units.** A Bullet value in metres or m/s copied into
   the uu port: `cd5a0bec7c` (restitution threshold 0.2 m/s used as 0.2 uu/s; every
   touchdown bounced).
2. **Mechanic ordered or applied unlike the game/RocketSim.** `dc594e9783` (extra hit
   velocity after the ball moves), `907cfca969` (no wheel pushback off the ball),
   `b944473383`, `8b683cd961` (boost minimum burn), `3bb0da92a1` (jump press after a
   jump), `02f16b8c0e` (engine fade on absolute speed), `b6ce04da0e`, `842da1b982`,
   `de9a0d267a`, `ef0d982a56`, `b65b319b7c`.

**Simulation soundness**
3. **Order-dependent contact solve.** Contacts resolved one surface or channel at a time:
   `f6ee863635`, `07ff405bbf` (and FR-030/050 they cite).
4. **Wrong lever arm, axis or contact geometry.** `f93ea6d58a`, `96e1ca7cf6`,
   `e29d249f1d`, `a37ff24a7a`.

**Evidence that holds**
5. **Alignment and scoring windows.** Comparing by row, not timestamp; scoring past the
   tape: `14e13b39d3`, `291740c186`, `5a99f20f9c`, `51ce2d8b58` (finite, strictly
   increasing timestamps, bounded pairing window, tie rule).
6. **Claims beyond the evidence.** Conclusions or tables that say more than the runs
   show: `b3bad80a30`, `78e3422195`, `17c7e4d2f0` (finding J kept open),
   `c6f9c5cc65` (pad finding restated; strict fixture reader), `4abfb3f029` (docs that
   contradicted the live result), `03909ae3cf` (floor contact taken from the step, not a
   z threshold; tie-up confirmed by a later observation).
7. **Ground truth overwritten or confused.** `9c742edb37` (candidate written over its
   capture; colliding ids), `80d9c55aa5`, `15f2b28e44` (record the input actually set).

**Hostile bytes**
8. **Framing and partial I/O.** `8f917ca8e0` (partial write desyncs framing; data before
   close dropped), `3d48f53114` (linear FrameDecoder), `0a35b6044f` (drain output after
   half-close; lost bundle must not resume), `87723e823d` (timed-out blocking receive is
   indeterminate).
9. **Unbounded work or counters.** `0a35b6044f` (per-pass accept budget), `8f917ca8e0`
   (per-client read caps; per-message event and name caps), `c431fbad2e` (u32 counters
   saturate).
10. **Stale or repeated frames.** `1893f8e3d8` (policy once per physics frame),
    `fa38f3f51e` (presence check before cached input), `01f13fe9e6` (validate state
    atomically), `4a8427d99d`, `0beac03b8a` (validate pickup state).

## Oracles

- The game, as committed captures: golden fixtures (`rb_verify_cli/tests/golden_captures.rs`,
  `human_captures.rs`), tape-bot experiments, `docs/research/FIDELITY-SCOREBOARD.md`.
- RocketSim (MIT, github.com/ZealanL/RocketSim): its divergences are recorded in ADRs
  (0019, 0026, 0027, 0045 ...) and `docs/research/PHYSICS-PARITY-VS-ROCKETSIM.md`,
  `ROCKETSIM-ANALYSIS.md`.
- Bullet3 (zlib, freely portable per ADR-0004; `THIRD_PARTY_NOTICES.md`).
- boxcars and subtr-actor (pinned in `rb_replay_ingest`/`recon-check`) as replay oracles;
  RLBot v5 flatbuffers schema; the BakkesMod bridge DLL notes (ADR-0083,
  `RL-GAME-SOCKET-FORMAT.md`).

## Settled decisions

`rusty_bullet/docs/adr/` (0002 verification first, 0003 Bullet fidelity target, 0004
port, 0030 k-step scoring, 0060 env, 0066 golden captures, 0084 live-test fail closed),
`docs/specifications/` (RB-PHYSICS-001 FRs, RB-SIM-001 determinism, RB-VERIFY-00x),
`rleval/docs/replay-parser-scope.md`, family ADR-0003 (`README.md`).

## Mined commits

158 commits answer a review; 8 held-out commits removed.
Read each one's diff (`git show <sha>`) for the defect class it fixed.

| Commit | Subject | Files |
|---|---|---|
| 001982cd75 | fix(rl_live_test): require invocation-owned bootstrap parent | `crates/apps/rocket_league/rusty_bullet/tools/rl_live_test/README.md`, `crates/apps/rocket_league/rusty_bullet/tools/rl_live_test/rl-lib.ps1`, `crates/apps/rocket_league/rusty_bullet/tools/rl_live_test/rl-safe.ps1` … |
| 51ce2d8b58 | replay-viewer: finite pairing window, ordered timestamps, tie rule | `crates/apps/rocket_league/crates/replay-viewer/src/physics.rs`, `crates/apps/rocket_league/rleval/docs/replay-viewer.md` |
| 80d9c55aa5 | fix(rl): preserve capture exports and validate viewer alignment | `crates/apps/rocket_league/crates/rb_verify_cli/src/lib.rs`, `crates/apps/rocket_league/crates/rb_verify_cli/src/main.rs`, `crates/apps/rocket_league/crates/rb_verify_cli/tests/write_candidate.rs` … |
| 9c742edb37 | rl: never write the candidate over its capture; disjoint ghost pris | `crates/apps/rocket_league/crates/rb_verify_cli/src/lib.rs`, `crates/apps/rocket_league/crates/rb_verify_cli/src/main.rs`, `crates/apps/rocket_league/crates/rb_verify_cli/tests/write_candidate.rs` … |
| b3bad80a30 | docs(rl): bound server-clone conclusions and keep finding J open | `crates/apps/rocket_league/rusty_bullet/docs/research/SERVER-CLONE-BRANCH-FINDINGS.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/verification/RB-VERIFY-002-capture-ingestion.md` |
| cd4e305503 | docs(rl): parser scope: oracles in tests/examples, kind-aware check | `crates/apps/rocket_league/rleval/docs/replay-parser-scope.md` |
| 17c7e4d2f0 | rl: hittickjump clips record throttle; only steer/pitch/yaw/roll are zero | `crates/apps/rocket_league/crates/rb_capture_ingest/fixtures/README.md`, `crates/apps/rocket_league/crates/rb_capture_ingest/fixtures/human/raw/README.md` |
| 78e3422195 | docs(rl): keep finding J open; steer/pitch/yaw/roll, not throttle, are zero | `crates/apps/rocket_league/rusty_bullet/docs/research/SERVER-CLONE-BRANCH-FINDINGS.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/verification/RB-VERIFY-002-capture-ingestion.md` |
| cf7a0ef2ad | docs(rl): map the server-clone branch findings to main | `crates/apps/rocket_league/rusty_bullet/docs/research/SERVER-CLONE-BRANCH-FINDINGS.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/verification/RB-VERIFY-002-capture-ingestion.md` |
| 2c6607c340 | fix(rleval): retain bound listener and diagnose test startup | `crates/apps/rocket_league/crates/rleval-app/src/main.rs`, `crates/apps/rocket_league/crates/rleval-app/src/server.rs`, `crates/apps/rocket_league/crates/rleval-app/tests/panels_api.rs` … |
| 9bd1a8132a | Separate periodic snapshots from the disclosed design in ADR-0001 | `crates/apps/rocket_league/rusty_bullet/docs/adr/0001-server-authoritative-client-prediction.md` |
| cb21a7c3de | rb_tape_bot: preflight stops the game only after the build succeeds | `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/preflight.ps1`, `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/test-preflight.ps1` |
| d400d72ed6 | Fix verifier discovery failures and exercise tape tool execution | `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/README.md`, `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/ball_events.py`, `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/run_batch.ps1` … |
| c2016e24c1 | docs: the game's first message comes at the splash, not at map load | `crates/apps/rocket_league/crates/rb_rl_game_bridge/README.md`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/bin/rb_rl_bridge.rs`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/link.rs` |
| c431fbad2e | rb_rl_game_bridge: saturate TickTracker's message counters; compose the long-run test | `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/tick.rs` |
| 38ef62b8da | fix(rocket-league): scrub terminal unpaired name escapes | `crates/apps/rocket_league/rusty_bullet/tools/rl_live_test/rl-safe.ps1`, `crates/apps/rocket_league/rusty_bullet/tools/rl_live_test/test-safe.ps1` |
| 4a57fe8552 | fix(rocket-league): address live-test safety and analysis review | `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/bin/rb_rl_demo_count.rs`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/tests/demo_count_cli.rs`, `crates/apps/rocket_league/rusty_bullet/docs/adr/0084-live-test-fail-closed-analysis-and-ownership.md` … |
| 3d48f53114 | rb_rl_game_bridge, rb_rlbot_wire: fix flaky stand-in test, linear FrameDecoder, docs drift | `crates/apps/rocket_league/crates/rb_rl_game_bridge/Cargo.toml`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/README.md`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/tests/real_core.rs` … |
| dd95401f28 | rb_rl_game_bridge: rb_rl_demo_count, a live-test runner and the first live findings | `crates/apps/rocket_league/crates/rb_rl_game_bridge/README.md`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/bin/rb_rl_demo_count.rs`, `crates/apps/rocket_league/rusty_bullet/docs/research/RL-LIVE-TEST-2026-10-12.md` … |
| 0a35b6044f | rb_rlbot_core: bound accept work per pass, drain output after a write half-close; revert game-frame skipping | `crates/apps/rocket_league/crates/rb_rl_game_bridge/README.md`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/rl_link.rs`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/tests/real_link.rs` … |
| 0beac03b8a | fix(rl-game-bridge): validate pickup state and qualify capture evidence | `crates/apps/rocket_league/crates/rb_rl_game_bridge/README.md`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/game_wire.rs`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/tests/robustness.rs` … |
| 9309e215a1 | fix(rl-game-probe): report strict decode and actor lifecycle evidence | `crates/apps/rocket_league/crates/rb_rl_game_bridge/README.md`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/bin/rb_rl_bridge.rs`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/bin/rb_rl_game_probe.rs` … |
| 4a8427d99d | fix(rl-game-bridge): retain slots and gate readiness on distributed mappings | `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/link_backend.rs`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/rl_link.rs`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/tests/real_core.rs` … |
| 01f13fe9e6 | fix(rlbot-core): preserve simulation identity and validate state atomically | `crates/apps/rocket_league/crates/rb_rlbot_core/README.md`, `crates/apps/rocket_league/crates/rb_rlbot_core/src/backend.rs`, `crates/apps/rocket_league/crates/rb_rlbot_core/src/lib.rs` … |
| 8f917ca8e0 | rb_rlbot_core, rb_rl_game_bridge: fixes from reviewing the whole PR | `crates/apps/rocket_league/crates/rb_rl_game_bridge/README.md`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/bin/rb_rl_bridge.rs`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/bin/rb_rl_game_probe.rs` … |
| 4abfb3f029 | rb_rl_game_bridge: verified against a live Rocket League; fixes it found | `crates/apps/rocket_league/crates/rb_rl_game_bridge/Cargo.toml`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/README.md`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/bin/rb_rl_bridge.rs` … |
| 989beeded8 | rb_rl_game_bridge: SpawnBot, so the bridge creates the bots' cars | `crates/apps/rocket_league/crates/rb_rl_game_bridge/README.md`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/bin/rb_rl_bridge.rs`, `crates/apps/rocket_league/crates/rb_rl_game_bridge/src/game_wire.rs` … |
| c6f9c5cc65 | fixture_checks: strict reader, tested activations and estimators; restate the pad finding | `crates/apps/rocket_league/rusty_bullet/docs/research/PHYSICS-PARITY-VS-ROCKETSIM.md`, `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/README.md`, `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/fixture_checks.py` … |
| 7317d5696d | rb_rlbot_client: a send restores blocking mode first; honest sleep caveat | `crates/apps/rocket_league/crates/rb_rlbot_client/src/connection.rs`, `crates/apps/rocket_league/crates/rb_rlbot_client/tests/connection.rs` |
| 87723e823d | rb_rlbot_client: bounded reads are nonblocking with naps; no SO_RCVTIMEO | `crates/apps/rocket_league/crates/rb_rlbot_client/src/connection.rs` |
| 62f0e3e1cb | rb_rlbot_client: keep the original io error as the source of a named socket error | `crates/apps/rocket_league/crates/rb_rlbot_client/src/connection.rs` |
| fa38f3f51e | rb_rlbot_bridge: check the ball and car before replaying the cached input | `crates/apps/rocket_league/crates/rb_rlbot_bridge/src/bot.rs`, `crates/apps/rocket_league/crates/rb_rlbot_bridge/tests/bridge.rs` |
| 1893f8e3d8 | rb_rlbot_bridge: run the policy once per physics frame | `crates/apps/rocket_league/crates/rb_rlbot_bridge/README.md`, `crates/apps/rocket_league/crates/rb_rlbot_bridge/src/bot.rs`, `crates/apps/rocket_league/crates/rb_rlbot_bridge/tests/bridge.rs` |
| 2a53f460b4 | rb_tape_bot: hive test skips frame 203 for real; README match-log commands use --length five | `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/README.md`, `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/tests/processes.rs` |
| 03909ae3cf | rb_env match flow: floor-contact regulation end, score/overtime comparison, confirmed tie-up | `crates/apps/rocket_league/crates/rb_env/examples/match_log_check.rs`, `crates/apps/rocket_league/crates/rb_env/src/flow.rs`, `crates/apps/rocket_league/crates/rb_env/src/lib.rs` … |
| d814b4d9a9 | rocket_league: family README, ADR-0008, docs links, release notes | `crates/apps/rocket_league/.gitattributes`, `crates/apps/rocket_league/.gitignore`, `crates/apps/rocket_league/README.md` … |
| eac3dd3374 | docs: tools README on finding rules by regime and the rig artifacts; drop a stray .pyc, ignore bytecode | `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/README.md`, `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/__pycache__/gen_fuzz.cpython-313.pyc` |
| c8a9027109 | tools: box-to-ball penetration in --wheel-trace, held-out wheel drops; off-centre findings in O014 | `crates/apps/rocket_league/crates/rb_physics_bullet/src/lib.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs`, `crates/apps/rocket_league/crates/rb_verify_cli/src/lib.rs` … |
| a2a9a8de6f | tools: wheel-ray instrument (rb-verify --wheel-trace, wheel_facets.py), ADR-0073, first wall findings in O012 | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/wheels.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/lib.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs` … |
| 26d5430438 | docs: game spread result (23 of 24 tapes repeat to 0.0 uu), pad log findings | `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md`, `crates/apps/rocket_league/rusty_bullet/docs/research/RESEARCH-BACKLOG.md`, `crates/apps/rocket_league/rusty_bullet/docs/roadmap/PARITY-PLAN.md` |
| b944473383 | fix(physics): the wheels' throttle ignores the boost minimum burn (FR-148) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/mod.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/tests.rs`, `crates/apps/rocket_league/crates/rb_verify_cli/tests/golden_captures.rs` … |
| b6ce04da0e | fix(physics): a dodge's forward part follows the throttle only for keyboard captures (FR-147, ADR-0070) | `crates/apps/rocket_league/crates/rb_env/src/lib.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/jump.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/mod.rs` … |
| e29d249f1d | fix(physics): a box meets a mesh triangle's edge or vertex by the separating-axis test (FR-146, ADR-0069) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/collision.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/mesh.rs`, `crates/apps/rocket_league/crates/rb_verify_cli/tests/golden_captures.rs` … |
| 8b683cd961 | fix(physics): a short boost press keeps the boost throttle for the minimum burn (FR-145) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/mod.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/tests.rs`, `crates/apps/rocket_league/crates/rb_verify_cli/tests/golden_captures.rs` … |
| 02f16b8c0e | fix(physics): the engine fades with the absolute forward speed (FR-144, O013) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/ground.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/tests.rs`, `crates/apps/rocket_league/crates/rb_verify_cli/tests/golden_captures.rs` … |
| 5a99f20f9c | fix(verify): align a scenario on car and ball; ball columns; batch scorer | `crates/apps/rocket_league/crates/rb_verify_cli/src/main.rs`, `crates/apps/rocket_league/crates/rb_verify_cli/src/scenario.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md` … |
| 3bb0da92a1 | fix(physics): a jump press just after a jump is ignored (FR-139, ADR-0065) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/jump.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/mod.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/tests.rs` … |
| 47f84b219d | fix(tape-bot): keep reading packets while the game launches; parse the first score row | `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/run_batch.ps1`, `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/src/bin/rb_run_tapes.rs` |
| 15f2b28e44 | fix(plugin): record the input SetVehicleInput was called with (v1.3, RB-RESEARCH-O008) | `crates/apps/rocket_league/rusty_bullet/bakkesmod-plugin/rusty_bullet_capture/RustyBulletCapturePlugin.cpp`, `crates/apps/rocket_league/rusty_bullet/bakkesmod-plugin/rusty_bullet_capture/RustyBulletCapturePlugin.h` |
| 907cfca969 | fix(physics): a wheel ray that hits the ball gets no pushback (FR-138, ADR-0063) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/collision.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/tests.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/wheels.rs` … |
| dc594e9783 | fix(physics): add the extra ball-hit velocity after the ball moves (FR-137, ADR-0062) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md` … |
| 489890450c | docs: re-scored tape-bot results, car-ball contact finding RB-RESEARCH-O010 | `crates/apps/rocket_league/rusty_bullet/docs/research/BOT-CAPTURE-PLAN.md`, `crates/apps/rocket_league/rusty_bullet/docs/research/BOT-RUN-SHEET.md`, `crates/apps/rocket_league/rusty_bullet/docs/research/RESEARCH-BACKLOG.md` … |
| 14e13b39d3 | fix(verify): align a scenario's recording by timestamp, not row (FR-011, spec 0.23.0) | `crates/apps/rocket_league/crates/rb_verify_cli/src/scenario.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/SPEC-REGISTRY.md` … |
| 281e924ab7 | tape-bot: fix bot.toml, a backslash in a TOML basic string is an escape | `crates/apps/rocket_league/rusty_bullet/tools/rb_tape_bot/bot.toml` |
| 291740c186 | fix(verify): do not score input on the frame past the tape end (FR-012 acceptance met) | `crates/apps/rocket_league/crates/rb_verify_cli/src/scenario.rs`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/verification/RB-VERIFY-003-divergence-scoring.md` |
| 5680d6edd0 | fix(physics): bundle wheel drive parameters (clippy too_many_arguments) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/ground.rs` |
| f93ea6d58a | fix(physics): static-contact lever arm runs to the body's own point (FR-135, ADR-0054) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/solver.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 96e1ca7cf6 | fix(physics): tire side force acts along the wheel's own axle (FR-132, ADR-0051) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/ground.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/tests.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs` … |
| db2442642b | Force LF on shell scripts; document rank-vs-ΔV limit and v14 recalibration (#143) | `crates/apps/rocket_league/rleval/docs/rank-assessment-synthesis.md` |
| f5173b8222 | docs: fix status wording | `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md` |
| b65b319b7c | Use RocketSim's car-vs-world material for car bodies (FR-100) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/solver.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md` … |
| ef0d982a56 | Port RocketSim's air control, damping and air throttle (RB-PHYSICS-001-FR-084) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/air.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/mod.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/tests.rs` … |
| de9a0d267a | Model a dodge's flip as RocketSim's timed torque with damping, pitch lock and cancel (RB-PHYSICS-001-FR-083) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/air.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/jump.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/mod.rs` … |
| 842da1b982 | Push a dodge along the ground-plane heading, as RocketSim does (RB-PHYSICS-001-FR-082) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/jump.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/tests.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md` … |
| 2c5a3b3ba5 | Record the zero-steer yaw fix's real-capture result (RB-PHYSICS-001-FR-081) | `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md` |
| cd5a0bec7c | Fix driving-car hop: scale restitution threshold to uu (RB-PHYSICS-001-FR-079) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/solver.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md` … |
| 3ffacc168f | Fix broken markdown tables in ROADMAP and TRACEABILITY | `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md`, `crates/apps/rocket_league/rusty_bullet/docs/roadmap/ROADMAP.md` … |
| 50142dc4f5 | Split drive.rs by mechanic; fix stale AGENTS/status docs | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/air.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive/boost.rs` … |
| f40bf4ee9b | gzip responses with a built-in encoder (assessment #5, remainder) (#131) | `crates/apps/rocket_league/crates/rleval-app/src/gzip.rs`, `crates/apps/rocket_league/crates/rleval-app/src/lib.rs`, `crates/apps/rocket_league/crates/rleval-app/src/server.rs` … |
| 42c604216a | docs: design proposal for episode-level review lists (#118) | `crates/apps/rocket_league/rleval/docs/episodes-design.md` |
| ae3d24db43 | Build/run BakkesMod capture plugin for real, fix frozen-car GetPRIs bug | `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md`, `crates/apps/rocket_league/rusty_bullet/bakkesmod-plugin/rusty_bullet_capture/README.md` … |
| 92a8a70362 | Fix PLUGINTYPE_SOCCAR compile error in rusty_bullet_capture plugin | `crates/apps/rocket_league/rusty_bullet/bakkesmod-plugin/rusty_bullet_capture/RustyBulletCapturePlugin.cpp` |
| 8ff44d6dcc | Update SPEC-REGISTRY/ROADMAP/TRACEABILITY for FR-075 | `crates/apps/rocket_league/rusty_bullet/docs/roadmap/ROADMAP.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/SPEC-REGISTRY.md`, `crates/apps/rocket_league/rusty_bullet/docs/traceability/TRACEABILITY.md` |
| 1b16bbc057 | Fold yaw input into the dodge/wall-jump-dodge direction (FR-073) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 3567f9180c | Update tracking docs for FR-072 (normalized diagonal dodge direction) | `crates/apps/rocket_league/rusty_bullet/docs/roadmap/ROADMAP.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/SPEC-REGISTRY.md`, `crates/apps/rocket_league/rusty_bullet/docs/traceability/TRACEABILITY.md` |
| c18311fd7b | Normalize diagonal dodge direction to match real Rocket League (FR-072) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 49a5c85966 | Update tracking docs for FR-071 (air-control damping audit) | `crates/apps/rocket_league/rusty_bullet/docs/roadmap/ROADMAP.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/SPEC-REGISTRY.md`, `crates/apps/rocket_league/rusty_bullet/docs/traceability/TRACEABILITY.md` |
| eadba1c97e | Document real air-control damping mechanism (FR-071, audit finding) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 45910ff3b7 | Update tracking docs for FR-070 (flip-cancel mechanism audit) | `crates/apps/rocket_league/rusty_bullet/docs/roadmap/ROADMAP.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/SPEC-REGISTRY.md`, `crates/apps/rocket_league/rusty_bullet/docs/traceability/TRACEABILITY.md` |
| b471ec1804 | Document real flip-cancel mechanism (FR-070, audit finding) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| eac78f4b73 | Update tracking docs for FR-069 (dodge-spin torque audit) | `crates/apps/rocket_league/rusty_bullet/docs/roadmap/ROADMAP.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/SPEC-REGISTRY.md`, `crates/apps/rocket_league/rusty_bullet/docs/traceability/TRACEABILITY.md` |
| 673a8cddd7 | Document real dodge-spin torque mechanism (FR-069, audit finding) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 05864d4410 | docs(FR-068): update spec registry, roadmap, traceability | `crates/apps/rocket_league/rusty_bullet/docs/roadmap/ROADMAP.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/SPEC-REGISTRY.md`, `crates/apps/rocket_league/rusty_bullet/docs/traceability/TRACEABILITY.md` |
| 8bbf9f3d87 | fix(FR-068): apply real per-axis air-control torque ratio | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 7647672096 | docs(FR-067): update spec registry, roadmap, traceability | `crates/apps/rocket_league/rusty_bullet/docs/roadmap/ROADMAP.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/SPEC-REGISTRY.md`, `crates/apps/rocket_league/rusty_bullet/docs/traceability/TRACEABILITY.md` |
| ecc02819c9 | docs(FR-067): confirm real Rocket League has no distinct wall-jump mechanism | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 1cf1ed9a36 | docs(FR-066): update spec registry, roadmap, traceability | `crates/apps/rocket_league/rusty_bullet/docs/roadmap/ROADMAP.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/SPEC-REGISTRY.md`, `crates/apps/rocket_league/rusty_bullet/docs/traceability/TRACEABILITY.md` |
| 2fc413a273 | docs(FR-066): confirm real handbrake friction reduction is anisotropic | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 18194e86c2 | Update SPEC-REGISTRY/ROADMAP/TRACEABILITY for FR-065 | `crates/apps/rocket_league/rusty_bullet/docs/roadmap/ROADMAP.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/SPEC-REGISTRY.md`, `crates/apps/rocket_league/rusty_bullet/docs/traceability/TRACEABILITY.md` |
| cc023cdba8 | Add FR-065: real steering is a wheeled-vehicle raycast model, not a torque (audit finding) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 90e00d5aa6 | Update SPEC-REGISTRY/ROADMAP/TRACEABILITY for FR-064 | `crates/apps/rocket_league/rusty_bullet/docs/roadmap/ROADMAP.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/SPEC-REGISTRY.md`, `crates/apps/rocket_league/rusty_bullet/docs/traceability/TRACEABILITY.md` |
| aa23c24b1f | Update SPEC-REGISTRY/ROADMAP/TRACEABILITY for FR-063 | `crates/apps/rocket_league/rusty_bullet/docs/roadmap/ROADMAP.md`, `crates/apps/rocket_league/rusty_bullet/docs/specifications/SPEC-REGISTRY.md`, `crates/apps/rocket_league/rusty_bullet/docs/traceability/TRACEABILITY.md` |
| 2e40709c44 | Add FR-063: real Rocket League uses per-contact-pair-type restitution/friction (audit finding) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/solver.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 72157b9166 | Add FR-060: landing auto-orientation vs. real auto-flip/auto-roll (audit finding) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 9b50d3fcc6 | Sync PROJECT-STATUS/RELEASE_NOTES for FR-059 merge | `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md`, `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md` |
| 035dd7c03c | Sync PROJECT-STATUS/RELEASE_NOTES for FR-058 merge | `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md`, `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md` |
| 33d0db1d36 | Add FR-058: real speed-dependent throttle taper | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 546fadaf6c | Sync PROJECT-STATUS/RELEASE_NOTES for FR-057 merge | `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md`, `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md` |
| 12cf4038bb | Add FR-057: hard cap on car angular speed | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md` … |
| 620299442d | Sync PROJECT-STATUS/RELEASE_NOTES for FR-056 merge | `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md`, `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md` |
| bc45f7db74 | Sync PROJECT-STATUS/RELEASE_NOTES for FR-055 merge | `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md`, `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md` |
| 47832719cc | Add FR-055: confirm GOAL_HALF_WIDTH/GOAL_HEIGHT + fix stale GOAL_DEPTH text | `crates/apps/rocket_league/crates/rb_physics_bullet/src/arena.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| ad7dae4bdf | Sync PROJECT-STATUS/RELEASE_NOTES for FR-054 merge | `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md`, `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md` |
| a34e91f4ae | Sync PROJECT-STATUS/RELEASE_NOTES for FR-053 merge | `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md`, `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md` |
| 07ff405bbf | Fix static-vs-dynamic combined-solve ordering (FR-052) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/solver.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md` … |
| f6ee863635 | Fix order-dependent static multi-surface contact resolution (FR-051) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/solver.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md` … |
| df4f397bd9 | RB-PHYSICS-001-FR-049: velocity-aligned friction direction selection | `crates/apps/rocket_league/crates/rb_physics_bullet/src/solver.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 17f142f2a2 | FR-048: solver.rs constraint-row setup/resolve reference validation | `crates/apps/rocket_league/crates/rb_physics_bullet/src/solver.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 5407326378 | FR-047: collision.rs remaining closed-form shape pairings reference validation | `crates/apps/rocket_league/crates/rb_physics_bullet/src/collision.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| f4d2d4b1df | FR-046: body.rs/mat3.rs reference validation | `crates/apps/rocket_league/crates/rb_physics_bullet/src/body.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/mat3.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md` … |
| a37ff24a7a | Validate box_vs_box against Bullet3's dBoxBox reference (FR-042) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/collision.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| 8c037e664d | Narrow FR-030's sandwiched-solve convergence gap (FR-041) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/solver.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md` … |
| f96f927430 | Implement sleeping for the sequential-impulse solver (FR-037) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/body.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/solver.rs` … |
| e04c73ff43 | Add warm-starting to resolve_dynamic_manifolds (FR-035) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/solver.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md` … |
| 5fff21bca2 | RB-PHYSICS-001-FR-032: investigate curved-fillet narrow phase, resolved | `crates/apps/rocket_league/crates/rb_physics_bullet/src/collision.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/lib.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md` … |
| f81e9606ec | RB-PHYSICS-001-FR-031: constant-calibration audit | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/lib.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md` … |
| f9a049a6be | RB-PHYSICS-001-FR-030: combined multi-body solve | `crates/apps/rocket_league/crates/rb_physics_bullet/src/lib.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/solver.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs` … |
| 8272a9e851 | Add corner-wall floor/ceiling arch radius (RB-PHYSICS-001-FR-025) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/arena.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs`, `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md` … |
| 6a830b3f91 | Add flip-cancel: cancel a dodge's spin early (RB-PHYSICS-001-FR-016) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/lib.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs` … |
| fa5ec3c6d9 | Add variable jump height for the ground jump (RB-PHYSICS-001-FR-015) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/lib.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs` … |
| 58c3f625a3 | Add dodge input (RB-PHYSICS-001-FR-014) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/lib.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs` … |
| 543893a530 | Add jump input (RB-PHYSICS-001-FR-010) | `crates/apps/rocket_league/crates/rb_physics_bullet/src/drive.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/lib.rs`, `crates/apps/rocket_league/crates/rb_physics_bullet/src/world.rs` … |
| 8ee19c187b | Update RB-VERIFY-001, roadmap, traceability, backlog for replay ingestion | `crates/apps/rocket_league/rusty_bullet/CHANGELOG.md`, `crates/apps/rocket_league/rusty_bullet/RELEASE_NOTES.md`, `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md` … |
| 6dfccf153b | Record ADR-0004 and resolve RB-RESEARCH-O001/O002 | `crates/apps/rocket_league/rusty_bullet/docs/PROJECT-STATUS.md`, `crates/apps/rocket_league/rusty_bullet/docs/adr/0004-bullet3-source-port-for-physics-core.md`, `crates/apps/rocket_league/rusty_bullet/docs/research/RESEARCH-BACKLOG.md` … |
| 9dfb45db04 | app: add an "Improve" tab — turn the existing leak/fault signals into actionable tips (#113) | `crates/apps/rocket_league/crates/rleval-app/src/pipeline.rs`, `crates/apps/rocket_league/crates/rleval-app/src/ui.rs`, `crates/apps/rocket_league/rleval/docs/app-guide.md` … |
| d4d16e7ace | case study: does either engine track a real player's real rank climb? (#111) | `crates/apps/rocket_league/crates/replay-pacifist/Cargo.toml`, `crates/apps/rocket_league/crates/replay-pacifist/src/bin/case_study.rs`, `crates/apps/rocket_league/rleval/docs/app-guide.md` |
| 76522b192c | pacifist: multi-match aggregation — history module, CLI, and corpus confirmation (#110) | `crates/apps/rocket_league/crates/replay-pacifist/Cargo.toml`, `crates/apps/rocket_league/crates/replay-pacifist/src/bin/pacifist_history.rs`, `crates/apps/rocket_league/crates/replay-pacifist/src/bin/validate_pacifist.rs` … |
| c7e5046f93 | docs: app guide — run, use, and calibrate the rleval app (#109) | `crates/apps/rocket_league/rleval/README.md`, `crates/apps/rocket_league/rleval/docs/app-guide.md` |
| 3096571867 | pacifist: shadow quality gets a guide-grounded trail-distance fault (pcfg-v2.2) (#107) | `crates/apps/rocket_league/crates/replay-pacifist/src/lib.rs`, `crates/apps/rocket_league/crates/replay-pacifist/src/metrics.rs`, `crates/apps/rocket_league/rleval/docs/pacifist-score-validation.md` |
| 05889a484a | pacifist: v1 (pcfg-v1) — possession events + per-opportunity dimensions (#101) | `crates/apps/rocket_league/crates/replay-pacifist/src/bin/validate_pacifist.rs`, `crates/apps/rocket_league/crates/replay-pacifist/src/bridge.rs`, `crates/apps/rocket_league/crates/replay-pacifist/src/context.rs` … |
| 0905eff4c7 | docs: Pacifist assessment-criteria spec (consolidation 1 of 2) (#97) | `crates/apps/rocket_league/rleval/docs/pacifist-assessment-criteria.md` |
| 2e0ba77ced | rank-assessment: Silver (+expand pagination & low-rank acceptance fixes) (#90) | `crates/apps/rocket_league/crates/replay-analyzer/tests/external_validation.rs`, `crates/apps/rocket_league/rleval/docs/rank-assessment-silver.md` |
| b5f689bda3 | rank-assessment: Bronze (corpus bronze bucket + lobby-completeness gate) (#89) | `crates/apps/rocket_league/crates/replay-scoring/src/config.rs`, `crates/apps/rocket_league/crates/replay-scoring/src/coverage.rs`, `crates/apps/rocket_league/crates/replay-scoring/src/lib.rs` … |
| d2a375acdf | scoring: add rank-relative grading as an additive layer (vs your bracket) (#86) | `crates/apps/rocket_league/crates/replay-scoring/src/bin/calibrate.rs`, `crates/apps/rocket_league/crates/replay-scoring/src/lib.rs`, `crates/apps/rocket_league/crates/replay-scoring/src/relative.rs` … |
| f80c66448f | scoring: ceiling experiment result + fix experimental pollution of weight fit (scfg-v8) (#79) | `crates/apps/rocket_league/crates/replay-scoring/src/calibrate.rs`, `crates/apps/rocket_league/crates/replay-scoring/src/config.rs`, `crates/apps/rocket_league/crates/replay-scoring/src/metrics.rs` … |
| ed01a18015 | bc-clone: self-contained ballchasing-style replay dashboard (full field coverage) (#63) | `crates/apps/rocket_league/crates/bc-clone/src/compare.rs`, `crates/apps/rocket_league/crates/bc-clone/src/html.rs`, `crates/apps/rocket_league/crates/bc-clone/src/lib.rs` … |
| adcc56c3c8 | Reverse-engineer the ballchasing analyzer + formal .replay format spec (#62) | `crates/apps/rocket_league/crates/bc-clone/Cargo.toml`, `crates/apps/rocket_league/crates/bc-clone/README.md`, `crates/apps/rocket_league/crates/bc-clone/src/bin/validate.rs` … |
| cbf194e314 | 3D viewer: add a full-screen button (#60) | `crates/apps/rocket_league/crates/rleval-app/src/ui.rs` |
| 4df10cb7b1 | Ship the gradient-boosted value model in production (#58) | `crates/apps/rocket_league/crates/replay-scoring/src/bin/reconcile.rs`, `crates/apps/rocket_league/crates/replay-skills/src/main.rs`, `crates/apps/rocket_league/crates/replay-value/src/bin/train_corpus.rs` … |
| 2fa83fa5ef | recon-check: fix two false R1 failures (empty-header roster, post-goal frames) (#56) | `crates/apps/rocket_league/crates/recon-check/src/lib.rs`, `crates/apps/rocket_league/rleval/docs/backlog.md` |
| 9555fa63db | Fix kickoff first-touch reaction: measure from car release (GO), not setup (#55) | `crates/apps/rocket_league/crates/replay-skills/src/config.rs`, `crates/apps/rocket_league/crates/replay-skills/src/detect.rs`, `crates/apps/rocket_league/crates/replay-skills/src/lib.rs` … |
| e9884887c4 | Candidate-mode for duration-gated runs + first real-corpus fitted config (#54) | `crates/apps/rocket_league/crates/replay-skills/src/bin/calibrate_skills.rs`, `crates/apps/rocket_league/crates/replay-skills/src/calibrate.rs`, `crates/apps/rocket_league/crates/replay-skills/src/detect.rs` … |
| 22b9335f1b | Extend candidate-mode to flick, power-shot, and redirect floors (#53) | `crates/apps/rocket_league/crates/replay-skills/src/bin/calibrate_skills.rs`, `crates/apps/rocket_league/crates/replay-skills/src/calibrate.rs`, `crates/apps/rocket_league/crates/replay-skills/src/detect.rs` … |
| a84ba6f7fc | parity: boost-pad pickup model (big/small/stolen/overfill) (#41) | `crates/apps/rocket_league/crates/replay-analyzer/src/analyze/bcstats.rs`, `crates/apps/rocket_league/crates/replay-analyzer/src/analyze/boost_pads.rs`, `crates/apps/rocket_league/crates/replay-analyzer/src/analyze/mod.rs` … |
| 3d83890699 | docs: reframe CI section — Actions intentionally disabled, not a blocker (#38) | `crates/apps/rocket_league/rleval/docs/handoff-next.md` |
| 1fac0d31a8 | docs: ballchasing stat-parity & viewer deep-dive audit (#37) | `crates/apps/rocket_league/rleval/docs/backlog.md` |
| a4fb66e820 | docs: record Phase 2 subtr-actor spike result (Option A viable) (#34) | `crates/apps/rocket_league/rleval/docs/parser-cross-validation-harness.md` |
| 33527f3289 | viewer: attack-direction goal tint, A-B loop, speed cap | `crates/apps/rocket_league/crates/replay-viewer/src/render.rs`, `crates/apps/rocket_league/crates/replay-viewer/src/scene.rs` |
| 57bdb8b8ed | viewer: loading overlay + ball trail; orientation verified | `crates/apps/rocket_league/crates/replay-viewer/src/render.rs`, `crates/apps/rocket_league/rleval/docs/backlog.md` |
| 11f207571a | viewer: broadcast (tv) camera preset | `crates/apps/rocket_league/crates/replay-viewer/src/render.rs` |
| 1ea7a47a10 | viewer: readability + navigation pass (camera, shadows, trails, timeline) | `crates/apps/rocket_league/crates/replay-viewer/src/render.rs`, `crates/apps/rocket_league/rleval/docs/backlog.md` |
| 35b42139a1 | Backlog: map-aware field geometry | `crates/apps/rocket_league/rleval/docs/backlog.md` |
| 8447851232 | Reconcile rubric against value model; cross-validate metrics | `crates/apps/rocket_league/crates/replay-scoring/Cargo.toml`, `crates/apps/rocket_league/crates/replay-scoring/src/bin/reconcile.rs`, `crates/apps/rocket_league/crates/replay-scoring/src/lib.rs` … |
| 66e3cc3ab3 | Validate reconstruction vs ballchasing; bind team from network | `crates/apps/rocket_league/crates/replay-analyzer/Cargo.toml`, `crates/apps/rocket_league/crates/replay-analyzer/src/analyze/identity.rs`, `crates/apps/rocket_league/crates/replay-analyzer/src/analyze/mod.rs` … |
| 69b77c5c2c | Calibrate scoring against ranked corpus; add value model + calibration harness | `crates/apps/rocket_league/crates/replay-analyzer/Cargo.toml`, `crates/apps/rocket_league/crates/replay-analyzer/src/analyze/reconstruct.rs`, `crates/apps/rocket_league/crates/replay-scoring/Cargo.toml` … |
| df2911ea08 | Resample to fixed 30 Hz grid + attack-direction normalization (T2) | `crates/apps/rocket_league/crates/replay-analyzer/src/analyze/mod.rs`, `crates/apps/rocket_league/crates/replay-analyzer/src/analyze/normalize.rs`, `crates/apps/rocket_league/crates/replay-analyzer/src/analyze/reconstruct.rs` … |
| a872e9f998 | Modular replay analyzer: canonical model + per-player track coalescing (T0+T1) | `crates/apps/rocket_league/crates/replay-analyzer/Cargo.toml`, `crates/apps/rocket_league/crates/replay-analyzer/build.rs`, `crates/apps/rocket_league/crates/replay-analyzer/src/analyze/identity.rs` … |
