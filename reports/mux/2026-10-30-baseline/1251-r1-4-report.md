<!-- mux/1251-r1 run 4, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 455 s, 3320624 tokens, $2.38 -->

# Review of Rusty-Mill/rusty_mill PR #1251: changes required

- **Base:** `d05170b8cc082cffaf6c348411e82bea84b9632d`
- **Head:** `55d4bbf92e2d5f8f1b12aa0ffeddf57775fe0375`. `git fetch origin pull/1251/head` returned this SHA, so the review is pinned.
- **Commit:** one, "rusty_mux: keep a reconnecting client through a lost handshake".

I could not build or run any tests (see Checks), so every finding below is traced from the source, not executed.

## Step 1: what the diff touches, and what must hold

| Item | Invariant | Source |
|---|---|---|
| Hello `takeover`, Welcome `token` (`State::connect`, `takes_over`) | After a lost Welcome, the client still holds a token the server accepts. A wrong token gets a new client and leaves the original alone. | DECISIONS rows 37 and 41. mosh resends state until the other side acknowledges it, so the receiver's last confirmed state stays usable. |
| Connection end (`disconnect`, `expire`, `orphans`) | Only the connection that owns the client can orphan or expire it. A stale end is ignored. The lease survives for at most the 30 s grace. | Row 41 |
| `request` marking a token confirmed | Only a message from the connection that currently serves the client can confirm its token. | Row 41 |
| `disconnect-client` (catalog C33) | It works on any client except the caller, now including orphans. Exit codes are unchanged. | Row 28 |
| TUI clean exit (Detach), and pane hang-ups in general | A client that means to leave gives up its lease at once. | Row 41. Row 33: the TUI holds only the focused block's lease. |

There is no new catalog method, event or feature bit. The behaviour rides on `TAKEOVER` (bit 19), which only the TUI asks for (`tui/src/main.rs:82-89`). ctl and MCP never request it, so their clients are unaffected.

## Blocking findings

### 1. P1: a pane the TUI hides while it holds the lease keeps that lease for 30 s, and the TUI never asks again
**Location:** `tui/src/main.rs:1566-1570`, with `server/src/state.rs:238-241`

**Trigger:** another client zooms a different block in the window, or the person shrinks their terminal until the focused block has no room.

**Trace (source-traced):**
1. The window is split `[A | B]` and the person's focus is on B, so B's pane connection (client X, with `TAKEOVER`) holds B's lease.
2. An agent runs `zoom-block A`. `relayout` → `settle` → `sync`. B is no longer visible, so focus moves to A. The `gone` loop hangs up B's pane (`main.rs:1569`) without a Detach or ReleaseLease. `lease()` runs only after that.
3. The server's `disconnect` sees a token, so X becomes an orphan and keeps B's attachment and lease for 30 s (`state.rs:238-241`). Before this PR, X was dropped and the lease freed at once.
4. The window is unzoomed. `sync` opens a new pane connection (client Y) for B. The person moves focus to B. `AcquireLease` fails with `LeaseHeld`, held by X.
5. Every key the person types is silently dropped (`main.rs:1397`, input is only sent if the pane holds the lease).
6. When X expires 30 s later, the `LeaseChanged` event is ignored (`Pane::take` and `on_event` don't handle it). So typing stays dead until the person moves focus away and back.
7. Meanwhile an agent's `input` or `terminal.send-keys` on B is refused with `LeaseHeld`.

The shrink trigger takes the same path. `Layout::rects` drops a block that has no room (`core/src/layout.rs:173-176`).

**Who is hurt:**
- The person at the terminal: keys silently lost.
- An agent: refused input.

This also contradicts row 41's own claim that the TUI "leaves at once without its lease".

**Required correction:**
- Send `Detach` (or `ReleaseLease`) before every deliberate hang-up in `sync`, as the exit path now does.
- Preferably also retry the focused block's lease on `LeaseChanged { holder: None }`.

**Regression test:** a TUI test in `tui/tests/lifecycle.rs` using `real_server()`:
1. Split the window and focus the right block.
2. A second raw connection sends `Zoom` for the left block, then `Unzoom`.
3. Focus the right block again and type `echo z-$((1+2))`.
4. Assert `z-3` appears within `PATIENCE` (10 s, which is under the 30 s grace).

This goes red at head and green with the fix.

### 2. P2: a connection the server ends on an error stays open and silent for the 30 s grace
**Location:** `server/src/conn.rs:19`, `conn.rs:47-51`

**Trace (source-traced):**
1. `serve` returns `Err` when a frame fails to decode (`UnknownType`, a bad frame) or when `pty.write_all` fails.
2. For a `TAKEOVER` client, `disconnect` orphans it and `run` then sleeps 30 s.
3. `rd` is an `Arc<UnixStream>` half that lives in `run`'s frame, so the socket file descriptor stays open throughout that sleep. The write half's `Arc` was dropped with `write_frames`, and dropping it does not close the socket.
4. The orphan is no longer in `clients`, so nothing more is written either. The TUI gets neither an end of stream nor an answer for 30 s. Before this PR it saw the end at once.
5. On a local socket, which never reconnects, `rusty-mux` hangs for 30 s instead of reporting the error.

The comment at `conn.rs:34-37` ("Dropping the losing futures closes both socket halves") is now false for this path.

**Who is hurt:** the person at the terminal.

**Required correction:** `drop(rd)` and `drop(frames)` before the grace sleep, or spawn the expiry as its own task.

**Regression test:** in `server/tests/takeover.rs`, open a `TAKEOVER` connection, write an undecodable frame, and assert the end of stream arrives within 1 s. It passes at base and is red at head.

### 3. P2: comments, docs and the change fragment disagree with the code
- **`tui/src/main.rs:505`** says "Detached, a server drops each block's client at once". This is false. `disconnect` (`state.rs:238`) orphans every client that holds a token, attached or not, so each detached block client also stays in `list` for 30 s and its `client-disconnected` event arrives 30 s late.
- **The same claim appears in three more places:**
  - `state.rs:233` ("A client that means to leave detaches first").
  - Row 41 ("it leaves at once").
  - The fragment's "Known limitations", which names only the control client.

  An agent that lists or disconnects clients (C33) sees one stale entry per pane.
- **DECISIONS row 37 (step 2a) was not marked superseded.** That is this file's convention, as row 6 shows. Row 37 still says a takeover needs a "still-connected client and its current token" and that "a client whose old connection already closed is gone, and its lease is free". Row 39 (step 2c) still says "A local socket keeps today's behaviour", yet the local TUI also asks for `TAKEOVER` and now gets the grace.
- **`proto/src/message.rs:328-329`** says "Each Welcome replaces it". An older confirmed token now stays valid.
- **`tui/tests/lifecycle.rs:1143-1145`** says "since hanging it up ends that client". With this PR it no longer does.

**Correction:**
- Fix the comments, the fragment and row 41 so they say block clients linger too, or make an explicit leave drop the client at once.
- Strike through rows 37 and 39 where they no longer hold.

There is no test for the docs. A server test that detaches, closes, and asserts the client is listed (or not) pins whichever behaviour is chosen.

### 4. P2: coverage gaps for changed behaviour
- **TUI Detach on exit (`main.rs:508-512`) has no test that goes red without it.** `a_client_that_detached_before_leaving_holds_no_lease` (`server/tests/takeover.rs:155`) drives the server directly. It passes at base too, because its Detach waits for Done.
  - **Where it belongs:** a lifecycle test. Run `rusty-mux` against `real_server()`, press Ctrl-] d, then have a raw client attach and `AcquireLease` at once, expecting Done.
