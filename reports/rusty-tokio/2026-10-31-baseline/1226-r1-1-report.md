<!-- rusty-tokio/1226-r1 run 1, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 406 s, 2857856 tokens, $2.23 -->

# Review of PR 1226: rusty_tokio opt-in fallback runtime for foreign executors

- **Repository:** Rusty-Mill/rusty_mill, PR 1226
- **Base:** `31c15429f115c0d6a8943ef797a1f750f4786176`
- **Reviewed head:** `aba661f23e2bda22c2d2e9340700d7fa72557239`. I fetched `pull/1226/head` and it matched this SHA. The diff is one commit, `aba661f23`.

**Verdict: changes required.** The core change is sound, but one P1 leak (shown by running it) and four P2 findings need fixing.

## What the PR does

It adds `runtime::install_fallback()`. This starts one process-wide runtime (one worker, plus the reactor, timer and blocking-pool threads), keeps it alive with `mem::forget`, and stores its handle in a `OnceLock`. It also changes about 45 internal `Handle::current()` lookups to `Handle::current_or_fallback()`: I/O registration, `sleep`, `sleep_until`, `current_time`, `spawn`, `spawn_blocking`, `task::Builder::spawn`, the signal global and the process pipe adoption. `Handle::current` and `try_current`, `block_in_place`, and `pause`/`resume`/`advance` are unchanged.

## What holds up (each failure class checked)
- **Lookup and publication:** `current_or_fallback` (`context.rs:47`) takes no lock and only clones an `Arc`. `install_fallback` builds the runtime under `INIT`, then publishes it through `OnceLock`, so it can't build twice, and nothing reads `FALLBACK` before it is fully built.
- **Wakers and cancellation:** no primitive, waiter or task state changed. The reactor and timer have their own threads (`mod.rs:711-714`), so a tokio-polled future is woken by threads that never stop. `fire_due` wakes after releasing its lock (`time/mod.rs:213`).
- **Paused clocks:** `pause()` needs the current-thread flavour and the fallback is multi-thread, so its clock can't be paused. `Instant::now()` (real time off-runtime) therefore agrees with `current_time()` (the fallback's real clock).
- **Teardown:** the fallback never shuts down, which matches the settled "lasts until exit" decision. Objects registered with it still deregister when dropped.
- **Parity and migration:** `try_current` is unchanged, so `rusty_request`'s backend choice (`rt.rs:45-52`) is unaffected. No crate moved, and the PR doesn't turn the fallback on in any consumer, so it matches the settled decision.
- **tokio dev-dependency:** it is test-only (`features = ["rt"]`) and needs no other tokio feature, since the oracle's tokio runtime doesn't enable I/O or timers.
- **Windows:** the change to the Windows signal global follows the same pattern. It was not compiled here (see validation gaps).

## Blocking findings

### 1. P1: a `kill_on_drop` child dropped on a foreign thread is killed but never reaped (zombie leak)
- **Where:** `src/process/mod.rs:426` (`Child::drop`); its doc is at `process/mod.rs:185-189`.
- **Trigger:** the fallback is installed, and a child spawned with `kill_on_drop(true)` is dropped on a tokio thread.
- **Why:** the PR changed `PipeIo::adopt` (`:452`), so `Command::spawn` with piped stdio now works on a tokio thread. But `Child::drop` still reaps only `if let Some(handle) = Handle::try_current()`, so the fallback is never used for the reap.
- **Trace (executed, in a scratch crate outside the tree):**
  1. `install_fallback()`.
  2. On a tokio current-thread runtime: `Command::new("sleep").arg("30").stdout(piped()).kill_on_drop(true).spawn()`, then `drop(child)`.
  3. SIGKILL is sent and no reap is scheduled. `/proc/<pid>/stat` still shows state **`Z`** 2 s later.
  4. Control: the same sequence inside a rusty_tokio `Runtime::block_on` leaves no `/proc` entry, because the child was reaped.
- **Who is hurt:** a long-running tokio process that spawns and drops children through rusty_tokio, such as an MCP stdio gateway restarting servers, leaks one zombie per child. tokio's `kill_on_drop` reaps in the background, so this also breaks tokio parity on the exact path this PR enables.
- **Before this PR:** the zombie already happened when a child was dropped off-runtime, and the doc said so. A piped child couldn't even be created on a foreign thread then. The PR makes this the normal fallback path.
- **Fix:** add `pub(crate) fn try_current_or_fallback() -> Option<Handle>` (try `try_current`, then `fallback::get`) and use it in `Child::drop`. Update the `kill_on_drop` doc.
- **Regression test:** the probe above as a `#[cfg(unix)]` test in `tests/fallback.rs`. It is red at head.

### 2. P2: `install_fallback`'s `# Errors` contract is false
- **Where:** `src/runtime/fallback.rs:33-35` claims "Returns the error from building the runtime, e.g. when a thread can't be spawned. A later call tries again."
- **Trace (source-traced):** every thread the build spawns uses `.expect`:
  - reactor: `io/reactor/epoll.rs:71` (likewise `kqueue.rs:122`, `windows.rs:423`, `io_uring.rs:197`)
  - timer: `time/mod.rs:160`
  - worker: `runtime/worker.rs:116`
- **Result:** if a thread can't be spawned, `install_fallback` panics instead of returning `Err`. If the worker spawn fails, the reactor and timer threads already started stay leaked. Only a failure in `Reactor::new()` (`mod.rs:711`) actually reaches `Err`.
- **Fix:** either make the build return those errors, or correct the doc to say thread-spawn failure panics.
- **Test:** none practical for thread exhaustion; fix the doc.

