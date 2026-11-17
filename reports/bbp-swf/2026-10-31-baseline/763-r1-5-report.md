<!-- bbp-swf/763-r1 run 5, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 278 s, 1410171 tokens, $1.33 -->

## Review of Rusty-Mill/rusty_mill PR 763: BBP run guards

**Verdict: changes required.** Three P1 findings: two let a run built from the wrong code count as pinned evidence, and the new dollar cap can be turned off by one stray byte. There are also four P2 findings. The decision-table tests are correct.

- **Base:** `5dbb633f9f2db1459002fc8fefee59eb4f9f2298`
- **Head:** `2b31b4e637088adf15227f8b039a8981f6a6a3db`. I fetched `pull/763/head` and it matches.
- **Scope:** one commit, the `rusty_bbp_host` crate plus one change fragment.

### Blocking findings

**1. P1: `bbp pairs` checks its own build commit, not the commit of the `bbp mod` that produced the runs.**
- **Where:** `src/pairs.rs:519-528` (`load` passes `crate::BUILD_SHA` to `pin`) and `src/pairs.rs:375-378` (`ModOut` only parses `state_ms`).
- **What happens (source-traced):** each `bbp mod` now prints `bbp_build_sha`, but nothing ever reads it. A manifest whose runs were made by an older build, a dirty build, or a build from before the PR (no `bbp_build_sha` at all) is accepted, as long as the binary running `bbp pairs` was built from HEAD. The output then records that binary's commit as `bbp_build_sha`, as if it described the runs.
- **Why it matters:** this is the replay-drift case. The fragment says "`bbp mod` prints it as `bbp_build_sha` and `bbp pairs` refuses unless it equals the checkout's HEAD"; the code doesn't do that.
- **Fix:** parse `bbp_build_sha` from every run's `mod_output`. Refuse a run where it is missing, `unknown`, `-dirty`, or not equal to the pinned HEAD.
- **Test that should go red without the fix:** a `load` with a `mod_output` carrying `"bbp_build_sha":"<other sha>"`, plus one with the field missing, next to a valid run that still passes.

**2. P1: `build.rs` labels a binary built from uncommitted code as the clean HEAD.**
- **Where:** `build.rs:24-28`. The only rebuild triggers are `<git-dir>/HEAD`, `<git-dir>/index` and `build.rs`.
- **What happens (executed):** I built a scratch crate using this exact `build.rs`.
  1. Committed and built three times; it printed `6b22378d… v1`.
  2. Edited `src/main.rs` without running any git command, then ran `cargo run`; it printed `6b22378d… v2-UNCOMMITTED`.
  3. The source was rebuilt but the build script was not re-run, so the dirty binary claims the clean commit. If the edit is then reverted, the checkout is clean at that HEAD and `pin` passes.
- **Same gap elsewhere:** editing `rusty_bbp` or any other dependency source has the same effect.
- **Why it matters:** this defeats the "-dirty when not clean" claim in `lib.rs:19` and in the fragment.
- **Fix:** make the build script re-run whenever any input source changes. Either declare `rerun-if-changed` on every workspace source directory that goes into the binary, or emit `rerun-if-changed` for the branch ref and drop the narrowing so the script re-runs every build. Otherwise, document that the recorded commit is only trustworthy for a clean, from-scratch build, and enforce that in the run script.
- **Test:** an integration test that edits a tracked file after a build, rebuilds, and expects `-dirty`. Or a CI step that builds from a fresh checkout and compares.

**3. P1: the live cost cap treats an unreadable turn log as $0 (fails open).**
- **Where:** `src/moderator.rs:447-458` (`enforce_cap`) together with `src/tokens.rs:322-325`.
- **What happens (executed):** `tokens::of` skips any turn whose log `read_to_string` cannot read and only counts it in `without_output`. `enforce_cap` never looks at `without_output`. Since stderr and stdout share the log (`moderator.rs:509,521-522`), one non-UTF-8 byte anywhere in a harness's output makes that whole turn cost $0 for every later poll.
- **Probe run in a scratch copy:**
  - Planner prints `\377` to stderr, then a 1M-output-token message ($20), with a $1 cap: the result was `moderator: wall limit reached in state Planning`. The cap never fired.
  - The control probe, without the stray byte, gave `cost cap reached ($20.0000 over $1.0000)`.
- **Same blind spot:** a harness whose output has no usage lines at all (no stream-json) also reads as $0.
- **Why it matters:** the fragment says the cap "fails closed". The type's own doc (`tokens.rs:288-290`) says such a turn's cost is "unknown, not zero".
- **Severity:** I graded this P1 rather than P0 only because the design keeps the API key's spend limit as a separate hard stop. Within `bbp mod`, money is spent past the cap.
- **Fix:**
  - In `enforce_cap`, fail the run when `without_output > 0` for any turn whose harness has been launched and whose log exists or should exist.
  - Better, read the log with lossy UTF-8 in `tokens::of`.
