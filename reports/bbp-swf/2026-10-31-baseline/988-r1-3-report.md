<!-- bbp-swf/988-r1 run 3, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 225 s, 1717656 tokens, $1.39 -->

**PR 988, Rusty-Mill/rusty_mill.** Base `90ee60b9e7bdaccedd8c22c04e67e47b9d2900ab`, reviewed head `54b9024e2b79bceccd09465005630256b531f6a4`. I fetched `pull/988/head` and it matches that head. It is one commit, "rusty_orch: permission leases for hosted goals (Stage 3c)".

**Verdict: changes required.** Two blocking findings: one harness can redirect another harness's leased path outside the lease, and cancel can be refused because of a lease even though the docs say it never is.

## Blocking findings

### 1. P1: one harness can move another's leased path outside the lease with a symlink
**Where:** `crates/apps/rusty_orch/crates/orch-bbp/src/lease.rs:43-63` (`bounded`), called per member from `confine` at `lease.rs:16`.

**What the code does:** each member's paths are resolved through symlinks once, at freeze. The rule "nothing is kept beneath a kept write root" is applied only within one member's own paths. Nothing stops one member's kept path from sitting beneath another member's kept write root.

Each time a harness starts, `rusty_sandbox` resolves the frozen string again: `executor.rs:255-257` calls `create_dir_all` then `canonicalize`, which follows symlinks. So a symlink planted after freeze changes what the sandbox actually grants.

**Failure scenario (executed in a scratch copy):**
- Lease `write: [W]`. Member `writer` has sandbox `write: [W]`; member `judge` has `write: [W/docs]`.
- `confine` freezes judge's write root as `W/docs`, inside the lease.
- The writer's harness, which may write all of `W`, replaces `W/docs` with a symlink to another directory, standing in for `$HOME`.
- At judge's next launch the write root resolves to `…/orch_bbp_259_1/home`, outside the lease. Probe `probe_cross_member_swap_leaves_the_lease` printed exactly that and passed its "outside the lease" assertion.
- Judge's harness then holds Landlock write on a host path that neither the lease nor its own configured sandbox granted. It could write, for example, `~/.bashrc`.

**Context:** the same nesting between members was possible with plain configured sandboxes before this PR. What this PR adds is a guarantee that does not hold: the ADR (`0017-permission-leases.md:22-25`), the fragment and the `lease.rs:43` comment all say the lease is resolved through symlinks and that no harness can swap a path another rule names.

I graded this P1 (a sandbox grants authority that was never given). It sits close to P0, since the second harness ends up with host write access chosen by the first.

**Required correction:** do one of these.
- Check at launch: refuse when a frozen root's resolved path differs from the frozen string, or open roots without following symlinks (`RESOLVE_NO_SYMLINKS` / `O_NOFOLLOW` on each component).
- Or apply the "beneath a kept write root" rule across the whole frozen pool and refuse a pool that nests one member's root under another's write root.

The first also covers swaps by processes outside the pool.

**Regression test:** the probe above, turned into an assertion that the swapped launch is refused, or that `confine` refuses the nested pool. Plus an ignored e2e case for the acceptance script.

### 2. P2: cancel is refused because of a lease, though the docs say it never is
**Where:** `crates/apps/rusty_orch/crates/orch-bbp/src/lib.rs:837`. `cancel` calls `self.freeze(&h, task, board, fence)`, and for a task never frozen that runs `lease::confine`.

**Failure scenario (executed):**
- Cancel a never-opened task with a leased fence while an assigned pool member is unsandboxed.
- It returns `Err(Permanent("pool member judge is unconfined; a lease needs every assigned member sandboxed"))` and issues no BBP command (probe `probe_cancel_of_never_opened_task_is_refused_for_a_lease`).
- `run::cancel` (`rusty_orch/src/run.rs:472`) turns that into `RunError::Lease`, so the goal's assignment is never retired.
- `run --cancel` keeps failing until someone edits the configuration.
- The same happens for any other `confine` refusal, such as a `checkout` outside the lease. A realistic trigger: card 1 was hosted before the lease was added to the config, and card 2 was never opened.

**What the docs say:** ADR `0017-permission-leases.md:36`, the fragment, the `lib.rs` comment and the PR's own text all say "Cancel is never refused for a lease".

**Required correction:** when `cancel` freezes a task that was never opened, freeze it without confinement (it starts no harness), or skip `confine` on the cancel path.

**Regression test:** the probe above; it should pass after the fix. No existing test covers cancel under a lease at all, whether ended, different or unconfinable.

## Questions for the owner
- ADR-0017 marks itself "Accepted for Stage 3c", but I found no recorded owner decision for permission leases in the repo docs or ADRs. Did the owner accept it, or should it be "Proposed"?
- The lease does not cover BBP's runner. Profile read roots, under which agent-written candidate code runs, are frozen unconfined in `profiles.json`. The fragment lists only system roots, the turn workspace and the object store as known additions. Should the runner's profile roots be bounded by the lease, or at least listed as a known limitation?

## Optional improvements
- `Frozen.lease` compares the fence's raw path strings, not resolved ones. Renaming a path in the lease (for example to a symlink spelling of the same directory) is refused as "another lease"; that is conservative but surprising.
- The lease's end is in whole seconds, so a harness can outlive the lease by up to about one second plus the moderator's poll interval.

## Checks performed
| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked -p {orch-core, orch-bbp, orch-store, orch-dispatch, rusty_orch} --all-targets -- -D warnings` | all pass |
| `cargo test --locked -p` for the same five crates | all pass (orch-bbp: 16 passed, 19 ignored e2e; rusty_orch: 1 ignored) |
| `python3 .github/scripts/changes.py check` | 293 fragments ok |
| Two probes in a scratch copy (`$TMPDIR/scratch`) | as described in findings 1 and 2 |

The configured cargo target directory was read-only, so the clippy and test runs used `CARGO_TARGET_DIR=$TMPDIR/target`. The `rusty_bbp`, `rusty_bbp_host` and `rusty_sandbox` crates are not touched by the diff and were not run. New library code has no `unwrap`, `expect` or indexing that can panic.

## Validation gaps
- **Acceptance script not run.** I did not run `.github/scripts/orch_bbp_acceptance.py`, including the new `lease-confined` case, or any ignored e2e test. They need built `bbp` and `rusty_orch` binaries and an enforcing Landlock kernel. Running `lease-confined`, plus a variant of finding 1 under real Landlock, would settle whether the swapped root is actually writable.
- **Base-SHA runs not done.** The new tests use APIs added by this PR (`Lease`, the `Fence.lease` field, `LeaseSpec`), so they cannot compile at the base and cannot pass there.
