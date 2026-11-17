<!-- rusty-tokio/1250-r1 run 3, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 343 s, 1316721 tokens, $1.18 -->

I found **three blocking defects (one P1, two P2)**, so the verdict is **changes required**.

The PR fixes the single-threaded case, but a `notify_one` that races a drop on another thread can still be lost: 7 in 1,000,000 runs here. It also claims tokio parity on two paths where tokio 1.53.3, run on the same calls, behaves differently.

**Review pin**
- Repository: Rusty-Mill/rusty_mill, PR #1250
- Base: `d05170b8cc082cffaf6c348411e82bea84b9632d`
- Head: `98621f936d802e1fb05912e47f1b569eebe230c9`. The fetched `pull/1250/head` matched it.
- Scope: one commit, touching `crates/libs/async/rusty_tokio/src/sync/notify.rs` and `changes/2026-10-30-rusty-tokio-notify-drop.md`.

## Blocking findings

### 1. P1: a `notify_one` racing a drop on another thread is still lost
**Where:** `notify.rs:101-104` and `114-118` (pop, unlock, then `fire`), `notify.rs:146` (`single` stored after the lock is released), `notify.rs:282-285` (`release` reads `single`).

**Trigger:** a waiter dropped on one thread while another thread's `notify_one` or `notify_last` is choosing it. This is the PR's own case ("the losing branch of a `select!` in a loop") on a multi-thread runtime.

**Failure (source-traced and executed):**
1. The queue is `[A, C]`.
2. Thread B calls `notify_one`: it takes `inner`, pops A and releases `inner`. It has not yet run `fire`, so A's `single` is still false.
3. Thread A drops its `Notified`. `release` takes `inner`, does not find A in the queue, reads `single == false`, and returns.
4. B's `fire(A, true)` then sets flags on a waiter that is already gone, and wakes A's stale waker.
5. C stays pending and no permit is banked. One `notify_one` has woken nobody.

Even when the store comes first in wall-clock time, the `Relaxed` store made after the unlock has no happens-before edge with the read in `release`. The comment at `notify.rs:282` ("Not queued: something already fired it") is wrong inside this window. So is the change fragment's claim that `Notify` "no longer loses a notify_one".

tokio stores the notification kind under the waiters lock (`tokio-1.53.3/src/sync/notify.rs:879-881`), and `drop_notified` reads it under that same lock (`:1342-1347`).

**Executed probe:** queue A then C, a `Barrier` lines up `notify_one` on one thread with `drop(A)` on the other, then the probe checks C and a fresh `notified()`.
- Head: 3 of 200,000 lost, then 7 of 1,000,000.
- Base: 200,000 of 200,000 lost.
- Scratch copy with `single.store(true)` moved before `drop(guard)` in `notify_one`, `notify_last` and `release`'s forward: 0 of 1,000,000.

**Required correction:** publish the single-wake mark while `inner` is still held, in all three places. Better, mirror tokio: set `single` and `woken` and take the waker under `inner`, and call `wake()` after unlocking.

**Regression test:** this barrier-driven two-thread stress test (at least 10⁶ iterations, asserting zero lost wakes), as a new `#[test]` in the `notify.rs` tests module or in `tests/sync.rs`. All four tests the PR adds are single-threaded and cannot reach this window.

### 2. P2: an `enable()` permit dropped unresolved is banked again; tokio consumes it
**Where:** `notify.rs:257-263` (`*registered = true` on the permit path), `release` at `:286-291`, the module doc at `:31-33` ("or a banked permit … as tokio does"), the change fragment line 7, and the test `a_permit_taken_by_enable_and_dropped_is_banked_again` at `:410-419`, which pins the non-tokio behaviour.

**Trigger:** `notify_one()`, then `enable()` returns true, the future is dropped, and a new `notified()` is polled.

**Failure (executed on both runtimes):**
- rusty_tokio: the new `notified()` is `Ready`, an extra wake.
- tokio 1.53.3: it is `Pending`. Taking the permit in `Init` moves tokio's future to `Done` (`notify.rs:1134-1137`), and `drop_notified` only acts in `Waiting` (`:1341`).

