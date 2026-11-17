<!-- rusty-tokio/1250-r1 run 4, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 251 s, 781353 tokens, $0.99 -->

# Review of PR 1250: rusty_tokio, a dropped `Notified` leaves the waiter queue

- **Repository:** Rusty-Mill/rusty_mill, PR #1250
- **Base:** `d05170b8cc082cffaf6c348411e82bea84b9632d`
- **Head reviewed:** `98621f936d802e1fb05912e47f1b569eebe230c9` (`git fetch origin pull/1250/head` returned exactly this SHA)
- **Diff:** one commit, touching `crates/libs/async/rusty_tokio/src/sync/notify.rs` and `changes/2026-10-30-rusty-tokio-notify-drop.md`

**Verdict: changes required.** The fix closes the old bug, but it opens a new lost wakeup on the race it set out to fix. Two of its "as tokio does" claims are also false.

## What must hold, and what I compared against

I compared the code with tokio 1.53.3's `sync/notify.rs`, the version in `Cargo.lock`:
- **I1:** a waiter still in the queue removes itself when dropped.
- **I2:** a single wakeup (`notify_one` or `notify_last`) that reached a waiter which was then dropped unresolved goes to the next waiter, using the same strategy, or is banked. tokio does this in `drop_notified`, lines 1361-1371.
- **I3:** a waiter's notification is set under the same lock the dropping side reads it under. tokio sets it in `notify_locked`, lines 868-881, and reads it under that lock at line 1347.
- **I4:** no waker is woken under `inner`.
- **I5:** a notification that has been used up is never passed on. In tokio, a future that took a permit is `Done`, and dropping a `Done` future does nothing (lines 1134-1137 and 1341).

I1, I4 and I5 for `notify_waiters` hold. I2, I3 and I5 for `enable` do not.

## Blocking findings

### 1. P1: a lost wakeup when `notify_one` and the drop race on two threads

- **Where:** `notify.rs:101-104` and `114-118` pop the waiter and unlock before `fire` runs. `fire` sets `single` only at `notify.rs:146`, outside the lock. `release` reads `single` under the lock at `notify.rs:282-285`.
- **Failure scenario (executed):**
  1. X and Y are both queued.
  2. Thread A calls `notify_one`, pops X and drops the lock.
  3. Thread B drops X. `release` locks, does not find X in the queue, reads `single == false`, and returns.
  4. Thread A runs `fire(X)`, which wakes a future that no longer exists.
  5. Y is never woken.
