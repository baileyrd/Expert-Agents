# Developers

Expert developers: agents that write code in a domain, grounded in the same review
fixes as its reviewer. Unlike reviewers they edit, commit and open PRs, so they carry no
read-only guard and are not run by the bench yet. Each is trialled on real issues, with
its PRs reviewed by the matching reviewer under `experts/` and by DottiusMaximus.

| Developer | Target | Reviewer | Grounding |
|---|---|---|---|
| [rusty-tokio](rusty-tokio.md) | Rusty-Mill/rusty_mill | [rusty-tokio](../experts/rusty-tokio/) | [grounding.md](../experts/rusty-tokio/grounding.md) |

A developer syncs to `.claude/agents/<name>-developer.md` in its target. It is promoted
only after its trial PRs pass review without regressions.
