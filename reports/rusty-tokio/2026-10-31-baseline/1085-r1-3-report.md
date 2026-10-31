<!-- rusty-tokio/1085-r1 run 3, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 344 s, 2372917 tokens, $1.90 -->

# Review of PR #1085 (rusty_lsp moves onto rusty_tokio): changes required

- **Repository:** Rusty-Mill/rusty_mill, PR #1085
- **Base:** `0beaf37853b445cb8e2a09485029f0d982d53d77`
- **Reviewed head:** `349b521ab20b790caa53174a1b802057d30237ea`. `git fetch origin pull/1085/head` returned this SHA. It has two commits: `33a7f5ef0` and `349b521ab`.

The PR switches `rusty_lsp`'s `runtime` and `tcp` features from tokio to `rusty_tokio`. It also adds three things to `rusty_tokio`: `watch::Receiver::wait_for`, `AsyncBufRead` for `io::Take`, and `Debug` for `Notify`.

Several things check out:
- **Migration order:** #911 runs after #903, #897, #899 and #896, which matches the inventory.
- **Approved API break:** the public API change is recorded as owner-approved in the inventory and the change fragment. No workspace crate turns on rusty_lsp's runtime features, and `l13` uses data types only.
- **No tier C problem:** rusty_lsp has no tier C (tokio-bound) dependency.
- **`select!` order:** `rusty_tokio::select!` starts at a random branch, like tokio's, so arm order is unchanged.
- **stdio:** rusty_tokio's stdio uses the blocking pool, like tokio's.
- **`Take` buffered reads:** the new `AsyncBufRead for Take` matches tokio's implementation line for line.
- **`wait_for` cancel safety:** at every `Pending`, `f` has already rejected the value the receiver has seen.
- **`wait_for` closed-channel error:** it calls `f` on the last value before returning an error, as tokio's docs promise.

Two problems are new in rusty_lsp. Its tokio-based version did not have them; they come from weaker `rusty_tokio` primitives sitting under its awaits. A third comes from the new `wait_for` itself.

## Blocking findings

### 1. P1: rusty_lsp's message loop leaks one waker per inbound message for the whole session
**Where:**
- `crates/libs/async/rusty_tokio/src/sync/watch.rs:204-222`: `changed`, which `wait_for` also uses at line 197.
- `crates/libs/protocol/rusty_lsp/src/server.rs:436-449`.

**Trigger:** any long LSP session.

**How it happens (traced in the source, then run):**
1. `changed()` pushes a cloned waker into `Inner::wakers`.
2. Nothing removes that waker when the future is dropped. Only `send`, `send_modify` or `Sender::drop` empties the list.
3. `run_loop` builds a fresh `writer_dead.changed()` inside `select!` on every iteration.
4. The future gets polled when the read is `Pending` or when the random start picks that branch first. It registers a waker, then gets dropped when `read_message` wins.
5. `writer_dead_tx` only sends when a write fails (`server.rs:306-314`). So the list grows by one waker for each such message until the connection ends.
6. With tokio, the same code removed the waiter on drop.

**Run:**
- A hand-polled copy of the loop, using `rusty_tokio::select!` and a counting waker: after 10,000 messages, 10,002 waker clones were still held.
- Polling `rx.changed()` and `rx.wait_for(..)` once each and then dropping them, 1,000 times each: 2,002 references held.
- tokio 1.53.3 on the same sequence: 2, i.e. none leaked.

**Who is hurt:** every rusty_lsp server. Memory grows with every `didChange` or `completion` message, and each leaked waker also keeps the `serve` task's allocation alive.

**Fix:** make a dropped `changed()` remove its waker, the way the Mutex, Semaphore and mpsc fixes did (`b6a9c9f6a`, `7096ee85c`). One way is a keyed waiter slot that is removed in `Drop`, with the waker dropped outside the lock. While you are there, store a new waker when `!will_wake`: `registered` currently keeps the first waker forever, which is a lost wake if the future is re-polled with a different waker.

**Regression test:** `tests/sync.rs::watch_changed_and_wait_for_dropped_after_pending_release_their_waker` — poll once with an `Arc` counting waker, drop the future, and assert `strong_count == 2`. It fails at both base and head.

### 2. P1: a panicking `wait_for` predicate poisons the channel, and a later drop can abort the process
**Where:** `crates/libs/async/rusty_tokio/src/sync/watch.rs:191-192`.

**Trigger:** any `f` that panics.

**How it happens (traced in the source, then run):**
1. `f(&guard.value)` runs while holding the channel's std `Mutex` guard.
2. If `f` panics, the mutex is poisoned. From then on, every `.lock().unwrap()` panics: `send` (line 72), `Sender::drop` (line 117) and `Receiver::drop` (line 240).
3. In the probe, a `Receiver` dropped while unwinding panicked again, giving "panic in a destructor during cleanup": the process aborted with SIGABRT.

**tokio's contract:** tokio documents "# Panics — If and only if the closure `f` panics. In that case, no resource owned or shared by this Receiver will be poisoned" (tokio-1.53.3 `src/sync/watch.rs:865-868`). It enforces this with `catch_unwind`, dropping the lock and then calling `resume_unwind` (lines 910-924). The same sequence on tokio leaves `send` working.

**Who is hurt:** every other task holding the channel. Their sends and drops start panicking, and the process can abort.

**Fix:** wrap `f` in `catch_unwind(AssertUnwindSafe(..))`. On a panic, drop the guard and then call `resume_unwind`, as tokio does.

