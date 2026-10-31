<!-- rusty-tokio/1085-r2 run 4, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 286 s, 2235945 tokens, $1.62 -->

# Review of PR 1085 (Rusty-Mill/rusty_mill): changes required

Two blocking P1 findings. K1, K2 and K3 are fixed, but the K3 fix adds a new hang (F2). K4 is still open and the migrated LSP server now hits it.

- **Base:** `0beaf37853b445cb8e2a09485029f0d982d53d77`
- **Head:** `2a6e9dfda91768eb60bf11a3199b83a5000107ec`. The fetched `pull/1085/head` matches it, and 349b521a is one of its ancestors.
- **Scope:** rusty_lsp moves from tokio onto rusty_tokio. rusty_tokio gains `watch::Receiver::wait_for`, `AsyncBufRead` for `Take`, `Debug` for `Notify`, and a real `Stdout::poll_flush`.

## Prior findings at this head

| ID | Status | Evidence |
|---|---|---|
| K1: predicate panic poisons the channel | **Fixed** | `watch.rs:198-206` uses `catch_unwind`, drops the guard, then resumes the panic, as tokio does at its `watch.rs:~940`. The test `watch_wait_for_predicate_panic_does_not_poison_the_channel` aborts its test binary at 349b521a and passes at head. |
| K2: wait_for inspects a version twice | **Fixed** | `watch.rs:195` marks the version seen before calling the predicate. `watch_wait_for_inspects_each_version_once` fails at 349b521a (`left: 2, right: 1`) and passes at head. |
| K3: Stdout flush is a no-op | **Fixed, but adds F2** | `stdio_stdout_flush.rs` fails at 349b521a (5 s timeout) and passes at head. rusty_lsp's `tests/stdio.rs` passes. |
| K4: dropped `changed()` leaves its waker | **Still open, now reachable from rusty_lsp** | See F1. |

## Blocking findings

### F1 (P1): a dropped `changed()` or `wait_for()` leaves its waker registered, and rusty_lsp now leaks one per wait (K4)
- **Where:** `crates/libs/async/rusty_tokio/src/sync/watch.rs:218-236`. `wait_for` inherits it through `:211`. The rusty_lsp call site is `crates/libs/protocol/rusty_lsp/src/server.rs:436-450`.
- **What's wrong:** `changed` pushes `cx.waker()` into `Inner::wakers` the first time it returns `Pending`. Nothing removes it when the future is dropped. Only `send`, `send_modify` or dropping the `Sender` clears the list.
- **Failure scenario** (source-traced from the code, confirmed by an executed probe):
  1. Each `run_loop` pass builds a new `writer_dead.changed()` inside `select!`, which polls its branches in random order.
  2. While `read_message` is waiting on stdin, `changed()` is polled and pushes the run_loop task's waker.
  3. The next frame arrives, the read branch wins, and the `changed()` future is dropped with its waker still in the list.
  4. `writer_dead_tx` only sends when the writer fails, so the list never drains during a healthy session.
