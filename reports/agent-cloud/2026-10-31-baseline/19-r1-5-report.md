<!-- agent-cloud/19-r1 run 5, variant baseline, prompt 7c00a3eee28643720845867db321dcd6e32967b9, model claude-opus-5-5, 2.1.314 (Claude Code); 398 s, 2161680 tokens, $1.76 -->

# Review of PR 19: per-agent CPU, memory and disk limits (ADR-023)

- **Repository:** baileyrd/agent_cloud, PR #19
- **Base:** `b9f9532a759da13b69cdb549ee3cd163fac1c42e`
- **Head:** `69fe6877ddec11081fb131f2bbe2a8e76eb4f277`. `git fetch origin pull/19/head` returned exactly this SHA. The PR is one commit.

**Verdict: changes required.** There is one P1: a VM started with `sudo ac-ctl` silently runs with no limits. There are six P2s. The core is sound: limits are applied inside `boot` on every create and resume, the disk quota is set before the VM starts, a failed limit undoes the boot, and the domain code does no I/O. The problems are in how the limits are read and wired up, in the host test script, and in a few doc claims.

## Blocking findings

### 1. P1: `ac-ctl create/resume/fork` boots an agent with no limits and says nothing
- **Where:** `crates/ac-ctl/src/main.rs:88`; the claim it breaks is `docs/decisions.md:993`.
- **Trigger** (*source-traced*): `ac-ctl` refuses to change agents while the gateway holds the registry, so the operator stops the gateway and runs `sudo ac-ctl resume a1`.
  - `ac-ctl` reads `AC_AGENT_*` only from its own environment (`std::env::var`), and `sudo` clears that environment.
  - It never reads `/etc/agent-cloud/gateway.env`, even though it already reads that file for `AC_BIND` (`gateway_bind`, line 44).
- **Result:** the agent boots with no `cpu.max`, no `memory.max` and no quota, with no warning, and stays that way until its next resume. ADR-023 says the settings live "in `gateway.env` (and read by `ac-ctl` too)".
- **Fix:** take any `AC_AGENT_*` variable that is unset in the environment from the last matching line of `gateway.env`, the same way `gateway_bind` does. If that file can't be read and no variable is set, refuse to boot rather than boot with no limits.
- **Test that should go red without the fix:** a unit test like `gateway_bind`'s, where a `gateway.env` text containing `AC_AGENT_MEMORY_MIB=2560` gives `Some(2560)`.

### 2. P2: `scripts/limits-test.sh` cannot pass on a real host
- **Where:** `scripts/limits-test.sh:30` and `:34`.
- **(a) Process check always fails** (*executed*): cgroupfs files report size 0. On this host, a `cgroup.procs` listing 46 processes has `stat` size 0, and `[ -s ]` returned false. So the script always prints `FAIL: no processes` and exits 1. Use `[ -n "$(cat "$CG/cgroup.procs")" ]` instead.
- **(b) Script dies early without a memory controller** (*executed* against a fake cgroup dir): with `pipefail`, the missing `memory.events` makes `grep` exit 2, so the script stops with exit 2.
  - That file is missing whenever the sandbox cgroup has no memory controller, which is the state ADR-023's Facts record when no memory limit is set.
  - It stops before the CPU and disk tests, with no message, and exit 2 is the same code as "run as root".
  - Fix: add `|| true` to that pipeline.

### 3. P2: `limits-test.sh` leaves the temporary quota in place on failure
- **Where:** `scripts/limits-test.sh:61–64`.
- **What happens** (*executed* with stub `ctr` and `btrfs`, gateway.env setting `AC_AGENT_DISK_GIB=50`): the script sets the quota to current use + 256 MiB. If `ctr task exec` then fails (agent stopped, exec timed out), `set -e` exits right after `out=$(gx disk …)`.
  - The `EXIT` trap only kills the FIFO holder.
  - The stub log shows `btrfs qgroup limit 269484032 …` and no restore call. The script exits 1 with no message.
