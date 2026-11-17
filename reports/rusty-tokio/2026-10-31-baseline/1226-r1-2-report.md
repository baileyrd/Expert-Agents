<!-- rusty-tokio/1226-r1 run 2, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 234 s, 1733797 tokens, $1.36 -->

I found one blocking defect: with the fallback installed, a `kill_on_drop` child dropped on a tokio thread is killed but never reaped, so it stays a zombie. I confirmed this with a test I ran. There are also two P2 findings, so the verdict is **changes required**.

**Pinned:** Rusty-Mill/rusty_mill, PR 1226, base `31c15429f115c0d6a8943ef797a1f750f4786176`, head `aba661f23e2bda22c2d2e9340700d7fa72557239`. The fetched `pull/1226/head` equals the head SHA, and there is one commit (`aba661f23`).

## What the PR touches, and the rule each part must follow

- **`install_fallback` / `fallback::get`** (`src/runtime/fallback.rs`): it builds the runtime once under `INIT`, publishes the handle through a `OnceLock`, and never drops the runtime (`mem::forget`). It holds no waker or caller value under `INIT`. If the build fails, nothing is published.
- **`Handle::current_or_fallback`** (`context.rs:47`): uses the thread's own runtime first, then the fallback. `Handle::current` and `Handle::try_current` are unchanged.
- **Reactor and timer:** in the multi-thread flavour both run on their own threads (`mod.rs:740-744`, `time/mod.rs:164`) and wake through plain `Waker`s, so a future polled by tokio gets woken.
- **I/O handles and `Sleep`:** each keeps the `Arc<Reactor>` or `Arc<TimerDriver>` it captured, so dropping one on a tokio thread deregisters from the correct driver (`time/mod.rs:473-479`).
- **Public API:** only `install_fallback` is added. No crate is migrated. tokio is added only as a dev-dependency (`Cargo.toml:216`).

## Blocking findings

### 1. P1: a `kill_on_drop` child is never reaped on the fallback path, leaving a zombie (executed)

- **Where:** `crates/libs/async/rusty_tokio/src/process/mod.rs:426`. `Child::drop` still uses `Handle::try_current()`, which returns `None` on a tokio thread, so the reaping `spawn_blocking` is skipped.
- **Why the PR makes this reachable:** the PR converted `PipeIo::adopt` (`process/mod.rs:452`) and `crate::spawn_blocking` to the fallback. So on a tokio thread, `Command::spawn` with piped stdio and `Child::wait` now work, where at base they panicked.
- **Trigger:** `install_fallback()` has run; a tokio task spawns a child with `kill_on_drop(true)`; the `Child` is dropped there. For example, a tokio `timeout` cancels `child.wait()`.
- **What happens:** `SIGKILL` is sent, but nobody calls `wait()`. Each dropped child keeps a PID-table slot until the process exits. A tokio service that cancels children will leak PIDs.
- **tokio parity:** tokio's `kill_on_drop` documents best-effort reaping by the runtime.
- **The fragment's claim:** it says `spawn_blocking` on a runtime-less thread now uses the fallback. That holds for the public function but not for this internal path.
- **How I ran it:** a probe in a scratch copy (`$TMPDIR/wt/.../tests/probe_zombie.rs`). On a tokio current-thread runtime with the fallback, spawn `sleep 30` with piped stdout and `kill_on_drop`, drop it, wait 500 ms, then read `/proc/<pid>/stat`.
  - Result: `fallback path left the child as Some('Z')`.
  - Control, same test inside a rusty_tokio `block_on`: the child is gone (`None`).
- **Fix:** use `Handle::try_current().or_else(fallback::get)` in `Child::drop`, for example through a `pub(crate) try_current_or_fallback()`. Then update the `kill_on_drop` doc (`process/mod.rs:186-189`) and the `Drop` doc (`:407-416`).
- **Regression test:** in `tests/fallback.rs` (`#[cfg(unix)]`), run the scenario above and assert the pid is reaped within a deadline. Use `waitpid(pid, WNOHANG)` returning `ECHILD`, or `/proc/<pid>` absent. It fails at head.

### 2. P2: the `# Panics` docs on converted paths are now false (source-traced)

- **Where:** every converted constructor still says "Panics if called outside a running [`crate::Runtime`]". Examples:
  - `io/tcp.rs`: `from_std` (~127, ~340), `connect_addr` (~313), `TcpSocket::listen` (~1117), `TcpSocket::connect` (~1133)
  - `io/udp.rs`: `from_std`
  - `io/unix_socket.rs` and `io/unix_datagram.rs`: all converted sites
  - `io/pipe.rs:275,452`
  - `io/async_fd.rs:91,108,122,136`
  - `process/mod.rs:206-211`: `Command::spawn`'s piped-stdio note
- **Why it's false:** with the fallback installed, these calls succeed. The PR updated this text only for `crate::spawn` and `crate::spawn_blocking` (`lib.rs:401-436`).
- **Who is hurt:** a caller reading the docs about rustdoc panic behaviour.
- **Fix:** add "…and [`runtime::install_fallback`] has not run". This is a docs-only change, so no test applies.

