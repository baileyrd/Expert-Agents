<!-- rusty-tokio/1066-r1 run 1, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 264 s, 850985 tokens, $1.10 -->

I found one real defect, so the verdict is **changes required**. On the PR's own target, the Mutex and Semaphore cancellation fix works.

**Review pin:** Rusty-Mill/rusty_mill PR #1066, base `9248ea8d51bb91cfe134ab3638b939fb2780141f`, head `b6a9c9f6a6d240a355aede951e3e466f03ce3402`. The fetched `pull/1066/head` matches that head SHA. The PR is one commit and touches `sync/mutex.rs`, `sync/semaphore.rs`, a new `tests/lock_cancel.rs` and a change fragment. I wrote probe tests in scratch copies outside the reviewed tree.

## Blocking findings

### F1 (P2): the new code drops wakers while holding the primitive's internal lock, so a waker whose drop touches the same Mutex or Semaphore deadlocks

**Trigger:** a queued waker holds the last reference to something whose drop uses the same primitive (another queued waiter, a guard or a permit). I graded it P2 because it needs an unusual waker. The impact is still a permanent deadlock: the internal lock stays held, so every later `lock`, `acquire` and release on that primitive blocks its thread for good.

**Where (head):**
- `mutex.rs:55`: `dequeue` throws away the waker it removes. It runs under the lock from `Place::drop` (`mutex.rs:85`) and from the acquire path (`mutex.rs:141`).
- `mutex.rs:45`: `rewait` replaces the old waker under the lock (called at `mutex.rs:146`).
- `semaphore.rs:128`: `Claim::drop` removes the waiter with `retain`, which drops its waker under the lock.
- `semaphore.rs:86`: `rewait` replaces the old waker under the lock (called at `semaphore.rs:377`).

**Failure scenario (executed):**
1. Task A's `lock_owned()` or `acquire_owned()` future is polled with waker W and queues a clone of W.
2. W owns another queued waiter on the same primitive, and W's local copy is dropped, so the queued clone is the last reference.
3. One of three things happens: A's future is dropped (cancelled), A is re-polled with a new waker, or (Mutex only) A acquires.
4. The code drops W while holding the lock. That drops the inner waiter, whose drop handler takes the same lock again, and the thread hangs forever.

**Why this counts as a regression:**
- The repo already fixed this exact class in mpsc (commit `23b28c0f9`, "drop mpsc wakers outside the channel lock"; see `mpsc.rs:66-123`, where removed or replaced wakers are returned and dropped after unlocking).
- At base, Mutex and Semaphore never removed or replaced a queued waker, so the PR introduces this.
- It also makes the new doc comments at `mutex.rs:68` and `semaphore.rs:91` false: "No code that can panic runs while `state` is held". The change fragment repeats that claim. Dropping (and cloning) a waker runs arbitrary code.

**Evidence:** I wrote five probes that wrap each case in a 2-second thread timeout:
- `mutex_cancelled_waiter_drops_its_waker_outside_the_lock`
- `mutex_repoll_drops_the_replaced_waker_outside_the_lock`
- `mutex_acquiring_waiter_drops_its_waker_outside_the_lock`
- `semaphore_cancelled_waiter_drops_its_waker_outside_the_lock`
- `semaphore_repoll_drops_the_replaced_waker_outside_the_lock`

| Tree | Result |
|---|---|
| Head | all 5 deadlock |
| Base | all 5 pass |
| Head patched to drop removed/replaced wakers after unlocking | all 5 pass, and all 9 `lock_cancel` tests still pass |

**Required correction:**
- Make `dequeue` and `rewait` return the removed or replaced `Waker`, the way mpsc's `stop_waiting` and `wait_to_send` do (with `#[must_use]`), and drop it after the guard is released. That covers `Place::drop`, the acquire path and the re-poll path in Mutex.
- In `Claim::drop`, take the removed `Waiter` out with `position` and `remove` instead of `retain`, and drop it after unlocking. In Semaphore's re-poll path, drop the replaced waker after unlocking.
- Correct the two doc comments and the change-fragment sentence.

