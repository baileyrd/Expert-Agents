<!-- conhost/1246-r2 run 3, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 308 s, 1780887 tokens, $1.76 -->

I found one blocking defect at this head. K1, K2 and K3 are all fixed in the code. But GitHub was blocked by the sandbox, so I couldn't check any conhost behaviour against its source.

**Verdict: changes required.**

**Pinning:** Rusty-Mill/rusty_mill PR 1246, base `66f4bc38c8181fd669c58f8e34df385a6afa895e`, reviewed head `8ec54b85a08dc6e0a57e2bafde91952c839dcf18`. `git fetch origin pull/1246/head` matches the head. Since the last review there is one new commit, `8ec54b85a`, on top of `197fae026`.

## Prior findings at this head

- **K1 (P1, cooked echo bypasses backpressure): fixed.** `read.rs:485-488` checks the queue before each character and holds the read once it passes 80 KiB. Escape now erases one character per step (`cooked.rs:660-680`). Reads behind a held read wait, and GetConsoleInput returns nothing, so input stays in order (`read.rs:313, 407-411, 445-449`). The held read resumes when the terminal reads output (`console.rs:539-543`). It always gets that chance: a read is only held above 80 KiB, and any read of output from above 64 KiB sends the resume signal (`runtime.rs:216`). The engine stops taking input past 80 KiB on the same measure. The most a step can overshoot is about 5 bytes: one character's echo plus `\r\n`. The new test drives 60 waiting reads, each cleared by Escape, and the queue's peak stays within 80 KiB + 16. *Executed.*
- **K2 (P2, trailing ESC at a slice boundary read as Escape): fixed in the code.** The input lane now counts writes in progress (`runtime.rs:145-169`). A slice that doesn't end where a write ended goes in as `Event::InputMore` (`runtime.rs:526-531`), and the decoder keeps the ESC (`input.rs:240-249`). The decoder test (every cut of four inputs) and the `input-more` fixture pass. The lane test `input_ends_where_a_write_ended_not_where_the_engine_cut` is Windows-only, so I didn't see it run.
- **K3 (P2, surrogate pairs converted one unit at a time): fixed.** `input::Utf8Units` keeps pairs whole in cooked echo, ANSI raw reads, ANSI GetConsoleInput records and win32-to-VT conversion. *Executed.*

**Do the new tests guard anything?** At the base the whole crate is new, so I ran them against the previous head instead. I copied six new tests into a scratch copy of `197fae026`. The only change was making `high_water` visible. All six fail there:
- `cooked_echo_stays_within_the_host_reserve_and_resumes_on_drain`
- `a_read_behind_held_echo_does_not_take_its_input`
- `a_hang_up_ends_reads_held_back_by_their_echo`
- `ansi_raw_reads_keep_a_supplementary_character_whole`
- `ansi_records_and_cooked_echo_keep_a_supplementary_character_whole`
- `a_vt_client_gets_a_win32_surrogate_pair_as_one_character`

The K2 tests use the new `feed(bytes, end)` signature, so they can't be built at the previous head.

## Blocking findings

**F1 (P2): the design doc understates the ANSI record cache after K3.**
- **Where:** `docs/projects/term/conhost/design.md:249` (§7.4) says "ANSI read caches … one character: 3 bytes, **2 records**". The code is `crates/libs/ui/rusty_conhost/src/console/read.rs:656-675`.
- **Trigger:** a client calls ReadConsoleInputA for 1 record.
- **Scenario (executed, scratch probe):** the queue holds a key-down for a lone high surrogate `0xD83D`, then a key-down for `€` (U+20AC). One record expands to U+FFFD plus `€`, which is 6 UTF-8 bytes. The read returns 1 record and `cached_records.len()` is **5**; after the next read it is 4.
  - Even a valid surrogate pair is 4 bytes, so it leaves 3 cached records.
  - The memory involved is tiny (5 × 20 B). The defect is that the §7.4 table this PR maintains is now wrong.
- **Fix:** change the row to "at most 5 records (a waiting lone half's U+FFFD plus a 3-byte character)". Also list the new fixed-size holders: `held_unit`, `record_utf8` and `vt_utf8`.
- **Regression test:** a core test that sends that input and asserts `cached_records.len() <= 5`. The constant used should be the one the doc names.

## Design questions

- While a cooked read is held, PeekConsoleInput and NOWAIT GetConsoleInput return 0 records. GetNumberOfInputEvents still reports N and the input event stays signalled. A client that loops on wait-then-peek would spin until the terminal reads its output. It needs a second client and a stalled terminal, so it's narrow and doesn't last; but §7.4 doesn't say what other clients see in this state.

## Optional improvements

- §7.2 step 4 still says "handle(Input)", and §7.1 doesn't mention the lane's `writing` count. Neither mentions `InputMore`.
- `a_read_behind_held_echo_does_not_take_its_input` only asserts `lines.len() < 4` for the held phase. Asserting the exact number completed per output read would make it sharper.

## Checks performed

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | pass, 104 tests; `own_pty` has 0 tests on Linux |
| `cargo clippy -p rusty_conhost --all-targets -- -D warnings` (Linux) | pass, no warnings for this crate |
| `python3 .github/scripts/changes.py check` | pass, "539 fragment(s) ok", exit 0 |
| New tests copied onto `197fae026` | all 6 fail there, as they should |
| Scratch probe of the ANSI record cache | 5 cached records (F1) |

## Validation gaps

- **No conhost source.** Cloning microsoft/terminal at `09beddc6` was blocked by the sandbox (github.com denied). Copies exist in other sessions' scratch areas, one under a grading directory, outside this task, so I didn't use them. The review is therefore incomplete on parity. Two points need checking at the pin:
  - **ANSI GetConsoleInput (`InputBuffer::Read`):** does conhost convert a pair as a whole, or one unit at a time? If one unit at a time, `Utf8Units` on records is a divergence that belongs in §5.10. Probe: ReadConsoleInputA of 😀 typed under ConPTY.
  - **Trailing ESC at the end of a read:** how does ConPTY's `InputStateMachineEngine` / `StateMachine` flush it? Probe: a lone ESC typed in plain-VT mode, and `ESC` then `[A` written separately.
- **Windows-only tests didn't run:** the `runtime` tests, including the K2 lane test, and `tests/own_pty.rs`.
- **No Windows clippy:** only the Linux target is installed.
- **D4 Windows CI run at `8ec54b85`:** not seen by me.
