# Verification: rusty_mux reconnect grace claims (#1251 first head)

Checked: head 55d4bbf9, base d05170b8, main ad1c3e37, each in its own detached worktree with its own CARGO_TARGET_DIR (worktrees and targets removed afterwards).
How I probed: source trace of the PR diff (base..head) and the same code on main; ran `cargo test -p rusty_mux_server --test takeover` at head, and ran the head's takeover.rs against the base server; mutation runs (deleted the guarded code and ran the whole `rusty_mux_server` and `rusty_mux_tui` test suites) at head and main; two probe tests added temporarily to server/tests/takeover.rs (a detached TAKEOVER client is still listed; `Disconnect` works on an orphan) and run at head and main, then reverted. Nothing committed.
Paths below are under `crates/apps/rusty_mux/crates/`.

## F3: real (partly right), P2 agreed. On main.

- Head `tui/src/main.rs:178-181`: the doc comment on `Missed::Broken` says "hanging the fresh connection up ends that client, so there is nothing left to continue". With this PR, `server/src/state.rs` `disconnect` (head diff, about :232-247) keeps any client that has a token as an orphan for `ORPHAN_GRACE` (30 s). Hanging up no longer ends the client, so the comment is stale. Source-traced.
- Head `tui/src/main.rs:820-826`: if a block's `reach` fails after the control takeover (for example its Welcome never arrives), the `Retry` is turned into `Broken`. At `:1159` `Broken` leads to `self.end(Ended::Failed(..))`, so the TUI exits. The server would still accept that block's old (confirmed) token for 30 s. Source-traced.
- Partly right: continuing would need more than a retry. The rejoin thread drops the control client's new token (`me`, from `:803-807`) when it returns `Err`. If a window is shown, the `List` call at `:811` has already confirmed that new token, which retires the old one held in `plan.me`. So the TUI has no valid control token left for a retry. The changelog headline "a reconnect that gives up midway can still resume" is true when the control handshake gives up (`Retry` before takeover). It overstates the case of a block handshake given up after the control takeover. No doc makes this exit the intended behaviour: DECISIONS step 2c only says the client stops when the server "has let a client go".
- Main: the comment is unchanged at `tui/src/main.rs:189-191`, the mapping is at `:848`, and `Broken` still ends the client at `:1232`. The `Abandoned` comment at `:258` ("hanging up could reach the server first, and end them") is now stale in the same way.

## F4: real, P2 agreed. On main.

- Executed (probe at head and main): a `TAKEOVER` client sends `Detach`, its connection drops, and 2 s later another client's `List` still shows it. Cause: `Mux::detach` (`core/src/mux.rs:515-521`) only unattaches the client. `disconnect` then orphans it because it holds a token, and `list` (`server/src/state.rs:533+`) lists every `mux.clients()`. The TUI asks for TAKEOVER on every block connection, local ones included (`tui/src/main.rs:86,89`).
- Text that disagrees with this:
  - Head `tui/src/main.rs:505` says "Detached, a server drops each block's client at once".
  - `docs/projects/mux/DECISIONS.md:41` says "so it leaves at once without its lease; its idle control client lingers".
  - The changelog fragment's Known limitations line names only the control client as lingering, which implies block clients do not.
- Partly right: the lease is released at once, which is what matters. Only the list entry lingers, and it holds nothing.
- Main: the same wording is at `tui/src/main.rs:518`, `DECISIONS.md:42` and `changes/2026-10-30-mux-reconnect-grace.md:9`. The probe gives the same result on main.

## F5a: real, P2 agreed. On main (for the clean-exit Detach).

- Executed at head: I deleted the clean-exit Detach loop (`tui/src/main.rs:508-512`) and ran `cargo test -p rusty_mux_tui`. All 31 unit tests and 22 lifecycle tests passed, so no TUI test guards this code.
- Executed: the PR's server test `a_client_that_detached_before_leaving_holds_no_lease` passes against the base server (the head's takeover.rs run on base: 6 pass, and only the two grace/token tests fail). At head it does guard the server side: with its `Detach` line removed, it fails. So it is a server-contract test, not a test of the TUI change.
- Main: this commit added a test for detaching hidden blocks (`a_block_zoomed_away_and_back_is_leased_again_at_once`, which covers `:1641`). The clean-exit loop (`tui/src/main.rs:521-525`) is still unguarded: with it deleted, 55 unit tests and 23 lifecycle tests pass (executed).

## F5b: real, P2 agreed. On main.

- Executed at head: I removed `&& !self.orphans.contains_key(&target)` at `server/src/state.rs:524` and ran the whole `rusty_mux_server` suite. Everything passed. The only `Disconnect` integration test (`tests/labels.rs:155`, `a_disconnected_client_loses_its_lease_and_its_connection`) uses LABELS clients without TAKEOVER, so it never involves an orphan.
- The behaviour itself works. Executed probe: `Disconnect` of an orphaned client removes it from `list`, at head and on main. It is untested, and the changelog fragment promises it ("another client can still end it with `Disconnect`"; DECISIONS cites C33).
- Main: the same check is at `server/src/state.rs:553`. With it mutated, every server test still passes (executed).
