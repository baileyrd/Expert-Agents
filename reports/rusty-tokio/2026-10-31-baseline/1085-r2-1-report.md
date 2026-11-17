<!-- rusty-tokio/1085-r2 run 1, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 491 s, 3070330 tokens, $2.16 -->

# Review of PR 1085 (Rusty-Mill/rusty_mill): changes required

- **Base:** `0beaf37853b445cb8e2a09485029f0d982d53d77`
- **Head reviewed:** `2a6e9dfda91768eb60bf11a3199b83a5000107ec`. I fetched `pull/1085/head` and it matches this SHA.

**Verdict: changes required.** K1, K2 and K3 are fixed. K4 is still open and now sits on rusty_lsp's main read loop. The K3 fix adds a new hang.

## Prior findings at this head

| ID | Status | Evidence |
|---|---|---|
| K1: a predicate panic poisons the channel | **Fixed.** `catch_unwind`, then drop the guard, then `resume_unwind`, the same order as tokio 1.53.3 `sync/watch.rs:909-925`. | Executed: `watch_wait_for_predicate_panic_does_not_poison_the_channel` fails at 349b521a (`PoisonError`, then the process aborts) and passes at head. |
| K2: a rejected version is inspected twice | **Fixed.** `*seen = guard.version` is set before the predicate runs (`watch.rs:194`). Closed-channel handling matches tokio `watch.rs:900-935`. | Executed: `watch_wait_for_inspects_each_version_once` fails at 349b521a (`left: 2, right: 1`) and passes at head. |
| K3: Stdout flush is a no-op | **Fixed, but it causes F3.** | Executed: `stdio_stdout_flush.rs` fails at 349b521a (nothing arrives within 5 s) and passes at head. |
| K4: a dropped `changed()` leaves its waker registered | **Still open.** It is now F1, and F2 is a related bug in the same function. | Executed, see F1. |

## Blocking findings

**F1 (P1): a dropped `changed()` or `wait_for` future leaves its waker in the channel, and rusty_lsp now does this on every message (K4)**
- **Where:** `crates/libs/async/rusty_tokio/src/sync/watch.rs:218-233`. The future pushes `cx.waker().clone()` and has no `Drop` that removes it. Only `send`, `send_modify` or dropping the `Sender` empties `wakers`.
- **How it happens in rusty_lsp** (traced from source, `crates/libs/protocol/rusty_lsp/src/server.rs:436-450`):
  1. Each loop iteration builds a new `writer_dead.changed()` arm inside `select!`.
  2. That arm returns Pending and pushes a waker.
  3. `read_message` wins and the `changed()` future is dropped, but its waker stays queued.
  4. `writer_dead_tx.send` only runs when the writer fails (`server.rs:306-314`).
  5. So a healthy session keeps one waker per incoming message until it ends.
- **Why it's new here:** on base, rusty_lsp used tokio's watch, whose `Notified` future removes itself on drop (tokio `changed_impl`, `watch.rs:983-1001`). This PR moves that code path onto rusty_tokio.
- **Second path:** `wait_for` inherits the same leak. An aborted request handler waiting in `wait_for_notifications` (`server.rs:650-655`) leaves a waker, which stays until the next `notes_done` send.
- **Executed probes** (scratch copy at head):
  - 1000 hand-polled `changed()` futures dropped after their first Pending left 1000 waker clones.
  - The `select!` loop pattern left 1000 after 1000 iterations.
  - 100 dropped `wait_for` futures left 100.
- **Who is hurt:** any long-running language server built on `Server::stdio` or `from_tcp`. Memory grows with message count and is never freed while the session lasts.
- **Fix:** give each waiting future a removable slot (key or id), drop it in the future's `Drop`, and clone wakers outside the lock where practical.
- **Regression test (`tests/sync.rs`):** poll `changed()` and `wait_for` once with an `Arc`-counting waker, drop them N times, and assert `Arc::strong_count` returns to its starting value. It fails at head.

**F2 (P1): the new `wait_for` (and `changed`) keeps only the first waker, so a re-poll with a different waker is never woken**
- **Where:** `watch.rs:229-232`. Once `registered` is true, later wakers are ignored.
- **Failure (executed):** poll with waker A and get Pending; poll with waker B and get Pending; then `send(1)`. B gets 0 wakes and A gets 1. This reproduces for both `changed()` and the new `wait_for`.
- **Trigger:** the future moves between tasks, or a combinator gives each child its own waker. The task waiting on B hangs.
- **Parity:** this breaks the `Future` contract (wake the waker from the latest poll). tokio's `Notified` replaces the stored waker when `!will_wake`.
- **Scope:** `changed()` already had this at base. `wait_for` is a new public API in this PR and inherits it.
- **Fix:** with F1's slot design, replace the stored waker when it won't wake the new one.
- **Regression test (`tests/sync.rs`):** the two-waker hand-polled sequence above, asserting B is woken exactly once.

