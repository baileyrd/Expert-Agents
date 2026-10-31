<!-- agent-cloud/19-r1 run 3, variant baseline, prompt 7c00a3eee28643720845867db321dcd6e32967b9, model claude-opus-5-5, 2.1.314 (Claude Code); 192 s, 670492 tokens, $0.89 -->

I found four blocking problems in PR 19 (per-agent CPU, memory and disk limits, ADR-023), so the verdict is **changes required**. The worst is that limits are silently skipped whenever an operator boots an agent with `sudo ac-ctl`.

**Repository:** baileyrd/agent_cloud, PR 19
**Base:** `b9f9532a759da13b69cdb549ee3cd163fac1c42e`
**Reviewed head:** `69fe6877ddec11081fb131f2bbe2a8e76eb4f277` (fetched `pull/19/head` and confirmed it matches). The diff is one commit, `69fe687 Per-agent CPU, memory and disk limits (ADR-023)`.

## Blocking findings

### 1. P1: `ac-ctl create` and `ac-ctl resume` boot agents with no limits
- **Where:** `crates/ac-ctl/src/main.rs:88`; `docs/decisions.md:993`; `crates/ac-ctl/src/main.rs:6-11`
- **What happens (source-traced):** `ac-ctl` builds its limits with `AgentLimits::from_lookup(|k| std::env::var(k).ok())`, so it only sees its own process environment. It reads `/etc/agent-cloud/gateway.env` only for `AC_BIND` and `AC_PROFILE` (`main.rs:40-46`, `doctor.rs`). The limits are set only in `gateway.env`, and `sudo` drops the caller's environment anyway.
- **Trigger:** `install.md:441-446` tells operators to stop the gateway, run `sudo ac-ctl ... reconcile`, then resume each agent. A `sudo ac-ctl resume a1` there gets the default limits: no `cpu.max`, no `memory.max`, no qgroup limit. Nothing is logged. A new agent from `ac-ctl create` also gets no disk quota.
- **Contradiction:** ADR-023 says the three settings are "in `gateway.env` (and read by `ac-ctl` too)". The ADR also says "unset … behaves as before", so nothing flags the gap, and the VM stays unlimited until its next gateway resume.
- **Fix:** have `ac-ctl` fall back to the last `AC_AGENT_*=` line in `GATEWAY_ENV`, the same way `gateway_bind` reads `AC_BIND`. Also print the limits it applies.
- **Regression test:** a unit test next to `gateway_bind`'s, where an env-file text holding `AC_AGENT_MEMORY_MIB=2560` gives `memory_mib == Some(2560)` with the variable unset.

### 2. P1: `limits-test.sh` never restores the configured disk limit and leaves a tiny quota on a live workspace
- **Where:** `scripts/limits-test.sh:58-64`, `:26`
- **What happens (executed):**
  - `"${configured:+${configured}G}${configured:-none}"` expands to `50G50` when `AC_AGENT_DISK_GIB=50`, which is the case the docs recommend. Probed under bash: `arg=[50G50]`.
  - `btrfs qgroup limit 50G50 …` gives `ERROR: 50G50 is not a valid numeric value with supported size suffixes` (executed with btrfs-progs on this host).
  - Under `set -e` the script exits right there. The running agent's workspace keeps the trial limit of current use plus 256 MiB, and the agent hits `EDQUOT` until its next gateway resume.
  - The `EXIT` trap only kills the stdin holder, so other failures between lines 61 and 64 also leave the trial limit in place. One example is `out=$(gx disk …)` failing because `ctr exec` errors.
  - The message on line 70 has the same bug (`50 GiB50`).
- **Fix:** use `${configured:+${configured}G}` with a separate default, e.g. `restore=${configured:+${configured}G}; restore=${restore:-none}`. Put the quota restore in the `EXIT` trap once line 61 has run.
- **Test:** a dash/bash probe of the expansion, plus a loopback btrfs run of the disk section with `AC_GATEWAY_ENV` pointing at a file that holds `AC_AGENT_DISK_GIB=1`.

