<!-- bbp-swf/932-r1 run 2, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 183 s, 628738 tokens, $0.74 -->

# Review of PR 932 (Rusty-Mill/rusty_mill): `bbp mcp` session servers and the `wait` tool, event-based step 3a

- **Base:** 1703450af78b2c70ecd0c4b18f8fa5c3ec117235
- **Head reviewed:** e0137a43f74e089ac0bf241bcff62494cca47c77 (the fetched `pull/932/head` matches; one commit)

**Verdict: changes required.** The central safety claim of the new session server is false: an agent can act in a turn before it has seen that the turn was granted. I reproduced this with a test.

## What this PR touches, and what must hold
- **Session binding** (`Binding.turn: Option`, `BBP_SESSION=yes`): an agent may act only in a turn whose grant it has seen. The token stays bound per turn, as rule R14 in the spec and the README's "invocation-bound token" require. If this breaks, work an agent composed earlier lands in a later turn.
- **`wait`:** the deadline is bounded, the task ending wakes it, and it holds no authority itself.
- **Per-turn servers and provider launchers:** behaviour unchanged. Launchers still require `BBP_TURN`.
- **R15 replay:** still answerable after the turn ends. I checked this: `engine.rs:197-200` runs the replay check before checking the token, so it does not matter which token the session sends with a replay.

## Blocking findings

### 1. P1: an action composed before the grant was seen still acts in that turn
- **Where:** `crates/apps/rusty_bbp_host/src/mcp.rs:468` (`self.shown = Some(held)`), `:564` (the live-turn check), and `:666-667` (the server reads requests one after another).
- **Docs it makes false:** README.md:11 ("a call from an inference that began before the grant is refused") and the module doc at `mcp.rs:13-15`.
- **How it happens:** the model sends `wait` and `post` in one message (parallel tool calls). The client writes both requests to stdin before reading either reply. The server handles `wait` first, which sets `shown` to the new turn. It then handles the queued `post`, which passes the check at `:564` and is committed with that turn's token. That `post` was written before the model saw the grant, which is exactly the stale-authority case the design says it closes.
- **Executed:** I added a probe to a scratch copy, `probe_pipelined_post_behind_wait_acts`. It sends `wait`, then `post`, without reading in between. Result: `wait={"woke":"turn","turn":1,...}` and `post={"Posted":1}` with `err=false`.
- **Required fix:** bind each acting call to proof that the model saw the grant. One option: `wait` returns a per-grant nonce, or the turn id, and every acting tool in a session must echo it. A call that lacks it, or carries a different one, is refused with "call wait". Another option: discard requests that were already buffered when `wait` replied.
- **Regression test:** the probe above, asserting that the pipelined `post` is refused. Next to it, a `post` sent after reading the `wait` reply and carrying the nonce must still be accepted. The probe is red at this head.

### 2. P2: a session server outlives its client by up to the `wait` timeout (600 s)
- **Where:** `crates/apps/rusty_bbp_host/src/mcp.rs:473-501`.
- **How it happens:** while `wait` blocks, nothing reads stdin, so the server does not notice the client has gone away.
- **Executed:** the probe `probe_eof_during_wait` has the coder role, which holds no turn, call `wait` with `timeout_s=8`, then closes stdin. The server exited 7.7 s after the close.
- **Scope:** the server cannot act during that time, because the next thing it reads is EOF. But it keeps its store handle and inotify watch for up to 10 minutes after its harness is gone. This matters once step 3b launches sessions, especially if the moderator ever relies on the server exiting.
- **Required fix:** in the wait loop, also poll stdin (for EOF or a hangup) and the parent process. Alternatively, have step 3b document and test that the moderator kills the process group.
- **Regression test:** close stdin during a long `wait` and assert the server exits within about 1–2 s.

### 3. P2: changed behaviour with no test
None of these are tested:
- The 600 s cap on `timeout_s` (`:481`).
- A `timeout_s` that is not an integer, or is negative, quietly falls back to 60 s. Arguably it should be refused.
- A session principal with no role.

They belong in `tests/mcp_e2e.rs`, or as unit tests of a pure helper that parses the timeout. The tests the PR does add cannot pass at the base, because the base `bbp mcp` requires `BBP_TURN`.

## Question for the owner
- `docs/bbp-event-based-plan.md:101` now reads "Answered 2026-10-23: 4 concurrent editors on the same artifact." This is a step-4 parameter recorded inside a step-3a PR, with no attribution or link showing the owner gave that answer. Please confirm it is your decision, or move it to its own record. It is outside the step this PR claims.

## Optional improvements
- `mcp-broker` now also accepts `BBP_SESSION=yes` through `Binding::from_env`. A relayed `wait` would block the broker for up to 600 s. Until step 3c, consider refusing session mode in the broker.
- The new comment at `mcp.rs:579` ("A replay after the turn ended still names the turn it was made in") is wrong once `shown` has moved to a later turn. It does no harm because the core checks for a replay before the token, but it should be reworded.

## Checks performed
| Check | Result |
|---|---|
| `cargo fmt --all --check` (scratch copy of head) | pass |
| `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings` | pass |
| `cargo test --locked -p rusty_bbp_host` (probes skipped) | lib 153, mcp_e2e 9, moderator_e2e 43 (3 ignored), admission 4, launchers 6, pairs_load 10, build_sha 6, confined_harness 3 (3 ignored) all pass |
| `tests/review_findings.rs`, `a_one_hour_run_is_priced_to_the_harness_reported_cost` | Fails about 2 runs in 3, at the base as well as the head; the PR does not touch it. It is an existing flaky test, not a finding here. |
| `python3 .github/scripts/changes.py check` | 247 fragments ok |
| Probes in the scratch copy | `probe_pipelined_post_behind_wait_acts` red (finding 1); `probe_eof_during_wait` shows the server living 7.7 s after the client closed (finding 2) |
| New library code scanned for `unwrap`, `expect` and indexing that can panic | none found |

## Validation gaps
- I did not test whether Claude Code or Codex actually send parallel MCP calls down one stdio pipe before reading replies. Settling that needs a live client run with real model traffic, which I did not do. Finding 1 stands either way, because the server accepts the crafted input that its own docs say it refuses.
- `orch_bbp_acceptance.py` was not run; this PR does not touch Orch.
- Both confined-sandbox tests marked ignored were skipped, so Landlock confinement was not checked. Nothing in the diff changes confinement.

**Verdict: changes required.**
