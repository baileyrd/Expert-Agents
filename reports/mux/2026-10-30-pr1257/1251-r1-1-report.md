# rusty_mux review: reconnect grace (blind trial)

- Repository: Rusty-Mill monorepo, worktree `/home/claude/trial-mux` (no PR number given; blind trial)
- Base: `d05170b8` (merge of #1245). Reviewed head: `55d4bbf9` ("rusty_mux: keep a reconnecting client through a lost handshake"); `git rev-parse HEAD` in the worktree = `55d4bbf92e2d5f8f1b12aa0ffeddf57775fe0375`.
- Diff: `git diff d05170b8...55d4bbf9`, 7 files (+211/-15): `server/src/{conn,state}.rs`, `server/tests/takeover.rs`, `tui/src/main.rs`, `tui/tests/lifecycle.rs`, `docs/projects/mux/DECISIONS.md`, `changes/2026-10-30-mux-reconnect-grace.md`.
- Probes: scratch worktrees `/tmp/claude-0/-home-claude/c6e91f6f-c04b-5b25-b0a9-c1761f25074d/scratchpad/probe-mux` (head, plus `server/tests/probe.rs`) and `.../scratchpad/probe-base` (base). Each was built in its own target directory: one shared target dir silently reused the head test binary for base, so base results were taken only from `target-base`.

## Step 1: predicted invariants

| Touched item | Invariant (source) | Prior art |
|---|---|---|
| `Welcome.token` / `Tokens{current, confirmed}` | A takeover whose reply is lost still leaves the client holding a token the server accepts (failure class "Lost handshake", #1243/#1251). A used token stops working once the client provably holds a newer one (DECISIONS 2026-10-29 step 2a: "rotating the token on each Welcome makes a used token worthless", now changed by row 2026-10-30). | mosh never rotates a session key on reconnect. Its state sync is idempotent: the client's last *acknowledged* state stays valid until the peer acks a newer one (`transportsender-impl.h`, acked vs sent states). |
| `State::disconnect` / `expire` / `orphans` (ORPHAN_GRACE 30 s) | A `TAKEOVER` client whose connection ends is kept 30 s with attachment and lease, then dropped unless taken over. Only the ending connection's own expiry drops it (generations) (DECISIONS 2026-10-30). | mosh server keeps the session indefinitely; tmux control mode drops the client on EOF. |
| `takes_over` | Accepts current or confirmed token, equal features, client connected **or orphaned**; constant-time compare (DECISIONS 2a + 2026-10-30). | n/a |
| `State::request` confirming the token | A request proves the client received this connection's Welcome (doc comment state.rs:278). | mosh: an ack names the state the receiver has. |
| `Disconnect` on an orphan | `disconnect-client` can end a client in grace (C33, DECISIONS 2026-10-30 "can still disconnect it"). | n/a |
| TUI clean exit sends `Detach` per pane | A clean exit releases leases at once. The fragment and the row say block clients "leave at once". | tmux `detach-client` |
| `lifecycle.rs` split test mark | The search marker is taken before the event it waits on ("Timing in tests"). | n/a |

No catalog method, event, feature bit or ctl/MCP surface changes. `CATALOG` asks for no `TAKEOVER` feature, so ctl (`ctl/src/lib.rs:588`) and MCP never get a token or a grace.

## Blocking findings

### F1 (P2): a token the client proved it holds by presenting it is not kept, so an idle client loses itself on the second lost Welcome
- **File:line:** `crates/apps/rusty_mux/crates/server/src/state.rs:159-164` (the carried `confirmed`), `:278-282` (the only place `confirmed` advances), `:952-962` (the `Tokens` doc).
- **Trace (executed):** `server/tests/probe.rs::probe_idle_pane_second_lost_welcome`. The client connects and sends requests, which confirm T1. Takeover 1 presents T1; the server sets current T2, confirmed T1, and the client *receives* T2 but sends no request (an idle TUI pane sends nothing after a takeover: `tui/src/main.rs:1681`, Attach only when not resumed). Takeover 2 presents T2; the server sets current T3, confirmed still T1, and this Welcome is lost. The retry presents T2, the only token the client has, and gets a **new client id** (assertion failed at probe.rs:42). The client is now "let go" even though it never lost track of its token. Presenting T2 in Hello 2 already proved the client held T2, and the server discarded that proof.
- **Who is hurt / trigger:** any `TAKEOVER` client that sends no request between two takeovers and then loses the second Welcome. The shipped TUI does not hit it today. Its control connection confirms at once with `List` (`main.rs:808-812`, window is always present with `INSPECT`), and a failed pane handshake after the control takeover ends the client anyway (`main.rs:817-822`). So no person or agent hits it now, which is why it is P2. It does make the change fragment's claim "a takeover accepts ... the last one it confirmed ... so a reconnect that gave up mid-handshake can still resume" true only for clients that happened to send a request in between, and it is exactly the path a pane retry (the natural next step) would take.
- **Correction:** on a successful takeover, set `confirmed = Some(takeover.token)` (the token just presented), not the previous `confirmed`. That still retires the older token on the next request, as row 2026-10-30 requires. Update the `Tokens` doc and the DECISIONS row wording ("the last one it confirmed, by presenting it or by sending a request after that Welcome").
- **Regression test:** `probe_idle_pane_second_lost_welcome` moved into `server/tests/takeover.rs`. It is red at head and goes green with the fix.

### F2 (P2): the PR makes "A local socket keeps today's behaviour" false; a killed local `rusty-mux` now holds its lease for 30 s
- **File:line:** `crates/apps/rusty_mux/crates/tui/src/main.rs:82-89` (`CONTROL`/`PANE` always ask for `TAKEOVER`, local or `--ssh`), `:419` and `:866` (local Hello and pane reach use them), `:455` (a local client never rejoins). The server side that newly applies the grace is `server/src/state.rs:238-241`. The doc made false is `docs/projects/mux/DECISIONS.md:39` ("A local socket keeps today's behaviour").
- **Trace (executed):** `server/tests/probe.rs::probe_killed_local_pane_keeps_lease`. A client with the TUI's `PANE` features attaches and acquires the lease, then its connection ends without a Detach (terminal tab closed, `kill`). 500 ms later another client is refused: `Failed { reason: "block … input lease is held by client …" }`. At base the lease was free at once (DECISIONS row 37: "A client whose old connection already closed is gone, and its lease is free"). The local TUI has no rejoiner (`rejoin` is `None` locally, `main.rs:457`), so the 30 s buys nothing locally. A person who reopens the block, or an agent's `ctl` input to it, is refused for up to 30 s.
- **Why P2, not P1:** the 30 s block for "a lost client" is owner-chosen in row 2026-10-30 and the fragment's Known limitations names it, so the behaviour itself is a design question (D1). The defect is that the PR leaves row 39's local-socket guarantee standing while the code no longer meets it.
- **Correction:** either ask for `TAKEOVER` only when a rejoin is configured (`--ssh`), which keeps row 39 true and costs a local client nothing, or amend row 39 and the fragment to say local clients get the grace too.
- **Regression test:** a TUI lifecycle test with a local socket. Kill the `rusty-mux` child (no Ctrl-] d), then `rusty-mux-ctl` (or a server-test client) acquires the block's lease within 1 s.

### F3 (P2): detached block clients do not "leave at once"; every `TAKEOVER` client lingers in `list` for the grace
- **File:line:** `crates/apps/rusty_mux/crates/tui/src/main.rs:505-507` ("Detached, a server drops each block's client at once"). Also `docs/projects/mux/DECISIONS.md:41` ("so it leaves at once without its lease; its idle control client lingers") and `changes/2026-10-30-mux-reconnect-grace.md:10` (Known limitations names only the control client). The code is `server/src/state.rs:238-241`: `disconnect` orphans on `tokens.contains_key`, whether attached or not.
- **Trace (executed):** `server/tests/probe.rs::probe_detached_takeover_client_lingers` / `probe_list_debug`. A `TAKEOVER` client sends `Detach` (Done), then its connection ends. 500 ms later a `List` from an `INSPECT|LABELS` client still has its `Entry::Client` (before=1, after=1). `ClientDisconnected` arrives 30 s late. An agent counting clients in `list`, or waiting for `client-disconnected` after a person quits `rusty-mux`, sees one ghost per pane plus the control client for 30 s. Leases are released correctly.
- **Correction:** fix the comment, the DECISIONS row and the fragment to say every client of a clean exit lingers in `list` for the grace, holding nothing. Alternatively, have the server drop a client at once when its connection ends cleanly right after a Detach. That needs a protocol signal, since the control client is also unattached but must survive.
- **Regression test:** if behaviour is changed, a server test: Detach, close, then `List` shows the client gone within `PATIENCE`. If only the docs change, none.

### F4 (P2): no test goes red without the TUI's Detach-before-exit
- **File:line:** `crates/apps/rusty_mux/crates/tui/src/main.rs:505-512`.
- **Trace (executed):** in `probe-mux` the block was disabled (`if false && app.lost.is_none()`) and `cargo test -p rusty_mux_tui` was run: 31 + 22 passed, 0 failed. The new server test `a_client_that_detached_before_leaving_holds_no_lease` (`server/tests/takeover.rs:154`) checks the server's Detach, not that the TUI sends it. It also passes at base.
- **Correction / test:** add a TUI lifecycle test in `tui/tests/lifecycle.rs`. Start `rusty-mux` on a local socket, type until a marker shows, detach with Ctrl-] d, then a second client (ctl `send`, or a raw `Hello`+`Attach`+`AcquireLease`) gets the lease within 1 s. Without the Detach it is refused for 30 s.

