<!-- rusty-tokio/1066-r1 run 5, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 255 s, 1292866 tokens, $1.11 -->

# Review of PR #1066: cancellation-safe Mutex and Semaphore waiters (#1064)

The cancellation fix itself is sound. But it introduces a new safe-code deadlock in four places, so the verdict is **changes required**.

- **Repository:** Rusty-Mill/rusty_mill, PR #1066
- **Base:** `9248ea8d51bb91cfe134ab3638b939fb2780141f`
- **Reviewed head:** `b6a9c9f6a6d240a355aede951e3e466f03ce3402`. I fetched `pull/1066/head` and it matches `HEAD`. The PR is one commit, `b6a9c9f6a`.
- **Touched:** `sync/mutex.rs`, `sync/semaphore.rs`, `tests/lock_cancel.rs` and a change fragment. No reactor, task-state or migration code changes.

## Blocking findings

### F1 (P1): wakers are now dropped while the internal state lock is held; a waker that owns a guard or permit of the same primitive deadlocks

**Where (head):**
- `mutex.rs:45`: `rewait` replaces the old waker with `*w = waker.clone()` under the lock.
- `mutex.rs:55`: `dequeue` drops the removed waker inside the lock. It is reached from `Place::drop` (`mutex.rs:84-85`) and from the success path (`mutex.rs:141`).
- `semaphore.rs:86`: `rewait` replaces the old waker with `w.waker = waker.clone()`.
- `semaphore.rs:128`: `Claim::drop`'s `retain` drops the removed waker under the lock.

**Trigger:** dropping a waker runs arbitrary code. Here the waker owns an `OwnedMutexGuard` or `OwnedSemaphorePermit` of the same primitive; the `Drop` of that guard or permit calls `release()`, which takes the same `std::sync::Mutex`.

**Failure scenario (executed):**
1. `m.try_lock_owned()` returns a guard `g`. A waker `W` is built from an `Arc` that holds `g`.
2. `m.lock()` is polled with `W` and returns `Pending`. The queue now holds the only clone of `W`.
3. The `lock()` future is dropped, for example by a `timeout` or a losing `select!` arm.
4. `Place::drop` takes the state lock and calls `dequeue`, which drops `W`. That drops `g`, `g` calls `Mutex::release`, and `release` tries to lock the same state again. The thread deadlocks.

The re-poll path (`rewait`) deadlocks the same way, and so do the two `Semaphore` paths (`Claim::drop` and `rewait`).

**Who is hurt:** safe code that cancels or re-polls a waiter whose waker indirectly owns the other end. The thread hangs for good, and so does every later user of that primitive.

**Why it is a defect:**
- **The PR introduces it.** At the base, neither primitive ever destroyed a waker under the lock: wakers were popped and woken after unlocking, and `close` moved the waiters out before dropping them.
- **This repo already treated the same pattern as a bug.** `23b28c0f9` ("drop mpsc wakers outside the channel lock") fixed it with the same Sender-owning-waker reasoning, and `mpsc.rs` now returns removed or replaced wakers to be dropped after unlocking.
- **Tokio parity.** tokio 1.53.3 `batch_semaphore.rs:551-576` builds an `old_waker` and calls `drop(waiters); drop(old_waker);`. On cancel (`Acquire::drop`, `:758-778`), the waker lives in `self.node` and is destroyed only after the lock guard is gone.

**Required correction:** use the `mpsc.rs` pattern.
- `rewait` returns the replaced waker (`std::mem::replace`).
- `dequeue` returns the removed `(id, Waker)`.
- `Claim::drop` takes the matching waiter out with `position` + `remove` instead of `retain`.
- Each caller drops these values after `drop(state)`. That covers `Place::drop`, the success path at `mutex.rs:141`, both `poll_fn` closures and `Claim::drop`.

**Regression tests:** add these to `tests/lock_cancel.rs`, or to a sibling file like `mpsc_cancel.rs`'s re-entrant tests. Each one runs on a thread with a timeout. I wrote all four in scratch (`$TMPDIR/probe/tests/reentrant.rs`): all 4 deadlock at the head and all 4 pass at the base.
- `mutex_cancel_drops_waker_outside_lock`
- `mutex_refresh_drops_old_waker_outside_lock`
- `semaphore_cancel_drops_waker_outside_lock`
- `semaphore_refresh_drops_old_waker_outside_lock`

### F2 (P2): new comment contradicts the code

