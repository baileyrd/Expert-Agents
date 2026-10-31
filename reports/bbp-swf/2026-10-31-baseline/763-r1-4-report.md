<!-- bbp-swf/763-r1 run 4, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 328 s, 1431581 tokens, $1.42 -->

I found five blocking defects in PR 763, so the verdict is **changes required**. The PR adds a build-commit pin, a live dollar cap, comparability checks in `bbp pairs`, and tests for the decision table. The pin checks the wrong binary, and the cost cap can be bypassed in two ways.

- **Repository:** Rusty-Mill/rusty_mill, PR #763
- **Base:** `5dbb633f9f2db1459002fc8fefee59eb4f9f2298`
- **Head reviewed:** `2b31b4e637088adf15227f8b039a8981f6a6a3db`. I fetched `pull/763/head` and it matches. The PR is one commit and only touches `rusty_bbp_host`.

## Blocking findings

### 1. P1: `bbp pairs` checks the commit of the binary running `pairs`, not the binary that ran each run

**Where:** `crates/apps/rusty_bbp_host/src/pairs.rs:375-378` (`ModOut` reads only `state_ms`), `:387-415` (`facts`), `:519-528` (`load`).

**Failure (source-traced):** `bbp mod` writes `bbp_build_sha` into its output (`bbp.rs:145-151`). Nothing ever reads it back. A search of the repo finds no other reader.
- `load` compares `crate::BUILD_SHA` against the checkout's HEAD. That is the commit of whatever binary is running `bbp pairs` now.
- Suppose all 26 runs were made with a `-dirty` binary, an `unknown` one, a stale one, or one from before this PR that records no SHA at all. Evaluating them later with a clean rebuilt `bbp` passes the pin. The output then reports `bbp_build_sha` = the evaluator's commit, which makes those runs look pinned.
- The fragment says "`bbp mod` prints it as `bbp_build_sha` and `bbp pairs` refuses unless it equals the checkout's HEAD". The code does not enforce that for the runs.

**What it should do:** refuse any run whose saved `mod_output` lacks `bbp_build_sha`, or whose value differs from `git_sha`.

**Fix:** add `bbp_build_sha` to `ModOut` (required, not defaulted) and check it in `facts`/`load` against the pinned HEAD.

**Regression test:** a `load`/`facts` test with a `mod_output` that has `"bbp_build_sha":"<head>-dirty"`, one with the field missing, and a valid neighbour that matches HEAD and passes.

### 2. P2: the reported build SHA can be stale, so an uncommitted edit can still pass the pin

**Where:** `crates/apps/rusty_bbp_host/build.rs:24-28`.

**Failure (executed):** I copied this exact `build.rs` into a scratch crate in a scratch git repo.
1. Commit, build twice. The SHA is clean.
2. Edit a source file without committing, then `cargo run`. The binary includes the edit (`v9-EDIT`) but still reports the clean SHA with no `-dirty`.

The cause: `rerun-if-changed` only watches `.git/HEAD` and `.git/index`. A working-tree edit touches neither, so the build script does not re-run while the crate does recompile. If the edit is reverted before `bbp pairs`, the checkout is clean, HEAD matches, and the pin accepts a binary built from code that was never committed.

One more gap from the same cause: a commit on a branch changes `refs/heads/<branch>`, not `HEAD`, so a new commit is caught only if the index also changes. That part is source-traced; in my probe the index did change.

**Fix:** make the build script re-run whenever the package source or the git refs change, e.g. `rerun-if-changed` on the source directories plus the ref files, or always re-run.

**Regression test:** build, edit a tracked file without touching git, rebuild, and assert `BUILD_SHA` ends in `-dirty`.

### 3. P2: a run that closes within one poll of crossing the cap returns `Ok(Closed)`

**Where:** `crates/apps/rusty_bbp_host/src/moderator.rs:336-357`. The `Closed | Cancelled` return comes before `enforce_cap`.

**Failure (executed):** in a scratch copy I took the scripted happy-path e2e test and set `--poll-ms 2000` and a $1 cap. The reviewer harness prints a $20 assistant message before its verdict, and the scripted human closes the task within the poll. Result: `Ok(Outcome { state: Closed, turns_launched: 4, .. })`, while `tokens::of` reports `usd=Some(20.0)`.

The fragment claims that over C the cap "kills the harnesses and fails the run". The design says a run over C counts as a failed run (`docs/bbp-concurrency-design.md:322`). The return value that `pairs` sees says success, and `bbp pairs` has no check against C.

**Fix:** run `enforce_cap` (or a final cost check) before returning `Ok` on the terminal path, and return `Err` when the cost is over C.

**Regression test:** that same probe: a slow poll, a costly last turn, and a fast human close should give `Err("cost cap reached")`.

### 4. P2: unreadable harness output counts as $0 for the cap

