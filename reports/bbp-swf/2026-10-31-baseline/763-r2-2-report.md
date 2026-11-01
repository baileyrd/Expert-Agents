<!-- bbp-swf/763-r2 run 2, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 291 s, 2934879 tokens, $2.06 -->

# Review of PR 763 (Rusty-Mill/rusty_mill): bbp run guards

The four earlier findings (K1–K4) are fixed at this head, but I found four new defects (three P1, one P2), so the verdict is **changes required**.

- **Base:** `5dbb633f9f2db1459002fc8fefee59eb4f9f2298`
- **Head:** `fa4a8a8a3bf8daff05ccb967d65ba9317b7e2aec`. `git fetch origin pull/763/head` gives exactly this SHA, so the review is pinned.

## Earlier findings, re-checked at this head

| | Status | Where |
|---|---|---|
| K1: the build pin checked the evaluator, not each run's producer | **Fixed.** Each run's own `bbp_build_sha` must be a clean 40-hex commit, all runs must share it, and it must equal the pinned HEAD. | `pairs.rs:482-499`, `551`, `559-582`, `711-712` |
| K2: a dirty build kept a clean `BBP_BUILD_SHA` | **Fixed.** `build.rs` names a path that never exists, so cargo reruns it on every build and it asks `git status --porcelain` each time. `tests/build_sha.rs` passes (6/6) against real cargo. | `build.rs:42-43` |
| K3: zero models priced a run at zero | **Fixed.** An arm with no models, a role with no models, or any granted turn with no model observed refuses the evaluation. | `pairs.rs:600-614` |
| K4: a terminal state returned success before the final cap check | **Fixed.** The cap runs before the terminal return, and again after the harnesses are killed (`check_terminal`). A breach becomes `stop: cap`, which `pairs` counts as over the cap. | `moderator.rs:412-441`, `541-547`, `pairs.rs:544` |

## Blocking findings

### F1 (P1): the moderator kills each harness before it prints its `result`, so nearly every real run counts as failed

**Where:** `moderator.rs:493-497` (the reap) combined with `tokens.rs:661-669` and `pairs.rs:89`, `553`.

**What happens (executed):**
- A turn ends when the agent makes its last tool call (a gated `request_decision`, a candidate, a verdict).
- A real `claude` then makes one more model call for its final reply, and only then prints the `result` line.
- At the next poll the reap sees the turn is no longer live, with the harness still running, and kills it.
- With no `result` line the turn is marked incomplete, so `killed_turns > 0`, `succeeded()` is false and `usd` is `None`.

**Probe:** I ran the head `bbp mod` with `--poll-ms 500` and a fake planner. It ends its turn through `bbp mcp`, prints the first line of `completed-ttl-1h.jsonl`, sleeps 3 s, then prints the `result` line.
- Output: `turn 1 ended; stopping its harness`.
- The log has 0 `result` lines.
- `bbp tokens --criteria docs/bbp/2b-criteria.json` reports `unknown_turns: 1`, `usd: null` and `complete: false`.

**Effect on the decision:** every run fails, and N=2 runs have more turns to fail on. Criterion 2 then fails, and the table answers "rerun with a corrected rubric or task set". That decision would come from the host's own kill, not from the runs.

**Required correction:** a harness whose turn ended normally gets a bounded grace period to exit and print its `result` before it is killed. Turns killed for revocation, deadline or cap stay distinct from that. How long the grace is, and whether a normal-end kill counts as a "killed turn" under the round-three decision, is the owner's call.

**Regression test:** a `moderator_e2e` test with a fake `claude` that prints `result` 1–2 polls after its ending tool call, asserting the turn is complete and `killed_turns == 0`.

Minor: in this case the `unknown` reason says "assistant messages after the last result" although there is no result at all (`tokens.rs:662-665`).

### F2 (P1): approved scores can be re-sealed after the unused versions are seen

**Where:** `grade.rs:372-382` and `391-407`.

**What happens (executed):** `release` overwrites `release.json` and `unused.json` without any check, and `join` trusts whichever `release.json` it is given. I ran this sequence:
1. Release with approved scores `{a:[true], b:[true]}`.
2. Read `unused.json`.
3. Release again with `b:[false]`.
4. Join.

