# Interview

Asked in the target project: "If you had to summarize this project in 5 words what would
they be? And if we wanted to recruit an expert for this domain, what would we look for?"

Answer (Nano, 2026-10-31), verbatim:

> **Five words:** "Reverse-engineering competitor products into roadmaps." That's my read from the threads so far: research-and-roadmap work on SAP LeanIX, Aha, Functionly and Jibility, plus the `reverse` repo. I haven't seen the repo contents, so correct me if the project is really something else.
>
> **Expert to recruit:**
> - **Core:** a product strategist or product-ops lead who has built roadmaps for B2B SaaS. They should be able to turn a competitor teardown into prioritized features.
> - **Domain:** enterprise architecture and portfolio management (LeanIX, Aha, Functionly are all in that space). Look for TOGAF or ArchiMate familiarity, and experience with CMDB, application-portfolio and capability-mapping tools.
> - **Technical:** an engineer who can infer a product's data model and APIs from its public behavior, docs and integrations. Look for Rust or Python and a graph or metamodel design background.
> - **Judgment:** someone who knows where the legal and ethical lines of competitive analysis sit, such as ToS, clean-room rules and no copying.
>
> If the goal is the tool itself rather than the research, tell me and I'll weight the engineering profile higher.
>
> baileyrd/reverse
>
> reverse expert

## Checked against the repo (2026-10-31)

The read holds with one widening. `main` has one target, `targets/duckdb-cli-1.4.1`: a
binary teardown of the DuckDB CLI with Python decoders for its storage format, validated
by fixture databases. Unmerged branches add SaaS and software teardowns: SAP LeanIX
(teardown plus build plan), Functionly (public-site spec and capture tools) and
promptfoo 0.125.1 (spec plus re-implementation plan, PR #1). So "products" covers
binaries and open-source tools as well as SaaS, and the deliverable is a spec of
evidenced claims that a plan or a decoder builds on.

## Profile

- **Scope**: `targets/` (each target's README, spec or findings doc, plan, tools,
  tests and fixtures), shared `tools/`, `templates/target/` and the root `README.md`.
- **Rarest trait, first axis**: the answer names none as rarest, so the first trait,
  turning a teardown into prioritised features, leads. Its oracle after the repo is the
  target's own spec: every plan item must trace to a numbered claim.
- **Traits to axes**:
  - Core (teardown to prioritised roadmap) is *Plan from evidence*: each plan or roadmap
    item cites the claims it rests on, and its priority and build-or-not call follow
    from stated evidence, not from marketing copy.
  - Technical (infer the data model and APIs from public behaviour) is *Claim
    evidence*: every claim is pinned to a version or capture date, cites an address,
    path, URL or probe, is labelled observed or inferred honestly, and a decoder or
    test validates it where it can.
  - Domain (enterprise architecture and portfolio tools) is *Model fidelity*: the
    recovered entities, relations and vocabulary match the target's own (fact sheet
    types, capabilities, ArchiMate or TOGAF terms where the vendor uses them) and are
    not mapped onto ours by accident.
  - Judgment (legal and ethical lines) is *Clean room*: no target binary, decompiler
    output, copied text, proprietary asset or credential enters the repo, capture stays
    within public access and the site's terms, and a re-implementation plan builds from
    the spec only.
  - "Rust or Python" is method, not an axis: it shapes the Tests step (stdlib
    `unittest` per target, decoders checked against ground-truth fixtures).
