<!-- agent-cloud/19-r1 run 1, variant baseline, prompt 7c00a3eee28643720845867db321dcd6e32967b9, model claude-opus-5-5, 2.1.314 (Claude Code); 324 s, 1615092 tokens, $1.40 -->

**Verdict: changes required.** PR 19 has two P1 defects and three P2 defects, the most serious with an executed trace.

**Pinned:** baileyrd/agent_cloud PR 19. Base `b9f9532a759da13b69cdb549ee3cd163fac1c42e`. Fetched head `69fe6877ddec11081fb131f2bbe2a8e76eb4f277`, which matches the stated head SHA. One commit: "Per-agent CPU, memory and disk limits (ADR-023)".

## Blocking findings

**1. P1: `limits-test.sh` leaves the agent's workspace with the small trial quota instead of putting the configured one back** (`scripts/limits-test.sh:64`, same pattern at `:70`). *Executed.*
- **Trigger:** `AC_AGENT_DISK_GIB=50` in gateway.env, which is the setup the docs recommend.
- **What happens:** `"${configured:+${configured}G}${configured:-none}"` expands to `50G50`, because both expansions fire when the variable is set. I checked the expansion under bash. The local btrfs-progs v7.1 rejects it before touching the filesystem: `ERROR: 50G50 is not a valid numeric value with supported size suffixes`, exit 1.
- Under `set -e` the script stops there. It never puts the configured limit back and never checks the result.
- **Fake-host run** (stub `ctr`, `btrfs` and `findmnt` on PATH, scratch copy of the script): the btrfs calls ended `qgroup limit 269484032 …/a1`, then `qgroup limit 50G50 …/a1`, and the script returned 1.
- **Effect:** the running agent is left with only 256 MiB of headroom above its current use until its next resume.
- **The same thing happens with no limit configured:** if the script stops after line 61 (Ctrl-C during the 512 MiB `dd`, or a failing `ctr exec` making `out=$(…)` fail under `set -e`), the trial quota stays. `limit_disk` only re-applies a limit when one is configured, so it then stays for good. The ADR says the configured limit is "put back after", and the persona requires that on every exit path.
- **Fix:** compute the restore value once (`restore=${configured:+${configured}G}; restore=${restore:-none}`). Restore it from the `EXIT` trap, set once the trial limit is applied.
- **Regression check:** a fake-PATH run like the one above should end with `qgroup limit 50G` for a configured limit, and with `qgroup limit none` when it is interrupted after line 61.

**2. P1: `ac-ctl` create, resume and fork skip the limits** (`crates/ac-ctl/src/main.rs:88`; claim at `docs/decisions.md:993`). *Source-traced.*
- ADR-023 says the settings live "in `gateway.env` (and read by `ac-ctl` too)". In fact `AgentLimits::from_lookup(|k| std::env::var(k).ok())` reads only the process environment. `ac-ctl` reads gateway.env only for `AC_BIND` and `AC_PROFILE` (`main.rs:44-52`, `doctor.rs`).
- **Trigger:** the operator follows install.md:529 (set the limits in gateway.env) and later runs `sudo ac-ctl resume a1`, `create`, or `snapshot fork`. sudo resets the environment, so `limits` is the default.
- **Effect:** the VM boots with no `cpu.max`, no `memory.max` and no qgroup limit, and nothing says so. Because the agent is now Running, the gateway does not re-apply the limits until it is paused and resumed.
- **Fix:** load `AC_AGENT_*` from gateway.env the way `AC_PROFILE` is loaded (environment first, then the file), or refuse to boot when gateway.env sets limits the process does not have. Fix the ADR wording to match.
- **Regression test:** a unit test for an `ac-ctl` lookup helper: a gateway.env containing `AC_AGENT_MEMORY_MIB=2560` with an empty environment must give `memory_mib == Some(2560)`.

**3. P2: `limits-test.sh` exits silently with code 2 when memory is not limited** (`scripts/limits-test.sh:34`). *Executed.*
- The cgroup has no `memory.events` when only CPU or disk is limited, because the memory controller is not enabled for it. `grep … 2>/dev/null | tr …` then fails, and `pipefail` with `set -e` stops the script.
- It stops right after the cgroup header. The CPU and disk tests never run, with no message, and exit 2 is the same code as the "run as root" refusal. My fake-host run stopped exactly there until I added a `memory.events` file.
- **Fix:** add `|| true`, or test with `[ -r ]` first. The fake-host run with no `memory.events` should reach the disk section.

