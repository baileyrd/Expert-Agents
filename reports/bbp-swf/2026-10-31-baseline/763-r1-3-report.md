<!-- bbp-swf/763-r1 run 3, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 237 s, 1066750 tokens, $1.18 -->

**PR 763 review: changes required.** I found two P1 and four P2 defects.

- **Repository:** Rusty-Mill/rusty_mill, PR #763
- **Base:** `5dbb633f9f2db1459002fc8fefee59eb4f9f2298`
- **Head:** `2b31b4e637088adf15227f8b039a8981f6a6a3db`. I fetched the PR head and it matches. The PR is one commit, `2b31b4e63`.

## Blocking findings

### P1-1. `bbp pairs` checks its own binary, not the binary that ran each run
- **Where:** `crates/apps/rusty_bbp_host/src/pairs.rs:374-377` (`ModOut` reads only `state_ms`), `:388-415` (`facts`), `:519-528` (`load`).
- **What happens (source-traced):** `bbp mod` now prints `bbp_build_sha` (`bin/bbp.rs:145-151`), but nothing reads it back. `load` calls `pin(…, crate::BUILD_SHA)`, which is the commit of the `bbp` process running `pairs`.
- **Failure scenario:** A manifest lists runs whose `mod_output` says `"bbp_build_sha":"<old sha>-dirty"`, or says `unknown`, or has no such field because the run was made with a binary from before this PR. One example is a build without the #727 verdict-binding fix. If `bbp pairs` itself is rebuilt from the pinned clean HEAD, it accepts all of these runs and records `bbp_build_sha` = HEAD in its output. The same gap applies to a run resumed by a second `bbp mod` built from a different commit.
- **Why it matters:** The fragment says `bbp pairs` "refuses runs that are … not pinned". The code does not do that, so runs that should be void count as evidence and the output certifies a commit they did not run.
- **Fix:** Parse `bbp_build_sha` in `ModOut`. Refuse any run whose value is missing, `unknown`, ends in `-dirty`, or differs from the pinned HEAD.
- **Regression test:** a `load` or `facts` test with a `mod_output` whose sha is a different commit, one with `-dirty`, and one with no field, each refused; plus a matching neighbour that passes.

### P1-2. `build.rs` reports a clean commit for a binary built with uncommitted code
- **Where:** `crates/apps/rusty_bbp_host/build.rs:24-28`.
- **Cause:** Rerun triggers are only `<git-dir>/HEAD`, `<git-dir>/index` and `build.rs`. Declaring any `rerun-if-changed` turns off Cargo's default of rerunning when package files change. Editing a tracked file touches neither HEAD nor the index, so Cargo recompiles the crate but keeps the old `BBP_BUILD_SHA`.
- **Executed:** I copied this `build.rs` into a scratch crate in a scratch git repo with a clean tree at `c99eeff…`:
  - The clean build printed `c99eeff… v1`.
  - I edited `src/main.rs` without committing and rebuilt. It printed `c99eeff… v2-UNCOMMITTED`, with no `-dirty`.
  - Running `git status` and rebuilding gave the same result.
- **Failure scenario:** Build `bbp` in one checkout with uncommitted edits (for example a changed threshold in `pairs.rs`), and point the manifest's `repo` at a clean checkout of the same HEAD. `pin` passes, and the output certifies HEAD for code HEAD does not contain. The `-dirty` guarantee in the fragment and in the comment at `build.rs:1-3` does not hold. Once P1-1 is fixed, every recorded run sha would depend on this too.
- **Fix:** Also rerun when the package's sources change: `rerun-if-changed=src`, plus `Cargo.toml` and the workspace crates it depends on. Otherwise, embed a hash of the sources. And watch the ref file `HEAD` points to (or `packed-refs`), not just `HEAD`.
- **Regression test:** an integration test or script that builds after a tracked-file edit and expects `-dirty`.

### P2-1. The extra-author model check accepts a second author on a different model
- **Where:** `pairs.rs:438-453`.
- **Rule it should follow:** The criteria (`docs/bbp-concurrency-design.md:232`) say the N=2 arm differs only by the second spec author, with the "same … model". The check only requires the extra role's model to appear somewhere in the control arm.
- **Executed:** In a scratch copy I added a probe test. Control was `planner=opus` and `coder=sonnet`; treatment added `planner2=sonnet`. `check_settings` returned `Ok(())`, so a pair whose authors use different models counts as comparable, and the criterion-7 cost ratio is affected.
- **Fix:** Compare an extra role's model with the role it duplicates, i.e. the control's spec author(s) that serve `planning`. If that pairing cannot be determined, refuse.
- **Regression test:** the probe above, flipped to expect a refusal.

