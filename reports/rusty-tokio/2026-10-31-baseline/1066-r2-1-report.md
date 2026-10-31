<!-- rusty-tokio/1066-r2 run 1, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 333 s, 1852208 tokens, $1.56 -->

# Review of PR 1066 (rusty_mill): Mutex and Semaphore cancellation fix

**Verdict: changes required.** The prior finding K1 is fixed, and I found no lost wake, leak or deadlock at the head. Three P2 findings remain: a false code comment, a stale count in the change fragment, and one changed behaviour with no test.

- **Repository / PR:** Rusty-Mill/rusty_mill #1066
- **Base:** `9248ea8d51bb91cfe134ab3638b939fb2780141f`
- **Head reviewed:** `3280c75ee19d93750bd973ee39f1d1dbf140a6cd`. `FETCH_HEAD` from `pull/1066/head` matches.
- **Commits:** `b6a9c9f6` (the cancellation-safe waiters fix) and `3280c75e` (the fix for K1).
- **Files changed:** `sync/mutex.rs`, `sync/semaphore.rs`, `tests/lock_cancel.rs` and the change fragment. This is not a crate migration, and no reactor or task-state code is touched.

## K1 (P2): wakers destroyed while the state lock is held — **fixed**

Every place that removes or replaces a waker now moves it out under the lock and drops it after unlocking:
- **Mutex:** `rewait` and `dequeue` return the waker (`mutex.rs:46`, `mutex.rs:53`). It is dropped after the lock is released at `mutex.rs:91` (when a cancelled future leaves the queue), `:143` (on acquire) and `:154` (on re-poll).
- **Semaphore:** `rewait` and `dequeue` return the waker (`semaphore.rs:83`, `:93`). It is dropped after unlocking at `:144` (cancelled claim) and `:390` (re-poll). `close` takes the whole queue and drops it after unlocking (`:265-277`), and `grant` hands the wakers out to be woken after unlocking.
- **Cloning under the lock** (`enqueue`, `rewait`, `next_waker`) is not a K1 case. tokio 1.53.3 does the same (`batch_semaphore.rs:556-561`): it clones under its waiter lock and moves the old waker out (`old_waker`) to drop later.

**Executed:** I ran the head's `tests/lock_cancel.rs` against `b6a9c9f6`'s `mutex.rs` and `semaphore.rs`. Both `*_drops_removed_and_replaced_wakers_outside_its_state_lock` tests failed through the 10 s deadlock timeout, and both pass at the head.

## Blocking findings

### F1 (P2): the comment "No code that can panic runs while `state` is held" is false
- **Where:** `mutex.rs:68`, `semaphore.rs:102`, and repeated in the fragment's **Changed** bullet.
- **What actually panics under the lock:**
  - A user waker's `clone` runs while `state` is held, in `enqueue` and `rewait`. That is arbitrary code and it can panic.
  - `Semaphore::release` does `guard.permits += n` (`semaphore.rs:419`). In debug builds, `add_permits(usize::MAX)` overflows and panics under the lock.
- **Executed:** I wrote two probes in a scratch copy:
  - A `lock()` polled with a waker whose clone panics.
  - `add_permits(usize::MAX)` on `Semaphore::new(1)`.

  Both panicked inside the lock. Both primitives still behaved correctly afterwards: `try_lock` succeeded, and `available_permits()` was still 1.
- **Impact:** The poison recovery itself is correct, because every such panic happens before any state is written. Only the stated reason is false. A later change that does write state and then call user code would be wrongly covered by this comment.
- **Required correction:** Reword the comment and the fragment to the true invariant: "everything that can panic under `state` (a user waker's `clone`, a permit overflow) runs before `state` is modified". Optionally, add tokio's `MAX_PERMITS` check before the mutation.
- **Regression test:** Add the two probes to `tests/lock_cancel.rs`. From reading the base code (I did not run them there), both should go red: the base's `.lock().unwrap()` panics on the poisoned lock at the next use.

### F2 (P2): the change fragment's test count is stale
- **Where:** `changes/2026-10-25-rusty-tokio-lock-cancel.md:12` says "11 hand-polled tests … On `main`'s code, 8 fail."
- **Executed:** I ran the head's `lock_cancel.rs` against the base's sources: **9 of 11 fail**. `3280c75e` raised the test count from 9 to 11 but did not update the failure count. The newly added Semaphore re-entrancy test also fails on `main`, because `main` never removes a cancelled waiter, so its destructor never runs.
- **Required correction:** Change "8" to "9". Optionally say which tests fail and why.

