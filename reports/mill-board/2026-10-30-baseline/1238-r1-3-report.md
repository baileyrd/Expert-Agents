<!-- mill-board/1238-r1 run 3, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 314 s, 2906738 tokens, $1.94 -->

## Mill Board PR 1238 review: card numbers (`number:<n>`)

**Verdict: changes required.** I found three blocking defects and one docs defect. None of them were confirmed by a run: the probe tests wouldn't build here, so every trace below comes from reading the code.

- **Repository:** Rusty-Mill/rusty_mill, PR #1238
- **Base:** d50375a25ced7622f88810e6d3bcad8f1986bb50
- **Head reviewed:** 43e0720953f356a5a1abec4fd4da8a6a9a3032a5. I fetched `pull/1238/head` and it matches. The PR is one commit.

### What the diff touches, and the rule each part must keep
- **Card creation** (`create`, `add_done_in` and `import` through `insert_numbered`, board.rs:345): each live card gets one number that no other card has, even with several boards writing to one rusty_tick store.
- **Startup numbering** (main.rs:299, `number_strays` board.rs:320): it must be safe when another writer is active, and it must not block any other write.
- **Name lookup** (`card_ref` api.rs:339, `get_number` board.rs:297): a number names exactly one card, and scope is enforced the same way as a lookup by id. This applies to all 13 MCP and HTTP tools that take an id (the web API uses the same table).
- **Reserved tag** (`reserved_tag` domain.rs): cards cannot set `number:`. This holds.
- **UI** (`heading`, dialog meta): shows the server's number. This holds, but I did not check it in a browser.
- **Docs:** ADR-0012 row, change fragment, README.

### Blocking findings

**1. P0: two boards on one store hand out the same number, so `#n` can act on the wrong card.**
- **Where:** board.rs:90 (`numbering: Mutex<()>`), board.rs:327 and board.rs:350.
- **Trigger:** two boards sharing one rusty_tick both create a card at about the same time (a person on each, or a person plus the GitHub import).
- **Cause:** the number is computed as the highest number in `store.list()` plus one, under a lock that only exists inside one process. The create request has no condition on rusty_tick, because a fresh UUID never collides, so nothing at the store stops a duplicate.
- **Trace:**
  1. Board A runs `insert_numbered` and lists: the highest number is 6.
  2. Board B runs `insert_numbered` and lists: the highest number is 6.
  3. A creates its card with `number:7`; B creates its card with `number:7`.
  4. Now two live cards are #7. `get_number` (board.rs:297) returns whichever was created first.
  5. An agent that was handed B's card calls `card_move {id:"#7", to:"done"}`. That closes A's card instead, and Done is terminal.
- **Who is hurt:** agents over MCP, people reading `#7` in the UI, and the other board. ADR-0012 and the brief both say more than one board can share one store.
- **Correction:** fence the counter at rusty_tick. For example, keep a counter document updated with `If-Match` (and `If-None-Match: *` on first use), and claim the number before the create request. Also have `get_number` refuse a number held by more than one card, rather than silently picking one.
- **Regression test:** in `tests/numbers.rs` or `tests/commit_order.rs`, put two `Board`s on one `FakeTick`. Wrap the store's API so every create request waits at a `Barrier(2)`, then assert the two numbers differ. I wrote this as `scratch/r1/.../tests/probe_numbers.rs::two_boards_on_one_store_hand_out_the_same_number` but could not build it.

**2. P1: any failure to number an unrelated stray card makes every `card_add`, `add_done` and GitHub import fail.**
- **Where:** board.rs:338 (the `?` inside `number_strays`, called from `insert_numbered` at board.rs:350) and domain.rs:522–524.
- **Trigger A (two boards):**
  1. Board A lists and sees stray card S without a number.
  2. Board B numbers S first.
  3. A's write to S fails on the version check, so A re-reads S. `numbered()` then returns `Invalid("card is already numbered")`.
  4. A's `card_add`, which never named S, fails with "invalid input: card is already numbered". The GitHub sync returns that error (`sync.rs:318`) and aborts the run.
- **Trigger B (one board):**
  1. The owner tags a task `agent` in rusty_tick along with 18 more tags. That is 19 tags, which rusty_tick accepts.
  2. Numbering writes `agent`, `board:backlog`, `number:N` plus the 18 tags. That is 21 tags, over rusty_tick's limit of 20 (`rusty_tick/src/service.rs:18,800`), so rusty_tick answers 400.
  3. From then on, every new card and every import fails with "rusty_tick answered 400 to PATCH /tasks/<S>", until someone edits S by hand.
