<!-- conhost/1246-r1 run 3, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 532 s, 3951006 tokens, $2.97 -->

I'm requesting changes on PR 1246. The two problems that block it are that one Escape keystroke can push output past the 144 KiB cap, and that held arrow keys can corrupt cmd's input line. The inherited checks pass, but I couldn't see conhost's source or run anything on Windows, so some conhost comparisons are flagged below as unverified.

- **Repository:** Rusty-Mill/rusty_mill, PR 1246
- **Base:** 66f4bc38c8181fd669c58f8e34df385a6afa895e
- **Head:** 197fae02607fa8c4428614aef7a4e7ac1b5bca7f. The fetched `pull/1246/head` equals this SHA.

**Limits on the evidence:**
- **conhost source:** the sandbox blocked github.com, so I couldn't clone microsoft/terminal at `09beddc6`. Statements below about how conhost behaves come from my memory of its source and are marked **(unverified)**. Findings 1 and 2 don't depend on them: the design's caps and the probes I ran settle those.
- **Builds:** the full workspace can't resolve dependencies offline. I ran tests and clippy in a scratch workspace holding `rusty_conhost`, `platform` and `rusty_wire` copied from the head and base trees (stable 1.98.1, the pinned version). All probes ran in scratch copies; the reviewed tree is unchanged.

## Blocking findings

### 1. P1: One Escape key can push output well past the 144 KiB cap (*executed*)
- **Where:** `src/cooked.rs:87-110` (`Line::vkey` / `backspace`) and `src/console/read.rs:462-464`, where echo is pushed with no budget check.
- **Trigger:** a client issues a cooked ReadConsoleW with ~32,000 units of initial data. That is legal: it fits the 64 KiB packet, and `Line::new` sets `limit = max(initial, 8192)`. Then the user presses Escape once.
- **What happens:** `vkey(ESCAPE)` writes `"\b \b"` for every unit: 32,000 × 3 = **96,000 B of echo from 1 byte of input**. In my probe the queue started at 80 KiB, the most at which §7.2 step 4 still takes input. The high-water mark reached **177,920 B against the 147,456 B (144 KiB) cap**.
- **Worse case (source-traced, not run):** with 15 such reads parked (15 × ~64 KiB fits the 1 MiB parked cap) and a terminal that sends win32-input-mode records, one 16 KiB slice of `[Esc, Enter] × 15` would echo 15 × 96,000 = **1,440,000 B in one turn**.
- **Docs made false:** §7.4 says "past 80 KiB the engine stops taking bytes … so echo stops" and that the queue "never exceeds 144 KiB". That assumes one slice echoes at most 64 KiB.
- **Parity:** ConPTY conhost redraws the edit line, so its output is bounded by the screen, not the buffer length **(unverified)**.
- **Fix:** bound echo where it's made. Either cap what one record can emit (erase by on-screen width, or `\r` plus erase-line, rather than per unit), or stop consuming records in `attempt` once `out.len()` would pass `CLIENT_BUDGET + 16 KiB`, leaving the rest queued.
- **Regression test:** a core test with a 32,000-unit initial-data read, the queue held at 80 KiB, then `Event::Input(b"\x1b")`, asserting `high_water ≤ 144 KiB`. It goes red today.

### 2. P1: A lone ESC at the end of a 16 KiB slice becomes an Escape key (*executed*)
- **Where:** `src/input.rs:238-242` and `runtime.rs` `take_input`.
- **Cause:** the decoder treats a trailing ESC as the Escape key. The engine slices the input lane at 16,384 bytes no matter where a sequence starts or ends.
- **Trigger:** the user holds Up or Down while output is backpressured, so more than 16 KiB of `ESC[A` builds up in the lane. 16,384 = 5,461 × 3 + 1, so the first slice ends exactly on ESC.
- **Result in cmd's cooked read:** the Escape clears the line, then `[A` is typed. My probe typed `dir`, then the split sequence, then Enter; the read returned `"[A\r\n"` instead of `"dir\r\n"`. VT-input clients aren't hurt, because the raw bytes still join up.
- **Fix:** the engine tells `Event::Input` whether more bytes are still waiting in the lane. While they are, a trailing lone ESC stays pending; only an ESC that ends the lane counts as the key. Alternatively, never end a slice on ESC.
- **Regression test:** a core test that feeds `…ESC` with "more" set, then `[A\r`, and expects `"dir\r\n"`.

