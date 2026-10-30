<!-- conhost/1246-r2 run 5, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 547 s, 3024533 tokens, $2.55 -->

# Review of PR 1246: rusty_conhost console input (host PR 3)

**Verdict: changes required.** The K1 fix keeps echo within the 80 KiB reserve, but it adds two P1 defects: a held read can stall, and it can lose its own queued input.

- **Repository:** Rusty-Mill/rusty_mill, PR 1246
- **Base:** 66f4bc38c8181fd669c58f8e34df385a6afa895e
- **Head:** 8ec54b85a08dc6e0a57e2bafde91952c839dcf18. `git fetch origin pull/1246/head` gave FETCH_HEAD = 8ec54b85, matching the head SHA. The only new commit since the last review (197fae02) is 8ec54b85.

## Prior findings at this head

| | Status |
|---|---|
| **K1 (P1)** cooked echo bypasses backpressure | **Cap fixed.** Echo is checked before every character (read.rs:485). Escape now erases one character per step (cooked.rs:111). `out.len()` includes the shared queue (console.rs:233, runtime.rs:610). The new test keeps the high-water mark within 80 KiB + 16. **But the fix introduces N1 and N2 below.** |
| **K2 (P2)** trailing ESC at a slice boundary | **Fixed in the core.** `feed(bytes, end)` keeps a trailing ESC unless the terminal's write ended there (input.rs:841–851). The split-anywhere test passes. The lane side (`writing` counter, runtime.rs:142–171, 250–257) traces correctly. It is `cfg(windows)`, so I could not compile or run it (see validation gaps). |
| **K3 (P2)** surrogates converted one unit at a time | **Fixed** for cooked echo, ANSI raw reads, ANSI records and win32-to-VT conversion. Tests pass. Two side effects follow as N3 and N4. |

## Blocking findings

### N1 (P1, liveness): a held cooked read can stall until an unrelated key
- **Where:** read.rs:485–488; console.rs:539–543; runtime.rs:216
- **Trigger:** a long cooked line is erased with Escape while 16–64 KiB of output is queued and the terminal is reading concurrently.
- **Cause:** a read is held when the engine's snapshot is over 80 KiB (`held` at handle start plus echo). It resumes only on `Event::OutputDrained`. The reader raises that flag only when one of its `read()` calls *starts* with more than 64 KiB queued (`before > DRAIN_WAKE_ABOVE`). Nothing ties the two thresholds together.
- **Trace** (console part *executed*, runtime part *source-traced*):
  1. A cooked read holds 8,000 typed characters.
  2. At the start of the handle, `held` = 61,440. Escape+Enter arrive.
  3. After 6,827 erase steps (20,481 bytes of echo, out.len = 81,921) the read is held. `echo_held` is true and the Enter is still queued.
  4. During that handle the reader drains at least 17,921 of the 61,440 bytes. Each of those reads starts at or below 61,440, so no flag is raised.
  5. The engine pushes 20,481 bytes, leaving at most 64,000 queued. Every later read also starts at or below 64 KiB, so no flag is raised.
  6. The probe shows that Resize and other events do not serve the read; only `OutputDrained` completes it. The input lane is empty, so no `Input` event comes either. cmd shows a half-erased line until the user presses another key.
- **Doc impact:** this makes the new §7.4 claim ("goes on when the reader drains", design.md:606) false.
- **Correction:** guarantee a wake whenever a hold exists and the real queue is at or below 80 KiB. For example, after `push_output`, if the console reports a held echo and `output_len() ≤ INPUT_PAUSE_ABOVE`, raise `output_drained` or poll. Alternatively, let the reader wake on any drain while a hold is pending.
- **Regression test:** make Host's `drain` model the 64 KiB gate, and add a test where the queue shrinks during the hold. A Windows runtime test should cover the same case.

### N2 (P1, input loss): later input slices evict a held read's own queued input
- **Where:** sched.rs:82–86; input.rs:681–687; read.rs:445–449
- **Trigger:** a paste into a cooked read while output sits near the 80 KiB reserve.
- **Cause:** a held read now leaves its input in the record queue across turns. The engine keeps taking 16 KiB lane slices whenever the shared queue is at or below 80 KiB, which is true right after the drain that resumes the read. The record queue drops its oldest records at 4,096 (§6, line 610), and here those are the held read's own records.
- **Trace (executed):**
  1. A cooked read is waiting with the queue at 80 KiB − 500.
  2. 4,000 'a' arrive. The read is held with 3,499 records left.
  3. The reader takes 1 KiB, `OutputDrained` fires, and 2,475 records are left.
  4. The same turn's planned slice of 3,000 'b' + CR arrives. The queue hits 4,096 and `InputDropped` is logged.
  5. The completed line has **2,619 'a'** and 3,000 'b': 1,381 typed characters are gone.
  6. The same trace at 197fae02 keeps all 4,000 'a'.
