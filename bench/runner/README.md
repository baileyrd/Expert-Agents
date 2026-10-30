# Runner

Runs one prompt variant of an expert N times on a case with
[promptfoo](https://www.promptfoo.dev/) and its Claude Agent SDK provider, then splits
the results into one report per run for grading.

```
npm ci --prefix bench/runner        # once: promptfoo and the Agent SDK, pinned
python3 -m bench.runner prepare bench/cases/mux/1251-r1 \
    --target ~/src/rusty_mill --out /tmp/bench-1251 --variant baseline --repeat 5
# run the printed promptfoo command, signed in to Claude Code, then:
python3 -m bench.runner extract /tmp/bench-1251/results.json \
    --out reports/mux/<date>-<variant>/
```

- **No API key.** The reviewer and the default grader use your signed-in Claude Code
  login (`apiKeyRequired: false`), so runs count against your plan's usage. Unset
  `ANTHROPIC_API_KEY` first: if it is set, it is used instead.
- **Blind.** `prepare` builds a new repository fetched by SHA from your clone of the
  target: the head and its history, with no later commits, no other refs and no
  remote. The reviewer cannot see the fix or the review.
- **Frozen prompt.** The prompt is the expert's Claude subagent `prompt.md` body as
  committed; `prepare` refuses uncommitted changes under `experts/` and records the
  commit as `prompt_sha`. Make a variant by committing it (on a branch) first.
- **Ablations.** `--drop "<heading>"` removes one declared section (repeatable).
  Sections marked `required` in `expert.toml` are refused.
- **Re-reviews.** A case with `prior` passes the prior round's key as the findings to
  re-check.
- **N runs.** `--repeat` (default 3) with `--no-cache`, so repeats are real runs.
- **Recall, automatically.** Each key finding is an `llm-rubric` assertion named after
  its id, scored 1 caught, 0.5 partial, 0 missed, graded by `--grader` (default
  `anthropic:messages:claude-sonnet-5-5`). promptfoo's view compares variants per key.
  This is a first pass, not the score of record.
- **Precision, by grading.** `extract` writes `<case>-<n>-report.md` and a skeleton
  `<case>-<n>.toml` per run. A blind verifier triages every finding into the skeleton
  ([bench/README.md](../README.md)), and [the scorer](../scorer/) reads those files.

The reviewer may use Read, Grep, Glob, Bash, WebFetch and WebSearch in its disposable
checkout; it has no write tools and the checkout has no remote. Keep `--out` outside
`reports/`, since it holds the checkout.
