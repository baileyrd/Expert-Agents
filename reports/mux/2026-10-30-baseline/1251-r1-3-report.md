<!-- mux/1251-r1 run 3, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 1374 s, 2505153 tokens, $1.94 -->

# Review of Rusty-Mill/rusty_mill PR #1251: changes required

The PR keeps a reconnecting client alive for 30 s after its connection ends, and accepts the last confirmed token on a retry. I found three P1 defects and two P2 defects. None has an executed trace: I couldn't build anything (see Checks), so every trace below is from reading the source.

- **Repository / PR:** Rusty-Mill/rusty_mill #1251
- **Base:** `d05170b8cc082cffaf6c348411e82bea84b9632d`
- **Reviewed head:** `55d4bbf92e2d5f8f1b12aa0ffeddf57775fe0375`. `git fetch origin pull/1251/head` gave this SHA, and the diff is one commit.

## What the diff touches, and the invariant for each

- **Hello/Welcome with a `Takeover` token** (`State::connect`, `takes_over`). A takeover whose Welcome is lost leaves the client holding a token the server still accepts, and the client comes back with its attachment and lease (#1243, #1251; DECISIONS 2c row). mosh avoids this whole class: state sync is idempotent and keyed by sequence numbers, so no secret rotates in a reply that could be lost.
- **Token state** (`Tokens{current, confirmed}`, confirmed by any request, state.rs:278-282). A token is retired only once the client has proven it holds the newer one. `serve` drops requests from a replaced connection (conn.rs:62), so a stale connection can't confirm a token. I ruled that out.
- **Client lifecycle** (`disconnect` → orphan → `expire`, or takeover). Only the connection that ended can expire the orphan (`Rc::ptr_eq`), and `disconnect-client` still ends an orphan (C33). Both hold. mosh's server keeps the session with no fixed grace; tmux control mode drops the client when its connection ends.
- **Lease and attachment held by an orphan.** A hidden or detached block must release what it holds. This one fails: Finding 2.
- **`Detach` sent by the TUI on clean exit.** It must reach the server before the path goes. This fails over `--ssh`: Finding 3.
- **Catalog:** no method or feature bit changed. `disconnect-client` now also reaches orphans. `list` and the `client-disconnected` event change timing (Finding 4).

## Blocking findings

### 1. P1: The retry backoff reaches the 30 s grace, so a lost handshake late in a run of retries still drops the client
- **Where:** `tui/src/main.rs:1261` (`1 << tries.min(5)`, capped by `BACKOFF` = 30 s at line 97) against `server/src/state.rs:46` (`ORPHAN_GRACE` = 30 s), with the wait at `server/src/conn.rs:48-50`.
- **Trigger:** the welcome that would hand over the new token is lost on the 5th or later reconnect attempt, or on the 4th when the next login takes more than about 14 s.
- **Failure scenario (source-traced):**
  1. The path dies. Logins 2–5 fail, which sets `tries` to 4 after waits of 2, 4, 8 and 16 s.
  2. Login 6 succeeds and the TUI sends `Hello{takeover: T0}`. The server rotates the token to T1 and moves the client onto the fresh connection; T0 stays confirmed.
  3. The Welcome trickles in, `HANDSHAKE` (10 s) runs out, and the TUI returns `Missed::Retry`. `discard()` kills the fresh ssh. The server sees EOF, and the client becomes an orphan whose grace starts now.
  4. `retry()` sets `tries` to 5, so the next attempt waits 30 s.
  5. Login 7 then has to log in and connect, so its `Hello{T0}` arrives more than 30 s after step 3. `expire` has already dropped the client, and `takes_over` finds no tokens.
  6. The TUI shows "the server let this client go".
- **Who is hurt:** the person at the terminal. It is the exact failure this PR says it fixes, and it is certain once `tries` ≥ 5.
- **Correction:** either retry promptly after a `Retry` that sent a takeover `Hello`, or make the grace exceed `BACKOFF + RELOGIN + HANDSHAKE`. Assert the chosen relation in code.
- **Regression test:** a TUI test in `lifecycle.rs` using `relaying_ssh("late-trickle", &["relay","fail","fail","fail","fail","trickle 0"])`. It should reach login 8 and assert that a clean detach prints "detached from block", not "let this client go". Add a cheap unit assert of the timing relation as well.

### 2. P1: A pane the TUI hides is hung up without `Detach`, so its lease is now held for 30 s
- **Where:** `tui/src/main.rs:1566-1570` (`sync()` hangs up panes that are no longer shown) and `tui/src/main.rs:1750-1752` (dropping a `Pane` hangs it up). Both now reach `server/src/state.rs:238-241`, which orphans any client that holds a token, lease and all.
- **Trigger:** a layout change hides the block the person has focused. For example, an agent runs `rusty-mux-ctl zoom-block --block B` on the person's window, or moves block A to another window.
- **Failure scenario (source-traced):**
  1. The person's focus is block A, and A's pane holds the lease.
  2. `LayoutChanged{zoomed: B}` arrives, then `relayout` → `settle` → `sync()`. Because A is no longer visible, focus moves to B (line 1563) and A's pane is hung up before `lease()` runs. No `ReleaseLease` or `Detach` is sent.
  3. The server keeps A's client for 30 s, still holding the lease.
  4. The agent's `acquire-lease --block A + send-keys` exits 1 ("lease is held by client …").
  5. After an unzoom, the TUI opens A on a fresh client. When the person moves focus back to A, `AcquireLease` fails, so `leased` stays false and keystrokes are silently dropped (line 1397).
- **Before this PR:** the hang-up dropped the client and freed the lease at once.
- **Who is hurt:** the person at the terminal and the agent.
- **Correction:** send `Detach` (or `ReleaseLease`) before hanging up any pane the TUI drops on purpose, at both sites.
- **Regression test:** a TUI test that splits the window, zooms the unfocused block through ctl, then has a ctl client `acquire-lease` on the formerly focused block within a few seconds and expects success.

### 3. P1: Over `--ssh`, the exit `Detach` races ssh being killed, so the lease can still be held 30 s after a clean exit
- **Where:** `tui/src/main.rs:508-516`, then `drop(app)` → `Forward::drop` at `tui/src/ssh.rs:165` (`child.kill()`, which is SIGKILL).
- **Trigger:** a clean exit over `--ssh` (Ctrl-] d or the last block closing).
- **Failure scenario (source-traced):**
  1. The TUI writes `Detach` into the local socket of ssh's `-L` forward and shuts the stream.
  2. Microseconds later it SIGKILLs ssh.
  3. If ssh had not yet read those bytes and written them to its TCP socket, the `Detach` is lost. sshd closes the channel, and the server orphans each pane client with its lease.
  4. The person reattaches from another terminal, or an agent wants to type, and gets `LeaseHeld` for up to 30 s.
