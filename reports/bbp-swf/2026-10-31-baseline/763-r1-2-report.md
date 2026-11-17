<!-- bbp-swf/763-r1 run 2, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 270 s, 1372456 tokens, $1.27 -->

# Review of PR 763 (Rusty-Mill/rusty_mill): BBP run guards

- **Base:** `5dbb633f9f2db1459002fc8fefee59eb4f9f2298`
- **Reviewed head:** `2b31b4e637088adf15227f8b039a8981f6a6a3db` (`FETCH_HEAD` of `pull/763/head` is this SHA; one commit)
- **Verdict: changes required.** Three blocking findings: one P1, both P2. The P1 is that `bbp pairs` never checks which binary produced the runs, despite what the change fragment says.

## Blocking findings

### 1. P1: `bbp pairs` checks its own build commit, not the commit of the `bbp mod` that produced the runs
- **Where:** `crates/apps/rusty_bbp_host/src/pairs.rs:375-380` (`ModOut` only reads `state_ms`), `:520-528`, `:491`.
- **What the PR claims:** the fragment says "`bbp mod` prints it as `bbp_build_sha` and `bbp pairs` refuses unless it equals the checkout's HEAD". The changelog says the binary's build commit must equal the clean checkout's HEAD.
- **What the code does:** `load` compares only `crate::BUILD_SHA`, which is the commit of the binary running `bbp pairs`. The `bbp_build_sha` that each run's saved `mod_output` now contains is never read. *Source-traced.*
- **Failure scenario:** run all 26 `bbp mod` invocations with a `bbp` built before #727 (the verdict-binding fix that `min_commit` exists to enforce), or from a dirty tree. Their outputs have no `bbp_build_sha`, or the wrong one.
  - Then build `bbp` fresh at the clean pinned HEAD and run `bbp pairs`.
  - It passes the pin and writes `bbp_build_sha` = HEAD into the evaluation. The output now looks like proof that the runs used the pinned build.
  - These runs should be void, but they count toward the decision.
- **Related doc drift:** `docs/bbp-concurrency-design.md:311` still says the pin "cannot prove the `bbp` binary came from it". So the doc and the fragment disagree, and the code matches neither one's implied guarantee.
- **Fix:** parse `bbp_build_sha` in `ModOut`. Make it required: a missing field refuses. Refuse when it differs from `git_sha` for any run in either arm.
- **Regression test:** a `load` test with two runs.
  - A `mod_output` with `"bbp_build_sha":"<other sha>"` refuses.
  - A `mod_output` with no such field refuses.
  - Its neighbour, whose sha equals HEAD, passes.

### 2. P1: a run that goes over the cap in its last poll closes as a success
- **Where:** `crates/apps/rusty_bbp_host/src/moderator.rs:338-357`.
- **What the PR claims:** the fragment says that over C the moderator "kills the harnesses and fails the run". The design doc's cost-cap rule 2 (`docs/bbp-concurrency-design.md:322`) says "a run over `C` counts as a failed run".
- **What the code does:** the `Closed | Cancelled` return comes before `enforce_cap`. *Source-traced.*
- **Failure scenario:** the final planner turn prints usage that pushes the run past C, then the task reaches `Closed` before the next poll.
  - `moderator::run` returns `Ok(Closed)` and the cap is never evaluated.
  - `bbp pairs` has no cap check of its own, so the run counts as closed and successful.
  - Meanwhile a run that crossed C one poll earlier is killed and counted as failed.
  - This feeds criterion 2's failure counts per arm and criterion 7's cost, depending only on when the crossing happened. This is a check that runs on one path only.
- **Fix:** run `enforce_cap` (or a final cost check) before returning on the terminal path, and report the over-cap status. Or record the cap and refuse over-cap runs in `bbp pairs`.
- **Regression test:** a moderator e2e test where a scripted planner prints an over-cap message and then drives the task to `Closed`. Expect `Err("cost cap reached…")`, or an outcome flagged over the cap.

### 3. P2: the live cap fails open when a turn log cannot be read (an unknown cost is read as $0)
- **Where:** `moderator.rs:447-451`, which reads through `tokens.rs:322`.
- **What the code does:** `tokens::of` skips any turn whose log `read_to_string` fails, and only counts it in `without_output`. `enforce_cap` ignores `without_output` and does `t.usd.unwrap_or(0.0)`.
  - The harness's stderr goes to the same log (`moderator.rs` spawn).
  - So one non-UTF-8 byte from the launcher, or a read that lands in the middle of a multi-byte character, prices the whole turn at $0.
