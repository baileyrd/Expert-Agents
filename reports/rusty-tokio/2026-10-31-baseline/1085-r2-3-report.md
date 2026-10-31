<!-- rusty-tokio/1085-r2 run 3, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 272 s, 1945897 tokens, $1.46 -->

I re-checked the four prior findings at this head. K1, K2 and K3 are fixed, and each fix has a test that fails before it and passes now. **K4 is not fixed.** It is still reachable on every message in rusty_lsp's read loop, and a related lost-wake bug in the same code is now part of a new public API. **Verdict: changes required.**

**Pinned:** Rusty-Mill/rusty_mill PR 1085. Base `0beaf37853b445cb8e2a09485029f0d982d53d77`, head `2a6e9dfda91768eb60bf11a3199b83a5000107ec`. I fetched `pull/1085/head` and it equals the head SHA. The PR has four commits: 33a7f5ef0 (the migration), 349b521ab (inventory note), a034f2120 (K1 fix) and 2a6e9dfda (K2 and K3 fixes).

## Prior findings at this head

| ID | Status | Evidence |
|---|---|---|
| K1: predicate panic poisons the channel lock | **Fixed.** `watch.rs:198-206` catches the panic, releases the lock, then re-raises it, as tokio's `wait_for` does. | `watch_wait_for_predicate_panic_does_not_poison_the_channel` aborts the process (SIGABRT, "panic in a destructor") at 349b521a and passes at head. |
| K2: a rejected version is checked twice | **Fixed.** `watch.rs:195` marks the version seen before the predicate runs, as tokio does. | `watch_wait_for_inspects_each_version_once` fails at 349b521a and passes at head. |
| K3: `Stdout` flush did nothing | **Fixed.** `stdio.rs:150-158` now flushes std's stdout buffer on the blocking pool, under the stdout lock, with its own state. A write that completes before `flush` starts is always included. | `tests/stdio_stdout_flush.rs` fails at base (times out after 5s) and passes at head. `rusty_lsp/tests/stdio.rs` passes. |
| K4: a dropped `changed()` wait leaves its waker registered | **Still open.** See F1. | Executed probe below. |

## Blocking findings

### F1 (P1): a dropped `changed()` or `wait_for` keeps its waker queued, and rusty_lsp drops one on every message (K4)
- **Where:** `crates/libs/async/rusty_tokio/src/sync/watch.rs:218-236`, which `wait_for` (`:211`) uses. It is reached from `crates/libs/protocol/rusty_lsp/src/server.rs:436-446`.
- **Trigger and trace (source-traced and executed):**
  1. `changed` pushes a clone of the waker into `Inner::wakers` (`:230`). Nothing removes it when the future is dropped; the vector is only emptied by `send`, `send_modify` or dropping the `Sender`.
  2. In `run_loop`, every loop turn polls `writer_dead.changed()` inside `select!`. The read arm wins for each incoming message, so that `changed` future is dropped.
  3. `writer_dead_tx` only sends when the writer fails, so the vector gains one waker per received message for the whole session. A long editing session sends a `didChange` per keystroke, so memory grows without bound.
  4. If the writer does fail, `send(true)` wakes every one of those stale wakers.
- **Executed probe** (hand-polled, counting waker, in a scratch crate): 10,000 polls of `changed()` to `Pending`, each then dropped, left **10,000** waker clones alive until the next `send`. 1,000 dropped `wait_for` futures left **1,000**.
- **Who is hurt:** every rusty_lsp server built with the `runtime` feature.
- **Parity break:** tokio 1.53.3 removes the waiter when it is dropped (`Notified` has a `Drop`, `notify.rs:1038`, used by `changed_impl` at `watch.rs:983-1002`). Its docs call `changed` and `wait_for` cancel safe (`watch.rs:789`, `:858`).
- **False claim:** the change fragment says `wait_for` was added "matching tokio". On cancel safety it does not.
- **Required correction:** give each wait a slot it owns. For example, keep a waiter list keyed by id or `Arc`, as rusty_tokio's `Notify` does, and remove the entry in a `Drop` on the `changed` future. Alternatively, build `changed` on a `Notify` whose `Notified` deregisters on drop.
- **Regression test** (`rusty_tokio/tests/sync.rs`): hand-poll `rx.changed()` to `Pending` 1,000 times with an `Arc<impl Wake>` waker, dropping it each time, then assert that `Arc::strong_count` is back to its starting value. Do the same for `wait_for`. Both fail at this head.

