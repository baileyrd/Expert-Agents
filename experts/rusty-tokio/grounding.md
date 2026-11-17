# Review fixes under crates/libs/async/rusty_tokio

17 commits answer a review; 13 held-out commits removed.
Read each one's diff (`git show <sha>`) for the defect class it fixed.

| Commit | Subject | Files |
|---|---|---|
| cc98997854 | rusty_tokio: wake LocalRuntime::block_on when a local task is scheduled | `crates/libs/async/rusty_tokio/src/runtime/mod.rs`, `crates/libs/async/rusty_tokio/src/task/local.rs`, `crates/libs/async/rusty_tokio/tests/local_runtime.rs` |
| 2d5b7bd847 | fix(rusty_tokio): retry io_uring PollRemove on EALREADY | `crates/libs/async/rusty_tokio/src/io/reactor/io_uring.rs` |
| 2d54c9ae64 | fix(rusty_tokio): keep a never-polled task's drop inside its span | `crates/libs/async/rusty_tokio/src/task/local.rs`, `crates/libs/async/rusty_tokio/src/task/mod.rs`, `crates/libs/async/rusty_tokio/tests/tracing_instrumentation.rs` |
| 92295661bb | fix(rusty_tokio): exit task spans before completing the join | `crates/libs/async/rusty_tokio/src/runtime/context.rs`, `crates/libs/async/rusty_tokio/src/task/local.rs`, `crates/libs/async/rusty_tokio/src/task/mod.rs` |
| 7ba275a351 | fix(rusty_tokio): keep RwLock waiters queued in place until they acquire | `crates/libs/async/rusty_tokio/src/sync/rwlock.rs`, `crates/libs/async/rusty_tokio/tests/sync.rs` |
| e23a258c8a | test(net): run the h2 interop oracles on rusty_tokio; fix shutdown parity (#903) | `crates/libs/async/rusty_tokio/src/io/tcp.rs`, `crates/libs/async/rusty_tokio/tests/net.rs` |
| c77dd28efa | fix(rusty_tokio): serialize cancellation and recheck idle publication | `crates/libs/async/rusty_tokio/src/runtime/current_thread.rs`, `crates/libs/async/rusty_tokio/src/sync/cancellation_token.rs` |
| d8dfe49e62 | fix(rusty_tokio): preserve abort across poll state transition | `crates/libs/async/rusty_tokio/src/task/local.rs`, `crates/libs/async/rusty_tokio/src/task/mod.rs`, `crates/libs/async/rusty_tokio/src/task/state.rs` … |
| 75502cdfaf | fix(tokio): finish io-uring poll teardown before fd close | `crates/libs/async/rusty_tokio/src/io/reactor/io_uring.rs` |
| 23b28c0f90 | fix(rusty_tokio): drop mpsc wakers outside the channel lock | `crates/libs/async/rusty_tokio/src/sync/mpsc.rs`, `crates/libs/async/rusty_tokio/tests/mpsc_cancel.rs` |
| 7096ee85c9 | fix(rusty_tokio): cancelled mpsc senders no longer swallow wakes | `crates/libs/async/rusty_tokio/src/sync/mpsc.rs`, `crates/libs/async/rusty_tokio/tests/mpsc_cancel.rs` |
| 9199892113 | fix(rusty_tokio): drop undelivered values when an mpsc receiver drops | `crates/libs/async/rusty_tokio/src/sync/mpsc.rs`, `crates/libs/async/rusty_tokio/tests/sync.rs` |
| 4f92633bb6 | build: restore edges that exist on purpose; pin them with ignores and tests | `crates/libs/async/rusty_tokio/Cargo.toml` |
| 392a852a47 | build: remove 118 unused dependencies flagged by cargo shear | `crates/libs/async/rusty_tokio/Cargo.toml` |
| 7874688ec6 | fix: replace deprecated fetch_update with compare-and-swap loops | `crates/libs/async/rusty_tokio/src/io/reactor/mod.rs` |
| d3e530b7d1 | rusty_stream: reap finished connections; bounded graceful drain (review 3.7, N03) | `crates/libs/async/rusty_tokio/src/task/join_set.rs`, `crates/libs/async/rusty_tokio/tests/join_set.rs` |
| 072bc1ccf4 | CI: Windows full-workspace test shards — cmd.exe's 8,191-char PATH limit | `crates/libs/async/rusty_tokio/tests/process_windows.rs` |

Migration review fixes outside the crate (same filter, read by hand; none is in a
held-out PR): 6597f23b5 and 9a5683b05 (`select!` order at migrated call sites),
43ddc4400 and 7a3267f5a (`TcpListener::bind` became async), c59efa181 (a consumer kept
tokio's `Mutex` while rusty_tokio's lost a wake), 7b3487ece (tier C crates kept on
tokio), e23a258c8 (tokio parity found by running the h2 oracle on rusty_tokio).

## Defect classes

1. **Cancelled waiter leaves a trace.** A future dropped while queued must leave the
   queue, and a wake it already received must pass to the next waiter. Otherwise a
   later waiter is never woken, or registrations grow for a connection's lifetime.
   (7ba275a35 RwLock waiter stays queued; 7096ee85c cancelled mpsc senders swallow
   wakes)
2. **User code under an internal lock.** A waker can own the other end of the channel
   or pipe, and a value's `Drop` can touch the primitive. Waking or dropping either
   under the state lock re-enters it and deadlocks. Move them out, unlock, then wake or
   drop. (23b28c0f9 mpsc; afe319c77 DuplexStream; 919989211 buffered values dropped
   after unlock)
3. **Lost wakeup across a publication window.** A wake or completion published outside
   the lock that guards the state the sleeper checks is missed: block_on parks on the
   wrong condvar, an idle check races a blocking completion. (cc9899785 LocalRuntime
   park; c77dd28ef cancellation token and idle publication)
4. **Task state races.** Abort racing the queued-to-running transition, a never-polled
   task's drop, and join completion published before the span exits. (d8dfe49e6,
   2d54c9ae6, 92295661b)