- **Executed:** in a scratch copy, I used the PR's own `$20` message under a `$1` cap, plus `printf '\377' >&2`.
  - Result: `moderator: wall limit reached in state Planning`. The cap never fired.
  - The same setup without the stray byte stops at "cost cap reached".
- **Why P2, not higher:** the API key's spend limit is still the hard stop.
- **Fix:** in the cap, treat any read error other than `NotFound` as "cannot enforce the cost cap". Also read the log lossily (`fs::read` + `from_utf8_lossy`) so one bad byte doesn't hide the usage lines.
- **Regression test:** the probe above (`probe_one_non_utf8_byte_in_the_turn_log_hides_the_cost_from_the_cap`), which fails at the head.

### 4. P2: "exactly one model per role" is checked as "at most one"
- **Where:** `pairs.rs:431`.
- **What the code does:** it tests `ms.len() > 1`, so a role with an empty model set passes.
  - This happens when the role's turns printed no assistant message with usage, for example a non-stream-json launcher.
  - If the role is empty in both arms, the sets compare equal and the pair is accepted as "same model".
- **Executed:** in a scratch unit test, `model_pair(&[("planner", &[])], &[("planner", &[])])` returned `Ok(())`.
- **Why it matters:** this contradicts the fragment's "observed, not declared" claim and the doc comment "each role uses exactly one model".
- **Fix:** require `ms.len() == 1` for every role that has turns.
- **Regression test:** the empty-set pair refuses, and the one-model neighbour still passes.

### 5. P2: the docs say less than, or the opposite of, what the code now does
- `crates/apps/rusty_bbp_host/README.md:29` and `:72` don't list `--max-usd`/`--criteria`. They still say `bbp mod` "fails after `--max-wall-secs`" as if that were the only stop.
- `docs/bbp-concurrency-design.md:311` and `:322-324` still say the cap "cannot stop a run in flight, only the key's limit does" and that enforcement is outside the code.
- **Fix:** update these, or the fragment, together with the owner question below.

## Ruled out
- **A stale `BUILD_SHA` after uncommitted edits.** *Executed* with the identical `build.rs` in a scratch crate. The script's own `git status --porcelain` updates `.git/index`, which then triggers `rerun-if-changed`. So the script re-runs on every build:
  - An edit made without touching the index was still reported as `-dirty`.
  - A new commit was picked up.
- **Peeking or blinding issues:** none touched. The decision-table tests match the doc's table order (`decide` is unchanged).

## Questions for the owner (not findings)
- **The live cap changes the approved-pending rule.** The doc's plan checks C after each run, and C is set after the pilot. The PR adds an in-flight kill that turns a run into a failed run. That changes which runs fail, so the owner should decide on it.
- **Two new comparability rules.** "One model per role per run" and "an extra N=2 role must use a model the control used" are new criteria.
  - The design doc accepts mixed rosters priced in dollars.
  - It also notes that a turn can use a helper model, such as a subagent on another model.
  - Under these rules, any helper-model use refuses the whole evaluation.
- **`reads_per_turn` as a new required manifest field** is reasonable, but it is a run setting the owner should confirm.

## Optional improvements
- Because of the index rewrite, `build.rs` re-runs and `rusty_bbp_host` recompiles on every build, and building mutates `.git/index`. Consider `git status --porcelain --no-optional-locks` and rerun on `HEAD` plus the resolved ref file.
- `enforce_cap` re-parses every turn log on every poll, which is O(total output) per poll. Fine at the planned scale.

## Checks performed
| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings` | pass (target dir moved to `$TMPDIR`; the default one is read-only here) |
| `cargo test --locked -p rusty_bbp_host --no-fail-fast` | all pass (lib 54, mcp_e2e 7, moderator_e2e 14 including the 3 new cap tests, shadow_log 6, why 13, why_live_turns 4), **except** `runner_e2e::sandboxed_workload_reaches_no_socket_endpoint`: `bind: PermissionDenied` from this review sandbox. That test isn't touched by the PR, so it's a validation gap, not a pass. |
| `python3 .github/scripts/changes.py check` | pass (124 fragments ok) |
| Scratch probes | cap with a non-UTF-8 byte: fails open (finding 3); empty model sets: accepted (finding 4) |
| `orch_bbp_acceptance.py` | not run; the PR doesn't touch Orch |
| Real provider | none run; no money spent |
| New library code | no `unwrap`, `expect` or panicking indexing |

**Validation gaps:**
- The socket-endpoint sandbox test, which needs an environment where the test can `bind`.
- The live cap against real `claude -p` stream-json timing, which needs a real provider and the owner's go.

## Verdict
**Changes required.**
