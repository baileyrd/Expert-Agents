# Expert Agents — spec index
Harvested: 2026-11-03 @ 4f2664c · remind-me tag `expert-agents` (entity `expert agents`, alias `expert-reviewer-agents`)
Design chat: unknown (voice, 2026-10-30; captured to remind-me with tags `expert-agents`, `handoff`)
Tags: [stated] [default] [open].

## Intent
Agents were being sent to work with no specific guidance [stated, D1]. This repo
holds domain-expert agents grounded in the defects past reviews caught, and a bench
that shows which prompt change helps or hurts [stated, D3]. It is the source of
truth; target repos receive copies (D6).

## Index
Pointers only. Open the listed docs at call time; they beat this pack.

| Subsystem | Docs | Owner | Status | Expert |
|---|---|---|---|---|
| Reviewers (`experts/<name>/`) | README.md, `experts/*/expert.toml` | baileyrd | active | this one |
| Developers (`developers/<name>/`) | `developers/*/developer.toml` | baileyrd | active | this one |
| Bench cases, suites, extract | bench/README.md | baileyrd | active | this one |
| Scorer (`bench/scorer/`) | bench/README.md § Scoring | baileyrd | active | this one |
| Orchestrator + expert-generator skill | orchestrator/README.md, `.claude/skills/expert-generator/SKILL.md` | baileyrd | active | this one |
| Read-only guard (`guard/readonly.py`) | sync/README.md, `guard/tests/` | baileyrd | active | this one |
| Sync to target repos | sync/README.md | baileyrd | active | this one |
| Codex dispatch | docs/codex-review-dispatch.md | baileyrd | stable | this one |
| Reports (`reports/<expert>/`) | bench/README.md § A graded run | baileyrd | active | this one |

Owner: every PR is authored as baileyrd (agents push under that login); reviews are
by DottiusMaximus (D9). Runner: [Eval-Kit](https://github.com/baileyrd/Eval-Kit) (D12).

## Roles, in order
Reviewer → tester → designer → builder → idea intake (D17). Write-side
"developer" agents exist for six domains, each paired with its reviewer (D16).

## Open PRs at harvest
#2 (recover pre-revision trials), #14 (reverse expert, unbenched), #19 (rusty-tokio
developer), #28 (orchestrator `mine` fixes). Status, not decisions.
