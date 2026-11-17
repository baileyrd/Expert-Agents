<!-- bbp-swf/763-r1 run 1, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 269 s, 1550456 tokens, $1.33 -->

# Review of PR 763: BBP run guards

- **Repository:** Rusty-Mill/rusty_mill, PR 763
- **Base:** `5dbb633f9f2db1459002fc8fefee59eb4f9f2298`
- **Head:** `2b31b4e637088adf15227f8b039a8981f6a6a3db`. I fetched `pull/763/head` and it matches this SHA. The PR is one commit.

**Verdict: changes required.** The commit check in `bbp pairs` checks the wrong binary (P1). The live cost cap reads spend it cannot see as $0 (P2), and a run that goes over the cap can still close as a success (P2).

## Blocking findings

### P1: `bbp pairs` checks the commit of its own binary, not of the binary that produced the runs
- **Where:** `crates/apps/rusty_bbp_host/src/pairs.rs:519-528` (`load` passes `crate::BUILD_SHA` to `pin` and records it), `pairs.rs:375` (`ModOut` reads only `state_ms`), and `pin` at `pairs.rs:491`.
- **Failure scenario (source-traced):**
  1. The 26 runs are made with a `bbp` built from an older commit, for example one before the verdict-binding fix #727 that `min_commit` exists to require, or from a dirty tree. Each `bbp mod` output records that commit as `"bbp_build_sha":"<old>"` or `"<sha>-dirty"`.
  2. The operator then checks out HEAD cleanly, rebuilds, and runs `bbp pairs`.
  3. `pin` compares the evaluator's own `BUILD_SHA` with HEAD and passes. Nothing reads the runs' `bbp_build_sha`.
  4. The evaluation counts every run and records `bbp_build_sha = HEAD`. That reads as proof the runs were pinned, when they ran code the criteria forbid.
- **Docs:** the change fragment says "`bbp mod` prints it as `bbp_build_sha` and `bbp pairs` refuses unless it equals the checkout's HEAD". In fact it is only the evaluator that gets checked. `docs/bbp-concurrency-design.md:311` ("cannot prove the `bbp` binary came from it") is not updated in either direction.
- **Required correction:**
  - Parse `bbp_build_sha` in `ModOut`, and in `facts` or `load` refuse any run whose value is missing, `-dirty`, `unknown`, or not equal to the pinned HEAD.
  - Keep the evaluator check as an extra guard.
  - Record one build SHA per run in the evaluation.
- **Regression test:** a manifest whose mod output carries `"bbp_build_sha":"<other sha>"` (and another with the field missing) must make `load` fail, while the same manifest with the HEAD SHA passes.

