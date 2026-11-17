<!-- bbp-swf/932-r1 run 4, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 415 s, 1355738 tokens, $1.17 -->

**Changes required.** One executed defect: the session gate fails open. Three more P2 findings cover a departure from the owner-accepted plan, docs that no longer match the code, and missing tests.

**Review of PR 932, Rusty-Mill/rusty_mill**
- Base: `1703450af78b2c70ecd0c4b18f8fa5c3ec117235`
- Reviewed head: `e0137a43f74e089ac0bf241bcff62494cca47c77`. I fetched `pull/932/head` and it equals both the expected head and the checkout. One commit.

## Blocking findings

**1. P2: a cancelled or timed-out `wait` still counts as shown, so an inference that never saw the grant can act in it.** `crates/apps/rusty_bbp_host/src/mcp.rs:468` (`self.shown = Some(held)`), `:596-600`, `:631`.
- **What the code does:** `woke()` marks the turn as shown when it builds the reply, not when the model receives it. The server is single-threaded and drops notifications. It never sees `notifications/cancelled` while blocked, and it answers the cancelled request anyway.
- **Scenario (executed, scratch probe in a copy of the head):**
  1. The planner session calls `wait` with a 60 s timeout.
  2. The client sends `notifications/cancelled` for that request, as on its own timeout or a user interrupt.
  3. The coder asks the planner a question and the turn rotates.
  4. The server still answers the cancelled request: `{"woke":"turn","turn":3,...}`. The client discards it.
  5. The planner then sends `post` from an inference that never saw turn 3. It is accepted: `{"Posted":3}`.
- **Why it matters:** the README now says "a call from an inference that began before the grant is refused." In this case it is not. The core cannot catch it, because the token is valid for the role's live turn (`engine.rs` `check_token`). A trigger is plausible: the 60 s default equals the usual MCP client request timeout.
- **Correction:** require each acting tool call to carry the turn number that `wait` returned, and refuse a mismatch. Only an inference that actually read the reply can know that number. Optionally also honour cancellation by reading stdin during a wait.
- **Regression test:** the probe above, added to `tests/mcp_e2e.rs`. It goes red at the head.

**2. P2: the code differs from the owner-accepted plan, and the PR rewrites the accepted text to match.** `docs/bbp-event-based-plan.md:53-57`.
- The plan says "Status: accepted by the owner, 2026-10-22". At the base, its definition of `wait` reads: "blocks until something the role may read is appended after its cursor, or the role is granted a turn, then returns what changed."
- The code wakes on grants only and returns no "what changed". The PR changes the accepted definition to "wakes on grants only" without a recorded owner approval.
- The reasoning (reading also needs a turn) may well be right, but it is a change to a settled decision.
- **Correction:** restore the accepted text and record the change as a proposal for the owner, or cite where the owner approved it.

**3. P2: docs that the change makes false.**
- `README.md:11` still opens "`bbp mcp` serves exactly one turn".
- `README.md:13` still says "Five tools".
- `README.md:126` still says only "the per-turn `bbp mcp` server, which derives the token for its own turn". A session derives tokens for every turn of its role.
- The comment at `mcp.rs:580` ("A replay … still names the turn it was made in") is false for sessions: the token is for the last shown turn. This has no effect on behaviour, because the core ignores the token on a replay (`engine.rs:197-199`).

**4. P2: missing tests for changed behaviour.**
- Provider launchers now explicitly refuse `BBP_SESSION=yes` (`launchers.rs:206-209`), and no test covers it. A test belongs in `tests/provider_launchers.rs`, with a valid `BBP_TURN` case beside it that still launches.
- The README claims `wait` is woken by the task log's file watch (inotify). The test's 10 s bound also passes with the 1 s polling fallback alone, so the watch is never tested. It needs an assertion of a wake well under 1 s on Linux.
- The 600 s cap on `timeout_s` is untested.

## Questions for the owner
- **Owner answer with no source.** `docs/bbp-event-based-plan.md:101` records "Answered 2026-10-23: 4 concurrent editors on the same artifact." Nothing in the repository records that answer. It is also step-4 material inside a step-3a PR, against the plan's one-step-per-PR rule. Is it the owner's answer?
- **Step 3 approval.** The plan's status line says only that step 2 is approved. Has step 3 (3a) been approved?
- **Possible busy loop.** While a turn is held, `wait` returns `held` immediately. The tool's description tells the model "call wait again whenever you have nothing to do". A model that waits instead of passing will loop, paying for an inference on each call, until the turn's deadline. This matters once 3b launches sessions.

## Optional improvements
- While `wait` blocks, `ping` goes unanswered for up to 600 s. A server whose client has died lives up to 600 s, without authority.
- A `timeout_s` that is not an integer (`"30"`, `30.5`) silently falls back to 60 s.
- `bbp mcp-broker` accepts a session binding. The moderator never passes one, so this is unreachable today, but the fragment says sessions are "not yet confined".

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings` | pass |
| `cargo test --locked -p rusty_bbp_host --no-fail-fast` | all pass except two, below |
| `python3 .github/scripts/changes.py check` | pass (247 fragments) |
| New tests at the base (head `mcp_e2e.rs` copied in) | both new tests fail there, so they guard the change |
| No `unwrap`, `expect` or panicking indexing in the new library code | confirmed |

The two test failures at the head are in files this PR doesn't touch:
- **`runner_e2e::sandboxed_workload_reaches_no_socket_endpoint`** fails identically at the base: the socket `bind` fails in this sandbox.
- **`review_findings`** is flaky: on 5 runs at the head it failed twice, on a different test each time, and passed 3 times. It passed in the one run at the base.

## Validation gaps
- Whether Claude Code or Codex time out or cancel an MCP tool call before 600 s. Settling this needs a real provider with a long `wait`; I did not run one.
- 3 Landlock-confinement tests are ignored here; that is not a pass.
- I did not run `orch_bbp_acceptance.py`. Nothing in it launches sessions.

**Verdict: changes required.**
