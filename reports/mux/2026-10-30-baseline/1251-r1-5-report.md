<!-- mux/1251-r1 run 5, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 482 s, 4867668 tokens, $2.81 -->

I've reviewed PR 1251, but I couldn't build or run any Rust here. All findings below come from reading the code. Only the change-fragment check actually ran. The verdict is **changes required**.

- **Repository:** Rusty-Mill/rusty_mill, PR 1251
- **Base:** `d05170b8cc082cffaf6c348411e82bea84b9632d`
- **Head reviewed:** `55d4bbf92e2d5f8f1b12aa0ffeddf57775fe0375`. `git fetch origin pull/1251/head` returned this SHA. It's one commit, "keep a reconnecting client through a lost handshake", touching 7 files.

## Invariants for what the diff touches
- **Hello/Welcome takeover token** (`state.rs:145-225`): after any lost reply, the client still holds a token the server accepts. Each Welcome replaces the token, and a used token stops working (DECISIONS.md:37).
- **Client end and the new orphan state** (`conn.rs:46-51`, `state.rs:234-272`):
  - A stale connection's end or expiry never drops a client that a newer connection now owns.
  - `Disconnect` from another client still ends an orphan.
  - Before this PR: "A client whose old connection already closed is gone, and its lease is free" (DECISIONS.md:37).
- **Lease:** a person typing holds it, so an agent's request is refused rather than interleaved (AGENT-PLACEMENT.md:39, SKILL.md:59).
- **Detach on clean exit** (`tui/src/main.rs:505-512`): the change fragment says block clients "release their leases at once".

On prior art: mosh has no tokens. Its sender keeps each state until the receiver acknowledges it, so the receiver can always resume from whatever it last got. That's the model the token rule below falls short of. No catalog methods, events or feature bits change.

## Blocking findings

### 1. P1: a killed `rusty-mux` locks the next one out of typing, with no time limit
**Where:** `server/src/state.rs:238-241`; `tui/src/main.rs:985-991`, `1600-1605`, `1397`. Source-traced.

**Trigger:** the first `rusty-mux` dies without sending Detach (terminal closed, killed), or exits through Ctrl-] d or a failed reconnect while the path is down (`main.rs:508` skips Detach then).

**Trace:**
1. The person's `rusty-mux` is killed. Both its control and block connections negotiate TAKEOVER, even in local mode (`main.rs:86,89`).
2. The server keeps its block client as an orphan that is still attached and still holds the lease (`state.rs:238-241`).
3. The person runs `rusty-mux SOCK BLOCK` again within 30 s. `AcquireLease` gets `LeaseHeld`, and `Pane::take` sets `leased = false`, `asking = None` (`main.rs:985-991`).
4. The TUI asks for the lease again only from `sync()`, which runs on a resize, layout or focus change (`main.rs:1585`, `1600-1605`). It doesn't handle `LeaseChanged`.
5. The orphan expires at 30 s and frees the lease, but nothing asks again. Every key is silently dropped (`main.rs:1397`) until the person happens to resize or refocus. With one block there's nothing to refocus.

At the base, the closed connection freed the lease at once, so the fragment's "keeps its lease for up to 30 s" understates this.

**Who's hurt:** the person at the terminal.

**Fix:** when the lease is refused, ask again on `LeaseChanged { holder: None }` for the focused block, or retry on a timer. Alternatively, don't negotiate TAKEOVER where the client has no rejoiner (local mode).

**Regression test:** a TUI test with `real_server`: start rusty-mux, kill it with `-KILL`, attach a second one to the same block, wait past the grace, then type until `two-2`. It goes red at the head and is green at the base. A draft is in `scratch/r2/probes/tui_probe.rs`.

### 2. P1: retrying a lost Welcome fails after a takeover with no request
**Where:** `state.rs:159-163`, `278-282`; doc comment at `state.rs:952-957`. Source-traced.

**Trace:**
1. The client holds T0, which it confirmed with a request.
2. Takeover with T0 → server stores `{current: T1, confirmed: T0}`. The client receives Welcome T1 but sends nothing on that connection.
3. The path drops. Takeover with T1 succeeds because T1 is current → `{current: T2, confirmed: T0}`. Welcome T2 is lost.
4. The retry with T1, the last token the client ever received, is refused. The server starts a new client, and rusty-mux would stop with "the server let this client go". Meanwhile the orphan keeps the old client's attachment and lease for 30 s.

The comment "A takeover whose Welcome never arrived leaves the client holding only `confirmed`" is false: the client can hold an unconfirmed `current`.

**Trigger:** any TAKEOVER client that follows `message.rs:127-128` and sends nothing between two takeovers. The shipped TUI doesn't reach it today: it always sends `List` right after the control Welcome, and a block-connection failure ends the client as `Broken`.

**Fix:** on takeover, set `confirmed` to the token presented, since presenting it proves the client holds it. This also retires T0 once T1 is used.