- **Same outcome:** Ctrl-C during `dd`, or a quoted `AC_AGENT_DISK_GIB="50"` (the restore `btrfs` call then fails).
- **Effect:** the tenant's workspace is left nearly full (`EDQUOT`) until its next resume.
- **Fix:** restore the configured limit from the `EXIT` trap once the trial limit is set, and strip quotes from `configured`.

### 4. P2: `ac-ctl` now needs a readable Kata config for every command
- **Where:** `crates/ac-ctl/src/main.rs:89`.
- **What happens** (*executed*): `limits.check(capacity.guest()?)` now runs before any command is dispatched, so `status`, `pause`, `destroy`, `reconcile`, `checkpoints` and `delete-checkpoint` also need a readable Kata config and valid `AC_AGENT_*` values.
  - With `AC_KATA_CONFIG=/nonexistent`: `ac-ctl: guest memory failed: /nonexistent: No such file or directory`, exit 1.
  - With `AC_AGENT_MEMORY_MIB=2G`: the command is refused the same way.
  - At base, `guest()` was only called during admission (`ac-fleet` line 420), for create, resume and fork.
- **Effect:** when Kata is broken, the commands you need to recover (pause, destroy, reconcile) stop working.
- **Fix:** check the limits only for create, resume and fork. Alternatively, skip the guest check when no memory limit is set.

### 5. P2: very large values wrap round instead of being refused
- **Where:** `crates/ac-domain/src/limits.rs:123`, `:129` and `:94`.
- **What happens** (*executed*, test in a scratch copy):
  - `AC_AGENT_MEMORY_MIB=17592186044416` gives `memory.max = "0"`, and `check(2048)` still returns `Ok`. Every boot would be OOM-killed.
  - `AC_AGENT_DISK_GIB=17179869185` silently becomes a 1 GiB quota.
  - `guest_mib + 128` with a huge `AC_VM_MEMORY_MIB` panics in a debug build and wraps in a release build.
- The ADR promises that "zero or anything malformed refuses the start".
- **Fix:** use `checked_mul`/`checked_add` and refuse values that overflow, with an error naming the variable.
- **Test that should go red without the fix:** add these values to `malformed_values_name_their_variable`.

### 6. P2: memory used while the VM boots never counts against `memory.max`
- **Where:** `crates/ac-ctr/src/lib.rs:551–552` and `crates/ac-ctr/src/limits.rs:73`; the ordering is set in ADR-023 at `decisions.md:998`.
- **Why** (*source-traced*): `+memory` is written to `agents/cgroup.subtree_control` only after `ctr run -d` returns.
  - The cgroup-v2 docs say memory stays charged to the cgroup that first used it until it is freed.
  - So on the first limited boot after each host reboot, the memory the guest touches while booting (guest kernel, init, container start, using `/dev/shm`) is charged to `agents`. It never counts against `kata_<id>`'s `memory.max`.
  - The CPU limit is likewise missing for that whole boot window.
- **Fix:** write `subtree_control` before `start_container`. Writing it twice does no harm, and it is fine before the VM exists. Keep `cpu.max` and `memory.max` after the start, and change that one sentence in the ADR.
- **Test:** have the mock `ctr run` record whether `<root>/agents/cgroup.subtree_control` exists when it runs.

### 7. P2: the "missing cgroup" error doesn't name the Kata setting, unlike the ADR says
- **Where:** `crates/ac-ctr/src/limits.rs:76`; the ADR text is at `decisions.md:1000`.
- **What happens:** the ADR says a sandbox cgroup that is "missing or holds no process" fails the boot with a message naming `sandbox_cgroup_only = true`. When the cgroup is missing, the error is only `read …/cgroup.procs: No such file or directory`.
- The test `an_empty_or_missing_cgroup_fails_the_boot_and_undoes_it` checks for exactly that message (`"cgroup.procs"`), so the gap is locked in.
- **Fix:** map `NotFound` to the same hint and update the test.

