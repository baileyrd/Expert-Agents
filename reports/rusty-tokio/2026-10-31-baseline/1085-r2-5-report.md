<!-- rusty-tokio/1085-r2 run 5, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 278 s, 2046199 tokens, $1.51 -->

## Review of PR 1085 (Rusty-Mill/rusty_mill): verdict **changes required**

- **Base:** `0beaf37853b445cb8e2a09485029f0d982d53d77`
- **Head:** `2a6e9dfda91768eb60bf11a3199b83a5000107ec`. I fetched `pull/1085/head` and it matches this SHA and the checkout.
- **Commits:** 33a7f5ef0 (moves rusty_lsp onto rusty_tokio), 349b521ab (inventory note), a034f2120 (fixes K1), 2a6e9dfda (fixes K3 and K2).

K1, K2 and K3 are fixed. K4 is still open and blocks the merge: the rusty_lsp message loop added by this PR hits it on most messages. Running the code also showed a second problem in the same function: a lost wake.

### Prior findings at this head

| ID | Status | Evidence |
|---|---|---|
| K1: predicate panic poisons the lock | **Fixed** | `watch.rs:198-206` catches the panic, releases the lock, then re-raises it, as tokio does (`tokio-1.53.3/src/sync/watch.rs` `wait_for`). `watch_wait_for_predicate_panic_does_not_poison_the_channel` passes. |
| K2: a rejected version is inspected twice | **Fixed** | `watch.rs:195` marks the version seen before the predicate runs. `watch_wait_for_inspects_each_version_once` passes, and would fail on the old code: `changed()` would see version 1 ≠ 0 and run the predicate a second time. Tokio's cancel-safety promise for `wait_for` (watch.rs:858) still holds. |
| K3: `Stdout::poll_flush` does nothing | **Fixed** | `stdio.rs:150-158` now flushes std's stdout on the blocking pool, under the stdout lock, with its own state slot. `flush_delivers_bytes_after_the_last_newline` and rusty_lsp's `stdio_delivers_a_small_response_while_the_server_runs` pass. Both tests are Unix-only. |
| K4: a dropped `changed()` leaves its waker registered | **Still open, P1**, below | Reproduced by running it. |

### Blocking findings

**K4 (P1, leak). A dropped `changed()` or `wait_for` leaves its waker in the channel; rusty_lsp's message loop hits this on most messages.**
- **Code:** `crates/libs/async/rusty_tokio/src/sync/watch.rs:218-236`. The waker is pushed into `Inner.wakers` on the first `Pending` poll. Nothing removes it when the future is dropped; only `send`, `send_modify` or dropping the `Sender` empties the list.
- **Where it is hit:** `crates/libs/protocol/rusty_lsp/src/server.rs:436-450`, which uses `select! { read_message, writer_dead.changed(), shutdown }` inside `loop`.
- **Trace (source-traced, then run):**
  1. On each pass the fair `select!` polls `writer_dead.changed()`. It returns `Pending` and pushes a clone of the task's waker.
  2. `read_message` then completes, and the `changed()` future is dropped. Its waker stays in the list.
  3. `writer_dead_tx` only sends when a write fails, so on a healthy connection the list is never emptied.
  4. Result: one waker leaks per message, roughly, for the life of the connection. A language server getting a `didChange` on every keystroke grows without limit. When the writer finally fails, a single `send` fires every stored waker.
- **Second path:** request tasks aborted by `$/cancelRequest` while waiting in `wait_for_notifications` (`server.rs:655`) leave their wakers until the next `notes_done` send.
- **This is new for rusty_lsp:** on tokio, the same loop did not leak. Tokio's `changed_impl` waits on a `Notified` future, which removes itself from the waiter list when dropped (`tokio-1.53.3/src/sync/notify.rs:1038`).
- **Executed** with a scratch probe (`tests/probe_k4.rs`):
  - 1,000 `changed()` futures, each polled once with a counting waker and dropped: 1,000 wakers remained, and one `send` delivered 1,000 wakes.
  - A dropped `wait_for` kept 1 waker.
  - A 10,000-pass `select!` loop shaped like `run_loop`: the first `send` afterwards took about 1.2 ms, consistent with roughly 10,000 stored wakers.
  - The `changed()` probe also fails at base. The leak predates this PR; the PR makes it reachable in rusty_lsp and copies it into the new `wait_for`.
