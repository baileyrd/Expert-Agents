<!-- bbp-swf/763-r2 run 1, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 439 s, 3311065 tokens, $2.68 -->

# Review of PR 763 (Rusty-Mill/rusty_mill): changes required

Three P1 findings show that the run guards can still miscount evidence.

- **Base:** `5dbb633f9f2db1459002fc8fefee59eb4f9f2298`
- **Head:** `fa4a8a8a3bf8daff05ccb967d65ba9317b7e2aec`. `git fetch origin pull/763/head` returned exactly this SHA.

## Prior findings at this head

| | Status | Evidence |
|---|---|---|
| **K1** build pin checks the evaluator, not each run's producer | **Partly fixed** | `pairs.rs:551,559` now check each run's own `bbp_build_sha`. That value comes only from the run's *last* `bbp mod` invocation (`bbp.rs:191`), and a resume by a different build is allowed. See finding 2. |
| **K2** `build.rs` keeps a stale clean SHA | **Fixed** | `build.rs:43` names a path that never exists, so the script reruns on every build. All 6 tests in `tests/build_sha.rs` pass, including the unstaged-edit and untracked-file cases. |
| **K3** zero models price at zero | **Fixed** | `check_settings` (`pairs.rs:591`) refuses an empty `role_models`, any role with no model, and `no_model_turns > 0` (which includes turns with no output). |
| **K4** a terminal state succeeds before the final cap check | **Fixed** | The cap is enforced before the terminal return, then again by `check_terminal` (`moderator.rs:542`) after the harnesses are killed. The 7 tests in `terminal_cap` and the cap tests in `moderator_e2e` pass. |

## Blocking findings

### 1. P1: a harness whose turn ends normally is killed before it prints its `result`, so a healthy run counts as failed
**Where:** `moderator.rs:493` together with `tokens.rs:496-499` and `pairs.rs:89,553`.

**Scenario (executed):** A turn ends through the protocol: a `pass`, a candidate, a verdict or a gate request. On the next poll the reaper sees the harness still running with no live turn and kills its whole process group (`"turn N ended; stopping its harness"`). Claude Code does not stop at the tool call. It makes one more API request and only then prints `result`, so the log has no result line. From there:
- `price_turn` returns `Unknown("no result line")`.
- `unknown_turns` becomes `killed_turns`.
- `RunFacts::succeeded()` returns false.

**Probe** (scratch copy, fake `claude` that ends its turn with a gate request, sleeps 2 s, then prints the captured `completed-ttl-1h` fixture):
- Result printed after the turn ends: `turn-1 log has result: false; complete=false, "no result line"; usd None`.
- Neighbour that prints the fixture before ending its turn: `complete=true, usd 0.0143018`.

**Effect:** Nearly every turn of a real run ends this way, so control and treatment runs both fail. Criterion 2 then decides the screen ("Inconclusive; no decision"), and criterion 7 never sees a pair. With the 0.5 s default poll, every real run is exposed to this.

**Correction:** After a turn ends normally (pass, yield, verdict, candidate, request), give its harness a bounded grace period to exit before killing it. Only an abort, a revocation or a deadline should be a kill. The grace period must also be bounded by the deadline and wall checks.

**Regression test:** a `moderator_e2e` run whose harness prints its result about 1 s after ending its turn must end with that turn `complete`.

### 2. P1: a resumed evidence run reports only its last invocation, so a wall stop or another build's segment disappears
**Where:** `moderator.rs:615` (the marker check allows a resume), `moderator.rs:639` (the marker records no build), `bbp.rs:186-191`, `pairs.rs:544,551`.

**Scenario (executed):** Segment 1 runs with `--max-usd` and the criteria file, the plan gate goes unanswered, and the wall limit stops it: `stop: Wall`. Segment 2 resumes with the same cap and criteria file, and the human answers. It finishes `state Closed, stop None`, and `require_evidence_marker` accepts the task. The saved `bbp mod` output is the last invocation's, so `pairs` sees no stop and loads a run that hit its wall limit as a success.
- The criteria say a wall-limit run fails, and the note says a wall-stopped run loads as failed.
- The same path hides a `stop: cap` raised for an unreadable log, `replay_equals_live` from earlier segments, and the build SHA of earlier segments. A first segment built from a dirty tree, resumed by the clean build, passes `check_builds`. That is the remaining gap in K1.
- One caveat on the probe: a gate answer raced segment 2's start, so one turn had no output and `usd` was `None`. That does not affect the stop being erased.

**Correction:** Pick one:
- refuse to resume a task that has a marker; or
- record each invocation (build SHA, stop, replay) under the task directory and have `pairs` fail the run if any segment stopped or came from another build. Also add `BUILD_SHA` to `marker_text`.

**Regression test:** wall-stop a capped run, resume it to `Closed`, and require `pairs::facts` to report it as not succeeded, or require the resume to be refused.

