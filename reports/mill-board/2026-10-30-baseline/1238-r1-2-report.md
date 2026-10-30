<!-- mill-board/1238-r1 run 2, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 214 s, 1276522 tokens, $1.10 -->

I couldn't finish the executed part of this review: the build is blocked. Verdict: **changes required**, because the source traces below are enough on their own.

**PR:** Rusty-Mill/rusty_mill #1238 · base `d50375a25ced7622f88810e6d3bcad8f1986bb50` · head `43e0720953f356a5a1abec4fd4da8a6a9a3032a5`. I fetched `pull/1238/head` and it matches the head SHA. It is one commit, `43e072095`.

## Invariants for what the diff touches

- **New card number** (`add_in`, `add_done_in`, `import` → `insert_numbered`): no two cards that exist on one store share a number, even with several writers. A process-local lock is not enough when two boards share a rusty_tick (the review brief; ADR-0012 commit protocol, `tick.rs:14-25`).
- **Numbering existing cards** (`number_cards` at startup, `number_strays` before every insert): fenced per card by `apply`'s check-and-write. A conflict must not make an unrelated write fail.
- **`card_ref` on every MCP tool and the HTTP ops** (`card_get`, `card_take`, `card_move`, `card_claim`, `card_release`, `card_update`, `card_plan` and its dependencies, `card_assign`, `card_comment`, `bind_execution`, `recover_execution`): a number names exactly one card. A scoped credential "hears of no other project" (`api.rs:1011`).
- **Reserved tag `number:`**: `card_update` and imports refuse it (`domain.rs:204-218`; `sync.rs:310` uses `reserved_tag`). This holds.
- **UI** (`ui.html` card heading, dialog meta, roadmap): shows the server's `number`; dragging and actions still use `id`. This holds.
- **Event `Numbered`**: shows up in `board_events` with actor `board`. With a rusty_tick store, another board sees it as `Edited` by `tick`. This is acceptable.

## Blocking findings

**1. P0: Two boards on one rusty_tick hand out the same number, and `#n` then acts on the wrong card.**
- **Where:** `board.rs:90` (a process-local `numbering: Mutex<()>`), `board.rs:326-331` (the next number is the highest from a `list()` snapshot), `board.rs:345-355` (POST with no fence on the number).
- **Trigger:** two boards share one store (a supported setup). The same failure also occurs with a single board in two cases, both covered in finding 2.
- **Trace (source-traced):**
  1. Board A runs `card_add`: it takes its own lock, `list()` shows the highest number is 6, so it picks 7.
  2. Board B runs `card_add`: its own lock, the same `list()`, also 7. B posts its task with `number:7`.
  3. A posts its task with `number:7`. Nothing in the POST checks the number, so both succeed.
  4. `get_number(7)` (`board.rs:297`) returns the first card in `list()` order (oldest first).
  5. An agent that was told "#7" by board A then runs `card_update {"id":"#7", body…}`, `card_move`, `card_take` or `card_plan` deps, and these change board B's card. A title or body overwrite corrupts a card. A wrong claim or move breaks the hand-off. Both a person in the UI and an agent over MCP are hurt.
- **Probe:** `scratch/head/crates/apps/rusty_mill_board/tests/probe_numbers.rs::two_boards_hand_out_the_same_number` hooks A's `POST /tasks` to run B's `add` first. I wrote it but couldn't run it (see Checks).
- **Fix:** allocate the number under a fence at rusty_tick that every board shares. One option is a counter document written with `If-Match`, or created with `If-None-Match: *` per number, and only then the task POST. A second option: give up on uniqueness and have `card_ref` refuse any number that matches more than one card.
- **Regression test:** the two-board probe above, in `tests/numbers.rs` or `tests/commit_order.rs`.

**2. P0: A number is reused while the card that holds it still exists.**
- **Where:** `tick.rs:189-191` (`on_board` hides cards in the trash and cards without the `agent` tag) together with `board.rs:326-331` (which only counts the numbers it can see).
- **Trace (source-traced):**
  1. Card #9 is the highest number. The owner moves it to rusty_tick's Trash. The rusty_tick README (line 69) says `DELETE` moves a task to the trash and it can be restored.
  2. The next `card_add` gets #9.
  3. The owner restores the first card. Two live cards are now #9, with the same wrong-card outcome as finding 1.
  4. The same thing happens if the owner removes `agent` from the task and adds it back later: the `number:9` tag stays on the task.
