<!-- rusty-tokio/1066-r2 run 3, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 296 s, 1329011 tokens, $1.36 -->

# Review of Rusty-Mill/rusty_mill PR #1066

- **Base:** `9248ea8d51bb91cfe134ab3638b939fb2780141f`
- **Head:** `3280c75ee19d93750bd973ee39f1d1dbf140a6cd`. I fetched `pull/1066/head` and it matches.
- **Commits:** b6a9c9f6 (makes Mutex and Semaphore waiters cancellation-safe, #1064) and 3280c75e (the fix for K1).

**Verdict: changes required.** There are three P2 findings. None is a hang or leak in the shipped code, but two are claims that are now false and one is a behaviour change with no test guarding it. K1 is fixed.

## Prior finding K1: fixed
Every waker that is removed or replaced is now handed back to the caller and dropped only after `state` is released:
- **Mutex:** `mutex.rs:48`, `:55`, `:90-91`, `:142-143`, `:153-154`.
- **Semaphore:** `semaphore.rs:88`, `:98`, `:143-144`, `:389-390`.
- **Grant and close paths:** these move wakers out and call `wake()` only after the unlock (`:71-76`, `:275-276`, `:421-422`).

I ran this (*executed*): both new re-entrant-waker tests deadlock and hit their 10 s limit at b6a9c9f6, and pass at the head. The fix is guarded by tests.

## Blocking findings

**F1 (P2): the change fragment's count of failing tests is wrong.** `changes/2026-10-25-rusty-tokio-lock-cancel.md:11`
- Commit 3280c75e changed "9 hand-polled tests" to "11" but left "On `main`'s code, 8 fail".
- *Executed:* with `tests/lock_cancel.rs` run against the base sources, **9 fail**. Only `semaphore_close_still_fails_a_queued_waiter` and `mutex_drops_removed_and_replaced_wakers_outside_its_state_lock` pass at base.
- **Fix:** change the line to "9 fail". It's worth also naming the two tests that pass at base, since neither guards a change against base.

**F2 (P2, coverage): no test guards the new order of operations in `Semaphore::close`.** `semaphore.rs:265-277`
- **The change:** `close` now stores `WAITER_CLOSED` for each waiter while it still holds the lock. The new `Claim` and re-poll logic depend on this.
- **Trace (*executed* as a mutation):**
  1. Thread A runs `close`: it takes the waiters and releases the lock.
  2. Task B re-polls its queued `acquire()` with a new waker. It still reads `PENDING`, `rewait` finds no entry, and the future returns `Pending` with no waker registered.
  3. A then stores `CLOSED` and wakes B's old waker.
  4. B hangs if its waker has changed, for example a future moved between `select!` or `FuturesUnordered` slots.
- **Evidence:** I put `close` back to storing after the unlock. All 11 `lock_cancel` tests still pass. A barrier-plus-repeat probe fails at iteration 60 without any widening of the window: "pending, latest waker never woken". It passes 20,000 iterations at the head.
- **Fix:** add that probe to `tests/lock_cancel.rs`. Queue an `acquire` with waker W1, then use a 2-party `Barrier` so a thread calls `close()` while the main thread re-polls with W2. Repeat about 20k times and assert that a `Pending` result means W2 was woken.

**F3 (P2, doc vs code): "No code that can panic runs while `state` is held" is false.** `mutex.rs:68`, `semaphore.rs:102`, and the fragment at line 10
- *Executed:* `Semaphore::new(1).add_permits(usize::MAX)` panics with an add overflow inside `release` while holding the lock (`semaphore.rs:419`, debug build).
- `Waker::clone` also runs user vtable code while the lock is held: `enqueue`/`rewait` in both files, and the `push_back` near `semaphore.rs:409`.
- **What still holds:** the conclusion is right. Each of these panics happens before any field is written, and my probe shows `available_permits()` still returns 1 after the poison is recovered.
- **Fix:** reword the claim to "every panic possible under `state` occurs before any field is modified", or check `add_permits` against `MAX_PERMITS` before taking the lock, as tokio does. My probe `add_permits_overflow_panics_under_the_state_lock` pins this either way.

## How each failure class came out (source-traced unless marked)
- **Cancelled waiter:**
  - A dropped Mutex front waiter passes the wake on (`mutex.rs:85-94`), whether it is dropped before or after it was woken. Its race with `release` is ordered by the lock both ways.
  - Dropping a middle waiter doesn't wake anyone, and doesn't need to: the front waiter still holds the wake.
  - A dropped Semaphore claim that is still pending leaves the queue and runs `grant()`. One that was already granted hands its permits back. One that was closed does nothing.
  - *Executed:* mutation B (no pass-on) and mutation C (no permit return) each turn their named test red, which confirms the fragment's claim.
- **Re-entrant waker:** fixed (K1). Clones still run under the lock, the same as tokio's `Notify` and batch_semaphore, so this is not a finding.
- **Publication window:** outcomes are now set only under the lock (`grant`, `close`), and the Claim reads them under the lock. The one window, in `close`, is closed (see F2 for its missing test).
- **Barging Mutex:** this is the existing, documented behaviour (not FIFO). A woken front waiter that loses the race stays queued, and the next `release` wakes it again. No wake is lost.
- **Task state, reactor, migration, Windows:** none apply. No task, reactor, fd, public signature or crate move is touched.
- **tokio parity:** close and permit semantics are unchanged. Granting is all-or-nothing rather than tokio's partial assignment, but nothing observable changes.

## Design questions
None.

## Optional improvements
- `RwLock`, which this PR doesn't touch, has the same pattern K1 found: `rwlock.rs:96` and `:103`, plus its `Place::drop`, drop a waker while holding the lock. It also still uses `.unwrap()`. This predates the PR and is worth a follow-up issue.
- `add_permits` doesn't check `MAX_PERMITS`; tokio panics before taking any lock there. This also predates the PR.

## Checks
| Check | Result |
|---|---|
| `cargo test -p rusty_tokio` (default features) | All 57 targets pass, including `lock_cancel` 11/11 |
| `cargo test -p rusty_tokio --all-features --no-fail-fast` | Not established. Every test that builds a runtime fails because the sandbox returns EPERM to `io_uring_setup` (raw syscall probe confirmed). `lock_cancel` passes 11/11 |
| `--features io-uring-reactor` | Not runnable here, for the same reason |
| `cargo clippy -p rusty_tokio --all-targets --all-features -D warnings` | Clean |
| `cargo fmt -p rusty_tokio --check` | Clean |
| `python3 .github/scripts/changes.py check` | 366 fragments ok |
| `cargo check --target x86_64-pc-windows-gnu` | Not run: the Windows std target isn't installed |
| `lock_cancel` against base and b6a9c9f6 sources, plus mutations A, B and C | Results above |

**Validation gaps:**
- The all-features and io_uring runs need a host that allows io_uring.
- Windows check and execution, and macOS execution.
- I didn't run `cargo test -p rusty_tonic`, which the fragment says passes. This isn't a migration, so it's not required.

**Housekeeping:** the scratch volume filled up partway through. I deleted only the scratch trees and build directories I had created under `$TMPDIR`. I didn't change the reviewed tree.

**Verdict: changes required** (F1–F3, all P2).
