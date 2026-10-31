# Expert Agents

Domain-expert reviewer agents: the prompts, how they are generated, and the bench
that shows which prompt changes help or hurt. This repo is the source of truth;
target repos receive copies.

Each reviewer encodes what a hard-to-hire specialist would check, grounded in the
defects past reviews actually caught. Reviewers are read-only and additive: they never
replace CI, the D4 Windows run, owner confirmation or the merge queue.

| Expert | Target | Domain |
|---|---|---|
| [conhost](experts/conhost/) | Rusty-Mill/rusty_mill | rusty_conhost, the Windows console host |
| [mux](experts/mux/) | Rusty-Mill/rusty_mill | rusty_mux, the terminal multiplexer for agents |
| [mill-board](experts/mill-board/) | Rusty-Mill/rusty_mill | Mill Board, the kanban agents and people share |
| [pythrust](experts/pythrust/) | baileyrd/PythRust | Pythrust's compiler: ownership inference, types, native backend |
| [remind-me](experts/remind-me/) | Rusty-Mill/rusty-remind-me | rusty-remind-me, the shared memory every agent reads and writes |
| [rusty-tokio](experts/rusty-tokio/) | Rusty-Mill/rusty_mill | rusty_tokio, our tokio replacement, and the migration onto it |
| [general](experts/general/) | Rusty-Mill/rusty_mill | fallback for PRs outside every domain |

## Layout

```
experts/<name>/      prompt.md (Claude subagent), codex.md (Codex), expert.toml
developers/          expert developers: write code in a domain, reviewed by its expert
bench/cases/         frozen cases: pr.toml pins a PR head; answer_key.toml lists its known findings
bench/suites/        Eval-Kit suites generated from the cases (python3 -m bench suite <expert>)
bench/scorer/        recall, precision and severity agreement against the key
reports/<expert>/    committed results, <date>-<variant>.md, graded runs beside them
orchestrator/        generates a new expert from an interview answer (skill: .claude/skills/expert-generator)
sync/                how approved prompts reach target repos
docs/                Codex dispatch procedure
```

`expert.toml` names each prompt's target path and the prompt's sections, so an
ablation can drop one section at a time. See [bench/README.md](bench/README.md) for the
case, key and run formats.

## Workflow

1. Generate or edit a prompt variant.
2. Run it on the bench, N runs per case.
3. Compare the report with the current baseline.
4. Promote only if it beats the baseline without regressing on any case.
5. Sync the promoted prompt to its target repos.

No promotion without a bench result. Changes land by PR, reviewed by DottiusMaximus.

## Checks

```
python3 -m unittest
```

Python 3.11 or later, standard library only. Running the bench also needs
[Eval-Kit](https://github.com/baileyrd/Eval-Kit) and the Claude CLI signed in.
