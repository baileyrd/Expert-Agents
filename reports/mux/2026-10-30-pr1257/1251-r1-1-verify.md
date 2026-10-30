# Verification of review claims F2-F4 on PR #1251 (head 55d4bbf9, base d05170b8)

Probes: `verify-mux/probe.rs` and `verify-mux/probe2.rs` (server integration tests, copied into
`crates/apps/rusty_mux/crates/server/tests/` of fresh detached worktrees at head and base, then
removed). The base was built with its own `CARGO_TARGET_DIR`. A first base run that shared head's
target dir gave head results, so I threw it away.

Measured (time from the old client's socket close until the next check passes):

| probe | base d05170b8 | head 55d4bbf9 |
|---|---|---|
| TAKEOVER client killed while holding the lease: another client can take the lease | ~0.25 s (first retry) | 30.07 s |
| TAKEOVER client that sent Detach, then closed: gone from `list` | ~0.25 s | 30.07 s |
| plain (no TAKEOVER) client: gone from `list` | <1 ms | <1 ms |
| PR test `a_client_that_detached_before_leaving_holds_no_lease` | passes (copied into base) | passes |
| TUI with the exit Detach block (main.rs:505-512) deleted: `cargo test -p rusty_mux_tui` | n/a | all pass (31 + 22 lifecycle) |

Current main: #1251 merged as 87569bec, with two more commits after 55d4bbf9: 70559a85 (keep the
presented token; detach blocks the TUI stops showing) and 3c62c1b9 (owner confirmation recorded,
"confirmed on #1251 the same day"). I checked 5991f222 and the newer fetched origin/main 65c04a00.
`State::disconnect` (main state.rs:255-267), the TUI's CONTROL/PANE constants, the exit comment at
main.rs:505, DECISIONS step 2c (":39") and the reconnect-grace row (41 at 5991f222, 42 at 65c04a00,
same wording) are unchanged in substance. None of the three claims is fixed on main.

## F2: real, P2 (severity right). Not fixed on main.

- Both TUI connection types always ask for TAKEOVER: tui/src/main.rs:82-86 (`CONTROL` includes `TAKEOVER`) and :89 (`PANE`). They are sent at :419 (control Hello) and :866 (block Hello). The PR did not add this (the base has the same constants), but at the base it did nothing unless a takeover was presented.
- A local `rusty-mux <socket>` calls `attach(..., None)` (main.rs:335). With `rejoin` None, the match at :456-459 gives `rejoiner: None`, so a local client never reconnects or takes itself over. `rejoins` at :455 only gates the `--ssh` path.
- The PR's server/src/state.rs:238-241 (`if self.tokens.contains_key(&client)`, then the client goes into `orphans`) plus conn.rs:47-51 (sleep `ORPHAN_GRACE`, then `expire`) keep every token holder for 30 s with its attachment and lease. Probe: on head the lease of a killed TAKEOVER client stays held ("input lease is held by client ...") for 30.07 s; on base it is free at once.
- So a killed or terminal-closed local `rusty-mux` blocks its focused block's lease for 30 s and gets nothing in return. That contradicts DECISIONS.md:39 ("A local socket keeps today's behaviour").
- Mitigating context, which keeps this at P2 and not P1: the PR's own row DECISIONS.md:41 and changes/2026-10-30-mux-reconnect-grace.md:10 ("a client that dies without detaching (killed, or its terminal closed) keeps its lease for up to 30 s; another client can still end it with `Disconnect`") accept the 30 s hold with no `--ssh` scope, and the owner confirmed the row on #1251 (3c62c1b9). The documented trade-off does not mention local clients, and its rationale (a reconnect that gave up mid-handshake) does not apply to them. Line 39 was left unchanged. The defect is a stale or contradicted decision line, plus an unscoped cost the owner may not have meant for local clients.

## F3: real, P2 (severity right). Not fixed on main.

- state.rs:238-241 checks only `tokens.contains_key`. `Detach` (state.rs:399 calls `mux.detach`, core/src/mux.rs:515-521 `unattach`) does not remove the token or the client. A detached TAKEOVER client whose connection ends is orphaned like any other.
- Probe: on head a client that sent `Detach` and then closed stays in `list` for 30.07 s. On base it is gone at once. It holds no lease: the PR test passes, and so does my lease probe after Detach.
- What is false:
  - tui/src/main.rs:505-507, "Detached, a server drops each block's client at once; one whose connection just ends is kept a while." Both kinds are kept 30 s. Only the lease and attachment differ.
  - DECISIONS.md:41, "so it leaves at once without its lease; its idle control client lingers for the grace". "Without its lease" is true. "Leaves at once" is false: the block clients linger in `list` too, not only the control client.
  - changes/...:10, "An idle control client lingers in `list` for the grace after a clean exit". This is incomplete in a misleading way: every block client lingers too. Line 8 ("release their leases at once") is correct.
- On main, 70559a85's new comment at the hidden-block detach (main.rs ~1567, "Left on purpose, so detached first: a server keeps a client whose connection just ends") carries the same implication. The DECISIONS row and changelog lines kept the wording.

## F4: real, P2 (severity right). Not fixed on main.

- Mutation probe on head: I deleted tui/src/main.rs:508-512 (the `if app.lost.is_none() { for pane ... Message::Detach }` block). `cargo test -p rusty_mux_tui --no-fail-fast` stayed green: 31 unit tests and 22 lifecycle tests. lifecycle.rs is the only test file that runs the `rusty-mux` binary (grep for `CARGO_BIN_EXE_rusty-mux`).
- server/tests/takeover.rs:154 `a_client_that_detached_before_leaving_holds_no_lease` only drives the server with the test `Client`. I copied it into the base worktree and it passes there, because the base drops any closed client at once. So it does not even pin the server half of the PR's change.
- On main, 70559a85 added `a_block_zoomed_away_and_back_is_leased_again_at_once` (lifecycle.rs). By source trace it covers the hidden-block Detach (main.rs ~1570), not the exit path. I did not run a mutation on main.