Both releases succeed (`rc=0`), and `join` accepts the second hash. The treatment run flips to `rubric_ok: false` and `lost_rubric_items: 1`, which feeds criteria 2 and 5.

**Why it matters:** this breaks the owner's grader decision, and the note's claim that the scores "cannot be edited after the unused versions were seen" (`bbp-concurrency-design.md`, grading order) is false.

**Required correction:** make release one-shot and bound to the pack.
- Create `release.json` and `unused.json` with `create_new`.
- Consume `unused.held.json`, so a second release has nothing left to release.
- Have `join` read the release from the pack directory, not from an argument.

**Regression test:** a second `release` into the same pack is refused; a first release still passes.

### F3 (P1): the evidence-run marker is written before the moderator takes the task lock

**Where:** `moderator.rs:372-373`, with the marker logic at `597-618`.

**What happens (executed):** `check_evidence_marker` runs, and may write the marker, before `lock()`. An uncapped moderator checks the marker only before locking and never again.

**Probe:** I held the task's moderator lock with `flock` (standing in for an uncapped `bbp mod`), then ran a capped `bbp mod`. It failed with "another moderator holds task T", but it had already written `run/evidence/<task>.json` with `max_usd 5` and the criteria hash.

**Effect:** the uncapped run carries on with no live cap, no pin or harness checks, and no 1 h cache-TTL or subagent-model pins. `require_evidence_marker` then accepts it in `bbp pairs` as an evidence run. Because cache lifetime changes cost, its arm can differ from its pair in more than the treatment.

**Required correction:** take the lock first, then check and write the marker under it.

**Regression test:** with the lock held, a capped `bbp mod` refuses and leaves no marker.

### F4 (P2): two `review_findings` tests are flaky

**Where:** `tests/review_findings.rs:19-24`, `202-215` and `367-377`.

**What happens (executed):** both tests use the same scratch tag (`completed-ttl-1h`) under the same pid, and `scratch()` runs `remove_dir_all` on that directory. Running in parallel, they delete each other's files. Across 4 runs of the suite, 2 failed, each time a different one of these two tests (`index out of bounds` at `:377`, and a `criteria` failure at `:37`). CI can go red on this head with no code change.

**Required correction:** give each test its own tag, or use a per-test counter.

## Design questions for the owner
- `check_settings` refuses the whole evaluation when a single granted turn left no output (for example, no launcher for its role or a harness that never started). The alternative is to fail only that run. Which is intended?
- Neither `agents-check` nor `bbp pairs` sees the operator's inherited environment: an endpoint or provider switch such as `ANTHROPIC_BASE_URL` or `CLAUDE_CODE_USE_BEDROCK`. The 2% cross-check uses the harness's own `total_cost_usd`, so it would not catch it either.

## Optional improvements
- Fix the misleading `unknown` reason noted under F1.
- Fix a stale doc comment: `grade.rs:332` still says `pack` writes `unused.json`.

## Checks run
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings`: passed. The default target directory is read-only in this sandbox, so this and the tests below used a scratch `CARGO_TARGET_DIR`.
- `cargo test --locked -p rusty_bbp_host`: failed.
  - `review_findings` is flaky (F4); it passes when run alone.
  - `runner_e2e::sandboxed_workload_reaches_no_socket_endpoint` fails because `bind` gets EPERM inside this sandbox. The PR doesn't touch the runner; this is a validation gap, not a pass.
  - The lib (113), `build_sha` (6), `moderator_e2e` (30), `pairs_load` (9) and `mcp_e2e` (7) suites all passed.
- `python3 .github/scripts/changes.py check`: passed (124 fragments ok).
- New library code: no `unwrap` or `expect`. The indexing is all bounded: by `position`, or by a length checked first.
- Probes, run against the head `bbp` binary in scratch directories: F1, F2 and F3 above.

## Validation gaps
- Not run: the acceptance script, because Orch is untouched.
- Not checked: Landlock confinement on this kernel.
- No real provider was run. A real one would settle F1's timing: run a Claude Code 2.1.300 turn under `bbp mod` and check whether `result` lands before the next poll. A real `claude` needs another model call after its last tool result, so I expect it to lose that race.
- I didn't run the new tests at the base commit. Most of them use APIs this PR adds, so they wouldn't compile there.

## Verdict
**Changes required.**
