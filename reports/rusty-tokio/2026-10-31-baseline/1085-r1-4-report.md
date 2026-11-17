<!-- rusty-tokio/1085-r1 run 4, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 448 s, 3017666 tokens, $2.17 -->

# PR review: Rusty-Mill/rusty_mill #1085, rusty_lsp onto rusty_tokio (#911)

- **Base:** `0beaf37853b445cb8e2a09485029f0d982d53d77`
- **Reviewed head:** `349b521ab20b790caa53174a1b802057d30237ea`. I fetched `pull/1085/head` and it matches this SHA.
- **Commits:** `33a7f5ef0` (refactor(rusty_lsp)!), `349b521ab` (docs(inventory))

**Verdict: changes required.** The migration makes rusty_lsp's server leak one waker for every message it reads; on tokio the same code leaks nothing (executed at both ends). The new `wait_for` also breaks tokio's documented no-poisoning contract.

## Invariants for everything the PR touches

| Item | Invariant | Where it stands at the head |
|---|---|---|
| `watch::changed` (new free fn, watch.rs:204) | A future dropped after `Pending` leaves no waker behind. tokio waits on `Notified`, which unlinks itself on drop (tokio watch.rs:983-1001, notify.rs:1038). | **Broken.** The waker stays in `Inner::wakers` until the next send (Finding 1). |
| `watch::Receiver::wait_for` (watch.rs:180) | Cancel safe. A panicking `f` poisons nothing (tokio watch.rs:849-868, 900-934). Checks the value before waiting. Returns `Err` on close without a match. | Close and recheck semantics match tokio. No lost wake: a send between the check and the `changed` call is caught because `seen` is not advanced when `f` is false. **Cancel safety and no-poison are broken** (Findings 1, 2). |
| `Sender::send`/`drop` | Wakers are woken after the lock is released. | Holds (watch.rs:79-83, 119-125). |
| `Take: AsyncBufRead` | Same as tokio `util/take.rs`: clamp to the limit; `limit == 0` means EOF without touching the inner reader. | Matches. Its only caller (transport.rs:74-77) behaves as on tokio. |
| `Debug for Notify` | Exists. | Holds (tokio's string differs; nothing depends on it). |
| rusty_lsp `select!` (server.rs:436) | Random start, as tokio's default. | Holds (macros.rs:52-56). |
| `CancelToken::cancelled` (cancel.rs:74-88) | `enable()` registers before the recheck, so no lost cancel. A dropped wait leaves nothing behind. | No lost wake. **Leaves a waiter behind** (Finding 3). |
| `Semaphore::acquire_owned` under `$/cancelRequest` abort (server.rs:734) | An aborted waiter leaves the queue and passes on any permit it was granted. | Holds: `Claim::drop` (semaphore.rs:140-160) dequeues or returns permits, then wakes outside the lock. |
| Public API | The breaking changes are deliberate and disclosed. | Disclosed in the fragment and CHANGELOG. The workspace dependency has `default-features = false` (Cargo.toml:486), and l13 keeps `runtime` off, so no workspace crate is affected. |

## Blocking findings

### 1. P1: rusty_lsp's read loop retains one waker for every inbound message, for the life of the connection
**Executed.**

- **Where:** `crates/libs/async/rusty_tokio/src/sync/watch.rs:204-222` (`changed` never deregisters) and `crates/libs/protocol/rusty_lsp/src/server.rs:436-451`. The PR makes this reachable by replacing `tokio::sync::watch` in rusty_lsp.
- **Trigger:** every LSP connection, on every message that has to wait for input.
- **Trace:**
  1. Each loop iteration builds a new `writer_dead.changed()`.
  2. `select!` polls it. It is `Pending`, so it pushes `cx.waker().clone()` into `Inner::wakers` (watch.rs:215-217).
  3. `read_message` wins, and `select!` drops the `changed()` future. Nothing removes its waker.
  4. `writer_dead_tx` only sends when a write fails, so the vector is never drained while the server is healthy.
  - Result: a clone of the serve task's waker accumulates per message for the whole editor session (a `didChange` per keystroke and more). Teardown then wakes the same task N times.
  - The same thing happens to aborted request tasks in `wait_for_notifications` (server.rs:650-656, 728). There each retained waker lasts until the notification worker's next send.
- **Executed evidence:**
  - A hand-polled `Server::serve` with a counting waker received 300 framed notifications. Waker clones went 4 → 304 at the head, and stayed 4 → 4 at the base on tokio.
  - Hand-polling 1000 `rx.changed()` futures and dropping them, and the same with 1000 `rx.wait_for(..)` futures, left 1001 references to the counting waker each time. The expected count is 1.
- **Who is hurt:** every rusty_lsp server. Memory and task references grow without bound over a session.
- **Required correction:**
  - Make `changed`/`wait_for` deregister on drop. Either key each waiter (an id or slab) and remove it in a `Drop` guard, or build them on a `Notify` whose `Notified` unlinks on drop, as tokio does.
  - Also replace a stored waker when it no longer `will_wake` the current one.
  - Correct the fragment's "matching tokio" claim until this is fixed.
- **Regression tests (both fail at the head):**
  - `crates/libs/async/rusty_tokio/tests/sync.rs`: hand-poll N `changed()` and `wait_for()` futures with an `Arc<impl Wake>` waker, drop each after `Pending`, and assert `Arc::strong_count == 1`.
  - `crates/libs/protocol/rusty_lsp/tests/integration.rs`: the counting-waker `serve` probe above, asserting the count stays flat across N messages.

### 2. P1: a panicking `wait_for` predicate poisons the channel and can abort the process
**Executed.**

- **Where:** `crates/libs/async/rusty_tokio/src/sync/watch.rs:191-195`.
- **Trigger:** a predicate that panics. No workspace caller has one today (rusty_lsp's is `seq >= watermark`). The grade comes from the impact.
- **The contract it breaks:** tokio 1.53.3 watch.rs:866-868 says "If and only if the closure `f` panics. In that case, no resource owned or shared by this `Receiver` will be poisoned." tokio does this with `catch_unwind`, dropping the guard before `resume_unwind` (watch.rs:910-922), and its lock wrapper ignores poisoning (loom/std/rwlock.rs:16-21). The fragment says `wait_for` matches tokio.
- **Trace:**
  1. `f` panics while it holds the `std::sync::Mutex` guard, so the guard drops during unwinding and poisons the mutex.
  2. After that, every `lock().unwrap()` in `send`, `borrow` and the `Drop` impls panics.
  3. If a `Receiver` or `Sender` is dropped during that unwind, its `Drop` panics inside a panic, and the process aborts.
- **Executed evidence:** after a predicate panic, `tx.send(1)` panicked at watch.rs:72. `Receiver::drop` then panicked at watch.rs:240 during unwinding, and the test binary aborted.
- **Required correction:** run `f` under `catch_unwind` and release the guard before `resume_unwind`. Better still, have watch take its lock through the poison-tolerant `lock()` helper that semaphore.rs:117-120 already uses.
- **Regression test** (`tests/sync.rs`): a predicate that panics; catch it, then assert `tx.send` succeeds and that dropping both ends does not panic.

### 3. P2: `CancelToken::cancelled()` keeps a waiter for every dropped wait until the token is cancelled or dropped
**Executed on the primitive; the rusty_lsp path is source-traced.**

- **Where:** `crates/libs/async/rusty_tokio/src/sync/notify.rs:240-300` (`Notified`/`OwnedNotified` have no `Drop`), reached through `crates/libs/protocol/rusty_lsp/src/cancel.rs:74-88`. The swap from `tokio::sync::Notify` is in this PR.
- **Trigger:** a handler that loops `select! { _ = token.cancelled() => …, r = step() => … }`, which is the "select!-friendly" use cancel.rs:30 advertises.
- **Trace:** each iteration's `Notified` is registered (by `enable()` and `poll`) and then dropped. Its `Arc<WaiterState>` and waker stay in `Inner::waiters` until `notify_waiters`, or until the whole token drops. Growth is bounded by the request's lifetime, but a long request (for example workspace indexing) grows by one entry per iteration.
- **Executed evidence:** hand-polling 1000 `Notified` futures and dropping each left 1001 references to the counting waker.
- **Required correction:** add a `Drop` for `Notified`/`OwnedNotified` that removes its `WaiterState` from `waiters`. If it was already fired by `notify_one`, the drop should pass the notification on, as tokio's `drop_notified` does.
- **Regression test:** a counting-waker drop test in `tests/sync.rs`.

## Design questions
- The fragment and CHANGELOG add `Send` bounds to `Server::new`, `read_message` and `write_message`, and say the owner approved the break. I couldn't confirm from sources that `Send` is required rather than incidental. If it isn't required, dropping it narrows the break.

## Pre-existing code outside this diff (for the owner, not counted against this PR)
- `Notify::notify_one`/`notify_last` (notify.rs:89-97, and the next function) pop a waiter whose `Notified` may already have been dropped, then fire into it, so the wake is lost. rusty_lsp only calls `notify_waiters`, so this PR doesn't reach it. Source-traced only; the same `Drop` fix as Finding 3 closes it.

## Failure classes ruled out
- **Lost wake in `wait_for`:** none. `seen` advances only on a match or in `changed`, so a send in the window makes `changed` return `Ready` at once.
- **Waker woken under the lock:** none; `send` and both `Drop` impls release the lock first.
- **Re-entrant predicate:** both runtimes behave alike. Calling `borrow()` inside `f` relocks under the guard on both (std `RwLock::read` may also panic or deadlock when re-entered).
- **Abort and task state:** nothing new; the semaphore handles aborted waiters correctly.
- **Reactor:** no reactor code changed. `from_tcp`'s `into_split` maps directly onto rusty_tokio's halves.
- **`select!` order:** fair by default, same as tokio.
- **Stdio:** reads and writes go through the blocking pool, as on tokio, with the same uncancellable blocking read.
- **Tier C:** rusty_lsp is tier B and has no tier C dependency.
- **Inventory:** #911 is a valid point in section 3's order. The per-crate table is a snapshot by convention (rk-constrain's row is also unchanged after its move).
- **New tests at the base:** they don't compile there because the APIs are missing, so they do guard the changes. There is no test for the drop or cancel behaviour (Findings 1-3).

## Checks run

| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | **Not valid here.** The sandbox returns EPERM for `io_uring_setup`, so every `Runtime::new()` fails (4 lib tests and most integration tests). Validation gap. |
| `cargo test -p rusty_tokio` (default features, epoll) | All new tests pass (`watch_wait_for_*` ×3, `notify_is_debug`, `take_over_a_buffered_reader…`, `take_with_no_limit_left…`). 33 failures, all sandbox socket errors (EPERM, ENODEV, a SUN_LEN path) in `net`, `net_parity`, `readiness`, `try_read_write` and the `unix*` suites, none of which the PR touches. |
| `cargo test -p rusty_tokio --features io-uring-reactor` | Not runnable (io_uring is blocked). Validation gap. |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Pass |
| `cargo check -p rusty_tokio --target x86_64-pc-windows-gnu --all-targets` | Not run: the target isn't installed (E0463). Validation gap. |
| `cargo test -p rusty_lsp --all-features` | Pass (179 + 3 + 5 + 72 + 8 passed, 6 ignored) |
| `cargo clippy -p rusty_lsp --all-targets --all-features -- -D warnings` | Pass |
| `python3 .github/scripts/changes.py check` | Pass (379 fragments ok) |
| Executed probes (scratch copies of the head and base under `$TMPDIR`, since removed) | Findings 1-3 reproduced at the head; the `serve` probe passes on tokio at the base. |

**Validation gaps:** io_uring and all-features runs (need an unsandboxed Linux host), Windows build and execution (stdio and TCP paths), macOS (kqueue).
