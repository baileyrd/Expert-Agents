---
name: expert-generator
description: Turn an interview answer ("summarize this project in five words; what expert would we recruit?") into a grounded expert PR reviewer in experts/<name>/, then bench it. Use when asked to create, generate or regenerate an expert reviewer from such an answer.
---

# Expert generator

Input: the person's answer to *"If you had to summarize this project in 5 words what
would they be? And if we wanted to recruit an expert for this domain, what would we look
for?"*, the target repo and a local clone of it. Output: `experts/<name>/` with
`interview.md`, `grounding.md`, `prompt.md`, `codex.md` and `expert.toml`, bench cases,
a graded bench run, and a PR.

The answer gives the persona and the axes. The defect classes come from the target
repo's own review fixes, never from general expertise. Read `README.md`,
`orchestrator/README.md`, `bench/README.md`, `bench/runner/README.md` and one existing
expert (`experts/mux/prompt.md`) before starting.

## 1. Record the interview

Ask for the name (kebab-case; the subagent becomes `<name>-expert`), the target repo and
the clone path if the request does not give them. Write `interview.md`: the question,
the answer verbatim, then a **Profile**:

- **Scope**: the paths in the target that make up the domain.
- **Rarest trait**: the one the answer calls rarest, else the first. It becomes the
  first axis and the first oracle after the repo itself.
- **Traits to axes**: each trait maps to a property the specialist protects (2 to 5
  axes). A trait about method (testing, fuzzing, benchmarks) is not an axis: it shapes
  the Tests step and the evidence list.

If `experts/<name>/` exists, this is a **variant**: write everything under
`--out <scratch>/<name>` instead, and replace the expert's files only after the bench
shows it beats the current prompt (step 7).

## 2. Cases before prompt

Pick three or more past PRs in scope that had review rounds, and build a case per
reviewed head as `bench/README.md` says (`pr.toml`, `answer_key.toml`, every key finding
citing the review and its fix commit). Reuse existing cases under
`bench/cases/<name>/`. These PRs are held out: the prompt must never name them, their
commits, or their findings' specifics.

## 3. Scaffold and ground

```
python3 -m orchestrator new <name> --target-repo OWNER/REPO --axes N [--out DIR]
python3 -m orchestrator mine --target <clone> --cases bench/cases/<name> -- <scope paths> \
    > <expert dir>/grounding.md
```

`mine` lists the commits that answered a review in scope, with every held-out PR's
commits removed. Read each one's message and diff, and the review threads of their PRs
(`gh api repos/OWNER/REPO/pulls/N/comments`, or the GitHub tools; skip held-out PRs).
From them, list:

- **Defect classes**: what reviewers kept catching, each named in a few words and backed
  by at least one listed commit. Merge near-duplicates; drop one-offs that no class
  covers.
- **Oracles**: the primary sources a specialist checks against: the reference
  implementation (a path to clone, pinned if the repo pins it), its own tests, specs,
  client source, public trackers.
- **Settled decisions**: the docs that decide behaviour (design docs, ADRs, DECISIONS).

## 4. Write prompt.md

Fill every `TODO(generator)`. Keep Authority, Pin the review and Return as generated.

- **Persona**: open with "You are the independent reviewer for", name the component and
  its risk, and say which knowledge is the rare part. The frontmatter `description`
  says when to use it, with the scope paths.
- **What to read**: the target's AGENTS.md, CONTRIBUTING.md, change fragment, and the
  settled-decision docs, with the sections that matter. Say that code differing from a
  decision is a finding, and disagreeing with a decision is a question for the owner.
- **Axes**: one bullet per Profile axis, each a property that must hold, not a topic.
- **Evidence, strongest first**: this repo and its review-fix commits first, then the
  oracles, then executed probes. Note licences that forbid porting.
- **Steps**: Predict (invariants with citations before reading the PR body), Break it
  (one bullet per defect class, each a concrete attack), Compare (docs, scope drift,
  recorded divergences), Tests (a test that is red without the change; run it at base).
  Each step ends with "Done when".
- **Findings**: P0–P2 defined for this domain, grade by impact alone, a trace for every
  finding, validation gaps for what the sources cannot settle.
- **Checks**: the target's real commands for the scope.

Match the existing experts' voice: short declarative sentences, no filler, about 8 to 11
KB. Then:

```
python3 -m orchestrator codex <expert dir>      # codex.md from prompt.md
python3 -m orchestrator check <expert dir> --target <clone>   # must report 0 problems
python3 -m unittest
```

`check` refuses leftover TODOs, sections that differ from `expert.toml`, a Codex
prompt without its placeholders, and any held-out PR number or commit in either prompt
(`--target` checks every commit of a held-out PR, not only its endpoints).

## 5. Bench

Runs use the person's signed-in Claude Code login, never an API key, on a machine with
the target clone, Node and the sandbox (`bench/runner/README.md`). If this session is
not on such a machine, stop here: commit, push, and hand the person the commands below
as a paste-ready prompt.

For a variant, first commit its `prompt.md` and `codex.md` over the expert's on a local
branch that is never pushed: the runner reads the committed `experts/<name>/prompt.md`.

```
npm ci --prefix bench/runner
python3 -m bench.runner prepare bench/cases/<name>/<case> --target <clone> \
    --out <work>/<case> --variant <variant> --repeat 5
# run the printed eval command; then:
python3 -m bench.runner extract <work>/<case>/grade/results.json \
    --out reports/<name>/<date>-<variant>/
```

Use `--repeat 5` so results compare with the baselines.

## 6. Grade and report

Fill each graded-run TOML: every finding gets `match` and `quality` against the key, or
a `triage` with a one-line reason checked against the clone at the case head. Then:

```
python3 -m bench.scorer bench/cases/<name>/<case> reports/<name>/<date>-<variant>/<case>-*.toml
```

Write `reports/<name>/<date>-<variant>.md`: recall, precision and severity per case,
per-finding hit rates, real bugs found outside the keys, run time and token cost, and
whether any run had network access. A real bug goes to the target repo as an issue
after a check on current `main`; anything an attacker could use goes to the owner first.

## 7. Land

A new expert lands with its cases and report. A variant replaces the expert's
`prompt.md` and `codex.md` only if it beats the current baseline without regressing any
case; otherwise land only the report. Open the PR, request review from
`dottiusmaximus`, and stop: agents never merge.
