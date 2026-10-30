# Review: PR #1165, Mill Board keeps its cards only in rusty_tick (ADR-0012 step 6)

- Repository: rusty_mill (worktree /home/claude/trial-mb-1165)
- PR: #1165, one commit `453dd9f2` ("Mill Board: keep the cards only in rusty_tick")
- Base SHA: `c4a0d031`. Reviewed head SHA: `453dd9f21e5c005b13f21d59fd30093af1bfbc01`. The caller pinned the head (worktree HEAD confirmed equal to 453dd9f2); no fetch was done.
- Diff reviewed: `git diff c4a0d031...453dd9f2` (27 files, +266 / -1791).

## 1. Predicted surface and invariants

| Touched item | Invariant (source) |
|---|---|
| `main.rs` store selection (`--store` / `MILL_BOARD_STORE` removed, `--tick-url` or `MemStore`) | The service never quietly falls back to a store that loses data. ADR-0012 §1: "the in-memory adapter stays for tests"; reviewer rule "silent fallback". |
| `main.rs` backup flags removed (`--backup-dir/-secs/-keep`) | A retired setting is refused, not ignored, when ignoring it loses data or protection. |
| `TickStore::parts` (`tick.rs:487-507`), used by `get` and by `replace` | A card read never pairs a newer board document with an older version (tick.rs module doc, lines 21-24 as changed). Retry is bounded and its failure is reported. |
| `Store` port doc (`store.rs:10-20`) | Doc matches what both adapters do. |
| `TickStore` events (`record`, `observe`) exercised by the new `tick_store_never_skips_an_event` | Every board write is one event, in id order, none skipped or duplicated (`commit_order.rs:22-23`). |
| `Settings` (`snapshot*`, `waiting` removed) | Settings stay Admin-only, one change at a time (`settings.rs:307-311`); unchanged apart from removed helpers. |
| `ui.html:144` card chip filter | `migrated:*` hidden as a chip but kept on the task (change fragment). |
| Deploy docs, unit, env example, README, ADR-0005, connecting-models | Agree with the code and give a safe upgrade path from the previous deployment (ADR-0012 §7: "its store file is kept until the tick-backed board has run"). |
| Tests: `service.rs` and mmdb tests deleted, `execution.rs` added, tick variants in `commit_order.rs`/`take.rs` | Every behaviour that remains keeps a test; each new test goes red without its change. |

## 2. Blocking findings

### F1. P0: an existing service env file now starts an in-memory board that reports healthy, accepts writes, and loses them on restart

- **File/line:** `crates/apps/rusty_mill_board/src/main.rs:197-204` (`None => Box::new(MemStore::default())`); `deploy/README.md:52-55` (upgrade steps); `deploy/board.env.example:3-5`.
- **Trace (executed, script `/tmp/claude-0/-home-claude/c6e91f6f-c04b-5b25-b0a9-c1761f25074d/scratchpad/probe-fallback.sh`):** I started the head binary with the environment the base `board.env.example` produced (`MILL_BOARD_STORE=…/board.mmap`, `MILL_BOARD_BACKUP_DIR=…`, settings dir, `MILL_BOARD_HTTP`, no `MILL_BOARD_TICK_URL`). This is what you get by following the PR's own "Upgrade and rollback" steps (replace the binary, `systemctl restart`) on a host that has not been reconfigured.
  1. The process starts with no warning (log: only `Mill Board listening`). `GET /health` returns `200 {"ok":true}`.
  2. `POST /api/card_add {"title":"written after upgrade"}` returns 200 with a new card.
  3. SIGTERM, exit 0, same env, restart: `board_list` returns `{"cards":[]}`. The card is gone. `board.mmap` is untouched and `MILL_BOARD_STORE` / `MILL_BOARD_BACKUP_DIR` were ignored without a message (clap does not read env vars for flags that no longer exist).
