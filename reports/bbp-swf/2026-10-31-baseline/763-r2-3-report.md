<!-- bbp-swf/763-r2 run 3, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 439 s, 3822242 tokens, $2.76 -->

# Review of Rusty-Mill/rusty_mill PR 763

**Verdict: changes required.** Three P1 findings and three P2 findings, each backed by an executed probe.

- **Base:** `5dbb633f9f2db1459002fc8fefee59eb4f9f2298`
- **Reviewed head:** `fa4a8a8a3bf8daff05ccb967d65ba9317b7e2aec`. The fetched `pull/763/head` matches both this SHA and the local HEAD.
- **Scope:** the PR touches only `rusty_bbp_host` (plus docs, the price file and the change fragment). Your local edits under `.claude/` are not part of the diff and I did not review them.
- **Probes:** they ran in a scratch copy made with `git archive` under `$TMPDIR/scratch`. The reviewed tree was not modified.

## Prior findings, re-checked at this head

- **K1 (build pin checked the evaluator, not each run): partly fixed.** `bbp pairs` now reads each run's own `bbp_build_sha` (`pairs.rs:482`) and requires all runs to share one commit (`check_builds`). That only covers the last `bbp mod` invocation. A resumed evidence run escapes the check: see F2.
- **K2 (build.rs kept a stale clean sha): fixed for edits, dependencies and branch moves.** It now reruns on every build (`build.rs:43`), and `tests/build_sha.rs` covers those cases. One remaining hole: F4.
- **K3 (zero models priced at zero): fixed.** `check_settings` refuses an arm with no model evidence and any granted turn that observed no model (`pairs.rs:600-614`).
- **K4 (terminal state succeeded before the final cap check): fixed.** On `closed` or `cancelled`, the harnesses are killed, then `check_terminal` runs and a failure becomes a `cap` stop (`moderator.rs:434-440`). Tests: `final_output_over_the_cap_on_a_terminal_state_is_not_a_success` and `terminal_cap.rs`.

## Blocking findings

### F1 (P1): A turn ended through the protocol is counted as a killed turn, so its run fails

- **Where:** `moderator.rs:493` (a harness whose turn is no longer live is killed on the next poll, with no grace period), `tokens.rs:653-669` (no `result` line means unknown cost), `pairs.rs:89` (`killed_turns == 0` is required for success).
- **Trigger:** every ordinary turn end. The core ends the turn on `request_decision`, a verdict or a pass. Claude Code then still needs one more model call before it prints its `result` line. The host README's own hand run describes this order: "the core … ended the turn; the moderator stopped the harness".
- **Probe (executed):** a planner prints one assistant line, stores a spec, posts the gated `request_decision`, then prints a valid priced `result` one second later. The moderator killed it before the `result`. The log contains only the assistant line, and `tokens::of` reports `complete=false, unknown_turns=1, usd=None`.
- **What goes wrong:** in a real run nearly every turn becomes a "killed turn". Every run then fails, so criterion 2 fails and the decision table returns "Inconclusive; no decision; rerun with a corrected rubric or task set", pointing at the rubric when the tooling is at fault. Criterion 7 can never be computed.
  - The owner's decision that "a killed turn fails its run" is settled. The defect is that the moderator produces killed turns out of normal turn ends.
  - The unknown reason also reads "assistant messages after the last result" when no result exists at all.
- **Fix:** after a turn ends, let its harness finish for a bounded time before killing the group. The token is already dead (`bbp mcp` refuses calls), so this is safe. Alternatively, separate "killed after a protocol end" from "killed mid-turn". This may need the owner's word, since it touches the killed-turn rule.
- **Missing test:** a scripted harness that ends its turn through MCP and then prints a `result` must come out `complete` and its run succeeded. Today no test covers this.

### F2 (P1): A resumed evidence run counts, judged only by its last invocation

- **Where:** `moderator.rs:615` (a resume with the same cap passes), `moderator.rs:639` (the marker records only the cap and the price-file sha, not the build or any segments), `pairs.rs:472` and `pairs.rs:482`.
- **Probe (executed):** a capped run stopped at its 2 s wall limit in Planning, then a capped resume of the same task. The resume was accepted and printed Planning at 2022 ms. `mod_facts` gave `spec_wall_ms=2022, spec_boundaries=1`, treating the start as exact. The task actually spent about 4 s in Planning.
- **What goes wrong:**
  - **K1 still open:** the first segment's `bbp` binary is recorded nowhere. A segment run by a dirty or older build, finished by a clean HEAD build, passes `check_builds`.
  - **Wrong `spec_wall`:** it covers only the last segment, which the code calls exact. Criterion 3 reads a shortened wall as measurable, so the decision rests on a wrong measurement.
- **Fix:** either refuse to count an evidence run that had more than one `bbp mod` invocation, or record each invocation (build sha, start time, first state seen) in the marker or log and have `pairs` require all of them clean and identical. A resumed run's Planning start must not count as exact.
- **Missing test:** a resumed capped run must be refused by `pairs`, or flagged with its full wall time.

### F3 (P1): Grade sealing is undone by running `release` again