### 3. P2: about 95 `# Panics` docs, plus `Handle::enter`'s doc and the README, are now false once the fallback is installed
- The PR updated `lib.rs:403/428` but left these saying "Panics if called outside a running `Runtime`":
  - `io/tcp.rs:77,128,291,314,341,1117,1134`
  - `io/udp.rs:71,518`
  - `io/unix_socket.rs:228-740` (13 sites)
  - `io/unix_datagram.rs:47,62,77`
  - `io/pipe.rs:145-473`
  - `io/async_fd.rs:91-249`
  - `io/lookup.rs:30`
  - `fs/mod.rs` (about 30 sites), `fs/read_dir.rs:91,171,184`
  - `process/mod.rs:208-211,237,249,335,366`
  - `signal.rs:414-872`
  - `task/builder.rs:50` and `task/join_set.rs:40` ("same as `crate::spawn`", which no longer panics)
- `context.rs:217-221` says building `sleep` outside `block_on` panics without `enter`.
- The README "Runtime" section (`README.md:90`) doesn't mention the new public `install_fallback`.
- **Fix:** add "…unless `runtime::install_fallback` has run" to each, and add a README bullet.

### 4. P2: test coverage of the changed behaviour is incomplete
- `tests/fallback_absent.rs:12` (`sleep_without_a_runtime_or_fallback_panics`) **passes at base**. The base `sleep` (`time/mod.rs:476` at base) calls `Handle::current()`, whose message already contains "no rusty_tokio runtime". It should expect `"install_fallback"`.
- These paths have no test that goes red without the change: `PipeIo::adopt` with process kill/reap, the signal global binding to the fallback (`signal.rs:317`), `task::Builder::spawn`, and UDP/Unix/pipe/`AsyncFd` registration.
- Only TCP, `sleep`, `timeout`, `spawn` and `spawn_blocking` are covered.
- **Fix:** add the missing tests to `tests/fallback.rs`. At minimum: process (finding 1), `signal::ctrl_c()`/`signal(...)` first used on a tokio thread, and the named `task::Builder::spawn`.

### 5. P2: the inventory note overstates the boundary change and leaves out the owner gate
- **Where:** `docs/research/ASYNC-RUNTIME-INVENTORY.md:187-191` says the fallback means "the boundary above no longer forces a library to wait for its consumers". It then lists dropping `rusty_request`'s `tokio` backend and `rusty-mcp-client`'s `tokio` feature as next steps "once their tokio consumers call it".
- **Settled decision:** "`install_fallback()` is an opt-in, process-wide runtime that lasts until exit, and turning it on in a consumer needs its own owner go-ahead."
- **Why it's wrong:** a library still waits for every tokio consumer to get that go-ahead and call `install_fallback()`. Dropping those backends before then makes tokio consumers that never installed it panic (G11 ordering).
- **Fix:** state the per-consumer go-ahead in the note.

## Design questions (for the owner, not findings)
- The fallback has one worker and the count can't be changed. CPU-heavy tasks spawned from tokio threads would all run one after another on it. Should `install_fallback` accept a `Builder`?
- `rusty_tokio::process::Child::id()` returns `u32` where tokio returns `Option<u32>`. The probe hit this. It existed before this PR and is outside the diff.

## Optional improvements
- `TimerDriver::update_waker` (`time/mod.rs:246`) and `cancel` (`:257`) drop the replaced or removed `Waker` while holding `inner`, because the `insert`/`remove` result is a temporary in the same statement. A waker whose drop touches the same timer would deadlock. This existed before the PR and was already reachable via `Handle::enter`, but foreign wakers are now the main use case. Fix: bind the result and drop it after the guard.
- `fallback.rs` tests named "…on_a_tokio_thread" build the `Sleep`/`JoinHandle` on the test thread before `block_on`. Building them inside the tokio future would test what the names say.
- `ARCHITECTURE.md`'s data-flow section (step 3) says workers block in `epoll_wait`, but the reactor has its own thread. This is old and outside the diff.

## Checks performed
| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | **Not valid in this sandbox.** 7 lib unit tests fail because `io_uring_setup` returns EPERM: `--all-features` turns on `io-uring-reactor`, so every `Runtime::build` fails here. The integration binaries weren't reached. |
| `cargo test -p rusty_tokio --no-fail-fast` (default/epoll) | 681 passed, 33 failed. All 33 are sandbox errors: 29 EPERM (Unix-socket bind), 3 ENODEV (multicast), 1 `SUN_LEN` (long `$TMPDIR` path). None are in code the diff touches. `tests/fallback.rs` 7/7 ok, `tests/fallback_absent.rs` 2/2 ok. |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Passed with no warnings. |
| `python3 .github/scripts/changes.py check` | Passed: "519 fragment(s) ok". |
| Scratch probe (`$TMPDIR/pr1226-probe.REYQ`) | Finding 1 shown by running it: zombie at head, reaped in the control. |
| `cargo check -p rusty_tokio --target x86_64-pc-windows-gnu --all-targets` | **Not run.** The target isn't installed, and installing it means writing to `~/.rustup`, outside the sandbox. |
| `cargo test -p rusty_tokio --features io-uring-reactor` | **Not run.** The sandbox denies io_uring (EPERM). |

## Validation gaps
- **Windows cross-check and execution:** the Windows signal global and the `UnixStream::connect` path through the fallback. Settle with the cargo check above plus `cargo test --test fallback` on Windows.
- **io_uring reactor:** settle with `cargo test -p rusty_tokio --features io-uring-reactor` on a Linux host that allows io_uring.
- **macOS/kqueue:** not run.
- **Full suite for the sandbox-failed tests:** re-run the EPERM/ENODEV tests and `--all-features` unsandboxed or in CI, and compare against base.

**Verdict: changes required.**