- **Test:** the probe above (stray byte, then a priced message, then expect "cost cap reached" or "cannot enforce").

**4. P2: an over-cap run that closes in the same poll is reported as a success.**
- **Where:** `src/moderator.rs:340-357`.
- **What happens (source-traced):** the `Closed | Cancelled` return comes before the cap check. Spend that crosses C during the last poll interval of a run that then closes returns `Ok`, and `bbp pairs` never compares cost with C.
- **Why it matters:** the fragment says "over C, kills the harnesses and fails the run".
- **Fix:** run `enforce_cap` (or a final cost check) before returning `Ok`. Report an over-cap closed run as failed.
- **Test:** a scripted planner that prints a priced message and then closes the task within one poll.

**5. P2: a capped run makes the whole evaluation refuse instead of becoming a failed pair.**
- **Where:** `src/bin/bbp.rs:244-246` (an `Err` goes to stderr only) and `src/pairs.rs:396-413` (`spec_wall_ms(&text)?` on an empty `mod_output`).
- **What happens (source-traced):** `load` returns an error, so the failed-pair rule ("a run that does not close is a failed run", as the fragment claims) is never reached. The wall-limit path already behaves this way; this PR adds a new path into it and documents the opposite.
- **Fix:** print the outcome JSON, including `bbp_build_sha` and the failure cause, on the cap and wall paths too. Have `facts` mark such a run as not closed.

**6. P2: the `reads_per_turn` check reads the final card, not the value the task was opened with.**
- **Where:** `src/pairs.rs:412` (`st.budget.reads_per_turn`).
- **What happens (source-traced):** `BudgetExtended` can set any field, including `ReadsPerTurn`, at any time (`rusty_bbp/src/engine.rs:1261-1266`, `state.rs:811-812`). A run opened at 10 and raised to 30 partway through passes a manifest of 30, even though its early turns ran under a different budget from its pair.
- **Why it matters:** the manifest field's doc says "every run must have been opened with".
- **Fix:** take the value from `TaskOpened`'s budget, and refuse any `BudgetExtended` on that field.
- **Test:** a run with an extension event, plus an unextended neighbour that still passes.

**7. P2: the docs now disagree with the code.**
- `docs/bbp-concurrency-design.md`, section "Cost cap for the run", says the cap "cannot stop a run in flight". Section "Pinning the run to a commit" says `bbp pairs` "cannot prove the `bbp` binary came from it".
- `crates/apps/rusty_bbp_host/README.md:29` doesn't list `--max-usd`/`--criteria`, and doesn't mention the new required manifest field `reads_per_turn`.

### Questions for the owner (not findings)

- **Unapproved run-plan items.** "Run plan proposals" is marked *not approved, not built*. That plan has C checked after each run, enforcement outside the code, and C set only after the pilot. This PR builds a different mechanism, a live in-host cap. Was building it approved?
- **The extra-role model rule** (`pairs.rs:446-451`) is set by the PR. A treatment `planner2` may use any model the control used, for example the coder's model. The second spec author can then run on a different model than the control's only spec author, which the criteria's "same … model" seems to rule out. Should the extra author be required to use the planner's model?
- **Other budget fields.** `check_settings` checks only `reads_per_turn`. The criteria require the same budget and brief in both arms, but messages, bytes, turns, iterations and the brief hash are not compared.

### Optional improvements
- `enforce_cap` re-reads every log on every poll. That's fine at these sizes; caching by length would be cheaper.

### Checks performed

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | passed |
| `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings` | passed |
| `cargo test --locked -p rusty_bbp_host --no-fail-fast` | lib 54 passed, mcp_e2e 7, moderator_e2e 14 (including the 3 new cap tests), shadow_log 6, why 13, why_live_turns 4. runner_e2e: 20 passed, 1 failed |
| `python3 .github/scripts/changes.py check` | 124 fragments ok |
| New library code for `unwrap`/`expect`/indexing | none that can panic |
| Probes (scratch copy) | build-SHA staleness (finding 2, reproduced); cost cap with a non-UTF-8 byte (finding 3, reproduced); control probe fires the cap as expected |

On the new tests:
- They can't pass at the base, because they use symbols that don't exist there (`check_settings`, `cost_cap`, `pin`'s third argument).
- The `pin` test passes the build commit in by hand, so neither `build.rs` nor the runs' recorded commit is tested anywhere.

### Validation gaps
- **runner_e2e failure:** `sandboxed_workload_reaches_no_socket_endpoint` panicked at `UnixListener::bind` (`runner_e2e.rs:1137`) because my sandbox blocks binding Unix sockets. The PR doesn't touch that test. It needs a rerun on a host without that restriction.
- `orch_bbp_acceptance.py` was not run; this PR doesn't touch orch.
- No real provider was run.
