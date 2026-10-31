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

## Measuring a developer: the fix bench

The developer counterpart of `bench/cases/`, in `bench/fixcases/<developer>/<case>/case.toml`
and `bench/fix/`.

- **Case**: a past fix commit in the target repo, pinned at its parent (`base`). The
  task is the defect written as a bug report: symptom and trace, never the fix. The fix
  commit's own test files are held back (`held_back`), with the command that runs them
  (`test`). `python3 -m bench.fix check` validates a case: the held-back tests fail at the
  base and pass at the fix.
- **Run**: Eval-Kit starts each run from a fresh checkout of the base, history only up
  to it, in Claude Code's sandbox with Edit and Write added, no network but crates.io, no
  read or write access to this repo, the target clone or `~/.claude`. GitHub, the
  reviewer subagent and the PR steps are out of scope: the run leaves its change in the
  working tree.
- **Grade** (`python3 -m bench.fix grade`, executed, not judged): the run's changed files
  are applied to the base in a scratch worktree, then:
  - **fixed**: with the held-back tests on top, the case's test command passes;
  - **suite green**: with the run's changes alone, `cargo test -p <crate>` passes;
  - **red at base**: the run's own test files (under `tests/`) fail at the base; `null`
    when its tests live in `#[cfg(test)]` modules;
  - **solved** = fixed and suite green.
  Secondary, by hand: diff size against the real fix, and reviewer findings on the diff.
- **Cases** (rusty-tokio): four fixes from the reviewer's grounding, `mpsc-cancelled-send`
  (7096ee85c), `mpsc-waker-under-lock` (23b28c0f9), `rwlock-queue` (7ba275a35) and
  `abort-poll-race` (d8dfe49e6).
- **Leak**: the developer's invariants are written from the grounding's defect classes,
  so these cases test whether it can apply a class it was told about, not discover one.
  The fixes behind #1064, #1250, 1066-r2 K1 and 1085-r1 K3 are named outright and are not
  used. New issues (like #1302-#1305, already taken in the
  [trials](rusty-tokio/trials-2026-10-31.md)) remain the blind test.

```
export PYTHONPATH=~/src/eval-kit EXPERT_AGENTS=$PWD TARGET=<rusty_mill clone>; unset ANTHROPIC_API_KEY
python3 -m bench.fix suite rusty-tokio --check
python3 -m bench.fix check rusty-tokio --clone $TARGET --work ~/bench-work/fixbench
python3 -m evalkit run bench/suites/rusty-tokio-dev/suite.toml --yes --jobs 2
python3 -m bench.fix grade rusty-tokio --clone $TARGET --work ~/bench-work/fixbench \
    --out reports/rusty-tokio-dev/<date>-baseline.json
```

Builds are large: run with `TMPDIR` and `CLAUDE_CODE_TMPDIR` on disk, not a RAM-backed
`/tmp`.
