<!-- rusty-tokio/1085-r2 run 2, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 2099 s, 2717148 tokens, $1.81 -->

# Review of Rusty-Mill/rusty_mill PR #1085: rusty_lsp moved onto rusty_tokio (#911)

- **Base:** `0beaf37853b445cb8e2a09485029f0d982d53d77`
- **Head reviewed:** `2a6e9dfda91768eb60bf11a3199b83a5000107ec`. The fetched `pull/1085/head` matches it.
- **Since the last review (349b521a):** `a034f2120` fixes K1 and `2a6e9dfda` fixes K2 and K3. No commit addresses K4.

**Verdict: changes required.** K4 is still open and this PR makes it reachable, and the K3 fix introduced a new self-deadlock in `Stdout`.

## Prior findings at this head

| | Status | Evidence |
|---|---|---|
| **K1**: a panicking `wait_for` predicate poisons the watch lock | **Fixed** | `watch.rs:198-206` catches the panic, releases the lock, then re-raises it, as tokio does. `watch_wait_for_predicate_panic_does_not_poison_the_channel` aborts the test binary at 349b521a (double panic at `watch.rs:240`) and passes at head. |
| **K2**: `wait_for` inspects a rejected version twice | **Fixed** | `watch.rs:195` marks the version seen before the predicate runs. `watch_wait_for_inspects_each_version_once` fails at 349b521a (`left: 2, right: 1`) and passes at head. |
| **K3**: `Stdout::poll_flush` is a no-op | **Fixed, but the fix adds F2 below** | `stdio.rs:150-158` really flushes now. `stdio_stdout_flush` and rusty_lsp's `tests/stdio.rs` both fail at 349b521a (body never delivered) and pass at head. |
| **K4**: a dropped `changed()` leaves its waker registered | **Open, and now reachable** | See F1. |

## Blocking findings

### F1 (P1): a dropped `watch` `changed()`/`wait_for` leaves its waker queued, and rusty_lsp's read loop does this once per inbound message (K4)

**Where:**
- `crates/libs/async/rusty_tokio/src/sync/watch.rs:218-236`: `changed` pushes a waker at line 230. Nothing removes it on drop; only `send`, `send_modify` or dropping the `Sender` drains the list.
- `watch.rs:180-213`: `wait_for` is new in this PR and inherits the same leak through line 211.
- The trigger is in `crates/libs/protocol/rusty_lsp/src/server.rs:436-446`.

**Failure scenario** (source-traced in rusty_lsp, executed for the primitive):
1. Each loop iteration builds a fresh `writer_dead.changed()` inside `select!`, and it returns `Pending`, which pushes a clone of the loop task's waker into `wakers`.
2. `read_message` wins, `select!` drops the `changed()` future, and the waker stays in the list.
3. `writer_dead_tx.send` runs only when the writer fails (`server.rs:306,310,314`), so on a healthy connection the list is never drained.

The `Vec<Waker>` therefore grows by one entry for every LSP message for the life of the session. A long editing session sends a `didChange` per keystroke, so the memory grows without bound.

Request handlers aborted by `$/cancelRequest` also strand a `wait_for` waker on `notes_done` (`server.rs:650-656`). That one is bounded: the next notification's `send` drains it.

At base, rusty_lsp used `tokio::sync::watch`. Its `changed_impl` waits on `Notify::notified()`, and `Drop for Notified` removes the waiter (`tokio-1.53.3/src/sync/notify.rs:1038-1045, 1330-1350`). tokio also documents `changed()` and `wait_for()` as cancel safe (`watch.rs:789-794, 858-863`). So the migration breaks a tokio contract the read loop relies on.

**Executed:** I hand-polled each future once with a waker that counts its live clones, then dropped it.
- 1,000 dropped `changed()` futures left **1,000** wakers registered.
- 100 dropped `wait_for()` futures left **100**.
- The `changed()` probe fails the same way at base, so the bug predates this PR. Before this PR, nothing in rusty_lsp reached it.

**Required correction:** remove the registration on drop. Either give each waiter a slot or key that its future removes under the lock in `Drop`, or build `changed` on rusty_tokio's `Notify` with `enable()`, as tokio does.

Per this review's waker rule, drop the removed waker *after* releasing the lock. The `enable()` route must also keep the "register, then check the version" order that the module docs (lines 14-22) require.

**Regression test:** add to `crates/libs/async/rusty_tokio/tests/sync.rs` the counting-waker test I ran:
- poll `rx.changed()` once, drop it, repeat N times;
- assert no extra waker clones are alive;
- do the same for `wait_for`.

It goes red at head.

### F2 (P1): `Stdout` deadlocks itself when a write or flush is abandoned and the other op runs on the same handle (new in this PR)

**Where:** `crates/libs/async/rusty_tokio/src/io/stdio.rs:119-122` (two independent states), `150-158` and `227-252`.