**Regression tests:** add the five probes above to `tests/lock_cancel.rs`, using a waker that owns another queued waiter on the same primitive (the same pattern as the Sender-owning waker in `tests/mpsc_cancel.rs`). Each fails at head and passes once fixed.

### F2 (P2): no test covers the barging behaviour the PR changed

**Where:** the release comment at `mutex.rs:177-181`.

**What changed (source-traced, then executed):** a woken front waiter that loses the lock to a caller who barges in (a fresh `lock()` or `try_lock()` that takes it first) now keeps its place at the front. That caller's release wakes it again. At base, the woken waiter re-queued at the back, so the release woke the waiter behind it instead. None of the 9 PR tests checks this.

**Required test:** add `mutex_front_waiter_keeps_its_place_after_a_barge` to `tests/lock_cancel.rs`:
1. Hold the lock and queue A, then B.
2. Release; A is woken.
3. `try_lock` barges in before A re-polls; poll A, which stays pending.
4. Drop the barging guard: A must be woken and B must not.
5. A acquires.

I ran this probe: it passes at head and fails at base (`review_probe.rs:151`).

## Failure classes I ruled out
- **Cancelled waiter:**
  - **Mutex:** dropping a waiter before its first poll is a no-op. Dropping a middle waiter wakes nobody, which is correct. Dropping the front waiter wakes the next one only if the lock is free (`next_waker` returns `None` while it is locked), and a later release covers the locked case.
  - **Semaphore:** dropping a waiter that is still queued removes it and grants the waiters behind it. One that was granted hands its permits back and grants the next waiters. One that was closed does nothing. Because `queued` is cleared before `Ready`, permits are never returned twice.
- **Publication window:** Semaphore outcomes are now written (including `close`, at `semaphore.rs:254-265`) and read (poll and `Claim::drop`) under the state lock. Mutex checks `locked` under the same lock that its waiters are queued under. I found no window.
- **Wake under the lock:** every `wake` happens after unlocking (`release`, `Place::drop`, `wake_all`). The only under-lock problem is the drops in F1.
- **tokio parity:** like tokio, a cancelled `lock()` gives up its place and a dropped `acquire()` returns any permits it was granted. Barging is pre-existing and documented.
- **Not touched by this diff:** task state, reactor teardown, migration and Windows. The changed code is plain std with no OS-specific paths.

## Design questions
- None.

## Optional improvements
- `rwlock.rs:93-104` has the same F1 pattern (`rewait` replaces and `dequeue` removes wakers under the lock). It predates this PR and was the model for it; worth a follow-up with the same fix.
- No deterministic test can show that `close` must now set outcomes under the lock; the race only shows up in a thread interleaving. A short comment at `semaphore.rs:261` linking it to `Claim::drop` would protect it from a future refactor.

## Checks performed
| Check | Result |
|---|---|
| Pin: fetch `pull/1066/head` | Equals head SHA ✔ |
| `cargo test -p rusty_tokio --all-features` (head) | `lock_cancel` 9/9 pass. Lib tests 21 pass / 4 fail (3 io_uring, 1 current_thread). Every test that builds a runtime (`abort_handle`, `abort_race`, `async_fd`, …) fails because the sandbox returns EPERM. Base fails the same 4 lib tests. None are in sync code. |
| `cargo test -p rusty_tokio --features io-uring-reactor --lib` | Same 21/4, EPERM from the sandbox |
| `lock_cancel.rs` at base | 8 of 9 fail, which matches the fragment's claim |
| Reviewer probes (F1, F2) at head, at base, and at head with the fix | As tabled above |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Pass |
| `python3 .github/scripts/changes.py check` | "366 fragment(s) ok" |
| `cargo check --target x86_64-pc-windows-gnu --all-targets` | Not run: the target isn't installed, so dependencies fail to compile |

## Validation gaps
- **Runtime tests:** these need a run outside this sandbox, which blocks runtime creation with EPERM. That includes the whole all-features suite and the io_uring reactor tests.
- **Windows:** `cargo check` for `x86_64-pc-windows-gnu` and Windows execution were not run.
- **macOS:** not run.
- **Not reproduced:** the fragment's claim that `cargo test -p rusty_tonic` passes.

## Verdict: **changes required** (F1, F2)
