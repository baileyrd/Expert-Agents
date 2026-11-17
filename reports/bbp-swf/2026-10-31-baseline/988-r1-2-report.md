<!-- bbp-swf/988-r1 run 2, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 346 s, 2659915 tokens, $1.84 -->

# Review of PR 988 (Rusty-Mill/rusty_mill): permission leases for hosted goals (Stage 3c)

**Verdict: changes required.** There are two blocking findings (one P1, one P2). All the checks I ran pass, and the real-sandbox acceptance case was not run.

- **Repository:** Rusty-Mill/rusty_mill, PR 988
- **Base:** `90ee60b9e7bdaccedd8c22c04e67e47b9d2900ab`
- **Head reviewed:** `54b9024e2b79bceccd09465005630256b531f6a4`. `git fetch origin pull/988/head` returned this exact SHA, and the checkout's `HEAD` matches it. The diff is a single commit: `54b9024e2 rusty_orch: permission leases for hosted goals (Stage 3c)`.

## Blocking findings

### 1. P1: the lease accepts a pool where one member can redirect another member's rules outside the lease
**Where:** `crates/apps/rusty_orch/crates/orch-bbp/src/lease.rs:22-38` and `:45-63`. The claim it breaks is in `docs/adr/0017-permission-leases.md:24-25`.

The overlap check only works within one member. `bounded` drops a member's read rule if it sits under that member's own write root, but never compares one member's rules with another member's write roots. The ADR claims "Nothing is kept beneath a kept write root, so a harness cannot swap a path another rule names." Across members that is false.

**Scenario (confine step executed, kernel step source-traced):**
- The writer member has write `<d>/srv/src`. The judge member has read `<d>/srv/src/vendor`. The lease is `{write: [<d>/srv/src]}`.
- I ran a probe test in a scratch copy of the head. `confine` returns `Ok`, with writer write `[…/srv/src]` and judge read `[…/srv/src/vendor]`.
- During a turn, the writer's harness replaces `srv/src/vendor` with a symlink to, say, `~/.ssh`. Its write root covers that path, so it is allowed to.
- On the judge's next launch, BBP passes the frozen rule path unchanged (`rusty_bbp_host/src/confine.rs:179`).
- Landlock opens each rule path with `O_PATH | O_CLOEXEC` and no `O_NOFOLLOW` (`platform-linux/src/sys/security.rs:162`). The symlink is followed, so the judge's harness gets read access to `~/.ssh`. That is outside both the lease and its own configured sandbox.

**What is new here:** the same pool could do this before the PR. What the PR adds is a check that approves the pool as lease-confined when it is not, plus docs that promise the opposite. A lease should either refuse this pool or remove the nesting. I tried to get the meet itself to create such a nesting; the `outermost` step prevents that, so the problem is limited to overlaps already in the members' configs.

**Correction:** in `confine`, after bounding every assigned member, refuse with a permanent error any kept read or write rule that lies strictly beneath another assigned member's kept write root. Alternatively, have BBP re-resolve each rule path at every launch and re-check it against the frozen lease. Either way, fix ADR line 24.

**Regression test:** a `lease.rs` unit test using the configuration above should expect `confine` to return `Err`. Today it returns `Ok`, so that test goes red at head.

### 2. P2: cancelling a never-opened card fails under a lease the pool cannot satisfy, so the goal cannot be cancelled
**Where:** `orch-bbp/src/lib.rs:837` calls `freeze`, which calls `lease::confine` at `:274`. That error propagates through `rusty_orch/src/run.rs:472`.

**Scenario (source-traced):**
- The hosting config has a `lease` and an assigned member with no sandbox, or `checkout` with the repository outside the lease.
- Every `open` fails as permanent, so no manifest is ever frozen.
- `rusty_orch run --cancel` takes the assignment over and calls `host.cancel` for every implement card. Each call reaches `freeze`, which runs `confine`, and the cancel is refused as permanent.
- `run.rs:472` returns before `retire` (`:490`), so the goal stays active and cannot be cancelled until the operator removes the lease from the config.

This contradicts the ADR, which says cancel "only removes authority". Cancel only freezes so it can open and immediately cancel a never-opened task, and it never starts a moderator. Confining in that path adds nothing and can block the cancel.

**Correction:** have `cancel` skip `confine` when freezing. One way is to freeze with `lease: None` when no manifest exists and record that the task was cancel-only; another is to tolerate a failed `confine` in the cancel path.

**Regression test:** a host unit test should cancel a never-opened task with a leased fence over an unsandboxed pool and expect `Cancelled`.

**Coverage gap in the same area:** the ADR also says "Cancel is never refused for an ended or different lease", and no test checks it. Today `cancel` doesn't call `leased()`, but if someone added that call, nothing would go red. That test belongs in `orch-bbp/src/tests.rs`, next to `a_leased_task_runs_confined_and_refuses_changed_or_ended_leases`.

## Design questions for the owner
- **The test runner is outside the lease.** Agent-written candidate code runs in BBP's test runner (`rusty_bbp_host/src/runner.rs:323-347`). That runner reads the frozen profile's `read_roots` plus the work directory, and the lease never touches either. The Known limitations list system roots, the turn workspace and the object store, but not the runner's profile roots. Should the lease bound the profile roots, or should the limitations say it doesn't?
- **Adding a lease to a goal already in flight fails its tasks permanently.** A task frozen unleased then refuses every leased fence. This is consistent with "widening needs a new task", but the operator gets a permanent error rather than a clear migration message.

## Optional improvements
- `ttl_secs: 0` is accepted. It produces a lease that has already ended, and every command then returns `Unavailable`. It could be refused at parse time in `cli.rs`.

## Checks performed
All of these ran in the working tree (source identical to the head), with a separate target directory:

| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --offline --locked -p {orch-bbp, orch-core, orch-dispatch, orch-store, rusty_orch} --all-targets -- -D warnings` | pass, all five crates |
| `cargo test --offline --locked -p` for the same five crates | pass. orch-bbp lib: 16 passed, 19 ignored; the ignored ones are the end-to-end and confined tests |
| `python3 .github/scripts/changes.py check` | pass (293 fragments ok) |

- **New library code:** no `unwrap`, `expect` or indexing that can panic in `lease.rs`, the `lib.rs` changes, `run.rs`, `cli.rs` or `assignments.rs`.
- **New tests at the base:** they use `Fence.lease` and `Lease`, which don't exist at the base, so they cannot compile there. They do guard the new behaviour.
- **Probe:** finding 1's `confine` behaviour was executed as a scratch unit test in an archive of the head under `$TMPDIR`. A first build there failed because of stale build artefacts in a shared target directory, so the probe was rerun in a fresh one.

## Validation gaps
- **Acceptance script not run.** I did not run `orch_bbp_acceptance.py`, including the new `lease-confined` case, which needs built binaries and enforced Landlock. So that case is unverified, not passing.
- **Kernel step of finding 1 is not executed.** The symlink being followed at sandbox setup comes from reading the source. Running the acceptance harness with the two-member configuration above, on a Landlock-capable kernel, would settle it.
- **Not covered by my test runs:** `rusty_bbp_host`, `rusty_bbp` and `rusty_sandbox` are untouched by this PR, so I didn't test them.
