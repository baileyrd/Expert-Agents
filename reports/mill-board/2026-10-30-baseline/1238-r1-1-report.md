<!-- mill-board/1238-r1 run 1, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 328 s, 1872338 tokens, $1.47 -->

I couldn't run any Rust check, so this review is source-traced only, and its verdict is **changes required**. The main problem: the card number is handed out under a lock inside one board process, while rusty_tick (the store the boards share) does nothing to stop two cards getting the same number. `#N` then quietly points at the wrong card.

## Pin
- Repository Rusty-Mill/rusty_mill, PR **1238**
- Base `d50375a25ced7622f88810e6d3bcad8f1986bb50`
- Reviewed head `43e0720953f356a5a1abec4fd4da8a6a9a3032a5`. The fetched `pull/1238/head` equals this SHA. It is one commit, `43e072095 feat(mill-board): give every card a board-wide number`.

## What the diff touches, and what must hold
- **Card creation** (`create`, `import` → `insert_numbered`, board.rs:345-356): each live card gets a unique number that never changes. This is the PR's own claim (domain.rs:518, change fragment). The README also requires that "an identifier is never reused while its owner still exists".
- **Numbering existing cards** (`number_strays` / `number_cards`, board.rs:311-342, main.rs:297-303): each write is fenced with If-Match (ADR-0012 §2), and numbering one card never breaks writes to unrelated cards.
- **`card_ref` in 11 MCP tools** (api.rs:340-347): a number names exactly one card; scope and capability checks stay as they were.
- **Mapping to rusty_tick tags** (tick.rs:279, 306-310): `number:` is reserved, and no tag on a card is silently lost.
- **UI** (ui.html:173, 185, 193, 226, 308) and **roadmap** (roadmap.rs:58, 135): display only, built from the server's card.

## Blocking findings

### 1. P0: two boards on one rusty_tick give two cards the same number, and `#N` then names the wrong card
- **Where:** board.rs:89-90 (a `numbering: Mutex<()>` that lives in one process), board.rs:326-331 (`next = max + 1` from `store.list()`), board.rs:350-355 (unfenced `insert`), tick.rs `insert` (a plain `POST /tasks`).
- **Trigger:** two boards share one store and create cards in the same window. That window runs from `list` to `POST`, and includes any numbering of older cards.
- **Trace (source-traced):**
  1. Board A runs `card_add`, takes its local lock, lists the store and computes `next = 7`.
  2. Board B does the same: its own lock, sees the same max, `next = 7`, and POSTs the task with `number:7`.
  3. Board A POSTs its task with `number:7`.
  4. Nothing in rusty_tick checks the number, so both succeed and the UI shows `#7` on two cards.
  5. `get_number` (board.rs:311-317) returns the first match in `(created_ms, id)` order. An agent that reads `#7` off the newer card and calls `card_move {id:"#7", to:"done"}`, `card_claim` or `card_comment` acts on the older card. Done is terminal, so a wrong close cannot be undone.
- **Who is hurt:** agents over MCP, people reading `#n`, and the other board.
- **Same race on older cards:** A numbers one unnumbered card as 7 while B numbers a different one as 7.
- **Required correction:** fence the number at rusty_tick.
  - Option 1: keep a monotonic counter document written with If-Match, or a per-number claim document written with `If-None-Match: *`, then POST. On 412, re-read and retry.
  - Option 2: if no rusty_tick change is allowed, write a fenced reservation first and check uniqueness before handing the number out.
  - "Needs no rusty_tick change" (change fragment, ADR row) cannot hold for a unique number shared by several writers. That trade-off is the owner's call, but the duplicate is a defect either way.
- **Regression test** (tests/numbers.rs or tests/commit_order.rs):
  - Board A wraps the shared `FakeTick` in a `TickApi` that pauses before A's first `POST /tasks`.
  - During the pause, board B on the same `FakeTick` runs `add`.
  - Assert the two numbers differ.
  - I wrote this probe as `scratch/head/crates/apps/rusty_mill_board/tests/probe_numbers.rs::two_boards_hand_out_the_same_number`, but couldn't run it (see Checks).

### 2. P0: a number is reused while its card still exists, and the change fragment says otherwise
- **Where:** tick.rs:189-191 and 483-491 (`list` skips trashed tasks and tasks without the `agent` tag), board.rs:326-331 (`max + 1` over that list), and the change fragment's "Known limitations" line.
- **Trigger:** the owner trashes the highest-numbered card in rusty_tick, or removes its `agent` tag. Both can be undone (rusty_tick README:69 lists `/restore`).
- **Trace (source-traced):**
  1. The owner trashes `#9`. Its task keeps the `number:9` tag.
  2. Someone runs `card_add`: the list no longer includes `#9`, so the new card becomes `#9`.
  3. The owner restores the trashed task, and two live cards carry `#9`.
  4. `get_number(9)` now resolves to the restored, older card. An agent that has been working on "#9" silently starts acting on a different card.
- The fragment calls this "deleted". In rusty_tick, delete is a reversible move to the trash, so the stated limitation is false.
- **Required correction:** use a high-water mark that never goes down, such as the fenced counter from finding 1, instead of `max(live numbers) + 1`. Fix the fragment either way.
- **Regression test:** with `FakeTick`, number cards 1 to 3, set `deletedMs` on 3, add a card, clear `deletedMs`, and assert all numbers are distinct.

