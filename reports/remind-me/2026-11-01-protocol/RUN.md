# remind-me, 2026-11-01, protocol-subset variant: raw run

Variant: `experts/remind-me/` at `055f36b` ("protocol-subset failure class for hand-rolled
parsers"), run as the suite's `baseline` variant on branch `claude/project-thread-k8h7wa`.
25 runs (5 cases × 5), judged; **not graded**. The graded-run TOMLs are unfilled skeletons for
the requester. Each report's header carries the `prompt_sha` it ran under.

Runner: Eval-Kit `0d594ab` (`main`), `python3 -m evalkit run --yes --jobs 2`, alongside the
rusty-tokio fix bench (also `--jobs 2`). Claude Code `2.1.316` on the signed-in login (no API
key), reviewer `claude-opus-5-5`, judge `claude-sonnet-5-5`. rusty-remind-me clone at
`e2e4e8dd1` (`main`, fetched before the run; not shallow). Before the run: `unittest` (69 OK),
`orchestrator check` (0 problems, 5 cases), `bench suite --check` and `evalkit score` were all clean.

## Judge hit rates (`--by-check`), next to the 2026-10-31 baseline

| Case | Key | This variant | Baseline (`9ebbf172`) |
|---|---|---|---|
| 343-r1 | K1 overlapping prefixed-token matches panic in `replace()` | 5/5 | 5/5 |
| 343-r1 | K2 card number next to other digits escapes redaction | 5/5 | 5/5 |
| 343-r2 | K1 later word-adjacent digit group cancels card redaction | 5/5 | 5/5 |
| 344-r1 | K1 sync outbox carries the new evidence but the old weight | 5/5 | 5/5 |
| 346-r1 | K1 malformed index falls back to array position | 5/5 | 5/5 |
| 346-r1 | K2 chunked embeddings responses are not decoded | 4/5 | 5/5 |
| 346-r2 | K1 `dechunk` accepts a body truncated after the zero-size chunk | 0/5 | 0/5 |
| **All** | | **29/35** | **30/35** |

The baseline's graded score is recall 0.70 and precision 1.00. The judge credits a mention
anywhere in a report, so it cannot show the change this variant aims at: 346-r1 K2 moving
from a validation-gap note to a blocking finding (baseline graded 0.00 against judge 5/5).
That needs the graded TOMLs. On the judge, 346-r2 K1 is still missed by every run. Raw
output: [by-check.txt](by-check.txt).

## Time, tokens, disk

| Case | Run time (5 runs summed) | Tokens | Cost | Longest run |
|---|---|---|---|---|
| 343-r1 | 65m43s | 6.1M | $5.73 | 16m29s |
| 343-r2 | 79m41s | 6.6M | $6.04 | 32m07s |
| 344-r1 | 55m18s | 14.2M | $9.12 | 15m14s |
| 346-r1 | 76m08s | 10.0M | $7.76 | 21m13s |
| 346-r2 | 59m31s | 6.7M | $7.14 | 16m59s |
| **All** | 336m21s summed, **2h59m54s wall** | 43.6M | $35.79 | |

From each report's header; the cost is the CLI's API-equivalent figure, billed to the plan, and
the judge pass (35 calls) is not included. Runs took about 3.6× the baseline's summed time
(92m29s). The baseline ran at `--jobs 4` with no other bench, while here two benches each built
Rust workspaces at once.

Disk on `/` before: 526G used, 1.4T free. After: 865G used, 1.1T free. `/tmp`: 16G free
throughout. The growth is mostly the runs' own cargo target dirs under the shared scratch
`~/bench-tmp/remind-me-variant-chunked-20261101/claude-1000` (318G: `target`, `target-prior`,
`tgt`, `pr344`, …), plus the concurrent bench's `~/bench-work/tmp` (247G).

## Smoke test and network

The suite's `[settings]` were rendered into a scratch `.claude/settings.json` and run with
`claude -p --setting-sources project --allowed-tools Bash`. `cat $EXPERT_AGENTS/README.md`
returned "No such file or directory" (denyRead), and `ls $TARGET` and `ls ~/.claude` printed
nothing. `curl https://github.com` was refused (`deny network-outbound github.com:443`).
`index.crates.io` answered 200. Allowed domains: `index.crates.io`, `static.crates.io`.

## Odd runs

None hung, came back empty or hit a rate limit or auth failure. All 25 runs and 35 judge calls
succeeded on the first attempt. 343-r2 run 3 took 32 minutes, the longest. The smallest report
(343-r2 run 5, 6.2 KB) has two blocking findings.

## Isolation caveat

Eval-Kit `0d594ab` creates each run's `evalkit-*` project under the host temp root and passes
`TMPDIR` through, so all 25 runs shared one `$TMPDIR`. The suite denies only `$EXPERT_AGENTS`,
`$TARGET` and `~/.claude`, so `--jobs` siblings could read each other's projects and scratch,
and so could the rusty-tokio bench running at the same time (its scratch is in
`~/bench-work/tmp`).

Trace check (tool calls only; Eval-Kit stores no tool output):
- No run named another run's `evalkit-*` directory, `~/bench-work/tmp` or another job's
  `~/bench-tmp/<id>`.
- Runs reused fixed names under the shared `$TMPDIR`. They shared build dirs (`target`, `tgt`,
  `target-base`), logs (`test.log`, `clippy.log`, `fulltest.log`), `HOME=$TMPDIR/home` for test
  runs, and source trees (`probe`, `base`, `scratch`, `clean`).
- Some trees were extracted with `mkdir -p` and no `rm -rf`, over whatever was left. Among
  343-r1 run 3, 344-r1 runs 3–4, 346-r1 runs 1, 4 and 5, and 346-r2 runs 2 and 4, I found none
  that overlaid a tree containing its own case's key fix.
- Only two leftover trees could carry a key fix: 343-r2's head (`9ef2ec3a`, the fix for
  343-r1) and 346-r2's head (`9daba9f8`, the fix for 346-r1). By the completion order in
  run.log, no 346-r2 run started before the last 346-r1 run ended. The 343-r2 runs that
  overlapped 343-r1 wrote only to `probe343` (after `rm -rf`), not to a name a 343-r1 run
  overlaid.
- This rests on the order in run.log and tool-call text, not on tool output, so it narrows
  the exposure without excluding it.

A per-run private `TMPDIR` plus a deny on the shared temp root would close it. The content key
of each run is in [keys.txt](keys.txt).