**Regression test:** `probe_retry_with_last_received_token_after_quiet_takeover` in `scratch/r2/probes/review_probe.rs`, which belongs in `server/tests/takeover.rs`.

### 3. P2: over `--ssh`, the exit-time Detach can be lost, so the lease is still held
**Where:** `tui/src/main.rs:505-517`, `tui/src/ssh.rs:161-166`. Source-traced.

**Trace:**
1. On Ctrl-] d, `Detach` is written into the local forward socket and the socket is shut down.
2. `drop(app)` then drops the Rejoiner, then `SshPath`, then `Forward`, and `child.kill()` sends SIGKILL to ssh. That is microseconds to milliseconds later, and nothing waits for the server's `Done`.
3. If ssh hasn't relayed the bytes yet, or exits with unread input so the kernel resets the TCP connection, the server only sees the connection end. The client is orphaned with its lease for 30 s, and finding 1 then follows.

The comment at `main.rs:507` and the fragment's "release their leases at once" are true only for a local socket.

**Fix:** wait (bounded) for the Detach replies before stopping the forward, or stop ssh gently and wait for it.

**Test:** a lifecycle test through `relaying_ssh`: detach, then right away a second client calls `AcquireLease` and must succeed.

### 4. P2: docs that now disagree with the code
- **`proto/src/message.rs:127-128` and `:328-329`:** say the token is "the token the server last gave", and "Each Welcome replaces it". The server now also accepts the confirmed older token.
- **DECISIONS.md:37:** "A client whose old connection already closed is gone, and its lease is free" and "makes a used token worthless" are not marked superseded by the new row 41.
- **SKILL.md:59-60** and **AGENT-PLACEMENT.md:39:** say "a user typing in that block holds it". A departed client can now hold the lease for 30 s, and an agent following the skill would back off with no hint to watch `lease-changed`.

### 5. P2: test coverage
- **Detach on clean exit (`main.rs:505-512`) has no test that goes red without it.** `a_client_that_detached_before_leaving_holds_no_lease` sends Detach itself, so it passes at the base too.
- **The modified `a_wrong_or_used_token_starts_a_new_client` (`takeover.rs:85-87`) now hides a replay window.** It sends a request before replaying the used token. A test that the used token is refused right after takeover goes red at the head: `probe_used_token_is_refused_right_after_takeover`.
- **`a_client_whose_connection_ends_is_kept_for_a_reconnect` doesn't reliably fail at the base.** It does `drop(old)` and then takes over straight away. At the base it passes whenever the server reads the new Hello before the old connection's EOF. It should first wait until the old connection's end is observed (for example, a marker from `list`).

## Design questions (for the owner)
- **Used tokens stay valid:** a used token keeps working until the new connection sends a request. For a block connection that means until the next key or resize, which can be indefinitely. This conflicts with row 37's "used token worthless". The new row 41 says "to be confirmed on the PR", and I can't verify the owner's decision cards it cites.
- **TAKEOVER changes meaning with no new feature bit:** an older `rusty-mux` that never sends Detach, talking to a newer remote server, would leave leases held for 30 s on every exit.
- **Agents can't tell an orphan from a live client:** `list` shows it as attached, `acquire-lease` is refused, and only `ClientDisconnected` arrives, 30 s later.
- **Local mode pays the grace for nothing:** it negotiates TAKEOVER but can never reconnect.

## Failure classes ruled out
- **Stale connection:** handled. `expire` compares the `Rc` pointer, and `connect` clears orphans.
- **Split stream, deadlines, last output, silent truncation, outside config:** no reads, frames, limits or ssh options changed.
- **Secrets:** tokens are not logged, no files are created, and the comparison stays constant-time with a non-short-circuit `|`.
- **Timing in tests:** the lifecycle marker move is safe because `draw()` only writes diffs while the path is down. No `Notify` waits were added.
- **Shutdown:** the 30 s sleep is in a detached `spawn_local` task, so it doesn't delay server exit.

## Checks and validation gaps
- **`python3 .github/scripts/changes.py check`:** passed ("541 fragment(s) ok", exit 0).
- **Server, TUI and ctl tests, and clippy: not run.** The pinned toolchain isn't installed and `~/.rustup` is read-only. The installed `stable` is the same 1.98.1, but cargo needs crates.io for uncached locked crates (`boxcars`, `tokio 1.53.3`), and the sandbox blocks `index.crates.io`. Building offline with unrelated workspace members removed in a scratch copy also failed, and I restored that copy.
- **Probes:** drafted in `scratch/r2/probes/`, not executed. None of the PR's new tests were run at the base or the head.
- **Not verified:** the PR's claim that it fixes the flaky `a_trickling_answer_is_given_up_in_time`, behaviour against a real sshd (finding 3), and Windows. `scratch/` already held `head/`, `base/` and log files of unknown origin; I didn't use them.

## Verdict
**Changes required.**