**Regression test:** `tests/sync.rs::watch_wait_for_panicking_predicate_does_not_poison` — after catching the panic, `tx.send(1)` must return `Ok` and dropping `tx` and `rx` must not panic.

### 3. P2: `CancelToken::cancelled()` leaves a waiter queued every time it loses a `select!`
**Where:**
- `crates/libs/async/rusty_tokio/src/sync/notify.rs:198` and `:236`: waiters are pushed, and `Notified` (line 241) and `OwnedNotified` (line 269) have no `Drop`.
- `crates/libs/protocol/rusty_lsp/src/cancel.rs:73-89`: its docs, at line 30, advertise `cancelled()` as "select!-friendly".

**Trigger:** a handler that loops over `select!` against `token.cancelled()`.

**How it happens (traced in the source, then run):**
1. Each `cancelled()` call enables and polls a `Notified`.
2. When another branch wins, the `Notified` is dropped, but its `Arc<WaiterState>` and waker stay in the token's queue.
3. They are released only by `cancel()` or when the token is dropped at the end of the request.

**Run:** 1,000 dropped `enable()`+`poll` futures left 1,000 wakers held. tokio held 0.

**Who is hurt:** long-running handlers, such as a streaming scan. Memory grows with each loop iteration until the request ends. The leak stops at the end of the request, which is why this is P2 rather than P1.

**Fix:** add a `Drop` for `Notified` and `OwnedNotified` that removes the state from `waiters`. If the waiter had already been picked by `notify_one`, `Drop` should pass that notification to the next waiter (or bank it as a permit).

**Regression test:** `tests/sync.rs::notified_dropped_after_pending_leaves_the_queue` — counting waker, assert the strong count returns to 2.

### 4. P2: the PR makes a comment in another crate false
**Where:** `crates/libs/ui/rusty_term/l13/Cargo.toml:17`.

The comment still says "rusty_lsp's tokio-backed `runtime` feature". After this PR it is backed by `rusty_tokio`.

**Fix:** reword it to say the feature is `rusty_tokio`-backed.

### 5. P2: new behaviour without a test
`wait_for` has no test for:
- the no-poison contract (finding 2),
- releasing its waker when cancelled (finding 1),
- cancel safety under `select!`.

The new tests in `tests/sync.rs` and `tests/chain_take.rs` only fail at base because the APIs did not exist yet, so they guard that the APIs exist and their happy paths. The tests named in findings 1 and 2 fill most of this gap. Add a cancel-safety test to `tests/sync.rs` as well.

## Pre-existing, out of scope (please file separately)
This one is not introduced or made reachable by this PR.
- **`Notify::notify_one` can be lost:** it can pop a waiter whose `Notified` was dropped and fire it, so the notification is swallowed (`notify.rs` `notify_one`/`notify_last` with no `Drop`). That is a lost wake for any `notify_one` user. rusty_lsp only calls `notify_waiters`, so it is not affected.

## Design questions
None.

## Optional improvements
- **Document the lock in `wait_for`.** Its doc should say that `f` runs under the channel lock and that the returned `Ref` blocks `send`/`borrow` while held. tokio documents both.
- **Move the fuzz crate off tokio.** `rusty_lsp/fuzz` still pulls in tokio just to host `block_on`. It could use a `rusty_tokio` current-thread runtime, and its `fuzz/Cargo.lock` would then need `rusty_tokio`. I did not build it, since it is detached from the workspace and I had no network.
- **Per-crate table.** The inventory's table row for `rusty_lsp` is unchanged. That is consistent with how other #911 crates are recorded, which is through status notes.

## Checks
| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | Could not be judged here. With all features on, every runtime uses the io_uring reactor, and `io_uring_setup` returns EPERM in this sandbox: 608 failures, all `Runtime::new` EPERM. |
| `cargo test -p rusty_tokio --features io-uring-reactor` | 4 lib failures, all io_uring EPERM. These files are not touched by the PR. |
| `cargo test -p rusty_tokio` (default reactor, `--no-fail-fast`) | 659 passed, 33 failed. All 33 are sandbox errors: Unix sockets EPERM and multicast ENODEV. `tests/sync.rs` passed 123/123 and `tests/chain_take.rs` 8/8, including all 6 new tests. |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Pass |
| `cargo test -p rusty_lsp --all-features` | Pass: 179 + 72 + 3 + 5 + 8 tests, 6 ignored |
| `cargo clippy -p rusty_lsp --all-targets --all-features -- -D warnings` | Pass |
| `cargo check --target x86_64-pc-windows-gnu`, for rusty_tokio and rusty_lsp | Did not run: the target is not installed |
| `cargo check -p l13` | Did not run: the cargo registry is read-only |
| `python3 .github/scripts/changes.py check` | Pass: 379 fragments ok |
| Probes in `$TMPDIR/rv/probe` (rusty_tokio at head vs tokio 1.53.3) | Findings 1–3 reproduced; tokio passes the same tests. |

## Validation gaps
- **io_uring and all-features runs:** need a Linux host where `io_uring_setup` is allowed.
- **Unix-socket and multicast tests:** need a run outside this sandbox.
- **Windows and macOS:** not compiled or run. The Windows `cargo check` targets above and the stdio and TCP paths through `Server::stdio` and `from_tcp` need a Windows CI run.
- **Fuzz crate:** not built (`cargo +nightly fuzz build` in `rusty_lsp/fuzz`).
- **CI:** I did not see `required-gate` results for this head.

## Verdict: **changes required**
Findings 1 and 2 are P1, and findings 3–5 are P2.
