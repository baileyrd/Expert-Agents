# Interview

Asked in the target project: "If you had to summarize this project in 5 words what would
they be? And if we wanted to recruit an expert for this domain, what would we look for?"

Answer (Nano, 2026-10-31), verbatim:

> **In five words:** self-hosted sandboxes for AI agents.
>
> **What to look for in an expert:** someone who has built or run sandboxed compute for untrusted code. A Fly.io, Modal, E2B, Firecracker, or gVisor background fits well. Specifically:
>
> - **Isolation and virtualization:** microVMs or containers, cgroups, namespaces, and Firecracker-style snapshot and restore. Checkpoints, forks and per-agent limits all rest on this.
> - **Storage and durability:** copy-on-write filesystems such as ZFS, btrfs or overlay, plus backup and restore drills like the a2 restore from Beast.
> - **Security:** threat-modeling agents as untrusted tenants, token lifecycle and expiry, egress control, and secrets handling.
> - **Agent tooling:** working knowledge of MCP and LLM agent workflows, so the gateway and operation catalog get designed for how agents actually behave.
> - **Running a single box:** Linux systems work and systemd, plus enough observability to keep BaileyServ healthy without a platform team.
>
> The person who's hardest to find, and who matters most, is the one with real isolation and security depth. The MCP knowledge is easy to pick up on top of that.

## Profile

- **Scope**: `crates/` (gateway, ctr, domain, egress, fleet, ports, store, viewer, history,
  ctl), `deploy/`, `images/`, `scripts/`, and the decision docs `docs/decisions.md`,
  `docs/security.md`, `docs/architecture.md`, `docs/install.md`.
- **Rarest trait, first axes**: isolation and security depth, named together as rarest,
  so they become the first two axes. The first oracles after the repo are its own threat
  model (`docs/security.md`) and the Kata, cgroup v2 and btrfs sources and docs.
- **Traits to axes**: isolation and virtualization is *Tenant isolation* (no agent
  reaches the host, another agent, or more than its limits); security is *Credential and
  egress containment* (every token, proxy credential and secret is scoped, expires or is
  revocable, and never widens on error); storage is *Workspace durability* (checkpoints,
  forks, backups and restores keep or refuse, never half-write); agent tooling is
  *Interface fidelity* (REST and MCP are one catalog, scopes hold on both, agents get
  errors they can act on); running a single box is *Host operability* (fails loud,
  undoes partial work, survives a reboot, scripts and docs match the host). Observability
  is not an axis: it shapes the evidence list (journal lines, history, doctor).