## Design questions
- **Container root filesystem and the disk quota:** `ctr run` has no `--read-only`, and the quota covers only the workspace. If Kata's writable container root filesystem is stored under `/var/lib/containerd` on the host's root filesystem, an agent writing to `/var/tmp` fills the host disk anyway. In that case the new threat-table row (`security.md:24`, "Runaway agent starves the host … a btrfs quota on its workspace") claims more than it delivers. Settle it on BaileyServ: `dd` 1 GiB to `/var/tmp` inside a1 and compare `df /var/lib/containerd`.
- **zram swap is outside `memory.max`:** `memory.swap.max` is not set. The ADR expects a sandbox at its limit to spill into zram, and compressed zram memory is not charged to the cgroup. One agent can therefore hold host RAM beyond `AC_AGENT_MEMORY_MIB` (up to the 8 GiB of zram) and take swap other agents need. That contradicts install.md:535, "host memory for the whole sandbox". Consider also writing `memory.swap.max`.
- **Restores give a fresh quota:** a restore, a fork or a `restore-backup` resets what counts against the quota, so each one gives the workspace a fresh N GiB. The ADR documents this. It is bounded by the checkpoint cap, but worth putting under Known gaps in `security.md`.

## Optional improvements
- `limits-test.sh` could check that the PIDs of QEMU, virtiofsd and the shim (`pgrep -f 'sandbox-<id>,'` and friends) are in `cgroup.procs`, not just that the file is non-empty. With `sandbox_cgroup_only = false`, a non-empty cgroup may still not hold them.
- The gateway could warn at startup when CPU or memory limits are set but the Kata config does not have `sandbox_cgroup_only = true`.
- The script's helper function `stat()` shadows the `stat` command. It's harmless now but easy to trip over later.

## Tests
- The new `ac-ctr` and `ac-domain` tests rely on the new `CtrConfig` fields and `ac_domain::limits`, so they don't compile at base. `create_names_the_cgroup_parent…` also checks for the new `--cgroup` argument.
- Not covered by any test:
  - reading the limits in `ac-ctl` or the gateway, including finding 1;
  - the overflow inputs in finding 5;
  - the controller ordering in finding 6.

## Checks run
- `scripts/ci-local.sh scripts`: all 15 files pass. dash isn't installed here, so `sh -n` ran under bash, which is laxer than CI.
- `scripts/ci-local.sh rust`, with `CARGO_HOME` and the target dir moved to `$TMPDIR` because `~/.cargo` is read-only:
  - `cargo fmt --check` and `cargo clippy -D warnings`: pass.
  - `cargo test --workspace --no-fail-fast`: everything passes except 4 tests in `ac-gateway/tests/notify.rs`. They fail at `UnixDatagram::bind` with EPERM because this sandbox blocks Unix sockets. That file is unchanged and the failure happens before the gateway runs, so it is unrelated to this PR.
- New Rust code: no `unwrap` or `expect` outside tests, and no I/O in `ac-domain::limits`.
- Probes in scratch copies: the overflow test (finding 5), `-s` on a real cgroupfs file (finding 2a), `limits-test.sh` with stub `id`, `ctr`, `btrfs` and `findmnt` and a fake cgroup dir (findings 2b and 3), and the built `ac-ctl` (finding 4).

## Validation gaps (need root, KVM and Kata on BaileyServ)
- Whether Kata 3.26 really creates `agents/kata_<id>` next to `--cgroup /agents/<id>` and puts QEMU, virtiofsd and the shim in it. The ADR's Facts claim this; I did not see it.
- Whether a simple-quota `qgroup limit` (which limits referenced bytes) actually returns `EDQUOT` inside the guest over virtiofs.
- Whether throttling and OOM behave as described.
- Where container root-filesystem writes land.

All of these need `sudo scripts/limits-test.sh --enable-quota a1`, after findings 2 and 3 are fixed. As written, the script can't pass.