### 3. P2: `limits-test.sh` always reports "no processes in the cgroup"
- **Where:** `scripts/limits-test.sh:30`
- **What happens (executed):** `[ -s "$CG/cgroup.procs" ]` checks the file size, and cgroupfs files always have size 0. `stat -c %s /sys/fs/cgroup/cgroup.procs` shows 0, and `-s` was false while `wc -l` counted 865 lines.
- **Effect:** the check always prints `FAIL: no processes in …`, so the script exits 1 even on a correct host. That makes it useless as the verification step the roadmap and security checklist rely on.
- **Fix:** `[ -n "$(cat "$CG/cgroup.procs")" ]`.

### 4. P2: messages and `ac-ctl` behaviour disagree with ADR-023
- **Missing cgroup error (`crates/ac-ctr/src/limits.rs:76`):** if the sandbox cgroup is missing, the error is `read …/cgroup.procs: No such file or directory`. ADR-023 (`decisions.md:1001`) says it names `sandbox_cgroup_only = true`. The unit test `an_empty_or_missing_cgroup_fails_the_boot_and_undoes_it` even asserts the plain text.
- **Quotas off (`limits.rs` `limit_disk` → `snap.rs:107`):** with quotas off, the boot fails with only btrfs's stderr. The ADR (`decisions.md:1011`) says it "fails the boot with what to do", i.e. it should name `btrfs quota enable --simple` or `limits-test.sh --enable-quota`. No test covers a failing `qgroup limit`.
- **Kata config read on every command (`crates/ac-ctl/src/main.rs:89`):** `limits.check(capacity.guest()?.0)?` now runs for every fleet command (pause, destroy, status, reconcile, snap), even when no limit is set. These now fail if the Kata config can't be read, for example when `AC_KATA_CONFIG` is set only in `gateway.env`. Before this PR only create and resume (admission) read it.
  - **Fix:** call `guest()` only when `memory_mib` is set, or only for create and resume.
  - **Test:** `ac-ctl status` succeeds with no readable Kata config and no limits.

## Design questions (for the owner, not findings)
- **Limits are written after the VM starts** (`crates/ac-ctr/src/lib.rs:552`, after `ctr run -d`). Memory cgroups don't move existing charges when a controller is turned on later. So on the first limited boot after a host reboot, guest pages touched during boot stay charged to `agents/` and never count against the limit. Turning on `+cpu +memory` in `<ns>/cgroup.subtree_control` before `ctr run` would close that window.
- **Restores and forks reset quota usage.** Under simple quotas, each restore or fork starts at about zero charged. Total disk per agent can therefore reach roughly one quota per subvolume that restores and forks create. ADR-023 accepts this, but it isn't listed in the security.md threat table.

## Optional improvements
- `limits-test.sh` defines a shell function named `stat`, which shadows the real command. Use another name.
- `mktemp -u` followed by `mkfifo` in `/tmp` could use `mktemp -d` instead.

## Checks run
- **`scripts/ci-local.sh scripts`:** 15 files pass. dash isn't installed here, so `sh -n` ran under bash, which is laxer than CI's dash. `limits-test.sh` is a bash script and was checked with `bash -n`.
- **`cargo fmt --check`:** passes.
- **`cargo clippy` and `cargo test`:** not run. The build needs the pinned `rusty_mill` git dependency, and the sandbox denied the connection to github.com:443 (`~/.cargo` is also read-only; I retried with a scratch cargo home). Unverified.
- **Executed probes:** the parameter expansion in `limits-test.sh` under bash; `btrfs qgroup limit 50G50` is rejected by btrfs-progs; cgroupfs `cgroup.procs` has size 0.
- **Manual code checks:** no `unwrap`/`expect` outside tests in the new code. `ac_domain::limits` is pure with no I/O. The new tests use the new `CtrConfig` fields, so they don't compile at the base, but I couldn't run them at either SHA.

## Validation gaps (need root, KVM, Kata or btrfs on BaileyServ)
- Whether Kata 3.26 rewrites `cpu.max` or `memory.max` on the sandbox cgroup after start, e.g. on a container update. Settle by running `limits-test.sh` (once fixed) after a job runs.
- Whether writing to `agents/cgroup.subtree_control` succeeds, which cgroup v2's no-internal-processes rule could block.
- Whether a full quota really surfaces as `EDQUOT` through virtiofs.
- Whether `btrfs qgroup show -re --raw -f` puts referenced bytes in column 2 under simple quotas.
- None of the host scripts were run here.

**Verdict: changes required.**