**4. P2: every `ac-ctl` fleet command now needs a readable Kata config** (`crates/ac-ctl/src/main.rs:89`). *Source-traced.*
- `limits.check(capacity.guest()?.0)?` runs before the command dispatch at `:117`, even when no memory limit is set.
- At base, `ac-ctl` read the Kata config only for admission (create and resume). Now `status`, `pause`, `destroy`, `reconcile` and the checkpoint commands also fail when `/etc/kata-containers/configuration.toml` is unreadable or has no `default_memory`. `reconcile` and `destroy` are recovery commands.
- **Fix:** call `guest()` only when `memory_mib.is_some()`.
- **Regression test:** `ac-ctl status` with `AC_KATA_CONFIG=/nonexistent` and no limits must not fail.

**5. P2: coverage gaps.**
- No test covers the gateway or `ac-ctl` refusing to start with a memory limit below guest + 128 MiB (`agent_limits` at `ac-gateway/src/main.rs:192` is untested beyond `check()`).
- No test covers a failed `cgroup.subtree_control` write undoing the boot.
- Nothing tests `limits-test.sh` itself. A fake-PATH test like my probe belongs beside the other script tests.

The new Rust tests (7 in `ac-domain`, 5 in `ac-ctr`) do not compile at base because of the new `CtrConfig` fields, so they do guard the change.

## Design questions
- **Container rootfs writes may still fill the host disk.** The disk limit covers only the workspace subvolume. With Kata sharing an overlayfs rootfs over virtio-fs, guest writes to `/tmp` or `/home` would land on the host's containerd snapshot dir, which is on the root filesystem. Yet the security.md threat row and the README now present per-agent disk limits as the mitigation for "Runaway agent starves the host". I couldn't settle this from the repo. To settle it on BaileyServ: `dd` 1 GiB to `/tmp` in a1 and watch `du` of `/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots`.
- **First boot after a host reboot.** `+memory` is enabled on `agents` only after the first sandbox is already running. Guest pages that sandbox faulted before then stay charged to `agents`, not `kata_<id>`. This is bounded by guest size, but it means `memory.peak` and `oom_kill` in that cgroup under-report for that one boot.

## Optional improvements
- The `limit_disk` error says "pause it and run ac-ctl workspace migrate", but migrate also needs the gateway stopped (install.md:501).
- `limits-test.sh` divides by `LOAD_SECS`, so `LOAD_SECS=0` makes awk fail.
- The script prints guest-controlled `dd` output to the operator's terminal unfiltered.

## Checks performed
- **`scripts/ci-local.sh scripts`:** 15 files pass. dash is not installed here, so files were checked with bash `sh -n`, which is laxer than CI.
- **`scripts/ci-local.sh rust`:**
  - It needed a scratch `CARGO_HOME`, because `~/.cargo` is read-only in the sandbox.
  - `cargo fmt --check` passed, and `cargo clippy --workspace --all-targets --locked` with `-D warnings` passed.
  - `cargo test --workspace --locked --no-fail-fast`: 423 passed, 4 failed. All four failures are in `crates/ac-gateway/tests/notify.rs` (EPERM binding a Unix socket at `notify.rs:18`). That is the sandbox; the PR does not touch notify, and I did not rerun them at base.
  - All 12 new tests passed.
- **Probes (executed):** the expansion under bash; local `btrfs qgroup limit 50G50` refused; `limits-test.sh` run against stub `ctr`, `btrfs` and `findmnt` from a scratch copy.
- **New non-test Rust:** no `unwrap` or `expect`, and no I/O in `ac_domain::limits`.

## Validation gaps (need root, KVM and Kata on BaileyServ)
`scripts/limits-test.sh` should only be run there after finding 1 is fixed. It would settle:
- whether `+cpu +memory` can be written to `agents/cgroup.subtree_control` (controllers available, and no processes directly in `agents`);
- whether Kata later overwrites `cpu.max` or `memory.max`;
- whether `--cgroup /agents/<id>` still yields `kata_<id>` holding the shim, QEMU and virtiofsd;
- whether `btrfs quota enable --simple` and `qgroup limit` behave as the ADR describes.

`post-reboot-check.sh` would show whether the limits come back after a reboot.