- **Docs:** the change fragment's "Known limitations" says this only happens when the card is "deleted". A card in the trash still exists, so the fragment understates the risk.
- **Probe:** `probe_numbers.rs::a_trashed_cards_number_is_reused_and_duplicated_on_restore` (written, not run).
- **Fix:** keep the high-water mark in durable, fenced storage (the same fix as finding 1) so it never goes back down. Also detect duplicates when resolving `#n`.

**3. P1: Numbering racing on another writer makes an unrelated `card_add` or import fail with a misleading error.**
- **Where:** `board.rs:338`, which uses `?` so any failure ends the call; `domain.rs:522-524`.
- **Trace (source-traced):**
  1. An unnumbered card S exists. Board B runs `card_add`; its `number_strays` lists S.
  2. Board A, just starting (`main.rs:299`) or adding its own card, numbers S first.
  3. B's PATCH on S gets 412, so `apply` retries. The retry's `get` sees S already numbered, and `numbered()` returns `Invalid("card is already numbered")`.
  4. B's `card_add` returns that error. An agent over MCP sees "invalid" for a request that was valid, and may stop instead of retrying. The GitHub sync's import fails the same way.
- **Probe:** `probe_numbers.rs::a_concurrent_numbering_fails_the_other_boards_card_add` (written, not run).
- **Fix:** when numbering a card that is already numbered, treat it as done (return the card unchanged), and recompute the next free number after a conflict.
- **Regression test:** that probe.

**4. P1: Scoped credentials can enumerate the names of projects they cannot use.**
- **Where:** `api.rs:340-346`; the messages come from `domain.rs:393` and `api.rs:488`.
- **Trace (source-traced):**
  1. A credential limited to `rl` calls `card_get {"id":"#1"}`, then `"#2"`, and so on.
  2. A missing number returns `no card has that number`. An existing card in another project returns `project "secret" is outside your scope`. `card_move`, `card_take` and the other tools do the same through `apply` and `take_in`.
  3. Before this change you needed an unguessable UUIDv4 to get that error. Now a simple loop reveals every hidden project's name and how many cards it has. That breaks "a limited credential hears of no other project" (`api.rs:1011`).
- **Fix:** resolve numbers only among cards in the caller's scope, and give the same "no card has that number" reply for cards outside it.
- **Regression test:** in the `api.rs` tests, next to `a_credential_limited_to_projects_sees_and_takes_only_those`, assert that `#n` for an out-of-scope card gives exactly the not-found reply. My probe is the `probe` module appended to the scratch copy of `api.rs`.

**5. P2: The README still lists the old reserved tags and storage mapping.**
- **Where:** `README.md:30-32` lists `agent`, `board:…` and `claim:…` as the board's own tags; `README.md:73` describes the storage mapping. Neither mentions `number:` or card numbers. The ADR and the code now reserve `number:`.
- **Fix:** update both passages.

**6. P2: The tests don't cover concurrency, and none was shown to fail at base.**
- `tests/numbers.rs` drives only one board, so nothing exercises findings 1–3.
- **Fix:** add a two-board `FakeTick` test that pauses at the POST or PATCH, plus a trash-then-restore test.

## Design questions
- **Not found vs. invalid:** an unknown number returns `Invalid` (HTTP 400), but an unknown UUID returns `NotFound`. An agent can't treat the two the same way. Is the mismatch intended?
- **Cost per write:** every `card_add` and every GitHub import now does a full `list()` (tags, board docs, all comments) under a lock before it writes. Every `#n` lookup does another full `list()`, so `card_plan` with up to 32 dependencies can do 32 of them. A simpler option is to allocate numbers from a fenced counter and look them up by tag (`/tags/number:n/tasks`).

## Optional improvements
- `number_strays` stops at the first failure, which also fails the new card being added. Numbering leftover unnumbered cards could happen in the background, so a card that can't be numbered doesn't block `card_add`.

## Checks
- **`python3 .github/scripts/changes.py check`:** passed ("529 fragment(s) ok").
- **`cargo test -p rusty_mill_board`, clippy and my probe tests:** not run. The build couldn't fetch crates: the sandbox denies `index.crates.io` ("user denied"), and `--offline` failed because `tokio 1.53.3` isn't in the local cargo cache. So I haven't seen the PR's own tests pass, haven't run them at base, and every trace above is source-traced. The probes are in `…/scratch/head/crates/apps/rusty_mill_board/tests/probe_numbers.rs` and the `probe` module at the end of the scratch `src/api.rs`. Running them, the crate's tests and clippy once crates.io is reachable would settle this.
- **Browser check of the `#n` heading and the "issue #n" label:** not run.

## Verdict
**changes required**