### F2 (P1): re-polling with a new waker keeps the old one, so the latest waker is never woken
- **Where:** `watch.rs:229-232`. Once `registered` is true, later polls never update the stored waker.
- **Trace (executed):** poll a `changed()` future with waker A and get `Pending`. Poll the same future again with waker B and get `Pending`. Then `send(1)`. Result: A is woken once and **B zero times**.
- **Impact:** this breaks the `Future` contract that the most recent waker is the one woken. The task that is actually waiting hangs.
- **Trigger:** the same pending future is polled under a different waker, for example after moving it to another task, or under a combinator that hands it a per-poll waker. This bug already existed in `changed` at base. The PR builds the new public `wait_for` on it, so it is now reachable through new API. rusty_lsp's own call sites keep a stable task waker and don't hit it. That trigger is narrow, but the impact is a lost wake, so it is graded P1.
- **Comparison:** tokio's `Notified::poll` replaces a stale waker, and rusty_tokio's own `Notify` does too (`notify.rs:202-206`).
- **Required correction:** on every `Pending`, store `cx.waker()` in the waiter's slot, replacing the old one unless `will_wake` says it's the same. The fix for F1 can cover this.
- **Regression test** (`tests/sync.rs`): the A-then-B sequence above, asserting B is woken exactly once.

## Design questions
None.

## Optional improvements
- **Fuzz target:** `rusty_lsp/fuzz/fuzz_targets/transport.rs` and the fuzz `Cargo.toml` still build a tokio runtime to drive what is now a rusty_tokio reader. I ran an equivalent of the fuzz body on tokio's current-thread runtime and it parses correctly, so nothing breaks. Moving it to `rusty_tokio::Runtime` would drop the leftover tokio dependency.
- **Stale comment:** `rusty_term/l13/Cargo.toml:17` still calls rusty_lsp's `runtime` feature "tokio-backed". Its point, that l13 doesn't link a runtime, is still true.
- **Outside this diff:** rusty_tokio's `Notified` has no `Drop`, so a dropped waiter stays queued and can absorb a `notify_one` meant for a live waiter. rusty_lsp only calls `notify_waiters`, so this PR doesn't make it reachable. It deserves its own issue.

## Other areas checked and ruled out
- **`select!` arm order:** rusty_tokio's `select!` is randomized like tokio's (README:693), so moved call sites behave the same.
- **`Take` as a buffered reader:** `poll_fill_buf` and `consume` match tokio's `Take`. The two new tests cover the limit and a zero limit.
- **`Debug` for `Notify`:** trivial.
- **New `Send` bounds and rusty_tokio types in public signatures:** this is the break the owner approved, recorded in the inventory (§3, line 151) and the change fragment. rusty_lsp is tier B and was moved in its slot in the #911 order. The only workspace consumer, l13, doesn't use the runtime feature.
- **Stdout cancellation and ordering:** a write and a flush each hold the stdout lock and have separate state. Dropping a flush mid-run abandons the blocking job, which is the same pre-existing pattern as writes.
- **Reactor teardown:** this PR touches no reactor code.

## Checks run
| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | **Blocked by the sandbox.** This feature set uses the io_uring reactor, and `io_uring_setup` returns EPERM here, so most runtime tests fail. No failure came from the PR's changes. |
| `cargo test -p rusty_tokio` (default epoll, `--no-fail-fast`) | 662 passed. 33 failed: Unix-socket tests got EPERM from the sandbox and multicast tests found no network device. None of those files is touched. All 9 new tests pass. |
| `cargo test -p rusty_tokio --features io-uring-reactor` | Blocked by the same EPERM. |
| `cargo test -p rusty_lsp --all-features` | Passes, including `tests/stdio.rs` and the integration suite. |
| `cargo clippy -D warnings`, rusty_tokio and rusty_lsp (all targets, all features) | Both pass. |
| `python3 .github/scripts/changes.py check` | Passes. |
| `cargo check -p rusty_tokio --target x86_64-pc-windows-gnu --all-targets` | **Not run:** the Windows standard library isn't installed here (E0463). |
| Base comparisons in scratch copies with separate target dirs | K3 test fails at base. K1 and K2 tests fail at 349b521a. |
| Executed probes | F1 and F2 as described above. |

## Validation gaps
- **io_uring:** the all-features and io-uring-reactor suites need a host that allows `io_uring_setup`.
- **Unix sockets and multicast:** these tests need a host without these sandbox limits.
- **Windows:** `cargo check` for the windows-gnu target, and `Stdout::flush` through a console or pipe on Windows. That needs CI or a Windows host.
- **macOS:** not exercised.

**Verdict: changes required** (F1 and F2).
