<!-- agent-cloud/19-r1 run 2, variant baseline, prompt 7c00a3eee28643720845867db321dcd6e32967b9, model claude-opus-5-5, 2.1.314 (Claude Code); 216 s, 889324 tokens, $0.98 -->

# Review of baileyrd/agent_cloud PR 19: Per-agent CPU, memory and disk limits (ADR-023)

**Verdict: changes required.** There are two P1 and four P2 findings. The worst is in the test script that operators are told to run: whenever a disk limit is configured, it leaves the agent's workspace with only about 256 MiB of free space and stops before reporting.

- **Base:** `b9f9532a759da13b69cdb549ee3cd163fac1c42e`
- **Head:** `69fe6877ddec11081fb131f2bbe2a8e76eb4f277`. I fetched `pull/19/head` and it matches this SHA. The PR is a single commit.

## Blocking findings

### P1-1: `limits-test.sh` never puts the configured disk limit back and leaves the agent with ~256 MiB free (executed)
**Where:** `scripts/limits-test.sh:64`, `:70`, and the restore missing from the trap at `:26`.

- **Trigger:** `AC_AGENT_DISK_GIB=50` in `gateway.env`. This is the documented setup, and install.md tells operators to run this script.
- **What happens:** the restore value is built as `${configured:+${configured}G}${configured:-none}`. When the variable is set, both halves expand, giving `50G50`. When it is unset, the result is `none`, which is correct.
  - I ran the expansion under bash: it prints `arg=[50G50] msg=[50 GiB50]`.
  - I ran `btrfs qgroup limit 50G50 <dir>` with btrfs-progs 7.1. It fails at parsing: "50G50 is not a valid numeric value", rc=1.
  - Because of `set -e`, the script exits there. The workspace keeps the trial limit set at `:61` (current use plus 256 MiB), and the result line is never printed.
- **Effect on the agent:** it gets `EDQUOT` after ~256 MiB of writes until its next resume.
- **Second path to the same state:** the restore is not on any exit path. If `ctr task exec` fails at `:62` (for example, the agent was paused meanwhile), or the run is interrupted during `dd`, the trial limit also stays.
- **Fix:**
  - Use `${configured:+${configured}G}` followed by `${configured:-none}` only when `configured` is empty. A cleaner form is `lim=none; [ -n "$configured" ] && lim=${configured}G`.
  - Restore the limit in the `cleanup` trap from the moment the trial limit is set.
- **Test:** none in the repo covers this. Add a probe that runs the restore line with `configured=50` against a `btrfs` stub and asserts the argument is `50G`. Then confirm on the host with `limits-test.sh`.

### P1-2: `ac-ctl create`/`resume` boot unlimited even though `gateway.env` sets limits (source-traced)
**Where:** `crates/ac-ctl/src/main.rs:88`, and the doc comment at `:9-10`.

- **What happens:**
  - `ac-ctl` reads `AC_AGENT_*` only from its own process environment.
  - The limits live in `/etc/agent-cloud/gateway.env`, and only `ac-gateway.service` loads that file (`EnvironmentFile`, `deploy/ac-gateway.service:18`).
  - `ac-ctl` already reads that file for `AC_BIND` (`GATEWAY_ENV`, `:40`, `:402`), but not for the limits.
- **Scenario:** the operator stops the gateway (required, otherwise exit 3) and runs `sudo ac-ctl resume a1`. This is also the step `restore-backup` tells them to take ("create (or resume) {id} to boot it"). The VM boots with no `cpu.max`, `memory.max` or quota, and nothing reports it.
- **Docs contradicted:** the module doc says the limits "apply here too", and ADR-023 says "read by `ac-ctl` too".
- **Fix:** take the limits from `gateway.env` the same way as `AC_BIND`: the process environment first, then the last matching line in the file. Alternatively, refuse to start when the file sets limits that the environment does not.
- **Test:** a unit test that builds the limits from (no env, a `gateway.env` text containing `AC_AGENT_CPUS=2`) and expects `cpus = 2`.

### P2-1: A huge memory or disk value wraps to a tiny limit instead of being refused (executed)
**Where:** `crates/ac-domain/src/limits.rs:123` (`m << 20`), `:129` (`g << 30`), and parsing at `:75`.

- **What happens:** any `u64` is accepted, and `<<` silently drops high bits.
  - I ran this with debug assertions on: `AC_AGENT_MEMORY_MIB=17592186044416` gives `memory.max 0`; `…417` gives `1048576`; `AC_AGENT_DISK_GIB=17179869184` gives `qgroup limit 0`.
  - `check()` passes, because the value is above the guest size.
  - The gateway logs the configured figure but writes a near-zero limit, so every VM is OOM-killed or the workspace is full.
- **ADR conflict:** ADR-023 says "anything malformed refuses the start".
- **Fix:** use `checked_mul`/`checked_shl`-style bounds (for example, at most host RAM, or 2^44 MiB) and return an error otherwise.
- **Test:** add the two values above to `malformed_values_name_their_variable`.

### P2-2: Unsetting `AC_AGENT_DISK_GIB` leaves the old quota on every workspace, while the gateway logs "disk unlimited" (source-traced)
**Where:** `crates/ac-ctr/src/limits.rs:33-35`, `deploy/gateway.env:18`, `docs/decisions.md:995`.

- **What happens:** with no disk limit, `limit_disk` returns early without clearing anything. A limit set by an earlier boot stays on the subvolume.
- **Docs contradicted:** `gateway.env` says "unset is unlimited", and the ADR says unset "behaves as before". Neither is true for workspaces that have already booted.
  - The ADR's rollback line, read as two separate steps, only removes the quota if the operator also runs `btrfs quota disable`. `limits-test.sh` (correctly) restores `none` when the variable is unset, which shows the intended meaning.
