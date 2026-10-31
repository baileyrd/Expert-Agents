<!-- rusty-tokio/1226-r1 run 5, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 302 s, 2765681 tokens, $1.82 -->

# Review of PR 1226 (Rusty-Mill/rusty_mill): verdict is **changes required**

- **Base:** `31c15429f115c0d6a8943ef797a1f750f4786176`
- **Head reviewed:** `aba661f23e2bda22c2d2e9340700d7fa72557239`. I fetched `pull/1226/head` and it matches.
- **Diff:** one commit that adds `runtime::install_fallback()` and sends 33 ambient-runtime lookups through the new `Handle::current_or_fallback()`.

The core change works: TCP, sleep, timeout, interval and spawn all run on a tokio thread, and my probes found no lost or extra wakes. But one cleanup path was missed and leaves zombie processes, and three documents now say things that aren't true.

## Blocking findings

### 1. P1: a `kill_on_drop` child dropped on a tokio thread becomes a zombie (*executed*)
- **Where:** `crates/libs/async/rusty_tokio/src/process/mod.rs:426`. `Child::drop` still uses `Handle::try_current()`. It never checks the fallback, so on a tokio thread it skips the reap.
- **How it happens:**
  1. The program calls `install_fallback()`.
  2. Inside tokio's `block_on`, it calls `Command::new("sleep").kill_on_drop(true).stdout(Stdio::piped()).spawn()`. That works now, because `PipeIo::adopt` (line 452) uses the fallback.
  3. It drops the `Child`. SIGKILL is sent, but no reap is scheduled.
  4. `/proc/<pid>/stat` still shows `Z` after 2 seconds.
- **Control case:** the same steps on a rusty_tokio runtime leave no process behind.
- **Introduced by this PR:** at base, step 2 panics with "there is no rusty_tokio runtime…". I confirmed that with a probe.
- **Who is hurt:** any tokio app that uses the fallback and drives a migrated crate that spawns `kill_on_drop` children. The nexus LSP, DAP and ACP clients use that pattern. Each dropped child leaks one zombie for the life of the process.
- **Conflicting claims:** the change fragment says `spawn_blocking` "on a thread with no rusty_tokio runtime use[s] that runtime", and its list of exceptions doesn't include this. tokio reaps `kill_on_drop` children no matter which thread drops them.
- **Fix:** in `Child::drop`, use `Handle::try_current().or_else(fallback::get)`. Then update the docs at `process/mod.rs:186-189` and `:407-417`.
- **Regression test:** add to `tests/fallback.rs`. Spawn a piped `sleep 30` with `kill_on_drop(true)` on a tokio thread, drop it, and assert the pid is not in state `Z` within a bounded wait. I ran this exact test: it fails at head and passes once the drop happens on a rusty_tokio runtime.

### 2. P2: about 25 "Panics" doc sections on the changed constructors are now wrong (*source-traced*)
- **Where:** for example `src/io/tcp.rs:127`, `:311`, `:338`, `:1115`, `:1131`; `udp.rs`, `unix_datagram.rs:46/61/76`, `unix_socket.rs` (13 sites), `pipe.rs:275/452`, and `task/builder.rs`.
- **Problem:** they still say "Panics if called outside a running `crate::Runtime`". Since this PR, they panic only when there is no runtime *and* no fallback. Only `lib.rs`'s `spawn` and `spawn_blocking` docs were updated.
- **Fix:** reword each to "…and `runtime::install_fallback` has not run", matching `lib.rs:404-406`.
- **Test:** none needed, it's docs only. The behaviour is already covered by `tests/fallback.rs`.

### 3. P2: the inventory note contradicts the settled decision on consumer adoption (*source-traced*)
- **Where:** `docs/research/ASYNC-RUNTIME-INVENTORY.md:186-191`.
- **What it says:** "A tokio application that calls it at startup can depend on crates that moved… so the boundary above no longer forces a library to wait for its consumers. The next steps are to drop `rusty_request`'s `tokio` backend and `rusty-mcp-client`'s `tokio` feature (#906), once their tokio consumers call it."
- **The decision:** the fallback is opt-in, and turning it on in a consumer needs its own owner go-ahead.
- **Why that matters:** removing those tokio backends forces every tokio consumer to turn the fallback on. The note presents that as the default next step and says the boundary is lifted, without the per-consumer approval the decision requires.
- **Fix:** say the boundary is lifted only for consumers the owner has approved one by one, and make that approval a precondition of the #906 steps.

