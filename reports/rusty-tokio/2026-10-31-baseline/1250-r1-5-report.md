<!-- rusty-tokio/1250-r1 run 5, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 368 s, 1157207 tokens, $1.22 -->

# Review of PR 1250 (Rusty-Mill/rusty_mill): `Notify` drop fix

- **Base:** `d05170b8cc082cffaf6c348411e82bea84b9632d`
- **Reviewed head:** `98621f936d802e1fb05912e47f1b569eebe230c9`. The fetched `pull/1250/head` matches it.
- **Scope:** one commit. It changes `crates/libs/async/rusty_tokio/src/sync/notify.rs` and adds `changes/2026-10-30-rusty-tokio-notify-drop.md`. It is not a migration.

**Verdict: changes required.** The fix still loses `notify_one` wakeups when a drop races the notify on another thread. I reproduced this on the unmodified head code. Two smaller differences from tokio contradict the PR's "as tokio does" claim.

## Blocking findings

### 1. P1: a drop racing `notify_one`/`notify_last` still loses the wakeup
**Where:** `notify.rs:100-108`, `:114-122` and `:145-147` (`fire`), with `:282-285` (`release`).

**Trigger:** a `Notified` dropped on one thread while another thread calls `notify_one`. This is exactly the `select!`-in-a-loop case the fragment cites (#1247) on a multi-thread runtime.

**Failure scenario** (*source-traced and executed*):
1. Waiters A and B are queued.
2. Thread X calls `notify_one`. It pops A under `inner`, then calls `drop(guard)` (`:103`). It has not yet reached `fire`, which stores `single = true` (`:146`).
3. Thread Y drops A's future. `release` takes `inner`, finds A not queued, reads `single == false` and returns (`:283-284`).
4. X then runs `fire` on A, a dead waiter. B stays Pending, and the one wakeup is gone.

The same window exists on the hand-on path (`:286-289`). tokio avoids it by storing `Notification::One(strategy)` while it holds the waiters lock (`tokio-1.53.3/src/sync/notify.rs:879-881`). Its `drop_notified` reads that value under the same lock (`:1340-1343`). The fragment's claim that Notify "no longer loses a notify_one" is therefore false.

**Evidence I ran:**

| Probe | Code | Result |
|---|---|---|
| Probe A, deterministic | Head, plus only a 100 ms pause between `drop(guard)` and `fire` in `notify_one` | **Fails:** B is Pending |
| Probe B, stress (A drops while B waits, 1M iterations per run) | Unmodified head, `--release` | **Fails** every run: 99, 1,354 and 2,673 of 1,000,000 wakeups lost |
| Probe B, with the correction below | Head plus the fix | **Passes:** 0 lost over 3 runs (3M iterations). The PR's 4 unit tests still pass |

**Required correction:**
- Set `single` (or a strategy enum, see finding 3) on the popped waiter **before** `drop(guard)` in `notify_one`, in `notify_last`, and in `release`'s hand-on branch.
- Stop `fire` from writing it.
- The `Relaxed` ordering is then justified by the lock, as in tokio.

**Regression test:** add the Probe B stress test (two persistent threads behind a barrier, `notify_one` against `drop(a)`, then assert B is Ready) to `crates/libs/async/rusty_tokio/tests/sync.rs`. It goes red at head in about 3 s. A `cfg(test)` pause hook in `notify_one` would make it deterministic, as Probe A was. No current test covers the multi-threaded window.

### 2. P2: a permit taken by `enable` is given back on drop, unlike tokio
**Where:** `notify.rs:257-263`, the test at `:411`, the module doc at `:31-33`, and the fragment's "Fixed" bullet.

**Trigger:** `notify_one()` with no waiters, then `notified().enable()` returns true, then the future is dropped unresolved.

**Difference** (*executed*, Probe C on the same call sequence): a fresh `notified()` is **Pending on tokio** but **Ready on rusty_tokio**. tokio moves to `State::Done` when it takes the permit (`notify.rs:1133-1137`), and `drop_notified` only acts in `Waiting` (`:1338`). So tokio consumes the permit. The PR's docs and fragment say the hand-back happens "as tokio does", and `a_permit_taken_by_enable_and_dropped_is_banked_again` locks in the opposite behaviour.

Who is hurt: code written against tokio's contract can see an extra wakeup. The only in-tree `enable` caller, `rusty_lsp/src/cancel.rs:82`, uses `notify_waiters` only, so it is not affected today.

**Required correction:** either:
- treat a permit taken in `enable` as consumed (do not set `registered` or `single` there) and flip the test to expect Pending; or
- keep the behaviour, but remove the tokio claim from the docs and fragment, and get the owner's go-ahead for a deliberate difference.

### 3. P2: a dropped `notify_last` wakeup goes to the oldest waiter, not the newest
**Where:** `notify.rs:286` (`release` always uses `pop_front`), and `single: AtomicBool` at `:51`, which records no direction.

**Trigger:** A, B and C are waiting. `notify_last()` fires C, and C is dropped unresolved.

**Difference** (*executed*, Probe C): tokio wakes **B**, because `drop_notified` hands on with the same `NotifyOneStrategy`, i.e. LIFO (`notify.rs:1364-1366`, `:868-870`). rusty_tokio wakes **A**. This contradicts the module doc's "as tokio does".

**Required correction:** store the direction (FIFO or LIFO) instead of a bool, and use `pop_back` for LIFO when handing on.

**Regression test:** the Probe C sequence, asserting `(a, b) == (false, true)`, in the `notify.rs` test module.

## Ruled out, with reasons
- **Dropped at other points:** dropped after its first Pending, or registered by `enable` only: removed from the queue (`:278-280`). Pending from the coop budget while already woken: handed on, which is correct. Ready reached through a permit or `woken`: `done` skips release. Woken by `notify_waiters` and dropped: not handed on, which matches tokio's `Notification::All`.
- **Re-entrant waker:**
  - `release` drops `inner` before `fire` (`:288`).
  - `fire` does wake while it holds `state.waker` (an edition-2021 if-let temporary; this is not new). A waker that re-polls its own future returns at the `woken` check (`:215`) before touching `state.waker`. A waker that drops the future only takes `inner`, which is free.
  - The `Arc` dropped under `inner` at `:279` is never the last reference.
- **Not touched by this PR:** task state, reactors (no fd or OS code changed), migration, and Windows-specific behaviour.

## Optional improvements
- `release` does a linear scan; this is acknowledged in the fragment.
- `permits += 1` counts up, while tokio keeps at most one permit. This was already true before the PR, and the hand-on path now adds to it. It is worth a follow-up issue, not a block.

## Checks
All checks ran in scratch exports of the trees, with `CARGO_TARGET_DIR` outside the repo.

| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | The 4 new tests pass. Every runtime-building test fails with EPERM because the sandbox blocks the io_uring syscall; base fails the same lib tests. |
| `cargo test -p rusty_tokio` (default features) | `tests/sync.rs` passes all 125. The same 40 socket-related EPERM failures occur at base and head, so the PR adds none. |
| `cargo test -p rusty_tokio --features io-uring-reactor --lib -- sync::notify` | 4 passed |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Clean |
| `cargo check -p rusty_tokio --target x86_64-pc-windows-gnu --all-targets` | **Not run:** the target's std is not installed |
| `python3 .github/scripts/changes.py check` | 541 fragments ok |
| PR's new tests run against base | 3 of 4 fail there, as they should. `..._not_passed_on` passes at base; it guards the new hand-on code against passing on too much. |

## Validation gaps
- **Windows:** `cargo check` was not run, so Windows is unverified. Run it on a host with the `x86_64-pc-windows-gnu` target installed.
- **Runtime-backed tests:** anything that builds an io_uring ring, or uses Unix and network sockets, failed with EPERM in this sandbox. CI or an unsandboxed Linux host needs to confirm these tests.
- **macOS:** not run.
