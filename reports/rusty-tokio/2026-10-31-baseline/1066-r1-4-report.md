<!-- rusty-tokio/1066-r1 run 4, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 298 s, 1422065 tokens, $1.33 -->

# Review: Rusty-Mill/rusty_mill PR #1066, "cancellation-safe Mutex and Semaphore waiters (#1064)"

- **Base:** `9248ea8d51bb91cfe134ab3638b939fb2780141f`
- **Reviewed head:** `b6a9c9f6a6d240a355aede951e3e466f03ce3402`. The fetched `pull/1066/head` equals this SHA.
- **Diff:** one commit touching `sync/mutex.rs`, `sync/semaphore.rs`, `tests/lock_cancel.rs` and `changes/2026-10-25-rusty-tokio-lock-cancel.md`.

**Verdict: changes required.** The cancellation fix itself is correct: every cancel and re-poll path I traced holds, and the PR's tests prove it. But the PR adds five new paths that drop a waker while the internal state lock is held. With a waker that touches the same primitive when dropped, each of them deadlocks; at base, none do. The repository already fixed this exact defect in mpsc (`23b28c0f9`).

## Invariants (step 1)

- **Mutex:**
  - Who wakes whom: whenever `locked == false` and the queue is non-empty, the front waiter has been woken since the lock was freed. `release` wakes the front, and `Place::drop` passes the wake on when the dropped waiter was at the front of a free lock.
  - Cancellation: a dropped `lock()` leaves the queue.
  - Re-polling: a re-poll refreshes the waker in place instead of queuing again.
  - Locking: no waker is woken or dropped under `state`.
- **Semaphore:** each waiter's outcome is set once, under `state`, by `grant` or `close`. A dropped claim either leaves the queue (pending) or returns its permits (granted), then calls `grant`. Locking rule as for Mutex: no waker woken or dropped under `state`, mirroring tokio `batch_semaphore.rs:551-576` and `:758-780`.

## Blocking findings

### F1 (P2): wakers are dropped under the state lock, causing a self-deadlock

**Trigger:** the dropped waker is the last reference to something whose `Drop` touches the same Mutex or Semaphore. Examples: a waker that owns an `OwnedMutexGuard` or `OwnedSemaphorePermit`, or a waker that is the last reference to a task holding one. The task case is reachable when a lock future is polled under one waker and then moved to another task. This is the same trigger as the mpsc precedent's Sender-owning waker.

**Sites (head):**
- `mutex.rs:45`: `rewait` runs `*w = waker.clone()`, dropping the old waker.
- `mutex.rs:55`: `dequeue` runs `self.waiters.remove(pos)`. It is reached from `Place::drop` (`:85`, a cancelled `lock()`) and from the acquiring poll (`:141`, which drops the stale waker after a waker change).
- `semaphore.rs:86`: `rewait`.
- `semaphore.rs:128`: `Claim::drop` → `retain`.

**Failure scenario** (*executed*):
1. A task polls `m.lock()` while the lock is held, under waker W1. W1's last `Drop` calls `m.try_lock()`.
2. The caller's own W1 handle goes away, so the queued clone is the last reference.
3. One of three things happens:
   - the future is re-polled under W2 (refresh);
   - the future is dropped (cancel);
   - the lock is freed and the future is re-polled under W2 (acquire).
4. Each path drops W1 inside `lock(&self.state)`. W1's drop re-enters `lock(&self.state)` on the same thread, and the thread deadlocks forever.

The Semaphore equivalents, refresh and cancel with a waker that calls `available_permits()`, deadlock the same way.

**Executed evidence:** a `rustc --test` probe that includes the pinned `mutex.rs` and `semaphore.rs` (details under Checks).

| Probe | head | base |
|---|---|---|
| `mutex_refresh_drops_reentrant_waker` | deadlock (3 s timeout) | ok |
| `mutex_cancel_drops_reentrant_waker` | deadlock | ok |
| `mutex_acquire_after_waker_change_drops_reentrant_waker` | deadlock | ok |
| `semaphore_refresh_drops_reentrant_waker` | deadlock | ok |
| `semaphore_cancel_drops_reentrant_waker` | deadlock | ok |

The PR introduces this regression: at base, Mutex and Semaphore only popped wakers and woke them after unlocking.

**Who is hurt:** a task, and its worker thread, hangs permanently. The workspace's other waiters on that primitive then hang too.

**Oracles:**
- tokio 1.53.3 `batch_semaphore.rs:575-576` (`drop(waiters); drop(old_waker);`). Tokio's `Acquire::drop` (`:758-780`) unlinks the node under the lock, but the waker lives in the future's own node and is dropped after the lock is released.
- This repo's `23b28c0f9`, which states this exact deadlock for mpsc and fixed it.

