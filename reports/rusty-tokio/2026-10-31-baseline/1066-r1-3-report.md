<!-- rusty-tokio/1066-r1 run 3, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 375 s, 1466485 tokens, $1.44 -->

# Review of Rusty-Mill/rusty_mill PR 1066: cancellation-safe Mutex and Semaphore waiters (#1064)

- **Base:** `9248ea8d51bb91cfe134ab3638b939fb2780141f`
- **Reviewed head:** `b6a9c9f6a6d240a355aede951e3e466f03ce3402`. A fresh fetch of `pull/1066/head` returned the same SHA, and it is one commit, b6a9c9f6a.

**Verdict: changes required.** The cancellation fix works: the PR's 9 tests pass, and a stress run with about 6,000 cancellations per run showed no hang or leak. But the new code drops wakers while holding the internal lock, and a waker that owns a guard or permit of the same primitive now deadlocks on four paths that did not deadlock before.

## What the PR touches, and what must hold

- **`Mutex`** (`mutex.rs`): `acquire`, `release`, the new `Place` guard and the queue helpers `enqueue`, `rewait` and `dequeue`.
  - A `Pending` waiter's latest waker must be in the queue.
  - `release` must wake the front waiter.
  - A dropped front waiter of a free lock must pass the wake on.
  - No waker may be woken or dropped under `state`. This is the repo's rule: mpsc fix 23b28c0f9 and `cancellation_token.rs:32`.
- **`Semaphore`** (`semaphore.rs`): `acquire_permits`, the new `Claim` guard, `grant`, `rewait`, `close` and `release`.
  - An outcome is decided once, under `state`.
  - A dropped claim either leaves the queue (still pending) or returns its permits (already granted).
  - `close`'s wake must reach the waiter's latest waker.
  - The same no-waker-under-lock rule applies. tokio's `batch_semaphore.rs:575-576` does `drop(waiters); drop(old_waker);`.
- **Not touched:** public signatures, reactors, file descriptors, task state, migration and Windows-specific code. The diff is private sync internals, so the reactor, migration and Windows failure classes don't apply.

## Blocking findings

### 1. P2: a waker is dropped while the internal lock is held, so a waker that owns a guard or permit deadlocks

- **Impact:** a permanent deadlock of the thread and of the primitive for every user. **Trigger:** a waker that owns an `OwnedMutexGuard` or `OwnedSemaphorePermit` of the same primitive.
- **Grade:** P2 because the rubric names "re-entrancy that needs an unusual waker" as P2. Judged on impact alone it would be P1, since the repo's own mpsc fix treated a waker owning a handle as a real risk.

**Where the waker is dropped under the lock** (all new in this PR):
- `mutex.rs:45`: `rewait` overwrites the old waker (`*w = waker.clone()`).
- `mutex.rs:55`: `dequeue` removes the entry and its waker. It is reached from `Place::drop` (`:84-85`) and from the Ready path (`:141`).
- `semaphore.rs:86`: `rewait` overwrites the old waker.
- `semaphore.rs:128`: `Claim::drop` removes the waiter with `retain`.

**Failure scenario (executed):**
1. Task A polls `m.lock()` with waker W, which owns the only `OwnedMutexGuard`.
2. Every other reference to W is dropped.
3. The `lock()` future is dropped, or re-polled with another waker.
4. `Place::drop` (or `rewait`) drops W while holding `state`. W's drop runs the guard's `Drop`, which calls `release`, which takes `state` again. Self-deadlock.

The semaphore deadlocks the same way through a permit drop calling `Semaphore::release`.

**Probe results** (scratch test, 5 s watchdog that tells a timeout apart from a panic):

| Path | Head | Base |
|---|---|---|
| Mutex `Place::drop` | **DEADLOCK** | fails on a stranded guard (the bug this PR fixes), no deadlock |
| Mutex `rewait` | **DEADLOCK** | ok |
| Semaphore `Claim::drop` | **DEADLOCK** | fails on a stranded permit, no deadlock |
| Semaphore `rewait` | **DEADLOCK** | ok |

**Required correction:** move every removed or replaced waker out of the critical section and drop it after unlocking:
- `dequeue`, `rewait` and the `retain` removal return the old `Waker`/`Waiter` with `#[must_use]`, as mpsc's `stop_waiting`/`wait_to_send` do (`mpsc.rs:64-90`).
- The Ready path in `Mutex::acquire` also holds the removed waker until after the lock is released.