- **Where:** `grade.rs:372-381` overwrites `release.json` and `unused.json` with no check that a release already happened. `join_sealed` (`grade.rs:480`) trusts whatever hash `release.json` holds.
- **Probe (executed), using the head's `bbp` binary:**
  1. `release` with approved scores S1 (the treatment met 1 of 3, pass mark 2), then read `unused.json`.
  2. Edit to S2 (the treatment now meets 2). `join` with S2 is refused, as intended.
  3. `release` again with S2, then `join`: exit 0, treatment `rubric_ok: true`.
- **What goes wrong:** approved scores edited after the unused versions were seen flip a run's success. The design note and the fragment say `join` makes this impossible.
- **Fix:** make the release one-shot. Take the held file away (rename it into the release), and refuse when it is missing or `release.json` exists. Record the hashes of the pack and the held file in `release.json`, and have `join` check them. State in the note what a local tool cannot prevent (for example, copies).
- **Missing test:** a second release with different approved scores must be refused.

### F4 (P2): The "clean" check honours git config that hides untracked files

- **Where:** `build.rs:37` and `pairs.rs:667` both use `git status --porcelain`, which follows `status.showUntrackedFiles`.
- **Probe (executed):** the PR's own test `a_new_untracked_file_makes_the_binary_dirty` fails when run with `GIT_CONFIG_GLOBAL` set to `status.showUntrackedFiles=no`: it gets the bare sha, not `-dirty`.
- **What goes wrong:** an untracked file the build reads (for example an untracked `.cargo/config.toml`) produces a binary labelled clean. This is a bounded fail-open, contrary to the fragment's claim that an untracked file reports `-dirty`.
- **Fix:** pass `--untracked-files=normal --ignore-submodules=none` in both places.
- **Test:** run `build_sha.rs` with that config set; it must stay green.

### F5 (P2): The evidence marker is written before the task lock

- **Where:** `moderator.rs:372-373`; `check_evidence_marker` writes the marker at `:613` before `lock` is taken.
- **Probe (executed):** an uncapped moderator holds a task that has no grant yet. A capped `bbp mod` on the same task fails with "another moderator holds task T1" but has already written the marker. `require_evidence_marker` then accepts the task, which the uncapped moderator runs with no live cap, no pin check and no TTL pins.
- **What goes wrong:** this breaks the documented rule that "only a task nothing has run on can start as an evidence run". The post-hoc model and cost checks in `pairs` still apply, which is why this is P2.
- **Fix:** take the lock first, then check and write the marker.
- **Test:** the scenario above must leave no marker.

### F6 (P2): A line caught half-written stops the run as over the cap

- **Where:** `tokens.rs:435-444` counts any line that starts with `{` and does not parse as bad, including an unterminated last line. `enforce_cap` turns that into `StopKind::Cap`, and `pairs` sets `over_cap`.
- **Probe (executed):** a harness that writes one JSON line in two pieces, two seconds apart, was stopped with "cannot enforce the cost cap … 1 line(s) of JSON could not be read".
- **What goes wrong:** a healthy run fails at random, which feeds criterion 2's failure counts.
- **Fix:** while the run is live, ignore a final line with no trailing newline. Keep failing closed on it at the terminal check.
- **Test:** the split-write harness above must not stop the run.

## Design questions for the owner

- **Unused versions sit beside the pack.** `unused.held.json` and `key.json` are written in plain text to the same directory as `pack.json`. "Held back" depends on the grader being given only `pack.json`. Should `pack` write them somewhere else?
- **The moderator's environment is not scrubbed.** Variables such as `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK` or proxy settings in the moderator's environment reach the harness unchecked. The pins check covers only the launcher text.
- **Several `result` lines in one log.** "The last result accounts for the whole session" has no captured fixture behind it.

## Optional improvements

- `bin/bbp.rs:157-158` reads the criteria file twice, once for the prices and once for the sha. Read it once.
- `join` reports only the approved-scores grader. The unused-scores grader is dropped.
- `grade.rs` indexes `scores.met[&u.id][*i]`, which assumes a key file nobody edited by hand.

## Checks performed

| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings` | pass |
| `cargo test --locked -p rusty_bbp_host --no-fail-fast` | 233 passed, 1 failed |
| `python3 .github/scripts/changes.py check` | pass (124 fragments) |
| `unwrap` / `expect` / `panic!` in new library code | none found |

- **The one test failure** is `runner_e2e::sandboxed_workload_reaches_no_socket_endpoint`. My sandbox refuses `bind` on a Unix socket (EPERM). The test is outside the diff and is a validation gap, not a pass. Every other binary passed: lib 113, build_sha 6, moderator_e2e 30, pairs_load 9, review_findings 18, terminal_cap 7, and the rest.
- **New tests at the base SHA:** I did not run them. They use APIs that do not exist at the base (`pins.rs`, `run_to_stop` and others), so they cannot compile there.
- **Not run:**
  - `orch_bbp_acceptance.py`: Orch is untouched.
  - Clippy and tests for the other crates: untouched.
  - Any real provider.

## Validation gaps

- Confirming F1 against real Claude Code needs a real run, which spends money and needs your go. The README's documented order of events already shows the same thing.
- Landlock and socket tests need a kernel and sandbox that allow them.
- Whether `modelUsage` is cumulative across several `result` lines needs a captured multi-result log.
