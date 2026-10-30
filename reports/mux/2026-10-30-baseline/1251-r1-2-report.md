<!-- mux/1251-r1 run 2, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 364 s, 2300654 tokens, $1.79 -->

I found two defects that block this PR, plus doc and test-coverage problems, so the verdict is **changes required**. Nothing was built or tested here: crates.io was blocked and the local crate cache doesn't have the versions this tree needs. Every trace below comes from reading the code.

**Pinned:** Rusty-Mill/rusty_mill PR #1251, base `d05170b8cc082cffaf6c348411e82bea84b9632d`, head `55d4bbf92e2d5f8f1b12aa0ffeddf57775fe0375`. The fetched `pull/1251/head` matches the head SHA. The PR is one commit and touches 7 files.

## What the diff touches, and what must hold
- **Hello/Welcome token rotation** (`state.rs` `connect`, `takes_over`, `Tokens`). Rule from the review class (#1243, #1251): after any lost handshake, the client still holds a token the server accepts. mosh never discards a state the client may still hold until the client acknowledges it (`transportsender-impl.h`, acknowledged-state tracking).
- **Connection end** (`conn.rs` `run`, `disconnect`, `expire`, `drop_client`, `orphans`). Rule: only the connection that currently owns a client may end it, so generations are respected. The PR's own decision row adds that a client that means to leave holds no lease.
- **`Disconnect`** (`state.rs:524`). Rule (C33): it frees the target's lease at once.
- **TUI exit and pane teardown** (`main.rs:505-512`, plus the existing `sync` at 1566-1570). Rule: a pane that lets go of a block also lets go of its lease. The 2c decision says reconnect only resumes.
- **No catalog method, event or feature bit changes.** ctl and MCP never request `TAKEOVER` (grep finds it only in the TUI, `main.rs:86,89`), so agents never create orphaned clients. They can still be blocked by one (finding 2).

## Blocking findings

### 1. P1: a token the client provably holds stops working after a second takeover
- **Where:** `crates/apps/rusty_mux/crates/server/src/state.rs:159-164`, which carries the old `confirmed` forward on takeover. The comment at `state.rs:954-956` states the opposite of what happens.
- **Trigger:** a `TAKEOVER` client sends no request between two takeovers, and the second takeover's Welcome is lost. Today's TUI doesn't hit this on its control connection, because `take_over` sends `List` right after Welcome (`main.rs:812`). Its block connections send nothing after a resume, but a failure there already ends the client (`Missed::Broken`). It is still a hole in the server contract this PR defines, and it is exactly the class it claims to fix.
- **Trace (source-traced):**
  1. The client holds token T0, and the server has `{current: T0, confirmed: T0}`.
  2. The path drops. Takeover 1 presents T0. The server sets `{current: T1, confirmed: T0}`, and the Welcome carrying T1 arrives.
  3. The client sends no request.
  4. The path drops again. Takeover 2 presents T1, which matches `current`. The server sets `{current: T2, confirmed: T0}` (the old `confirmed` is carried forward). The Welcome carrying T2 is lost and the client gives up.
  5. The client retries with T1. T1 is neither T2 nor T0, so it gets a new client.
  6. The TUI's `reach`/`take_over` maps that to `Missed::Dropped`: "the server let this client go". The orphaned client, with its attachment and lease, expires 30 s later.
- **Who is hurt:** the person at the terminal, or any other client that uses `TAKEOVER`.
- **Fix:** a successful takeover proves the client holds the token it presented. In `connect`, set `confirmed = Some(takeover.token)` when `continued` is `Some`.
- **Regression test** (in `server/tests/takeover.rs`):
  1. Connect with `block_for`, so T0 is confirmed.
  2. `taking_over` with T0, keep the T1 from its Welcome, send nothing, drop it.
  3. `taking_over` with T1 (Welcome "lost"), drop it.
  4. `taking_over` with T1 must return the same id. It is red at head.

### 2. P1: a pane leaving the layout is hung up without Detach, so its lease is held for 30 s
- **Where:** `crates/apps/rusty_mux/crates/tui/src/main.rs:1566-1570`, made reachable by `state.rs:238-241`. That loop calls `pane.hang_up()` on blocks that are no longer visible, and it runs *before* `self.lease()`, so the focused pane never sends `ReleaseLease`. Before this PR, the server dropped that client at once. Now it keeps it as an orphan, still attached and still holding the lease, for `ORPHAN_GRACE`.
- **Trace (source-traced):**
  1. A person has `rusty-mux` on a split window, A|B, with A focused and leased.
  2. An agent runs `rusty-mux-ctl zoom-block B` (catalog `zoom-block`, `schema.rs:741`).
  3. The TUI gets the window-layout event. `relayout` finds nothing leaving (the layout is unchanged), so it calls `settle` and then `sync`. `visible()` is now only B, so A's pane is removed and hung up while it still holds the lease.
  4. The server orphans A's client, still holding the lease.
  5. For 30 s, any agent trying to take A's lease is refused with `LeaseHeld`.
  6. If the person unzooms within 30 s, the new pane for A is also refused the lease.
- **Who is hurt:** an agent driving ctl or MCP, and the person at the terminal.
- **Not covered by the PR's docs:** the change fragment's "Known limitations" only covers a client that dies without detaching, and the DECISIONS row only covers "a lost client". This is a live client.
- **Fix:** send `Detach` (which releases the lease through `unattach`) before every `hang_up` on a pane being dropped. Simplest is to do it inside `Pane::hang_up`, which also covers exit.
- **Regression test** (TUI lifecycle test):
  1. Split, with A focused.
  2. Run ctl `zoom-block B`.
  3. Wait with ctl `wait --event lease-changed` for A's holder to become none, well inside 30 s. It is red at head.

### 3. P2: docs and comments the PR makes false
- **Prior decisions now contradicted, not amended:**
  - `docs/projects/mux/DECISIONS.md:37` (step 2a) still says: a takeover names "a still-connected client and its current token"; "A client whose old connection already closed is gone, and its lease is free"; "rotating the token on each Welcome makes a used token worthless". The PR also changed `a_wrong_or_used_token_starts_a_new_client`, so a used token is now only refused after a request.
  - `DECISIONS.md:39` (step 2c) still says "A local socket keeps today's behaviour". The local TUI also asks for `TAKEOVER` (`main.rs:86,89`), so a killed local `rusty-mux` now holds its lease for 30 s.
  - The new row at `DECISIONS.md:41` should say it supersedes these.
- **"At once" claims that are false:**
  - `tui/src/main.rs:505`: "Detached, a server drops each block's client at once." `disconnect` orphans every token-holding client whether or not it is attached (`state.rs:238-241`).
  - `DECISIONS.md:41`: "so it leaves at once". It lingers for 30 s.
- **The change fragment** says only "an idle control client lingers in `list`". Every block client lingers too.
- **Fix:** amend the 2a/2c rows (or mark them superseded), correct the comment, and correct the fragment's limitations text.

### 4. P2: changed behaviour with no test that goes red without it
New tests, judged by reading the code since nothing could run here (see Checks):
- `a_client_that_detached_before_leaving_holds_no_lease` passes at base.
- `without_the_feature_a_client_goes_with_its_connection` passes at base.
- `a_client_whose_connection_ends_is_kept_for_a_reconnect` is only red at base if the server handles the old connection's end before the new Hello. If the Hello wins, base takes over the still-live client and passes.
- `a_takeover_whose_welcome_was_lost_can_be_retried_with_the_old_token` is deterministically red at base. Good.
- The unit test `an_orphan_expires_only_by_its_own_connection` does not compile at base. Good.

Behaviour with no guarding test:
- **TUI detach on clean exit** (`main.rs:508-512`). Needs a TUI lifecycle test: exit cleanly, then a second client acquires the lease well before 30 s.
- **`Disconnect` of an orphan** (`state.rs:524`), which the fragment promises. Needs a server test: orphan a leased client, `Disconnect` it from another client, and the lease is free at once.

## Failure classes checked
- **Lost handshake:** finding 1. The single-loss case is covered by the confirmed token.
- **Stale connection:** ruled out. `expire` compares `Rc::ptr_eq` against the cancel of the connection that orphaned the client, a takeover removes the orphan entry, and `serve` checks `served_by` before applying a request, so an old connection can't confirm a token. Hidden blocks: finding 2.
- **Split stream:** no byte filters are touched.
- **Deadlines:** no new reads. The orphan task sleeps 30 s holding the read half and a 64 KiB buffer. That's bounded and not a defect.
- **Last output and silent truncation:** not touched.
- **Outside config:** no ssh or tempdir changes.
- **Secrets:** no token is logged. `Tokens` derives `Debug` but is never printed, and tokens are still compared in constant time.
- **Timing in tests:** moving the `from` mark earlier in the split test is safe. While the path is down the render is a diff and repaints nothing, so only the post-rejoin full redraw can contain both markers. No new `Notify` waits.

## Design questions for the owner
- The DECISIONS row says "to be confirmed on the PR" and its source is decision cards I can't verify. That 30 s lease hold, and a used token staying valid until the next request (indefinitely on an idle block connection), need the owner's explicit sign-off.
- When the TUI quits (Ctrl-] d) while the path is lost, no Detach is sent (`app.lost.is_some()`), so its leases are held for 30 s.

## Optional improvements
- Make `a_client_whose_connection_ends_is_kept_for_a_reconnect` deterministic: wait until the server has actually seen the old connection end before taking over. A second client watching `list` won't work, because an orphan stays listed.
- The fragment's "a reconnect that gave up midway can still resume" only holds while the control Welcome is pending. A give-up after `List`, or during a block connection's handshake, still ends the client (`Missed::Broken`), as before this PR.

## Checks and validation gaps
- `python3 -I .github/scripts/changes.py check`: ran it, exit 0, "541 fragment(s) ok".
- `cargo test -p rusty_mux_server`, `-p rusty_mux_tui`, `-p rusty_mux_ctl`, clippy, and running the new tests at base: **not run**. crates.io was blocked, and the cached tokio doesn't match what `rusty_tokio` requires, even with unrelated crates excluded in a scratch copy. None of these are verified. I didn't use the `tests-head.log` I found in the scratch directory, because I didn't produce it.
- Real sshd and Windows behaviour weren't exercised. Finding 2 can be settled with the stand-in `ssh` in `tui/tests/lifecycle.rs` once the build works.

**Verdict: changes required.**
