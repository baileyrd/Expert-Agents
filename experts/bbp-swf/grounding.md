# Review fixes under crates/libs/protocol/rusty_bbp, crates/apps/rusty_bbp_host, crates/apps/rusty_orch, crates/libs/rusty_sandbox, crates/foundation/rusty_crdt, docs/bbp, docs/bbp-concurrency-design.md, docs/bbp-event-based-plan.md, docs/bbp-event-driven-design.md, .github/scripts/orch_bbp_acceptance.py

99 commits answer a review; every commit of held-out PRs 763, 932 and 988 removed (48 by `mine`, 9 more from later review rounds by hand).
Read each one's diff (`git show <sha>`) for the defect class it fixed.

| Commit | Subject | Files |
|---|---|---|
| 29a59e56cf | rusty_crdt: panic-safe insert ordering, both batch groupings, qualify IdReused | `crates/foundation/rusty_crdt/src/lib.rs`, `crates/foundation/rusty_crdt/tests/laws.rs` |
| c3f81edbc2 | rusty_crdt: dependency-free sequence CRDT (RGA) with property-tested laws | `crates/foundation/rusty_crdt/Cargo.toml`, `crates/foundation/rusty_crdt/src/lib.rs`, `crates/foundation/rusty_crdt/tests/laws.proptest-regressions` … |
| 82e6427ab1 | bbp: refuse a stale co-edit only on a shared line, not a section | `crates/libs/protocol/rusty_bbp/src/coedit.rs`, `crates/libs/protocol/rusty_bbp/src/command.rs`, `crates/libs/protocol/rusty_bbp/src/engine.rs` … |
| d3d1bb1b03 | bbp slots: every acquisition clears the slot's earlier owner | `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/admission.rs`, `crates/apps/rusty_bbp_host/src/admission/tests.rs` … |
| 0be612a89c | bbp admission: tasks are scoped by store; claim write paths must be canonical | `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/admission.rs`, `crates/apps/rusty_bbp_host/src/admission/tests.rs` … |
| 4ce0676890 | bbp: scripted co-edit load harness and its report | `crates/libs/protocol/rusty_bbp/src/coedit.rs`, `crates/libs/protocol/rusty_bbp/src/lib.rs`, `crates/libs/protocol/rusty_bbp/tests/coedit.rs` … |
| 3d0eb839bc | docs(bbp): record terminal accounting fix and schema evidence limits | `crates/apps/rusty_bbp_host/src/tokens.rs` |
| 11933eece2 | fix(bbp): an error result never completes a resumed turn; require result_index | `crates/apps/rusty_bbp_host/src/tokens.rs`, `crates/apps/rusty_bbp_host/tests/review_findings.rs` |
| d55f4b1a84 | fix(bbp): fail closed when a group's members cannot be read | `crates/apps/rusty_bbp_host/src/groups.rs` |
| 3d067ac178 | fix(bbp): settle harness groups without trusting recyclable pids | `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/groups.rs`, `crates/apps/rusty_bbp_host/src/lib.rs` … |
| 62c6522d64 | fix(orch,bbp): prove cancel cleanup without the moderator; find unrecorded opens | `.github/scripts/orch_bbp_acceptance.py`, `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/bin/bbp.rs` … |
| f565ddd5bb | bbp mod: session launchers resume one conversation, one process per turn | `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/moderator.rs`, `crates/apps/rusty_bbp_host/tests/moderator_e2e.rs` … |
| 293ff878b8 | feat(bbp): let the Codex and Claude launchers run confined | `.github/scripts/orch_bbp_acceptance.py`, `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/docs/provider-launchers.md` … |
| 93fce857ba | bbp mod: wake for deadlines, keep pidfds optional | `crates/apps/rusty_bbp_host/src/moderator.rs`, `crates/apps/rusty_bbp_host/src/waker.rs`, `crates/apps/rusty_bbp_host/tests/moderator_e2e.rs` … |
| b3208bff5f | fix(bbp): consume the process group where it goes unused | `crates/apps/rusty_bbp_host/src/confine.rs`, `crates/apps/rusty_bbp_host/src/moderator.rs` |
| fc2e90c3fc | docs(bbp): mark artifact ops and RGA compatibility as proposed; label 2b account as external | `docs/bbp-event-based-plan.md` |
| 2e691dfc29 | fix(bbp): start the slot holder before its harness | `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/bin/bbp.rs`, `crates/apps/rusty_bbp_host/src/confine.rs` … |
| 856305b698 | fix(bbp): resolve a relative --work before building a confined turn | `crates/apps/rusty_bbp_host/src/confine.rs` |
| 80f6a99e29 | fix(bbp): hold agent slots in a host process, never signal a reaped group | `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/bin/bbp.rs`, `crates/apps/rusty_bbp_host/src/moderator.rs` … |
| e3b5473f6e | fix(bbp): open slot files for writing, not append, so Windows can truncate | `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/slots.rs` |
| 183411d041 | fix(orch): keep the board token out of children; refuse unlinked resume | `crates/apps/rusty_orch/ARCHITECTURE.md`, `crates/apps/rusty_orch/crates/orch-bbp/src/supervisor.rs`, `crates/apps/rusty_orch/crates/orch-bbp/src/tests_e2e.rs` … |
| 90da69a3ca | bbp mod: a wait error while draining still kills every harness left | `crates/apps/rusty_bbp_host/src/moderator.rs` |
| 69857fa6ec | fix(orch): checked budgets, child re-grant, durable execution recovery | `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/orch-core/src/factory.rs` |
| a65a65c7fe | fix(orch): harden durable execution invariants | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/orch-core/src/factory.rs` |
| 262853f6b5 | fix(orch): preserve verbatim Windows paths | `crates/apps/rusty_orch/crates/rusty_orch/src/impact.py` |
| 3357116226 | fix(orch): preserve Cargo metadata identities | `crates/apps/rusty_orch/crates/rusty_orch/src/impact.py`, `crates/apps/rusty_orch/crates/rusty_orch/tests/inspect.rs` |
| 083720c2af | fix(orch): normalize Windows metadata paths | `crates/apps/rusty_orch/crates/rusty_orch/src/impact.py`, `crates/apps/rusty_orch/crates/rusty_orch/src/inspect.rs` |
| 609b2f6c7b | fix(orch): normalize Cargo records bottom-up | `crates/apps/rusty_orch/crates/rusty_orch/src/impact.py` |
| a267905eb6 | fix(orch): sort normalized Cargo records | `crates/apps/rusty_orch/crates/rusty_orch/src/impact.py` |
| ba3ee20f56 | fix(orch): stabilize Cargo metadata revisions | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/rusty_orch/src/impact.py` |
| 712ecb6a49 | fix(orch): keep impact inspection launch bounded | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/rusty_orch/src/impact.py` … |
| 29ed9d2a5c | fix(orch): stream bundled inspection source over stdin on Windows | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/rusty_orch/src/inspect.rs` … |
| d193720c30 | fix(orch): guard maintenance publication and shared task identity | `crates/apps/rusty_bbp_host/tests/provider_launchers.rs`, `crates/apps/rusty_orch/crates/rusty_orch/MAINTENANCE.md`, `crates/apps/rusty_orch/crates/rusty_orch/src/intelligence.py` … |
| 3522c7ded7 | fix(orch): isolate maintenance validation inputs and public reports | `crates/apps/rusty_orch/crates/rusty_orch/src/maintenance.rs`, `crates/apps/rusty_orch/crates/rusty_orch/src/maintenance/e2e.rs`, `crates/apps/rusty_orch/crates/rusty_orch/src/maintenance/tests.rs` … |
| e1dd58f0e9 | fix(orch): preserve inspect impact and portable finding identity | `crates/apps/rusty_orch/crates/rusty_orch/src/intelligence.py`, `crates/apps/rusty_orch/crates/rusty_orch/tests/inspect.rs` |
| 083287ba7f | bbp: no-result reason, dead floor code, docs and one fragment for the follow-ups (third review, finding 8) | `crates/apps/rusty_bbp_host/src/tokens.rs`, `docs/bbp-concurrency-design.md` |
| 59265b3338 | tests: a turn with no result says no result (third review, finding 8) (red) | `crates/apps/rusty_bbp_host/tests/review_findings.rs` |
| 991fe6daf4 | bbp pins: a checked flag given twice is refused; tests for the third review's surviving mutations | `crates/apps/rusty_bbp_host/src/pairs.rs`, `crates/apps/rusty_bbp_host/src/pins.rs`, `crates/apps/rusty_bbp_host/tests/moderator_e2e.rs` … |
| b9a8e5af68 | tests: a checked flag given twice (third review, finding 6) (red) | `crates/apps/rusty_bbp_host/src/pins.rs` |
| 76ed54c869 | bbp pins: --tools default is every tool, so it no longer counts as no subagents (third review, finding 5) | `crates/apps/rusty_bbp_host/src/pins.rs`, `crates/apps/rusty_bbp_host/tests/fixtures/stream-json/tools-default-starts-a-subagent.jsonl`, `crates/apps/rusty_bbp_host/tests/review_findings.rs` |
| 9c3200a9f6 | tests: --tools default is every tool, the Agent tool included (third review, finding 5) (red) | `crates/apps/rusty_bbp_host/src/pins.rs` |
| 9ad6b948e8 | fix(bbp): bound failed provider loops and isolate author Git context | `.github/scripts/orch_bbp_acceptance.py`, `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/docs/provider-launchers.md` … |
| 9c7f23ad16 | fix(sandbox): retain job ownership through startup failures | `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/run_control.rs`, `crates/apps/rusty_bbp_host/src/runner.rs` … |
| c4ce8b38de | fix(orch-bbp): address task isolation and recovery review | `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/bin/bbp.rs`, `crates/apps/rusty_bbp_host/src/human.rs` … |
| 1198e6224d | fix(rusty_bbp): finalizer-only merge gate, schema 4 for event-driven logs | `crates/libs/protocol/rusty_bbp/docs/spec/bbp-v0.5-implementation-spec.md`, `crates/libs/protocol/rusty_bbp/src/engine.rs`, `crates/libs/protocol/rusty_bbp/src/event.rs` … |
| 0abafeefc6 | feat(rusty_bbp): opt-in event-driven scheduling (phase 1, step 6) | `crates/apps/rusty_bbp_host/src/admin.rs`, `crates/apps/rusty_bbp_host/src/bin/bbp.rs`, `crates/apps/rusty_bbp_host/tests/moderator_e2e.rs` … |
| 14b332751b | fix(bbp): use approval event for attribution cutoff | `crates/libs/protocol/rusty_bbp/src/spec_metrics.rs` |
| 6af00afee9 | fix(bbp): compare shared effects in overlapping spec edits | `crates/libs/protocol/rusty_bbp/src/spec_metrics.rs` |
| 43fb1dea0c | rusty_sandbox: restrict socketpair to stream/seqpacket in the endpoint-free modes | `crates/libs/rusty_sandbox/README.md`, `crates/libs/rusty_sandbox/src/helper.rs` |
| 794b80cc1b | fix(rusty_bbp): metrics overlap keyed by source path; exact stale verdicts | `crates/libs/protocol/rusty_bbp/docs/spec/bbp-v0.5-implementation-spec.md`, `crates/libs/protocol/rusty_bbp/src/engine.rs`, `crates/libs/protocol/rusty_bbp/src/metrics.rs` … |
| cc0ffc4dd8 | fix(rusty_bbp): check turns where a turn is minted; serialize finalizers | `crates/libs/protocol/rusty_bbp/docs/spec/bbp-v0.5-implementation-spec.md`, `crates/libs/protocol/rusty_bbp/src/engine.rs`, `crates/libs/protocol/rusty_bbp/src/state.rs` … |
| ff43695899 | runner: no local sockets for sandboxed commands; rusty_sandbox: Sockets::InternetOnly | `crates/libs/rusty_sandbox/README.md`, `crates/libs/rusty_sandbox/src/helper.rs` |
| 8a98b99ca4 | fix(rusty_bbp): evaluate budgets after a rejection | `crates/libs/protocol/rusty_bbp/docs/spec/bbp-v0.5-implementation-spec.md`, `crates/libs/protocol/rusty_bbp/src/engine.rs`, `crates/libs/protocol/rusty_bbp/tests/concurrency.rs` |
| ce2550bae6 | bbp: wake_set counts a verdict post only with current-stage authority | `crates/libs/protocol/rusty_bbp/src/wake.rs`, `crates/libs/protocol/rusty_bbp/tests/wake.rs` |
| fd972c85fc | test(rusty_bbp): fix clippy::redundant_closure in the wake test helper | `crates/libs/protocol/rusty_bbp/tests/wake.rs` |
| b032b7b16c | fix(rusty_bbp): a command refused before the task exists writes nothing | `crates/libs/protocol/rusty_bbp/src/engine.rs`, `crates/libs/protocol/rusty_bbp/tests/roles.rs` |
| 854da80ee0 | rusty_orch: ADR-0013 corrected after design review | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/docs/adr/0013-bbp-task-host.md` |
| 429e154d60 | rusty_orch: ADR-0013, the BBP task host (Proposed) | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/docs/adr/0013-bbp-task-host.md` … |
| ab3489e8bf | runner_e2e: the Coder reapplies its stored diff in a fresh clone; unrelated settlements settle nothing | `crates/apps/rusty_bbp_host/tests/runner_e2e.rs` |
| 2a194f9537 | runner_e2e: the shape test matches whole output lines, not the echoed command | `crates/apps/rusty_bbp_host/tests/runner_e2e.rs` |
| 9e7cf17990 | rusty_bbp_host: the recovery branches a terminal rerun can take | `crates/apps/rusty_bbp_host/docs/proving-run.md`, `crates/apps/rusty_bbp_host/docs/proving/agents.json`, `crates/apps/rusty_bbp_host/tests/runner_e2e.rs` |
| 0039903906 | runner: the work root must be owned by this user and closed to others; shape test probes the Landlock ABI and skips only without Landlock | `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/runner.rs`, `crates/apps/rusty_bbp_host/tests/runner_e2e.rs` |
| e14675a345 | runner_e2e: the sandbox-shape test probes capability on its own and uses real rename/link syscalls | `crates/apps/rusty_bbp_host/tests/runner_e2e.rs` |
| 79ba61823b | rusty_bbp_host: the environment-fault recovery sequence, specified and tested; prompts read the settlement | `crates/apps/rusty_bbp_host/docs/proving-run.md`, `crates/apps/rusty_bbp_host/docs/proving/agents.json`, `crates/apps/rusty_bbp_host/tests/runner_e2e.rs` |
| 0529f11936 | runner: exclusive per-run scratch directory, fail closed on symlinks; PAL REFER test gated on the kernel ABI; change fragment | `crates/apps/rusty_bbp_host/src/runner.rs`, `crates/apps/rusty_bbp_host/tests/runner_e2e.rs` |
| a75e77c1f9 | rusty_bbp_host: prompt and runbook corrections from the first proving run | `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/docs/proving-run.md`, `crates/apps/rusty_bbp_host/docs/proving/agents.json` … |
| 65c258f91f | proving-run runbook: split the MILL export; log shows intent, the card shows effect | `crates/apps/rusty_bbp_host/docs/proving-run.md` |
| 40f162a8ad | rusty_bbp_host proving run: absolute config paths, MCP-only roles lose built-in tools, structured harness logs | `crates/apps/rusty_bbp_host/docs/proving-run.md`, `crates/apps/rusty_bbp_host/docs/proving/agents.json`, `crates/apps/rusty_bbp_host/src/moderator.rs` |
| cf6a15f66f | rusty_bbp: move the design record into the crate's docs/ | `crates/libs/protocol/rusty_bbp/README.md`, `crates/libs/protocol/rusty_bbp/docs/README.md`, `crates/libs/protocol/rusty_bbp/docs/review/orch-core-relationship.md` … |
| 79c0a0ea7e | rusty_bbp: refuse a task log that is not at its canonical path | `crates/libs/protocol/rusty_bbp/src/fs_store.rs`, `crates/libs/protocol/rusty_bbp/tests/durable.rs` |
| 7f0322868b | rusty_bbp_host moderator: fence on restart, task-scoped files, process groups | `crates/apps/rusty_bbp_host/Cargo.toml`, `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/lib.rs` … |
| b9c7aa3784 | rusty_bbp_host: artifact content, replay after turn end, explicit --rev | `crates/apps/rusty_bbp_host/README.md`, `crates/apps/rusty_bbp_host/src/bin/bbp.rs`, `crates/apps/rusty_bbp_host/src/human.rs` … |
| 3d865711d4 | rusty_bbp: file store serialises processes with a directory lock | `crates/libs/protocol/rusty_bbp/README.md`, `crates/libs/protocol/rusty_bbp/src/fs_store.rs`, `crates/libs/protocol/rusty_bbp/tests/durable.rs` |
| 54a9b5af92 | rusty_bbp_host: address Codex review on the runner | `crates/apps/rusty_bbp_host/src/admin.rs`, `crates/apps/rusty_bbp_host/src/profiles.rs`, `crates/apps/rusty_bbp_host/src/runner.rs` … |
| 61dfd8991e | rusty_bbp: driver boundary checks, payload derives from stored bytes | `crates/libs/protocol/rusty_bbp/README.md`, `crates/libs/protocol/rusty_bbp/src/codec.rs`, `crates/libs/protocol/rusty_bbp/src/command.rs` … |
| 42627d5496 | feat(rusty_orch): enforce StopRule::BestEffort in the adapters | `crates/apps/rusty_orch/ARCHITECTURE.md`, `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/README.md` … |
| 3f08342130 | fix(rusty_orch): complete persistence boundary coverage | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/orch-store/README.md` … |
| c74600f627 | fix(rusty_orch): restore main's import order in the merged nexus test; harden the subprocess test | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/crates/rusty_orch/tests/subprocess_resume.rs` |
| b544c60f30 | fix(rusty_orch): harden persistence recovery contracts | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/orch-store/README.md` … |
| 8f6b0a23d0 | fix(orch-store): drop trait imports that 1.98 clippy flags as unused | `crates/apps/rusty_orch/crates/orch-store/src/lib.rs` |
| 38e6d2e18b | feat(rusty_orch): persist and resume a goal with --state over the multimodal_db engine | `crates/apps/rusty_orch/ARCHITECTURE.md`, `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/README.md` … |
| 796c936b55 | fix(rusty_orch): tighten progress-line formatting and add regression coverage | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/rusty_orch/Cargo.toml` … |
| 3bb657dae8 | fix(rusty_orch): drop an unused trait import that 1.98 clippy rejects | `crates/apps/rusty_orch/crates/rusty_orch/src/main.rs` |
| 7499cfabb9 | fix(rusty_orch): review context, clean stdout, stop semantics, routing shape | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/orch-cli/src/prompt.rs` … |
| d067638bd9 | feat(rusty_orch): the command line, `rusty_orch run <goal.json>` | `crates/apps/rusty_orch/ARCHITECTURE.md`, `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/README.md` … |
| 5a9013497e | fix(orch): preserve classified runner compatibility | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/orch-codex/examples/research_review.rs` … |
| 9a45915ddb | fix(orch): keep missing login recoverable | `crates/apps/rusty_orch/ARCHITECTURE.md`, `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md` … |
| 2ae76e384a | fix(orch): fail unsupported roles without retrying | `crates/apps/rusty_orch/ARCHITECTURE.md`, `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md` … |
| 7b8e5af58e | fix(orch-cli): resolve superseded entry refs | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/orch-cli/src/prompt.rs` … |
| 480cdb44b7 | test(orch-ollama): assert the real-binary reply carries a per-run nonce | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/orch-ollama/tests/real_ollama.rs` |
| dd5a2b4cdb | orch-cli: resumed cards see their own question and its answer (#448) | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/orch-cli/src/prompt.rs` … |
| 0ddb2e3f53 | orch-codex: strict output schema and --ignore-rules (review fixes) | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/orch-codex/README.md` … |
| 093eb0abeb | rusty_orch: add orch-codex, Agent::Codex over codex exec, read-only | `crates/apps/rusty_orch/ARCHITECTURE.md`, `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/README.md` … |
| 13460dc0fb | rusty_orch: no self-approval; the ollama deadline is a hard bound | `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md`, `crates/apps/rusty_orch/crates/orch-core/src/board.rs` … |
| 496737b4bd | rusty_orch: agents never settle decisions alone (ADR-0005) | `crates/apps/rusty_orch/ARCHITECTURE.md`, `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/RELEASE_NOTES.md` … |
| 6535e4cf9d | rusty_orch: add orch-ollama, Agent::Local over the Ollama CLI | `crates/apps/rusty_orch/ARCHITECTURE.md`, `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/README.md` … |
| 47e69ddd58 | orch-dispatch: caller-owned Ledger, atomic board writes, retry exhaustion | `crates/apps/rusty_orch/ARCHITECTURE.md`, `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/README.md` … |
| 49c007a188 | rusty_orch: add orch-dispatch, the in-memory dispatcher over orch-core | `crates/apps/rusty_orch/ARCHITECTURE.md`, `crates/apps/rusty_orch/CHANGELOG.md`, `crates/apps/rusty_orch/README.md` … |
| 1b1cf51dc0 | rusty_orch: draft ADR-0003 on dispatcher reuse boundary | `crates/apps/rusty_orch/docs/adr/0003-dispatcher-reuse-boundary.md` |

Not used for classes: #781's commits (76ed54c, 9c3200a, 991fe6d, b9a8e5a, 083287b,
59265b3) answer a later review round of held-out #763, so no class rests on them. The
`impact.py` and `inspect.rs` fixes (Windows Cargo metadata paths) are repository
intelligence, outside every axis.

## Defect classes

1. **A metric reads the wrong event.** A measurement's cutoff, base or key comes from a
   neighbouring event or path instead of the one its definition names: attribution
   keyed by storage position instead of the approval event, overlap keyed by the new
   path instead of the old, a stale verdict judged by what was appended instead of what
   a read actually returned, shared effects of overlapping edits compared by hunk
   ranges. (14b3327, 6af00af, 794b80c)
2. **Unknown is read as zero.** An error result, an unnumbered result, a `/proc` entry
   that cannot be read or a turn-log listing that fails counts as complete, absent or
   "no harness ran". Only the specific not-found errors mean gone; everything else
   fails closed. (11933ee, d55f4b1, 3d0eb83)
3. **A budget or gate checked on one path only.** A limit charged where the common path
   runs but not where a rejection, a deadline or a consultation return mints the same
   thing; a gate any approver can open; an agent approving its own decision; a role
   woken for an action it cannot take. (8a98b99, cc0ffc4, 1198e62, 13460dc, ce2550b)
4. **Stale authority acts.** A second moderator relaunches a live turn, a released slot
   keeps its old owner's paths, equal ids in two stores are one task, a recovered
   execution is written without compare-and-set, a log off its canonical path loads,
   two writers both pass a revision check. (7f03228, d3d1bb1, 0be612a, 69857fa,
   79c0a0e, 3d86571, b9c7aa3)
5. **A process outlives its authority.** A harness starts before the process that
   holds its slot, a reaped or recycled pid is signalled, a drain returns early and
   drops harnesses unkilled, a silent harness delays a deadline to the next poll,
   cleanup is assumed from a free lock, a child escapes its group, a long-lived
   process crosses a turn boundary. (2e691df, 80f6a99, 3d067ac, 90da69a, 93fce85,
   62c6522, 13460dc, f565ddd, 9c7f23a, c4ce8b3)
6. **Confinement wider than its grant.** Unix or abstract sockets reach a local daemon,
   a datagram socketpair reconnects outside, a work root another user can write, a
   scratch path that is a symlink, the parent's credentials in a child's environment,
   a claim path aliased by `..` or a symlink. (ff43695, 43fb1de, 0039903, 0529f11,
   183411d, 0be612a)
7. **Replay or co-edit drift.** A log that an older build replays under different rules,
   a refused command that still appends, a frozen input rewritable under a live task,
   a payload not derived from its stored bytes, a CRDT law checked in one grouping, a
   caller panic leaving the structure torn, a co-edit check coarser than the decision
   rule. (1198e62, b032b7b, 54a9b5a, 61dfd89, 29a59e5, 82e6427, 4ce0676)

## Oracles

- **The 2b criteria and run inputs**: `docs/bbp-concurrency-design.md` "2b gate
  criteria" (definitions, noise gate, failed pairs, grading order, decision table) and
  `docs/bbp/2b-run/`, `docs/bbp/2b-criteria.json`. The first oracle for any measurement.
- **The BBP spec**: `crates/libs/protocol/rusty_bbp/docs/spec/bbp-v0.5-implementation-spec.md`
  (R-rules, invocation context §108-110, transition table, log schema) and its review
  record under `docs/review/`.
- **Claude Code's stream-json output** (captured fixtures under
  `crates/apps/rusty_bbp_host/tests/fixtures/stream-json/`) and the provider launcher
  doc, for what a harness actually prints.
- **Linux**: Landlock ABI levels, seccomp, pidfd, `O_PATH`, process groups; man pages
  and the kernel's Landlock docs.
- **Method**: paired designs and blinding (CONSORT for the vocabulary), small-sample
  caution (a screen, not an estimate), pre-registration.

## Settled decisions

- Orch ADRs `crates/apps/rusty_orch/docs/adr/`: 0005 (agents never settle decisions
  alone), 0011 (best-effort stop), 0013 (BBP task host), 0017 (permission leases, when
  present), 0018 (fair admission).
- `docs/bbp-concurrency-design.md` Decisions (owner, 2026-10-13) and the 2b criteria;
  `docs/bbp-event-based-plan.md` (one step per PR, decision rules fixed before a
  harness run).
- `crates/apps/rusty_bbp_host/README.md` and `docs/proving-run.md`.
- AGENTS.md/CONTRIBUTING.md: change fragment per PR, agents never merge.
