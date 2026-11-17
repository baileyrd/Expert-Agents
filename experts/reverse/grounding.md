# Grounding: reverse

Target: baileyrd/reverse, scope `targets/`, `tools/`, `templates/target/`, `README.md`.

## Review-fix commits

```
python3 -m orchestrator mine --target ../reverse -- targets tools templates README.md
```

found **0 commits that answer a review**. On 2026-10-31 the repo had 3 commits on
`main` (scaffold, the DuckDB fold-in, a README update) and one open PR (#1, promptfoo,
no reviews). The DuckDB work was flattened in from baileyrd/RE_Dudb at `76de70d`, whose
history is outside this project. No PR is held out, because none has review rounds.

So the defect classes below are **provisional**. They rest on the repo's own rules
(root `README.md`, `templates/target/`) and on gaps visible in the work on `main` and
the three teardown branches, not on what a reviewer caught. Replace or confirm each one
once three reviewed PRs exist; those PRs become the first bench cases.

## Defect classes (provisional)

**Plan from evidence** (first trait)
1. **Plan item without a claim.** A plan or roadmap entry, or a priority, cites no
   section of the spec, or cites one that does not say it. promptfoo `plan.md` cites
   sections for scope; LeanIX keeps its roadmap and evidence register (E1–E16) in an
   HTML page and an external artifact link, not in a spec the plan can cite.
2. **Build call without a trigger.** "Build it" with no stated condition or cost; the
   promptfoo plan's "Should we build it?" is the pattern to hold others to.

**Claim evidence**
3. **Claim with no evidence or no validation.** The README rule is
   `§n.m — claim · evidence · validation`; promptfoo leaves validation `—` on most
   source-read claims, Functionly omits the validation column.
4. **Inference labelled as observation.** `obs`/`inf` (Functionly) and
   `obs`/`src`/`doc` (promptfoo) labels exist; a claim derived from another must not
   carry `obs`.
5. **Unpinned claim.** Each target pins version, sha256 or capture date and source
   commit (DuckDB sha256; promptfoo `b3cea642`; SaaS capture dates). A claim from a
   later capture or a different version without saying so.
6. **Decoder and spec disagree; fixture not reproducible.** DuckDB decoders are checked
   against `PRAGMA storage_info` ground truth (`*.storage.json`) and each fixture has a
   `.sql` or `.sh` that rebuilds it. A new fixture without its recipe, or a test that
   only checks the decoder against itself.
7. **Silent skip.** Tests skip when optional packages or `DUCKDB_CLI` are missing
   (2 of 121 skipped on a plain Python); a claim validated only by a skipped test is
   unvalidated.
8. **Doc layout drift.** The README and template say `spec.md`; the DuckDB target uses
   `docs/duckdb-cli-v1.4.1.md` and its README says `python -m pytest tests` while the
   root README says stdlib `unittest`. New targets must not widen the drift.

**Model fidelity**
9. **Vocabulary mapped, not recovered.** Entities and relations named in our terms, or
   a vendor's meta-model version mixed with another's (LeanIX meta model v4, 12 fact
   sheet types, `rel<From>To<To>`).

**Clean room**
10. **Wall breach.** Raw decompiler output belongs in gitignored `notes/`; target
    binaries in gitignored `binary/`. A spec that pastes pseudocode, a plan that reads
    source instead of the spec, or a re-implementation that copies code (promptfoo is
    MIT and still declined) is a finding.
11. **Capture outside public access or terms.** Functionly and LeanIX state "no login,
    no API calls beyond what the page loads". Logged-in capture, trial abuse, scraping
    against robots or terms, or committed screenshots of proprietary UI.
12. **Secrets and personal data.** Fixture keys are synthetic (`encryption_key
    'secret'`); a real key, token, tenant id or employee name from a demo must not land.

## Oracles

- **This repo**: each target's spec or findings doc, decoders, tests and fixtures;
  `templates/target/` for the expected shape.
- **Binary targets**: the target binary itself at the pinned sha256 (Ghidra 12.1.4 via
  REA, `readelf`, `nm`, objdump); running the pinned CLI on a fixture (`DUCKDB_CLI`).
  The vendor's source (DuckDB is MIT, tag v1.4.1) may check a claim, but the spec must
  not cite it: the DuckDB doc states "nothing is taken from DuckDB's source tree".
- **Open-source targets**: the source at the pinned commit, its docs and a probe run
  (`fixtures/probe/`).
- **SaaS targets**: the public pages and docs at the capture date, public listings
  (G-Cloud), the vendor's published meta-model and API docs; ArchiMate 3.2 and TOGAF
  for vocabulary.
- **Executed**: rerunning a target's tests and decoders on its fixtures.

## Settled decisions

Root `README.md` Rules: no target binaries committed; spec is claims plus evidence;
observer/implementer wall with `spec.md` as the only handoff; Ghidra projects, dumps
and large artifacts stay out. Each target README records its identity table and scope
(for example "public pages and the public demo only"). There is no AGENTS.md,
CONTRIBUTING.md or change-fragment process in this repo.
