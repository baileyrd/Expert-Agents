# Review: PR #1238 — Mill Board card numbers

- Repository: Rusty-Mill (rusty_mill), worktree /home/claude/trial-mb-1238
- PR: #1238, "feat(mill-board): give every card a board-wide number" (one commit)
- Base: d50375a25ced7622f88810e6d3bcad8f1986bb50
- Reviewed head: 43e0720953f356a5a1abec4fd4da8a6a9a3032a5 (worktree HEAD confirmed equal; PR fetch done by caller per brief)
- Diff: `git diff d50375a2...43e07209` (11 files, +318/-42)

## 1. Predicted surface and invariants

| Touched item | Invariant |
|---|---|
| `Board::insert_numbered` (card_add, card_add done, import) | A number is unique across every card in the store, including when a second board shares the same rusty_tick (ADR-0012:108-110 "Two boards (or a board and a stray client)"; every read-derive-write fenced at rusty_tick). |
| `Board::number_strays` / `number_cards` (startup + before each insert) | Numbering a stray is fenced (it goes through `apply`, CAS) and a failure to number a stray must not turn the caller's unrelated write into a misleading refusal. |
| `Board::get_number` + `api::card_ref` (every MCP/HTTP tool taking a card id, `card_take id`, `card_plan deps`) | A number resolves to exactly one card or a refusal; it never silently picks one of several. Scope still checked on the resolved card. |
| `Card.number`, tag `number:<n>` (`tick.rs` task_tags / card_of) | Number survives every write and project move; `number:` reserved (card_update, GitHub import). |
| `EventKind::Numbered` | Agents following `board_events` see numbering as a change. |
| UI `heading`, dialog meta, `issue #n` | Display-only; uses `k.id` for every action. |
| Docs: change fragment, ADR-0012 table, board README | Agree with the code. |

## 2. Blocking findings