### P2: the live cost cap reads spend it cannot see as $0
- **Where:** `moderator.rs:451` (`t.usd.unwrap_or(0.0)`), relying on `tokens.rs:322-325` (any read error is filed as `without_output`) and `tokens.rs:235-260` (only `assistant` lines are priced).
- **Failure scenario (executed in a scratch copy, using the PR's `capped_run` harness):**
  - **Non-UTF-8 byte on stderr.** The harness prints a $20 priced assistant message, then a single non-UTF-8 byte on stderr. `spawn` sends stderr into the same `turn-N.log` (`moderator.rs:508-522`), so `read_to_string` fails and the turn is skipped as `without_output`. With a $1 cap, the result was `Err("moderator: wall limit reached in state Planning")` after the full 6 s wall. The control probe without the byte gave `cost cap reached ($20.0000 over $1.0000)`.
  - **Result line only.** A launcher whose output carries the usage only in a `result` line (`--output-format json` style, `total_cost_usd: 20`) gave the same result: wall limit, cap never fired.
- **What it should do:** the fragment says the cap fails closed when cost cannot be priced. Any turn with a launcher whose log exists but cannot be read, or that has printed output but no priced assistant usage, is unknown cost. Only a log that is genuinely not found means the turn has not started yet.
- **Severity:** the API key's spend limit is still the hard stop, so this is a cap failing open in a bounded case, not uncapped spend.
- **Required correction:** in `enforce_cap`, fail the run on a read error other than `NotFound`, and on a turn whose log is non-empty but yields no priced usage. Alternatively, read the log as bytes with lossy decoding so a stray byte cannot hide a whole turn.
- **Regression tests:** the two probes above, asserting `cost cap` or `cannot enforce` instead of the wall limit.

### P2: a run can close over the cap and still be reported as Closed
- **Where:** `moderator.rs:338-347`, which runs before the cap check at `moderator.rs:352-357`.
- **Failure scenario (source-traced):** a harness prints usage that takes the run over C, and the task reaches `Closed` before the next poll's cap check runs.
  - The loop returns `Ok(Outcome{state: Closed})` without pricing the run.
  - `bbp mod` prints `"state":"Closed"`, and `bbp pairs` counts the run as closed.
  - The fragment says "over C, kills the harnesses and fails the run". The run plan (`bbp-concurrency-design.md:322`) says "a run over C counts as a failed run".
- **Required correction:** run `enforce_cap` once more before the Closed/Cancelled return (and before the wall-limit return), and fail the run if it is over the cap.
- **Regression test:** a scripted roster that closes the task in the same poll in which a harness prints usage over the cap; it must return `Err` containing `cost cap`.

### P2: docs and test coverage
- **Docs:**
  - `crates/apps/rusty_bbp_host/README.md:29,72` does not mention `--max-usd`/`--criteria` or the new `bbp_build_sha` output field.
  - The "Cost cap for the run" section of `docs/bbp-concurrency-design.md:317-324` still says the cap "cannot stop a run in flight" and that enforcement is only the key limit plus `--max-wall-secs`.
  - The new manifest field `reads_per_turn` is not documented anywhere.
- **Tests:** none of the following has a test:
  - `load` calling `check_settings` and `pin` with `crate::BUILD_SHA`. The unit tests call `pin` and `check_settings` directly, so removing either call from `load` would leave every test green.
  - The `bbp mod` flag pairing (`--max-usd` without `--criteria`, or a non-positive value).
  - `bbp mod` printing `bbp_build_sha`.

  These belong in `pairs.rs` tests (a manifest fixture through `load`) and a binary-level test of `bbp mod` argument errors.

## Design questions for the owner
- **Which model an extra N=2 role may use.** `check_settings` accepts an extra role on *any* model the control used. For example, control planner on Opus and coder on Sonnet still accepts `planner2` on Sonnet, so the arms differ in more than N. The criteria say "same … model"; whether the second author must match the first author's model is a rule for the owner, not one this PR should set.
- **A live cap at all.** The cost-cap section is marked "not approved, not built", and C is to be set after the pilot. Adding a live cap is a tooling choice the owner should confirm, along with whether a cap stop is recorded in the run output.

## Optional improvements
- `build.rs` reruns only when `.git/HEAD` or `.git/index` change, not when a branch ref moves. In my scratch-crate probes every stale case came out as `-dirty`, so the pin refuses; that is safe but can force needless rebuilds. Watching `refs/` or `packed-refs`, or always rerunning, would avoid that.
- The new decision-table tests guard `decide()`, which this PR does not change. That is fine as regression cover, but they are not tests of anything the PR changes.

## Checks performed
| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings` | pass, with `CARGO_TARGET_DIR` in `$TMPDIR` because the default target directory is read-only here |
| `cargo test --locked -p rusty_bbp_host --no-fail-fast` | 118 passed, 1 failed |
| `python3 .github/scripts/changes.py check` | pass (124 fragments ok) |
| Probes in a scratch copy (`git archive` of the head) | non-UTF-8 stderr and result-only output both slip past the cap; the control probe is capped |
| Probes of `build.rs` in a scratch crate | stale cases came out `-dirty` (fails closed) |

- **The one test failure:** `runner_e2e::sandboxed_workload_reaches_no_socket_endpoint` fails because this sandbox refuses the test's own `bind` (EPERM). Nothing in this PR touches that path, but it means the test is unverified here, not passing.
- **Validation gaps:**
  - The Orch acceptance script was not run; the PR touches only `rusty_bbp_host`.
  - I did not check whether real Claude Code stream-json output emits zero-usage `<synthetic>` model messages. If it does, the new live cap would kill a run on them as "unpriced". Settling this needs a captured real-provider fixture, which I did not run.