- **Doc impact:** the design's backpressure for this case is the input lane blocking `PtyMaster::write`, not dropping records.
- **Correction:** while `echo_held` is set, the engine should take nothing from the input lane, for example via a `View` field that makes `plan.input` 0. The terminal's write then blocks.
- **Regression tests:** this probe as a core test, plus a `sched` test.

### N3 (P2): a unit parked in `held_unit` is invisible to record readers and the input event
- **Where:** read.rs:571–580, 594–596, 642–678, 262; console.rs:552
- **Trigger:** a lone high surrogate followed by a key, read by an ANSI raw ReadConsole whose buffer the U+FFFD fills.
- **Trace (executed):**
  1. A 3-byte raw ReadConsoleA is waiting.
  2. A win32 high half, then 'x', arrive. The read returns `EF BF BD` and `held_unit` = 'x' with the queue empty.
  3. PeekConsoleInputW returns 0 records, and the input event is off.
  4. ReadConsoleInputW waits, then returns 'y' when it arrives. 'x' is skipped and reaches some later ReadConsole instead.
- **Correction:** push the unit back to the front of the record queue, or make `read_records`, `get_number_of_input_events` and `sync_input_available` account for `held_unit`.
- **Regression test:** this probe.

### N4 (P2): the docs understate the ANSI record cache
- **Where:** design.md:614 says the cache is "one character: 3 bytes, **2 records**".
- **Trace (executed):** a pair is now one 4-byte UTF-8 character. ANSI GetConsoleInput with count 1 on "😀" returns 1 record and leaves `cached_records` = 3.
- **Correction:** say 3 records. A core test asserting the bound should go with it.

## Design questions
- **Record cap vs. slice size.** The 4,096-record cap with oldest-dropped (§6) is justified as "about 1 s of fast typing or paste". The lane hands over 16 KiB at once, though, and `terminal_input` stores the whole slice before serving reads. A single paste of 8,000 characters into a waiting cooked read kept only 4,096 (executed). Long commands pasted into cmd or PSReadLine lose their start. Should the engine size slices to the queue's free room, or apply lane backpressure when the queue is near its cap?

## Optional improvements
- `vt_for` emits Alt's ESC with the high half (input.rs:874). A VT-input reader that reads between the halves sees a lone ESC at the end of its read.
- The fixture `input-more` step is good. A fixture for a held echo plus a drain would document N1 once it is fixed.

## Checks
- `cargo test -p rusty_conhost`: **104 passed**, 0 failed (Linux). `own_pty`: 0 tests on Linux.
- `cargo clippy -p rusty_conhost --all-targets -- -D warnings`: **clean** (Linux target).
- `python3 .github/scripts/changes.py check`: **539 fragment(s) ok**.
- Probes, run in a scratch copy at scratch/r3/copy, not in the reviewed tree:
  - held-echo liveness (N1)
  - eviction at head and at 197fae02 (N2)
  - ANSI record cache size (N4)
  - `held_unit` visibility (N3)

## Validation gaps
- **No conhost source.** Cloning microsoft/terminal was blocked (github.com denied by the sandbox), so these are unverified against the pinned 09beddc6, and I can't say whether §5.10 needs entries:
  - parity of ReadConsoleA and ANSI GetConsoleInput on surrogate pairs
  - ConPTY's handling of a trailing ESC at a read boundary (`VtInputThread`/`StateMachine`)

  **Proposed probe:** `conhost_probe` under OpenConsole running ReadConsoleA with 1/2/4-byte buffers and ReadConsoleInputA count 1 on 😀, plus a split `ESC [A` written in two pipe writes.
- **Windows-only code not checked.** `runtime.rs` is `cfg(windows)`. The `writing` counter, `take_input`'s `end` flag, and the test `input_ends_where_a_write_ended_not_where_the_engine_cut` were not compiled, linted or run. The `own_pty` integration tests were not run either. I did not see a Windows CI log.
