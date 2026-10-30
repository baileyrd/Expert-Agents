<!-- mill-board/1238-r1 run 4, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 211 s, 1313561 tokens, $1.05 -->

# Review of Rusty-Mill/rusty_mill PR #1238: board-wide card numbers

**Verdict: changes required.** The new card numbers are not unique once two boards share one rusty_tick store. Because every MCP tool now accepts `#n`, a move, claim, comment or Done can land on the wrong card. I could not build or run any tests, so every trace below comes from reading the code.

- **Base:** `d50375a25ced7622f88810e6d3bcad8f1986bb50`
- **Reviewed head:** `43e0720953f356a5a1abec4fd4da8a6a9a3032a5`. `git fetch origin pull/1238/head` returned this same SHA.
- **Diff:** one commit, `feat(mill-board): give every card a board-wide number`.

## What the diff touches, and what must hold
- **Writes:** `insert_numbered` (behind `add_in`, `add_done_in` and `import`), `number_strays` / `number_cards` (a new `Numbered` event, run at startup and before every new card), and `Card::numbered`.
  - A number is unique across every board and writer on the store, and it never changes.
  - Every read-derive-write is fenced at rusty_tick. ADR-0012 §2 says "Two boards (or a board and a stray client)" share the store.
- **Reads:** `get_number` and `card_ref` (`#n` or `n` on every op that takes an id, over both `/api/<op>` and `/mcp`), and `card_of` parsing `number:`.
  - A name must point at exactly one card, or the call is refused.
- **Reserved tags** (`domain.rs` `reserved_tag`): `number:` can't be set by a card. The diff does this correctly.
- **UI:** `heading()` on the board and roadmap, and the dialog meta line. Display only; it reads `number` from the same poll as the rest of the card. Nothing found.

## Blocking findings

### 1. P0: numbers come from a lock inside one process, so two boards hand out the same number, and `#n` then acts on whichever card is listed first
- **Where:** `src/board.rs:90` (`numbering: Mutex<()>`), `:328-331` (next = highest number + 1, read from a list), `:345-353` (insert under that number), `:297-302` (`get_number` takes the first match), `src/api.rs:340-347`.
- **Trigger:** two boards on one rusty_tick, which ADR-0012 allows, each create a card at about the same time.
- **Trace (source-traced):**
  1. Board A runs `card_add`. It takes its own lock, and `number_strays` lists the cards: the highest number is 5, so the new card gets 6.
  2. Board B runs `card_add` and takes *its* own lock. Its list still has 5 as the highest, so its card also gets 6.
  3. Both POSTs succeed. `If-None-Match` only guards the task id, and the two ids differ.
  4. Now two cards carry `number:6`. An agent calls `card_move {"id":"#6","to":"done"}` (or `card_take`, `card_claim`, `card_comment`, or `card_plan` with `deps:["#6"]`).
  5. `card_ref` resolves to whichever card comes first in `store.list()` and the write goes ahead without complaint.
  6. Done is terminal, so the wrong card is closed for good. The other card stays open.
- **Who is hurt:** agents over MCP, the person in the UI (who sees two `#6` cards), and the other board.
- **Same root cause, smaller trigger (source-traced):** the board never checks that numbers are unique, and `n + 1` is unchecked.
  - If the owner tags a task `number:18446744073709551615` in rusty_tick, `map_or(1, |n| n + 1)` wraps to 0 in release builds (the workspace sets no `overflow-checks`). Every later card then gets `#0`. In a debug build, every `card_add` panics instead.
  - If the owner copies a `number:6` tag onto another task, two cards are #6 and `get_number` again picks one without saying so.
- **Required correction:**
  - Fence number allocation at rusty_tick. For example, keep a counter document that the board advances with `If-Match` before the POST and retries on 412; or claim each number with `If-None-Match: *`. A process-local mutex is not enough.
  - In `get_number`, refuse when more than one card has the number ("number is ambiguous; use the id"). Never pick one.
  - Use `checked_add` and refuse when the number is out of range.