### 3. P2: changed behaviour with no test

`tests/fallback.rs` covers TCP, `sleep`, `timeout`, `spawn` and `spawn_blocking`. Nothing covers the fallback path for:
- UDP, Unix stream, listener and datagram, `UnixSocket`, pipes
- `AsyncFd`
- process (piped stdio, and the reap in finding 1)
- `signal::global` on Unix and Windows
- `task::Builder::spawn`
- `interval`, `interval_at` and `sleep_until`, which go through `current_time` and `Sleep::at`

Each would panic without the change. Add at least a `UnixStream::pair` round trip, a `Command` with piped stdout plus `wait`, `task::Builder::new().spawn`, and an `interval` tick, all to `tests/fallback.rs`, each driven by tokio's `block_on`. Every one would fail to compile or panic at base.

On whether the new tests guard anything at base:
- `tests/fallback.rs` does not compile at base (`install_fallback` is missing).
- `fallback_absent::spawn_without_a_runtime_or_fallback_panics` fails at base, because the old message lacks "install_fallback".
- `sleep_without_a_runtime_or_fallback_panics` passes at base. That is acceptable for a test that only checks the old panic still happens.

## Failure classes checked
- **Cancelled waiter:** no new waiter, queue or permit. `Sleep` and I/O drops use the driver they captured.
- **Re-entrant waker:** `INIT` only wraps `Builder::build`, and nothing the build starts calls back in. No waker is woken or dropped under `INIT`.
- **Publication window:** `get_or_init` runs under `INIT` after the build. Readers use `OnceLock::get`, which gives the correct ordering. A failed build publishes nothing and a later call retries, as documented.
- **Task state:** unchanged.
- **Reactor teardown:** the fallback is never torn down. Registrations keep their `Arc<Reactor>`. The fd and registration order is unchanged.
- **Parity:** without the fallback, behaviour matches base except the panic text. With it, the only deviation I found is finding 1.
- **Migration:** no crate moved and the inventory order is untouched. tokio stays a dev-only oracle.
- **Windows:** the Windows `signal::global` (`signal.rs:693`) and the Windows `UnixStream::connect` (`unix_socket.rs:662`) were converted the same way. I could not run the cross-check (see below).

## Design questions (for the owner, not findings)
- **Coop budget:** rusty_tokio resources polled on a tokio thread have no rusty_tokio budget in scope (README ~191 documents that this means unbudgeted), and they don't charge tokio's budget either. A read loop on a socket that is always ready could starve a tokio worker. Should the fallback's docs say so?
- **Inventory note:** the new paragraph (`docs/research/ASYNC-RUNTIME-INVENTORY.md:182-191`) lists dropping `rusty_request`'s tokio backend "once their tokio consumers call it" as next steps. Under the settled decision, turning the fallback on in a consumer needs its own owner go-ahead. The note should say that, so the plan isn't read as already approved.

## Optional improvements
- **Thread names:** `install_fallback`'s docs could say that blocking-pool threads also take the name `rusty-tokio-fallback`.
- **README:** the Runtime section doesn't mention `install_fallback`.
- **Pre-existing, not introduced here:** `TimerDriver::update_waker` and `cancel` (`time/mod.rs:246, 257`) drop the replaced waker while holding `inner`. With foreign wakers now the common case, a waker whose `Drop` touches the same timer would deadlock. Worth a follow-up issue.

## Checks run
- **`cargo test -p rusty_tokio --all-features`:** could not be validated here. 7 lib unit tests fail with EPERM from `io_uring_setup` (the sandbox blocks io_uring, and the feature routes every runtime through it). This covers `--features io-uring-reactor` too, which I did not run separately.
- **`cargo test -p rusty_tokio --no-fail-fast` (default features):**
  - `fallback` 7/7 and `fallback_absent` 2/2 pass, as do all other targets except below.
  - Failures are environmental: 3 multicast tests (ENODEV, `tests/net.rs`) and every Unix-socket test (EPERM on bind, in `unix`, `unix_datagram`, `unix_socket`, `net_parity`, `readiness`, `try_read_write`). These all fail before reaching the changed code paths.
- **`cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings`:** passed.
- **`python3 .github/scripts/changes.py check`:** passed, "519 fragment(s) ok".
- **`cargo check -p rusty_tokio --target x86_64-pc-windows-gnu --all-targets`:** did not run. The `x86_64-pc-windows-gnu` std is not installed, so dependencies failed to compile.
- **Probe:** `probe_zombie` in the scratch copy fails at head (finding 1). The control passes.

## Validation gaps
- **io_uring:** the whole suite under the io_uring reactor, and the Unix-socket and multicast tests. These need a host that allows io_uring and Unix-socket bind, or CI.
- **Windows:** the cross-check and Windows execution, especially the Windows `signal` global and `UnixStream::connect` under the fallback.
- **macOS:** kqueue behaviour was not executed.

## Verdict
**changes required**
