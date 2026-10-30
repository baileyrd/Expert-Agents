# Runner

Runs one prompt variant of an expert N times on a case with
[promptfoo](https://www.promptfoo.dev/) and its Claude Agent SDK provider, then splits
the results into one report per run for grading.

Needs Node 22.22 or newer (promptfoo's floor; `npm ci` refuses older), and on Linux
`bwrap` and `socat` for the sandbox.

```
npm ci --prefix bench/runner        # once: promptfoo and the Agent SDK, pinned
python3 -m bench.runner prepare mux/1251-r1 \
    --target ~/src/rusty_mill --out /tmp/bench-1251 --variant baseline --repeat 5 \
    --model <model> --oracle mosh=~/src/oracles/mosh
# 1. run the printed review command, signed in to Claude Code
python3 -m bench.runner extract /tmp/bench-1251/grade/results.json \
    --out reports/mux/<date>-<variant>/
# 2. run the printed rubric command for a first-pass recall per key finding
```

`prepare` takes a case id or directory. It links the work directory's `node_modules` to
the pinned one here, because promptfoo loads the Agent SDK from beside the config.

## Blind by construction

- **No key the review can reach.** The review config holds the task and the prompt,
  nothing from the answer key. Results and the rubric config `extract` writes afterwards
  live in the work directory's `grade/`, which every review config denies, so running
  the review again later cannot read them. `extract` refuses results outside a prepared
  `grade/`, and graded runs anywhere but this repo or `grade/`.
- **A checkout with nothing later.** `prepare` builds a new repository fetched by SHA
  from your clone: the head and its history, no later commits. Its `origin` is itself,
  holding only `refs/pull/<pr>/head`, so the prompts' pinning step works.
- **A sandbox the reviewer cannot leave.** Bash runs in the Claude Code sandbox or not
  at all (`allowUnsandboxedCommands: false`), and the run fails if no sandbox is
  available (`failIfUnavailable: true`). This repo, the target clone and `grade/` are
  denied to Bash (`filesystem.denyRead`) and to Read, Grep and Glob (`Read(//…)` rules);
  writes go only to the checkout and a scratch directory. `--out` inside either is
  refused.
- **No web tools, no network by default.** WebFetch and WebSearch are always off: the
  sandbox's domain allowlist does not cover them, and any web access reaches the live
  review. Sandboxed Bash allows no domains unless `--allow-domain` opens one
  (repeatable), for example the crates.io hosts if `cargo` has no cached registry.
  Opening `github.com` lets Bash read the live review, so a run that does is not blind;
  say so in its report.
- **Oracles instead of network.** The prompts' strongest evidence is a reference source
  (microsoft/terminal for conhost, mosh for mux). Clone it beforehand at the commit the
  target pins (conhost: `THIRD_PARTY_NOTICES.md` at the case head) and pass
  `--oracle NAME=PATH`, repeatable, with a full (not blobless) clone. `prepare` snapshots
  PATH's HEAD into `<out>/oracles/NAME` with no remote, so later edits or untracked files
  in PATH never reach a run, and the task names each snapshot and its commit; the reviewer
  reads it with sandboxed Bash and cannot write it. A path inside this repo, the target
  clone or the work directory is refused. Without one, the conhost expert cannot read
  conhost and marks its citations "(unverified)", so the run measures less than a live
  review does.

## The rest

- **Model.** `--model` is required and recorded with every result and in each report's
  header, so two reports compare only when their models match.
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
- **N runs.** `--repeat` (default 3) gives each run its own provider, checkout and
  scratch directory, so runs never see each other's builds or probe files; `--no-cache`
  keeps every run real.
- **Extract.** Writes `<case>-<n>-report.md` per run and a graded-run skeleton
  `<case>-<n>.toml` per successful run, and refuses before writing anything if any of
  those files exist. A failed run's report names its error, gets no skeleton, and makes
  the exit status 1.
- **Recall, first pass.** The rubric config grades each saved report with one
  `llm-rubric` per key finding (1 caught, 0.5 partial, 0 missed), using `--grader`.
  The score of record comes from the graded-run files: a blind verifier triages every
  finding ([bench/README.md](../README.md)) and [the scorer](../scorer/) reads them.
