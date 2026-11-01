# Interview

Asked in the target project: "If you had to summarize this project in 5 words what would
they be? And if we wanted to recruit an expert for this domain, what would we look for?"

Answer (Nano, 2026-10-31), verbatim:

> **Five words:** Rust Rocket League physics, game-verified.
>
> That's how I read it from the threads: a Rust physics sim (rusty_mill) checked against the live game through the bridge, replay parsing, the RLEval Spire gap work, and calibration probes like ball sleep thresholds and boost pads.
>
> **What to look for in an expert:**
> - **Game physics fidelity.** They've matched a sim to a real engine before: rigid bodies, collision and contact response, sleep and rest thresholds, fixed-timestep determinism. Bullet or PhysX experience is a strong signal, since Rocket League runs on Bullet.
> - **The Rocket League modding and research scene.** RLBot, RocketSim, RLGym, or replay tooling like boxcars/carball. Someone from there already knows the car, ball and pad mechanics we're measuring by hand.
> - **Measurement discipline.** They design probe sweeps, compare runs statistically, and treat the live game as ground truth instead of trusting intuition.
> - **Systems Rust.** Performance-minded and comfortable with binary formats for the replay parser.
> - **Useful extras:** RL or ML-environment experience, since sims like this usually exist to train bots.
>
> The rarest find is the second one paired with the first. A RocketSim or RLGym contributor with physics-engine depth would be ideal.
>
> Rusty-Mill/rusty_mill
>
> Rocket League Expert

## Profile

- **Scope**: `crates/apps/rocket_league/` in Rusty-Mill/rusty_mill: the physics port
  (`rb_physics_bullet`, `rb_domain`, `rb_env`), verification (`rb_verify_cli`,
  `rb_scenario`, `rb_capture_ingest`, `rb_replay_ingest`), the game and RLBot bridges
  (`rb_rl_game_bridge`, `rb_rlbot_*`), the replay parser (`rl_replay`) and the probe and
  live-test tools under `rusty_bullet/tools/`. Not the rleval web service.
- **Rarest trait, first axis**: Rocket League mechanics knowledge paired with physics
  depth, so the first oracle after this repo is the game itself as recorded in the
  committed captures, then RocketSim and its divergences recorded in the ADRs.
- **Traits to axes**: the RL scene paired with physics is *Mechanics parity* (car, ball,
  pad and arena behaviour matches the game); game physics fidelity is *Simulation
  soundness* (rigid body, contact, sleep, fixed step, determinism, the RL-environment
  extra); measurement discipline is *Evidence that holds* (a calibration or parity claim
  follows only from what the recordings establish, and a probe controls what it varies);
  systems Rust is *Hostile bytes* (the replay parser, the game socket and the RLBot wire
  stay bounded and ordered on any input). Measurement discipline also shapes the Tests
  step: a fidelity change needs a capture or golden bound, not only a unit test.
