<!-- rusty-tokio/1226-r1 run 4, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 387 s, 4282276 tokens, $2.46 -->

# Review: Rusty-Mill/rusty_mill PR 1226, opt-in fallback runtime for rusty_tokio

The PR has four blocking findings: one P1 and three P2. Verdict: **changes required**.

- **Repository:** Rusty-Mill/rusty_mill
- **PR:** 1226
- **Base:** `31c15429f115c0d6a8943ef797a1f750f4786176`
- **Reviewed head:** `aba661f23e2bda22c2d2e9340700d7fa72557239` (fetched `pull/1226/head`; it matches the given head SHA)

**What changed:** the PR adds `runtime::install_fallback()`, which starts one process-wide multi-thread runtime (1 worker) and forgets its `Runtime` so it lasts until exit. It also adds `Handle::current_or_fallback()` and switches 37 lookup sites to it: I/O registration, `sleep`/`Sleep::at`/`current_time`, `spawn`, `spawn_blocking`, `task::Builder::spawn`, the signal global and the process `PipeIo`. The design itself is settled (opt-in, process-wide, lasts until exit). I found no defect in the fallback mechanism itself; the defects below are where the PR's code, docs and tests don't match what it claims.

## Blocking findings

### 1. P1: `kill_on_drop` children become zombies on a foreign thread once the fallback is installed
- **Where:** `crates/libs/async/rusty_tokio/src/process/mod.rs:426`. `Child::drop` still calls `Handle::try_current()`, the one lookup site the PR did not switch.
- **Trigger:** `install_fallback()` has run, then on a tokio thread: `Command::new(..).stdout(Stdio::piped()).kill_on_drop(true).spawn()`, and the `Child` is dropped before `wait()`. A dropped `timeout(.., child.wait_with_output())` is a common way to get there.
- **Trace (executed):**
  1. `spawn()` now works because `PipeIo::adopt` (line 452) falls back. At base the same call panicked (base probe: `panicked: true`).
  2. `drop` sends SIGKILL, but `Handle::try_current()` returns `None` on the tokio thread, so the reaping `spawn_blocking` is skipped.
  3. The child stays in state `Z` until the parent process exits.
- **Evidence:** a scratch probe saw `/proc/<pid>/stat` state `Some('Z')` in 3 of 3 runs. The control case, the same steps inside a rusty_tokio runtime, was reaped every time.
- **Who is hurt:** any tokio app using the fallback that times out child processes. It leaks a pid and process-table slot per dropped child.
- **Contradicts:** the change fragment says `spawn_blocking` "on a thread with no rusty_tokio runtime use[s] that runtime". tokio reaps killed children in the background on a best-effort basis.
- **Fix:** use `Handle::try_current().or_else(fallback::get)` in `Child::drop`, and update the `kill_on_drop` docs at `process/mod.rs:182-189` and `:402-416`.
- **Regression test** (`tests/fallback.rs`): on a tokio thread, spawn `sleep 30` with piped stdout and `kill_on_drop(true)`, drop it, then poll `/proc/<pid>` (Linux; elsewhere `waitpid(WNOHANG)`) until it is gone. It goes red at head.

### 2. P2: `install_fallback`'s `# Errors` doc is false for thread-spawn failures
- **Where:** `runtime/fallback.rs:429-431` says it "Returns the error from building the runtime, e.g. when a thread can't be spawned. A later call tries again."
- **What the code does (source-traced):** `build()` starts its threads with `.expect(..)`:
  - reactor at `io/reactor/epoll.rs:71`, and the same in kqueue and io_uring
  - timer at `time/mod.rs:160`
  - worker at `runtime/worker.rs:119`
- **Consequence:** a spawn failure panics inside `install_fallback` instead of returning `Err`. If the timer or worker spawn fails after the reactor thread started, that reactor thread is leaked, and every retry leaks another. Only the `Reactor::new()` syscalls (e.g. `epoll_create1`) return `Err`.
- **Fix:** either propagate the spawn errors in `Builder::build`, or rewrite the doc: errors only from reactor creation, and it panics if a runtime thread can't be spawned.
- **Test:** only needed if the code is changed. Put it in a subprocess with a lowered `RLIMIT_NPROC` and assert `Err` with no extra threads.

### 3. P2: docs disagree with the code and with settled decisions
- **(a) Stale `# Panics` docs.** About 90 rustdoc lines still say "Panics if called outside a running [`crate::Runtime`]" for calls that now fall back. Examples:
  - `io/tcp.rs` (7), `io/unix_socket.rs` (13), `io/pipe.rs` (11), `signal.rs` (8)
  - `process/mod.rs` (5), including `Command::spawn` at `:207-211`, which says piped stdio "needs the ambient reactor"
  - `fs/mod.rs` (30), `io/async_fd.rs` (4)

  Only `lib.rs` was updated. The README "Runtime" section (`README.md:90`) never mentions `install_fallback`.
- **(b) The inventory note contradicts settled decisions.** `docs/research/ASYNC-RUNTIME-INVENTORY.md:182-191` says:

  > "the boundary above no longer forces a library to wait for its consumers … The next steps are to drop `rusty_request`'s `tokio` backend and `rusty-mcp-client`'s `tokio` feature (#906), once their tokio consumers call it."

  Under the settled rule, turning on `install_fallback()` in a consumer needs its own owner go-ahead. So the boundary still holds a library back until each of its consumers gets that go-ahead. Dropping `rusty_request`'s tokio backend would also reverse the recorded result of `RUSTY-MCP-SOVEREIGNTY.md` item 3 ("No bridge: `rusty_request`'s additive `tokio` feature picks the runtime per call, so the fetch runs on the gateway's own `tokio` and no second runtime starts"). It would force the gateway, which stays on tokio under D4, to start a second runtime.