## Design questions (documented behaviour that hurts a real client)

- **D1.** The owner-chosen 30 s lease hold for a client whose connection ends (row 2026-10-30) also applies to a local `rusty-mux`, which can never reconnect. Closing a terminal tab blocks typing into that block, from a new `rusty-mux` or an agent, for 30 s. Should the grace apply only to clients that can rejoin (see F2's first fix)?
- **D2.** `list` gives no way to tell a client in grace from a live one. An agent cannot tell "a person is attached" from "a person left within 30 s". An `orphaned`/`since` field behind `LABELS` would answer it without polling.

## Optional improvements (non-blocking)

- `a_client_whose_connection_ends_is_kept_for_a_reconnect` (`takeover.rs:136`) drops `old` and takes over at once. If the server reads the new Hello before the old EOF, it takes over a still-connected client and does not exercise the orphan path. It failed 5/5 at base, so the race favours the intended order, but waiting for the old connection's end first (e.g. a test-only `has_client` probe, or a watcher seeing no `ClientDisconnected`) would make it deterministic.
- `without_the_feature_a_client_goes_with_its_connection` (`takeover.rs:168`) polls `List` with a 20 ms sleep. It is bounded by `PATIENCE`, so it is not a flake source, but waiting for `ClientDisconnected` on an event stream would match "never poll the screen".
- `conn.rs:48-51` keeps one sleeping task per ended `TAKEOVER` connection for 30 s. That is bounded by connection count and harmless, but a reconnect storm leaves many timers. A single per-client deadline would do.

## Failure classes (step 2)

- **Lost handshake:** F1 (executed). For the shipped TUI's control connection the new rule works (`a_takeover_whose_welcome_was_lost_can_be_retried_with_the_old_token` red at base, green at head). The fix's motivating test `a_trickling_answer_is_given_up_in_time` failed 2/6 at base and passed 6/6 at head.
- **Stale connection:** ruled out. `expire` uses `Rc::ptr_eq` against the ending connection's cancel (unit test `an_orphan_expires_only_by_its_own_connection`, plus a source trace for A→B→B-ends→A-expiry). An old connection's requests after a takeover are refused by `served_by` before they can confirm a token (`conn.rs:62`). `Disconnect` of an orphan removes it from `orphans`, so a later expiry is a no-op. IDs are random UUIDs, so they are never reused.
- **Split stream / Deadlines:** ruled out, because framing (`conn.rs::Frames`) and the TUI's deadline reads are unchanged.
- **Last output:** no change to close ordering. An orphan's output is skipped by `send_frame` (not in `clients`) and its snapshot is re-sent on takeover (`state.rs:183-186`). Validation gap: a block that closes while its client is orphaned yields no `BlockClosed` on the taken-over pane connection (PANE has no `EVENT_SEQ`). This is the same as the pre-existing silent-path case, and the TUI presumably relies on the control `List`. Not traced to a hang.
- **Silent truncation:** not touched.
- **Outside config:** not touched (no ssh/`-L`/TMPDIR change).
- **Secrets:** tokens are never logged or printed. The constant-time compare is kept, with a non-short-circuit `|` over both tokens. No new files. Ruled out.
- **Timing in tests:** the `lifecycle.rs:1214-1217` move is correct. A probe with every re-login failing (`["relay","fail","fail",…]`) made the loop fail with `not drawn again: ""`, so the earlier mark does not let pre-reconnect output satisfy it. The split test passed 6/6 at head. No new `Notify`-based waits.

## Agent contract (step 3)

No catalog method, event or feature bit was added or changed. `disconnect-client` now also finds orphans (`state.rs:524`), and its error code for an unknown client is unchanged. ctl and MCP never negotiate `TAKEOVER`, so their connections get no grace. Agents see TUI clients linger (F3, D2) and can be refused a lease for 30 s after a person's TUI dies (F2, D1).

## Tests (step 5): red without the change?

| Test | Base | Head |
|---|---|---|
| `takeover.rs::a_client_whose_connection_ends_is_kept_for_a_reconnect` | FAILED 5/5 | ok |
| `takeover.rs::a_takeover_whose_welcome_was_lost_can_be_retried_with_the_old_token` | FAILED 5/5 | ok |
| `takeover.rs::a_client_that_detached_before_leaving_holds_no_lease` | ok (guards nothing new, see F4) | ok |
| `takeover.rs::without_the_feature_a_client_goes_with_its_connection` | ok (regression guard for non-TAKEOVER clients) | ok |
| `takeover.rs::a_wrong_or_used_token_starts_a_new_client` (edited) | ok | ok |
| `state.rs::regression::an_orphan_expires_only_by_its_own_connection` | does not compile at base (new API) | ok |
| `lifecycle.rs::a_trickling_answer_is_given_up_in_time` | FAILED 2/6 | ok 6/6 |
| TUI Detach-before-exit | no test (F4) | — |

## Checks performed

All at head `55d4bbf9`, in the `probe-mux` scratch worktree:
- `cargo test -p rusty_mux_server`: pass (51 + 12 + 12 + 5 + 4 + 1 + 6 + 8 tests across targets, 0 failed).
- `cargo test -p rusty_mux_tui`: pass (31 + 22, 0 failed).
- `cargo test -p rusty_mux_ctl`: pass (64 + 2 + 34, 0 failed).
- `cargo clippy -p rusty_mux_server -p rusty_mux_tui -p rusty_mux_ctl --all-targets -- -D warnings`: clean.
- `python3 .github/scripts/changes.py check`: "541 fragment(s) ok".
- Probes in `server/tests/probe.rs`: F1, F2 and F3 traces red as described. F4 is the TUI suite green with the Detach removed.

Validation gaps:
- Real sshd: whether the exit-time `Detach` bytes reach the server before `Forward::drop` kills `ssh` (`tui/src/ssh.rs:161-166`) on a slow link. Settle it with `rusty-mux --ssh` to a real host on a throttled link: Ctrl-] d, then `ctl` acquires the lease at once.
- Windows: the TUI's reconnect is Unix-only and was not run.
- CI: the required checks on a PR head were not seen (blind trial).

## Verdict

**changes required.** Four P2 findings, no P0 or P1.
