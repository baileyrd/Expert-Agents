<!-- rusty-tokio/1066-r1 run 2, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 251 s, 1114063 tokens, $1.08 -->

I found one blocking defect and a related false doc claim, so the verdict is **changes required**. The PR does fix the bugs it targets: 8 of its 9 new tests fail on the base and pass on the head. But it creates a new deadlock that I reproduced: in four places, a waker is dropped while the primitive's own state lock is held. The repo already fixed the same bug in mpsc (23b28c0f9).

**Repository:** Rusty-Mill/rusty_mill, PR #1066
**Base:** 9248ea8d51bb91cfe134ab3638b939fb2780141f
**Reviewed head:** b6a9c9f6a6d240a355aede951e3e466f03ce3402 (the fetched `pull/1066/head` matches it)

## Blocking findings

### F1 (P2): a waker is dropped while the state lock is held, and a waker that owns a guard or permit deadlocks
**Severity:** P2, the rubric's grade for re-entrancy that needs an unusual waker. **Trigger:** a waker whose last copy owns an `OwnedMutexGuard` or `OwnedSemaphorePermit` of the same primitive, or anything whose `Drop` takes that lock. **Impact:** a permanent deadlock of the thread, on the non-reentrant `std::sync::Mutex`.

**Where:**
- `src/sync/mutex.rs:45`: `rewait` overwrites the old waker (`*w = waker.clone()`) with the lock held.
- `src/sync/mutex.rs:55`: `dequeue` throws away the removed `(id, Waker)` with the lock held. It is reached from `Place::drop` (`:85`, a cancelled `lock()`) and from the success path (`:141`).
- `src/sync/semaphore.rs:86`: `rewait` overwrites `w.waker` with the lock held.
- `src/sync/semaphore.rs:128`: `Claim::drop` removes queued waiters with `retain`, which drops their wakers with the lock held.

**Scenario (executed):** a semaphore with 1 permit. Take it with `try_acquire_owned()` and move that permit `p` into waker `W1`. Poll `acquire()` once with `W1`; it returns `Pending`, so the queue holds a copy of `W1`. Drop the caller's `W1`, which leaves the queue holding the only copy. Then either re-poll with a different waker or drop the `acquire()` future:
1. `rewait` (or `retain`) drops `W1` while holding `state`.
2. That drops `p`, whose `Drop` calls `Semaphore::release` → `lock(state)`.
3. The thread deadlocks.

The same scenario with an `OwnedMutexGuard` deadlocks the `Mutex` on re-poll and on cancel.

My probe (`$TMPDIR/rev1066/probe/probe_1066.rs`) has four tests, each run on a thread with a 3-second watchdog:

| Test | Head | Base |
|---|---|---|
| `semaphore_repoll_drops_permit_owning_waker_under_lock` | deadlocked | ok |
| `semaphore_cancel_drops_permit_owning_waker_under_lock` | deadlocked | ok |
| `mutex_repoll_drops_guard_owning_waker_under_lock` | deadlocked | ok |
| `mutex_cancel_drops_guard_owning_waker_under_lock` | deadlocked | ok |

The base passes all four because it never dropped a waker under the lock: re-polls only cloned, cancels didn't touch the lock, and wakes ran after unlock. So this PR introduces the deadlock.

**Who is hurt:** any caller whose waker can own a handle to the same primitive, and any custom executor whose task `Drop` touches the primitive. The persona's own rule — no waker or caller-owned value is woken or dropped under an internal lock — is exactly what the mpsc fix enforced.

**Required correction:** follow 23b28c0f9 (`mpsc.rs`):
- Have `rewait` return the replaced waker (`std::mem::replace`), and have `dequeue` and the `Claim::drop` removal return the removed waker or waiter.
- Drop those values only after `drop(state)`.
- On the `acquire` success path, move the dequeued entry out and drop it after the guard.

**Regression tests:** add the four probe tests above to `tests/lock_cancel.rs`, modelled on the waker-owns-a-`Sender` tests in `tests/mpsc_cancel.rs`. They go red at this head.

### F2 (P2): the claim that "no code that can panic runs while `state` is held" is false
**Where:** `src/sync/mutex.rs:68` and `src/sync/semaphore.rs:91`. The change fragment repeats it ("No code that can panic runs while it is held").

