# Grounding: agent-cloud

Target: baileyrd/agent_cloud, scope `crates/`, `deploy/`, `images/`, `scripts/`, `docs/`.

## Review-fix commits

```
python3 -m orchestrator mine --target ../agent_cloud --cases bench/cases/agent-cloud \
    -- crates deploy images scripts docs
```

found 6 commits whose messages answer a finding (11 held-out commits removed). No PR in
the repo had a GitHub review round: all 25 merged or closed without reviews or review
comments (checked 2026-10-31). The defects this project actually caught were found live,
on BaileyServ or a1, and fixed in commits whose messages quote the symptom. Those
field-found fixes are the main source below, alongside the 6 mined commits.

Held out (bench cases, never named in the prompts): PR #14 with its fix PR #16, and PR
#19 with its fix PRs #20 and #22 (#21 and #23 were closed drafts of the same fixes).

## Defect classes

**Tenant isolation** (rarest trait)
1. **Host action hits the wrong process.** A kill pattern matched a shell whose command
   line held the same words (`72bd1d0`); a pattern starting with `-` was read by pkill as
   an option (`3ff1105`). Match on argv[0] and pass patterns after `--`.
2. **A lookup that silently falls back.** `tmux display -t` falls back to the current pane
   when the window is gone, so the job status probe saw the agent's shell and the kill
   script would have killed it (`d20300b`). Any target lookup must fail when the target
   is missing, not resolve to something else.
3. **Tenant bytes reaching a command.** The viewer's view guarantee moved from tmux
   read-only into the type system: a closed command enum, input only as hex send-keys
   (`b4024cf`); a window id is accepted only as `@<digits>` from the latest list
   (`dee4ad3`). Every string from an agent or a token holder that reaches a host command
   is parsed into a value and rendered back.

**Credential and egress containment**
4. **Credential wider or longer-lived than the job.** The capacity bench read the owner
   token; it now creates a scoped throwaway token and revokes it on every exit path, and
   refuses to start if one is left over (`45b7b88`).
5. **Secret file modes and ownership.** The egress agents file must be root:root 0600 and
   each attached profile root-owned and parseable, else doctor names it (`0a7b78e`).
   Security.md's threat table (ADR-011, ADR-016, ADR-022) lists what each credential may
   reach.

**Workspace durability**
6. **A partial operation left in place.** A backup cut short is never listed and is
   deleted next run; restore keeps the replaced workspace as a checkpoint (`a751823`,
   ADR-021); a stream is written as `.part`, flushed and renamed (`a611e2f`).
7. **Test fakes that differ from the real tool.** The stand-in btrfs lost the sticky bit
   as non-root, so CI saw subvolumes as plain directories (`b852766`).

**Interface fidelity**
8. **Two tables for one catalog.** MCP tools repeated the REST routes and a test kept
   them in step; ADR-020 made a rule carry its tool (`05bd4ee`). A scope check present on
   one transport and not the other is the risk.

**Host operability**
9. **Slow taken for wedged.** The old watchdog SIGKILLed exec clients under load; it now
   acts only on sustained failures on an idle host, with a logged ladder (`1cfa43c`).
10. **Exec stdin and the Kata shim.** Handing a live Kata exec EOF or `/dev/null` wedged
    the shim at the 126th exec (`07d6b41`, `f9c4d9d`, `31889f6`).
11. **Host facts read the wrong way.** A btrfs subvolume's `st_dev` is not the block
    device (`f94684e`); a duplicated key must be refused as Kata does (`66eac64`).
12. **Checks that match text instead of status.** curl's stderr and stdout interleave
    through an exec, so a correct proxy denial was reported as FAIL (`2168365`); the
    post-reboot check must fail when the gateway's status says reconcile failed
    (`b457984`). Scripts are checked with dash's `sh -n` in CI; Fedora's sh is bash
    (`e513b7d`, `scripts/ci-local.sh`).
13. **Readiness.** `Type=notify` with `READY=1` only once the listener is bound
    (`95a8460`, `1905d56`, ADR-015).

## Oracles

- Kata Containers 3.x: `configuration.toml` (`sandbox_cgroup_only`, `default_memory`,
  `default_vcpus`) and the shim's cgroup layout; containerd `ctr` task and exec semantics.
- Linux cgroup v2 (`Documentation/admin-guide/cgroup-v2.rst`: `cpu.max`, `memory.max`,
  `memory.events`, controller delegation through `cgroup.subtree_control`), PSI
  (`Documentation/accounting/psi.rst`).
- btrfs-progs: `send`/`receive --dump` stream semantics, subvolumes, simple quotas
  (`btrfs quota enable --simple`, `qgroup limit`).
- systemd: `sd_notify(3)`, `systemd.service(5)` (`Type=notify`, `NotifyAccess`).
- tmux(1) target resolution; firewalld and nftables for the agents zone.
- MCP specification (tools/list, tools/call) for the gateway's MCP interface.
- The pinned rusty_mill rev in `Cargo.toml` (ADR-008) for `rusty_serve`, `platform`,
  `rusty_crypto_key`.

## Settled decisions

`docs/decisions.md` ADR-001 to ADR-023 (each with Status, Facts, Decision), threat model
and Known gaps in `docs/security.md`, trust boundaries in `docs/architecture.md`, host
procedure and "Operations: commands not to run" in `docs/install.md`, measured numbers
in `docs/sizing.md`. The repo has no AGENTS.md, CONTRIBUTING.md or change fragments.