### 3. P1: when two writers number the same old card, `card_add`, `import` or startup fails with a misleading error
- **Where:** domain.rs:522-524 (`numbered` errors when the card already has a number) and board.rs:337-339 (any error from numbering an old card aborts `insert_numbered`).
- **Trigger:** two writers number the same unnumbered card.
- **Trace (source-traced):**
  1. A task has the `agent` tag but no number.
  2. Board A's `card_add` calls `number_strays`, which reads that task.
  3. Board B (its own `card_add`, or its startup `number_cards`) PATCHes it to `number:5` first.
  4. Board A's PATCH gets 412 → `Conflict`. `apply` retries, re-reads the card, and `numbered()` now returns `Invalid("card is already numbered")`.
  5. Board A's `card_add` fails with an "invalid" error about a card the caller never named. Board A's own card is not created.
- Startup `number_cards` on the second board also fails; it only logs a warning (main.rs:300).
- Even with a single board, any persistent failure numbering one old card (a run of conflicts, or a rusty_tick 5xx on that task) blocks every `card_add` and GitHub import.
- **Who is hurt:** an agent over MCP, which reads "invalid" and stops; and a person in the UI.
- **Required correction:**
  - In the numbering closure, return the card unchanged if it already has a number. `apply` already treats `next == current` as a no-op.
  - Don't let a failure on an unrelated card fail the caller's create: number what you can, log the rest, and still insert.
- **Regression test:** same pausing wrapper as finding 1, paused before A's `PATCH /tasks/` while B runs `number_cards()`. Assert A's `add` succeeds. This is probe `second_board_numbering_a_stray_breaks_card_add`, not run.

### 4. P1: existing user `number:` tags are silently dropped or turned into the card's number
- **Where:** tick.rs:306-310 (the first `number:` tag becomes the card's number, and a value that doesn't parse becomes `None`), tick.rs:311-316 (all `number:*` tags are removed from `card.tags`), tick.rs:279 (rewrites tags on the next write).
- **Background:** at base, `reserved_tag` did not include `number:`, so `card_update` and GitHub label import could store such tags.
- **Trace (source-traced):**
  - A card tagged `number:abc` loses that tag from the UI and MCP at once. The startup numbering PATCH then rewrites the task's tags without it, deleting it in rusty_tick with no log line.
  - Two cards tagged `number:1` both become `#1`, which leads to the same wrong-card resolution as finding 1.
- **Required correction:** at startup, find cards whose `number:` tags don't parse or are duplicated, and refuse or report them rather than dropping them. Document the migration in the fragment.
- **Regression test:** a `FakeTick` task with tags `["agent","number:abc"]` keeps a visible record of that tag after `number_cards`, or `number_cards` refuses. A second test: two tasks tagged `number:1` don't both resolve to `#1`.

### 5. P2: the README's reserved-tag list is now wrong
- README.md:30-32 says "The tags `agent`, `board:…` and `claim:…` are the board's own … the GitHub sync drops such labels". The code now also reserves `number:` and sync.rs:310 drops it.
- README.md:73 describes the tag mapping without `number:`.
- **Correction:** add `number:` to both places.

### 6. P2: missing tests for changed behaviour
- Only `card_get` is tested with a number (api.rs:654-674).
- Untested: `card_take {id:"#n"}`, `card_plan` dependencies given as numbers, and every two-writer path (findings 1 and 3).
- **Where they belong:** api.rs unit tests for the tool arguments; tests/numbers.rs or commit_order.rs for the two-board traces.
- The new tests can't compile at base, since the `number` field doesn't exist there, so they do guard the single-board behaviour. I couldn't run them at either SHA.

## Failure classes with no finding
- **Two-step write:** the number goes out in the same `POST` as the task, and numbering an existing card is one fenced PATCH. No new two-request write.
- **Scope leak:** `card_ref` only turns a number into an id. Scope is still checked on the card (`card_get`, and inside `apply`). Sequential numbers don't reveal anything new: at base, `board_events` already shows other projects' card ids (README:47), and `OutOfScope` already named their project.
- **Token races:** the diff touches no authentication code.
- **UI out of step:** `#n` is drawn from the same server card object that every poll replaces; the UI keeps no new state.
- **Board rules:** columns, claims and capabilities are unchanged. The numbering write only changes the number. Done cards stay Done (only tags are patched). `number:` is refused by `check_tags`.

## Design questions
- A unique, never-reused number needs a fenced counter in rusty_tick. That contradicts the PR's "no rusty_tick change". The owner has to choose between that and a weaker guarantee that is documented honestly.
- The owner can edit or delete the `number:` tag from the phone, which breaks "a number never changes". ADR-0012 accepts the same for `board:` and `claim:`; is that acceptable for an identifier?

## Optional improvements
- Each number lookup does a full `store.list()`, which reads every task, board document and comment. `card_plan` with 32 dependencies does 32 of these.
- `card_add` now always does a full list while holding the registry and numbering locks.
- Numbering many old cards at startup is sequential and happens before the board serves requests. It is O(N²) in documents fetched, because each `get()` lists every board document and comment.
- An unknown number returns `Invalid` while an unknown UUID returns `NotFound`. Returning `NotFound` for both would give agents one signal for "no such card".

## Checks
- `python3 .github/scripts/changes.py check`: **passed** ("529 fragment(s) ok").
- **Could not run** `cargo test -p rusty_mill_board`, the probe tests, the base-SHA runs or clippy:
  - The pinned toolchain 1.98.1 is available as stable, but the lockfile's crates (tokio 1.53.3, rustls 0.23.46, …) aren't in the local cargo cache.
  - crates.io is denied by the sandbox network rules.
  - In the scratch copy I trimmed the workspace and re-resolved against the cache. The build still needs `dyn-clone` (pulled in by `schemars`), which isn't cached.
  - To settle these, run the full suite plus the three probes in `scratch/head/crates/apps/rusty_mill_board/tests/probe_numbers.rs`, and the new tests at base, somewhere with registry access.
- **Not checked:** the browser rendering of `#n` and CI. There is no live two-board deployment to test against.

## Verdict
**Changes required**