- **Required correction:** give each waiting future its own slot (a keyed waiter list, or a `Notify`-style waiter). Remove the slot in `Drop`, and drop the removed `Waker` only after releasing the channel lock, so a waker whose drop runs code can't re-enter the lock. The same rewrite should fix K5.
- **Regression tests (in `tests/sync.rs`):** `watch_dropped_changed_deregisters_its_waker` and `watch_dropped_wait_for_deregisters_its_waker`. Each polls once with an `Arc`-counting waker, drops the future, and asserts the waker count returns to its starting value and a `send` delivers 0 wakes. Both fail at this head.

**K5 (P1, lost wake; narrow trigger). The new `wait_for` wakes the first waker it saw, not the latest one.**
- **Code:** `watch.rs:229-232`. `if !registered` stores only the first poll's waker. `Future::poll` requires that the waker from the most recent poll is the one woken. Tokio's `Notified` replaces its stored waker on each poll.
- **Trace (executed, `tests/probe_waker_swap.rs`):** poll `wait_for` with waker A (`Pending`), poll again with waker B (`Pending`), then `send(1)`. Result: A is woken once, B zero times. Whoever is now driving the future through B hangs. `changed()` behaves the same way.
- **Trigger and scope:** the trigger is a future re-polled from a different task or combinator context. No call site in this PR does that, but `wait_for` is new public API that ships with the behaviour. The `changed()` half already exists at base.
- **Required correction:** on every `Pending` poll, replace the stored waker unless `will_wake` says it is the same one.
- **Regression tests:** `watch_changed_wakes_the_latest_waker` and `watch_wait_for_wakes_the_latest_waker` in `tests/sync.rs`. Both fail at this head.

### Other failure classes checked
- **Locks and published state:** the version, wakers and value share one lock, and wakers are woken after the lock is released (`watch.rs:79-83`). Nothing is lost there.
- **New `AsyncBufRead for Take`** (`async_io.rs:520-538`): it caps reads at the limit and subtracts what is consumed the same way tokio's `Take` does.
- **Stdout write then flush:** they run one after the other in the writer task and both take `stdout_lock`, so their order holds.
- **`select!` arm order:** rusty_tokio's `select!` starts at a random branch like tokio's (`rusty_tokio-macros/src/select.rs:54`), so arm order matches tokio.
- **Not applicable:** no reactor code, task-state code, or tier C dependency is touched (Cargo.lock only drops `tokio` from rusty_lsp).
- **Public API break:** it follows G11; the change fragment and section 3 of the inventory record the owner's approval.

### Design questions
None.

### Optional improvements
- The per-crate table row for rusty_lsp (`ASYNC-RUNTIME-INVENTORY.md:223`) still shows tokio. This matches how rusty_tonic's row was left, so it isn't a defect; the section 3 note covers it.
- `Stdout::poll_write`, which this PR doesn't change, returns the previous operation's byte count if it is re-polled with a different buffer after a cancel. Tokio's `Blocking` never does that. It could be a separate ticket.

### Checks (all run in a scratch clone at the head SHA)

| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | Couldn't produce a result: all 610 failures are the sandbox refusing io_uring (EPERM). |
| `cargo test -p rusty_tokio --features bytes,futures-io-compat,tracing,thread-per-core,io-uring-fs` (all features except `io-uring-reactor`) | 690 passed, 42 failed. All 42 are in files this PR doesn't touch: io_uring and unix sockets refused with EPERM, ENODEV for multicast, and a socket path too long (SUN_LEN) because of the scratch directory. Every new watch, Take, Notify and stdout test passed. |
| `cargo test -p rusty_tokio --features io-uring-reactor` | Not run; io_uring is blocked here. |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Pass |
| `cargo test -p rusty_lsp --all-features` | 269 passed, 0 failed, 6 ignored |
| `cargo clippy -p rusty_lsp --all-targets --all-features -- -D warnings` | Pass |
| `python3 .github/scripts/changes.py check` | Pass (379 fragments OK) |
| `cargo check -p rusty_tokio --target x86_64-pc-windows-gnu --all-targets` | Not run; the Windows target isn't installed. |

**Validation gaps:**
- Run the io_uring test suites on a Linux host that allows `io_uring_setup`.
- Run the Windows `cargo check`.
- Exercise stdout flush on Windows and macOS. Both new flush tests are Unix-only, so Windows console and pipe flushing has no test.
- Re-run the Unix-socket tests outside the sandbox.

### Verdict: **changes required**
The blocker is K4 (P1); K5 (P1) is fixed by the same `watch` rewrite.
