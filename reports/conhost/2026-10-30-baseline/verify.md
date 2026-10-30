# conhost/1246-r1 claim verification

Graded blind against `bench/cases/conhost/1246-r1/answer_key.toml`. The worktrees were at head
`197fae02`, at the fix `8ec54b85` and at origin/main `ad1c3e37`, all in the grader's scratchpad and
removed afterwards. Probes were `#[cfg(test)]` modules appended to `src/console/read.rs`, using the
crate's own `Host` test harness, and the file was restored after each run. `cargo test -p
rusty_conhost --lib` gives 93 passed at the head on Linux. Conhost sources come from
microsoft/terminal at the pinned `09beddc6`, fetched raw from GitHub. The human review is
https://github.com/Rusty-Mill/rusty_mill/pull/1246#pullrequestreview-5706594467, read through the
GitHub API, together with the PR's review comments.

## Key K3

**The key is correct. I made no change to what it requires, and added only a clarifying comment.**

**Head probe.** U+1F600 was typed as UTF-8, and separately sent as two win32-input-mode records
D83D and DE00:

| Path | Head `197fae02` | Fix `8ec54b85` |
|---|---|---|
| Cooked echo (`cooked.rs:79`, `push_units(echo, &[ch])`) | `ef bf bd ef bf bd 0d 0a` | `f0 9f 98 80 0d 0a` |
| Cooked read reply (`consume` on the whole line) | correct, `3d d8 00 de` / `f0 9f 98 80` | correct |
| ANSI raw read (`read.rs:515`, `consume(.., &[ch], ..)`) | `ef bf bd ef bf bd` | `f0 9f 98 80` |
| ANSI GetConsoleInput records (`read.rs:582`) | `ef bf bd ef bf bd` | `f0 9f 98 80` |
| VT-input client, pair records (`input.rs:550` `vt_for`) | `"\u{fffd}\u{fffd}"` | `"😀"` |
| VT-input client, UTF-8 typed | `"😀"` (passes through raw) | `"😀"` |

**Why three runs called the cooked path correct.** Runs 1 and 3 looked at the cooked read's
*returned text*, and that text is correct. The echo that the terminal shows is the part converted
per unit. Run 2's "a non-VT client gets 😀 correctly" refers to records, not to echo. None of these
statements contradicts the key.

**What the fix changed.** Commit 8ec54b85 adds `input::Utf8Units`, which holds a high surrogate
until its low half arrives. It is used in all four places: the cooked echo, raw ANSI reads (a high
half is held for its low half), ANSI GetConsoleInput records, and win32-to-VT conversion. A lone
half becomes U+FFFD. The commit message says so in its third bullet.

**The human review.** Dottius's item 3 ([P2]) names `cooked.rs Line::char`, `read.rs
read_chars/read_records` and `input.rs vt_for`, which is three files. The key's three paths (echo,
ANSI reads, vt_for) follow those files, with read_chars and read_records grouped together as "ANSI
reads". The summary lists four conversions and the caught_if lists three paths, so the two are
consistent.

**The 2-of-3 bar.** The bar is fair. Each path is a separate site, and naming one of them is
partial credit. I added a TOML comment to the key, with no change to its semantics. The comment
records that read_chars and read_records count as one path, and that "the cooked path is correct"
refers to the reply and so does not refute the echo path.

**Grades.**
- Run 5 F4 names echo and ANSI reads, so it is caught.
- Run 1 F3 names ANSI raw reads and records, which is one path, so it is partial.
- Run 2 F5 names vt_for only, so it is partial.
- Runs 3 and 4 name ANSI raw reads only in non-blocking sections, so they are not graded. Each run
  file has a comment saying so.

## K1 and K2 (key matches)

- **K1:** runs 1–4 caught it. Each one names the cooked echo pushed with no budget check
  (`read.rs:462-464`) as able to exceed 144 KiB in one handle call.
- **Run 5 misses K1.** Its "Amplification" failure class claims the echo stays within the cap.
- **K2:** all five runs caught it. Each names the 16 KiB slice boundary right after ESC
  (`input.rs:238-242`) becoming Escape plus text.

## Ctrl+C / Ctrl+Break doc mismatch (r1 F4, r2 F7, r4 F3, r5 F6 part)

**Class: real.** design.md:403 says "Nothing is queued" and :405 says "Never queued". At the head,
`intercept` (`read.rs:140-161`) acts on key-downs only, so the key-up is stored. The PR's own test
`ctrl_c_goes_to_every_client_and_ends_cooked_reads` asserts "Only the key-up is queued". Both
design lines are unchanged on origin/main.

## Missing required tests (r1 F5, r2 F8, r4 F4, r5 F6 part)

**Class: real.** Design §7.4 (design.md:634) lists "cooked echo under sustained terminal input" for
the stalled-reader tests. At the head, the only high-water assertions are the write tests at
console.rs:1736-1766. §7.2 (design.md:574) asks for these, and neither is present:
- a Windows PeekConsoleInput/ReadConsoleInput loop that checks the input event;
- `hang_up` while a ReadConsole is pending. `tests/own_pty.rs:310`
  `hang_up_closes_the_console_and_releases_waiters` runs `ping`, with no pending ReadConsole.

`examples/conhost_probe.rs` has no input subcommands. The fix commit later added a cooked-echo
reserve test, which supports that the omission was real.

## r2 F2: read reply not capped at 1 MiB

**Class: fixed_since.**
- **Head probe:** a raw ReadConsoleW with a 16 MiB buffer, then 16 records of
  `ESC[65;30;97;1;0;65535_` (336 B), completed with a 2,097,120 B reply.
- **Cause:** `capacity` (`read.rs:357`) is the client's output size, never clamped, and
  `pop_stream` expands the repeat count.
- **Fix:** Codex flagged the same unbounded reply building as P1 on 8ec54b85. It was fixed in
  615e0824: `READ_REPLY_CAP` (main `read.rs:35`, used at :324 and :388), and `serve_reads` is now
  budgeted per call.

## r2 F4: focus reports reach VT-input clients that never enabled them

**Class: real.** Run 2 graded it P1. My judgement is P2-level, since it shows only as stray
`[I`/`[O` text.
- **Head probe:** a client with VIRTUAL_TERMINAL_INPUT reads `ESC[I`. Under VT input,
  `read.rs:100-104` passes every token through raw, Focus included.
- **Conhost at 09beddc6:** `InputBuffer::WriteFocusEvent` (inputBuffer.cpp:585-600) calls
  `TerminalInput::HandleFocus`, and that returns nothing unless the client's own FocusEvent mode is
  set (terminalInput.cpp:420-427).
- **Design:** §5.5 says the host emits `?1004h` "as inbox conhost does" and does not decide focus
  delivery. So this is a parity defect and not a documented choice.
- **On main:** unchanged (main `read.rs:112`).
- Runs 1, 3 and 4 raised this only as a design question, so it is not graded for them.

## Second cooked read overwrites a kept line rest (r2 F6, r5 F3)

**Class: real.** Unchanged on origin/main (main `read.rs:755-758`).
- **Head probe (run 5's trace):** read A (4 B) and read B (64 B) both wait. Input `abcdef\r` gives
  A "ab". Then `x\r` gives B "x\r\n". A third read waits, and "cdef\r\n" is gone.
- **Cause:** `finish_line` always calls `keep_pending` (`read.rs:497-502` and `:649-653`), which
  replaces the rest even with an empty one.
- **Conhost:** `_handlePostCharInputLoop` saves only when `!input.empty()`
  (readDataCooked.cpp:811-816), so run 5's trace diverges from conhost.
- **Caveat on run 2's trace:** run 2 used two reads with 2-unit buffers, so both rests are
  non-empty. In that case conhost's `SavePendingInput` (inputReadHandleData.cpp:33-42) also
  replaces, which means run 2's exact scenario matches conhost. I graded it real anyway, because
  it is the same unconditional replace and the same defect. A stricter grader could call r2 F6
  conhost parity.

## r3 F3: cooked echo emits C0 controls raw

**Class: real.** Unchanged on main.
- **Head probe:** typing `a`, ^L, ^N, ^G at a cooked read echoes `61 0c 0e 07` (`cooked.rs:78-81`).
- **Conhost at 09beddc6:** renders C0 controls in the edit line as `^` + (ch + '@'), and Tab as
  spaces (readDataCooked.cpp:1349-1366).
- **Design:** not recorded as a divergence.

## r3 F4: cursor keys, history and popups have no owning PR in §10

**Class: real.** A documentation gap. design.md:723 defers them as "later", and no row in §10
(design.md:719-735) takes them. The glossary (design.md:44) includes history in a cooked read.
Unchanged on main (design.md:747).

## r5 F1: a paste past 4,096 records drops its start even with a read waiting

**Class: design_question.**
- **Head probe:** a 6,000-character paste plus CR into a waiting cooked read with a 32,000 B buffer
  returned 4,096 units and logged `InputDropped`.
- **Cause:** `terminal_input` builds every record before `serve_reads`.
- **Why a design question:** design.md:499 sizes the 4,096-record cap explicitly for "fast typing
  or paste", with "oldest dropped, logged once", and §7.4 (design.md:610) keeps it. The behaviour is
  what is documented. Whether a waiting read should drain the records first is a design call.

## r5 F5: `ESC ESC [A` decodes as Alt+Esc plus the text `[A`

**Class: real, low impact.** Unchanged on main (`escape` still maps an ESC-then-C0 pair to Alt).
- **Head probe:** GetConsoleInput returns Alt down, Esc up, Alt up, then the text records `[` and
  `A`.
- **Cause:** `escape` (`input.rs:251`) maps ESC followed by a C0 to Alt+that, and `intercept`
  (`read.rs:160`) drops the Alt+Esc down.
- **Why it is wrong:** neither reading of the input, Escape then Up or Meta+Up, yields literal
  `[A`.
- **Impact:** only terminals that do not speak win32-input-mode send this.
