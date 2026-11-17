<!-- bbp-swf/763-r2 run 5, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 275 s, 2483156 tokens, $1.85 -->

# Review of PR 763 (Rusty-Mill/rusty_mill): changes required

- **Base:** `5dbb633f9f2db1459002fc8fefee59eb4f9f2298`
- **Head reviewed:** `fa4a8a8a3bf8daff05ccb967d65ba9317b7e2aec`. I fetched `pull/763/head` and it resolved to this SHA.

All four earlier findings are fixed as written. Three new blocking defects can still let a run that should not count, or a score edited after peeking, reach a decision. A fourth is a stopped run that blocks the whole evaluation, and a fifth is a flaky test.

## The four earlier findings

| | Status at this head |
|---|---|
| **K1** The pin checked the evaluator, not each run's producer | **Fixed for the last invocation only.** `pairs.rs:551` now reads `bbp_build_sha` from each run's own `bbp mod` output, and `check_builds` (`pairs.rs:559-582`) refuses a run built from another, mixed, dirty or unknown commit. An earlier invocation of a resumed run is still not checked; see finding 2. |
| **K2** `build.rs` reran only on HEAD/index changes | **Fixed.** `build.rs:43` points `rerun-if-changed` at a path that never exists, so cargo reruns the script on every build and `git status --porcelain` is asked each time. |
| **K3** `check_settings` accepted zero models | **Fixed.** `pairs.rs:600-614` refuses a run with no model evidence, a role with no model, or a granted turn with no model. A result line alone now names no model. Finding 3 is a side effect of this check. |
| **K4** A terminal state returned success before the final cap check | **Fixed.** The moderator runs `enforce_cap` before the terminal return (`moderator.rs:414-427`), and `check_terminal` (`moderator.rs:438-441`) runs after the harnesses are killed. A failure becomes a `Stop`, and `bbp` exits non-zero. |

## Blocking findings

### 1. (P1) A capped `bbp mod` that never gets the lock still marks the task as an evidence run
- **Where:** `moderator.rs:372-373` and `moderator.rs:592-614`.
- **What happens:** the evidence marker is checked and written before the moderator lock is taken.
- **Interleaving (executed):**
  1. Moderator A starts without a cap. It finds no marker, takes the lock, and has not granted a turn yet.
  2. Moderator B starts with `--max-usd`. It finds no marker and no `TurnGranted`, so it writes the marker.
  3. B then fails with "another moderator holds task".
  4. A carries on with no live cap. It also skips the pin check, the cache-lifetime and subagent-model environment pins, and the final cap check.
  5. `bbp pairs` runs `require_evidence_marker`, which passes, so A's run counts as evidence.
- **Probe:** `probe_a_marker_written_by_a_capped_mod_that_never_took_the_lock`, in a scratch copy, holds the lock in the place of A. `run_to_stop` with a cap returned "another moderator holds task T1", and afterwards `require_evidence_marker(..)` returned `Ok(())`.
- **Fix:** take the lock before `check_evidence_marker`. Write the marker only while holding the lock and only if there is no `TurnGranted`, using `create_new` so that one writer wins.
- **Regression test:** the probe above. It also needs a neighbour test showing that a capped start that does hold the lock still writes the marker.

### 2. (P1) A resumed evidence run is judged only by its last invocation
- **Where:** `moderator.rs:376`, `moderator.rs:609-616` (resume allowed), `pairs.rs:467-476` and `pairs.rs:551`.
- **What happens (source-traced):** a run that `--max-wall-secs` stopped can be resumed with the same cap and price file. Nothing checks the earlier invocation.
  - **Stop status:** only the last invocation's `bbp mod` output goes in the manifest. A wall stop taken while no harness was live leaves no killed turn. After the resume the run closes, prints no `stop`, and counts as a success. The docs say a stopped run is a failed run.
  - **Producer:** the first invocation's `bbp_build_sha` is never checked. A `-dirty` build can run the first segment and a clean build finish it, and the run passes K1's pin.
  - **Timing:** `Visits` restarts at the resume. The resumed invocation's `spec_wall_ms` is either a replanning visit or only the rest of the planning visit. `spec_boundaries` treats the resume point as an exact Planning start (`pairs.rs:459-461`). Criterion 3 reads a wrong measurement.
- **Fix:** do one of the following.
  - Refuse to resume an evidence run that has a recorded stop.
  - Or record each invocation's build and stop in the marker (or an append-only file under the task). Then have `pairs` refuse a mixed build or any recorded stop, and refuse a `spec_wall` that spans more than one invocation.
- **Regression test:** a wall-stopped evidence task resumed to `Closed`, and a resume by a different build. `pairs::load_as` must refuse both, or load them as failed.

