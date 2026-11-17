<!-- rusty-tokio/1085-r1 run 5, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 463 s, 4774542 tokens, $2.89 -->

I reviewed PR 1085 and the verdict is **changes required**. The diff moves rusty_lsp's `runtime` and `tcp` features onto rusty_tokio and adds `watch::Receiver::wait_for`, `AsyncBufRead for Take` and `Debug for Notify`. Moving rusty_lsp exposes it to rusty_tokio's `watch` and `Notify` primitives, which leave a waiter behind when a future is dropped. I reproduced one of these in rusty_lsp's own read loop: memory grows with every message for as long as the connection lasts.

- **Repository:** Rusty-Mill/rusty_mill, PR #1085
- **Base:** `0beaf37853b445cb8e2a09485029f0d982d53d77`
- **Reviewed head:** `349b521ab20b790caa53174a1b802057d30237ea` (confirmed: `FETCH_HEAD` for `pull/1085/head` matches)

Probes ran from scratch crates in `$TMPDIR`; I changed nothing in the reviewed tree.

## Blocking findings

### 1. P1: a dropped `watch::Receiver::changed()` leaves its waker queued, and rusty_lsp's read loop drops one per message
- **Where:** `crates/libs/async/rusty_tokio/src/sync/watch.rs:204-222` (`changed`, line 216 pushes the waker; nothing removes it on drop). It becomes reachable at `crates/libs/protocol/rusty_lsp/src/server.rs:436-446`.
- **Trigger:** any rusty_lsp session; it happens on every message read.
- **What happens:** each turn of `run_loop` builds a new `writer_dead.changed()` inside `select!`. That future registers a waker clone in `Inner::wakers`, then is dropped whenever `read_message` wins. `writer_dead_tx` only sends when a write fails, and only `send` or `Drop` of the sender clears the list. So the `Vec<Waker>` grows by one entry per message until the connection ends.
- **Why it is new:** at base this was tokio's `watch`, whose `changed_impl` waits on a `Notify` that removes itself on drop (tokio 1.53.3 `sync/watch.rs:983-1001`, `notify.rs:1038`). The `changed()` code is unchanged at base, but no consumer used it this way before.
- **Evidence (executed):**
  - Hand-polled `rx.changed()` 1000 times, polled to `Pending` and then dropped: rusty_tokio kept **1000** waker clones; tokio 1.53.3 kept 0.
  - Wrapped rusty_lsp's real `Server::serve` in a counting waker: it held 13 clones after 10 messages and **503 after 500**.
- **Who is hurt:** every long-running LSP session served by rusty_lsp.
- **Fix:** make `changed()` cancellation-safe. Give each waiter a slot that its `Drop` removes, and replace the stored waker on every poll, the way #1064 did for `Mutex`/`Semaphore` (`rwlock.rs:152-160` has the same pattern). Hoisting one pinned `changed()` out of the loop in `server.rs` would stop the leak there as a stopgap, but not for other callers.
- **Regression tests:**
  - `rusty_tokio/tests/sync.rs`: `watch_changed_dropped_after_pending_leaves_no_waker` (hand-polled, counting waker; fails at head).
  - `rusty_lsp/tests/integration.rs`: a serve-loop waker-count test like my probe (`$TMPDIR/probe1085lsp/tests/leak.rs`), asserting the count stays flat between 10 and 500 messages.

### 2. P1: a panicking `wait_for` predicate poisons the channel, breaking tokio's documented promise
- **Where:** `crates/libs/async/rusty_tokio/src/sync/watch.rs:191-192`. The predicate `f` runs while the watch's std `Mutex` is held, with no unwind guard.
- **Trigger:** any predicate that panics.
- **Contract broken:** tokio 1.53.3 documents for `wait_for`: "If and only if the closure `f` panics. In that case, no resource owned or shared by this Receiver will be poisoned." Its implementation wraps `f` in `catch_unwind`, drops the lock, then resumes the panic (`sync/watch.rs:865-868, 910-922`).
- **Evidence (executed):**
  - After the predicate panicked, `tx.send(1)` panicked with `PoisonError` at `watch.rs:72`.
  - The receiver's `Drop` then panicked too, at `watch.rs:240`.
  - When that drop runs during unwinding, the result is "panic in a destructor during cleanup … aborting" (SIGABRT). The same sequence on tokio returns `Ok` from `send`.