**F3 (P1): `Stdout::flush` hangs after a write future was dropped mid-flight**
- **Where:** `crates/libs/async/rusty_tokio/src/io/stdio.rs:150-158`, together with `poll_locked` at `stdio.rs:227-251`.
- **Failure (executed):**
  1. `out.write(b"..")` is polled once. It takes the process-wide `stdout_lock`, starts the blocking-pool write, and returns Pending.
  2. That future is dropped, as a losing `timeout` or `select!` arm would be. The `Busy` future stays in `out.state` and keeps the lock guard; it is only released when someone polls that state again.
  3. `out.flush()` runs a separate `flush` state that waits for the same lock.
  4. Result: `timeout(3s, out.flush())` expires at head. At base (flush was a no-op) the same probe passes.
- **Other handles:** a flush on a *different* `stdout()` handle also hangs at head. The lock-holding stale write is older code (other handles' writes could already block on it), but flush now blocks there too.
- **Parity:** tokio's `Blocking::poll_flush` (tokio-1.53.3 `src/io/blocking.rs:146-171`) first drives the in-flight operation in its own state, then flushes. The new doc comment ("Its own state, so a flush never completes an unrelated in-flight write in its place") describes exactly the choice that causes the hang.
- **Who is hurt:** any caller that times out or `select!`s on a stdout write and then flushes or shuts down the handle. rusty_lsp's writer task never cancels its writes, so the LSP path isn't hit today.
- **Fix:** in `poll_flush`, first drive a `Busy` `self.state` to completion and return its error if it failed, then start the flush. Longer term, run the lock and the operation as a spawned unit so the guard isn't tied to the caller polling.
- **Regression test:** a new single-test file such as `tests/stdio_stdout_abandoned_write.rs` (it needs its own process because the lock is global). Hand-poll a write once, drop it, and assert `timeout(flush)` is `Ok`.

**F4 (P2): a comment this PR makes false**
- **Where:** `crates/libs/ui/rusty_term/l13/Cargo.toml:17` still calls rusty_lsp's `runtime` feature "tokio-backed".
- **Fix:** change it to "rusty_tokio-backed".

## Design questions
- `wait_for` runs the predicate while holding the channel's `std::sync::Mutex`, as tokio does under its read lock. A predicate that calls `borrow` or `send` on the same channel deadlocks. Should the `wait_for` docs say so, as tokio's docs do?

## Optional improvements
- The rusty_lsp fuzz harness (`fuzz/fuzz_targets/transport.rs`) still uses tokio's runtime as an executor, and `fuzz/Cargo.lock` lacks `rusty_tokio`. Switching it to `rusty_tokio::Runtime` would drop that tokio dependency.
- tokio wraps `wait_for` in a coop budget (`cooperative`); rusty_tokio doesn't.
- No test calls `Take::consume` with an amount larger than the limit to check the clamp (tokio `take.rs:123-125`).

## Ruled out
- **Lost wakes in `changed()` and `send`:** the version check and waker registration happen under one lock, and `send` updates the value and takes the wakers under that same lock, then wakes outside it.
- **Lock order between write and flush:** both run under `stdout_lock`, write first.
- **`Take` buffered reads:** match tokio `take.rs:107-127`.
- **`select!` arm order:** rusty_tokio's `select!` starts at a random branch (`macros.rs:52-54`), like tokio's default, so no arm-order change at moved call sites.
- **Tier and API scope:** rusty_lsp is tier B with tokio fully removed. The workspace entry keeps `default-features = false`, so l13 stays runtime-free. The public API break is recorded under G11 and in the change fragment as owner-approved; I didn't check that approval independently.
- **Not touched by this diff:** reactor code and task state.

## Checks
| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | Can't run here. It enables the io_uring reactor and the sandbox denies `io_uring_setup` (EPERM), so every runtime test fails at startup. |
| `cargo test -p rusty_tokio --features bytes,futures-io-compat,tracing` (epoll) | All new tests pass. The only failures are Unix-socket bind (EPERM) and multicast ("No such device") tests in files this diff doesn't touch; both are sandbox limits. |
| `cargo test -p rusty_tokio --features io-uring-reactor` | 573 fail, all from the same EPERM (584 occurrences in the log). Environment, not code. |
| `cargo clippy -D warnings` for `rusty_tokio` and `rusty_lsp`, all targets and features | Pass. |
| `cargo test -p rusty_lsp --all-features` | Pass: 269 passed, 6 ignored. |
| `cargo check --target x86_64-pc-windows-gnu` for both crates | Not run: the target's `std` isn't installed. |
| `python3 .github/scripts/changes.py check` | Pass: 379 fragments ok. |
| Probes, in scratch worktrees I removed afterwards | F1, F2 and F3 reproduce at head. F3 doesn't reproduce at base. |

One false lead: my first "base" probe run reused the head build from a shared cargo target directory. A clean base build in its own directory gives the base results above.

## Validation gaps
- Windows compile, plus Windows and macOS runs, especially Stdout flush on a Windows console; the flush test is `cfg(unix)`.
- The io_uring reactor suite, which needs a host that allows `io_uring_setup`.
- Building the rusty_lsp fuzz crate (`libfuzzer-sys` isn't available offline).
- CI's `required-gate` for this head.
