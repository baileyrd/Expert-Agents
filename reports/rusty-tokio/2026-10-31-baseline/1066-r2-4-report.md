<!-- rusty-tokio/1066-r2 run 4, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 253 s, 633261 tokens, $0.78 -->

# Review of Rusty-Mill/rusty_mill PR 1066: two P2 findings, changes required

- **Base:** 9248ea8d51bb91cfe134ab3638b939fb2780141f
- **Reviewed head:** 3280c75ee19d93750bd973ee39f1d1dbf140a6cd. I fetched `pull/1066/head` and it matches.
- **Commits:** b6a9c9f6 (cancellation-safe Mutex and Semaphore waiters, #1064) and 3280c75e (the fix for K1).

**Verdict: changes required.** The prior finding K1 is fixed, and the fix is guarded by tests. Two findings remain, both about wording (P2), not behaviour.

## K1 re-check: fixed

Every waker that is removed or replaced is now moved out under the state lock and dropped after it is released. That covers:
- `Place::drop` (mutex.rs:82-96).
- The Mutex path that acquires (mutex.rs:139-145) and the one that refreshes the waker (mutex.rs:146-154).
- `Claim::drop` (semaphore.rs:126-147).
- The Semaphore waker refresh (semaphore.rs:383-395).
- `close` (semaphore.rs:265-277).

Wakes in `release` and `grant` also run after the lock is dropped.

Executed evidence:
- **At b6a9c9f6:** both re-entrant tests fail by timing out after 10 s, which is the deadlock.
- **At the head:** both pass.
- **Mutations at the head:** I moved each of the five drops back under the lock, one at a time (Mutex `Place::drop`, Mutex acquire, Mutex refresh, Semaphore refresh, Semaphore `Claim::drop`). Each one turns its test red. So every path is guarded on its own.

## Blocking findings

**F1 (P2): the change fragment's test count is false.**
- **Where:** `changes/2026-10-25-rusty-tokio-lock-cancel.md:12`, which says "11 hand-polled tests … On `main`'s code, 8 fail."
- **What happens (executed):** I ran the head's `tests/lock_cancel.rs` against the base, and 9 of 11 fail. The extra one is `semaphore_drops_removed_and_replaced_wakers_outside_its_state_lock`. At the base, a cancelled claim never leaves the queue, so its waker's destructor never runs, and `available_permits` is 0 where the test expects 2. Only `semaphore_close_still_fails_a_queued_waiter` and `mutex_drops_…` pass there.
- **Fix:** say "9 fail". No regression test applies; it is a wording fix.

**F2 (P2): the "No code that can panic" comment is false.**
- **Where:** mutex.rs:68 and semaphore.rs:102, which say "No code that can panic runs while `state` is held, so a poisoned lock still guards consistent state."
- **What happens (traced in the source):** caller code does run under the lock, through `Waker::clone`, which calls the caller's `RawWakerVTable` clone function. That happens at mutex.rs:40 (enqueue), mutex.rs:48 (refresh) and mutex.rs:64 (`next_waker`), and at semaphore.rs:88 and semaphore.rs:407. Also, `permits += …` at semaphore.rs:134 and semaphore.rs:419 can panic on overflow in debug builds.
- **Impact today:** none on behaviour. Each clone runs before the state it changes, so a panic leaves the state consistent (at worst a skipped `next_id`). Only the stated reason is wrong. Someone who later edits these functions trusting the comment could break poison recovery.
- **Fix:** reword it, for example: "anything that can panic under `state` (waker clones, permit arithmetic) runs before the mutation it feeds, so a poisoned lock still guards consistent state."
- **Test:** in `tests/lock_cancel.rs`, poll a `lock()` or `acquire()` with a waker whose clone panics once, inside `catch_unwind`. Then check that a later `try_lock`/`try_acquire`, a release and the waking of the next waiter still behave correctly.

## How I checked each failure class

The invariants I checked for each touched item: each waiting future has exactly one queue entry; release or grant wakes the waiter at the front; a dropped waiter removes its entry and passes on a wake it received; a semaphore permit it was granted but never took is given back. Each failure class from the review steps:

- **Cancelled waiter:** I traced a Mutex waiter dropped while queued, after being woken, and from the middle of the queue. I also traced a barging `lock()` that takes the mutex ahead of the woken front waiter. That front waiter stays queued and is woken again on the next release. On the Semaphore side, I traced a drop while still waiting, a drop after permits were granted, and a drop after `close`. No wake is lost, and the tests cover these cases.
- **Re-entrant waker:** removing or replacing a waker is now safe (K1). Waker clones still run under the lock, which matches tokio's `batch_semaphore`; see the optional improvements.
- **Publication window:** a waiter's outcome is set and read only under the state lock, and that now includes `close`.
- **Task state, reactor teardown, Windows behaviour:** not touched by this diff.
- **Migration:** this PR moves no crate onto the runtime.
- **tokio parity:** this Mutex lets a new `lock()` take the mutex ahead of queued waiters, so it is not strictly FIFO. That predates this PR and is documented at mutex.rs:178-189.

## Design questions

None.

## Optional improvements

- A waker clone that re-enters the primitive still deadlocks, because cloning happens under the lock. tokio does the same, so this matches it, but one sentence in the module docs could say so.
- The doc comment on `Semaphore::release` now points to `State::grant`, a private item. That link may not resolve in public rustdoc.

## Checks run

| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features --test lock_cancel` | 11/11 pass; also passes with default features |
| `cargo test -p rusty_tokio --test sync` (default features, epoll) | 119 pass |
| `cargo test -p rusty_tokio --all-features` | Every test that builds a runtime fails with EPERM (`Operation not permitted`) in this sandbox, including untouched io_uring, current_thread and abort tests. Not caused by the PR. |
| `cargo test -p rusty_tokio --features io-uring-reactor` | Same EPERM |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Clean |
| `cargo fmt -p rusty_tokio -- --check` | Clean |
| `python3 .github/scripts/changes.py check` | "366 fragment(s) ok" |
| `cargo check --target x86_64-pc-windows-gnu` | Not run: the target isn't installed here |
| Head tests at base and at b6a9c9f6, plus five mutations | Results as above |

## Validation gaps

- **Full suite, all features and io-uring:** I didn't see these pass, because the sandbox blocks runtime creation. They need a Linux run with io_uring allowed.
- **Default-feature full suite:** its output was lost when the temp volume hit its disk quota. To make room I deleted only the scratch worktrees and build directory I had created; about 7 GB remains on that volume and I didn't touch it.
- **Windows and macOS:** not executed. The Windows cross-check needs `rustup target add x86_64-pc-windows-gnu`. The diff is platform-independent `std::sync` code, so the risk is low.

**Verdict: changes required (F1, F2).**
