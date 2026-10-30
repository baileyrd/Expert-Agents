<!-- conhost/1246-r2 run 1, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 276 s, 2123202 tokens, $1.91 -->

I couldn't complete this review: GitHub was blocked, so none of the conhost parity claims could be checked against the source. On the code itself, all three prior findings are fixed, and I found two new documentation defects (P2), so the verdict is still **changes required**.

**Repository:** Rusty-Mill/rusty_mill, PR 1246
**Base:** 66f4bc38c8181fd669c58f8e34df385a6afa895e
**Reviewed head:** 8ec54b85a08dc6e0a57e2bafde91952c839dcf18. The fetched `pull/1246/head` matches it, and the base is an ancestor.

## Prior findings at this head

| | Status | Evidence |
|---|---|---|
| **K1 (P1)** Cooked echo bypassed output backpressure | **Fixed** | `console/read.rs:485` checks the echo against the 80 KiB limit before every character, and Escape now erases one character per step (`cooked.rs:111`). A held read keeps its input queued, and reads behind it wait (`read.rs:313`, `read.rs:407`). `OutputDrained` lets held reads continue (`console.rs:539-543`). Each step adds at most about 9 B, so the queue stays near 80 KiB, well under the 144 KiB cap. **Executed:** removing the check, the waiting behind a held read, or the drain retry each turns tests red. |
| **K2 (P2)** A trailing ESC at a slice boundary was read as the Escape key | **Fixed** | A lone trailing ESC is only the Escape key when the terminal's write ended there (`input.rs:244-249`). The input lane counts writes still in progress (`runtime.rs:145-169`, `take_input` at 250-257), and the engine sends `InputMore` otherwise. **Executed:** making ESC always the Escape key turns 4 tests red, including the fixture. The lane-counting test `input_ends_where_a_write_ended_not_where_the_engine_cut` only runs on Windows, so I didn't run it. |
| **K3 (P2)** Surrogate pairs were converted one UTF-16 unit at a time | **Fixed** | `Utf8Units` (`input.rs:601-645`) is now used for cooked echo, ANSI records (kept separately for key-downs and key-ups), win32-to-VT conversion, and ANSI raw reads, which hold a high half until its low half arrives (`read.rs:566-582`). **Executed:** breaking the pairing in `Utf8Units` turns 4 tests red, and breaking it in `next_text` turns 2 red. |

## Blocking findings

**F1 (P2): §7.4 understates how many records the ANSI read cache holds.**
- **Where:** `docs/projects/term/conhost/design.md:614` says "one character: 3 bytes, 2 records". The code is `read.rs:658-675`.
- **Trigger, executed:** type 😀, then make an ANSI `GetConsoleInput` call with room for 1 record. The low half's record turns into 4 UTF-8 byte records, 1 is returned, and **3** go into `cached_records`. My probe printed `cached_records after count=1: 3`.
- **Cause:** the K3 fix put a 4-byte character into a single record's expansion; before it, the most was 3 bytes. A held `held_unit` also exists now and isn't listed in the table.
- **Correction:** change the row to "3 bytes, 3 records, 1 held UTF-16 unit".
- **Regression test:** assert `cached_records.len() <= 3` after that call. Asserting `<= 2` fails at this head.

**F2 (P2): the design's interface and engine loop leave out `Event::InputMore`.**
- **Where:** `console.rs:116-120` adds `InputMore(&[u8])`, and `runtime.rs:526-531` sends it. But the `Event` list in design §3.3 (`design.md:166-174`) shows only `Input`, and §7.2 step 4 (`design.md:540`) says `handle(Input)`. Neither section says that the end of a write decides what a trailing ESC means.
- **Trigger:** source-traced.
- **Correction:** add `InputMore` and its meaning to §3.3 and §7.2 step 4.
- **Regression test:** none, since this is documentation only.

## Design question

While a cooked read is held by its echo, the console gives contradictory answers about input:
- **What I saw (executed):** with 8 held reads, `GetNumberOfInputEvents` returned 19, the input-available event was set, and `PeekConsoleInput` (NOWAIT) returned 0 records.
- **Why it happens:** the §7.2 invariant keeps the event tied to "the queue is non-empty", while §7.4 keeps the held read's input queued.
- **Who it hurts:** a second client that waits on stdin and then peeks would spin at full CPU until the terminal reader drains. .NET's `Console.KeyAvailable` peeks like this (`ConsolePal.Windows.cs`, from memory, not checked).
- **Conhost:** has no such state. Under ConPTY a blocked output pipe stalls every client instead of answering inconsistently (from memory).
- **Decision needed:** whether the event and the count should exclude input that a held read has reserved.

## Optional improvements
- In plain-VT input mode, a write ending in `ESC O` or `ESC [` (Alt+Shift+O, Alt+[) stays pending even when the write ended there. The next key then turns it into an unknown sequence that is dropped. Win32-input-mode, which rusty_term supports, avoids this path. See the validation gaps.
- The test `a_line_rest_past_the_pending_cap_is_dropped_and_logged` (`read.rs:1171`) seeds pending data on the *output* object, which isn't realistic. It still exercises the cap.

## Checks performed

| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux) | **Pass**: 104 tests in the library; `own_pty` ran 0 tests on Linux |
| `cargo clippy -p rusty_conhost --all-targets -D warnings` (Linux) | **Pass** |
| `cargo fmt --check` | **Pass** |
| `python3 .github/scripts/changes.py check` | **Pass**: 539 fragments ok |
| Mutations M1–M6 (echo check, waiting behind a held read, drain retry, ESC decision, `Utf8Units` pairing, `next_text` pairing) | Each turns at least one test red |
| Scratch probes | Cache gives 3 records (F1); held echo answers 19 events / 0 peeked (design question) |

I ran the mutations in a scratch worktree, which I've since removed; the reviewed tree is unchanged. The new tests can't be run at the base, because the base has no input path. Those that guard the fix commit were checked by the mutations above.

## Validation gaps
- **Conhost source:** cloning microsoft/terminal failed because the sandbox blocked github.com. Every parity statement here, about ReadConsole, GetConsoleInput, RAW_READ, RAW_FLUSH, GetNumberOfInputEvents, FlushConsoleInputBuffer, GenerateConsoleCtrlEvent and the input event, is from memory and has no checked `path:line`. Copies exist in other sessions' scratch folders, but they're outside this task, so I didn't use them. **This alone leaves the review incomplete** until someone with GitHub access repeats the parity pass.
- **Trailing `ESC O` / `ESC [`:** what conhost does with these at the end of a write under ConPTY is unknown. A probe would settle it: run `conhost_probe` raw-ReadConsoleInput under ConPTY with win32-input-mode off, write `\x1bO`, then `x`, and record what comes back.
- **Windows-only:** I didn't run Windows clippy, the `runtime` tests (including the `writing`-counter test for K2), or `tests/own_pty.rs`. No Windows target is installed here.
- **D4 Windows CI run:** I didn't see one for 8ec54b85; GitHub wasn't reachable.

## Verdict
**Changes required** (F1 and F2, both P2). The review is also incomplete on conhost parity until the source can be checked.

Unrelated: `.bash_profile`, `.bashrc` and `.claude/agents` show up as untracked in the reviewed checkout. I didn't create them and left them alone.
