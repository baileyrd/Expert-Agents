# pythrust, 2026-11-01, variant vs baseline@main: raw gate run

Variant: `experts/pythrust/` at `3d54c89` ("report every traced defect; prove each distinctness
assumption"), branch `claude/project-thread-dxffed`. Base: the same suite's `baseline` at `main`.
`main` resolved to the local ref `91b7e97`; `experts/pythrust`, `bench/suites/pythrust` and
`bench/cases/pythrust` are identical there and at `origin/main` `5b7639c`. **Not graded** (as
captured). The 20 extracted reports here are the variant's, with unfilled graded-run TOMLs.

> **Editorial note, 2026-11-02:** the TOMLs in this directory have since been graded; the graded
> result is [2026-11-01-variant.md](../2026-11-01-variant.md). The `baseline@main` re-run below
> (CLI `2.1.316`) is still only judged, not graded.

**Baseline re-run.** The 2026-10-31 baseline ran on Claude Code `2.1.314`; this machine now has
`2.1.316`, which changes every content key. The old store (60 records) was copied in first, but
no key matched, so `gate` re-ran `baseline@main` alongside the variant: 40 runs, both arms on the
same CLI. The baseline numbers below are from that re-run, not from
[2026-10-31-baseline](../2026-10-31-baseline.md).

Runner: Eval-Kit `0d594ab` (`main`), `python3 -m evalkit gate bench/suites/pythrust/suite.toml
baseline@main baseline --max-drop 0.1 --jobs 2`, alongside the rusty-tokio bench (`--jobs 2`).
Claude Code `2.1.316` on the signed-in login (no API key), reviewer `claude-opus-5-5`, judge
`claude-sonnet-5-5`. PythRust clone `~/src/PythRust` (not shallow), `origin/main` `b8ac312`.

## Gate

```
baseline vs baseline@main over 4 case(s)
lift +35%  (95% interval +0% to +70%)
improved: 48-r1, 48-r2
regressed: none
warning: the interval is wider than 20%, so a drop of 10% may go unseen; add cases or samples
gate passed (max drop 10%)
```

Exit code 0. 40 runs and the judge pass, all OK, none retried.

## Judge per key (`score --by-check`)

| Case | Key | Variant `3d54c89` | baseline@main (re-run, 2.1.316) | 2026-10-31 baseline (2.1.314) |
|---|---|---|---|---|
| 47-r1 | K1 loose function forwarding its parameters bypasses the tempered call check | 5/5 | 5/5 | 4/5 |
| 48-r1 | K1 different tuple slots assumed to hold different lists | 5/5 | 3/5 | 3/5 |
| 48-r1 | K2 exception holding a list turns off aliasing checks | 5/5 | 2/5 | 3/5 |
| 48-r2 | K1 different depths assumed to be different lists | 5/5 | 4/5 | 4/5 |
| 48-r2 | K2 parameter and global assumed never the same list | 5/5 | 3/5 | 4/5 |
| 65-r1 | K1 dict view drops element provenance | 5/5 | 5/5 | 5/5 |
| 65-r1 | K2 `get`'s default evaluated only on a miss | 4/5 | 4/5 | 4/5 |
| 65-r1 | K3 KeyError carries the converted key | 3/5 | 3/5 | 4/5 |
| **All** | | **37/40** (90% of runs pass all) | **29/40** (55%) | 31/40 |

The judge credits a mention anywhere in a report; graded recall needs the TOMLs (the
2026-10-31 graded recall was 0.73). Raw output: [by-check-variant.txt](by-check-variant.txt) from this worktree, and
[by-check-baseline-main.txt](by-check-baseline-main.txt) from the suite as of `main`, scored against the same store (read-only).

## Time, tokens, disk

| | Runs | Summed run time | Longest run | Tokens | Cost |
|---|---|---|---|---|---|
| Variant | 20 | 111m17s | 8m08s | 48.8M | $35.59 |
| baseline@main | 20 | 96m28s | 8m06s | 36.6M | $29.66 |
| **Gate** | 40 | 207m45s summed, **1h51m12s wall** | | 85.4M | $65.25 |

From the stored traces; the cost is the CLI's API-equivalent figure, billed to the plan, and the
judge calls are not included. Disk on `/` before: 577G used, 1.3T free; after: 600G used.
`/tmp`: 16G free throughout. No disk errors.

## Odd runs

None failed, hung or hit a rate limit. **No run lost its review to a background-task notice:**
all 40 stored `final_text`s are full reviews (each over 2,500 characters with a blocking-findings
section). In the rocket-league v2 bench, two runs stored only a reply to a late notice.

## Smoke test and network

The suite's `[settings]` were rendered into a scratch `.claude/settings.json`.
`cat $EXPERT_AGENTS/README.md` returned "No such file or directory"; `ls $TARGET` and
`ls ~/.claude` printed nothing; `curl https://github.com` was refused (CONNECT 403,
`deny network-outbound github.com:443`); `index.crates.io` answered 200. Allowed domains:
`index.crates.io`, `static.crates.io`.

## Isolation caveat

Eval-Kit `0d594ab` creates each run's `evalkit-*` project under the host temp root
(`~/bench-tmp/pv48`) and passes `TMPDIR` through, so all 40 runs, variant and baseline alike,
shared one `$TMPDIR`. The suite does not deny it, so `--jobs` siblings, runs of the other arm
and the concurrent rusty-tokio bench could read each other's projects and scratch, including
background-task output under `claude-1000/`.

Trace check (tool calls only; Eval-Kit stores no tool output):

- No run named another run's `evalkit-*` directory or another job's scratch.
- Three runs reached into the shared root by path (`$TMPDIR/..`, `~/bench-tmp/pv48/...`), and
  each path leads back into that run's own project: 48-r1 s1 (`hw19uo8g`), 48-r2 s3
  (`rxh8mkvk`) and 65-r1 s0 (`wc5p_3zw`).
- Fixed names shared across runs:
  - Cargo target dirs (`tgtb`, `tgtbase`, `target-base`, `tgt2`) and `uvcache`. A shared target
    dir can hand a run a stale build of a path dependency, which a rocket-league grader hit;
    whether any run here tested one cannot be told from the traces.
  - Base-commit source trees (`base65`, `rv48base`, `base`), each used only by runs of one case
    at that case's base commit.
- Key fixes reachable from other case heads: 48-r1's fix is 48-r2's head, and 65-r1's head
  contains the 47-r1, 48-r1 and 48-r2 fixes. No run extracted one of those heads under a name
  that a run of the affected case reused.

Proposed mitigation, not a verified isolation boundary: give each run a private `TMPDIR` and
deny the shared temp root to Bash's sandbox. That covers scratch and Bash only. The suite still
enables Read, Grep and Glob, which a Bash sandbox deny does not fence, and a separate host temp
root would need the same coverage. Fencing those tool paths and validating the sandbox in a real
run remain outstanding. None of this says contamination occurred in these runs. The content keys
of all 40 runs, both arms, are in [keys.txt](keys.txt).
