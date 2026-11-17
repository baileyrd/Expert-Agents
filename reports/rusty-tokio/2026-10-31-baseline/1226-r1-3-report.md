<!-- rusty-tokio/1226-r1 run 3, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 341 s, 2868035 tokens, $1.85 -->

# Review of Rusty-Mill/rusty_mill PR #1226: opt-in fallback runtime

- **Base:** `31c15429f115c0d6a8943ef797a1f750f4786176`
- **Head reviewed:** `aba661f23e2bda22c2d2e9340700d7fa72557239` (`git fetch origin pull/1226/head` returned this exact SHA)
- **Commit:** one, `aba661f23`, "rusty_tokio: opt-in fallback runtime for foreign executors"

**Verdict: changes required.** I found one P1 and three P2s. The core mechanism (reactor and timer on their own threads, waking through ordinary `Waker`s) holds up.

## What I checked each touched item against

- **`Handle::current_or_fallback`** (`context.rs:47-56`): it should use the thread's own runtime first, then the fallback, else panic. `Handle::current` and `try_current` must not change. Both hold.
- **`install_fallback`** (`fallback.rs:32-49`): it must build at most one runtime. It does: a second check under `INIT` stops racing callers from building two, and `get_or_init` publishes the handle. A failed build leaves `FALLBACK` empty, so the next call retries. `mem::forget` keeps the runtime alive until exit, as the decision requires.
- **Timers** (`time/mod.rs`): every `Pending` must leave a waker that something will wake.
  - `register` pushes the deadline and waker under `inner`, then calls `notify_one`, so a sooner deadline wakes the timer thread.
  - `fire_due` removes the wakers under the lock and wakes them after dropping it.
  - `Sleep::poll` checks `now()` before registering, so a stale `update_waker` after a firing is harmless.
  - The fallback is multi-thread, so it can never be paused. `clock_now()` (real time off-runtime) therefore agrees with the fallback timer's `now()`.
- **Reactor:** each I/O object keeps a clone of the reactor it registered on, so deregistration goes to the same reactor. The epoll, kqueue, Windows and io_uring backends each run their own thread (`epoll.rs:66`, `kqueue.rs:117`, `windows.rs:418`, `io_uring.rs:189`), so a tokio-polled future is woken without any rusty_tokio worker.
- **`spawn`, `spawn_blocking` and `task::Builder::spawn`:** spawning from a non-worker thread goes through the existing injection path that `Handle::spawn` already uses. `JoinInner::finish` (`join.rs:68-76`) wakes the join waker after releasing the lock.

## Blocking findings

### 1. P1: on a tokio thread with the fallback installed, `kill_on_drop` children become zombies
- **Where:** `src/process/mod.rs:426`
- **Trigger:** a tokio application calls `install_fallback()`, then drops a `rusty_tokio::process::Child` with `kill_on_drop(true)` on a tokio thread. A common case is `tokio::time::timeout(cmd.output())` that times out.
- **Trace (executed):**
  1. `Command::spawn` with piped stdio now works on the tokio thread, because `PipeIo::adopt` uses `current_or_fallback`.
  2. On drop, `Child::drop` sends SIGKILL.
  3. It then calls only `Handle::try_current()`, which returns `None` on a tokio thread, so the reap via `spawn_blocking` is skipped.
- **Probe:** I added `tests/probe_fallback.rs` in a scratch copy of the head, ran it, and read `/proc/<pid>/stat` 500 ms after the drop:
  - Inside a rusty_tokio runtime the child was reaped (`state None`).
  - On a tokio current-thread runtime with the fallback installed, it was left as a zombie (`state Some('Z')`).
- **Why it matters:**
  - At base, the piped/`output()` path panicked in `PipeIo::adopt` on a tokio thread, so this PR is what makes it reachable.
  - The change fragment says `spawn_blocking` "on a thread with no rusty_tokio runtime use[s] that runtime", but this internal `spawn_blocking` doesn't.
  - tokio's `kill_on_drop` reaps on a best-effort basis in the background, so tokio applications expect it.
  - Each dropped child leaks a pid-table entry for the life of the process.
