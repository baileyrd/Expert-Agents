<!-- conhost/1246-r2 run 3, variant generated, prompt 127573c57ee83fe961ef918e3a829f48e3277fd6; 252 s, 1555360 tokens, $1.49 -->

I re-checked K1–K3 at 8ec54b85: all three are fixed. The fix commit adds three defects of its own, all confirmed by probe runs, so the verdict is **changes required**.

## Review: Rusty-Mill/rusty_mill PR 1246
- **Base:** `66f4bc38c8181fd669c58f8e34df385a6afa895e`
- **Head:** `8ec54b85a08dc6e0a57e2bafde91952c839dcf18`. I fetched `pull/1246/head` and it matches this SHA, which is also the checkout.
- **Scope:** I reviewed the whole diff, concentrating on the fix commit 8ec54b85, which answers K1–K3.

### Prior findings
| # | Status at head | Evidence |
|---|---|---|
| K1 (P1) cooked echo bypasses backpressure | **Fixed** | `read.rs:485-488` checks `out.len() + echo.len() > 80 KiB` before every character. Escape now erases one character per step (`cooked.rs:96-112`). A held read keeps its input, and the reads behind it wait (`read.rs:407-418`, `436-450`). `OutputDrained` re-runs `serve_reads` (`console.rs:539-543`). It can't stall: a hold means more than 80 KiB is queued, and any reader drain above 64 KiB raises `output_drained` (`runtime.rs:216`). Test `cooked_echo_stays_within_the_host_reserve_and_resumes_on_drain` passes. |
| K2 (P2) trailing ESC at a slice boundary | **Fixed (core)** | `take_input` reports a write end only when the lane is empty and `writing == 0` (`runtime.rs:250-257`), and every path keeps `writing` balanced (`runtime.rs:142-170`). `escape(b, false)` returns `Incomplete` (`input.rs:240-249`). `a_split_anywhere_decodes_as_the_whole` passes. The lane-side test runs only on Windows (see gaps). |
| K3 (P2) surrogate pairs one unit at a time | **Fixed on the four named paths** | `Utf8Units` is used in cooked echo, `vt_for`, `read_records` and `next_text`. The holding scheme in `next_text` has its own defect (N1). |

### Blocking findings

**N1 (P1, liveness). A character held in `held_unit` is hidden from the input event, GetNumberOfInputEvents and GetConsoleInput.**
- **Trigger:** a lone high surrogate followed by another character, read by an ANSI raw ReadConsole whose buffer the U+FFFD exactly fills.
- **Where:** `src/console/read.rs:565-580` (`next_text`) and `:594-596` (`next_char`); `src/console.rs:551-552` (`sync_input_available` looks only at `self.input`).
- **What goes wrong:** `next_text` pops the unit after the high half out of the queue into the console-wide `held_unit`, and nothing counts it.
- **Executed trace** (probe in a scratch copy):
  1. Terminal input `ESC[0;0;55357;1;0;1_` then `a`, with mode = PROCESSED_INPUT.
  2. ReadConsoleA (raw) with a 3-byte buffer returns `EF BF BD`.
  3. Then: `input_available=false`, GetNumberOfInputEvents = 0, PeekConsoleInput returns 0 records, `held_unit=Some('a')`.
  4. The `a` only appears in a later ReadConsole.
- **Impact:** a client blocked in `WaitForSingleObject(hIn)` before reading (the normal pattern) is not woken for the `a`. It waits for an unrelated later key, and ReadConsoleInput clients never see the `a` in order.
- **Second case, same cause:** in the `None` branch (`read.rs:577-579`), a high half taken by a read that then completes with earlier data is also invisible to ReadConsoleInput and Peek, so a later GetConsoleInput gets the low half alone.
- **Required correction:** decide the pair by peeking at the next record in the queue (`self.input.iter()`) instead of popping it. Otherwise `held_unit` has to count in `sync_input_available`, in GetNumberOfInputEvents and in `read_records`.
- **Regression test** (core): the trace above, asserting that the input event stays true and that GetConsoleInput returns `a`.

**N2 (P2, design doc out of date). The ANSI record cache can now hold 3 records, but §7.4 says 2.**
- **Where:** `docs/projects/term/conhost/design.md:614` says "one character: 3 bytes, 2 records". `src/console/read.rs:672-676` now spreads a surrogate pair's 4 UTF-8 bytes over the low half's record.
- **Executed:** with input `😀`, ANSI GetConsoleInput for 1 record returned 1 record and left `cached_records.len() == 3`.
- **Required correction:** change §7.4 to "3 bytes, 3 records" and update the total if it moves. A test should assert the cache never exceeds 3 records.

**N3 (P2, liveness: hot poll / wrong count). While a held echo owns the queue, the input event stays set and GetNumberOfInputEvents counts the held read's keys, but PeekConsoleInput returns nothing.**
- **Trigger:** a cooked read held by its echo, plus another client polling with PeekConsoleInput while the terminal reader is stalled.
- **Where:** `src/console/read.rs:313` empties GetConsoleInput results while `echo_held` is set. `get_number_of_input_events` (`read.rs:268-277`) and `sync_input_available` (`console.rs:551-552`) still report the queue.
- **Executed:** 8 prefilled cooked reads, then Escape+Enter ×8 in one slice. Result: `echo_held=true`, `input_available=true`, GetNumberOfInputEvents = 14, Peek = 0, 81,921 bytes of output queued.
- **Impact:** a `WaitForSingleObject(hIn)` + `PeekConsoleInput` loop (the `Console.KeyAvailable` pattern) spins hot for the whole stall. In conhost the cooked read would already have taken those records, so the event would be reset.
- **Required correction:** treat the input as unavailable while `echo_held` is set, in both the event and the count. The hold always ends on a drain, which re-syncs both.
- **Regression test** (core): the trace above, asserting that the last `SetInputAvailable` is false and the count is 0 while held, and that both come back once drained.

### Design questions
None.

### Optional improvements
- In `end_reads` (`read.rs:212-215`), `echo_held` stays true if the held read was removed but another cooked read remains. It clears on the next drain, but it would be cleaner to recompute the flag directly.
- `next_char` returns `held_unit` with modifier state 0 (`read.rs:595`), which drops the key's ControlKeyState.

### Checks
- `cargo test -p rusty_conhost --offline` (Linux): 104 passed, 0 failed.
- `cargo clippy -p rusty_conhost --all-targets --offline -- -D warnings`: clean.
- `python3 .github/scripts/changes.py check`: 539 fragments ok.
- Probes in a scratch copy of the head (`probe_held_unit_hides_a_character_from_the_input_event`, `probe_ansi_record_cache_size`, `probe_peek_during_held_echo`): each ran and reproduced N1–N3 as traced.
- The new tests can't run at BASE (66f4bc38 has no input layer) or at 197fae02 (the APIs changed: `feed(bytes, end)`, `Event::InputMore`, `vt_for(.., &mut Utf8Units)`). They do target the K1–K3 behaviour directly.

### Validation gaps
- `runtime.rs` tests, including `input_ends_where_a_write_ended_not_where_the_engine_cut`, are Windows-only and I didn't run them.
- I did not see the D4 manual Windows CI run at 8ec54b85. A new run is owed after this push.
- I did not compare conhost's ANSI handling of lone surrogates (`InputBuffer` consume and cache paths at the pinned commit). A probe of ReadConsoleA and ReadConsoleInputA with a lone `0xD83D` under ConPTY would settle the parity for N1.

### Verdict
**Changes required** (N1 P1; N2 and N3 P2).