**Trace (source-traced):** `Waker::clone` runs the waker's own `RawWakerVTable` clone function, which can run any code. It runs under the lock at `mutex.rs:37` and `:45`, at `semaphore.rs:86`, and in the `Waiter` push in `acquire_permits`. Waker drops also run under the lock (F1). Either can panic and poison the lock.

As far as I can tell, the state stays consistent if that happens: in `enqueue`, a clone that panics before `push_back` only skips an id, and assignment keeps the slot valid. So poison recovery is probably fine, but the reason the comment gives is wrong.

**Correction:** once F1 is fixed, change the comment and the fragment to name the user code that does run under the lock (`Waker::clone`), and say why a panic there leaves the state consistent.

## Failure classes checked (ruled out, with reasons)
- **Cancelled waiter, Mutex:**
  - Release wakes the front waiter and leaves it queued.
  - Only an acquire or a drop removes a waiter, and new waiters join at the back, so the woken waiter stays at the front.
  - If it is dropped while the lock is free, `Place::drop` passes the wake to the next waiter. If someone else took the lock in between, `locked` is true and that holder's release wakes the new front.
  - A non-front waiter that is polled early and wins the lock removes itself, and the old front gets the next release.
  - A future dropped before its first poll never creates a `Place`.
- **Cancelled waiter, Semaphore:** waiters are granted or closed only while `state` is held (`grant`, and now `close`), and `Claim::drop` reads the outcome under the same lock, so a cancel and a grant can't race. A granted claim returns its permits, and a pending one leaves the queue and re-runs `grant`. There is no await between `poll_fn` returning `Ready(Ok)` and the permit being built, so a drop can't land there. This matches tokio's `Acquire::drop`, which also gives back permits it had acquired.
- **Wake under the lock:** none. `release`, `grant`, `close` and `Place::drop` all wake after unlocking. Drops under the lock are F1.
- **Publication window:** `close` now sets outcomes before unlocking, which closes the earlier window between a cancel and a close.
- **tokio parity:** the Mutex still isn't strictly FIFO, but that predates this PR and is documented in `release`.
- **Not applicable:** task state, reactor teardown, migration and Windows. The PR touches none of them; it changes only `sync/mutex.rs`, `sync/semaphore.rs`, a test file and a fragment.
- **Tests:** each changed behaviour has a test, and I ran all 9 at the base: 8 fail there and `semaphore_close_still_fails_a_queued_waiter` passes, as the fragment says. Re-entrant waker drops (F1) have no test.

## Design questions
None.

## Optional improvements
- `RwLock` (`rwlock.rs:93-104`, which this PR says it mirrors) uses the same `rewait`/`dequeue` pattern and probably has the same drop-under-lock problem. That code predates this PR, so it's a follow-up, not part of this verdict.

## Checks (run on scratch copies made with `git archive`)

| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | `lock_cancel` passes 9/9. Every test that starts a runtime fails with EPERM, because this sandbox blocks io_uring setup and `--all-features` builds the runtime on io_uring. |
| `cargo test -p rusty_tokio` (default features, epoll) | 649 passed, 33 failed. The 33 are AF_UNIX and multicast (ENODEV) tests; the base fails the identical 33, so they come from the sandbox. `tests/sync.rs` and `mpsc_cancel.rs` pass. |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Passed (only manifest warnings). |
| `python3 .github/scripts/changes.py check` | Passed: "366 fragment(s) ok". |
| `cargo check --target x86_64-pc-windows-gnu --all-targets` | Not run: the target's std isn't installed (E0463). |
| Base comparison | `lock_cancel.rs`: 8 of 9 fail at the base. The F1 probes pass at the base and deadlock at the head. |

To get disk space back during the base rerun, I deleted my own scratch build directory (`$TMPDIR/rev1066/target`, 4 GB). Nothing else was touched.

**Validation gaps:**
- The all-features and `io-uring-reactor` suites need a host that allows io_uring.
- The Windows target check needs `rustup target add x86_64-pc-windows-gnu`.
- I didn't run `cargo test -p rusty_tonic`, which the fragment lists.
- No Windows or macOS execution.

## Verdict
**Changes required** (F1, F2).