- **Who is hurt:** every other holder of the channel. Senders, receivers and clones all panic from then on, and the process can abort.
- **Fix:** use `catch_unwind` around `f`, drop the guard, then `resume_unwind`, as tokio does. Alternatively, make the watch's lock calls tolerate poisoning.
- **Regression test:** `tests/sync.rs`: `watch_wait_for_panicking_predicate_does_not_poison`. It should check that `send`, `borrow` and drop still work afterwards.

### 3. P2: `changed()` (and so `wait_for`) wakes the first waker it saw, not the latest
- **Where:** `watch.rs:215-218`. The `registered` flag stops a later poll from replacing the stored waker.
- **Trigger:** the future is polled again with a different waker, for example after it moves to another task.
- **What happens (executed):** I polled with waker A, polled again with waker B, then sent. A was woken; B was woken **0** times, so the task holding the future is never woken. This breaks the `Future` contract that only the waker from the most recent poll may be woken.
- **Fix:** the same as finding 1, replacing the waker on every poll.
- **Regression test:** `tests/sync.rs`: `watch_changed_repolled_is_woken_through_latest_waker`. It mirrors `lock_cancel.rs:132/197`.

### 4. P2: a dropped `Notify::notified()` stays queued, so rusty_lsp's `CancelToken::cancelled()` builds up waiters
- **Where:** `crates/libs/async/rusty_tokio/src/sync/notify.rs:198, 236, 241`. `Notified` has no `Drop`. It becomes reachable from `crates/libs/protocol/rusty_lsp/src/cancel.rs:74-89`.
- **Trigger:** a handler that uses `cancelled()` as a `select!` branch in a loop. rusty_lsp's docs call it "`select!`-friendly".
- **What happens (executed):** after 1000 `notified()` futures were polled and dropped, **1000** waiter entries were still held. They stay until `cancel()` runs or the token is dropped, so each request leaks for its whole lifetime. Before this PR, tokio's `Notified::drop` removed the entry.
- **Related, not used by rusty_lsp (source-traced):** `notify_one` pops the oldest entry even when its future was dropped, so the wakeup is lost for a waiter that is still live.
- **Fix:** a `Drop` for `Notified`/`OwnedNotified` that removes the entry, and passes on a wakeup the dropped waiter had already received.
- **Regression test:** `tests/sync.rs`: `notified_dropped_after_pending_leaves_no_waiter`.

### 5. P2: the new `wait_for` runs code under the watch's state lock (source-traced)
- **Where:** `watch.rs:191-192` runs the predicate under the lock; `watch.rs:216` clones the waker under it.
- **Trigger:** a waker whose `clone` touches the same channel, or a predicate that calls the channel, for example `tx.send` or a `borrow` on the sender or another receiver.
- **What happens:** std `Mutex` is not re-entrant, so the thread deadlocks against itself. #1064 set the opposite rule for `Mutex` and `Semaphore` (`tests/lock_cancel.rs:317, 358`: "never clones a waker under its state lock").
- **Fix:** clone the waker outside the lock, as part of the finding 1 rewrite.
- **Regression test:** a clone-hook test like `lock_cancel.rs:286-358`, added for `watch`.

### 6. P2 (doc made false): `crates/libs/ui/rusty_term/l13/Cargo.toml:17`
It still says "rusty_lsp's tokio-backed `runtime` feature". After this PR that feature runs on rusty_tokio.