- **Required correction:**
  - Add a non-panicking `pub(crate)` lookup, `Handle::try_current().or_else(fallback::get)`, and use it at `process/mod.rs:426`.
  - Update the drop docs at `process/mod.rs:186-189` and `:406-417` to say the reap also happens through the fallback.
- **Regression test:** in `tests/fallback.rs` (Unix only), spawn `sleep 30` with piped stdout and `kill_on_drop(true)` inside `on_tokio`, drop it, and assert `/proc/<pid>` is gone, or poll `waitpid(WNOHANG)` until a deadline. This is my probe, and it fails at the head.

### 2. P2: about 85 `# Panics` docs are now false once the fallback is installed
- **Examples:**
  - `src/io/tcp.rs:77, 128, 291, 314, 341, 1117, 1134`
  - `src/process/mod.rs:208-211` (`Command::spawn`: "adopting a piped fd needs the ambient reactor"), `:237`, `:249`
  - Every "Panics if called outside a running [`crate::Runtime`]" in `fs/mod.rs` (30), `pipe.rs` (11), `unix_socket.rs` (13), `signal.rs` (7), `udp.rs`, `unix_datagram.rs`, `async_fd.rs` and `fs/read_dir.rs`.
- **Trace (source-traced):** each of these now calls `current_or_fallback`, directly or through `crate::spawn_blocking`, so none of them panics once `install_fallback()` has run.
- **Why it's clearly unintended:** the PR updated exactly this sentence on `crate::spawn` and `crate::spawn_blocking` (`lib.rs:402-406`, `:427-429`), so the remaining docs now disagree with both the code and the crate's own updated wording.
- **Required correction:** add "…and [`runtime::install_fallback`] has not run" to each. This is a docs-only fix; no test needed.

### 3. P2: the inventory note conflicts with the settled rule on turning the fallback on in a consumer
- **Where:** `docs/research/ASYNC-RUNTIME-INVENTORY.md:186-191`
- **What it says:** "A tokio application that calls it at startup can depend on crates that moved to `rusty_tokio`, so the boundary above no longer forces a library to wait for its consumers… The next steps are to drop `rusty_request`'s `tokio` backend and `rusty-mcp-client`'s `tokio` feature (#906), once their tokio consumers call it."
- **The settled decision:** `install_fallback()` is opt-in, and "turning it on in a consumer needs its own owner go-ahead." That rule is in my review brief; I found no repository document that records it.
- **The conflict:** moving a library ahead of its tokio consumers forces each consumer to call `install_fallback()`. That is exactly the step that needs a separate go-ahead. Until each go-ahead is given, the boundary in the paragraph above (`:156-166`) still holds. The note presents those consumer calls as routine next steps.
- **Required correction:**
  - State that each consumer's call needs its own owner go-ahead.
  - State that the boundary still applies to any consumer without one.
  - Put dropping the #906 backends behind those approvals.
  - Ideally, record the decision in a repository document, since my brief is currently the only source I found for it.

### 4. P2: tests don't cover the changed paths that have their own logic
- **Gap:** `tests/fallback.rs` covers TCP, `sleep`, `timeout`, `spawn` and `spawn_blocking`. It doesn't cover:
  - **process:** `PipeIo::adopt` and the drop-time reap. A test here would have caught finding 1.
  - **`task::Builder::spawn`:** it takes a separate path through `spawn_named`, at `task/builder.rs:59`.
  - **`signal::global()`:** a process-wide `OnceLock` that now binds the signal pipe to the fallback reactor, at `signal.rs:317`.
  - **Racing first calls to `install_fallback`:** `installing_twice_returns_the_same_runtime` is sequential, so the `INIT` second check is never exercised.
- **Required correction:** add these to `tests/fallback.rs`. The race case needs N threads behind a `Barrier`, each calling `install_fallback()` and asserting one `id()`. Each test should fail if its call site is changed back to `Handle::current()`.

## Failure classes