### F3 (P2): `close` now sets each waiter's outcome under the lock, and no test guards that
- **Where:** `semaphore.rs:265-277`. The fragment presents this as a deliberate change.
- **Gap:** `semaphore_close_still_fails_a_queued_waiter` (`lock_cancel.rs:212`) passes on the base, so it guards nothing about the reordering.
- **Failure scenario if the order is reverted (source-traced):** the store would again happen after unlocking.
  1. Thread A runs `close()`, takes the queue and unlocks.
  2. Task B re-polls its queued `acquire` with a new waker W2. Under the lock it still reads `PENDING`. `rewait` finds no queue entry, and B returns `Pending`.
  3. A stores `CLOSED` and wakes the old waker W1.
  4. B waits on W2 forever, for example inside `select!` or `FuturesUnordered`, where the per-branch waker differs.

  Who is hurt: any task waiting on a semaphore that is shut down concurrently.
- **Required test:** Put a two-thread stress test in `tests/lock_cancel.rs`. Thread A calls `close()`. Thread B keeps re-polling the queued `acquire` with a fresh flag waker each time. After joining, assert that the waker from B's last `Pending` poll was woken. Repeat about 10⁴ times, gated by a barrier. It should go red with the store moved back after `drop(guard)`.

## Other failure classes

- **Cancelled waiter:** each case below leaves no queue entry or permit behind and passes its wake on, and each has a hand-polled test that goes red on the base:
  - Mutex: dropped after its first `Pending`, dropped after being woken but before running, dropped from the middle of the queue.
  - Semaphore: dropped while pending, dropped after being granted (permits come back), dropped while closed (nothing to undo), a large head waiter cancelled.
- **Publication window:** both the sleeper's outcome check and the grant/close store now happen under `state`.
- **Not touched by this diff:** task state, reactor teardown, migration, Windows paths. The code is platform-independent `std` code.
- **tokio parity:** returning permits from a cancelled or granted claim matches tokio's `Acquire` drop.

## Design question
- `Mutex` still lets a new `lock()` take the lock ahead of a woken front waiter (non-FIFO). tokio's `Mutex` is FIFO. This is existing, documented behaviour, but a caller relying on tokio's fairness could be hurt. This is for the owner to decide, not a finding.

## Optional improvements (outside this diff)
- `rwlock.rs:96` and `:104` replace and remove wakers while holding the lock, which is the same pattern as K1. The PR's Mutex docs say each waiter holds a place "as `RwLock`'s do", so this deserves a follow-up issue.
- `add_permits` has no `MAX_PERMITS` assert, unlike tokio's `batch_semaphore.rs:372-384`.
- If one waker's `wake()` panics, `wake_all` drops the remaining granted wakers without waking them. This existed before the PR.

## Checks run

| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | Ran; not usable as evidence. `--all-features` enables `io-uring-reactor`, and the sandbox returns EPERM for `io_uring_setup`, so 603 tests fail at runtime construction (614 EPERM panics). `lock_cancel` and `mpsc_cancel` passed. |
| `cargo test -p rusty_tokio` (default features, epoll) | 651 passed, 33 failed. All 33 are EPERM or ENODEV from the sandbox's Unix-socket and multicast limits. `sync`: 119/119. `lock_cancel`: 11/11. |
| `lock_cancel.rs` on base sources / on `b6a9c9f6` sources | 9 of 11 fail / the 2 K1 tests deadlock (timeout) |
| Panic probes (F1) | Executed, both behave as described in F1 |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Passes (fresh run confirmed) |
| `python3 .github/scripts/changes.py check` | 366 fragment(s) ok |

I built with incremental compilation and debug info off, because my first build filled the temp volume's quota. I deleted only my own build and scratch directories.

## Validation gaps
- **io_uring:** `--all-features` and `--features io-uring-reactor` behaviour need a Linux host that allows `io_uring_setup`.
- **Socket tests:** the 33 default-feature failures need a host without the sandbox's socket limits.
- **Windows:** `cargo check --target x86_64-pc-windows-gnu` was not run because only the Linux target is installed. Windows and macOS execution were not run.
- **CI:** I did not check CI or `required-gate` status for this head.