- **`disconnect-client` on an orphan (`state.rs:524`) is untested.**
  - **Where it belongs:** `server/tests/labels.rs` or `takeover.rs`. Close a `TAKEOVER` client, disconnect it from a second client, expect Done, then check the lease is free.

## Design questions (documented behaviour that hurts)
1. **A token is "confirmed" by a request, not by presenting it.** A resumed pane connection sends no request until the person types in it. So `confirmed` stays at the pane's first-connection token across later takeovers.
   - **Trace:** takeover #1 succeeds and the client holds Tp2 (server: current Tp2, confirmed Tp1). The pane is idle. Takeover #2 presents Tp2 and the server rotates to Tp3, but that Welcome is lost. The client holds only Tp2, while the server accepts Tp3 and Tp1. The retry fails.
   - **Reachability:** the TUI cannot hit this today, because a pane failure ends it rather than retrying. But it breaks the "lost handshake" invariant for any client that does retry.
   - **Possible fix:** count the token presented in the Hello as confirmed, since presenting it proves the client holds it.
   - Row 41 settles the request-based rule, so this is a question for the owner, not a finding.
2. **A local `rusty-mux` never reconnects but still asks for `TAKEOVER`.** So a killed local terminal now holds its lease for 30 s and gains nothing from the grace. Should local mode stop asking for `TAKEOVER`?
3. **Row 41 cites owner decision cards "to be confirmed on the PR".** I could not see that confirmation.

## Optional improvements
- `a_client_whose_connection_ends_is_kept_for_a_reconnect` depends on scheduling. If the server reads the new Hello before the old connection's end, it takes over a still-connected client and passes at base too. The unit test `an_orphan_expires_only_by_its_own_connection` does cover the orphan path.
- The `lifecycle.rs` marker move (taking the output position before `wait_logins`) is correct.

## Failure classes ruled out
| Class | Why it's ruled out |
|---|---|
| Stale connection | `expire` compares the owning cancel's pointer (`Rc::ptr_eq`). A replaced connection fails `served_by`. `request` confirms only on the serving connection. |
| Split stream, deadlines, last output, silent truncation, outside config | Not touched by the diff. |
| Secrets | Tokens are compared in constant time. `Tokens` is never logged. No files are written. |
| `Notify` caveats | `orphans` uses the `Notify` only for identity and never waits on it. |

## Checks and validation gaps
| Check | Result |
|---|---|
| `python3 .github/scripts/changes.py check` | Ran it: "541 fragment(s) ok". |
| `cargo test -p rusty_mux_server`, `-p rusty_mux_tui`, `-p rusty_mux_ctl`, and clippy | **Not run.** The sandbox denies `index.crates.io` (cargo failed resolving the workspace's `boxcars` dependency). An offline build from a trimmed scratch workspace also failed: crates such as `unicode-segmentation` and `anes` aren't in the local cache. I did not see any of these pass, at head or at base. |

Remaining validation gaps:
- **Base-vs-head test runs:** the base/head claims about which tests go red were not executed, nor were the regression tests proposed above.
- **mosh and tmux comparison:** I did not clone mosh or tmux; their behaviour in the invariant table is from my own knowledge.
- **Owner confirmation:** I could not see the owner confirmation that row 41 refers to.

## Verdict
**Changes required.** One P1 (finding 1) and three P2s (findings 2–4).
