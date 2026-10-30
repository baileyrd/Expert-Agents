<!-- mux/1251-r1 run 1, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 484 s, 3020411 tokens, $2.38 -->

# Review of PR 1251: changes required

**Rusty-Mill/rusty_mill PR 1251**, "rusty_mux: keep a reconnecting client through a lost handshake"
- Base: `d05170b8cc082cffaf6c348411e82bea84b9632d`
- Head: `55d4bbf92e2d5f8f1b12aa0ffeddf57775fe0375`. `git fetch origin pull/1251/head` returned this SHA, so the review is pinned.
- One commit, 7 files.

I couldn't build or run anything with cargo (see Checks), so every finding below is traced through the source, not executed.

## What the PR touches, and the rule each part must follow
- **Takeover handshake and token rotation** (`State::connect`, `takes_over`): a client must never be left holding a token the server refuses, even when the Welcome is lost (#1243, and the new DECISIONS row of 2026-10-30). A token that has been used must stop working once the newer one is confirmed. mosh works the same way: the sender keeps its states until the receiver acknowledges one (`transportsender-impl.h`, the assumed receiver state).
- **Orphans** (`disconnect`, `expire`, `drop_client`): the end or expiry of a connection that has already been replaced is ignored. The code meets this: `Rc::ptr_eq` on the cancel handle, state.rs:250-260. A detached or hidden client releases what it holds (C33, and the 2026-10-30 row). tmux has no takeover; a client that leaves is simply gone.
- **Confirming a token on a request** (state.rs:280-282): only the connection that currently serves the client can confirm. This holds because `serve` checks `served_by` first (conn.rs:62).
- **`disconnect-client`** on an orphan: works (state.rs:524).
- **TUI clean exit**: sends `Detach` on each pane, then shuts the socket.

## Blocking findings

### 1. P1: the `Detach` sent at a clean exit can go unread, so the lease stays held for 30 s
- **Where:** `tui/src/main.rs:505-513`, `server/src/conn.rs:37-51`, `server/src/state.rs:238-241`
- **Trigger:** a clean exit (Ctrl-] d) while the block is producing output.
- **What happens (source-traced):**
  1. The client writes `Detach` on the pane socket and immediately calls `shutdown(Both)`. It does not wait for the `Done` reply.
  2. On Linux, that shutdown makes every later server write on the socket fail with EPIPE.
  3. The server's `select!` starts each poll at a random branch (`rusty_tokio/src/macros.rs:52-54`).
  4. When `write_frames` is polled first with output queued, its write fails and the select returns. `serve` is never polled, so the `Detach` waiting in the socket buffer is never read.
  5. `disconnect` then finds a token and keeps the client as an orphan, still attached and holding the lease, for `ORPHAN_GRACE`.
- **Who is hurt:**
  - An agent's `acquire-lease` or `input` on that block gets `LeaseHeld` for up to 30 s.
  - The person re-attaching within that time gets a refused lease, and the keys they type are dropped silently (main.rs:1397).
- **Why it blocks:** it contradicts the PR's own DECISIONS row ("detaches each block before a clean exit, so it leaves at once without its lease") and the change fragment. Before this PR the race didn't matter, because the client was dropped as soon as its connection ended.
- **Correction:** the client should wait, briefly and with a deadline, for each `Detach`'s `Done` before shutting down, or half-close the write side and drain until the server closes. Optionally, the server's `select!` could be `biased;` with `serve` ahead of `write_frames`, so requests already buffered are read first.
- **Regression test:** add one to `server/tests/takeover.rs`, repeated about 30 times:
  1. A `TAKEOVER` client attaches and leases a block that runs a program writing continuously.
  2. It writes a raw `Detach` frame and calls `shutdown(Both)` without reading the reply.
  3. A second client's `AcquireLease` must succeed within 1 s.

