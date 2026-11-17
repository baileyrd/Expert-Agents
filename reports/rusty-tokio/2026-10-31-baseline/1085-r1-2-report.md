<!-- rusty-tokio/1085-r1 run 2, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 350 s, 2949694 tokens, $2.13 -->

I found three blocking defects, so the verdict is **changes required**. Two of them are leaks that `rusty_lsp` did not have on tokio; the third is a broken tokio contract in the new `wait_for`.

**Pin**
- **Repository and PR:** Rusty-Mill/rusty_mill #1085
- **Base:** `0beaf37853b445cb8e2a09485029f0d982d53d77`
- **Head:** `349b521ab20b790caa53174a1b802057d30237ea`. The fetched `pull/1085/head` matches it.
- **Commits:** two. `33a7f5ef0` moves `rusty_lsp` onto rusty_tokio (#911); `349b521ab` updates the inventory.
- **Changes in rusty_tokio:** `watch::Receiver::wait_for`, `AsyncBufRead for io::Take`, and `Debug for Notify`.

## Blocking findings

### F1 (P1): every message the server reads leaves a waker behind (executed)
- **Where:** `rusty_tokio/src/sync/watch.rs:204-222`, reached from `rusty_lsp/src/server.rs:436-450`.
- **Trigger:** any long-running server. It happens on every read that waits for input.
- **Invariant broken:** a future dropped after `Pending` must leave nothing registered. tokio's `changed()` is documented cancel safe and removes its waiter on drop.
- **The defect:** rusty_tokio's `changed()` pushes `cx.waker().clone()` into `Inner::wakers` and has no `Drop` to take it out. Only `send`, `send_modify` and `Sender::drop` empty that list.
- **Trace (source-traced):**
  1. `run_loop` builds `writer_dead.changed()` on every pass of its `select!`.
  2. While the editor is idle, `read_message` is `Pending`, so `select!` polls every arm. `changed()` pushes one waker of the server task.
  3. A message arrives and the read arm wins. The `changed()` future is dropped, but its waker stays in the list.
  4. The next pass pushes another one.
  5. `writer_dead_tx` only sends when a write fails, so nothing ever empties the list. It grows by one waker per message (each `didChange`) for the life of the connection.

  Base used tokio's `watch`, so this is new in `rusty_lsp`. The new `wait_for` (`watch.rs:180-199`) inherits the same leak.
- **Executed:** I hand-polled each future once to `Pending` with a counting waker, then dropped it.
  - rusty_tokio `changed()`, 1000 times: **1000 wakers retained**.
  - rusty_tokio `wait_for()`, 100 times: **100 retained**.
  - tokio 1.53.3 `changed()`: **0 retained**.
- **Fix:** give `changed()` (and so `wait_for`) a waiter id and a drop guard that removes its entry under the lock. Drop the removed waker after the lock is released, as `rwlock.rs` and `semaphore.rs` (#1064) already do. Also refresh the stored waker when it changes (`will_wake`), not only on the first poll.
- **Regression test:** in `rusty_tokio/tests/sync.rs`, poll `rx.changed()` and `rx.wait_for(..)` to `Pending` N times with an `Arc<impl Wake>` waker, dropping each one, then assert `Arc::strong_count` is back to its starting value. Both go red at head.

### F2 (P1): `CancelToken::cancelled()` keeps a waiter for each dropped future (executed)
- **Where:** `rusty_lsp/src/cancel.rs:74-88`, on top of `rusty_tokio/src/sync/notify.rs:176-217` and `:236`.
- **Trigger:** a request handler that runs `select!` in a loop with `token.cancelled()` as one arm. `cancel.rs:30` documents `cancelled()` as the `select!`-friendly form, so this is the intended use.
- **The defect:** `Notified` has no `Drop`. A dropped waiter, with its waker, stays in `Notify::waiters` until `notify_waiters` runs or the token is dropped.
- **Trace (source-traced):**
  1. The handler loops `select! { _ = token.cancelled() => .., x = work() => .. }`.
  2. Each time `work()` wins, the pinned `Notified` is dropped while still queued.
  3. The token's queue grows by one entry per pass for as long as the request runs.

  Base used `tokio::sync::Notify`, whose `Notified` removes itself on drop.
- **Executed:**
  - Head `rusty_lsp::cancel::CancelToken::cancelled()` with the same drop loop: **1000 wakers retained**.
  - Base's `cancelled()` body copied verbatim onto `tokio::sync::Notify`: **0 retained**.
- **Related risk, not reachable from rusty_lsp:** `notify_one` pops the front entry. If that entry is a dropped waiter, the notification is lost. `rusty_lsp` only calls `notify_waiters`, so it does not hit this.
- **Fix:** add `Drop` to `Notified` and `OwnedNotified`. It should take the entry out of `waiters`, and if `notify_one` had already chosen it, pass the wake to the next waiter. Until then, `CancelToken` could keep a tokio-equivalent primitive.
- **Regression test:** the hand-polled drop test above. Put it in `rusty_tokio/tests/sync.rs` for `Notify`, and in `rusty_lsp/src/cancel.rs` tests for `cancelled()`.

### F3 (P1): a panic in the `wait_for` predicate poisons the channel and can abort the process (executed)
- **Where:** `rusty_tokio/src/sync/watch.rs:191-196`.
- **Trigger:** a predicate that panics.
- **Contract broken:** tokio 1.53.3 `src/sync/watch.rs:865-868`: "If and only if the closure `f` panics. In that case, no resource owned or shared by this `Receiver` will be poisoned." tokio wraps `f` in `catch_unwind` and releases the lock before `resume_unwind`. The change fragment says this API matches tokio.
- **The defect:** `f(&guard.value)` runs while the channel's single `std::sync::Mutex` is held, with no `catch_unwind`.
- **Trace (executed):**
  1. A predicate panics and the unwind poisons the mutex.
  2. Every later call that locks with `.lock().unwrap()` then panics: `send` (`watch.rs:72`), `borrow`, `changed`, `Receiver::drop` (`:240`) and `Sender::drop` (`:117`).
  3. In my probe, the unwind that followed dropped a `Receiver`. Its `Drop` panicked during cleanup and the process aborted with **SIGABRT** ("panic in a destructor during cleanup").
  4. The same sequence on tokio passes and `send` succeeds.

  `rusty_lsp`'s own predicate (`seq >= watermark`) cannot panic. The defect is in the rusty_tokio API this PR adds.
- **Fix:** run `f` under `catch_unwind(AssertUnwindSafe(..))`, drop the guard, then `resume_unwind`. Alternatively, recover poisoned locks with `unwrap_or_else(PoisonError::into_inner)` throughout `watch.rs`.
- **Regression test:** in `tests/sync.rs`, poll `wait_for(|_| panic!())` inside `catch_unwind`, then assert `tx.send(1)` succeeds and both ends drop cleanly.

### F4 (P2): a comment this PR makes false
- **Where:** `crates/libs/ui/rusty_term/l13/Cargo.toml:17` still says "rusty_lsp's tokio-backed `runtime` feature".
- **Fix:** say rusty_tokio-backed.

### F5 (P2): missing tests for changed behaviour
- **Where:** `rusty_tokio/tests/sync.rs`.
- **The gap:** the three `wait_for` tests check the happy paths only, and one of them relies on 10 ms sleeps. Nothing tests dropping `wait_for` after `Pending`, a panicking predicate, or the "last value was checked when `Err` is returned" guarantee.
- **Fix:** add the tests named in F1 and F3, plus a hand-polled test where the sender sends and drops between two polls.

The new tests that do exist would go red at base: `wait_for`, `AsyncBufRead for Take` and `Debug for Notify` don't exist there, so the tests don't compile.

## Ruled out, with reasons
- **`select!` arm order:** rusty_tokio's `select!` picks a random start like tokio's (`macros.rs:33-60`), so arm order is unchanged.
- **`wait_for` lost wake or wrong final value:** `changed()` re-checks the version under the same lock it registers under. A send followed by a sender drop is still seen, because `version != seen` is checked before `sender_alive`. When it returns `Err`, the predicate has already rejected the current value.
- **`Take::poll_fill_buf` / `consume`:** matches tokio's `Take`. A zero limit returns an empty buffer, and `consume` is capped at the limit.
- **Other primitives `rusty_lsp` uses:**
  - Semaphore: cancellation-safe since #1064 (`b6a9c9f6a`, before base).
  - RwLock: its module docs say a dropped waiter leaves the queue.
  - mpsc `recv` loops: never cancelled.
  - oneshot plus `timeout`: dropping the receiver is clean.
- **Tier C or reactor:** `rusty_lsp` has no G9 dependency. The `fuzz` crate is outside the workspace and still uses a tokio runtime; I compiled and ran its `transport` target body against the head and it works, because a slice reader needs no reactor.
- **Migration scope:**
  - The public signature change and the new `Send` bounds are disclosed in the CHANGELOG, the change fragment and the inventory. They follow rusty_tokio's documented `Send` design for its I/O extension methods (`io/async_io.rs:236-240`, "not a drop-in tokio replacement").
  - No workspace crate turns on `rusty_lsp/runtime`: the workspace entry sets `default-features = false`, and `l13` inherits it.
  - #911 lists `lsp` as one of its small crates.
- **Stdin:** rusty_tokio reads stdin on a blocking-pool thread, as tokio does, so the shutdown behaviour is the same.

## Design questions
- `watch::Ref` holds the channel's only mutex (`watch.rs:247-249`). A predicate or caller that calls `borrow()` on another handle of the same channel while holding a `Ref` always deadlocks; under tokio's read lock it usually doesn't. This was true before this PR, but `wait_for` now runs user code under that lock. Owner question: should `watch` move to an `RwLock`, or should the docs warn about it?
- I can't confirm the owner's approval of the public API break, which the change fragment and inventory both claim. The issue tracker isn't reachable from here.

## Optional
- The change fragment says `Debug for Notify` matches tokio. It prints `Notify { .. }`, while tokio's derived output shows fields. It's worth saying "adds `Debug`" instead.
- `rusty_lsp/fuzz` still uses a tokio runtime. A rusty_tokio runtime would make it consistent, or the inventory could note that it stays.

## Checks
All checks used `CARGO_TARGET_DIR` outside the repo and `--offline`.

| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | **Not valid here.** `io_uring_setup` returns EPERM in this sandbox, and with all features every runtime fails to start (exit 101). Validation gap. |
| `cargo test -p rusty_tokio --features io-uring-reactor` | Same EPERM in the 4 io_uring and current-thread lib tests (exit 101). Validation gap. |
| `cargo test -p rusty_tokio` (default features, `--no-fail-fast`) | 659 passed, 33 failed. All 33 are sandbox causes: EPERM (29), multicast "No such device" (3), and a socket path longer than `SUN_LEN` from the long TMPDIR (1). `tests/sync.rs` 123/123 and `tests/chain_take.rs` 8/8 passed, including all six new tests. |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Pass. |
| `cargo test -p rusty_lsp --all-features` | Pass, 267 tests. |
| `cargo clippy -p rusty_lsp --all-targets --all-features -- -D warnings` | Pass. |
| `cargo check --target x86_64-pc-windows-gnu` (rusty_tokio, rusty_lsp) | **Not run.** The Windows target's standard library isn't installed (E0463). Validation gap. |
| `python3 .github/scripts/changes.py check` | Pass, 379 fragments. |
| Scratch probes (hand-polled, counting waker) | F1, F2 and F3 reproduced. tokio passes the same probes. |

**Validation gaps:**
- io_uring and all-features runs need a host that allows `io_uring_setup`.
- The Windows check and any Windows or macOS run need those targets.
- CI results for this head were not seen.

## Verdict
**Changes required.**