- **Fix:** when there is no disk limit but quotas are enabled, run `btrfs qgroup limit none <ws>`. Alternatively, document that removing a limit takes `qgroup limit none` per workspace.
- **Test:** a fake-spawner test that boots with `disk_gib: None` on a subvolume and expects `qgroup limit none` (or the documented behaviour).

### P2-3: Guest memory touched before `+memory` is enabled is never charged to the sandbox (source-traced)
**Where:** `crates/ac-ctr/src/limits.rs:68-73` writes `subtree_control` only after `ctr run -d` returns (`lib.rs:552`).

- **Background:**
  - ADR-023's Facts say `agents` passes only `cpuset cpu` to its children, so the first limited `kata_<id>` after each host reboot runs without a memory controller until this write.
  - cgroup-v2.rst (Memory Ownership) says a page "is charged to the cgroup which instantiated it and stays charged … until the area is released". Pages QEMU faults during guest boot therefore stay charged to `agents`, outside that VM's `memory.max`, for its whole lifetime.
- **Fix:** write `cgroup.subtree_control` before `start_container`. Keep the `cpu.max`/`memory.max` writes after start, as the ADR decides. Then every `kata_<id>` is created with the controllers already on.
- **Test:** extend `cpu_and_memory_limits_go_on_the_sandbox_cgroup_after_start` to assert that `subtree_control` exists before the `ctr run` call. The mock can record a file check when it is invoked.
- **Still needs the host:** confirm with `memory.current` against the guest's resident set size (RSS) in `limits-test.sh`.

### P2-4: Quotas off fails the boot with raw btrfs stderr, not "what to do" (source-traced)
**Where:** `crates/ac-ctr/src/limits.rs:46-55`, which goes through `snap.rs:107`.

- **What happens:** ADR-023 says "with quotas off, fails the boot with what to do". The cause is actually btrfs's own message, with no pointer to `btrfs quota enable --simple` or `limits-test.sh --enable-quota`.
  - The plain-directory case does name the fix (`ac-ctl workspace migrate`).
- **Fix:** map a failed `qgroup limit` to a cause that names `AC_AGENT_DISK_GIB` and the enable step.
- **Test:** a mock with `btrfs` exiting 1 that asserts the cause contains `quota enable --simple`.

**Coverage gaps (included in P2):**
- `agent_limits` in the gateway (refusing to start below guest + 128) has no gateway-level test. The domain `check` is covered.
- `limits-test.sh` has no stub test.
- The new ac-ctr and ac-domain tests are red at the base only because they don't compile there. Their assertions do guard the order (quota before `run`) and the undo-on-empty-cgroup path.

## Design questions
- `ac-ctl` now calls `capacity.guest()` and `limits.check()` for every fleet command, including `pause`, `destroy`, `status` and `reconcile`. An unreadable Kata config or a too-small `AC_AGENT_MEMORY_MIB` in the operator's shell now blocks `ac-ctl pause/destroy`, which is exactly what an operator needs during an incident. Should the check apply only to `create` and `resume`?
- When an OOM kill ends a VM, nothing reports it. ADR-023 lists this under "Not now", but a client sees an agent that silently died; the watchdog reports it as a generic VM death.

## Optional improvements
- `limits-test.sh` defines a function named `stat`, which shadows `/usr/bin/stat`. A name like `cpustat` is clearer.
- The `sed` that reads `AC_AGENT_DISK_GIB` does not strip quotes or whitespace (`AC_AGENT_DISK_GIB="50"` would give `"50"G`).

## Checks performed
- **`scripts/ci-local.sh scripts`:** passes ("15 files pass"). dash is not installed here, so POSIX scripts were checked with bash's `sh -n`, which is laxer than CI's dash. `limits-test.sh` is bash, so it is unaffected.
- **`scripts/ci-local.sh rust`:** the first run failed because `~/.cargo` is read-only in this sandbox. I reran it with `CARGO_HOME` and `CARGO_TARGET_DIR` in `$TMPDIR`.
  - `cargo fmt --check` passed and `cargo clippy --workspace --all-targets --locked` finished with `-D warnings`.
  - `cargo test`: every suite passed (including ac-domain's new limits tests and ac-ctr's 76 passed, 1 ignored) except `ac-gateway --test notify`: 4 tests failed with `Operation not permitted (os error 1)`.
  - I ran that test from a `git archive` of the base and it fails the same way, so it is caused by this sandbox, not the PR.
- **New Rust code:** no `unwrap`/`expect` outside tests, and no I/O in `ac_domain::limits`.
- **Probes I executed:** the bash restore-value expansion; btrfs-progs 7.1 rejecting `50G50`; the `u64` shift wrap with debug assertions on.

## Validation gaps (need root, KVM, Kata and btrfs on BaileyServ)
- Whether `kata_<id>` holds QEMU, virtiofsd and the shim when started with `--cgroup /agents/<id>`. Also whether writing `+memory` to `agents/cgroup.subtree_control` succeeds (the no-internal-process rule, and the root's `subtree_control`). Settle with `sudo scripts/limits-test.sh <agent>` once P1-1 is fixed.
- How simple quotas behave on forks and restores, and `EDQUOT` inside the guest over virtiofs: `limits-test.sh --enable-quota`.
- Throttling under load and the memory-limit and zram behaviour: `limits-test.sh` together with `memory.events`.
- Limits re-applied after a host reboot: `post-reboot-check.sh`, which does not check `cpu.max` or `memory.max` yet.
- ADR-023 is correctly marked **proposed**, and the roadmap and security checklist items stay unchecked. That is consistent with these gaps.
