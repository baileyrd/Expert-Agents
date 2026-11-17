<!-- agent-cloud/19-r1 run 4, variant baseline, prompt 7c00a3eee28643720845867db321dcd6e32967b9, model claude-opus-5-5, 2.1.314 (Claude Code); 361 s, 1579121 tokens, $1.45 -->

# Review of baileyrd/agent_cloud PR 19: per-agent CPU, memory and disk limits (ADR-023)

- **Base:** b9f9532a759da13b69cdb549ee3cd163fac1c42e
- **Head reviewed:** 69fe6877ddec11081fb131f2bbe2a8e76eb4f277 (fetched `pull/19/head`; it matches)

**Verdict: changes required.** I found two P1 and five P2 defects. Five of the seven were confirmed by running probes; the other two are traced from the source.

## Blocking findings

### P1-1. `limits-test.sh` leaves a quota of current use + 256 MiB on the agent whenever a disk limit is configured
`scripts/limits-test.sh:64`, `:61`, `:26`
- **What happens (executed):** the restore step builds its argument as `"${configured:+${configured}G}${configured:-none}"`. When `AC_AGENT_DISK_GIB=50`, that expands to `50G50`.
- btrfs-progs 7.1 refuses that value: `ERROR: 50G50 is not a valid numeric value with supported size suffixes`, exit 1.
- Under `set -e` the script stops there. The trial limit from line 61 stays on the running agent's workspace until its next resume, so the agent gets `EDQUOT` after about 256 MiB more of writes. The script never prints the disk result.
- The trigger is the recommended setup (a disk limit set in `gateway.env`), so this happens on every run.
- **Second path:** the trap only kills the fifo holder. Any failure between lines 61 and 64 (for example, `ctr task exec` failing makes `out=$(gx …)` fail) also leaves the trial limit in place, even with no limit configured.
- **Fix:** use `${configured:+${configured}G}` with a separate `none` fallback. Save the limit to put back and restore it from the `EXIT` trap.
- **Regression check:** run the script against a fake `btrfs` that logs its argv (like my probe) and assert the last `qgroup limit` call is `50G <ws>`, including when `ctr` fails.

### P1-2. `sudo ac-ctl create/resume` boots agents with no limits, and says nothing
`crates/ac-ctl/src/main.rs:88`
- **ADR-023 (decisions.md:993):** the settings live "in `gateway.env` (and read by `ac-ctl` too)".
- **What the code does (source-traced):** `ac-ctl` reads `AC_AGENT_*` only from its own environment. Its docs say to run it with `sudo` while the gateway is stopped (install.md:434–445, 501–505). `sudo` resets the environment, so these variables are gone.
- The same tool already falls back to `gateway.env` for `AC_BIND` and `AC_PROFILE` (`main.rs:44`, `doctor.rs`), but not for the limits.
- **Result:** `sudo ac-ctl resume a1` starts a VM with no `cpu.max`, `memory.max` or quota, and nothing reports it.
- **Fix:** read `AC_AGENT_*` from `/etc/agent-cloud/gateway.env` when they are not in the environment. If that file can't be read, refuse the boot rather than booting unlimited.
- **Regression test:** a unit test in the style of `gateway_bind` that resolves the limits from a `gateway.env` text.

### P2-1. Very large limit values wrap to 0 instead of being refused
`crates/ac-domain/src/limits.rs:123`, `:129` (also `:94`)
- **What happens (executed):** `AC_AGENT_MEMORY_MIB=17592186044416` parses, passes `check(2048)`, and produces `memory.max = "0"`. `AC_AGENT_DISK_GIB=17179869184` produces a quota of 0 bytes.
- The cause is that `m << 20` and `g << 30` silently drop the overflowing bits. This breaks ADR-023's "anything malformed refuses the start".
- With `memory.max` at 0, every VM is OOM-killed right after `create` returns Ok. With a 0 quota, every write fails.
- `guest_mib + MIN_OVERHEAD_MIB` can also overflow.
- **Fix:** use `checked_mul` / `checked_shl` with an upper bound, and return an error on overflow.
- **Regression test:** add these two values to `malformed_values_name_their_variable`.

### P2-2. `limits-test.sh` always reports "no processes", and `--enable-quota` can never run on a fresh host
`scripts/limits-test.sh:30`, `:38`, `:51`
- **First problem (executed):** cgroupfs files report a size of 0. On this host, `stat` gave `size=0` for a `cgroup.procs` that holds 58 PIDs, so `[ -s …/cgroup.procs ]` is false. The script therefore prints `FAIL: no processes` and exits 1 even on a correct host.
- **Second problem (executed):** when `kata_<agent>` does not exist, the `awk` on the missing `cpu.stat` at line 38 ends the script with exit 2 before line 51. My run with `--enable-quota` made no `btrfs` call at all.
- That breaks the documented order. `gateway.env` and install.md say to set `AC_AGENT_DISK_GIB` and enable quotas through this script. But with the limit set and quotas off, every boot is refused, so no agent is running for the script to use.
- **Fix:**
  - Test for processes with `[ -n "$(cat …)" ]`.
  - Run the quota enable first, independent of the cgroup checks, or document `btrfs quota enable --simple <mount>` as its own step to do before setting the limit.

