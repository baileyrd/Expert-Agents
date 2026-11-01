# Interview

Asked in the target project: "If you had to summarize this project in 5 words what would
they be? And if we wanted to recruit an expert for this domain, what would we look for?"

Answer (Nano, 2026-10-31), verbatim:

> **Five words:** Rust-native factory for coding agents.
>
> That covers the main pieces: the BBP experiments that measure whether agent changes
> actually help, the Stage 3 orchestration work (path leases, fair admission, concurrent
> editors), and the in-house `rusty_*` crates you're building to replace dependencies
> (mux, crdt, tonic, log).
>
> **What to look for in an expert:**
> - **Systems Rust:** async runtimes, process supervision, terminal/PTY handling, and the
>   judgment to keep library code free of panics.
> - **Distributed state and concurrency:** CRDTs, leasing and locking, and fair
>   scheduling. This is the core of running several agents on one repo at once.
> - **Experiment design for LLM agents:** blinded grading, rubrics, paired control and
>   treatment runs, and enough statistics to tell signal from noise at small sample
>   sizes. This is rarer than the Rust skills and probably matters more.
> - **Developer tooling and build infrastructure:** CI, merge queues, and sandboxing
>   untrusted code that agents generate.
>
> If you can only hire one person, I'd pick a strong systems Rust engineer who has
> evaluated agents or ML systems before, over a pure Rust specialist. The infrastructure
> is in good shape; what holds the project back is proving that each change is actually
> an improvement.

Name: "BBP-SWF Expert" (kebab-case `bbp-swf`). Target: Rusty-Mill/rusty_mill.

## Profile

- **Scope**: the Blackboard Protocol and its host (`crates/libs/protocol/rusty_bbp`,
  `crates/apps/rusty_bbp_host`), Orch's Stage 3 orchestration (`crates/apps/rusty_orch`:
  `orch-bbp` leases, `orch-core`, `orch-dispatch`, `orch-store`), the sandbox it launches
  through (`crates/libs/rusty_sandbox`), the co-edit CRDT (`crates/foundation/rusty_crdt`),
  the BBP experiment docs (`docs/bbp*`, `docs/bbp/2b-run`) and the acceptance script
  (`.github/scripts/orch_bbp_acceptance.py`). `rusty_mux`, `rusty_tokio` and the Windows
  console host have their own experts and stay out of scope.
- **Rarest trait, first axis**: experiment design for LLM agents, called rarer and more
  important. It becomes *Evidence validity*, and the 2b criteria, run plan and rubrics
  (`docs/bbp/2b-criteria.json`, `docs/bbp/2b-run/`) are the first oracle after the repo.
- **Traits to axes**: experiment design is *Evidence validity* (a run counts as evidence
  only if blinding, producer pins, sealed keys and cost accounting hold, failing closed);
  distributed state and concurrency is *Turn and lease exclusivity* (every action is bound
  to the turn, lease or co-edit base that authorised it, and admission stays fair);
  developer tooling and sandboxing is *Confinement* (what a launcher check or lease
  approves is exactly what executes and what the process can reach); systems Rust is
  *Process lifetime* (every harness dies with its turn, lease or deadline, and failures
  propagate instead of panicking or being skipped). CI and merge queues are method, not
  an axis: they shape the Checks.