5. **Reactor teardown against the kernel.** Deregistration must finish before the fd
   closes, must not wait forever on a completion the kernel will not post (EALREADY),
   and must not hold the driver lock while waiting; stale completions must not hit a
   reused fd. (75502cdfa, 2d5b7bd84)
6. **tokio parity.** Where rusty_tokio mirrors a tokio API, tokio's documented behaviour
   is the contract: `poll_shutdown` on a reset peer, buffered values dropped with the
   receiver, a channel that drains like tokio's. (e23a258c8, 919989211)
7. **Migration changes more than the runtime.** Fixed-order versus random `select!`
   polling, a sync constructor that became async, a primitive with weaker guarantees
   swapped in under a remote await, a tier C dependency moved onto a runtime it cannot
   use. (6597f23b5, 9a5683b05, 43ddc4400, 7a3267f5a, c59efa181, 7b3487ece)
8. **Windows environment.** Behaviour that only holds on one OS: cmd.exe reads only
   8,191 characters of PATH in a full-workspace run. (072bc1ccf)

## Oracles

- **tokio** at the version in `Cargo.lock`, cloned shallow into scratch:
  `tokio/src/sync/` (batch_semaphore.rs, notify.rs, watch.rs, mpsc/), `runtime/task/`
  (state.rs, harness.rs), `runtime/io/` (registration, scheduled_io.rs), and its
  loom tests under `tokio/src/*/tests/loom_*.rs`. MIT.
- **mio** for the reactor backends: `src/sys/unix/selector/{epoll,kqueue}.rs`,
  `src/sys/windows/` (AFD polling, `afd.rs`, `selector.rs`). MIT.
- **std::task** docs: the `Waker` and `RawWakerVTable` contracts (what clone, wake and
  drop may do), `Future::poll`, `Pin`.
- **Kernel and OS**: io_uring(7), `io_uring_enter(2)`, `IORING_OP_POLL_REMOVE` and
  `ASYNC_CANCEL` return codes; epoll(7) edge-trigger rules; Windows
  `GetQueuedCompletionStatusEx`, `CancelIoEx`.
- **Public trackers**: tokio, mio, async-std and smol issues for the same symptom.

## Settled decisions

- `docs/research/ASYNC-RUNTIME-INVENTORY.md`: tiers A/B/C, gaps G1-G13, the order in
  section 3, and the per-crate table. Tier C crates keep tokio (G9).
- `crates/libs/async/rusty_tokio/ARCHITECTURE.md` Boundaries and Non-goals: not a
  drop-in tokio replacement; no loom; io_uring is readiness-only and opt-in.
- `crates/libs/async/rusty_tokio/docs/decision-request-*.md` and `docs/adr/`.
- `install_fallback()`: an opt-in, process-wide runtime that lasts until exit (owner,
  2026-10-29); using it in a consumer needs its own go-ahead.
- `docs/research/RUSTY-MCP-SOVEREIGNTY.md` D4: a crate keeps tokio while its framework
  needs it.
