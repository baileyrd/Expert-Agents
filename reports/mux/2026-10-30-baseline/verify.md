# mux/1251-r1 claim verification (baseline, 5 runs)

Head 55d4bbf9, base d05170b8, origin/main ad1c3e37. The key's fix 70559a85 is on main.
Probe worktrees (mine): scratchpad/grade-mux/{head,main}. Probe file
`crates/apps/rusty_mux/crates/server/tests/probe_grade.rs`, plus a `Client::raw()` accessor
added to `tests/common/mod.rs`, both only in those worktrees. Run with
`cargo test -p rusty_mux_server --test probe_grade -- --nocapture --test-threads=1`.

Key matches: K1 = run 2 F1, run 5 F2 (both caught). K2 = run 1 F2, run 2 F2, run 3 F2,
run 4 F1 (all caught). Runs 1 and 4 also name K1 in their Design questions; those sections are not graded.

## A. The exit Detach goes unread before shutdown (run 1 F1)

**Class: real. On main.**
- Trace: tui main.rs:508-513 writes `Detach`, then calls `shutdown(Both)` without waiting for
  `Done`. The server's `run` (conn.rs:37-45) races `serve` against `write_frames` in an unbiased
  `select!`, which starts each poll at a random branch (rusty_tokio macros.rs, rule 3). If
  `write_frames` hits EPIPE first, the `Detach` is never read. `disconnect` (state.rs:238-241)
  then orphans the client together with its lease.
- Executed with `probe_detach_then_shutdown_while_flooding`: 30 iterations. A TAKEOVER client
  leases a block running an `echo` loop, writes a raw `Detach` frame and calls `shutdown(Both)`.
  A second client attaches and retries `AcquireLease` for 2 s.
  - Head: 7/30 iterations still failed with "input lease is held by client ...".
  - Main: 6/30.
- First probe attempt: B was not attached, so every iteration failed with a different error.
  Corrected and re-run before counting.

## B. Comments and docs the PR makes false (run 1 F3, run 2 F3, run 3 F4, run 4 F3, run 5 F4)

**Class: real. On main.** At least these mismatches exist:
- tui main.rs:505-507 (main :518) says "Detached, a server drops each block's client at once".
  `disconnect` (state.rs:238-241) orphans any client with a token, attached or not, so a
  detached pane client stays for 30 s.
- main.rs:178-180 (main :189) `Missed::Broken` says "hanging the fresh connection up ends that
  client". It is now kept for the grace.
- DECISIONS.md:37 (2a: "A client whose old connection already closed is gone, and its lease
  is free"; "a used token worthless") and :39 (2c: "A local socket keeps today's behaviour").
  Neither is marked superseded, and the local TUI also asks for TAKEOVER (main.rs:86-89).
  Unchanged on main.
- proto message.rs:329 (main :347) says "Each Welcome replaces it". The confirmed older token
  stays valid.
- tui/tests/lifecycle.rs:1143-1145 says "hanging it up ends that client".
- The change fragment's limitations name only the idle control client. On main it is amended
  for hidden blocks but still omits detached block clients lingering.

Weaker items: state.rs:233 "means to leave detaches first" is ambiguous rather than false.
SKILL.md:60 / AGENT-PLACEMENT.md:39 ("a user typing in that block holds it") is a stretch.
The finding stands on the items above.

## C. No test goes red without the TUI's exit Detach (run 1 F4, run 2 F4, run 3 F5, run 4 F4, run 5 F5)

**Class: real. On main.**
- server/tests/takeover.rs:155 `a_client_that_detached_before_leaving_holds_no_lease` uses
  `request()`, which waits for `Done` (common/mod.rs:250-262). At base, the ended connection
  dropped the client anyway, so the test passes there too.
- tui/tests/*.rs never checks the server-side lease after a detach. The only lease references
  are the scripted server's at lifecycle.rs:86-116. No new test on main either.
- Not separately verified:
  - run 2's sub-claim that `a_client_whose_connection_ends_is_kept_for_a_reconnect` is racy at
    base;
  - run 5's identical sub-claim.
- Run 5's "replay window" sub-bullet: row 41 documents a used token as valid until a request
  proves the new one. So that part is a design question. The finding as a whole is real on its
  main item.

## D. Retry backoff reaches the 30 s grace (run 3 F1)

**Class: real (source trace). On main.**
- A Welcome that times out after the Hello was sent is `Missed::Retry` (main.rs:788, :806 path
  via `req.welcome().map_err(retry)`). The server has already taken the client over.
  `rejoin.discard()` (main.rs:213-215) kills the fresh ssh, so the server orphans the client
  and the 30 s `ORPHAN_GRACE` starts (state.rs:46).
- `retry()` (main.rs:1260-1262) waits `min(1 << min(tries,5), 30)` s. Once `tries` reaches 5,
  the next attempt starts 30 s later, then still has to log in and connect. Its Hello lands
  after `expire`, `takes_over` finds no tokens, and the client ends with "the server let this
  client go".
- Unchanged on main: main.rs:1334, state.rs:48.
- No doc says the grace must cover the backoff, so this is not a design question.

## E. Over --ssh the exit Detach races ssh being SIGKILLed (run 3 F3, run 5 F3)

**Class: real (source trace). On main.**
- main.rs:508-516 writes `Detach`, shuts down the socket and hangs up the panes. `drop(app)`
  then joins the pane readers (they end at once after the shutdown) and drops the forward.
  `Forward::drop` (ssh.rs:161-166) calls `child.kill()`, which is SIGKILL.
- Nothing waits for `Done`, so ssh can die before relaying the bytes. The client is then
  orphaned with its lease. This is the same lost-Detach outcome A executes locally.
- Not executed over ssh.

## F. A connection the server ends on an error stays open for the grace (run 4 F2)

**Class: real (executed). On main.** `probe_bad_frame_connection_end`:
- A TAKEOVER client sends a frame of type 0x7f. The server logs "unknown message type 0x7f",
  but the client's read sees no EOF within 5 s, at both head and main.
- A plain (non-TAKEOVER) client doing the same sees EOF in under 1 ms.
- Cause:
  - `rd` lives in `run`'s frame across the `ORPHAN_GRACE` sleep (conn.rs:19, 47-50).
  - `OwnedUnixWriteHalf` is just an `Arc<UnixStream>` with no shutdown on drop (rusty_tokio
    unix_socket.rs:1131, 964).
- The comment at conn.rs:34-36 ("Dropping the losing futures closes both socket halves") is
  false on this path.

## G. A killed rusty-mux locks the next one out of typing with no time limit (run 5 F1)

**Class: real (source trace). On main.**
- A killed TUI's pane client is orphaned holding the lease (state.rs:238-241; the TUI asks
  TAKEOVER locally too, main.rs:86-89).
- The new TUI's `AcquireLease` is refused. `Pane::take` sets `leased = false` and clears
  `asking` (main.rs:985-991).
- `lease()` is only called from `sync()` (main.rs:1585, 1600-1605), and tui/src/main.rs has no
  `LeaseChanged` handling at head or main. When the orphan expires, nothing asks again, and
  keys are dropped at main.rs:1397 until a resize or focus change.
- The fragment's limitation says "up to 30 s", so this is not documented as intended.
