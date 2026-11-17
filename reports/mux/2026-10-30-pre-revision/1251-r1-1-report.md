# mux-expert review: rusty_mux reconnect grace (first head)

- Repository: Rusty-Mill/rusty_mill
- PR number: not given (reviewed as a commit range, blind test)
- Base: `d05170b8cc082cffaf6c348411e82bea84b9632d` (pinned with `git rev-parse`)
- Reviewed head: `55d4bbf92e2d5f8f1b12aa0ffeddf57775fe0375` (pinned; worktree HEAD equals it)
- One commit: `55d4bbf9 rusty_mux: keep a reconnecting client through a lost handshake`
- Files: `server/src/{conn,state}.rs`, `server/tests/takeover.rs`, `tui/src/main.rs`, `tui/tests/lifecycle.rs`, `docs/projects/mux/DECISIONS.md`, `changes/2026-10-30-mux-reconnect-grace.md`

## Step 1: predictions (touched items and invariants)

| Item | Invariant | Prior art |
|---|---|---|
| `Hello{takeover}` / `Welcome{token}` (token rotation, `State::connect`, `takes_over`) | Lost handshake (#1243, #1251; DECISIONS 2026-10-30 row): whatever handshake reply is lost, the client still holds a token the server accepts, and the attachment and lease come back. | mosh never rotates its credential: one session key for life, replay stopped by sequence numbers (`mosh/src/network/network.cc:483-486`), so it has no lost-rotation problem. A rotating token is only safe if every token the client can still hold is accepted until a newer one is proven. |
| Token confirmation (`State::request`, `Tokens`) | Once the client proves it has a newer token, older ones stop working (DECISIONS 2026-10-30: "a request on the new connection retires the older one"). A proof must come from the connection that is serving the client (generations). | n/a in mosh. |
| Orphan state (`disconnect`/`expire`/`orphans`) | Stale connection: only the connection that orphaned a client can expire it; a takeover in the grace cancels it; a hidden or detached block releases what it holds. DECISIONS 2026-10-30: kept 30 s with attachment and lease. | mosh-server keeps the session indefinitely by default (`MOSH_SERVER_NETWORK_TMOUT`, `src/frontend/mosh-server.cc:394-403`). tmux control mode drops a client on EOF, no grace. |
| `Disconnect` on an orphan (`state.rs:524`) | C33 (DECISIONS 2026-10-27): any client may disconnect any client but itself; the holder's lease is freed. | |
| TUI clean exit (`main.rs:505-512`) | A client that means to leave releases its lease at once (fragment, DECISIONS row). | tmux `detach` |
| Catalog / ctl / MCP | Not touched (`proto/src/schema.rs` unchanged). Observable agent effects: `list` shows orphans; `client-disconnected` arrives 30 s later for `TAKEOVER` clients; `acquire-lease` refused for up to 30 s. | |

## Blocking findings

### F1 (P2): a takeover does not record the token the client presented, so a second lost Welcome after an idle reconnect drops the client

- **File and line:** `crates/apps/rusty_mux/crates/server/src/state.rs:159-161` (confirmed is carried over from the old record); doc claim at `state.rs:952-957`.
- **Severity:** P1 at its worst (the lost-handshake class this PR fixes: the person's `rusty-mux --ssh` stops with "the server let this client go"). I lowered it one level because today's only `TAKEOVER` client reaches it only in an unusual setup: a control connection without `INSPECT`, or a block connection once the TUI retries a failed block takeover (see F3).
- **Failure scenario (executed, plus source-traced for the TUI):**
  1. Client connects with T1 and sends a request, so `confirmed = T1`.
  2. The path is lost and the reconnect succeeds: takeover with T1 gives `current = T2, confirmed = T1`. The client receives T2 and sends nothing on this connection. In the TUI a resumed block connection sends no request (`main.rs:1686-1688`: no Attach for a resumed pane; `leased` comes from the resume, so no AcquireLease), and the control connection only sends List when `plan.window` is set (`main.rs:809-816`); otherwise its first request is the `Sync` 5 s later.
  3. The path is lost again. Takeover with T2 (valid as `current`) gives `current = T3, confirmed = T1`. T1 is a token the client threw away; T2, which it just presented and still holds, is accepted by neither field.
  4. The Welcome carrying T3 is lost (the slow handshake this PR is about). The retry presents T2 and is refused, so the server starts a new client; the TUI maps this to `Missed::Dropped` and exits. After 30 s the orphan is dropped with its lease.
  - Executed: probe `probe_second_lost_welcome_after_an_idle_reconnect` (scratch `server/tests/probe.rs`). It fails at head: `assertion left == right failed: the token the client presented and still holds`, which means a new ClientId.
- **Who is hurt:** the person at the terminal, whose session ends on a flapping link.
- **Required correction:** presenting a token in `Takeover` already proves the client holds it, so on a successful takeover set `confirmed` to the presented token instead of carrying the old record's value. One line: `let confirmed = continued.and(takeover.map(|t| t.token));`. I tried this in scratch. The probe goes green and all 8 `takeover.rs` tests stay green, including `a_wrong_or_used_token_starts_a_new_client`. Also fix the `Tokens` doc comment (`state.rs:952-957`).
- **Regression test:** the probe above, added to `server/tests/takeover.rs`: take over with T1, take over again with T2 without sending a request, drop that connection, and retry with T2, which must keep the id.

### F2 (P2): a pane the TUI stops showing is hung up without Detach or ReleaseLease, so its orphan holds the block's lease for 30 s

- **File and line:** `crates/apps/rusty_mux/crates/tui/src/main.rs:1566-1570` (gone panes are `hang_up`), together with `server/src/state.rs:238-241` (every `TAKEOVER` client is orphaned with its lease). `lease()` at `main.rs:1594-1610` runs only after the gone panes are removed, so the hidden pane never sends `ReleaseLease`.
- **Severity:** P1 (a person or an agent cannot type into a block for up to 30 s, which breaks "other clients and agents may type into the blocks not in focus", `main.rs:1591-1592`, and the stale-connection rule that a hidden block releases what it holds). I lowered it one level because the trigger needs a second client to change the window's zoom, and the effect is bounded by the 30 s grace.
- **Failure scenario (source-traced in the TUI, server half executed):**
  1. The TUI shows a split window, focused on block A, and its A connection holds A's lease.
  2. An agent runs `rusty-mux-ctl zoom` on block B. `LayoutChanged` with `zoomed = B` reaches the TUI and `relayout` keeps both blocks in `staying`, so `settle` calls `sync()`.
  3. `sync()` moves focus to B (`main.rs:1563-1565`), then removes and `hang_up`s A's pane while it still has `leased = true`. No `Detach` or `ReleaseLease` is sent.
  4. The server orphans A's client with its attachment and lease for 30 s. Before this PR the hang-up dropped it and freed the lease at once.
  5. Within 30 s: the agent's `acquire-lease --block A` fails (exit 1, "lease is held by client …"). Or the agent unzooms, the person moves focus back to A, the TUI's new A connection gets `LeaseHeld`, and the person's keys into A are refused until the orphan expires.
  - Executed: probe `probe_hung_up_pane_keeps_lease`. A `TAKEOVER` block connection that ends without Detach leaves a second client's `AcquireLease` refused: `Failed { reason: "block … input lease is held by client …" }`.
- **Who is hurt:** the person at the terminal and an agent driving ctl or MCP.
- **Required correction:** when a pane leaves the view (`sync` gone panes, and `drained`), send `Detach` before `hang_up`, the same way the clean exit at `main.rs:508-512` does. Alternatively, give `Pane` a `leave()` that detaches and then hangs up, and use it everywhere except `lose()`.
- **Regression test:** a TUI lifecycle test, in `tui/tests/lifecycle.rs` with a real server. Split the window, zoom the other block from a second (ctl) client, then `acquire-lease` the first block from that client at once. It must succeed well within 30 s.

### F3 (P2): the TUI still treats a lost block Welcome as fatal, and its comments now say something false

- **File and line:** `crates/apps/rusty_mux/crates/tui/src/main.rs:178-181` ("hanging the fresh connection up ends that client"), `main.rs:808` ("from here on, a failure ends the client"), `main.rs:1159-1161` (`Missed::Broken` ends the client). The change fragment's line 5 claim ("a reconnect that gives up midway can still resume") overstates it.
- **Severity:** P2. The docs disagree with the code, and the PR's stated outcome only holds before the control Welcome.
- **Failure scenario (source-traced):** the path is lost. On the fresh path the control Welcome arrives within `HANDSHAKE` (control taken over, List sent, so the new token is confirmed and the old one retired). The trickling link then misses the shared `by` deadline on a block's Welcome (`reach`, `main.rs:858-871`). `Missed::Retry` becomes `Missed::Broken` (`main.rs:823-826`) and the TUI exits with "reconnecting: the server did not answer in time". With this PR, though, hanging up no longer ends any client: the server keeps the control and every block client for 30 s and would accept a retry. The TUI also drops `me` (the control's new, now-confirmed token) on this error path. A retry with the old `plan.me` would therefore fail as well, so retrying needs both the new `me` and F1's fix.
- **Who is hurt:** the person at the terminal on a slow link.
- **Required correction:** either make a block-stage failure retryable (return the taken-over `me` with the error, and keep each block's `Resume` token, which F1 then keeps valid), or narrow the fragment and DECISIONS row to "a reconnect that gives up before the control connection's Welcome". Either way, fix the comments at `main.rs:178-181` and `main.rs:808`.
- **Regression test:** a lifecycle test with `relaying_ssh` trickling only after the first Welcome (a relay mode that passes N bytes, then trickles). The TUI must come back with the same client ids.

### F4 (P2): the docs say a detached block client leaves at once, but it lingers for the grace

- **File and line:** `crates/apps/rusty_mux/crates/tui/src/main.rs:505-507` ("Detached, a server drops each block's client at once"), `docs/projects/mux/DECISIONS.md:41` ("so it leaves at once without its lease; its idle control client lingers"), `changes/2026-10-30-mux-reconnect-grace.md:10` ("An idle control client lingers in `list`").
- **Failure scenario (executed):** probe `probe_detached_block_client_lingers`. A `TAKEOVER` block client attaches, takes the lease, sends `Detach`, and closes. 500 ms later another client's `List` still shows it. `disconnect` (`state.rs:238`) orphans any client holding a token and ignores whether it is attached. Only the lease is released, which is what the PR's server test `a_client_that_detached_before_leaving_holds_no_lease` checks. An agent reading `list` after the person quits sees 1 + N clients for 30 s, not 1.
- **Required correction:** correct the three texts to "each block client releases its attachment and lease at once; it and the control client stay listed for the grace". Alternatively, have the server drop a detached, unleased client at once on disconnect; that changes behaviour and needs a test.
- **Regression test:** if the text changes, none. If the behaviour changes, a probe like the one above in `server/tests/takeover.rs`.

### F5 (P2): two changed behaviours have no test that goes red without them

- `tui/src/main.rs:508-512` (Detach before a clean exit). No TUI test checks that another client can take the lease right after `rusty-mux` exits. `server/tests/takeover.rs::a_client_that_detached_before_leaving_holds_no_lease` also passes against the base server (I ran it there), so it covers neither change. It belongs in `tui/tests/lifecycle.rs`: exit with Ctrl-] d, then a ctl `acquire-lease` on the shown block succeeds immediately.
- `server/src/state.rs:524` (`Disconnect` on an orphan). The fragment's known-limitations line promises "another client can still end it with `Disconnect`", but no test exercises it. It belongs in `server/tests/takeover.rs`: orphan a leased client, `DisconnectClient` it from another client, then `AcquireLease` succeeds and the id is gone from `list`.

The other new server tests do go red without the change. Against the base `state.rs` and `conn.rs`, `a_client_whose_connection_ends_is_kept_for_a_reconnect` and `a_takeover_whose_welcome_was_lost_can_be_retried_with_the_old_token` failed in 3 of 3 runs.

## Failure classes

| Class | Result |
|---|---|
| Lost handshake | F1 (executed). The PR's own case (first lost Welcome) is fixed and covered. |
| Stale connection | `serve` checks `served_by` before `State::request` (`conn.rs:62-65`, no await between them on the local runtime), so a replaced connection cannot confirm a token it never got. `expire` is keyed on the connection's own `cancel` via `Rc::ptr_eq` (`state.rs:250-257`, unit test `an_orphan_expires_only_by_its_own_connection`). A budget-evicted client is not orphaned, because `drop_client` runs first and `served_by` then returns false. Hidden block: F2. Detached block: F4. |
| Split stream | Not touched: no filter or framing change. `Frames` is unchanged. |
| Deadlines | Not touched on the server. The TUI's clean-exit `Detach` is a blocking `conn.send` of one small frame to the local ssh forward. It could only block if ssh stopped reading with a full channel window, which keystroke-sized traffic will not reach. Ruled out. |
| Last output | Not touched. `drained` and the closing paths are unchanged. |
| Silent truncation | Not touched. |
| Outside config | Not touched. No ssh invocation or path change. |
| Secrets | No new logging. `Tokens` derives `Debug`, but nothing formats `State` or `Tokens`. No files written. The wider replay window (an old token stays valid until the new connection's first request) only matters to someone who can already reach the owner-only socket, and they can already `disconnect-client` anyone (DECISIONS 2026-10-27, C33). Ruled out as a finding. |
| Timing in tests | The `lifecycle.rs` change marks output before waiting for the login: a correct fix for a race. `without_the_feature_a_client_goes_with_its_connection` polls `List` against a deadline (20 ms sleeps between real checks, bounded by `PATIENCE`), which is acceptable. The new takeover tests pass whichever of the old connection's EOF and the new Hello the server handles first. No `Notify` wait was added (the orphan map stores the `Rc<Notify>` only as an identity). |

## Agent contract

No catalog method, event or feature bit changed (`proto/src/schema.rs` and `ctl` untouched). Exit codes are unchanged. Effects on agents: F2 (`acquire-lease` exits 1 for up to 30 s) and F4 (`list` shows stale clients). For a `TAKEOVER` client, `client-disconnected` now arrives up to 30 s late. That is documented by the DECISIONS row, so it is not a finding. An agent can still wait on `lease-changed` instead of polling, because `mux.disconnect` releases the lease and emits the event on expiry.

## Design questions

- Orphans in `list` look identical to live clients. An agent or a person picking a client to `disconnect-client` cannot tell a lingering one apart. A `connected` flag on the client entry would help. The current behaviour is documented, so this is a question for the owner.
- The DECISIONS row says "Owner chose on decision cards …, to be confirmed on the PR". The 30 s lease hold for a killed client is therefore pending owner confirmation, not settled.

## Optional improvements

- `state.rs:278-279` says "A request proves the client got this connection's Welcome". That is not true for a client that pipelines a request right after its `Hello`, which the protocol allows. The TUI waits for the Welcome, so it is not affected. Either note that the rule assumes no pipelining, or confirm on a request only once its `req` is after the Welcome.
- `conn.rs:48-51` parks each ended connection's task for 30 s while it holds an `Rc` of the state. This is harmless, but could become one timer per orphan if connection churn grows.

## Checks performed

All runs used a scratch copy of the head (`git archive 55d4bbf9`) under `<scratch>/mux-exec`, with the probe file moved aside for the suite runs.

- `cargo test -p rusty_mux_server`: pass (all binaries, including `takeover` 8/8 and the lib's 51).
- `cargo test -p rusty_mux_tui`: pass (lib 31, lifecycle 22, in 19.4 s).
- `cargo test -p rusty_mux_ctl`: pass (64 + 2 + 34).
- `cargo clippy -p rusty_mux_server -p rusty_mux_tui -p rusty_mux_ctl --all-targets -- -D warnings`: exit 0.
- `python3 .github/scripts/changes.py check` (in the worktree): "541 fragment(s) ok", exit 0.
- Probes (`server/tests/probe.rs`, scratch only): all 3 fail at head as described in F1, F2 and F4. F1's probe passes with the one-line fix, and so do the 8 takeover tests.
- Base-code red check: the new takeover tests run against base `state.rs` and `conn.rs` (3 runs). Two fail every time, the others pass.

## Validation gaps

- Real sshd, a real network change or a sleep: not run. The stand-in `ssh` was exercised only through the existing lifecycle suite.
- Windows: not run. The server has a single `disconnect` caller (`conn.rs:47`), so there is no separate Windows path to miss, but the Windows CI leg was not seen.
- CI for this head: not seen.
- The F2 and F3 TUI traces are source-traced. I did not write a TUI probe with a second client zooming, or a relay that trickles only after the first Welcome.

## Verdict

**changes required**: F1 to F5. F1 and F2 are lost-handshake and stale-connection defects with executed traces.