### 3. (P1) The approved-scores seal can be redone after the unused versions are seen
- **Where:** `grade.rs:372-383`. The doc it makes false is `docs/bbp-concurrency-design.md:289`: "they cannot be edited after the unused versions were seen".
- **What happens (executed):** `release_files` overwrites `release.json` every time it runs. After a first release has written `unused.json`, the approved scores can be edited and released again into the same `--out`. That succeeds with a new hash, and `join_files` accepts the edited scores.
- **Probe:** `probe_c_a_second_release_reseals_edited_approved_scores`. The second release returned `Ok(...)` and the join returned `Ok`.
- **Why it matters:** `lost_rubric_items` is computed from the approved scores against the unused ones. Criterion 5, and so the decision, can follow scores chosen after peeking.
- **Fix:**
  - Create `release.json` and `unused.json` with `create_new`, and refuse if either already exists.
  - State in the doc that the release hash has to be recorded outside the grader's control (for example, committed or printed to the owner) before `unused.json` is read.
- **Regression test:** the probe above, with a single release still passing as the valid neighbour.

### 4. (P2) A stopped run whose killed harness printed nothing blocks the whole evaluation
- **Where:** `pairs.rs:609-614`. This contradicts `docs/bbp-concurrency-design.md:332` and fragment line 18 ("loads in `bbp pairs` as a failed run").
- **What happens (executed):** a cap or wall stop kills every live harness, including one granted moments earlier. If that harness printed nothing, or no file exists, `check_settings` refuses the whole manifest instead of loading the run as failed.
  - **Probe:** `probe_b_stopped_run_with_a_silent_killed_harness_loads_as_failed`, with an empty log. `load_as` returned `Err("p1/treatment: no model observed for role planner")`. I did not get a result for the "init line only" and "no log file" variants, because the loop stopped at the first error.
- **Why it matters:** criterion 2 can never be computed with this failure included. The only way to get an evaluation is to drop or rerun the pair, which is the cherry-pick that criterion 2's failed-pair rule exists to prevent.
- **Coverage gap:** the existing stopped-run tests (`pairs_load.rs:286-314`) call `facts` only and never reach `check_settings`.
- **Fix:** exempt a run that will count as failed (a recorded stop, or killed turns) from the model-evidence refusal. Keep the refusal for runs that would count as successes.
- **Regression test:** the probe above, run through `load_as`.

### 5. (P2) Two new tests share a scratch directory, so the suite is flaky
- **Where:** `tests/review_findings.rs:202-213` and `tests/review_findings.rs:368-379`.
- **What happens (executed):** both tests use the tag `completed-ttl-1h` for `scratch()`, which makes `bbp-review-{tag}-{pid}`. They run in parallel in one process, and one test's `remove_dir_all` deletes the other's log.
  - Run 1: all 18 passed.
  - Run 2: `the_effective_cache_lifetime_is_logged_per_turn_and_per_run` panicked with "index out of bounds: the len is 0" at line 377.
- **Fix:** give each test a unique tag.

## Design questions for the owner
- Should an evidence run that was stopped be resumable at all? The doc allows a resume with the same cap; finding 2 shows what that costs.
- The "Changed (owner decisions, round three)" items cover cache pricing and setting sources. The doc row change "summed over both arms" for the poll error bound is a change to how a criterion is measured. Please confirm it was approved.

## Optional improvements
- `bin/bbp.rs:157-158` reads the criteria file twice, once for the prices and once for the sha. Read it once so the two always come from the same bytes.
- If the moderator kills a harness while it is writing, the log can end in a cut-off JSON line. `scan` then makes `tokens::of` return an error and `pairs` refuses, rather than loading a failed run. Consider tolerating a cut-off last line in a turn that is already known to be killed.

## Checks performed
All checks ran in a scratch copy at `$TMPDIR/rv763`, extracted with `git archive` from the head, with `CARGO_TARGET_DIR` pointed at scratch.

- **`cargo fmt --all --check`:** passes.
- **`cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings`:** passes.
- **`cargo test --locked -p rusty_bbp_host --no-fail-fast`:** two failures, everything else passes.
  - The lib tests (113), `pairs_load` (9), `moderator_e2e` (30), `terminal_cap` and `build_sha` all pass.
  - `review_findings` failed 1 of 18 on the second run (finding 5).
  - `runner_e2e::sandboxed_workload_reaches_no_socket_endpoint` fails because this session's sandbox refuses Unix socket `bind` (EPERM). That is an environment limit, not a result.
- **`python3 .github/scripts/changes.py check`:** passes (124 fragments ok).
- **Not run:** the other crates and `orch_bbp_acceptance.py`. The diff does not touch them (`git diff --quiet` is clean for `crates/apps/rusty_orch` and `crates/libs`).
- **Probes A, B and C:** run as above; each reproduces its finding.
- **Validation gaps:**
  - The sandboxed socket test needs a host that allows Unix sockets.
  - Landlock enforcement was not checked.
  - The pricing and cache-split assumptions were checked only against the captured fixtures. No real provider was run.
  - I did not do the base-SHA red/green runs: the new tests use APIs that don't exist at base, so they cannot compile there.
  - `pins.rs` (the shell-launcher parser) was only partly read.

## Verdict
**Changes required.**