- **What becomes false:** the change fragment's claim ("its block clients release their leases at once") and the DECISIONS row ("it leaves at once without its lease"), over ssh. The comment at line 507, "Shutting the socket below still delivers this first", holds only on a local socket.
- **Correction:** wait, with a short bound, for each `Detach`'s `Done` (or for the server's end of stream) before stopping the forward.
- **Regression test:** a TUI test with a relaying stand-in that delays its relay. Detach cleanly, then a ctl `acquire-lease` on the block must succeed at once.

### 4. P2: Docs say a detached client leaves at once, but the code keeps it for 30 s
- **Where:** `tui/src/main.rs:505` ("a server drops each block's client at once"), `server/src/state.rs:233` ("A client that means to leave detaches first"), and the DECISIONS 2026-10-30 row ("it leaves at once").
- **What actually happens (source-traced):** `disconnect` checks `tokens`, not attachment (state.rs:238). A detached pane client therefore stays in `list` and sends `client-disconnected` 30 s late, the same as a dead client.
- **Why the fragment is incomplete:** its "Known limitations" mention only the idle control client.
- **Who is hurt:** an agent reading `list` or waiting on `client-disconnected`.
- **Correction:** either drop an unattached, unleased client at once when its connection ends, or correct the comments, the decision row and the fragment.
- **Regression test:** a server test that detaches, drops the connection, and expects `list` to omit the client within the patience bound.

### 5. P2: Coverage: the TUI's exit `Detach` has no test that goes red without it
- `a_client_that_detached_before_leaving_holds_no_lease` (`server/tests/takeover.rs`) would pass at base too, judging from the source; I couldn't run it there. `Detach` already released the lease before this PR, and the connection ending dropped the client. So nothing tests the change at `main.rs:508-512`.
- **Where the test belongs:** in `tui/tests/lifecycle.rs`. Exit the TUI cleanly, then have a ctl `acquire-lease` on the block succeed within, say, 2 s, both locally and through `relaying_ssh`.

## Failure classes ruled out
- **Stale connection:** `served_by` is checked before every request (conn.rs:62), and `expire` checks it is the same connection (`Rc::ptr_eq`).
- **Split stream, deadlines, outside config, secrets:** no parser, frame deadline, ssh argument or file changed. Token comparison stays constant-time with a non-short-circuit `|`, and tokens are never logged.
- **Silent truncation:** no reply size changed.
- **Last output:** `Detach` is sent after `stop`, on the exit path only.
- **Timing in tests:** see the optional improvements; the new marker in the split-window test is sound.

## Design questions for the owner
- A resumed pane sends no request after the rejoin, so its old token stays valid alongside the new one indefinitely. This replaces the old rule that a token works once. It is harmless to the TUI today, because a pane handshake failure ends the client (`Broken`). But any other `TAKEOVER` client that idles after a takeover can't survive a second lost Welcome.
- If the person presses Ctrl-] d while the path is down, nothing can send `Detach`. Its leases are then held for 30 s after the old ssh dies; before this PR they were freed at once.
- The new DECISIONS row says "to be confirmed on the PR". I couldn't see the owner's decision cards, so please confirm it.

## Optional improvements
- `a_client_whose_connection_ends_is_kept_for_a_reconnect` goes red at base only if the server handles the old connection's EOF before the new `Hello`, which is up to the scheduler. The unit test `an_orphan_expires_only_by_its_own_connection` does guard the orphan path deterministically.

## Checks and validation gaps
- `python3 -I .github/scripts/changes.py check`, run on a scratch export of the head: "541 fragment(s) ok".
- **Not run:** `cargo test -p rusty_mux_server`, `-p rusty_mux_tui`, `-p rusty_mux_ctl` and clippy.
  - rustup can't write to `~/.rustup/tmp` under the sandbox, so I called the stable 1.98.1 cargo binary directly.
  - The offline build then failed on a missing `unicode-segmentation 1.13.3`, and access to `index.crates.io` was denied.
  - So no test result, at head or at base, is mine, and no trace above is executed. Allowing crates.io access, or providing a vendored cache, would let the regression tests described above be run.
- **Real sshd:** the Finding 3 race needs a run against a real sshd and OpenSSH client.
- **Windows:** `--ssh` is Unix-only, so nothing there to check.

**Verdict: changes required**