**Where:** `moderator.rs:447-458` (`enforce_cap` uses `t.usd` and ignores `t.without_output`), relying on `tokens.rs:322-325`.

**Failure (executed):** in a scratch copy, a planner launcher runs `printf '\377\n' >&2; echo '<$20 assistant line>'; sleep 60` under a $1 cap.
- The moderator sends stderr to the same `turn-N.log` (`moderator.rs:521-522`).
- `read_to_string` fails on the invalid UTF-8.
- `tokens::of` then treats the turn as `without_output`, which the cap reads as $0.
- Result: `moderator: wall limit reached in state Planning`, not "cost cap reached".

The control probe, without the stray byte, fires the cap as expected. Any read error other than "file not found" should fail closed, the same way an unpriced model already does.

**Fix:** in `enforce_cap`, fail when a launched turn's log cannot be read (or when `without_output > 0` for a turn the moderator launched). Or read the log lossily as bytes.

**Regression test:** the probe above, expecting the cap to fire.

### 5. P2: a run in which a role never printed an assistant message refuses the whole evaluation

**Where:** `pairs.rs:394-401` (`or_default()` creates an empty model set for that role) and `:440-447`.

**Failure (executed, unit test in scratch):** control `planner={m1}`, treatment `planner={}` gives `Err("p: role planner used {\"m1\"} in the control and {} in the treatment")`.

The host README describes this exact case: an expired token forfeits every turn and the harness prints no assistant messages. The criteria say a failed pair "is reported, excluded from every median" and counts toward criterion 2. Here, one such run blocks `bbp pairs` for the entire screen, so the failure count is never reported.

**Fix:** compare only roles with at least one observed model, or skip the comparison for pairs that already failed, and leave the failure to the failed-pair rule.

**Regression test:** this probe, plus the existing "differs" case, which must still refuse.

### 6. P2: one statement in the design doc is now wrong

`docs/bbp-concurrency-design.md:322` says the per-run cap "cannot stop a run in flight, only the key's limit does". After this PR, `bbp mod --max-usd` can stop a run in flight. The doc should say what the live cap does and where it fails open (findings 3 and 4).

## Questions for the owner

- **Extra-role model rule.** `check_settings` accepts an extra N=2 role on any model the control used anywhere. In a mixed roster, the second spec author could therefore run on the tester's cheaper model, and the two arms would differ in more than the treatment. The criteria only say "same … model". Which model the extra author must use is a rule for the owner, not the PR.
- **Live cap vs the plan.** The "Cost cap for the run" section sits under "Run plan proposals (not approved, not built)", and its item 4 says enforcement is the key limit plus `--max-wall-secs`. Should the run plan adopt `--max-usd`, and should a run it kills count as a failed run? The fragment says it does.
- **Helper models.** "Exactly one model per role" refuses any turn that used a helper agent on another model. `tokens::by_model` says this can happen. Today `--tools ""` and the Coder's tool list make it unlikely, but `--model` does not pin a subagent's model.

## Optional improvements

- `an_inconclusive_criterion_is_never_a_build` does not cover `noise_gate` or `c3`. Today `c3` absorbs an inconclusive noise gate, but `decide` never checks `noise_gate == Holds` or `c3 == Holds` before it returns "build".
- The decision-table tests compile and should pass at base too, since `decide` is unchanged. They are new coverage of existing behaviour, not tests of a change. I did not check the fragment's claim that "mutating the order of two rows fails three tests".
- `enforce_cap` re-reads every event and every turn log on every poll. That is fine at this scale.

## Checks

| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings` | pass (target dir moved to `$TMPDIR`, the default one is read-only here) |
| `cargo test --locked -p rusty_bbp_host --no-fail-fast` | lib 54/54, `mcp_e2e` 7/7, `moderator_e2e` 14/14 (includes the three new cap tests), `shadow_log` 6/6, `why` 13/13, `why_live_turns` 4/4 pass; `runner_e2e` 20/21 |
| `runner_e2e::sandboxed_workload_reaches_no_socket_endpoint` | fails at `runner_e2e.rs:1137` at **both** base and head, so it comes from this environment, not this PR |
| `python3 .github/scripts/changes.py check` | 124 fragments ok |
| `unwrap` / `expect` / panicking indexing in new library code | none |
| Probes (scratch clone, outside the reviewed tree) | finding 4: cap did not fire; finding 3: `Ok(Closed)` at $20 over a $1 cap; finding 5: whole evaluation refused; finding 2 (toy crate with the PR's `build.rs`): stale clean SHA |

**Validation gaps:**
- The socket-endpoint sandbox test cannot run here, so it is neither a pass nor a fail.
- I did not run `orch_bbp_acceptance.py`. The diff does not touch `orch-bbp`.
- I did not run a real provider. Whether real Claude Code turns ever use more than one model (the helper-model question) needs a recorded stream-json from a turn that uses a subagent.

## Verdict

**Changes required.**