**Required correction:** do what `mpsc.rs` already does. `rewait` and `dequeue` should return the replaced or removed `Waker`, marked `#[must_use]`. `Claim::drop` should take the removed `Waiter` out with `position` + `remove` instead of `retain`. The caller drops all of these after `drop(state)`.

**Regression tests:** add the five re-entrant-waker cases above to `crates/libs/async/rusty_tokio/tests/lock_cancel.rs`. Each should run in a thread with a timeout, following the pattern of the four re-entrant tests in `tests/mpsc_cancel.rs`. All five go red without the fix.

### F2 (P2): the poison-recovery comments and fragment claim are false, and the new behaviour is untested

**Where:** `mutex.rs:68` and `semaphore.rs:91`, both reading "No code that can panic runs while `state` is held". The change fragment's "Changed" bullet makes the same claim.

**Why it's false** (*source-traced*): `Waker::clone` and `Waker` drop run arbitrary `RawWakerVTable` code under the lock. The places are `mutex.rs:37`, `:45`, `:55` and `:64`, plus `semaphore.rs:86`, `:128` and the `push_back` in `acquire_permits`. `mutex.rs:64` (`next_waker`, called from `release`) is new: base popped the waker instead of cloning it.

**Trace:**
1. `release` sets `locked = false`.
2. The front waiter's waker clone panics.
3. The lock is poisoned, and the front waiter is never woken while the lock is free.

The data stays consistent, but the wake invariant does not.

**Missing test:** the behaviour change (recover from poisoning instead of `unwrap`) has no test that goes red at base.

**Correction:**
- After F1, reword both comments to "only waker clones run under the lock".
- Correct the fragment.
- Add a poisoned-lock test to `tests/lock_cancel.rs`: a waker whose `clone` panics once, wrapped in `catch_unwind`, followed by `try_lock` / `try_acquire`. That test panics at base and passes at head.

## Failure classes ruled out
- **Cancelled waiter:**
  - Mutex: a dropped head waiter, a waiter dropped after its wake, a dropped middle waiter, a barging `try_lock` taking the lock between wake and re-poll, and a non-front waiter acquiring on a spurious poll. Each keeps the wake chain.
  - Semaphore: a waiter granted then dropped hands its permit to the next one; a waiter granted, then closed, then dropped returns its permit; a granted and polled waiter doesn't return permits twice.
  - All of the above were *executed* at head; three of them fail at base.
- **Publication window:** outcomes and `locked` are only read or written under `state`. `close` now stores `CLOSED` under the lock. I couldn't find any observable difference from the old store-after-unlock order (a PENDING claim's `retain` finds nothing, and `grant` is a no-op), so its lack of a test is not a finding.
- **Re-entrant wake:** every `wake()` happens after unlocking. Clones under the lock are covered by F2 and the optional note below.
- **Out of scope:** task state, reactor teardown, Windows and migration. The diff is `std`-only sync code and moves no crate.
- **tokio parity:**
  - Mutex barging (non-FIFO) is pre-existing and documented at `mutex.rs:175-183`.
  - Semaphore drop and return-permits now matches tokio's `Acquire::drop`.

## Design questions
None.

## Optional improvements
- `mutex.rs:64`: `release` clones the front waker under the lock where it could take it out. A clone-re-entrant waker is pathological, but cloning after unlocking would match `mpsc`.
- `semaphore_close_still_fails_a_queued_waiter` passes at base (executed), so it guards nothing about the change. That's fine as a smoke test, but the fragment counts it among the 9 tests.

## Checks and validation gaps

**Ran:**
- `python3 .github/scripts/changes.py check`: passed (366 fragments ok).
- The PR's `tests/lock_cancel.rs`, compiled with `rustc --test` against the pinned head and base copies of `mutex.rs` and `semaphore.rs`: 9/9 pass at head; 8 fail and 1 passes at base. This matches the fragment's claim.
- My probe, built the same way: 5 deadlocks at head, all ok at base (F1); 5 invariant checks pass at head, 3 of them fail at base.
- The sources are in `$TMPDIR/pr1066-probe/`. I deleted the binaries afterwards to free quota.

**Not run (validation gaps):** the sandbox's tmpfs quota for this session had about 60–85 MB free, too little to build the crate. Writes to `$HOME`/`~/.cargo` are outside the sandbox. So I did not run:
- `cargo test -p rusty_tokio --all-features`
- `cargo test -p rusty_tokio --features io-uring-reactor`
- `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings`
- `cargo check -p rusty_tokio --target x86_64-pc-windows-gnu --all-targets`
- `cargo test -p rusty_tonic`

Running them requires more scratch quota, which is the user's call. I did not run Windows or macOS execution either (the code is `std`-only, so the OS risk is low). I didn't see CI.
