<!-- bbp-swf/932-r1 run 1, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 258 s, 1142589 tokens, $1.12 -->

# Review of Rusty-Mill/rusty_mill PR 932: `bbp mcp` session servers and the `wait` tool (event-based step 3a)

- **Base:** `1703450af78b2c70ecd0c4b18f8fa5c3ec117235`
- **Reviewed head:** `e0137a43f74e089ac0bf241bcff62494cca47c77` (I fetched `pull/932/head` and it matches this SHA.)

**Verdict: changes required.** Two P2 findings block: a missing test for the main stale-turn rule, and docs that the diff makes false. The code itself held up against every attack I tried.

## What I checked the code against

- **Stale authority:** a session acts only in `shown`. `shown` changes only inside `wait`, and the token is built for that turn (`mcp.rs:432-433`, `:563-583`). The engine checks it against the role's live turn (`engine.rs:256-279`). A call from before the grant gets "you hold no turn yet". A call after the shown turn ended gets "turn N has ended", even when the role already holds a newer turn. I ran a scratch probe of that last case at head and the call was refused.
- **Replays (R15):** these happen before the token check (`engine.rs:198-202`). So when a session replays an operation using the token of a different turn, the original answer still comes back.
- **Launchers:** they still refuse a session binding (`launchers.rs:206-209`). No spending or real-provider path is opened.
- **Errors:** an error from `wait` or `driver.sync()` is passed back as a tool error, not swallowed. The new code adds no `unwrap`, `expect` or panicking indexing.

## Blocking findings

### 1. [P2] No test for a stale call after the same role is granted a new turn
- **Where:** `crates/apps/rusty_bbp_host/tests/mcp_e2e.rs:629-640`; the rule lives at `src/mcp.rs:432-433`.
- **Why it matters:** the README and the change fragment promise that "a call from an inference that began before the grant is refused". The case that matters most: the session was shown turn N, N ended, and the same role was then granted N+1 without calling `wait`. The PR's test only covers N ending while a different role (the coder) holds the next turn. In that case `held()` is `None`, so the test cannot tell "act in `shown`" apart from "act in whatever turn the role holds now".
- **Executed:** in a scratch copy I changed `turn()` to `self.binding.turn.or(self.shown.and_then(|_| self.held()).or(self.shown))`.
  - All 9 `mcp_e2e` tests, including both new ones, still passed.
  - My probe aborted turns until the planner was granted again (`first=1 regranted=2`). The stale `post` was accepted: `{"Posted":1}`, `err=false`.
  - Without the mutation, the probe passes: the stale call is refused, then `wait` returns `turn 2` and a fresh post succeeds.
- **Fix:** add that test: the session is shown turn N, N ends, the role is regranted, `post` without `wait` is refused, then `wait` and `post` succeed. It should fail under the mutation above.

### 2. [P2] Docs and comments the diff makes false
- `crates/apps/rusty_bbp_host/src/lib.rs:3-4` still says "the per-turn MCP server… One process serves one turn". A session server serves a whole task.
- `README.md:13` still says "Five tools: …", but line 11 of the same README now says a session has a sixth tool, `wait`.
- `src/broker.rs:7-9` says the broker serves "the ordinary per-turn [`Server`]". But `broker::broker` takes `Binding::from_env()` (`bin/bbp.rs:114`), which now accepts `BBP_SESSION=yes`. So `bbp mcp-broker` will serve a session to a confined harness. The change fragment says sessions are "not yet confined (3c)".
  - **Fix:** either have the broker refuse `binding.turn == None` until 3c, or document that it accepts sessions.
- `src/mcp.rs:580`: the comment says "A replay after the turn ended still names the turn it was made in". For a session, the token belongs to the turn last shown, which can differ from the turn the operation was made in. This is harmless only because replays are matched before the token check; the comment should say so.

## Design questions (for the owner)

1. **Approval and scope of this step.** The plan says each step is "one PR each … each behind its own owner approval". Its status line records only "step 2 approved". This PR:
   - implements step 3,
   - splits it into 3a/3b/3c,
   - edits the owner-questions section to record "Answered 2026-10-23: 4 concurrent editors on the same artifact" (`docs/bbp-event-based-plan.md:101-102`). That is a step-4 parameter, and nothing in the repository backs the answer.

   Please confirm the step-3 go, the split, and that answer. The answer is outside this PR's step.
2. **`held` returns at once (`mcp.rs:465`).** The tool description says "call wait again whenever you have nothing to do". An agent that has finished but still holds its turn gets `held` straight back each time. Each retry costs a model inference until the turn ends. This hurts a real client once 3b launches sessions. Consider blocking until the held turn ends, or telling the agent how to end its turn.
3. **`wait` blocks the stdio loop for up to 600 s.** During that time the server cannot answer `ping`, act on `notifications/cancelled`, or notice stdin closing. A session whose client or moderator has died lives up to 600 s more. This is acceptable while nothing launches sessions, but 3b needs it settled under the process-lifetime rule. The client's MCP tool timeout against a 600 s `wait` is unvalidated.

## Optional improvements
- A `timeout_s` that is not an unsigned integer (`"5"`, `5.5`, `-1`) silently becomes 60 s. Refusing it would be clearer.
- No test checks that a provider launcher refuses `BBP_SESSION=yes` (`launchers.rs:209`), even though the change fragment states it.
- An unconfined harness inherits the moderator's environment (`moderator.rs:1022-1029` adds variables but doesn't clear any). A stray `BBP_SESSION=yes` there now makes every per-turn `bbp mcp` and launcher exit with "set exactly one". That fails safe but stops progress. `env_remove("BBP_SESSION")` at launch would avoid it.

## Checks performed
- `cargo fmt --all --check`: passed.
- `python3 .github/scripts/changes.py check`: passed (247 fragments OK).
- `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings`: passed. I used a scratch `CARGO_TARGET_DIR`, because the default target directory is read-only here.
- `cargo test --locked -p rusty_bbp_host --no-fail-fast`: everything passed except `runner_e2e::sandboxed_workload_reaches_no_socket_endpoint`. That test panics at `bind: Operation not permitted` because my sandbox forbids Unix socket binds. The diff doesn't touch it; this is a validation gap, not a pass.
- Head's `mcp_e2e.rs` run against base code: both new tests fail there and the other 7 pass, so the new tests do guard the change.
- Scratch probe and mutation (finding 1): executed as described above.
- Not run:
  - `orch_bbp_acceptance.py`, since the diff doesn't touch Orch.
  - The confinement tests that are `#[ignore]`d.
  - Any real provider or MCP client.
  - Other crates, since the diff only touches `rusty_bbp_host`.

## Validation gaps
- How Claude Code and Codex behave with a tool call that blocks for up to 600 s, including client-side timeouts and cancellation. This needs a real client, and is due in 3b.
- The socket-endpoint runner test needs an environment that allows Unix socket binds.

**Verdict: changes required.**
