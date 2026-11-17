<!-- bbp-swf/932-r1 run 3, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 278 s, 1216998 tokens, $1.00 -->

**PR review: Rusty-Mill/rusty_mill #932, "bbp mcp: session servers and the wait tool" (event-based step 3a)**

The PR claims a session server refuses any call from an inference that began before the grant. I sent such a call through a scratch test and the server accepted it, so the verdict is **changes required**.

- Base: `1703450af78b2c70ecd0c4b18f8fa5c3ec117235`
- Reviewed head: `e0137a43f74e089ac0bf241bcff62494cca47c77` (`git fetch origin pull/932/head` gives this SHA; one commit)

## Blocking findings

### 1. P1: a call sent before the grant acts in the granted turn (*executed*)

**Where:** `crates/apps/rusty_bbp_host/src/mcp.rs:468` (`self.shown = Some(held)`), checked at `mcp.rs:564`. The claim being contradicted is in `README.md:11` and the module doc at `mcp.rs:11-14`.

**The rule the PR states:** a session acts only in a turn that `wait` has shown, "so a call from an inference that began before the grant is refused".

**What the code does:** `shown` is set when the server *builds* the `wait` reply, not when the client has read it. The server handles one request at a time from stdin. So a request the client wrote while `wait` was blocked sits in the input buffer and is handled after `shown` has moved to the new turn.

The same gap opens without any pipelining:
- A client can abandon a long `wait` (`timeout_s` up to 600) because of its own tool-call timeout.
- `notifications/cancelled` is dropped (`mcp.rs:599`), and the server could not see it during the blocking `wait` anyway.
- The abandoned `wait` still sets `shown`, so the model's next call acts in a turn it never saw.

**Executed trace:** I wrote a scratch test in a copy of the head, using the PR's own helpers. The steps:
1. The planner session waits, posts, and its turn is aborted. The coder now holds the turn.
2. The planner client sends `wait{"timeout_s":30}`, then at once sends `post{"op":"pre",...}`. Both are written before any grant to the planner exists.
3. The coder asks the planner, and the coder's turn is aborted, which grants the planner a turn.
4. The `wait` returns `turn`. The `post` that was written before the grant returns `{"Posted":3}` with `isError` false. It should have been refused with "you hold no turn yet; call wait".

The engine's token check (R14) does not catch this. The turn really belongs to the role, and the server signs the call with that turn's token. The only thing meant to stop it is `shown`, and that does not stop it.

**Required correction:** tie authority to proof that the model received the reply. One option: `wait` returns a fresh per-grant nonce, and every acting tool must echo it; the server refuses a missing or stale nonce. A turn number alone can be guessed, because turn ids are sequential. Alternatively, narrow the README and module doc to what the code actually enforces, but then the property this step relies on is gone.

**Regression test:** the trace above, added to `tests/mcp_e2e.rs`. It must go red at this head. Add a neighbour that stays green: a `post` sent after `recv_call(pending)` is accepted.

### 2. P2: changed behaviour without tests

- **Launcher refusal:** `launchers.rs:209` makes a provider launcher refuse a session binding ("a provider launcher serves one turn"). No test covers it. One belongs in `launchers/tests.rs` or an e2e launcher test: `BBP_SESSION=yes` with no `BBP_TURN` is refused, and `BBP_TURN=N` still passes.
- **Session replay after the turn ends:** a session server's token now comes from the shown turn (comment at `mcp.rs:~580`). No test covers an exact replay of an accepted op after the shown turn has ended, or a changed payload under that op being refused (`OpConflict`). The new session test never replays an op. The existing replay tests only use per-turn servers.

## Design questions for the owner

1. **Narrower `wait` in the plan.** `docs/bbp-event-based-plan.md` changes step 3's `wait`. Before, it woke "when something the role may read is appended … or the role is granted a turn". Now it wakes on grants only. The owner approved this plan, so the narrowing needs the owner's agreement, not just the PR's.
2. **Recorded owner answer.** The plan now says owner question 2 was "Answered 2026-10-23: 4 concurrent editors on the same artifact". This PR is the only place that answer appears. Please confirm the answer is the owner's; it is also outside step 3a.
3. **Lifetime of a blocked session server.** While `wait` blocks, the server cannot see stdin close. A session whose client has died lives up to 600 s longer. Nothing launches sessions yet, but 3b will need a rule for how a session server stops.

## Optional improvements

- `lib.rs:4` still says "One process serves one turn".
- If the moderator's own environment carries `BBP_SESSION`, the broker it spawns (`confine.rs:226-236`) now gets both variables and fails to start: `Binding::from_env` errors with "set exactly one". That fails closed, but removing `BBP_SESSION` in that spawn would avoid it.
- A `timeout_s` given as a float or a negative number silently falls back to 60 s. Refusing it would be clearer.

## Checks performed

I ran everything in a scratch worktree at the head, using a separate cargo target directory because the shared one is read-only here. I removed the worktrees afterwards.

| Check | Result |
|---|---|
| `cargo fmt --all --check` | pass |
| `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings` | pass |
| `cargo test --locked -p rusty_bbp_host` | all suites pass except one, below |
| `runner_e2e::sandboxed_workload_reaches_no_socket_endpoint` | fails with `bind: Operation not permitted`; my sandbox blocks Unix sockets, and the PR does not touch this code |
| `python3 .github/scripts/changes.py check` | pass (247 fragments ok; the PR's fragment is present) |
| PR's two new tests run at the base | both red, so they guard the change |
| Probe for finding 1 | accepted the pre-grant post (red against what the PR claims) |
| New library code: `unwrap`, `expect`, panicking indexing | none in `mcp.rs` or `launchers.rs` |

## Validation gaps

- **Socket confinement test:** the runner test above needs a host that allows Unix sockets.
- **Acceptance script:** I did not run `orch_bbp_acceptance.py`.
- **Real client timeouts:** I did not check how Claude Code or Codex time out a long MCP tool call, for example Codex's tool timeout setting, which I believe defaults to 60 s. That decides how often the abandoned-`wait` path of finding 1 happens in practice. A scripted client is enough to settle it; no real provider is needed.

## Verdict

**changes required**
