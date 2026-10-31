# Bench

A frozen test set of past PR heads with answer keys, run by
[Eval-Kit](https://github.com/baileyrd/Eval-Kit), and a scorer for the graded runs.

## A case

`cases/<expert>/<pr>-r<round>/` is one reviewed head of one PR.

`pr.toml` pins it:

```toml
expert = "mux"
repo = "Rusty-Mill/rusty_mill"
pr = 1251
title = "..."
round = 1                 # which review round of the PR this head got
base = "<40-char SHA>"
head = "<40-char SHA>"    # the head the human review was written against
prior = "1251-r1"         # re-reviews only: the case whose key is passed as prior findings
```

`answer_key.toml` lists the blocking findings the human review raised at that head,
each with the commit that fixed it:

```toml
review = "<link to the review or review comment>"

[[finding]]
id = "K1"
severity = "P2"           # the human review's grade; "ungraded" if it said only "blocking"
title = "..."
files = ["path/at/head"]
fix = "<40-char SHA>"
summary = "the failure scenario, as the review gave it"
caught_if = "what a reviewer finding must name to count as caught"
```

A key holds only what the human review raised. Findings beyond it are judged one at a
time when a run is graded, never added to the key after the fact.

## Running a trial

Each expert's cases become an Eval-Kit suite under `suites/<expert>/`, generated and
committed like `codex.md`, and checked by the tests to match the cases and prompt:

```
python3 -m bench suite <expert> [--model M] [--allow-domain D]...  # writes suite.toml, baseline.prompt.toml
export EXPERT_AGENTS=$PWD TARGET=~/src/<target clone>             # read by the suite at run time
unset ANTHROPIC_API_KEY
python3 -m evalkit run   bench/suites/<expert>/suite.toml --yes   # 5 runs per case, then the judge
python3 -m evalkit score bench/suites/<expert>/suite.toml --by-check
python3 -m bench extract <expert> --out reports/<expert>/<date>-baseline/
```

**Blind by construction.**

- **A checkout with nothing later.** Each run starts from a new repository fetched by
  SHA from `$TARGET`: the head, its history and the base, no later commits. Its `origin`
  is itself, holding only `refs/pull/<pr>/head`, so the prompts' pinning step works.
- **No key the review can reach.** The run's input names the PR and its SHAs, and for a
  re-review the prior round's findings by id and title. Judge checks hold the key; the
  judge runs only after every review, and the suite and its store live in this repo,
  which the run cannot read.
- **A sandbox the reviewer cannot leave.** The suite's settings make Bash run in Claude
  Code's sandbox or not at all, and deny `$EXPERT_AGENTS`, `$TARGET` and `~/.claude` to
  Bash and to the Read tool. Edit, Write and the web tools are denied. An unset variable
  fails the run instead of denying nothing.
- **No network by default.** `--allow-domain` opens a host to sandboxed Bash, for
  example the crates.io hosts for cargo. Opening `github.com` lets Bash read the live
  review, so a run that does is not blind; say so in its report.

**What a run records.** Eval-Kit keys each run by the prompt, model, case and
settings, and stores it in `suites/<expert>/.evalkit/` (ignored by git). Re-running
`evalkit run` only fills what is missing, so a changed prompt re-runs and an unchanged
one costs nothing. `--model` is recorded in the suite; reports compare only when their
models match. `bench extract` takes each run by the key the current suite gives it
(computed by Eval-Kit, so it needs the same Eval-Kit on `PYTHONPATH`), refuses if any run
lacks a record under its key, and never relabels an older prompt's or model's run. It
refuses uncommitted changes under `experts/` and `bench/suites/` and records this repo's
commit as `prompt_sha`.

**Recall, first pass.** Each key finding is one judge check, so `score --by-check`
gives a per-finding hit rate. The score of record comes from the graded runs below,
which also give precision and severity.

**Variants and ablations.** A variant is the expert's prompt changed on a branch:
`evalkit gate bench/suites/<expert>/suite.toml baseline@main baseline --max-drop 0.1`
runs both sides and compares them. `evalkit ablate bench/suites/<expert>/suite.toml
baseline` drops one section at a time (the prompt spec has one section per heading,
named by its role in `expert.toml`).

**Not carried over.** Oracle snapshots (`--oracle` in the old runner): a run reads
no reference source, so citations to one are marked unverified. The baselines ran
without oracles too.

## A graded run

A grader (a person, or a verifier agent that sees only the claim, the cited lines and
the docs, never the reviewer's reasoning) turns a reviewer's report into a run file:

```toml
case = "mux/1251-r1"
variant = "baseline"
prompt_sha = "<40-char SHA of this repo>"
run = 1

[[finding]]
id = "F1"
severity = "P2"
title = "..."
match = "K1"              # a key finding it covers...
quality = "caught"        # ...caught or partial

[[finding]]
id = "F2"
severity = "P1"
title = "..."
triage = "real"           # ...or not in the key: real, fixed_since, design_question, false_positive
reason = "executed on main at <sha>: ..."
```

- `real`: confirmed by executing it, or by a source trace a second reader agrees with.
- `fixed_since`: real at the head, gone on `main` since.
- `design_question`: the behaviour is documented as intended.
- `false_positive`: the trace is wrong.

Graded runs live beside their report: `reports/<expert>/<date>-<variant>/<case>-<n>.toml`.

## Scoring

```
python3 -m bench.scorer bench/cases/mux/1251-r1 reports/mux/<date>-<variant>/1251-r1-*.toml
```

- **Recall**: each key finding scores its best match (caught 1, partial 0.5, missed 0),
  averaged over the key. The per-finding hit rate across runs shows which findings a
  variant reliably catches.
- **Precision**: true ÷ (true + false positive). Key matches, `real` and `fixed_since`
  are true, since each was real at the reviewed head; design questions count on
  neither side.
- **Severity**: each caught key finding compares the reviewer's grade with the key's,
  as agree, over or under.