**Failure scenario** (executed):
1. `poll_write` sets up an op in `self.state`. That op takes the process-wide `stdout_lock` (line 239), starts the blocking write and returns `Pending`.
2. The caller abandons the write, for example through `timeout(d, out.write_all(..))` or a losing `select!` arm. The boxed op now sits in `self.state` holding the lock guard, and it only makes progress when `poll_write` is called again.
3. The caller then calls `out.flush()`. That starts a separate op in `self.flush`, which waits on `stdout_lock` and never gets it.
4. The reverse order (abandon a flush, then write) hangs the same way.

Because the lock is process-wide, every other `Stdout` handle's writes hang behind it too.

**Executed:** both orders are hand-polled once and then abandoned.
- At head, both hit their 3-second timeout ("flush hung behind this handle's own abandoned write" / "write hung behind this handle's own abandoned flush").
- At base, both pass.

**Parity:** tokio's `Blocking` keeps one state for write and flush. `poll_flush` first drives any in-flight op to completion, then flushes, tracked by `need_flush` (`tokio-1.53.3/src/io/blocking.rs:146-176`).

rusty_lsp's writer task awaits `write_all` and then `flush` in sequence without cancelling, so `Server::stdio` doesn't hit this. Any rusty_tokio user who puts a timeout or `select!` around a stdout write and then flushes does.

**Required correction:** use a single state per handle for write and flush, as tokio does. At minimum:
- `poll_flush` must first drive a `Busy` `self.state` to completion;
- `poll_write` must first drive a `Busy` `self.flush`.

**Regression tests:** my two probes, each in its own file because the lock is process-wide (e.g. `tests/stdio_stdout_abandoned_write.rs` and `tests/stdio_stdout_abandoned_flush.rs`):
- `poll_write` once, check it returns `Pending`, then `timeout(3s, out.flush())` must succeed;
- `poll_flush` once, then `timeout(3s, out.write_all(..))` must succeed.

Both go red at head and pass at base.

### F3 (P2): the stdio state docs are now false

**Where:** `stdio.rs:51-56` says "One in-flight operation at a time per handle". `Stdout` now has two (`state` and `flush`), which is the root of F2.

**Required correction:** update the comment with the F2 fix. No test needed beyond F2's.

## Failure classes checked and ruled out

- **Publication window in `wait_for`:** the version is marked seen under the lock, then `changed` re-locks and compares. A `send` between the two makes the versions differ, so `changed` returns `Ready` and no wake is lost.
- **Closed-channel behaviour:** matches tokio's final inspection of a changed value.
- **`select!` parity:** rusty_tokio's `select!` polls from a random branch, like tokio's (`macros.rs:52-56`), so arm order at the moved call site is unchanged.
- **`cancel.rs`:** keeps the `notified()`, `enable()`, re-check pattern, which rusty_tokio's `Notify` supports.
- **`documents.rs`:** rusty_tokio's `RwLock` is write-preferring FIFO, like tokio's.
- **`Take` as `AsyncBufRead`:** matches tokio's clamp logic; `chain_take` passes.
- **Scope:** the public API break (rusty_tokio types, added `Send` bounds) is declared and owner-approved in the change fragment, and the inventory records it. rusty_lsp is tier B and has no tier C dependency.
- **Reactor teardown:** not applicable; no reactor code changed.

## Design questions

None.

## Optional improvements

- `Debug for Notify` prints `Notify { .. }`, while tokio's derived `Debug` shows fields. The fragment's "matching tokio" only holds in that both implement `Debug`.

## Checks

All runs used a `git archive` of each SHA in scratch.

| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | **Not valid here.** 610 failures, all EPERM from `io_uring_setup` (the sandbox blocks io_uring). |
| `cargo test -p rusty_tokio --features io-uring-reactor` | **Not valid here.** All 573 failures are the same EPERM. |
| `cargo test -p rusty_tokio` (default/epoll, substitute) | 662 passed, 33 failed. All 33 are in socket files the PR doesn't touch, failing on sandbox EPERM/ENODEV or a scratch path too long for a Unix socket. `sync` (125), `stdio*`, `chain_take` and `stdio_stdout_flush` all passed. |
| `cargo test -p rusty_lsp --all-features` | Passed (269 tests). |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Passed. |
| `cargo clippy -p rusty_lsp --all-targets --all-features -- -D warnings` | Passed. |
| `python3 .github/scripts/changes.py check` | Passed (379 fragments ok). |
| `cargo check -p rusty_tokio --target x86_64-pc-windows-gnu --all-targets` | **Not run**: the target's std isn't installed and can't be fetched offline. |
| Probes | F1 and F2 at head and base; K1/K2/K3 tests at 349b521a. Results are given above. |

**Validation gaps:**
- io_uring execution, on a host that permits `io_uring_setup`.
- The Windows check, after `rustup target add x86_64-pc-windows-gnu`.
- Windows and macOS execution of the new rusty_lsp `tests/stdio.rs` child-process test, and of `Stdout` flush.
- CI's `required-gate` result for this head.

I didn't modify the reviewed tree. The probes and builds are under `$TMPDIR/rv1085`.
