<!-- bbp-swf/988-r1 run 5, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 221 s, 1754518 tokens, $1.33 -->

# Review of Rusty-Mill/rusty_mill PR 988: permission leases for hosted goals (Stage 3c)

- **Base:** `90ee60b9e7bdaccedd8c22c04e67e47b9d2900ab`
- **Head reviewed:** `54b9024e2b79bceccd09465005630256b531f6a4`. `git fetch origin pull/988/head` returned this SHA and it is also the checkout's `HEAD`.
- **Verdict: changes required.** There is one P0 and one P2. With a lease, one pool member can make another member's harness read host files outside the lease. Cancel is also refused under a lease, which the docs say never happens.

## Blocking findings

### 1. P0: one member can widen another member's reach past the lease with a symlink

**Where:** `crates/apps/rusty_orch/crates/orch-bbp/src/lease.rs:16-40` and `:43-63`. The claim it breaks is at `lease.rs:43-44`, ADR-0017 line 24, and the change fragment.

**The claim.** Each member's sandbox is "met with the lease, both resolved through symlinks, keeping nothing beneath a kept write root, so no harness can swap a path another rule names."

**What the code does.** `bounded` drops reads beneath a write root only within one member's own sandbox. `confine` never compares one member's kept read paths with another member's kept write paths.

**Why that matters.** The BBP host re-resolves sandbox paths every time a turn starts:
- `rusty_bbp_host/src/confine.rs:160-197` builds the rules for each turn.
- The rules end up in `platform-linux/src/sys/security.rs:162`, which opens each path with `O_PATH | O_CLOEXEC` and no `O_NOFOLLOW`. So the Landlock rule follows whatever symlink is at that path when the turn starts.

**Failure scenario (executed in a scratch copy, final step source-traced):**
1. Lease: read `[L]`, write `[L/w]`.
2. Member `writer` has sandbox write `[L/w]`. Member `judge` has sandbox read `[L/w/data]`. This is the usual maker/reviewer layout.
3. `confine` accepts the pool. It keeps `writer.write = [L/w]` and `judge.read = [L/w/data]`.
4. During its own turn, `writer` replaces `L/w/data` with a symlink to `outside/`.
5. My probe then shows that `judge`'s kept rule resolves to `outside/`, outside the lease, and reads `outside/secret`.
6. When `judge`'s next turn starts, `O_PATH` follows the symlink. Landlock then grants `judge` read access to whatever host directory `writer` chose, such as `~/.config` or `~/.ssh`. The only exception is the BBP directory, which `refuse_reaching` still blocks.

The same happens if a read path that does not exist at freeze time sits under another member's write root. `resolved` only resolves the ancestors that already exist, and the missing part can be created later as a symlink.

I graded it P0 because secrets can reach an agent. The trigger is any two assigned members where one's kept read path lies strictly beneath the other's kept write path.

**Required correction.** Apply the "nothing beneath a write root" rule across the whole confined pool, not per member:
- drop or refuse any member's kept read path that lies strictly beneath any member's kept write path; and/or
- have BBP open the rule paths with `O_NOFOLLOW`, or check that a path still resolves inside the lease, each time a turn starts.

**Regression test.** A `lease.rs` unit test with two members, one reading `L/w/data` and the other writing `L/w`, asserting that `confine` refuses the pool or drops the read. Optionally also a `lease-confined` e2e variant where the writer plants a symlink and the second member must see "denied".

### 2. P2: cancel is refused under a lease, despite "Cancel is never refused for a lease"

**Where:** `crates/apps/rusty_orch/crates/orch-bbp/src/lib.rs:837` (cancel calls `freeze` with the fence) and `:274` (`lease::confine(...).map_err(permanent)?`). The claim is in ADR-0017 line 36, the change fragment, and the doc comment at `lib.rs:818-819`.

**Failure scenario (executed).** I ran `host.cancel` on a never-opened task, with a leased fence and the default pool, whose members have no sandbox. It returned:

```
Err(Permanent("pool member judge is unconfined; a lease needs every assigned member sandboxed"))
```

The same happens for any `confine` error: a checkout outside the lease, a relative sandbox path, or a non-UTF8 path. So `rusty_orch --cancel` cannot end a goal whose leased `open` was refused. Every retry fails the same way, because no manifest was ever published.

**Required correction.** Either:
- in `cancel`, freeze a never-opened task without applying the lease. Nothing gets started, since the task is opened straight into Cancelled with no principal and no moderator. Or:
- narrow the documented claim to what the code does.

**Regression test.** Cancel under a leased fence with an unconfined member must return `Progress::Cancelled`. No current test covers cancel under a lease.

## Design questions for the owner

- **Validators run outside the lease.** A lease bounds only the harnesses of assigned pool members. The BBP runner runs agent-written candidate code under the workload profiles' read roots, and those can reach outside the lease. The ADR's "a lease never runs unconfined" reads wider than that. Should profile roots be checked against the lease, or the docs say so explicitly?
- **Lease paths are compared as typed text.** The task's frozen lease is compared with the new fence's lease by raw string equality (`LeasePaths`), before any path resolution. Reordering the paths in the config, or spelling one through an alias, fails the task permanently even though the reach is the same. Comparing the resolved, sorted sets would avoid that.

## Optional improvements

- `open` freezes, and so publishes a manifest, before `leased` checks whether the lease has ended (`lib.rs:654-655`). The result is harmless, but checking the end first avoids freezing under a lease that has already lapsed.

## Checks run

| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `python3 .github/scripts/changes.py check` | pass (293 fragments ok) |
| `cargo clippy --locked -p orch-core -p orch-bbp -p orch-store -p orch-dispatch -p rusty_orch --all-targets -- -D warnings` | pass |
| `cargo test --locked` on the same five crates | pass; 19 `orch-bbp` e2e tests ignored (they need built binaries) |
| Probes in a scratch copy of the head (`$TMPDIR/probe`) | both reproduced: `probe_cross_member_read_beneath_another_members_write_escapes_the_lease`, `probe_cancel_under_a_lease_is_refused_for_an_unconfined_member` |

- **New library code:** no `unwrap`, `expect` or indexing that can panic. `lease.rs` and `lib.rs` keep the crate-level deny lints.
- **New tests at the base:** they do not compile there, because they use the new API (`Fence.lease`, `Grant.lease`, `LeaseSpec`), so they cannot pass at the base.

## Validation gaps

- I did not run `python3 .github/scripts/orch_bbp_acceptance.py`, including the new `lease-confined` mode, or any ignored e2e test. They need `BBP_TEST_BIN`/`ORCH_TEST_BIN` and an enforced Landlock sandbox. That suite is not a pass.
- The last step of finding 1, Landlock granting read through the planted symlink, comes from reading `open_path_fd` and `Turn::command`. To settle it, run a two-member `lease-confined` variant on a Landlock-capable host with built binaries.

**Verdict: changes required.**