**Regression tests:** four tests in `tests/lock_cancel.rs`, one per path, each with a waker that owns the primitive's owned guard or permit, as in `tests/mpsc_cancel.rs`. They must finish without deadlock and leave the lock free and the permits back at their full count.

### 2. P2: the poison-recovery comment is false

- **Where:** `mutex.rs:68` and `semaphore.rs:91`, where the comment says: "No code that can panic runs while `state` is held". The change fragment repeats the claim.
- **Why it's false (source-traced):** waker `clone` (`enqueue`, `rewait`, the push in `acquire_permits`) and the waker drops in finding 1 run arbitrary `RawWakerVTable` code under the lock.
- **Failure scenario:** a waker whose drop panics inside `Claim::drop`'s `retain` poisons the lock before `grant()` runs. `lock()` then recovers that state as if it were consistent, but the waiters behind were never granted.
- **Required correction:** the fix for finding 1 removes the drops. Then reword the comment to the true invariant: only waker clones run under the lock, and a clone that panics happens before any change to the state.

### 3. P2: no test covers the `close` ordering change

- **The change:** `semaphore.rs:261` now stores `WAITER_CLOSED` while `close` holds the lock. The change is correct and needed.
- **Failure without it (executed by mutation):**
  1. Waiters A and B are queued, then `close` runs.
  2. A's waker re-polls B with a new waker W2. B still reads PENDING, `rewait` finds no queue entry, and B returns `Pending`.
  3. `close` then wakes B's old waker. W2 is never woken, so B hangs.
- **Evidence:** with the store moved back after `drop(guard)`, my probe `close_reentrant_repoll_does_not_lose_the_wake` fails, while all 9 `lock_cancel` tests still pass. Nothing in the PR guards this change.
- **Required test:** add that probe (A's waker re-polls B with a new waker during `close`; assert B resolves or W2 is woken) to `tests/lock_cancel.rs`.

## Design questions

None new. `Mutex` remains non-FIFO when a new `lock()` call grabs a just-released lock ahead of the woken waiter. That is documented at `mutex.rs` `release` and existed before this PR.

## Optional improvements

- `rwlock.rs:96` and `:104` have the same under-lock waker drop. It predates this PR, so it isn't a finding here. The fragment's "`RwLock` was already cancellation-safe" is true for cancellation, but RwLock deserves the same follow-up as finding 1.
- No test covers the poison-recovery change.

## Checks performed

| Check | Result |
|---|---|
| Fetched head equals the PR head SHA | ✅ |
| `cargo test -p rusty_tokio --all-features` | ❌ in this sandbox: every runtime build fails with EPERM from `io_uring_setup`. `lock_cancel` alone: 9/9 pass. |
| `cargo test -p rusty_tokio` (default features, `--no-fail-fast`) | 649 pass, 33 fail. All 33 are sandbox errors (Unix socket EPERM, multicast ENODEV) in files the diff doesn't touch. All sync tests pass. |
| `lock_cancel.rs` against base sources | 8 of 9 fail, which confirms the fragment's claim |
| Probes for findings 1 and 3, at head and base | as described above |
| Mutation: `close` store moved back outside the lock | the PR's tests stay green; my probe goes red |
| Multi-thread stress (16 tasks, random timeouts on `lock`/`acquire_many`), 5 release runs at head | about 6,000 cancellations per run; no hang, no leaked permit, mutex free at the end |
| tokio 1.53.3 oracle (`batch_semaphore.rs:551-576` and `Acquire::drop` at `:758-780`) | old waker dropped after unlocking; contradicts this PR's pattern |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | ✅ pass |
| `python3 .github/scripts/changes.py check` | ✅ 366 fragments ok |
| `cargo check -p rusty_tokio --target x86_64-pc-windows-gnu --all-targets` | ❌ not run to completion: the Windows target isn't installed here |

## Validation gaps

- **io_uring:** `cargo test --features io-uring-reactor` can't run in this sandbox (EPERM). The diff doesn't touch the reactor, but a Linux host with io_uring allowed should run it.
- **Windows:** the cross-check above and Windows/macOS execution were not done. The code is OS-independent sync logic, so the risk is low.
- **Not reproduced:** the fragment's own claims that "disabling the pass-on fails its test" and that `rusty_tonic` tests pass.

All probes were written and run in scratch copies under `$TMPDIR`. I made no changes to the reviewed tree and posted nothing to GitHub.

**Verdict: changes required** (findings 1–3).