### 2. P1: a pane hidden by a layout change keeps its lease for 30 s
- **Where:** `tui/src/main.rs:1566-1570` (`sync` hangs up hidden panes) together with `state.rs:238-241`
- **Trigger:** another client or an agent zooms a different block in the person's window.
- **What happens (source-traced):**
  1. The person's two-block window has focus on block A, which holds the lease.
  2. An agent runs `zoom-block B`. The resulting `LayoutChanged` leads to `relayout`, which has nothing leaving (zoom doesn't remove blocks from the layout), then `settle`, then `sync`.
  3. `visible()` is now just B, so focus moves to B and A's pane is hung up with no `Detach` or `ReleaseLease`.
  4. Before this PR, the server dropped that client and freed A's lease. Now it keeps it as an orphan, attached and holding A's lease, for 30 s.
  5. Within those 30 s, the person unzooms and moves focus back to A. The fresh pane's `AcquireLease` gets `LeaseHeld` (main.rs:1602), and keys typed into A are dropped silently (main.rs:1397).
  6. An agent typing into A also gets `LeaseHeld`.
- **Why it blocks:** the fragment's known limitation covers only a client that dies; this one is a live client.
- **Correction:** send `Detach` (which releases the lease, `core/src/mux.rs:515`) and wait for its `Done` before `hang_up` in `sync`.
- **Regression test:** a TUI test in `lifecycle.rs` against `real_server`:
  1. Show two blocks with A focused.
  2. Run ctl `zoom-block B`, then `unzoom-window`.
  3. ctl `acquire-lease --block A` must succeed within 2 s.

### 3. P2: comments and docs this PR makes false
- `main.rs:505-507` says "Detached, a server drops each block's client at once". In fact state.rs:238 keeps every `TAKEOVER` client as an orphan whether attached or not. Detached block clients stay in `list`, and `client-disconnected` fires 30 s late.
- The fragment's "An idle control client lingers in `list`" and the DECISIONS row's "it leaves at once" leave out those block clients.
- `main.rs:173-178` (`Missed::Broken`) says "hanging the fresh connection up ends that client". The server now keeps it for 30 s.
- **Correction:** amend all four texts.

### 4. P2: no test covers the TUI's detach on exit
- No test goes red without `main.rs:505-513`.
- `a_client_that_detached_before_leaving_holds_no_lease` uses `request()`, which waits for `Done` (`tests/common/mod.rs:250-262`). So it doesn't exercise the client's send-then-shutdown path, and by the source it also passes at base.
- **Where it belongs:** a TUI lifecycle test with `real_server`: attach, Ctrl-] d, then ctl `acquire-lease` on the block succeeds well inside 30 s.

## Failure classes checked
- **Lost handshake:** fixed for the control connection, which sends `List` straight after the takeover Welcome (main.rs:810-814), so its token is confirmed at once.
- **Stale connection:** generations are correct. The hide and exit paths are Findings 1 and 2.
- **Split stream and deadlines:** ruled out. The diff touches no parser, filter or read loop.
- **Last output:** ruled out. The diff leaves the drain-before-relayout path alone.
- **Silent truncation:** ruled out. No reply limits change.
- **Outside config:** ruled out. ssh invocation and temporary files are untouched.
- **Secrets:** tokens are compared in constant time and never logged. `Tokens` derives `Debug`, but nothing formats it.
- **Timing in tests:** the `lifecycle.rs` change now takes the marker before the wait, which is correct. The new waits poll for a condition rather than sleeping.

## Design questions for the owner
- **What counts as confirming a token** (state.rs:159-163). The code carries the old `confirmed` token forward instead of the token presented in the Hello. A client that receives a takeover Welcome and sends no request, then loses the next takeover's Welcome, holds a token the server refuses. An idle resumed pane is exactly that client: it sends no `Attach` (main.rs:1681). Today's TUI can't reach this, because a pane failure ends the client (`Broken`). The decision says confirmation is "by sending a request"; should presenting a token in a Hello count as well?
- **A delayed duplicate Hello.** One carrying the confirmed old token could still take the client back in the one round trip between a takeover's Welcome and its first request.
- **Agents can't see orphans.** In `list`, an orphan looks like a live client, and `client-disconnected` arrives 30 s late, so an agent can't tell the person has gone.
- **Local attach gets the grace too.** It also asks for `TAKEOVER`, so the 30 s lease hold applies locally, while the step 2c decision says "A local socket keeps today's behaviour".

## Optional improvements
- Drop `Debug` from `Tokens` (state.rs:958).
- `a_client_whose_connection_ends_is_kept_for_a_reconnect` doesn't wait for the server to see the old connection end. It may take over a still-connected client, so it can pass at base.

## Checks run, and validation gaps
- **Change fragments:** `python3 .github/scripts/changes.py check changes` on an exported copy of the head: **541 fragment(s) ok**, exit 0. (To run it I also ran `git init` in that scratch copy, which is outside the reviewed tree.)
- **Not run: `cargo test` (`-p rusty_mux_server`, `-p rusty_mux_tui`, `-p rusty_mux_ctl`), clippy, running the new tests at base, and the race probe for Finding 1.**
  - The sandbox denied crates.io.
  - The local cache lacks the versions the lockfile pins (`tokio` 1.53.3, `rustls` 0.23.46), and the git dependency `platform` isn't checked out.
  - A copy with the workspace narrowed still failed to resolve offline.
  - The scratch directory already held a `tests-head.log` I didn't create; I didn't use it.
  - With crates.io access or a warm cache, the same commands plus the two regression tests above would settle this.
- **Also gaps:** behaviour against a real sshd (how the Detach/EOF/close ordering crosses the forward) and Windows `AF_UNIX` shutdown semantics.

## Verdict
**Changes required.**
