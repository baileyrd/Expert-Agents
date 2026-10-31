<!-- rusty-tokio/1066-r2 run 5, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 291 s, 1798109 tokens, $1.40 -->

# Review of PR 1066: K1 is fixed; two P2 findings in comments and the change fragment

**Verdict: changes required.** Both findings are wrong statements in a comment and in the change fragment. The code itself held up under everything I ran.

**Pinned:** Rusty-Mill/rusty_mill PR 1066 · base `9248ea8d51bb91cfe134ab3638b939fb2780141f` · head `3280c75ee19d93750bd973ee39f1d1dbf140a6cd`. The fetched `pull/1066/head` equals the head SHA. There are two commits: b6a9c9f6 (the original fix) and 3280c75e (the fix for K1).

## K1 re-check (P2, waker destroyed under the state lock): resolved
- **Mutex:** the three places that removed or replaced a waker now hand it out and drop it after `state` is released: `Place::drop` (mutex.rs:85-91), the `acquire` ready path (141-143) and `rewait` (146-154).
- **Semaphore:** same for `Claim::drop` (semaphore.rs:131-144) and the re-poll path (388-390). `close` (271-276) and `grant` move wakers out and wake them only after unlocking.
- **Tests:** the two new tests that use wakers whose destructor re-enters the lock deadlock at b6a9c9f6 (both hit the 10 s bound) and pass at this head. So they guard K1.
- **Out of scope but related:** `RwLock` still drops wakers under its lock (rwlock.rs:96, :104). This PR doesn't touch it; see the optional items.

## Blocking findings

**F1 (P2): the new comment and the fragment claim "no code that can panic runs while `state` is held". That is false.**
- **Where:** mutex.rs:68-69, semaphore.rs:102-103, and the "Changed" bullet of `changes/2026-10-25-rusty-tokio-lock-cancel.md`.
- **Why it's false:** `Waker::clone` runs the waker's own clone code under `state`, and that code can panic. It happens in `enqueue` (mutex.rs:40), `rewait` (48), `next_waker` (64), in semaphore.rs:88, and in the semaphore's `push_back` at 404-408.
- **Trace (executed):** the lock is held; waiter W queues with a waker whose second clone panics; the guard is dropped, so `release` sets `locked = false` and then `next_waker`'s clone panics under `state`.
  - The panic comes out of the guard's drop, and the lock is left poisoned.
  - The probe then found `try_lock` succeeds (the lock is free) and W was never woken.
  - So W, and every waiter behind it, waits until some unrelated caller locks and unlocks.
- **What still holds:** I checked every clone site, and each runs before any state change it belongs to. So recovering from poison is still sound. Only the stated reason is wrong.
- **Trigger:** a waker whose clone panics. That is unusual, but the grade is for a doc that disagrees with the code.
- **Correction:** reword both comments and the fragment. For example: "Only `Waker::clone` can panic under `state`, and every call happens before the state change it belongs to, so a poisoned lock still guards consistent state."
- **Test:** none is needed for the wording. If the claim is kept, add a test to `tests/lock_cancel.rs` with a waker whose clone panics, asserting the mutex and semaphore stay usable afterwards.

**F2 (P2): the fragment says "On main's code, 8 fail". Run against base, 9 of the 11 tests fail.**
- **Where:** the "Verified" bullet of the change fragment.
- **Executed:** I ran the head's `tests/lock_cancel.rs` against the base's `mutex.rs` and `semaphore.rs`. 9 tests failed and 2 passed.
- **Why:** "8" was true of the original 9 tests. `semaphore_drops_removed_and_replaced_wakers_outside_its_state_lock`, added by 3280c75e, also fails at base: base never drops a cancelled waiter's waker, so its `available_permits() == 2` check fails.
- **Correction:** change it to "9 fail", or state which tests the count refers to.

## Failure classes checked
- **Cancelled waiter (traced and executed):**
  - Mutex: dropped after `Pending`, after its wake, and while in the middle of the queue. Each time it leaves the queue, and a front waiter passes its wake on when the lock is free. If someone took the lock in between, that holder's release wakes the next waiter.
  - Semaphore: a `PENDING` claim removes itself and calls `grant`. When the queue is at rest its front never fits, so removing a middle waiter grants nothing (correct). A `GRANTED` claim returns its permits and grants. A `CLOSED` claim does nothing.
  - The claim's outcome is only set under `state` (`grant`, and `close` at 272-274), so reading it in `Claim::drop` gives a final answer.
  - Stress probe: 8 threads × 20k iterations, cancelling at random right after `Pending` or after a short park, with a 5 s lost-wake watchdog and a check that permits are conserved. It catches the semaphore lost wake at base and passed 10 of 10 runs at head.
- **Re-entrant waker:** a re-entrant destructor is K1, now fixed. A re-entrant `clone` under the lock is the same as tokio, which also clones under its waiter lock (notify.rs, batch_semaphore.rs). Not a finding.
- **Publication window:** waits re-check `locked` or the outcome under `state`, and every wake is issued after the state change it announces. None lost.
- **tokio parity:** returning a granted-but-unused permit on drop now matches tokio's `Acquire` drop. A fresh `lock()` can still take the lock ahead of a woken waiter (not strict FIFO), but that predates this PR and is documented at mutex.rs:183-189.
- **Task state, reactor teardown, migration:** not touched. The diff is only `sync/mutex.rs`, `sync/semaphore.rs`, a test and the fragment.
- **Windows:** the changed code is std-only with no OS-specific paths.

## Design questions
None.

## Optional improvements
- `RwLock` drops wakers under its lock in `rewait` (rwlock.rs:96) and `dequeue` (rwlock.rs:104). This predates the PR and is outside its diff, but it is the same class as K1, so it's worth a follow-up issue.
- The semaphore re-entrancy test covers the removal and re-poll paths only. A re-entrant waker on the `GRANTED` drop path and on `close` would pin those down too; both are correct today by inspection.

## Checks
| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | `lock_cancel` 11/11 pass. Every test that builds a runtime fails, because the sandbox refuses `io_uring_setup` (EPERM, e.g. io_uring.rs:403). That's environmental and in files this PR doesn't touch. |
| `cargo test -p rusty_tokio` (default features, `--no-fail-fast`) | 57 binaries pass. All 40 failing tests panic while creating an AF_UNIX socket (EPERM) or joining multicast (ENODEV), which are sandbox limits. |
| `--features io-uring-reactor` | Same EPERM; covered by the all-features run. |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Passes. |
| `cargo check --target x86_64-pc-windows-gnu --all-targets` | Not run: the target isn't installed (E0463). |
| `python3 .github/scripts/changes.py check` | Passes (366 fragments ok). |
| Head tests against base / b6a9c9f6 / head | 9 fail / 2 fail (the K1 tests time out) / 0 fail. |
| Stress probe and panicking-clone probe | Results as described above. |

**Validation gaps:**
- io_uring and Unix-socket tests in a sandbox that allows those syscalls, i.e. a normal CI Linux runner.
- The Windows cross-check after `rustup target add x86_64-pc-windows-gnu`.
- Windows and macOS execution. The changed code is std-only, so I expect no difference.

My scratch probes are under `$TMPDIR/probe1066`. Nothing in the reviewed tree was modified.