- **Who is hurt:** this is the case the PR exists to fix: a `select!` loop on a multi-thread runtime, with the notifier on another thread (#1247). It is a hang until the next `notify_one`. The same window exists in `release`'s own forward at lines 286-289, so a forwarded wakeup can be lost the same way. There is also a memory-ordering problem: the read is `Relaxed` with no happens-before edge to the store.
- **Evidence:**
  - With a hook placed between `drop(guard)` and `fire` in a scratch copy of the head code, the test fails every time: Y has been woken 0 times and polls `Pending`.
  - With no hook, a stress test on the head copy lost 1 wakeup in 300,000 runs.
- **Fix:** set `single` and `woken` while `inner` is still held in `notify_one`, `notify_last`, `notify_waiters` and `release`. Take and wake the waker only after unlocking, the way tokio's `notify_locked` does.
- **Regression test:** a test in `sync/notify.rs` that drops X between the pop and the wake, and asserts that Y becomes ready. It needs a `cfg(test)` hook or a stress loop with a barrier. It fails on the current head.

### 2. P1: a permit taken by `enable` is banked again, which tokio does not do

- **Where:** `notify.rs:257-263`, where `enable` taking a permit now sets `registered = true`, so `release` banks the permit again. The same claim appears in the module docs (lines 31-33), the fragment ("or a permit taken by `enable` ... as tokio does"), and the test `a_permit_taken_by_enable_and_dropped_is_banked_again`, which makes the divergence the expected behaviour.
- **Failure scenario (executed):** call `notify_one`, then `notified().enable()`, which returns `true`, then drop the future, then poll a fresh `notified()`. Head returns `Ready`, tokio returns `Pending`, and base returns `Pending`.
- **Who is hurt:** a single notification is delivered twice. tokio's own documented `recv` idiom (`enable()`, then `try_recv` returns `Some`, return) puts every permit back. Because `permits` here is an unbounded counter, they never drain, and later waiters wake with nothing to do. No in-tree code calls `enable` today, so this hits code ported from tokio.
- **Fix:** when `enable` takes a permit, the future should become done, matching tokio's `Done`, so dropping it banks nothing.
- **Regression test:** change the PR's test to assert that the fresh `notified()` stays `Pending`.

### 3. P2: a forwarded `notify_last` goes to the oldest waiter, not the newest

- **Where:** `notify.rs:286` always forwards with `pop_front`. tokio forwards with the original strategy (`Notification::One(strategy)` passed to `notify_locked`).
- **Failure scenario (executed):** waiters A, B and C are queued; `notify_last` reaches C; C is dropped. Head wakes A; tokio wakes B. That contradicts "as tokio does" in the module docs and the fragment.
- **Fix:** store the strategy instead of a bool, and forward with `pop_back` for a LIFO wakeup.
- **Regression test:** this exact sequence, asserting which waiter is woken.

### 4. P2: a doc this PR makes false

- **Where:** `sync/cancellation_token.rs:7-13` still says a dropped `Notified` "stays in `Notify`'s waiter queue until the next notification".
- **Fix:** update it to give the reason that is still true, or drop the claim.

### 5. P2: test coverage

- `OwnedNotified`'s new `Drop` impl has no test.
- The `notify_last` forward has no test.
- No test crosses threads.
- These belong in `sync/notify.rs`'s test module, as the tests named in findings 1-3.

## How the PR's own tests behave

| Test | At base | At head | What it guards |
|---|---|---|---|
| `a_waiter_dropped_unfired_does_not_swallow_a_notify_one` | fails | passes | the fix |
| `a_notify_one_reaching_a_dropped_waiter_passes_to_the_next` | fails | passes | the fix |
| `a_permit_taken_by_enable_and_dropped_is_banked_again` | fails | passes | the divergence in finding 2 |
| `a_consumed_wakeup_and_a_notify_waiters_one_are_not_passed_on` | passes | passes | over-forwarding only, not the fix |

## Failure classes ruled out

- **Re-entrancy:** nothing is woken under `inner`. The only `Arc` dropped under the lock, in `release` at line 279, is never the last reference.
- **Wakeups that were used up, and `notify_waiters` wakeups:** neither is passed on (I5 holds outside `enable`).
- **Reactor, migration, `select!` order:** not touched by this PR.
- **Windows:** the code has no platform-specific paths.
- **New `Drop` impl and borrow checking:** `rusty_mux_server`, `rusty_lsp` and `rusty_term` still compile with `--all-targets`.

## Design questions

None.

## Optional improvements (none of these were introduced by this PR)

- `fire` (`notify.rs:148-150`) calls `wake()` while holding the `state.waker` mutex, because the `if let` temporary lives through the block. A waker that re-polls the same future synchronously would deadlock. Take the waker into a local first.
- `permits` is an unbounded counter, while tokio holds at most one permit.

## Checks run

| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | Failed: 7 lib failures. All are `Operation not permitted` when creating a runtime (io_uring, stdio, current_thread), in code this PR doesn't touch. With `--no-fail-fast`, the integration suites and doctests fail the same way. Not attributable to the PR, but not seen green. |
| `cargo test -p rusty_tokio --all-features --lib sync::notify` | 4/4 pass |
| `cargo test -p rusty_tokio --features io-uring-reactor` | Same 7 sandbox failures |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Passed |
| `python3 .github/scripts/changes.py check` | Passed: 541 fragments ok |
| `cargo check --offline` on `rusty_mux_server`, `rusty_lsp`, `rusty_term` | Passed |
| Scratch probes (head, base, tokio 1.53.3 side by side) | Results as given in findings 1-3 |

## Validation gaps

- **Windows check:** `cargo check --target x86_64-pc-windows-gnu` did not run because the target isn't installed (`rustup target add x86_64-pc-windows-gnu`).
- **`sessionmgr-daemon`:** did not build because the `rustls` crates aren't in the offline cache.
- **Runtime-backed tests:** the sandbox returns `Operation not permitted` when a runtime is created, so these need an unrestricted Linux host.
- **macOS and Windows execution:** not run.

**Verdict: changes required.**
