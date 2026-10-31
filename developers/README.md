# Developers

A developer is the write side of an expert: it takes an issue or review finding in its
domain, writes the regression test first, implements the fix on its own branch, has the
domain's read-only reviewer (`experts/<name>/`) review the diff, and opens a PR for
DottiusMaximus. It never queues or merges, and it never replaces CI, the D4 Windows
run, owner confirmation or the merge queue. Reviewers stay read-only; nothing here
changes that.

| Developer | Target | Reviewer it pairs with |
|---|---|---|
| [rusty-tokio](rusty-tokio/) | Rusty-Mill/rusty_mill | [experts/rusty-tokio](../experts/rusty-tokio/) |

Each directory holds `prompt.md` (Claude subagent), `codex.md` (Codex, with `<TASK>` and
`<OWNER/REPOSITORY>` filled in by the dispatcher) and `developer.toml` (target paths).

## The rusty-tokio developer

Its invariants come from the reviewer's grounding (`experts/rusty-tokio/grounding.md`,
17 review-fix commits) plus what the reviewer's
[2026-10-31 baseline](../reports/rusty-tokio/2026-10-31-baseline.md) and the issues it
produced (Rusty-Mill/rusty_mill #1302-#1306) showed: cloning a `Waker` runs user code
too, the fallback runtime must be reachable from drop paths, I/O shims must keep std's
flush contract, and counts in change fragments must be measured.

## Measuring a developer (not yet built)

A fix bench, the developer counterpart of `bench/cases/`:

- **Case**: a past review-fix commit (e.g. 23b28c0f9, 7ba275a35, 7096ee85c from the
  grounding) pinned at its parent, with the issue or
  review text as the task. The fix commit's own regression test is held back.
- **Pass**: on the developer's head, the held-back test passes, the developer's own test
  was red at the parent, the required checks pass, and the reviewer returns no blocking
  findings. Secondary: diff size against the real fix, and reviewer findings per round.
- **Live**: #1302-#1305 are open issues the developer can take now; each PR it opens is
  scored the same way, with DottiusMaximus's review as the final grade.
- **Leak**: the prompt's invariants name the classes behind #1064, #1250, 1066-r2 K1 and
  1085-r1 K3, so cases built from those fixes are not blind; prefer the grounding's
  older fixes or new issues.