### F1 — P1 — Two boards on one rusty_tick hand out the same number
- **File:** `crates/apps/rusty_mill_board/src/board.rs:90` (process-local `numbering: Mutex<()>`), `:320-342` (`number_strays` derives `max+1` from a `list()` snapshot), `:345-355` (`insert_numbered` commits it with an unfenced POST).
- **Trigger:** two boards (or two board processes, e.g. during a restart overlap) sharing one rusty_tick, which ADR-0012:108-110 explicitly contemplates.
- **Trace (executed, probe `probe_two_boards_hand_out_the_same_number`):** board A `card_add` -> `number_strays` lists, max = 0, next = 1 -> (before A's POST) board B `card_add` lists, max = 0, posts "from B" as `number:1` -> A posts "from A" as `number:1`. Result: `A=Some(1) B=Some(1)`; `get_number(1)` returns "from B" while A's caller was told its card is #1. Nothing in rusty_tick makes a tag unique, and the `numbering` mutex only serialises one process.
- **Who is hurt:** an agent over MCP told "#1" by board A then calls `card_claim`/`card_move`/`card_assign`/`card_comment`/`card_take {id:"#1"}` and acts on the other card (F2 makes the pick silent). People in the UI see two cards labelled `#1`.
- **Fix:** fence number allocation at rusty_tick: keep a counter document (a doc kind written with `If-Match`/`If-None-Match: *`) and reserve the number by CAS before the task POST (a lost reply leaves a gap, never a duplicate); or, if single-writer is the intended scope, say so in the ADR and refuse to start a second board on the store. Either way F2's ambiguity refusal is still needed.
- **Regression test:** `tests/numbers.rs` (or `tests/commit_order.rs`): two `Board`s on one `FakeTick`, with a gating `Store` wrapper that runs board B's `add` just before board A's `insert`; assert the two numbers differ. This probe fails at head.

### F2 — P1 — A number held by two cards resolves silently to whichever lists first
- **File:** `crates/apps/rusty_mill_board/src/board.rs:297-303` (`get_number` uses `.find(...)`), consumed by `crates/apps/rusty_mill_board/src/api.rs:340-347` (`card_ref`) in every card tool.
- **Trigger:** any duplicate number: F1, F3, or the owner copying/duplicating a task or typing a `number:` tag in rusty_tick (reserved tags are only refused by `card_update`; rusty_tick itself accepts them).
- **Trace (executed, both F1 and F3 probes):** after a duplicate exists, `get_number(n)` returns the first card in `(created_ms, id)` order with no signal; `card_ref` turns that into the UUID every write then uses. An authority decision (which card an agent may move/claim/take) is resolved from a non-unique key.
- **Who is hurt:** an agent over MCP mutates or claims the wrong card; `card_plan deps` records the wrong dependency.
- **Fix:** collect matches; zero -> "no card has that number", more than one -> a refusal naming the ambiguity ("two cards have number n; use the card id"), so the agent stops instead of acting.
- **Regression test:** unit test in `board.rs` or `tests/numbers.rs`: insert two cards with `number: Some(3)` into a `MemStore`, assert `get_number(3)` is an `Invalid` ambiguity error and `card_get {"id":"#3"}` refuses. Fails at head.

### F3 — P1 — A trashed (or untagged) card's number is reused while the card still exists; restoring it yields two live cards with one number
- **File:** `crates/apps/rusty_mill_board/src/board.rs:326-331` (next = max over `store.list()`), with `crates/apps/rusty_mill_board/src/tick.rs:189-191` and `:483-491` (`list` drops tasks with `deletedMs` set or without `agent`).
- **Trigger:** owner moves the highest-numbered card to the Trash (rusty_tick `DELETE /tasks/{id}` is a restorable trash, README:69 and `/restore`), or removes its `agent` tag, then any card is added, then the task is restored/retagged.
- **Trace (executed, probe `probe_trashed_card_number_reused_then_restored`):** add "one" (#1), "two" (#2); trash "two"; add "three" -> gets #2; restore "two". `two=Some(2) three=Some(2)`, two live cards numbered 2, `get_number(2)` -> "two". The change fragment's limitation ("if the highest-numbered card is deleted in rusty_tick, its number can be handed out again") does not say a trashed card can come back and collide, and an identifier is reused while its owner still exists.
- **Who is hurt:** people in the UI (two `#2`), agents via F2.
- **Fix:** derive the high-water mark from every `agent`-tagged task including trashed ones (`GET /tags/agent/tasks` already returns them before `on_board` filters), or better from the counter in F1's fix, which never goes backwards. Update the fragment's limitation to what remains (purge only, or none).
- **Regression test:** `tests/numbers.rs` with `FakeTick`: as the probe above (set `deletedMs`, add, clear `deletedMs`), assert at most one card carries each number. Fails at head.

### F4 — P1 — `card_add` / `card_add done` / GitHub import fail with "card is already numbered" when another writer numbers a stray first
- **File:** `crates/apps/rusty_mill_board/src/board.rs:338` (`?` on each stray's `apply`) inside `insert_numbered` `:345-355`; `Card::numbered` `crates/apps/rusty_mill_board/src/domain.rs` (refuses an already-numbered card).
- **Trigger:** a stray (task tagged `agent` in rusty_tick) exists and a second board (or the same board's startup `number_cards` in another process) numbers it between this board's `list` and its `apply`. Also any transient failure on a stray's PATCH (3 CAS conflicts from the owner editing it, a tick 5xx).
- **Trace (executed, probe `probe_stray_numbered_by_other_board_fails_card_add`):** board A `card_add` -> `number_strays` lists (stray unnumbered) -> board B `number_cards` numbers the stray -> A's `apply` re-reads, `numbered()` refuses -> A's `card_add` returns `Err(Invalid("card is already numbered"))` and creates nothing. The agent's own new card is refused with a reason about a different card it never named, so the MCP contract ("a refusal names the reason so the agent stops") points it the wrong way.
- **Fix:** numbering strays is housekeeping: in `number_strays`, treat a stray that is already numbered on re-read as done (make `numbered` idempotent-skip in that path, recompute `next` after), and do not let a stray's failure fail the caller's insert (log and continue; the stray is retried next time). The new card's number must still come from the fenced allocation of F1.
- **Regression test:** `tests/numbers.rs`: gating `Store` that runs board B's `number_cards` right after board A's `list`; assert A's `add` succeeds and the stray keeps B's number. Fails at head.

### F5 — P2 — Board README still lists only `agent`, `board:` and `claim:` as reserved; ADR-0012 tag budget not updated
- **File:** `crates/apps/rusty_mill_board/README.md:30-32` ("The tags `agent`, `board:…` and `claim:…` are the board's own ... the GitHub sync drops such labels when it imports an issue") and `:73` (storage mapping names only `board:`/`claim:`); `docs/adr/0012-one-task-store.md:245-246` (tag budget "leave 14").
- **Trace (source-traced):** `reserved_tag` (`domain.rs:203-210`) now includes `number:`, used by `card_update`/`Card::new` and by `sync.rs:310`, so a GitHub label `number:…` is now dropped on import and refused on cards; the README does not say so, and the ADR budget is now 13. The change fragment and ADR table row were updated, the README was not.
- **Fix:** add `number:…` to README:30-32 and the storage line at :73; update the ADR budget line.
- **Test:** docs only; none.

## 3. Failure classes, ruled in or out

- **Lost update:** ruled in (F1). Numbering stray cards goes through `apply` (CAS), so a stray is never numbered twice — only the new card's number is unfenced.
- **Two-step write:** `number_strays` then `insert` is several requests; a failure after some strays leaves them durably numbered (fine) and the insert not done (caller sees an error; card_add retry is no worse than base, new UUID each call as before). Import uses the deterministic `import_id`, so a retried import is still refused as a duplicate by the store. The failure mode is F4.
- **Silent fallback or reuse:** ruled in (F3). Also `card_of` (`tick.rs:306-310`) turns a malformed `number:` tag into `None`, which then gets a fresh number and the malformed tag dropped from the task. That replaces rather than duplicates, so it is not a finding.
- **Scope leak:** `card_ref` resolves across all projects, but each tool still checks scope on the resolved card (`api.rs:487`, `board.rs:675` inside `apply`). A scoped credential probing `#1..#N` learns that a card exists and gets its project name from `OutOfScope(project)`. That is not new: `board_events` already gives every card's UUID to any credential (README:47), and `card_get` by UUID returns the same error at base. Ruled out as introduced.
- **Token races:** no auth code touched. N/A.
- **UI out of step:** UI only renders `k.number`. Every action still sends `k.id`, so nothing new is stale. Ruled out at source level; the browser check was not run (gap).
- **Tests that miss the window:** `tests/numbers.rs` uses a single board throughout, so it cannot see F1/F4. Those are covered by the regression tests named above. `tick_store_never_skips_an_event` is flaky with the same signature (321 vs 320 events) at base (3/60) and head (2/40), so it is pre-existing and not caused by this PR.

## 4. Board rules and agent contract

- Transitions, claims, Done terminal: unchanged. `Card::numbered` bumps `rev` and keeps `updated_ms`, and the `numberer` actor (`projects: None`) passes `may_work_in`. It writes an `EventKind::Numbered` event so `board_events` followers see it. The only consumers of event kinds are inside the crate (searched).
- Reserved tags: `number:` is refused by `card_update`/`Card::new` (tested) and dropped by the GitHub import. Docs gap in F5.
- `card_take {id:"#n"}`: resolution happens before `take_in`, which rechecks inside its CAS. It is only wrong when the number is ambiguous (F2).
- Tool descriptions updated for each id field. Unknown number -> `Invalid("no card has that number")`, which is distinguishable from a malformed id.

## 5. Design questions (non-blocking)

- **Cost:** each `card_add`/import now runs a full `store.list()` (with TickStore: tasks, lists, all board docs, all comments). Each number-form argument does another, so `card_plan` with 32 numeric deps makes 32 full listings. An index (number -> id) rebuilt from `list` or from events would keep the tool cheap. Not a correctness defect.
- Is a second board on one rusty_tick a supported deployment? If yes, F1 needs a store-side counter. If no, ADR-0012:108-110 should say numbering assumes one board.

## 6. Optional improvements

- `card_ref` accepts `+5` (Rust `u64` parse). This is harmless but undocumented.
- `next += 1` is unchecked at `u64::MAX`. Unreachable in practice.

## 7. Checks performed

- `cargo test -p rusty_mill_board --no-fail-fast` at head (scratch worktree): all suites pass (lib 83, numbers 4, store_contract 3, take 17, tick_store 18, web 14, ...). One earlier run had `commit_order::tick_store_never_skips_an_event` fail. Repeated runs show the same flake at base d50375a2 (3/60) and head (2/40), so it is pre-existing.
- `cargo clippy -p rusty_mill_board --all-targets -- -D warnings`: clean.
- `cargo fmt -p rusty_mill_board --check`: clean.
- `python3 .github/scripts/changes.py check`: "529 fragment(s) ok".
- New tests at base: `tests/numbers.rs`, the `api.rs` unit test and the `store_contract.rs` additions do not compile at base (no `Card::number`, `get_number`, `number_cards`, `EventKind::Numbered`), so they guard the change (source-traced from the compile errors seen when the shared target briefly held base artefacts).
- Probe tests (scratch copy, `tests/probe_numbers.rs`, kept at /tmp/claude-0/-home-claude/c6e91f6f-c04b-5b25-b0a9-c1761f25074d/scratchpad/probe_numbers.rs.keep): all three fail at head, reproducing F1, F3 and F4, and F2's silent pick.

**Validation gaps:** browser checks (`tests/ui/*.mjs`, need Playwright and a running board) were not run, so the `#n` corner and dialog meta are unverified in a browser. No live rusty_tick (`tick_live` ignored). CI for the head was not seen.

## 8. Verdict

**changes required** — 4 x P1 (F1-F4), 1 x P2 (F5).