- **Cancelled waiter:** I dropped `Sleep` after `Pending` (`cancel` removes the waker) and after it had fired (the entry was already removed, so nothing is left behind). Dropping a `JoinHandle` doesn't cancel the task, which matches tokio. The PR adds no new queues.
- **Re-entrant waker:** the PR adds no waker calls under a lock. There are existing ones, which the fallback now makes common with foreign wakers:
  - `TimerDriver::update_waker` and `cancel` (`time/mod.rs:246`, `:257`) drop the replaced or removed `Waker` while holding `inner`.
  - `JoinInner::poll` (`join.rs:90`) drops the old waker while holding `state`.

  I found no concrete re-entrant trace with tokio's waker: the last reference to a task's waker can't drop while that task is being polled. So I'm not counting these as findings, but they're listed under optional improvements.
- **Publication window:** timer registration, then notify, then a shutdown check is in order. Publication through `OnceLock` is release/acquire. No window found.
- **Task state:** the PR doesn't change the task state machine.
- **Reactor teardown:** the fallback is never shut down. A Handle's registrations keep their own `Arc<Reactor>`, and no fd/registration order changes.
- **Parity:** tokio has no equivalent API; outside a runtime it panics. Panic behaviour without the fallback is kept, and `fallback_absent.rs` covers it.
- **Migration:** no crate moves in this PR. `rusty_request`'s `rt::current()` (`crates/libs/net/rusty_request/src/rt.rs:44-53`) still picks tokio on a tokio thread, because `try_current` is unchanged, so the inventory's claim about it holds.
- **Windows:** the only Windows-specific change is the `signal.rs:693` swap, which has the same types as before. I couldn't run the cross-check (see below).

## Design questions for the owner
- **One worker for the whole process.** Every rusty_tokio `spawn` from tokio code runs on a single worker, so one blocking or CPU-heavy task stalls all the others. Should the worker count be configurable, for example `install_fallback_with(Builder)`?
- **`rusty_request`'s module docs** (`rt.rs:26-29`) say that with no runtime the caller gets rusty_tokio's "no runtime" diagnostic. With the fallback installed, that path now works instead.

## Optional improvements
- Move the replaced or removed wakers out of the lock in `TimerDriver::update_waker`/`cancel` and `JoinInner::poll`, now that foreign wakers are routine.
- Add the fallback to the README's runtime section and to ARCHITECTURE's data flow section; it's a new process-wide component.

## Checks run

| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | **Couldn't run here.** `--all-features` selects the io_uring reactor, and the sandbox returns EPERM for `io_uring_setup`, so 7 lib tests fail at setup. |
| `cargo test -p rusty_tokio --no-fail-fast --features bytes,futures-io-compat,tracing,thread-per-core` (epoll) | 64 binaries ok, including `fallback` 7/7 and `fallback_absent` 2/2. Failures in `net`, `net_parity`, `readiness`, `try_read_write`, `unix`, `unix_datagram` and `unix_socket` (multicast and AF_UNIX bind blocked by the sandbox) are **the same set as at base**: I ran the base from a `git archive` copy and diffed the failing tests. |
| New tests at base | `fallback.rs` doesn't compile at base (no `install_fallback`), so it fails there. `fallback_absent::spawn_…` fails at base; `fallback_absent::sleep_…` passes at base, which is fine for a no-regression guard. |
| Probe `tests/probe_fallback.rs` (scratch copy) | The own-runtime case passes; the fallback case leaves a zombie (finding 1). |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Passes. |
| `python3 .github/scripts/changes.py check` | 519 fragments ok. |
| `cargo test -p rusty_tokio --features io-uring-reactor` | **Gap:** io_uring is blocked by the sandbox (EPERM). |
| `cargo check --target x86_64-pc-windows-gnu --all-targets` | **Gap:** the target isn't installed, and `rustup target add` failed because the rustup home is read-only. |

**Validation gaps still open:**
- Real io_uring and `--all-features` test runs on a Linux host that allows `io_uring_setup`.
- The Windows cross-check, and real Windows execution of `tests/fallback.rs`.
- macOS/kqueue execution.
- The AF_UNIX and multicast suites, on a host that allows them.

I posted nothing and changed nothing in the reviewed tree. The scratch copies are in `$TMPDIR/base` and `$TMPDIR/headcopy`.

**Verdict: changes required.**