## Design questions (not defects)
- `Handle::current()` and `try_current()` deliberately ignore the fallback (stated in `fallback.rs:26`), and `block_in_place` still needs the thread's own runtime. A migrated crate that calls `rusty_tokio::runtime::Handle::current()` directly will still panic on a tokio thread even with the fallback on. `rk-kernel` is one candidate: the inventory lists a `Handle::current` call in it, so it would hit this if it moves. Is that the intended contract for migrations?
- Signal handling (`signal.rs:317`) attaches to whichever runtime first sets up the shared signal state. With the fallback, that can be the long-lived fallback or a short-lived runtime, depending on which thread gets there first. This predates the PR; the fallback just adds one more way to get there.

## Optional improvements
- Give `install_fallback` a section in `README.md` (runtime section and Testing), next to the tokio-interop decision request in `docs/decision-request-real-tokio-interop-bridge.md`.
- The new `sleep_without_a_runtime_or_fallback_panics` also passes at base, so it only protects behaviour that hasn't changed. That's fine, just noting it. The `spawn` version correctly fails at base.
- When the timer replaces or cancels a waker (`time/mod.rs:246`), it drops the old foreign waker while holding the timer lock. That's harmless for tokio's wakers but could be re-entrant with an unusual executor. It predates the PR.

## Failure classes I checked
- **Cancelled waiter:** I polled a `Sleep` by hand on a plain thread with a counting waker and dropped it after it returned `Pending`. No wake arrived afterwards, and a live sleep was woken exactly once (*executed*).
- **Re-entrant waker:** `fire_due` releases the lock before waking (`time/mod.rs:213`), so this is ruled out apart from the optional note above.
- **Lost wake on registration:** the timer adds the deadline under its lock and the timer thread waits on that same lock's condvar, so a wake can't slip between them.
- **Concurrent install:** 16 threads calling `install_fallback()` at once all got one runtime id (*executed*).
- **Task state and reactor teardown:** the PR doesn't change these. The fallback runtime is intentionally never torn down (`mem::forget`), and I/O objects deregister through the reactor they captured.
- **Migration:** the PR moves no crates. The only migration problem is finding 3.
- **nextest:** the fallback tests and the no-fallback tests are in separate binaries, so one can't leak the fallback into the other.

## Checks run
| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | Not usable here. The sandbox's seccomp returns EPERM for io_uring setup, so every reactor test fails at runtime. |
| `cargo test -p rusty_tokio --features bytes,futures-io-compat,tracing,thread-per-core --no-fail-fast` | 693 passed, 33 failed. The failures are EPERM on AF_UNIX sockets, ENODEV on multicast, and one SUN_LEN path. **The same tests fail at base**; the only new failure at base is the new `spawn_…panics` test. `fallback` (7/7) and `fallback_absent` (2/2) pass. |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Passed |
| `python3 .github/scripts/changes.py check` | Passed (519 fragments ok) |
| `cargo check … --target x86_64-pc-windows-gnu` | Not run: the Windows standard library isn't installed (E0463), and installing it needs network and a toolchain change. |
| Scratch probes (in throwaway worktrees, since removed) | The zombie probe failed as described in finding 1. The interval, concurrent-install and hand-polled-sleep probes passed. |

## Validation gaps
- **io_uring:** `cargo test -p rusty_tokio --features io-uring-reactor` (and `--all-features`) on a Linux host that permits io_uring.
- **Windows:** the cross-compile check, plus a real Windows run of `tests/fallback.rs` (IOCP/AFD reactor, the Windows `UnixStream::connect` path through `spawn_blocking`, Windows signal setup).
- **macOS:** a kqueue run of `tests/fallback.rs`.
- **Sandbox-blocked tests:** the AF_UNIX and multicast tests, run where those are allowed.

## Verdict: **changes required**
Fix finding 1 (P1) and findings 2–3 (P2).