### 3. P1: the evidence-marker check runs before the moderator lock, so two starts can both pass it
**Where:** `moderator.rs:372-373` (source-traced).

**Interleaving:**
1. Moderator A starts without a cap and reads no marker (`(None, None) => Ok`).
2. Moderator B starts with a cap, sees no `TurnGranted`, and writes the marker.
3. A takes the lock first. B fails with "another moderator holds".

A then runs the task with no cap, no pin check and no TTL pin. The task carries B's marker, so `require_evidence_marker` counts it as an evidence run.

**Correction:** Take the lock before `check_evidence_marker` and the `TurnGranted` check.

**Regression test:** a deterministic test that holds the lock, writes the marker, then releases the lock, and checks that an uncapped `run` taken after that is refused.

### 4. P2: a half-written last line in a live harness log stops the run as `cap`
**Where:** `tokens.rs:441-446` and `tokens.rs:644`, reached from `moderator.rs:553`.

**Scenario (executed):** The harness log ends in an unterminated line, `{"type":"assistant","message":{...,"usage":{"input_tokens":1`. The run is stopped with `Stop{Cap, "1 line(s) of JSON could not be read"}`.
- During a live run, any poll that reads while the harness is mid-write sees exactly this. Buffered reads of a regular file can see part of a multi-page write.
- The run is then marked `over_cap` (`pairs.rs:544`), so it counts as failed.
- N=2 has more harnesses writing at once, so it is hit more often.

**Correction:** While the run is live, ignore a final line that has no `\n` yet; still fail closed on it at the end of the run.

**Regression test:** an unterminated last line while the run is live is left alone, and the same line at the end of the run still stops it.

### 5. P2: `grade release` overwrites an existing release, so approved scores can be edited after the unused versions were seen
**Where:** `grade.rs:380-381`.

**Scenario (executed):** `release_files` with scores `[false,false]`, then again into the same output directory with `[true,true]`. Both calls return `Ok`, and `join_sealed` accepts the edited scores against the new `release.json`.

The design doc (line 289) says the approved scores "cannot be edited after the unused versions were seen". The code does not enforce that.

**Correction:** Refuse when `release.json` or `unused.json` already exists, opening them with `create_new`.

**Regression test:** a second release into the same directory fails.

### 6. P2: missing tests for changed behaviour
- No test has a harness that prints its result after its turn ends (finding 1).
- No test resumes a capped run after a stop (finding 2).
- No test runs the marker check against a concurrent start (finding 3).
- No test covers re-release (finding 5).

## Design question for the owner
The note itself says "a killed turn is a failed turn, as a revoked builder's harness is in an N=2 run". Above one live turn, every transition revokes the other live turns. So in an N=2 planning run, the second author is killed whenever the first posts the gate request. Read literally, almost every treatment run fails, and criterion 2 ("N=2 failures ≤ N=1 + 1") cannot hold. Is a *revoked* turn meant to fail its run, or only to count its cost at the lower bound?

## Optional improvements
- `bbp.rs` reads the criteria file twice for one cap: once for the prices, once for the SHA. Read it once.
- The fragment says "an edited dependency" makes the build `-dirty`. That holds only for dependencies inside the checkout, not edited registry sources or a git-ignored `.cargo/config`. Narrow the wording.
- A granted turn whose log is missing (`tokens.rs:638`) adds nothing to the live cap's floor. The cap's blind check (`moderator.rs:553`) only sees turns that have a log file, so a missing log is never treated as blind. `pairs` still refuses such a run, so this only weakens the live check.

## Checks run
- `cargo fmt --all --check`: pass.
- `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings`: pass.
- `cargo test --locked -p rusty_bbp_host`: all binaries pass except `runner_e2e::sandboxed_workload_reaches_no_socket_endpoint`. It fails on `bind: EPERM` from this session's outer sandbox, in a file the PR does not touch. I ran `terminal_cap`, `review_findings`, `shadow_log`, `why_live_turns` and `why` separately; all pass.
- `python3 .github/scripts/changes.py check`: 124 fragments ok.
- Probe tests (`probe763.rs`, in a scratch copy under `$TMPDIR`): 4 run, each reproduced the behaviour above.
- One early set of check logs was overwritten by another session sharing `$TMPDIR`, so I discarded it and reran everything in a separate directory.
- The PR's new tests depend on APIs added by this PR (`check_terminal`, `run_to_stop`, the `grade` release functions), so they cannot compile at the base. I did not run them there.

## Validation gaps
- Not run: `orch_bbp_acceptance.py`, and the Landlock-dependent sandbox tests (the outer sandbox blocks them).
- No real Claude Code run, so these are unconfirmed:
  - how long the harness takes after a tool call to print `result` (finding 1);
  - whether any real stream-json line has a non-object `message` field, which would fail parsing and stop a capped run.

  One paid single-turn BBP pilot under `bbp mod` would settle both. Spending on it needs the owner's go.

## Verdict: **changes required**
