<!-- rusty-tokio/1085-r1 run 1, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 331 s, 2644173 tokens, $1.93 -->

# Review: Rusty-Mill/rusty_mill PR #1085

- **Base:** `0beaf37853b445cb8e2a09485029f0d982d53d77`
- **Reviewed head:** `349b521ab20b790caa53174a1b802057d30237ea`. `git fetch origin pull/1085/head` returned this SHA, so the review is pinned.
- **Commits:** `33a7f5ef0` (rusty_lsp onto rusty_tokio; adds `watch::Receiver::wait_for`, `AsyncBufRead for Take`, `Debug for Notify`) and `349b521ab` (inventory note).

**Verdict: changes required.** There are three P1 findings and one P2, and I reproduced all three P1s by running probe tests. They are in the watch channel that rusty_lsp now depends on, and the migration is what puts them on rusty_lsp's normal message path.

## Invariants checked (step 1)

| Item | Invariant |
|---|---|
| `watch::changed()` (now a free fn, `watch.rs:204`) | A `Pending` result leaves a waker that `send` or `Sender::drop` will wake. Dropping the future leaves nothing registered. In tokio this holds because `Notified::drop` takes the waiter off the list (tokio `notify.rs:1330`). |
| `watch::Receiver::wait_for` (`watch.rs:180`) | It is cancel safe: when cancelled, the last value it marked seen is one `f` rejected. It returns `RecvError` only after `f` has rejected the last value. If `f` panics, nothing is poisoned (tokio `watch.rs:866-867, 910-922`). `f` must not run under a lock that other handle methods also take (tokio's `receiver_count` and `clone` use atomics, `watch.rs:1414, 202`). |
| `Take::poll_fill_buf`/`consume` | Never returns or consumes more than `limit`; returns `&[]` at a limit of 0. This matches tokio's `take.rs`. **Holds.** |
| `Debug for Notify` | Adds no locking. **Holds.** |
| rusty_lsp `run_loop` `select!` (`server.rs:436`) | rusty_tokio's `select!` uses a random start, the same as tokio (`macros.rs:51-54`), so arm order is unchanged. Each loop iteration must leave nothing registered once the arm that lost is dropped. |
| rusty_lsp public API (G11) | Only the runtime types change, plus the `Send` bounds the changelog lists. |

## Blocking findings

### 1. P1 – Every message rusty_lsp reads leaves a waker behind on `writer_dead`, and they are never freed for the life of the connection

- **Where:** `crates/libs/async/rusty_tokio/src/sync/watch.rs:204-222` (`changed` pushes at line 216 and has no `Drop` that removes the entry), reached from `crates/libs/protocol/rusty_lsp/src/server.rs:436-442`.
- **What happens:**
  1. Each `run_loop` iteration builds a new `writer_dead.changed()` inside `select!`.
  2. While the server waits for the client, `read_message` is `Pending`, so `select!` polls `changed()`. That pushes a clone of the task's waker into `Inner::wakers`.
  3. The client's message arrives, the read arm wins, and the `changed()` future is dropped. Its waker stays in the `Vec`.
  4. `wakers` is only drained by `send` or `Sender::drop`. Here that means only when the writer fails or the connection ends.
  5. Result: one stored waker per idle-then-message cycle, kept for the whole session. Each one also holds a reference to the run-loop task. When the connection ends, all of them are woken at once.
- **Before this PR:** with tokio's `watch` (built on `Notify`), dropping the future removed the waiter, so this did not happen.
- **Who is hurt:** every long-running LSP session, e.g. a `didChange` per keystroke. Memory grows with the number of messages. The same happens to any caller that puts the new `wait_for` in a `select!` loop, which is how tokio documents using it.
- **Evidence (executed):**
  - A hand-polled copy of the `run_loop` shape, with a counting waker and 500 messages, ended with **502 live waker clones**.
  - A direct test (poll `rx.changed()` once, drop it, repeat 1000 times) ended with **1000 wakers retained**.
- **Fix:** make `changed()` remove its registration when dropped, for example a waiter slab or list with a `Drop` guard like the one rusty_tokio's `Notify` uses, or build it on `Notify::notified()`. Replacing the stored waker at a matching slot is not enough on its own.
- **Regression test** for `crates/libs/async/rusty_tokio/tests/sync.rs`: poll `changed()` to `Pending` 1000 times with a counting `Arc<Wake>`, dropping the future each time, then assert the live clone count is 0. This test fails at both base and head (the primitive predates this PR), so it guards the fix rather than the migration.

### 2. P1 – A panicking `wait_for` predicate poisons the channel and aborts the process

- **Where:** `watch.rs:190-196`. `f(&guard.value)` runs while the channel's `std::sync::Mutex` is locked, with no `catch_unwind`.
- **What happens:**
  1. `f` panics while the guard is held, which poisons the mutex.
  2. Unwinding drops the async block's locals, including the `Receiver`.
  3. `Receiver::drop` (`watch.rs:239-241`) calls `lock().unwrap()`, which panics on the poisoned mutex.
  4. A panic during cleanup is fatal: the process aborts with SIGABRT.
  5. Even if something survives, every later `send`, `borrow` or `drop` on any handle panics.
- **tokio contract broken:** tokio documents "If and only if the closure `f` panics. In that case, no resource owned or shared by this `Receiver` will be poisoned." It achieves that with `catch_unwind`, releasing the lock before resuming the panic (tokio `watch.rs:910-922`). In tokio, the same code yields a `JoinError` and the process keeps running.
- **Evidence (executed):** a task spawned with `rusty_tokio::spawn` that runs `rx.wait_for(|_| panic!())` aborted the test binary with "panic in a destructor during cleanup" (SIGABRT). A second, `block_on` variant aborted the same way.
- **Fix:** wrap `f` in `catch_unwind(AssertUnwindSafe(..))`, drop the guard, then `resume_unwind`, as tokio does. Optionally also make `Drop` for `Sender`/`Receiver` tolerate a poisoned lock (`unwrap_or_else(PoisonError::into_inner)`).
- **Regression test** for `tests/sync.rs`: spawn a task whose `wait_for` predicate panics. Assert that the `JoinHandle` returns `Err(is_panic)` and that `tx.send(1)` afterwards returns `Err(SendError)` or `Ok` without panicking.

### 3. P1 – A `wait_for` predicate that calls another method on the same channel deadlocks

- **Where:** `watch.rs:190-192`. The predicate runs under the one channel mutex that `Sender::receiver_count` (`:110`), `Receiver::clone` (`:225`), `borrow` and `send` all lock.
- **What happens:** a predicate such as `|v| *v >= tx.receiver_count()` locks the mutex again on the same thread. `std::sync::Mutex` deadlocks (or panics) when re-locked by the thread that holds it. In tokio, `receiver_count` is an atomic load (`watch.rs:1414`) and `Receiver::clone` takes no value lock (`watch.rs:1005`), so the same code completes.
- **Evidence (executed):** that exact predicate on a rusty_tokio runtime **did not complete within 3 seconds**; the worker thread was deadlocked.
- **Fix:** at minimum, move `receivers_alive` and `sender_alive` into atomics so the count and clone methods don't take the lock. Better, evaluate `f` on a value read under a lock that handle-bookkeeping methods never take (tokio splits a value `RwLock` from atomic state). If some re-entrancy is kept by design, document it on `wait_for`.
- **Regression test** for `tests/sync.rs`: run the predicate above on a separate thread and assert it finishes within a short timeout.

### 4. P2 – A comment in another crate is now false

- **Where:** `crates/libs/ui/rusty_term/l13/Cargo.toml:17`. It still says "rusty_lsp's tokio-backed `runtime` feature". After this PR that feature is backed by rusty_tokio.
- **Fix:** reword it to "rusty_lsp's `runtime` feature (rusty_tokio)". No test is needed.

## Failure classes ruled out (step 2)

- **Cancelled `wait_for` waiter:** cancel safe. When it is `Pending`, `seen_version` equals the version `f` last rejected; a later send would have made `changed()` return `Ready`. Apart from finding 1, a cancellation leaves no queue slot behind.
- **Publication window:** the version check and the waker push happen under one lock; `send` drains the wakers under that lock and wakes them after releasing it. Nothing is lost.
- **Waker woken under the lock:** `send`, `send_modify` and `Sender::drop` all wake after `drop(guard)`. This is fine.
- **Task state and reactor teardown:** not touched by this diff.
- **Migration:**
  - `select!` arm order: fair in both runtimes.
  - No constructor became async, and there is no tier C dependency.
  - Workspace dependents (`l13`, and `nexus-vt` in comments only) use `default-features = false`, which the workspace entry at `Cargo.toml:486` sets, so they are unaffected.
  - The fuzz `transport` target still drives `read_message` from a tokio `block_on`. I ran that path from a runtime-less executor and it passes (executed). I could not build the fuzz crate itself offline because `libfuzzer-sys` is not cached.
  - `integration.rs` changes are only renames and reformatting.
- **Order:** #911's prerequisites (#903, #897, #899, #896) are recorded as done or closed (G4 closed in #997). The G11 API change is declared in the fragment, the CHANGELOG and the inventory.

## Design questions

- **New `Send` bounds on `transport::{read_message, write_message}`, `Server::new` and `run_loop`.** I removed them in a scratch copy and the build failed: rusty_tokio's `AsyncReadExt::read_exact` and `read_line` require `Self: Send`, which tokio's do not. The bounds are therefore forced by rusty_tokio, not chosen by this PR. The fragment and CHANGELOG disclose them and say the owner approved. The owner should still decide whether rusty_tokio's extension traits should drop that bound before more G11 crates inherit it.
- **The fragment says `Debug for Notify` matches tokio.** It implements the trait, but the output is `Notify { .. }`, not tokio's derived fields. Only an exact-output caller would notice.

## Optional improvements

- `send_modify`'s closure also runs under the lock with no `catch_unwind`, so it can poison the channel the same way as finding 2. This predates the PR; tokio guards it.
- The inventory table row for `rusty_lsp` (`ASYNC-RUNTIME-INVENTORY.md:223`) still lists it as on `t` (tokio). Only the status note was updated.
- `watch_wait_for_skips_values_until_one_matches` paces itself with `sleep(10ms)`. A hand-polled version with a counting waker would test the same thing deterministically.

## Tests for the changed behaviour (step 4)

- The new `wait_for`, `Take` and `Debug` tests fail at base because those APIs don't exist there, so they do guard the additions.
- Missing coverage for `wait_for`: cancellation, a panicking predicate, a re-entrant predicate, and dropped-future cleanup. These are the regression tests listed under findings 1-3.

## Checks run

All checks ran in a scratch export of the head, with a scratch target directory, offline.

| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | **Not usable here.** With all features, every runtime uses io_uring, and the sandbox denies io_uring (EPERM). 602 tests failed for that reason, including 4 lib tests. |
| `cargo test -p rusty_tokio --features io-uring-reactor` | Same EPERM failures in the 4 io_uring and current_thread lib tests. |
| `cargo test -p rusty_tokio` (default/epoll, `--no-fail-fast --tests`) | 648 passed, 33 failed. The failures are in `net`, `net_parity`, `readiness`, `try_read_write` and the `unix*` targets (multicast and unix sockets the sandbox blocks), none of which this diff touches. **`sync.rs` and `chain_take.rs` passed in full, including all 6 new tests.** |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Passed |
| `cargo test -p rusty_lsp --all-features` | Passed (179 + 72 + 8 + 5 + 3; 6 ignored) |
| `cargo clippy -p rusty_lsp --all-targets --all-features -- -D warnings` | Passed |
| `cargo check -p rusty_lsp --no-default-features` | Passed |
| `python3 .github/scripts/changes.py check` | Passed (379 fragments ok) |
| `cargo check --target x86_64-pc-windows-gnu` (rusty_tokio and rusty_lsp) | **Not run.** The target's std is not installed (E0463). |
| Probe tests (scratch only, nothing written to the reviewed tree) | Waker leak reproduced (1000 and 502 retained); predicate panic aborted the process; re-entrant predicate deadlocked; runtime-less transport read passed. |

## Validation gaps

1. **io_uring and full-feature suite:** needs a host that permits io_uring. Run `cargo test -p rusty_tokio --all-features` and `--features io-uring-reactor` there.
2. **Socket and unix tests:** run without the sandbox to confirm the 33 epoll-mode failures are environmental only.
3. **Windows:** run `cargo check --target x86_64-pc-windows-gnu --all-targets` with the target installed, then run rusty_lsp's `Server::stdio` and `from_tcp` on Windows. rusty_tokio's stdin handling and named pipes have not been run there.
4. **macOS:** not exercised.
5. **Fuzz crate:** `cargo fuzz build transport` needs network access to fetch `libfuzzer-sys`.

**Verdict: changes required.**