**Where:** `mutex.rs:68` and `semaphore.rs:91` both say: "No code that can panic runs while `state` is held, so a poisoned lock still guards consistent state."

**Problem (source-traced):** waker code does run under the lock. `Waker::clone` runs there in `enqueue` (`mutex.rs:37`) and `rewait` (`mutex.rs:45`, `semaphore.rs:86`), and the waker drops from F1 run there too. Both are arbitrary vtable code and can panic.

The recovery itself still looks safe: on each path, an unwind leaves the queue consistent (`VecDeque::remove` has finished before the drop, and a replaced slot still gets its new value). But the stated justification is false.

**Required correction:** fixing F1 removes the drops from under the lock. Then reword the comment to say that only waker clones run under the lock, and why a panic there leaves the state consistent. No test needed; this is a documentation fix.

## Design questions

None. The non-strict-FIFO Mutex (a fresh `lock()` can take the lock ahead of a woken waiter) was there before this PR. It is documented in `release`, and this PR improves it, because the woken waiter now keeps its place at the front.

## Optional improvements

- **`close` has no regression test.** `semaphore_close_still_fails_a_queued_waiter` passes at the base, so it doesn't guard the "close sets outcomes under the lock" change. I traced the old ordering with the new `Claim` and found nothing observable: a `PENDING` read, then a no-op `retain` and an empty `grant`. So this is defensive hardening, not a coverage gap. The fragment could say so.
- **RwLock has the same pattern, but it predates this PR.** `rwlock.rs:96` (`w.waker = waker.clone()` in `rewait`), and the removal in its `Place` drop, destroy wakers under the lock. The fragment's line "RwLock was already cancellation-safe" is accurate for cancellation, but RwLock may want the F1 fix as a follow-up.

## Failure classes checked

- **Cancelled waiter (source-traced, plus the PR's tests executed):**
  - Mutex: dropping before the first wake removes the waiter. Dropping after its wake while at the front passes the wake on whenever the lock is free. Dropping a middle waiter has no effect on wakes. A front waiter that a fresh `lock()` overtook is woken again when that caller releases.
  - Semaphore: a dropped `PENDING` claim leaves the queue and calls `grant`. A dropped `GRANTED` claim returns its permits and calls `grant`. A `CLOSED` claim does nothing.
- **Re-entrant waker:** F1.
- **Publication window:** ruled out.
  - Mutex: `locked`, the queue and `next_waker` are all read and written under one lock, and a waiter only queues after it sees `locked` under that lock.
  - Semaphore: outcomes are now stored, and the poll reads them, under the state lock.
- **Task state, reactor teardown, migration and Windows:** not applicable. The change is pure `std` sync code with no OS-specific path.
- **Tokio parity:** cancel safety and returning granted permits match `batch_semaphore.rs` `Acquire::drop`. Dropping wakers outside the lock does not match (F1).
- **Change fragment claims:** I verified "8 of 9 fail on `main`" by running `lock_cancel.rs` against the base sources: 8 failed and the close test passed.

## Checks run

The sandbox has a disk quota of about 75 MB, so I built with debug info and incremental compilation off and cleared `target` between runs.

| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --test lock_cancel` (head) | 9/9 pass |
| Same file against the base sources (scratch copy) | 8 fail, 1 pass, as the fragment says |
| `cargo test -p rusty_tokio --lib` | 23/23 pass |
| `cargo test -p rusty_tokio --test sync` | 119/119 pass |
| Re-entrant waker probes (scratch) | 4/4 deadlock at head, 4/4 pass at base |
| `cargo clippy -p rusty_tokio --lib --test lock_cancel -- -D warnings` | clean |
| `cargo fmt -p rusty_tokio --check` | clean |
| `python3 .github/scripts/changes.py check` | "366 fragment(s) ok" |

## Validation gaps (not run, or not seen to pass)

- `cargo test -p rusty_tokio --all-features`: the build ran out of disk quota (`Disk quota exceeded`) while building the library archive.
- `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings`: the link step failed under the same quota. Only the narrower clippy run above passed.
- `cargo test -p rusty_tokio --features io-uring-reactor`: not run because of the quota. The diff doesn't touch the reactor.
- `cargo check --target x86_64-pc-windows-gnu`: that target isn't installed. No Windows or macOS execution.
- `cargo test -p rusty_tonic` and the other cross-crate claims in the fragment: not re-run.

## Verdict: **changes required**

F1 (P1) blocks the merge and F2 (P2) needs a comment fix.
