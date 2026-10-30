<!-- mill-board/1238-r1 run 5, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 263 s, 1657234 tokens, $1.32 -->

I couldn't execute anything, so every trace below is source-traced and the verdict is **changes required**. The main problem is that card numbers are handed out from a snapshot under a lock that only one process holds. That gives duplicate numbers when two boards share a store, and an agent naming `#n` can then act on the wrong card.

## Review of Rusty-Mill/rusty_mill PR 1238

- **Base:** d50375a25ced7622f88810e6d3bcad8f1986bb50
- **Reviewed head:** 43e0720953f356a5a1abec4fd4da8a6a9a3032a5. `git fetch origin pull/1238/head` returned this SHA. One commit, 43e072095.

### What the diff touches, and what must hold
- **Card creation** (`create`, `import` → `insert_numbered`, board.rs:345): no two live cards share a number, and a number is never reused while its card still exists (brief's durability axis). The check must be fenced at rusty_tick, because more than one board can share a store (ADR-0012:109).
- **Startup numbering of unnumbered cards** (`number_cards` and `number_strays`, board.rs:311–342; main.rs:297): each write is fenced, and a number never changes.
- **Number lookup** (`card_ref` / `get_number`, api.rs:340, board.rs:297): every MCP tool that takes a card id resolves a number to exactly one card, or refuses.
- **Reserved tag `number:`** (domain.rs `reserved_tag`, tick.rs `card_of`/`task_tags`): users can't set it, and it survives every write.
- **UI** (`heading`, `syncDialog`) and the roadmap's `number`: display only. They read server state, so no staleness issue.

### Blocking findings

**F1 — P0: two boards hand out the same number, and `#n` then reaches the wrong card**
- **Where:** board.rs:90 (`numbering: Mutex<()>`), board.rs:320–355 (`number_strays`, `insert_numbered`), board.rs:297 (`get_number` uses `.find` and returns the first match).
- **Trigger:** two boards on one rusty_tick (supported, ADR-0012:109) each get a `card_add` (or a GitHub import) at the same time.
- **Trace (source-traced):**
  1. Board A runs `store.list()` and computes max+1 = 7.
  2. Board B runs `store.list()` and also computes 7.
  3. A sends `POST /tasks` with `number:7`, under `If-None-Match` on its own new UUID. That precondition does not cover the number.
  4. B does the same and also gets 201.
  5. Two live cards are now `#7`. The mutex only orders requests inside one process.
- **Impact:** `get_number(7)` returns whichever is older in the `(created_ms, id)` sort. An agent that read `#7` off the newer card and calls `card_move {id:"#7", to:"done"}`, `card_claim` or `card_assign` changes the other card. Done is terminal, so a wrong close can't be undone on the board. People in the UI see two `#7` cards.
- **Required correction:** take numbers from one durable counter in rusty_tick, e.g. a `board` document advanced with `If-Match` and retried on 412, before the task POST. Also make `get_number` refuse when more than one card has the number, so the lookup fails closed and names the clash.
- **Regression test:** in `tests/numbers.rs` (or `commit_order.rs`), two `Board`s on one `FakeTick` behind a gate that holds each board's first `POST /tasks` until both arrive; assert the numbers differ. A second test: two cards tagged `number:7` → `card_get #7` is refused.
- **Probe:** written as `scratch/head/crates/apps/rusty_mill_board/tests/probe_numbers.rs::two_boards_hand_out_the_same_number`, but not run (see Checks).

**F2 — P0: a number is reused while its card still exists (trash, or the `agent` tag removed)**
- **Where:** tick.rs:189–191 (`on_board` drops trashed tasks and tasks without the `agent` tag), board.rs:327–331 (next number = max over listed cards only).
- **Trace (source-traced):**
  1. The owner trashes card `#9`, the highest number, in rusty_tick.
  2. The board lists cards without it, so the next new card gets `#9`.
  3. The owner restores the task (`POST /tasks/{id}/restore`). Its `number:9` tag is intact, so there are now two `#9` cards. The F1 wrong-card impact follows.
  - Same result if the owner removes the `agent` tag and later adds it back.
- **Docs:** the change fragment (changes/2026-10-29-mill-board-card-numbers.md:21–22) lists "if the highest-numbered card is deleted in rusty_tick, its number can be handed out again" as a known limitation. In rusty_tick, `DELETE` means trash, which can be restored, so that line understates the effect.
- **Required correction:** the durable counter from F1 never goes backwards, which fixes this. Add the ambiguity refusal in `get_number`.
- **Regression test:** probe `a_trashed_card_number_is_reused_and_restore_duplicates`, and the `untagging_agent_then_retagging_duplicates` probe.

**F3 — P1: `card_add` and the GitHub import fail when another board numbers the same unnumbered card first**
- **Where:** board.rs:337–340 (`number_strays` inside `insert_numbered`), domain.rs `Card::numbered` (returns "card is already numbered"), board.rs:672–692 (`apply` retries).
- **Trace (source-traced):**
  1. An unnumbered card exists (a task tagged `agent` directly in rusty_tick).
  2. Boards A and B each get a `card_add` and both read the unnumbered card at version v.
  3. A's PATCH succeeds. B's PATCH gets 412 → `Conflict`.
  4. B's retry re-reads the card, `numbered` returns `Invalid("card is already numbered")`, and B's `card_add` fails.
- **Who is hurt:**
  - The agent gets an `Invalid` error about a card it never named and can't act on it.
  - In sync.rs:315–318 only `Conflict` is tolerated, so the whole GitHub sync run aborts.
  - The owner editing that unnumbered card on the phone can also exhaust the three retries and fail every `card_add`.
- **Required correction:** in this numbering pass, an already-numbered card means "skip it"; the next number is recomputed after the pass (or comes from the F1 counter). Numbering failures must not fail the unrelated insert.
- **Regression test:** probe `racing_strays_fail_card_add` (two boards, gate on the first `PATCH /tasks/`); both adds must succeed.

**F4 — P2: the README disagrees with the code**
- crates/apps/rusty_mill_board/README.md:30–31 still lists only `agent`, `board:…` and `claim:…` as the board's own tags.
- README.md:73 maps only column and claim to tags. `number:` is missing from both.
- There is no README mention that tools accept `#n`/`n`, nor of the new `Numbered` event kind that `board_events` consumers will see.
- **Correction:** update both lines and the tools/events text.

**F5 — P2: tags users set before this PR are reinterpreted or silently dropped**
- **Where:** tick.rs:306–316.
- **Trigger:** before this PR, `card_update` and GitHub label import accepted tags such as `number:5` or `number:x`.
- **Trace (source-traced):**
  - After upgrade, a numeric `number:5` tag becomes the card's number, which may duplicate an assigned one (F1 impact).
  - A non-numeric tag is filtered out as reserved and treated as unnumbered. The numbering write then sends a full tag list without it, so the user's tag is deleted.
- **Correction:** at startup, refuse to number (log and skip) cards whose `number:` tag the board did not assign, or migrate them explicitly. Don't drop them silently.
- **Test:** a `FakeTick` task carrying `number:x` keeps that tag after `number_cards`.

**Coverage (part of F1–F3):** every new test in `tests/numbers.rs` uses a single board. Nothing pauses two writers at the list → POST window. `tests/commit_order.rs` was not extended.

### Design questions (not blocking)
- Each card adds a unique `number:<n>` tag to rusty_tick's tag registry (`GET /tags`). The owner's phone tag list will grow by one tag per card, and rusty_tick's assistant treats `#word` as tag syntax. A field in the `board` document would avoid this, but it can't ride in the creating POST. That trade-off is the owner's call.
- The numbering events are attributed to the actor name `"board"`, which is not reserved against a real identity of that name.

### Optional improvements
- `card_ref` runs a full `store.list()` (four rusty_tick GETs) for every number. With `card_plan` deps that is up to 32 lists per call. Resolve all numbers from one list.
- `card_ref` accepts `"+5"` because `u64::from_str` takes a leading `+`.

### Ruled out
- **Scope leak:** checked and ruled out. Sequential numbers let a project-limited credential probe `#1..#N` and get `OutOfScope(project)` errors. But `board_events` already gives it every card's UUID (README:47), and `card_get` by UUID already returns the same error, so this PR adds no new leak.
- **Agent contract:** checked and ruled out. `card_take` still resolves the number to a UUID and rechecks inside the CAS. Done cards stay on the board, so they count toward the maximum. `rusty_orch` (millboard.rs) does not parse event kinds.

### Checks
| Check | Result |
|---|---|
| `python3 .github/scripts/changes.py check` | passed ("529 fragment(s) ok", exit 0) |
| `cargo test -p rusty_mill_board` | **not run** |
| clippy | **not run** |
| Probe tests (4, scratch copy) | **not run** |
| New tests at base | **not run**. They can't pass there anyway: `Card::number` and `get_number` don't exist at base, so they won't compile. |

The tests couldn't build:
- Pinned toolchain 1.98.1 is installed as stable, but `--offline` resolution fails. `boxcars` and `tokio 1.53.3` are missing from the local cache.
- Fetching crates.io was denied by the sandbox network policy. Whether to allow it is your call.

### Validation gaps
- All of `cargo test`, clippy and the probes in `scratch/head/.../tests/probe_numbers.rs`. Running them online settles F1–F3 as executed traces.
- A browser check of the `#n` corner badge and `issue #n` text.
- A two-board run against a live rusty_tick.

### Verdict: **changes required**