### P2-3. Rolling back a start after `ctr run` succeeded does not stop the running task
`crates/ac-ctr/src/lib.rs:552` → `undo_start` (`:515`)
- **What happens (source-traced):** until this PR, `undo_start` only ran after `ctr run` itself failed. Now it also runs when `limit_cgroup` fails, which is after `ctr run -d` started a VM.
- `undo_start` runs `pkill`, then `ctr container delete`. It never runs `task delete --force`, unlike `stop()` (`:558`).
- The `boot` comment says "Removing the container fails while a task still runs in it". Right after `pkill`, containerd may not yet have seen the shim die. Then the container delete, the snapshot removal and the bundle `rootfs` removal (still mounted) fail. The undo reports an error and leaves the container record behind until a later boot clears it.
- **Fix:** undo a started VM with `stop()`, so `task delete --force` comes first.
- **Regression test:** extend `an_empty_or_missing_cgroup_fails_the_boot_and_undoes_it` to assert that `task delete --force` runs after `run`.

### P2-4. Memory used while the first VM boots is charged to the parent cgroup, not the agent's
`crates/ac-ctr/src/limits.rs:73`
- **What happens (source-traced, per the kernel's cgroup-v2 docs):** a page stays charged to the cgroup that created it, and moving or enabling controllers does not move existing charges.
- `+memory` is first written to `agents/cgroup.subtree_control` *after* the first VM has booted. ADR Facts confirm that until then, guest RAM is charged to `agents`.
- So for the first agent started after each host boot, the guest RAM touched during boot is not counted against its `memory.max`.
- **Fix:** enable the controllers in the parent before `ctr run`, creating `<root>/<namespace>` if it is missing. Keep writing `cpu.max`/`memory.max` after the start, as the ADR decides.
- **Test:** assert that the `subtree_control` write happens before the `run` command.

### P2-5. With quotas off, the boot error does not say what to do
`crates/ac-ctr/src/limits.rs:47`
- ADR-023 (decisions.md:1010–1011) says that with quotas off, the boot "fails … with what to do".
- The code passes through the raw `btrfs qgroup limit` error. Only the plain-directory case adds guidance. No test covers the quotas-off case.
- **Fix:** wrap the error with "enable simple quotas: `btrfs quota enable --simple <mount>`", and add a mock test where `btrfs` exits non-zero.

## Design questions
- **Writes outside the workspace may not be covered by the disk limit.** The container's own filesystem (`/`, `/tmp`, home outside `/workspace`) is shared from the host, so writes there probably land in containerd's snapshot directory on the root filesystem, which the quota does not cover. The new threat-table row says "Runaway agent starves the host" is handled by "a btrfs quota on its workspace". Should security.md list writes outside `/workspace` as still open, or should the image make them tmpfs/read-only? Which snapshotter is used isn't recorded in the repo.
- **Removing the disk limit leaves old quotas in place.** Unsetting `AC_AGENT_DISK_GIB` keeps existing limits on subvolumes, which differs from "a host that sets none behaves as before". The rollback text partly covers this via `quota disable`.

## Optional improvements
- `ac-ctl` now reads the guest size for every command (`main.rs:89`), so `status`, `pause`, `destroy`, `reconcile` and the snapshot commands fail if the Kata config can't be read. It would be better to check only on `create`/`resume`.
- `limits-test.sh` could check that the shim, QEMU and virtiofsd PIDs are actually in `kata_<id>`, not just count processes. Without that, `sandbox_cgroup_only = false` would be caught only partly.

## Checks performed
| Check | Result |
|---|---|
| `scripts/ci-local.sh scripts` | Passed, 15 files. dash is not installed, so `sh -n` ran as bash, which is laxer than CI's dash. |
| `cargo fmt --check` | Passed |
| `cargo clippy --workspace --all-targets --locked`, `-D warnings` | Passed, no warnings |
| `cargo test --workspace --locked --no-fail-fast` | 24 test binaries passed, including all new `ac-domain` and `ac-ctr` limit tests. `ac-gateway/tests/notify.rs` had 4 failures with `Operation not permitted` when opening a socket; that test is not touched by this PR and fails because of this sandbox. I did not see it pass. |
| `unwrap`/`expect` outside tests; I/O in `ac-domain` | None in the new code |
| New tests at the base | They use the new `CtrConfig.limits`/`cgroup_root` fields, so they cannot compile at the base and guard the change |

Probes (all executed in a scratch copy):
- the shell expansion of the restore argument, and btrfs-progs 7.1 parsing it;
- `-s` on a real `cgroup.procs`;
- `limits-test.sh --enable-quota` with no running agent, using fake `ctr`/`btrfs`;
- an `ac-domain` unit test with oversized values.

**Validation gaps** (these need root, KVM and Kata on BaileyServ):
- `scripts/limits-test.sh` end to end, once P1-1 and P2-2 are fixed;
- that `ctr run --cgroup /agents/<id>` still gives `agents/kata_<id>` holding the shim, QEMU and both virtiofsd;
- that Kata never rewrites `cpu.max`/`memory.max` after the start;
- that `EDQUOT` reaches the guest through virtiofs;
- that `btrfs quota enable --simple` works on the root filesystem;
- whether writes to the container's own filesystem reach the host root filesystem (`dd` to `/tmp` in a guest while watching `df /`).

**Cleanup needed:** `git worktree add` registered my scratch copy (`$TMPDIR/scratch`) as a worktree in this repository's `.git`. `git worktree list` shows it as prunable. Nothing in the reviewed tree changed, but you may want to run `git worktree prune` to remove the entry.
