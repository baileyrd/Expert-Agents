<!-- bbp-swf/988-r1 run 1, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 297 s, 1981761 tokens, $1.50 -->

I'm returning **changes required** for PR 988: two blocking findings and one doc defect. All repository checks pass, and every mode of the acceptance script passes with Landlock enforced in this sandbox.

**Repository:** Rusty-Mill/rusty_mill, PR 988
**Base:** `90ee60b9e7bdaccedd8c22c04e67e47b9d2900ab`
**Head:** `54b9024e2b79bceccd09465005630256b531f6a4`. The fetched `pull/988/head` equals this SHA. It is one commit, "permission leases for hosted goals (Stage 3c)".

## Blocking findings

### 1. P1: one pool member can widen another member's sandbox past the lease, using a symlink
`crates/apps/rusty_orch/crates/orch-bbp/src/lease.rs:16-63` (`confine`, `bounded`)

- **Why it fails:** `bounded` drops paths beneath a kept write root only within the same member's sandbox. It never compares one member's read paths against another member's write roots. The freeze resolves symlinks once. When BBP later launches a harness, `rusty_sandbox` resolves each sandbox path again (`crates/libs/rusty_sandbox/src/executor.rs:254-257`, reached through `rusty_bbp_host/src/confine.rs:179`) and grants Landlock access to whatever the path points to at that moment.
- **Scenario (executed in a scratch copy):**
  - Lease `{write:[D/src/a]}`. The writer member has sandbox write `[D/src/a]`; the judge member has sandbox read `[D/src/a/b]`.
  - `confine` keeps the writer's write root as `D/src/a` and the judge's read root as `D/src/a/b`.
  - The writer's harness, within its own rights, replaces `D/src/a/b` with a symlink to `D/outside`.
  - At the judge's next launch, its read root resolves to `D/outside`, which the lease does not reach.
  - The probe printed `judge read root resolves at launch to …/outside`, and its assertion that the path stays inside the lease failed.
- **Effect:** the judge can read any host path the writer chooses, outside the lease. Confined harnesses keep internet sockets, so the data can leave the machine. This contradicts ADR-0017:24-27 ("Nothing is kept beneath a kept write root, so a harness cannot swap a path another rule names … a lease never runs unconfined") and the change fragment.
- **Fix:** apply the "nothing beneath a write root" rule across the whole frozen pool. Drop or refuse any member's read or write path that lies strictly beneath another assigned member's kept write root. That root is where the turn workspace and checkout live, if those can nest. Alternatively, BBP could refuse a sandbox root whose resolved path at launch differs from the one frozen.
- **Regression test:** a `lease.rs` unit test with exactly the configuration above. It should assert that the judge keeps nothing beneath `D/src/a`, or that `confine` refuses the configuration. It goes red at this head.

### 2. P2: cancel is refused under a lease, despite the documentation
`crates/apps/rusty_orch/crates/orch-bbp/src/lib.rs:837`

- **Why it fails:** `cancel` calls `self.freeze(&h, task, board, fence)`. For a task that was never frozen, this runs `lease::confine`. Any confinement error, such as an unsandboxed assigned member or a `checkout` outside the lease, is returned as `Permanent`, so the cancel is refused.
- **Scenario (executed):** the default test configuration (members unsandboxed), a never-opened task, and a live lease on the fence. `cancel` returned `Err(Permanent("pool member judge is unconfined; a lease needs every assigned member sandboxed"))` and made no BBP calls. So `run --cancel` cannot cancel the goal's cards or retire the assignment unless the operator first deletes `lease` from the configuration.
- **Docs it makes false:** `lib.rs:818` ("not refused for an ended or different lease"), ADR-0017:36 and the change fragment ("Cancel is never refused for a lease").
- **Fix:** freeze for cancel without confining. A task that has never run starts no moderator, and cancel only removes authority. For example, pass the fence without its lease, or skip `confine` on the cancel path while still recording `LeasePaths`.
- **Coverage gap:** no test cancels under a leased fence at all, whether ended, different or not yet frozen. Two tests belong in `orch-bbp/src/tests.rs`:
  - an unconfinable pool under a lease, which must still cancel;
  - a task frozen under a lease, cancelled with an ended lease and with a different one, which must reach `Progress::Cancelled` in both cases.

### 3. P2: the docs claim the lease confines the whole goal, but validator workloads are not bounded by it
Change fragment line 10 (Known limitations), ADR-0017:26, and `crates/apps/rusty_orch/RELEASE_NOTES.md` ("confines a hosted goal").

- **Why it fails:** `confine` bounds only the assigned members' `Sandbox`. `Frozen.profiles` is copied verbatim. BBP's runner executes the candidate, which is agent-written code, with the profile set's `read_roots` (`rusty_bbp_host/src/runner.rs:323-346`), and the lease never narrows those (source-traced).
- **Effect:** the known-limitations list names the paths BBP adds (system roots, the turn workspace, the object store) but not the profiles' read roots. An operator reading "a lease never runs unconfined" would not expect test code to reach past the lease.
- **Fix:** either meet the profile read roots with the lease at freeze, or list them as a known limitation in the fragment, ADR and README.

## Design questions (for the owner)
- Reading the lease from the same hosting file that the run uses means an operator "renews" by re-running. An expired lease therefore turns into a fresh one with no separate approval. If the lease is meant to be a grant from someone other than the operator who runs the goal, this needs a decision. This is not a finding.

## Optional improvements
- `open` freezes and publishes the manifest before `leased` refuses an ended lease (`lib.rs:654-655`). It is harmless, but checking `live` before `freeze` would avoid confining a task under a lapsed grant.
- A build from before this PR still reads the v1 `assignments.mmap` and would act on its older generation. The host's `admit` refuses it as superseded, so it fails safe, but a sentence in the ADR about downgrades would help.

## Checks performed
- `cargo fmt --all --check`: pass.
- `cargo clippy --locked -p orch-core -p orch-dispatch -p orch-store -p orch-bbp -p rusty_orch --all-targets -- -D warnings`: pass.
- `cargo test --locked` for `orch-core`, `orch-dispatch`, `orch-store` and `orch-bbp`: pass.
- `cargo test --locked -p rusty_orch`: the first run failed one test, `maintenance::tests::repository_lock_survives_file_presence_and_releases_on_process_end` (`WouldBlock`). The PR's only change there is adding `lease: None` to a fence. It passed when rerun alone, and the full `--no-fail-fast` rerun passed every target, so I treat it as a flake unrelated to this PR.
- `python3 .github/scripts/changes.py check`: pass (293 fragments).
- `python3 .github/scripts/orch_bbp_acceptance.py` with the real `bbp`, `rusty_orch` and `rusty_mill_board` binaries: all 19 modes pass, including the new `lease-confined` (the harness printed "secret: denied"). `harness-confined` also passed, which shows Landlock is enforced in this sandbox rather than skipped.
- Probes in a scratch export of the head (`git archive`, outside the reviewed tree): both findings 1 and 2 reproduced as described.
- No real provider was run and no money was spent.

## Validation gaps
- Finding 1 is executed only up to the path resolution at launch (`confine` output plus `canonicalize`). It was not run end to end through `bbp mod` with two harnesses. An e2e test like `orch_binary_confines_a_pool_member_to_the_goals_lease`, where the writer plants the symlink and the judge then reads through it, would settle it.
- Finding 3 is source-traced only.

**Verdict: changes required**