- **Fix:**
  - Change the per-item docs to "panics if there is no rusty_tokio runtime on this thread and `install_fallback` has not run", and add a README paragraph.
  - Reword the inventory: the fallback makes this possible for a consumer that gets owner go-ahead to enable it. Moving the next steps out of #906 is a question for the owner.

### 4. P2: several switched lookup sites have no test that fails if they revert
`tests/fallback.rs` covers `TcpListener::bind`, `TcpStream::connect`, `sleep`, `timeout`, `spawn` and `spawn_blocking`. Reverting any of these to `Handle::current()` would leave every test green (source-traced against the test file):
- `task::Builder::spawn` (`task/builder.rs:59`)
- `Sleep::at`, used by `sleep_until`, `timeout_at` and `Interval::poll_tick` (`time/mod.rs:415`)
- `current_time`, used by `interval` and `reset*` (`:330`)
- `UdpSocket`, `UnixStream`/`UnixListener`/`UnixDatagram`, `pipe`, `AsyncFd`
- process `PipeIo::adopt`, and the signal global

Add one tokio-thread case per distinct path to `tests/fallback.rs`: `Builder::new().name(..).spawn`, `sleep_until`, `interval().tick()`, `UdpSocket::bind` round-trip, `UnixStream::pair()` round-trip, and `Command` with piped stdout read to EOF.

## Design questions (for the owner)
- The fallback has a single worker. A CPU-heavy or blocking `rusty_tokio::spawn` from any tokio thread stalls every other fallback task process-wide. That matches the documented design, but consumers who opt in should know it.

## Optional improvements
- `tests/fallback_absent.rs:13` expects `"no rusty_tokio runtime"`, which the old `Handle::current` message (`context.rs:29`) already contains, so it passes at base and guards nothing. Expect `"install_fallback"`, as the `spawn` case does.
- **Pre-existing, outside this diff:** `io/reactor/mod.rs:328` calls `waker.wake()` inside `if let Some(waker) = waker_slot.lock().unwrap().take()`. Under edition 2021 the guard lives through the body, so a waker that polls inline and re-enters `poll_ready` on the same `ScheduledIo` would deadlock. `:263` and the timer's `update_waker`/`cancel` (`time/mod.rs:246,257`) drop old wakers under lock in the same way. Foreign executors are now the stated use case, so this is worth a separate ticket.

## Failure classes checked
- **Cancelled waiter, re-entrancy, publication window:** the diff adds no new waiters, locks or awaits. The fallback reuses the existing reactor, timer and task paths. `fire_due` wakes outside its lock. The coop budget defaults to unconstrained outside `Task::run` (`coop.rs:63`), so tokio-polled resources are never throttled.
- **Task state:** the forgotten `Runtime` keeps its workers, reactor, timer and blocking pool alive (`runtime/mod.rs:711-796`, `:1289`). `install_fallback`'s double-checked `OnceLock` plus mutex is race-free.
- **Reactor teardown:** never runs for the fallback (it lasts until exit, by decision). Objects keep the reactor `Arc` they registered with.
- **Parity:** `Handle::current`/`try_current` are unchanged, so `rusty_request/src/rt.rs:45` still picks its backend per call. The `kill_on_drop` parity break is finding 1.
- **Migration:** no crate moves in this PR. The tokio dev-dependency is exempt from `check_workspace_deps.py`'s pin list.
- **Windows:** the code paths are the same shape as on Unix, but nothing was executed (validation gap).

## Checks run
| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | Not usable here: the io_uring reactor gets EPERM from `io_uring_setup` in this sandbox, so all io_uring-backed tests fail. **Validation gap** |
| `cargo test -p rusty_tokio --features io-uring-reactor` | Same sandbox block. **Validation gap** |
| `cargo test -p rusty_tokio --features bytes,futures-io-compat,tracing,thread-per-core --no-fail-fast` (epoll) | `fallback` 7/7 and `fallback_absent` 2/2 pass. Failures are all sandbox errors: `AF_UNIX` bind EPERM (unix, unix_datagram, unix_socket, readiness, try_read_write, net_parity) and multicast ENODEV (net). `unix_datagram` and `net` fail the same way at base |
| `fallback` and `fallback_absent` binaries, 50 runs each | 0 failures in 100 runs |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Clean |
| `python3 .github/scripts/changes.py check` | "519 fragment(s) ok", exit 0 |
| `cargo check -p rusty_tokio --target x86_64-pc-windows-gnu --all-targets` | Not run: the target isn't installed and adding it needs network. **Validation gap** |
| Scratch probes (worktrees in `$TMPDIR`, since removed) | Zombie reproduced at head 3/3; base piped `spawn` without a runtime panics |

**Other validation gaps:**
- io_uring execution of the fallback reactor on a host that allows `io_uring_setup`.
- macOS/kqueue and Windows/IOCP-AFD execution of `tests/fallback.rs`.
- The required CI checks on this head (I did not see them).

## Verdict
**Changes required.**
