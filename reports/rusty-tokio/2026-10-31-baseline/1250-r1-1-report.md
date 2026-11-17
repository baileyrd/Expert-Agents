<!-- rusty-tokio/1250-r1 run 1, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 373 s, 1567320 tokens, $1.49 -->

I found one blocking lost-wakeup race, which I triggered on the unmodified head, plus two places where the PR's "as tokio does" claim is wrong. **Verdict: changes required.**

# Review: Rusty-Mill/rusty_mux PR #1250, "rusty_tokio: a dropped Notified leaves the waiter queue"

- **Repository:** Rusty-Mill/rusty_mill
- **PR:** #1250
- **Base:** `d05170b8cc082cffaf6c348411e82bea84b9632d`
- **Head reviewed:** `98621f936d802e1fb05912e47f1b569eebe230c9`. I fetched `pull/1250/head` and it matches.
- **Diff:** one commit touching `src/sync/notify.rs` (+159/−6) and `changes/2026-10-30-rusty-tokio-notify-drop.md`.

## What must hold (step 1)
| Item | Invariant | Source |
|---|---|---|
| `notify_one` / `notify_last` popping a waiter | Whether the wakeup was a single one must be recorded while the `inner` lock is held, before the lock is released. A drop that takes the same lock must see it. | tokio 1.53.3 `notify_locked` stores `Notification::One(strategy)` under the waiters lock. `drop_notified` reads it under that lock ("We hold the lock, so this field is not concurrently accessed"). |
| Dropping a waiter that is still queued | It leaves the queue. Nothing is woken or freed while the lock is held. | Module docs, notify.rs:29-33 |
| Dropping a waiter that was fired but never resolved | A single wakeup goes to the next waiter in the original strategy's order, or becomes a permit. A `notify_waiters` wakeup is not passed on. | tokio `drop_notified` → `notify_locked(…, strategy)` |
| A permit consumed by `enable` or `poll` | It is consumed. The future is `Done`, and dropping it gives nothing back. | tokio `poll_notified` `Init` branch → `State::Done`. `drop_notified` only acts in `Waiting`. |
| Shutdown signal (`runtime/mod.rs:384-392`) | Uses `notify_waiters` only. The new `Drop` just removes the dead entry, which is an improvement. | — |

## Blocking findings

### F1 (P1): a `notify_one` racing a dropped waiter is still lost
- **Where:** notify.rs:102-104, :116-118, :145-147, :282-284 and :286-289.
- **Trigger:** a `Notified` is dropped on one thread while `notify_one`/`notify_last` runs on another. That is the PR's own `select!` loop case, on the multi-threaded runtime with an `Arc<Notify>`.
- **Cause:** `notify_one` pops waiter A and releases `inner` (line 103). Only then does `fire` store `single` (line 146), and `fire` never takes `inner`.
- **Trace:**
  1. Thread B runs `notify_one`, pops A and drops the guard.
  2. Thread A drops its `Notified`. `release` locks `inner` and does not find A in the queue (line 278).
  3. `release` reads `single == false`, because B has not stored it yet, and returns at line 284.
  4. B's `fire` then marks a waiter that no longer exists.
  5. Waiter C stays `Pending` and no permit is banked: a lost wakeup and a hang.
- The same window exists when `release` passes a wakeup on (lines 288-289) while the next waiter is itself being dropped. The `Relaxed` store and load of `single` also have no happens-before edge between them.
- **Executed:** probe `concurrent_drop_and_notify_one_never_loses_the_wakeup` (scratch copy; two waiters, spin-aligned threads, release build):

  | Tree | Lost wakeups |
  |---|---|
  | head | 1,078 of 300,000 (two runs) |
  | base | 100,000 of 100,000 |
  | head + candidate fix | 0 of 300,000 (three runs) |