### P2-2. The live cost cap is skipped when the run closes, and the cap is not recorded
- **Where:** `moderator.rs:338-357`, `bin/bbp.rs:125-136`.
- **Closed before cap (source-traced):** The `Closed`/`Cancelled` return comes before `enforce_cap`. A run that goes over C and closes within the same poll returns `Ok(Closed)` and counts as closed. Another run with the same cost that is still open at the next poll is killed and counted as failed. The fragment says "over C … fails the run".
- **Cap not recorded:** Neither `--max-usd` nor the criteria sha is printed in the `bbp mod` output, and `pairs` cannot see them. Arms run with different caps, or one with no cap, are accepted, even though the cap now decides whether a run fails.
- **Fix:** Check the cap on the terminal path as well, or have `pairs` apply "over C is failed" afterwards. Print the cap and the criteria sha in the output, and make `pairs` require the same values in both arms.
- **Regression test:** a capped run whose harness closes the task in the poll where it goes over C, expected to fail.

### P2-3. The design doc is now wrong on two points
`docs/bbp-concurrency-design.md` is unchanged by this PR:
- Lines 321-324 say the per-run cap "cannot stop a run in flight" and "Enforcement is outside the protocol … No code".
- Line 311 says `pairs` "cannot prove the `bbp` binary came from it".

The code now does both of these, partly (see P1-1 and P1-2). Update the run plan, marking these as proposals that wait for owner approval, or narrow the fragment.

### P2-4. The wiring in `load` and `facts` is untested
- **Executed:** In the scratch copy I deleted `check_settings(&pairs, m.reads_per_turn)?;` from `load`. All 25 `pairs` tests still passed.
- **Also untested:** how `facts` builds `role_models`, and the `bbp_build_sha` field in the `mod` output.
- **Where tests belong:** a `load`-level test in `pairs.rs` with a small task dir and manifest. It should cover a `reads_per_turn` mismatch, a model mismatch, and a wrong build sha (once P1-1 is fixed).

## Questions for the owner (not findings)
1. **Live cap:** The approved run plan has a post-run check of C, enforced outside the code. This PR adds a live kill, which turns over-cap runs into failures. That is a run rule set without a recorded approval. Since N=2 costs more by design, a per-run cap kills treatment runs more often, which biases the comparison. Is a live cap wanted, and should it apply equally to both arms?
2. **One model per role:** `tokens.rs:231-234` already expects a turn to use more than one model (helper agents). The new rule refuses the *entire* evaluation if one role used two models in one run. One Claude Code helper-agent call after money has been spent would make the whole screen unevaluable. Should that rule exist, and should it void one pair rather than the whole evaluation?
3. **Manifest field:** `reads_per_turn` is now required. Older manifests fail to parse, which fails safe, but the manifest format is not documented anywhere.

## Optional improvements
- `enforce_cap` re-reads every event and every harness output on each poll, so it costs more as a run grows. Tracking per-turn offsets would fix that.
- If a role's turns all produced no assistant messages in one arm, that role gets an empty model set. That mismatches the other arm's model and refuses the evaluation; it could be reported as "unobserved" instead.

## Checks
- `cargo fmt --all --check`: passed.
- `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings`: passed.
- `cargo test --locked -p rusty_bbp_host --no-fail-fast`: everything passed except one test.
  - Passed: lib 54 (all `pairs` and decision-table tests), mcp_e2e 7, moderator_e2e 14 (including the three new cap tests), shadow_log 6, why 13, why_live_turns 4.
  - Failed: runner_e2e `sandboxed_workload_reaches_no_socket_endpoint` panicked at `tests/runner_e2e.rs:1137`, when `UnixListener::bind` returned an error. This file is not touched by the PR and the failure looks like this sandbox blocking socket binds. I did not re-run it at base, so that cause is my judgment, not tested.
- `python3 .github/scripts/changes.py check`: 124 fragments ok.
- **Probes I ran:** the scratch `build.rs` crate (P1-2); a scratch-copy probe test plus the `load` mutation (P2-1, P2-4).
- **New tests at base:** the cap tests and the settings and pin tests do not compile at base, so they guard the change. The decision-table tests exercise `decide()`, which this PR does not change, so they would pass at base. They add coverage but guard no change in this PR.

## Validation gaps
- I did not run `orch_bbp_acceptance.py`; the PR doesn't touch Orch.
- The runner_e2e socket test needs a host that allows Unix socket binds.
- I didn't check the claim that "mutating the order of two rows fails three tests".
- No real provider was run.

## Verdict: **changes required**
