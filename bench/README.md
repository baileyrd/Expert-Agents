# Bench

A frozen test set of past PR heads with answer keys, and a scorer. The runner that
produces reviewer output is not built yet (see [runner/](runner/README.md)).

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

Blind: a fresh worktree at `head`, the prompt and nothing else, with no access to
later commits, `main` or the PR's comments. Freeze the prompt for the whole run and
record this repo's commit SHA as `prompt_sha`, with the duration and token cost in the
report. One run proves little; run each variant N times.

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