- **Who is hurt:** a long editor session grows by one waker for every read that had to wait (for example, each keystroke's `didChange`). If the writer later fails, `send(true)` wakes all of those stale wakers.
- **Why it's a parity break:** on tokio 1.53.3, `changed_impl` waits on `Notified`, which unlinks itself when dropped (`tokio/src/sync/watch.rs:983-1002`). So the leak is new to rusty_lsp in this PR.
- **Executed probe (hand-polled, counting waker):** 1,000 dropped `rx.changed()` futures leave the waker's `Arc::strong_count` at 1002 instead of 2. The same happens with 1,000 dropped `rx.wait_for(..)` futures.
- **Required correction:** give each waiter a removable slot in `Inner`, for example an id-keyed map or slab like `mutex.rs`'s place. Remove the slot when the future is dropped, and replace the stored waker when a later poll brings a different one (`will_wake`).
  - The current `registered` flag never updates the stored waker, so a re-poll with a different waker would lose the wake. That is pre-existing, but the same fix covers it.
- **Regression test:** add the probe to `tests/sync.rs` as `watch_dropped_changed_and_wait_for_leave_no_waker`. It fails at head.

### F2 (P1, introduced by this PR): `Stdout::flush` hangs forever after a write that was cancelled
- **Where:** `crates/libs/async/rusty_tokio/src/io/stdio.rs:146-158`, using `poll_locked` at `:225-252`.
- **What's wrong:** `poll_flush` now takes the process-wide `stdout_lock` through its own `flush` state. A write that was cancelled after taking the lock leaves its boxed future in `self.state`, still holding `_guard`. Only `poll_write` polls that future, so the lock is never released.
- **Failure scenario** (executed):
  1. `timeout(200ms, out.write_all(1 MiB))` runs while stdout's pipe is full. The write takes the lock, enters the blocking pool, and the timeout fires.
  2. The pipe then drains, so the blocking write finishes, but nothing polls its future, so the guard stays held.
  3. `out.flush()` waits on the lock forever. The probe gave up after 5 s.
  - The reverse order also hangs (a cancelled flush followed by a write); that one is source-traced only.
- **Who is hurt:** any rusty_tokio user that times out or `select!`s a stdout write and then calls `flush` or `shutdown`.
  - rusty_lsp's writer task never cancels `write_all`, so its own path is not affected.
  - At base, `flush` was a no-op and returned immediately: the same probe passes at base in 0.25 s and hangs at head.
- **Parity:** tokio's `Blocking::poll_flush` first drives any in-flight operation to completion and returns its error (`tokio-1.53.3/src/io/blocking.rs:146-176`). The doc comment at `stdio.rs:149-150` ("a flush never completes an unrelated in-flight write in its place") describes the opposite of tokio's contract.
- **Required correction:**
  - In `poll_flush`, drive a busy `self.state` to completion first and return its error, as tokio does. Only then start the flush. Alternatively, share one state between write and flush.
  - Fix the doc comment to match.
- **Regression test:** a new `tests/stdio_stdout_flush_after_cancel.rs`. It must be the only test in its file because it redirects fd 1. It passes at base and fails at head, as shown by the probe.

## Design questions
None.

## Optional improvements
- `stdio.rs:245-248`: the "unreachable" branch quietly returns `Ok(0)`. `unreachable!()` would show the assumption.
- `crates/libs/ui/rusty_term/l13/Cargo.toml:17` still calls rusty_lsp's runtime feature "tokio-backed". The diff doesn't touch this file, but its claim is now stale. Separately, the changelog's "no workspace crate uses the runtime features" holds, because the workspace entry sets `default-features = false`.
- The rusty_lsp row in the inventory's per-crate table (`ASYNC-RUNTIME-INVENTORY.md:223`) still lists its tokio usage. Only the status note in section 3 was updated.
- Pre-existing, not from this PR: a cancelled stdout write that holds the lock also blocks every other `Stdout` handle in the process.

## Other failure classes checked
- **Re-entrant waker:** `send` and `Drop` already wake outside the lock. The `wait_for` predicate runs under the channel lock, as tokio's does under its read lock.
- **Migration:** `select!` stays fair (random) by default, as tokio's is. rusty_lsp's only workspace dependent doesn't enable the runtime features. The new `Send` bounds and the public type changes are declared in the changelog and the change fragment as an owner-approved G11 break.
- **`Take` as `AsyncBufRead`:** matches tokio, and both new tests pass.

## Checks run
- **`cargo test -p rusty_tokio --all-features`: not passing here.** 122 passed, 610 failed. 614 panics are io_uring `Operation not permitted`: the sandbox blocks `io_uring_setup`, and `--all-features` enables the io_uring reactor. 7 more fail with `File exists` in `fs.rs`, only in this run. → validation gap.
- **`--features io-uring-reactor`:** not run separately, since it would hit the same EPERM. → gap.
- **`cargo test -p rusty_tokio` (default features, epoll):** 662 passed, 33 failed.
  - Every failure is a Unix-socket bind (EPERM) or IPv4/IPv6 multicast (ENODEV) in `unix*.rs`, `net*.rs`, `readiness.rs` or `try_read_write.rs`. The diff touches none of these.
  - All of the PR's new rusty_tokio tests pass.
- **`cargo test -p rusty_lsp --all-features`:** 269 passed, 0 failed.
- **`cargo clippy -D warnings`:** passes for rusty_tokio (all targets, all features) and for rusty_lsp.
- **`python3 .github/scripts/changes.py check`:** "379 fragment(s) ok".
- **`cargo check --target x86_64-pc-windows-gnu`:** not run. Installing the target failed because the rustup home is read-only. → gap.
- **Probes:** run in a scratch clone at head, base and 349b521a, with results as given above. The reviewed tree was not modified.

**Validation gaps:** io_uring runs, Unix-socket and multicast tests, Windows build and execution (including rusty_lsp's child-process stdio test), and macOS.

**Verdict: changes required** (F1 and F2).