- **Who is hurt:** people and agents creating cards, and the GitHub sync. The error names another card or says "invalid input", so an agent cannot tell what to do next.
- **Correction:**
  - Treat "already numbered" as success: return the card unchanged, which `apply` already treats as an idempotent no-op.
  - Make numbering strays best-effort, logged and skipped, so it never fails the new card. The next number must still come from the fenced counter in finding 1.
- **Regression test:** use a hook that runs `B.number_cards()` just before A's first write to S, then assert A's `add` succeeds (`probe_numbers.rs::a_stray_numbered_by_another_board_fails_card_add`). Add a second test with a 19-tag stray made by the owner.

**3. P1: a number is reused while its card still exists in rusty_tick, which produces duplicates.**
- **Where:** board.rs:327, and tick.rs:189–191 (`on_board` drops trashed tasks and tasks without the `agent` tag).
- **Trigger:** the owner moves the highest-numbered card to the trash, or removes its `agent` tag. The sync code itself names that as a supported owner action at sync.rs:313–314.
- **Trace:**
  1. #2 goes to the trash.
  2. The next new card is #2.
  3. The owner restores the trashed task. rusty_tick supports this via `POST /tasks/{id}/restore`, which is in its README.
  4. Two live cards are now #2, with the same consequences as finding 1.
- **Doc conflict:** the change fragment's "Known limitations" says the number "can be handed out again" only when the card is *deleted*. A trashed task is not deleted. This breaks the rule that an identifier is never reused while its owner still exists.
- **Correction:** a durable counter that never goes down (the same fix as finding 1). Failing that, take the highest number across trashed and untagged tasks too.
- **Regression test:** `probe_numbers.rs::a_trashed_card_restored_shares_its_number`. It sets `deletedMs` with `owner_edit`, adds a card, clears `deletedMs`, then asserts the new card is not #2.

**4. P2: the README still lists only three reserved tags.**
- **Where:** README.md:30–32 says "The tags `agent`, `board:…` and `claim:…` are the board's own".
- **Correction:** add `number:…`. The README also never mentions card numbers, or that the 13 id-taking tools accept `#n`.

### Ruled out, with reasons
- **Scope leak through sequential numbers:** naming `#n` outside your scope gets `OutOfScope(project)`. That is the same as naming a card by UUID, and `board_events` already exposes card UUIDs from every project (README:47). So this is not new.
- **Lock order:** `create` takes the registry lock and then the numbering lock. `update_projects` and `number_cards` never take them the other way round, so they cannot deadlock.
- **Lookup race:** a card's number never changes (`numbered` refuses), so turning a number into an id before the write cannot go stale. `card_take` with a number still rechecks the card inside the version-checked write.
- **Lost reply on create:** a retried `card_add` creating a duplicate card was already true before this PR. Numbers don't change that.
- **UI staleness:** the number is display-only and comes from the server on every render and poll.
- **Settings and roles:** untouched.

### Design questions (not blocking)
- **Cost:** every `card_add` now lists the whole store, including every board and comment document, while holding one lock for the whole process. Each `#n` lookup lists the store again, so `card_plan` with 32 dependency numbers does 32 full listings. Looking numbers up through a fenced counter or an index would avoid this.
- **Lookup miss:** an unknown number returns `Invalid("no card has that number")`, but an unknown UUID returns `NotFound`. Agents would find one "not found" error easier to handle.

### Optional improvements
- `Card::numbered` says "`updated_ms` stays", but rusty_tick moves `updatedMs` on every change it stores. With rusty_tick as the store, numbering at startup changes the update time of every stray card.
- Cards with 17 or more of their own tags gain a 21st tag when claimed, one sooner than before, and the claim then fails with a 400. That failure is not in `take_in`'s skip list, so it stops `card_take` for everyone. The board should cap card tags so they fit rusty_tick's limit of 20.

### Checks and validation gaps
- **Change-fragment check:** I ran `python3 -I .github/scripts/changes.py check` and it passed ("529 fragment(s) ok", exit 0).
- **Tests, clippy and probes: not run.** The sandbox blocks crates.io, and the local cargo cache lacks crates pinned in the lockfile (tokio 1.53.3, dyn-clone 1.0.20, boxcars). I trimmed the workspace and regenerated the lockfile in my scratch copy, and offline builds still failed. So `cargo test -p rusty_mill_board`, clippy and my three probe tests never ran.
- **Tests at the base commit:** I couldn't run the new tests there. They use `Card::number`, which doesn't exist at the base commit, so they would fail to compile there. None of them covers two boards or a numbering race, which is the gap behind findings 1 to 3.
- **Not run:** the browser check of the `#n` heading, dialog and `issue #n` label, and any live rusty_tick run (`tick_live.rs`).
- **To close these gaps:** run the scratch probes at `scratch/r1/crates/apps/rusty_mill_board/tests/probe_numbers.rs` and the crate's tests where crates.io (or a populated cargo cache) is reachable.