- **Who is hurt:** any multi-threaded caller that races `notified()` in a `select!`. rusty_mux (#1247) holds its `Notify` in an `Rc`, so it is single-threaded and does not hit this. I did not find a current workspace caller that both drops waiters in a loop and notifies from another thread. sessionmgr-daemon notifies from a `std::thread` but awaits only once.
- **Grade:** by impact (a hang). The fragment's claim "no longer loses a notify_one to a Notified that was dropped" is false under concurrency.
- **Correction:** store `single` and `woken` while `inner` is held, in `notify_one`, `notify_last`, `notify_waiters` and `release`'s pass-on. Take and wake the waker after dropping the guard. The candidate fix I verified does exactly this, as a `mark` step under the lock and a `wake` step outside it.
- **Regression test:** add the probe above to `crates/libs/async/rusty_tokio/tests/sync.rs`. It is red at head (about 0.36% per iteration, so 300k iterations is reliably red) and green with the fix.

### F2 (P2): a permit taken by `enable` is handed back, unlike tokio, yet the docs say "as tokio does"
- **Where:** notify.rs:259-262, the module docs at :31-33, and the change fragment.
- **Trace:** `enable` takes a permit and sets `registered = true` and `single = true`. Dropping the future then runs `release`, which re-banks the permit. tokio moves the future to `State::Done` and does not give the permit back.
- **Executed:** on tokio 1.53.3, the sequence `notify_one(); notified().enable(); drop; notified().poll()` returns **Pending**. The PR's test `a_permit_taken_by_enable_and_dropped_is_banked_again` asserts **Ready**.
- **Effect:** a spurious extra wakeup, not a lost one. For example, tokio's own documented `enable` + `try_recv` pattern gets one extra loop. It is also inconsistent within the crate: a permit taken by `poll` (lines 220-222) is not given back.
- **Correction:** either match tokio (mark the future done on the `enable` permit path and do not pass it on), or keep the behaviour and rewrite the docs and fragment to state the divergence. Either way, the test should encode the chosen behaviour, and if parity is chosen it should assert Pending.

### F3 (P2): a passed-on `notify_last` goes to the oldest waiter, where tokio sends it to the newest
- **Where:** notify.rs:286 (`pop_front` always), with the docs at :31-33.
- **Trace:** waiters A, B and C are queued. `notify_last` fires C, and C is dropped before it runs.
- **Executed on both runtimes:**
  - tokio: `a ready = false, b ready = true`
  - rusty_tokio head: `a ready = true, b ready = false`
- **Correction:** record the strategy (FIFO or LIFO) with the wakeup instead of a `single` bool, and pop from the matching end.
- **Regression test:** the LIFO probe as a unit test in `notify.rs`'s `mod tests`.

### F4 (P2, coverage)
- No test covers the concurrent drop/notify path behind F1; the PR's tests are all single-threaded and hand-polled.
- Of the four new tests, three are red at base, so they guard the change. The fourth (`a_consumed_wakeup_and_a_notify_waiters_one_are_not_passed_on`) passes at base too. It only guards against over-forwarding, which is fine, but it does not cover the fix.

## Failure classes ruled out
- **Waiter dropped while queued:** it is removed under the lock. The `Arc` removed at line 279 is never the last reference, because `self.state` still holds one, so no waker is dropped under `inner`.
- **Waiter woken but not yet run:** passed on, apart from the F1 window.
- **`notify_waiters` wakeup:** correctly not passed on.
- **Re-entrant waker or drop under `inner`:** every `wake` runs after `inner` is released. A `Notified` dropped from inside a waker only takes `inner`, which is free at that point.
- **Task state, reactor, migration and Windows:** not touched by the diff, which is pure `std` code with nothing OS-specific.
- **Linear scan in `release`:** a scan under the lock, stated in the fragment's known limitations.

## Design question
rusty_tokio counts permits, while tokio keeps a single permit bit. This predates the PR, but F2's give-back adds to the drift between the two. The owner should decide whether `Notify` aims for exact tokio permit semantics.

## Optional improvements
- **Pre-existing, outside the diff:** in `fire` (notify.rs:148-150, edition 2021), the `if let … = state.waker.lock().unwrap().take()` guard stays alive across `waker.wake()`. A waker that polls the same `Notified` inline would deadlock on that mutex. The PR adds a new caller of this from `Drop` (line 289). Taking the waker into a local first, as the candidate fix does, removes the problem.
- Give `done` and `registered` one shared state enum (Init/Waiting/Done), as tokio does, so the two permit paths cannot drift apart.

## Checks run
| Check | Result |
|---|---|
| `cargo test -p rusty_tokio --all-features` | **Environment failure.** The sandbox refuses io_uring setup (`EPERM`), so every runtime-backed test fails under `io-uring-reactor`. The 7 lib failures are identical at base. Not a valid signal. |
| `cargo test -p rusty_tokio --features io-uring-reactor` | Not run separately. It needs the same blocked io_uring setup. **Validation gap.** |
| `cargo test -p rusty_tokio` (default, epoll) | 62 targets pass, including the lib `notify` tests, `sync.rs` and `shutdown.rs`. 7 targets fail (`net` multicast `ENODEV`, and the unix-socket suites) with identical counts at base, so they are environmental. |
| PR's tests at base | 3 red, 1 green (see F4). |
| `cargo clippy -p rusty_tokio --all-targets --all-features -- -D warnings` | Pass. |
| `cargo check --target x86_64-pc-windows-gnu --all-targets` | **Could not run.** The target's std is not installed and there is no network to fetch it. **Validation gap.** |
| `python3 .github/scripts/changes.py check` | Pass (541 fragments). |
| tokio parity probes on tokio 1.53.3 (from the local registry; GitHub was blocked by the sandbox) | Executed, see F2 and F3. |
| F1 stress probe at head, base and candidate fix | Executed, see F1. |

I ran all checks and probes in scratch copies under `$TMPDIR`; the reviewed tree is unchanged. Remaining validation gaps: a real io_uring run, and Windows and macOS execution.

## Verdict: **changes required**
F1 is a lost wakeup the PR claims to fix, and it still happens under concurrency. F2 and F3 are tokio-parity gaps behind a false "as tokio does" claim. F4 is the missing concurrent test.