**Missing tests:** the three new `wait_for` tests in `tests/sync.rs` cover the happy paths only. Cancellation, panics, waker replacement and leaks are covered only by the regression tests listed above.

**Ruled out:**
- **Lost wakeup inside `wait_for`:** none. `seen` only advances inside `changed()`, so a send between the predicate check and the next registration is still seen.
- **`select!` order:** random, like tokio (`macros.rs` doc, point 3).
- **Semaphore and RwLock under abort:** cancellation-safe (#1064, `rwlock.rs` docs).
- **`Take::consume` and `poll_fill_buf`:** match tokio.
- **tier C / G9:** rusty_lsp has no G9 dependency, so tokio's reactor isn't needed.
- **Reactor code:** the diff touches none.
- **Workspace dependents:** nexus-vt and l13 take rusty_lsp with default features off, so the claim that no workspace crate uses the runtime feature holds.
- **The fuzz crate:** still on tokio, but its transport body builds and runs against the head. I ran it as a test.

## Design questions
- **New `Send` bounds in rusty_lsp's public API** (`R: Send` on `Server`, `read_message`; `W: Send` on `write_message`). These come from rusty_tokio's extension methods requiring `Self: Send` (`async_io.rs:260, 585, 604, 839`), which differs from tokio. The changelog and change fragment disclose it as owner-approved. But G1's goal is that "a migration renames the crate and nothing else", and the inventory doesn't list this as a gap. Should it be recorded, since every later migration of generic I/O code will hit it?
- **Possible hang at exit:** `Runtime::drop` waits with no time limit for the blocking pool (`runtime/blocking.rs:182`). A stdin read still blocked after `exit` can therefore delay process exit. tokio documents the same behaviour for `stdin`, so this is parity, not a defect.

## Optional improvements
- The fragment's "matching tokio" for `Debug for Notify`: tokio's derived output lists its fields, while this prints `Notify { .. }`.
- `watch::Ref` has no `has_changed()`, which tokio's does.
- Pre-existing and outside this diff: `Sender::send` drops the old value while holding the lock (`watch.rs:76`). tokio drops it outside.
- `watch_wait_for_skips_values_until_one_matches` uses sleeps where a deterministic sequence would do.

## Checks
- **`cargo test -p rusty_tokio --all-features`** and **`--features io-uring-reactor`**: both fail here, but not because of the code. Every failure is `PermissionDenied` from `io_uring_setup` when a runtime is built; the sandbox blocks io_uring. This is a validation gap and needs a Linux host where io_uring is allowed.
- **Same tests with every non-io_uring feature:** 671 passed, 33 failed. All 33 failures come from the sandbox:
  - 29 are unix-socket EPERM.
  - 3 are multicast ENODEV.
  - 1 is a socket path too long for SUN_LEN (the `$TMPDIR` path is long).

  `tests/sync.rs` passed 123/123 and `tests/chain_take.rs` 8/8, including all six new tests.
- **`cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings`:** passes.
- **`cargo check -p rusty_tokio --target x86_64-pc-windows-gnu --all-targets`:** could not run because the target isn't installed (E0463). Validation gap, along with Windows and macOS execution of rusty_lsp's stdio and TCP paths.
- **`cargo test -p rusty_lsp --all-features`:** 267 passed, 0 failed, 6 ignored.
- **`cargo clippy -p rusty_lsp --all-targets --all-features -- -D warnings`:** passes.
- **`python3 .github/scripts/changes.py check`:** "379 fragment(s) ok".
- **Probes run:**
  - Hand-polled counting-waker tests for `changed` and `Notified`, with tokio 1.53.3 as the comparison.
  - The `wait_for` poisoning test.
  - The rusty_lsp serve-loop waker count.
  - The fuzz-target body.

  The PR's own new tests can't compile at base because the APIs are new, so they do guard the change.

**Verdict: changes required.** The two P1s are the `watch` waker leak in rusty_lsp's read loop and the `wait_for` poisoning.