- **Regression tests:**
  - In `tests/numbers.rs`: two `Board`s on one `FakeTick` (or a store wrapper that pauses `list` on a barrier). Both `add` at the same time; assert the numbers differ. This goes red at the head.
  - A unit test: two cards tagged `number:6`, then `card_get "#6"` must be refused.
  - A unit test: `number:u64::MAX`, then `add` is refused rather than numbered 0.

### 2. P1: when two boards number the same unnumbered card at once, `card_add` fails with a misleading "card is already numbered" error
- **Where:** `src/board.rs:338`, `src/domain.rs:522-525`.
- **Trigger:** an unnumbered card exists (for example, a task the owner tagged `agent` on the phone), and two boards both try to number it.
- **Trace (source-traced):**
  1. Boards A and B both list the cards and both see unnumbered card X.
  2. A applies `Numbered` and X becomes #6.
  3. B's `apply` re-reads X and calls `c.numbered(next)`. That returns `Err(Invalid("card is already numbered"))`.
  4. The `?` turns this into the reply to B's `card_add` (or `import`). The agent gets an `Invalid` refusal for a card it never named, and it cannot tell that a retry would work.
  5. The same race at startup only logs a warning (`main.rs:299`).
- **Required correction:** skip a card that has gained a number since the list was read (return it unchanged, so `apply` returns Ok), then recompute the next free number. This ties into the fix for #1.
- **Regression test:** in `tests/numbers.rs`, board B lists, board A numbers the unnumbered card, then B's `add` must succeed with a distinct number.

### 3. P2: the board's README no longer matches the code
- **Where:** `crates/apps/rusty_mill_board/README.md:30-31`, which still says "The tags `agent`, `board:…` and `claim:…` are the board's own", and `:73`, which lists the storage mapping without `number:`.
- **Effect:** `card_update` now also refuses `number:…`, and the ADR table in this same PR says so. The README, which the PR did not touch, does not.
- **Required correction:** update both lines.
- **Regression check:** none beyond a doc review.

## Design questions (not blocking)
- **Numbers can be reused.** The change fragment documents that deleting the highest-numbered card in rusty_tick lets its number be handed out again. An agent holding an old `#n` (in a comment or a runner log) would then act on a different card. The fix for #1 removes this for free: a counter stored in rusty_tick only ever goes up.

## Optional improvements
- **Speed:** `get_number` lists every card for each `#n` lookup, and every new card lists every card twice. On `TickStore` that is a full scan per call.
- **Clearer error:** an unknown number returns `Invalid("no card has that number")`, while an unknown UUID returns `NotFound`. Using `NotFound` for both would be more consistent for agents.
- **Stricter parsing:** `u64` parsing accepts `+5` in a `number:` tag and as `card_ref` input.

## Checked and ruled out
- **Two-step write:** the number goes into the same POST that creates the task. A lost reply on `import` hits `If-None-Match` and uses up no number. `card_add` retries could already duplicate before this PR, so this PR adds nothing there.
- **Scope leak:** `card_ref` resolves the number before the scope check, and an out-of-scope result then gives `OutOfScope(project)`. A scoped credential could already learn the same thing from `board_events` (README:47 documents this), so it is not new. Writes still check `may_work_in` inside the compare-and-swap (`board.rs:675`).
- **Board rules:** the internal "board" actor that numbers cards has no capabilities, and `apply` only checks project scope. `Numbered` never changes the column or claim. Done stays terminal.
- **Token races:** none touched.
- **UI out of step:** the number is read from the same poll data as the rest of the card, so no new staleness path.

## Checks and validation gaps
- `python3 .github/scripts/changes.py check`: **passed** ("529 fragment(s) ok").
- `cargo test -p rusty_mill_board` and `cargo clippy`: **not run**. The pinned toolchain (1.98.1) is installed, but a build in a scratch copy failed with `no matching package named boxcars`. Fetching it needs `index.crates.io`, which the sandbox refused (denied by you), and the local cargo cache doesn't have it. I therefore did not run the PR's new tests (`tests/numbers.rs`, `api.rs::a_card_is_named_by_id_or_by_number`) at the head or at the base, and I did not execute the two-board probe.
- **To close the gaps:** run the full crate tests and clippy with registry access, run the two-board barrier test from #1, and do a browser check of the `#n` heading and the `issue #n` label.

**Verdict: changes required.**