### 3. P2: Cooked echo sends control characters to the terminal raw (*executed*)
- **Where:** `src/cooked.rs:78-81`.
- **What happens:** typing `a`, Ctrl+L, Ctrl+N, Ctrl+G in a cooked read echoes `a\x0c\x0e\x07` raw. The terminal acts on them: FF moves down a line, BEL rings, SO switches the character set (rusty_term honours SO at `core/src/parser.rs:1039`). The cursor then moves without the host knowing.
- **conhost:** cmd's prompt displays these as `^L`, `^N`, `^G` and expands Tab **(unverified against source)**.
- **Not recorded:** this difference isn't in §5.10.
- **Fix:** echo controls below 0x20 as `^X` the way conhost does, or record it as a V-row.
- **Regression test:** a cooked read fed `\x0c` echoes `^L`.

### 4. P2: The deferred cooked editing has no owning PR in §10
- **Where:** design.md:723. The PR 3 row now says "the cursor keys, history and popups later", but no row in §10 takes them on.
- **Why it matters:** the glossary (design.md:44) defines a cooked read as including history, and the change fragment says interactive cmd works. Today Up, Left and F7 are silently ignored in cmd.
- **Fix:** assign the work to a §10 row (PR 10, or a new row) with its estimate.

## Design questions and validation gaps
- **Focus reports go to every VT-input client (*executed*):** `ESC[I` reaches a VIRTUAL_TERMINAL_INPUT client as text even though the client never asked for focus reports. My recollection is that conhost's `InputBuffer::WriteFocusEvent` → `TerminalInput::HandleFocus` only forwards them when the client set `?1004h` **(unverified)**. If that holds, wsl.exe and ssh.exe sessions would see `^[[I`/`^[[O` on every focus change. The §5.5 table doesn't decide this case.
  - **Windows probe to settle it:** a VT-input client under ConPTY, then focus out and in, then `PeekConsoleInput`.
- **ANSI raw reads of non-BMP characters (*executed*):** RAW_READ or ReadConsoleA in raw mode returns `ef bf bd ef bf bd` for 😀 instead of its UTF-8 bytes. `read_chars` converts one UTF-16 unit at a time; the cooked path doesn't have this problem.
  - **Windows probe to settle it:** the same read under ConPTY with CP 65001. If conhost pairs the surrogates, this becomes a P2.
- **Windows-only tests not run:** `interactive_cmd_runs_typed_lines`, `ctrl_c_stops_the_running_command_not_the_shell`, the condrv `key_for` test, and clippy on the `cfg(windows)` code. These need the D4 Windows run.
- **Other conhost citations, also from memory (unverified):**
  - Ctrl+C ends only cooked reads, Ctrl+Break ends raw ones too (`readDataRaw.cpp` Notify), and GetConsoleInput ignores both.
  - Ctrl+Break flushes input (`HandleGenericKeyEvent`).
  - CLOSE_OBJECT ends reads with STATUS_ALERTED; DISCONNECT ends them with STATUS_THREAD_IS_TERMINATING.
  - `ProcessGroupId` sits at offset 28 (`CONSOLE_SERVER_MSG`).
  - The startup `?9001h ?1004h` matches `VtIo::StartIfNeeded`.
  - The PR's code agrees with all of these.

## Failure classes ruled out
- **Containment:** short descriptors return STATUS_ILLEGAL_FUNCTION; a zero-size output returns STATUS_BUFFER_TOO_SMALL; initial-data offsets are checked with `get`. Nothing tears the console down.
- **Retained memory:** a waiting cooked line reserves `limit × 2` and stays within `MAX_RETAINED_PER_CALL`. Kept line rests are capped at 512 Ki units. The ANSI caches hold at most 3 bytes and 2 records.
- **Many reads completed by one slice:** their combined data is bounded by the records they consume. Initial-data replies trade one-for-one with the parked reservation they release.
- **Custody:** if the ctrl worker can't start, `prepare_with` fails before any child exists. If the engine can't start, the worker stops (tested). The Ctrl queue is capped at 256 and coalesces.

## Optional improvements
- `GetConsoleInput` with a zero record count could carry a comment citing conhost's answer.

## Checks run
| Check | Result |
|---|---|
| `cargo test -p rusty_conhost` (Linux, head, scratch workspace) | 93 passed |
| New fixture `synthetic-input.txt` run against base code | fails at base (line 5), so it guards the change; the new unit tests depend on code that doesn't exist at base |
| `cargo clippy -p rusty_conhost --all-targets -D warnings` (Linux, clean head copy) | clean; `cfg(windows)` code not covered |
| `python3 .github/scripts/changes.py check` | 539 fragments ok |
| Probes in scratch | five probes, all reproduced the behaviour described above |
| Full workspace build, Windows tests, conhost source | not done: dependencies unavailable offline, no Windows, github.com blocked |

**Verdict: changes required.**
