# Runner

Runs one prompt variant of an expert N times on a case with
[promptfoo](https://www.promptfoo.dev/) and its Claude Agent SDK provider, then splits
the results into one report per run for grading.

```
npm ci --prefix bench/runner        # once: promptfoo and the Agent SDK, pinned
python3 -m bench.runner prepare bench/cases/mux/1251-r1 \
    --target ~/src/rusty_mill --out /tmp/bench-1251 --variant baseline --repeat 5
# 1. run the printed review command, signed in to Claude Code
python3 -m bench.runner extract /tmp/bench-1251/results.json \
    --out reports/mux/<date>-<variant>/
# 2. run the printed rubric command for a first-pass recall per key finding
```

## Blind by construction

- **No key during the review.** The review config holds the task and the prompt,
  nothing from the answer key. The rubric config is written by `extract`, after the
  reviews are done, from their saved reports.
- **A checkout with nothing later.** `prepare` builds a new repository fetched by SHA
  from your clone: the head and its history, no later commits. Its `origin` is itself,
  holding only `refs/pull/<pr>/head`, so the prompts' pinning step works.
- **A sandbox the reviewer cannot leave.** Bash runs in the Claude Code sandbox or not
  at all (`allowUnsandboxedCommands: false`), and the run fails if no sandbox is
  available (`failIfUnavailable: true`). This repo and the target clone are denied to
  Bash (`filesystem.denyRead`) and to Read, Grep and Glob (`Read(//…)` deny rules);
  writes go only to the checkout and a scratch directory. `--out` inside either is
  refused.
- **No network by default.** WebFetch and WebSearch are off and the sandbox allows no
  domains. `--allow-domain` opens one (repeatable), for example the crates.io hosts if
  `cargo` has no cached registry. Opening `github.com` lets the reviewer read the live
  review, so a run that does is not blind; say so in its report.

The Linux sandbox needs `bubblewrap` and `socat` installed.

## The rest

- **No API key.** The reviewer and the default grader use your signed-in Claude Code
  login (`apiKeyRequired: false`), so runs count against your plan's usage. Unset
  `ANTHROPIC_API_KEY` first: if it is set, it is used instead.
- **Frozen prompt.** The prompt is the expert's Claude subagent `prompt.md` body as
  committed; `prepare` refuses uncommitted changes under `experts/` and records the
  commit as `prompt_sha`.
- **Ablations.** `--drop "<heading>"` removes one declared section (repeatable).
  Sections marked `required` in `expert.toml` are refused.
- **Re-reviews.** A case with `prior` passes the prior round's key as the findings to
  re-check.
- **N runs.** `--repeat` (default 3) with `--no-cache`, so repeats are real runs.
- **Extract.** Writes `<case>-<n>-report.md` per run and a graded-run skeleton
  `<case>-<n>.toml` per successful run, and refuses before writing anything if any of
  those files exist. A failed run's report names its error, gets no skeleton, and makes
  the exit status 1.
- **Recall, first pass.** The rubric config grades each saved report with one
  `llm-rubric` per key finding (1 caught, 0.5 partial, 0 missed), using `--grader`.
  The score of record comes from the graded-run files: a blind verifier triages every
  finding ([bench/README.md](../README.md)) and [the scorer](../scorer/) reads them.
