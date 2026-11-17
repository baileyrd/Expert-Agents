<!-- rusty-tokio/1066-r2 run 2, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 358 s, 1332663 tokens, $1.23 -->

# Review of PR 1066: K1 is fixed on the normal paths, but one unwind path remains — changes required

Rusty-Mill/rusty_mill PR 1066, base `9248ea8d51bb91cfe134ab3638b939fb2780141f`, reviewed head `3280c75ee19d93750bd973ee39f1d1dbf140a6cd`. The fetched `pull/1066/head` matched the head SHA.

The diff touches four files: `sync/mutex.rs`, `sync/semaphore.rs`, the new `tests/lock_cancel.rs` and the change fragment. No reactor or migration code is touched.

## K1 re-check: resolved on every normal path
Every waker that the queue removes or replaces is now moved out under the lock and dropped after it. The five sites are:
- **Mutex:** `Place::drop` (mutex.rs:90), the ready path in `acquire` (:142), and the re-poll path (:153).
- **Semaphore:** `Claim::drop` (semaphore.rs:143) and the re-poll path (:389).
- `grant`, `close` and `release` move wakers out and wake them after unlocking.

**Executed:** the two new tests with re-entrant waker destructors deadlock (10 s timeout) against b6a9c9f6's sources and pass at head. I also reverted each of the five sites on its own, so the waker is dropped before the unlock again; each revert makes its test deadlock. Every site is covered by a test.

## Blocking findings

**N1 (P2): `Place::drop` still drops the removed waker under the state lock when it unwinds (residual K1)**
- **Where:** `crates/libs/async/rusty_tokio/src/sync/mutex.rs:84-85`.
- **Trigger:** an unusual waker — one waiter's waker destructor re-enters the mutex, and the next waiter's waker panics in `clone`.
- **Cause:** `removed` is declared after `state`. If `state.next_waker()` panics, which it can when a user waker's `clone` panics, the unwinding drops locals in reverse order. `removed` is therefore dropped while the guard is still held.
- **Trace (executed):**
  1. Lock held. A queues with a waker whose drop calls `try_lock`. B queues with a waker whose second clone panics. The test drops its own copy of A's waker.
  2. The holder releases, and A is woken.
  3. A's future is dropped: `Place::drop` takes A out of the queue, and `next_waker()` clones B's waker, which panics.
  4. During unwinding, A's waker is dropped under `state`. Its `try_lock` then waits on the same `std::sync::Mutex` forever.
  
  The probe test deadlocked at head.
- **Who is hurt:** the dropping thread hangs, and every later `lock()` on that mutex hangs with it.
- **Also contradicted:** the comment at mutex.rs:68 and semaphore.rs:102, "No code that can panic runs while `state` is held", is false, because user `RawWakerVTable::clone` runs under the lock. The poison recovery the comment justifies is still sound: I traced every clone site, and the state stays consistent.
- **Fix:** declare `let removed;` before `let mut state = lock(self.state);`, so the guard drops first even when unwinding. Narrow the comment to say the state stays consistent if a waker's `clone` panics.
- **Executed check of the fix:** with that reorder, the probe completes (the panic propagates and nothing deadlocks), and all 11 `lock_cancel` tests still pass.
- **Regression test:** add it to `tests/lock_cancel.rs`, inside `bounded`. It needs waker A with a destructor that calls `try_lock`, and waker B whose second clone panics. Wrap `drop(a)` in `catch_unwind`. The test deadlocks at head.
- **Not affected:** `Claim::drop`, the Mutex ready path and both re-poll paths have no code that can panic between the move-out and the unlock.

**N2 (P2): the change fragment miscounts failures on base**
- **Where:** `changes/2026-10-25-rusty-tokio-lock-cancel.md:12`.
- **Claim:** "11 hand-polled tests … On `main`'s code, 8 fail."
- **Executed:** `lock_cancel.rs` from head, run against base 9248ea8d, fails **9** of 11 tests. The semaphore re-entrancy test fails on base because of the permit-count assertion, not a deadlock. The mutex one passes.
- **Fix:** correct the count. It's worth noting that the semaphore re-entrancy test fails on base because the dropped waiter's waker stays queued (never dropped), not because of a deadlock.

## Other failure classes
- **Cancelled waiter** (dropped after first `Pending`, after its wake, mid-queue): traced. The wake passes on correctly, and no permit leaks. A dropped claim's outcome is only set under the lock, so a claim that is granted and then dropped returns its permits.
- **Publication window:** traced. `close` now stores outcomes under the lock. The Mutex front waiter stays queued until it acquires, so a lock taken by another caller in between only delays it.
- **Re-entrant waker:** covered by the K1 re-check and N1. Clones under the lock follow the same practice as tokio.
- **Not applicable to this diff:** task state, reactor teardown, migration and Windows-specific behaviour. The changed code is portable std code.

## Design questions (pre-existing, not introduced here)
- `Mutex` still lets a new caller take a free lock ahead of queued waiters (documented at mutex.rs:183-189), whereas tokio's `Mutex` is FIFO-fair.
- `try_acquire` refuses while waiters are queued, but tokio's can take free permits.

## Optional improvement
- `rewait` uses `will_wake` to skip replacing an equal waker, which is good. Consider a short comment that clones are the only user code still run under the lock.

## Checks performed
| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | `lock_cancel` 11/11 pass. Failures elsewhere are all EPERM from io_uring setup, blocked by the sandbox, in code this diff doesn't touch. |
| `cargo test -p rusty_tokio --features io-uring-reactor --test lock_cancel` | 11/11 pass |
| `cargo test -p rusty_tokio` (default features) | `lock_cancel` 11, `sync` 119 and `mpsc_cancel` 8 all pass. The only failures are unix-socket tests (EPERM) and multicast tests (ENODEV), both sandbox limits. |
| `lock_cancel.rs` against base | 9/11 fail (N2) |
| `lock_cancel.rs` against b6a9c9f6 | both K1 tests deadlock |
| Reverting each K1 site individually | every revert makes its test fail |
| N1 probe | deadlocks at head; passes with the fix |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | clean |
| `python3 .github/scripts/changes.py check` | 366 fragments ok |
| `cargo check --target x86_64-pc-windows-gnu` | **not run**: the target's std isn't installed here |

## Validation gaps
- The Windows cross-check could not run.
- io_uring-backed runtime tests could not run in this sandbox (EPERM). They are unaffected by this diff.
- macOS and Windows execution were not done.
- The fragment says `cargo test -p rusty_tonic` passes; I did not run it.
- I have not seen CI.

## Verdict: **changes required**
K1 is fixed on every normal path. N1 is a remaining instance of K1's class on the unwind path, and N2 is a wrong count in the change fragment.