The "same call sequence, different wake count" check fails, and the doc's "as tokio does" is false.

**Required correction:** leave `registered` false on the permit path (consume the permit like tokio), drop the test, and fix the doc and fragment. If re-banking is really what you want, take it to the owner as a parity question and remove the "as tokio does" claim.

**Regression test:** a test asserting that a fresh `notified()` is `Pending` after enable takes the permit and the future is dropped.

### 3. P2: a forwarded `notify_last` turns into FIFO; tokio keeps LIFO
**Where:** `notify.rs:286` (`release` always calls `pop_front`), the doc at `:31-33`, and the fragment.

**Trigger:** waiters A, B, C are queued, `notify_last()` fires C, then C is dropped before it resolves.

**Failure (executed on both runtimes):**
- rusty_tokio: A is woken, B is pending.
- tokio 1.53.3: A is pending, B is woken. The drop forwards `Notification::One(strategy)` through `notify_locked`, which keeps the original strategy (`:1364-1366`, `:867-870`).

**Required correction:** record the strategy (FIFO or LIFO) in place of the `single` bool and forward with it.

**Regression test:** this A/B/C sequence asserting that B is the one woken, next to `notify_last_wakes_the_most_recently_registered_waiter` in `tests/sync.rs`.

## Failure classes ruled out
- **Cancelled waiter:**
  - Dropped after `Pending`: removed by `release`. Covered by the new test, which goes red at base.
  - Dropped after being woken but before running: forwarded, apart from finding 1.
  - Dropped after a `notify_waiters` wake: correctly not forwarded.
- **Re-entrancy:** `release` drops `inner` before calling `fire`. The `Arc` it removes under the lock is never the last reference, because the dropping future still owns its own. Wakers are dropped with the future, outside every lock.
- **Coop:** a `poll_proceed` `Pending` leaves `done == false`, so the waiter stays registered. That is correct.
- **Not touched by this diff:** task state, reactors, migration, and anything OS-specific (the code is pure std).

## Design questions
- `permits` is a counter (two `notify_one` calls with nobody waiting bank two permits), while tokio keeps at most one. This predates the PR, but finding 2 adds to it.

## Optional improvements
- `fire` (`notify.rs:148-150`, edition 2021) calls `waker.wake()` while that waiter's `waker` mutex is still held, because it is a temporary in the `if let`. A waker that polls inline would deadlock. This predates the PR; the new forwarding path reuses it. Take the waker into a local first.
- `release` calls `lock().unwrap()` inside `Drop`. A poisoned mutex would panic during unwinding and abort the process. Consider `unwrap_or_else(PoisonError::into_inner)`.

## Checks
All runs used a scratch copy of the head (via `git archive`) with build output kept outside the reviewed tree.

| Check | Result |
|---|---|
| New tests run at base | 3 of the 4 go red. `a_consumed_wakeup_and_a_notify_waiters_one_are_not_passed_on` passes at base, so it guards against over-forwarding only. |
| `cargo test -p rusty_tokio --all-features` | Lib: 28 passed, 7 failed; base fails the same 7. All runtime-backed integration tests fail at `Runtime::new` with `PermissionDenied`, because the sandbox denies io_uring. **Not established.** |
| `cargo test -p rusty_tokio` (default features, epoll) | `tests/sync.rs` 125/125 passed. The 4 new notify tests pass. 33 failures, all in socket binaries (`net`, `net_parity`, `unix*`, `readiness`, `try_read_write`, which the sandbox restricts); none involve Notify. |
| `cargo test -p rusty_tokio --features io-uring-reactor` | 136 passed, 586 failed, all from io_uring being denied. **Not established.** |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Passed. |
| `cargo check --target x86_64-pc-windows-gnu --all-targets` | Could not run: the target's std is not installed (`E0463`). |
| `python3 .github/scripts/changes.py check` | Passed ("541 fragment(s) ok"). |

**Validation gaps:**
- io_uring and all-features runtime tests on a host that allows io_uring.
- The socket test binaries on an unrestricted host.
- The Windows cross-check, and actually running on Windows and macOS.
- CI for the head SHA, which I did not see.

**Verdict: changes required.**
