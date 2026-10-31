---
name: expert-generator
description: >-
  Turns an interview answer ("summarize this project in five words; what
  expert would we recruit?") into a grounded expert PR reviewer under
  experts/, then benches it. Maps the answer's traits to review axes,
  mines the target repo's own review-fix commits for defect classes (held-out
  bench PRs removed), fills the scaffold from python3 -m orchestrator
  new/mine/codex/check, builds bench cases, grades runs against answer keys
  and opens a PR with the report. Also regenerates an existing expert as a
  variant that lands only if it beats the current prompt on the same cases.
  Use when asked to create, generate, regenerate or bench an expert reviewer,
  to "make an agent" or "recruit an expert" for a repo or domain, or when
  someone pastes a five-words-and-an-expert answer.
metadata:
  version: 1.2.0
compatibility: >-
  Run from an Expert-Agents checkout. Needs python3, git, a local clone of
  the target repo, and gh or the GitHub MCP tools. Benching also needs Node
  and a signed-in Claude Code login. The wrap-up retro uses the skill-retro
  skill from baileyrd/skill_pack when installed.
---

# Expert generator

Input: the person's answer to *"If you had to summarize this project in 5 words what
would they be? And if we wanted to recruit an expert for this domain, what would we look
for?"*, the target repo and a local clone of it. Output: `experts/<name>/` with
`interview.md`, `grounding.md`, `prompt.md`, `codex.md` and `expert.toml`, bench cases,
a graded bench run, and a PR.

The answer gives the persona and the axes. The defect classes come from the target
repo's own review fixes, never from general expertise: a reviewer grounded in what this
repo's reviewers actually caught finds this repo's bugs, while a generic one restates
textbook advice. Read `README.md`,
`orchestrator/README.md`, `bench/README.md`, `bench/runner/README.md` and one existing
expert (`experts/mux/prompt.md`) before starting.

Copy this checklist and tick it off; each step says what "done" means.

```
- [ ] 1. interview.md written, Profile has 2–5 axes
- [ ] 2. 3+ bench cases, held-out PRs listed
- [ ] 3. scaffold + grounding.md (defect classes, oracles, settled decisions)
- [ ] 4. prompt.md + codex.md; check reports 0 problems; unittest green
- [ ] 5. bench runs (or paste-ready handoff)
- [ ] 6. graded runs scored, report written
- [ ] 7. PR open, review requested
- [ ] 8. wrap-up retro reported
```

## 1. Record the interview

Ask for the name (kebab-case; the subagent becomes `<name>-expert`), the target repo and
the clone path if the request does not give them. The answer itself is the one input
you cannot reconstruct: to regenerate an expert, use its `interview.md` if it has one,
otherwise ask for the answer. Never write one from the existing prompt, since axes
inferred from the prompt only reproduce it. Write `interview.md`: the question,
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
commits, or their findings' specifics, because a prompt that has seen the answer key
scores recall it would not have on a new PR. That includes a finding restated as a
general rule: `check` catches PR numbers and commits only, so reread the finished prompt
against each `answer_key.toml` finding.

If the review threads cannot be read, cite the fix commit whose message quotes the
review, mark the severity ungraded, and say so in the report.

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

Fill every `TODO(generator)`. Keep Authority, Pin the review, Return and the frontmatter
after `tools` (effort, isolation, the read-only guard) as generated.

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
KB. Create the branch once (`git switch -c expert/<name>`), then:

```
python3 -m orchestrator codex <expert dir>      # codex.md from prompt.md
python3 -m orchestrator check <expert dir> --target <clone>   # must report 0 problems
git add experts/<name> bench/cases/<name> && git commit
python3 -m unittest
```

Commit before `unittest`: the runner's tests refuse uncommitted changes under `experts/`.
If `check` reports problems or a test fails, fix the prompt and run them again;
bench only on a clean `check`, since a leaked held-out commit invalidates every run.
`check` refuses leftover TODOs, sections that differ from `expert.toml`, a Codex
prompt without its placeholders, and any held-out PR number or commit in either prompt
(`--target` is required once the expert has cases, so every commit of a held-out PR is checked).

## 5. Bench

Runs use the person's signed-in Claude Code login, never an API key, on a machine with
the target clone, Node and the sandbox (`bench/runner/README.md`).

For a variant, first make sure the current prompt has runs on every case the variant
will run: bench it, as committed on `main`, on any case its latest report lacks, so the
comparison is like for like. Do this before installing the variant: the runner reads the
prompt from the worktree's `experts/`, and `--variant` is only a label. Then, in a
separate worktree on a local branch that is never pushed, commit the variant's
`prompt.md`, `codex.md` and `expert.toml` over the expert's (`check` fails if the
headings differ from the manifest) and bench it there:

```
git worktree add -b bench-local/<name>-<variant> <wt> HEAD   # never pushed
cp <variant files> <wt>/experts/<name>/ && git -C <wt> commit -am "bench: install <variant>"
# run prepare below from <wt>; extract into this checkout's reports/
```

```
npm ci --prefix bench/runner
python3 -m bench.runner prepare bench/cases/<name>/<case> --target <clone> \
    --out <work>/<case> --variant <variant> --repeat 5 --model <model>
# run the printed eval command; then:
python3 -m bench.runner extract <work>/<case>/grade/results.json \
    --out reports/<name>/<date>-<variant>/
```

Use `--repeat 5` so results compare with the baselines. `--model` is required and
recorded with every result; pass the same model for the current prompt and the variant,
because reports compare only when their models match.

If this session is not on such a machine, run `prepare` on each case anyway (it needs no
login and proves the case SHAs), then stop: push, open the PR as a draft whose body holds
the whole sequence above as a paste-ready prompt, and mark it ready once the report lands.
For a variant that means the current prompt's missing runs first, then the worktree
install, then the variant's runs; `--variant` alone would bench the current prompt under
the variant's name. Commit the variant's files under
`reports/<name>/<date>-<variant>/variant/` on the pushed branch so that machine has them.

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
`prompt.md`, `codex.md` and `expert.toml` only if it beats the current prompt, on the
same cases, without regressing any case; otherwise land only the report. Open the PR, request review from
`dottiusmaximus`, then do step 8. Agents never merge.

## 8. Wrap-up retro

After step 7 (or the step 5 handoff, if the bench ran elsewhere), run the `skill-retro`
skill on this skill, grounded in this run: did the Profile's trait-to-axis mapping hold up
once the defect classes were mined, did `mine` surface enough review fixes or did the
classes rest on one commit each, did `check` catch something the steps should have
prevented, and did the bench handoff or grading need a command these steps lack. This
runs unattended and only reports; apply what it finds in a separate, approved PR, never
in the expert's PR. `skill-retro` lives in
[baileyrd/skill_pack](https://github.com/baileyrd/skill_pack/tree/main/meta/skill-retro);
if it is not installed, answer the same questions in a short `## Retro` section of the
PR body or handoff instead.