- **Who is hurt:** agents over MCP and people in the UI. They see an empty board that looks healthy, `card_take` returns null, and every card, claim and comment written until someone notices is lost at the next restart. With GitHub sync configured, the empty board also imports every open issue as a new Backlog card and mirrors `board:backlog` labels onto those issues (source-traced from `sync.rs:1-20`: unlinked open issues become Backlog cards; the board's column is mirrored as the label).
- **Trigger:** a board.env still in the base example's shape (`MILL_BOARD_STORE` set, no `MILL_BOARD_TICK_URL`). The trigger is narrow but the impact is lost cards, so this is graded P0.
- **Required correction:** in HTTP (service) mode, refuse to start without `--tick-url` unless an explicit opt-in such as `--in-memory` is given. ADR-0012 §1 keeps the memory adapter for tests only. At minimum, refuse at start when any retired variable (`MILL_BOARD_STORE`, `MILL_BOARD_BACKUP_DIR`, `MILL_BOARD_BACKUP_SECS`, `MILL_BOARD_BACKUP_KEEP`) is set, with a message that points to `MILL_BOARD_TICK_URL`. In `deploy/README.md` "Upgrade and rollback", add the env-file edit (remove the retired variables, set `MILL_BOARD_TICK_URL` and `RUSTY_TICK_TOKEN`). ADR-0012 §7 says an existing `board.mmap` and the previous binary are kept until the tick-backed board is confirmed; the README should say so too, because no binary after this PR can read that file.
- **Regression test:** a process test (the slot left by the deleted `tests/service.rs::process`) that spawns the binary with `MILL_BOARD_STORE` set and `--http`, and with `--http` and no `--tick-url`, and asserts a non-zero exit with a message naming `--tick-url`. Without the fix the binary serves 200, so the test goes red.

### F2. P2: the new `tick_store_never_skips_an_event` fails about 1 run in 6, because `observe` double-counts the board's own inserts; the rewritten `Store` doc claims the opposite

- **File/line:** `tests/commit_order.rs:104-107` (new test); cause in `src/tick.rs:581-605` (`observe`) against `src/tick.rs:646-676` and `571-578` (`insert` then `record`); doc at `src/store.rs:12-14` ("the card and its event become visible together").
- **Trace (executed):** I ran the head `commit_order` test binary 150 times: 22 failed. In 60 more runs, every one of 11 failures was `tick_store_never_skips_an_event` at `commit_order.rs:82`: "every add committed one event" (`left != 320`). Source trace: writer W `POST /tasks` succeeds, and the task is visible in rusty_tick → the reader's `events_since` → `observe` reads `tagged()`, finds a task with no `seen` entry, and pushes `Created` by actor `tick` → W's `record` pushes its own `Created` by `nano`. One add now shows as two events.
- **Who is hurt:** CI and the merge queue first (the PR adds a test that goes red at random). Agents following `board_events` see a phantom `created`/`edited` by `tick` for a board write. That behaviour predates this PR, but the PR's new test now exposes it, and its rewritten port doc says it cannot happen. This is the observation-racing-writes class from #1246, and its earlier fixes are not ancestors of this head.
- **Required correction:** stop `observe` from reporting a board write in flight. For example, mark the id or expected version as pending in `Events` before the request (in `insert`, `replace`, `put_doc`), and have `observe` skip pending ids. Or have `record` replace an observed `tick` event for the same card and version instead of appending. Correct `store.rs:12-14` so it says what the tick adapter guarantees.
- **Regression test:** a deterministic test in `tests/tick_store.rs` with a `FakeTick` hook that calls `board.events(None, …)` after the `POST /tasks` reply and before `record`, then asserts exactly one event, by the writing actor. Keep `tick_store_never_skips_an_event` as a stress test once it is stable.

### F3. P2: the only process-level test of the binary was deleted with no replacement

- **File/line:** deleted `tests/service.rs` `mod process` (base `tests/service.rs:689-805`). It covered behaviour that remains at the head: `GET /health` 200 from the real binary, `first-admin.token` written mode 0600 and never logged (`main.rs:134-150`), and SIGTERM giving exit 0 within the drain (`main.rs:293-305`).
- **Trace (source-traced):** at the head, `grep -n "first-admin\|CARGO_BIN_EXE\|kill" tests/*.rs` finds only `project_export.rs` spawning the binary, and only for `--export-projects`. `settings.rs` checks the mode of `identities.json`, not `first-admin.token`, and not that the token stays out of the log. `shutdown.rs` drives `serve_until_drained` in-process, not the signal path in `main.rs`. `main.rs` store selection, which the PR changed, now has no test at all (see F1).
- **Who is hurt:** a person who runs the service. A regression that logs the first admin's token, or that breaks SIGTERM handling, would pass CI.
- **Required correction:** keep the process test and drop only its second-owner (`in use`) assertion: spawn with no `--tick-url` (or a stand-in tick), check health, token-file mode and absence from stderr, and SIGTERM exit 0. Add the F1 start-refusal cases there.
- **Regression test:** the restored `process::the_server_reports_health_keeps_its_first_credential_off_stderr_and_stops_on_sigterm`, minus the `--store` lines.

### F4. P2: the `main.rs` module doc still describes the removed store's health rule

- **File/line:** `crates/apps/rusty_mill_board/src/main.rs:12-13`: "`GET /health` answers 200 while the store can commit and 503 once it needs a restart."
- **Trace (source-traced):** the "needs a restart" state belonged to the deleted mmdb adapter. At the head `Store::health` is always `Ok` for `MemStore` (`store.rs:41`), and for `TickStore` it is 503 while rusty_tick does not answer 200 (`tick.rs:739-747`). The README and deploy README were updated to say this; `main.rs`, which the PR edited in the same block, was not.
- **Who is hurt:** an operator or agent reading `--help` or the source doc.
- **Required correction:** "200 while the store answers (with `--tick-url`, rusty_tick's `/health`), 503 while it does not."
- **Regression test:** none needed; it is a doc fix.

## 3. Failure classes

- **Lost update:** no new read-derive-write. `replace` still fences each request on the version it read (`tick.rs:678-718`). `parts()` now re-reads the version, so the `current` that `replace` diffs against is coherent. Ruled out for this diff.
- **Two-step write:** unchanged (`insert` with a document, `card_plan` with priority and deps). No new two-step write. The operator step that touches a running store's files (the backup restore) is deleted. Ruled out for this diff.
- **Silent fallback or reuse:** F1 (executed).
- **Scope leak:** the PR touches no visibility, credential or project logic. The UI chip filter hides any tag starting `migrated:`, including one a user sets themselves; the dialog's tag field still shows and keeps it (`ui.html:195-219`). Not a leak.
- **Token races:** not touched.
- **UI out of step:** the chip filter is render-only and runs in the same `render()` on the poll and the success path. No staleness change.
- **Tests that miss the window:** F2 (a flaky count, executed). `execution.rs` races eight binders without pausing at the window between the task read and the document read. With the head's `tick.rs` restored to base `parts()`, it failed 31 of 40 runs, so it guards the fix, but only by chance (see optional O1). No test reads real credentials from the environment. The `projects.mjs` browser check was not run (validation gap).

## 4. Board rules and agent contract

- Transition table, claims, Done, reserved tags, `card_take` order and its recheck in the CAS: `domain.rs` and `board.rs` are untouched apart from deleting `backup`/`compact`. `take.rs` gains a `TickStore` race (`racing_workers_never_share_a_card_in_rusty_tick`), which passed 150 of 150 runs.
- Settings: still Admin-gated and serialised under `changing` (`settings.rs:307-311`). Only the snapshot helpers and the `waiting` test counter were removed. No lock-out path was added.
- Agent contract: no MCP tool or route was added or changed. A new reply is possible: `get` / `card_get` can now return `Conflict` after 8 reads of a task that keeps moving (`tick.rs:507`). That is a read, so retrying it is safe. See O2 on its wording.

## 5. Design questions

- **D1.** README line 4 ("The board is the only state") was already inaccurate in tick mode. With the board file gone it is plainly false: rusty_tick holds the state, and the owner edits it directly (ADR-0012 Consequences). Worth rewording; not graded, because the line predates this PR.

## 6. Optional improvements

- **O1.** Make the `parts()` fix's test deterministic. A `FakeTick` hook could move the version (a `PUT /docs/board` under `If-Task-Match`) between the first task read and the documents read, then assert that `get` returns the document with its matching revision. Today `execution.rs` catches it in about 78% of runs.
- **O2.** When `parts()` gives up, return an error that says the task is changing under the read (for example `Storage("task kept changing; retry")`). `Conflict` reads to an agent as "your rev is stale" on a call that sent no rev.
- **O3.** `parts()` now makes five requests per card read (task, lists, every board document, every comment, task again). This is fine at today's size; consider `GET /docs/board/{id}` (ADR-0012 step 1 added it) instead of listing every document.

## 7. Checks performed

| Check | Result |
|---|---|
| `cargo test -p rusty_mill_board` at head (probe worktree) | All suites passed on the first run (68 unit; commit_order 4, execution 2, github 4, project_export 1, projects 11, settings 10, shutdown 5, store_contract 3, take 17, tick_store 14, web 7; live tests ignored). |
| Stress: `commit_order` ×150 at head | **22 failed** (F2). |
| Stress: `execution` ×150, `take` ×150 at head | All passed. |
| `execution` ×40 with `parts()` reverted to base | 31 failed, 9 passed: the test guards the fix, by chance. |
| `cargo clippy -p rusty_mill_board --all-targets -- -D warnings` | Clean. |
| `cargo fmt -p rusty_mill_board --check` | Clean. |
| `python3 .github/scripts/changes.py check` | `462 fragment(s) ok`. |
| Process probe of the old env file (F1) | Executed: serves 200, accepts a write, loses it on restart. |
| Repo-wide grep for `--store`, `MILL_BOARD_STORE`, `MILL_BOARD_BACKUP`, `board.mmap` | No stale references outside the change fragment and the intended README notes. |

## 8. Validation gaps

- `tests/ui/projects.mjs`, the new chip check: a Playwright browser check I did not run.
- The change fragment's claim that "the cards were migrated into rusty_tick and the board has run on `--tick-url`" (ADR-0012 step 6's precondition) needs the live deployment's `board.env` and rusty_tick contents to settle. If the deployed env still names `MILL_BOARD_STORE`, F1 triggers on the next upgrade.
- CI for the head: not seen.
- rusty_tick's own tests: not run, because the diff does not touch rusty_tick.

## Verdict

**Changes required.** Blocking findings: 1 P0 (F1), 0 P1, 3 P2 (F2, F3, F4).
