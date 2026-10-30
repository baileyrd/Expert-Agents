<!-- mill-board/1165-r1 run 3, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 298 s, 2588108 tokens, $1.75 -->

# Review of PR 1165 (Rusty-Mill/rusty_mill): changes required

I could not build or test anything here, so every finding below is traced from the source; none was executed. There is one P0 and three P2 findings.

- **Base:** `c4a0d031b437e9140d192f172838f4261da5b889`
- **Head:** `453dd9f21e5c005b13f21d59fd30093af1bfbc01`. `git fetch origin pull/1165/head` gives FETCH_HEAD = HEAD = 453dd9f2, so the pin holds.
- **Scope:** one commit, "Mill Board: keep the cards only in rusty_tick (ADR-0012 step 6)". It removes the mmdb store, the one-owner lock and the backups. It changes the card read in `TickStore::parts`, hides `migrated:*` chips in the UI, and moves or deletes tests.

## Invariants for what the diff touches

| Item | Invariant |
|---|---|
| Store selection at start (`main.rs:197-204`) | A missing or retired storage setting refuses to start. It never falls back quietly to a state that loses data. ADR-0012:40: "the in-memory adapter stays for tests". |
| `TickStore::parts` (`tick.rs:487-507`), used by `get` and inside `replace` | A card's revision names its task and its board document together (`tick.rs:14-25`). The CAS in `replace` still compares a fresh read. |
| `list` (`tick.rs:623`) | A mixed card in a listing is refused when written (`replace` at `tick.rs:690`). This holds. |
| `/health` | 200 while rusty_tick answers, 503 otherwise (README, deploy/README). |
| SIGTERM | Drain, then exit 0 (deploy/README "Stop", ADR-0005). |
| First-admin token | Goes to an owner-only file and stays out of the journal (`main.rs:134-151`). |
| UI chips (`ui.html:144`) | Hides only `migrated:*`. The dialog's tag field still round-trips every tag (`ui.html:195,219`). |
| Settings | The lock is still taken for each change (`settings.rs:310`). Only the backup snapshot path was removed; there are no remaining callers (checked by grep). |

## Blocking findings

### 1. P0: an upgraded service that still sets `MILL_BOARD_STORE` starts an empty in-memory board and loses every card written to it

- **Where:** `crates/apps/rusty_mill_board/src/main.rs:197-204`, together with the `Cli` struct at `main.rs:43-107`, which no longer defines `store`.
- **Trigger:** a deployment configured from the base `board.env.example` (`MILL_BOARD_STORE=...`, `MILL_BOARD_BACKUP_DIR=...`, no `MILL_BOARD_TICK_URL`) whose operator follows deploy/README "Upgrade and rollback": replace the binary and run `systemctl restart`.
- **Why it happens:** clap ignores environment variables it does not declare. `tick_url` is `None`, so the match takes `MemStore::default()`. Credentials still load from `MILL_BOARD_SETTINGS_DIR`, so every agent authenticates. `MemStore::health` returns `Ok`, so `/health` answers 200 and systemd sees a healthy service. (Passing `--store` on the command line does fail as an unknown argument. The systemd unit's `ExecStart` takes no arguments and reads only the env file, so the env-var path is the one a deployment hits.)
- **Failure scenario (source-traced):**
  1. The board restarts on the new binary.
  2. An agent calls `board_list` or `card_take` and gets an empty board.
  3. People and agents add or move cards. Every reply reports success.
  4. The next restart (`Restart=always`, a crash, an upgrade) drops all of those writes.
  5. `MILL_BOARD_BACKUP_DIR` is ignored the same way, so no copy exists. The old `board.mmap` stays on disk but nothing reads it.
- **Who is hurt:** people in the UI and agents over MCP. Both lose cards and claims they were told were committed.
- **Docs the code contradicts:** ADR-0012:40 says "the in-memory adapter stays for tests". The PR makes it the service's silent default, and README:69-70 now documents "Without `--tick-url` the board lives in memory". ADR-0012:224-226 also expects a deployed `--store` board to be exported with `board_list` first. deploy/README gives that operator no step, and the new binary can no longer read the file.
- **Required correction:**
  - Refuse to start, naming the setting, when any of `MILL_BOARD_STORE`, `MILL_BOARD_BACKUP_DIR`, `MILL_BOARD_BACKUP_SECS` or `MILL_BOARD_BACKUP_KEEP` is set. The message should point at `MILL_BOARD_TICK_URL` and the migration.
  - Make the in-memory board an explicit opt-in (for example `--in-memory`), at least in `--http` mode, so that omitting a setting never selects it.
  - Add an "upgrading from `--store`" note to deploy/README.
- **Regression test:** a process test, next to `tests/project_export.rs`, that runs the binary with `MILL_BOARD_STORE=<tmp>/board.mmap`, `--http 127.0.0.1:0` and no tick URL. It should assert a non-zero exit with the variable named on stderr. A second case checks that `--http` without `--tick-url` or the opt-in is refused. Both go red at head.

### 2. P2: the only real-binary tests of the first-admin token file, `/health` and SIGTERM were deleted with no replacement

- **Where:** the deleted `tests/service.rs` `process::the_server_reports_health_keeps_its_first_credential_off_stderr_and_stops_on_sigterm` (base, around lines 690-805).
- **Why it matters:** apart from its "second process is refused" part, that test covers behaviour this PR keeps and documents:
  - `announce_first_admin` (`main.rs:134-151`) writes a 0600 `first-admin.token` and keeps the secret out of a non-terminal stderr (the journal).
  - `/health` answers 200 through the real binary.
  - SIGTERM leads to exit 0 (`main.rs:153-186`, deploy/README "Stop").

  A grep of `tests/` finds no other test for any of these. `project_export.rs` is the only remaining `CARGO_BIN_EXE` test, and it exits before startup.
- **Scenario (source-traced):** a later change prints the token to stderr, or breaks the signal thread, and no test goes red. The first case puts a credential in the journal.
- **Correction:** port the process test with the `--store` argument and the second-process assertion removed. It can run in memory (or on the opt-in from finding 1) with `--settings-dir`. It should assert the 0600 file, the secret absent from stderr, `/health` 200, and exit 0 on `kill -TERM`. It goes red if `announce_first_admin` ever writes the secret to stderr.

### 3. P2: the `parts` re-read fix has no test that enters the race window, and the retry limit is untested

- **Where:** `src/tick.rs:487-507`, with `tests/execution.rs:23-52`.
- **What the fix does:** the logic is correct. rusty_tick's `put_doc_for` bumps the task and writes the document in one `&mut self` operation (`rusty_tick/src/service.rs:832-869`), so re-reading the version after the documents rules out a newer document paired with an older revision.
- **What the test does:** the only guard is the moved 8-thread `identical_execution_binding_is_concurrent_and_restart_idempotent`. The fake rusty_tick (`tests/fake_tick/mod.rs`) has no hook to pause between `GET /tasks/{id}` and `GET /docs/board`. The bug needs thread B's two reads to straddle thread A's fenced PUT, so at base the test passes on most interleavings. That is the "does not pause at the racing step" class. I could not run it at base to measure how often (see Checks).
- **Also untested:**
  - After `READ_ATTEMPTS` (8) the read returns `Conflict` ("card changed concurrently; retry"). No test covers it.
  - A task deleted or untagged between the two reads should return `None`. No test covers it.
- **Correction:** a `TickApi` wrapper in `tick.rs` tests, or in `tests/tick_store.rs`, that makes a fenced `PUT /docs/board/{id}` on the first `GET /docs/board`. Then assert that `get` returns the card with the new document at the new revision. That test fails at base on every run. Add a second wrapper that moves the version on every read and asserts `Err(Conflict)`.

### 4. P2: the `main.rs` module doc still describes the removed store's health semantics

- **Where:** `src/main.rs:12-13`: "`GET /health` answers 200 while the store can commit and 503 once it needs a restart".
- **What is true now:** `TickStore::health` (`tick.rs:739-748`) answers 503 while rusty_tick is down, and the in-memory store is always 200. The PR's README ("503 while rusty_tick is unreachable") and deploy/README both say so.
- **Correction:** align the doc comment with the README.

## Design questions (not blocking)

- **`migrated:` is not a reserved tag, and the UI now hides it.** `tools/migrate_artifact_board.py:56-57` says the board's own tags include the `migrated:` prefix. But `domain::reserved_tag` (`domain.rs:163-166`) and ADR-0012:58 reserve only `agent`, `board:` and `claim:`. The PR's own browser check sets the marker through `card_add`, which shows any credential can set it. With the chip hidden, a person can no longer see on the board that a card carries the marker the rollback selects on. The ADR settles the reserved set, so this is the owner's call: reserve `migrated:` (and refuse it in `card_update`), or keep it visible.
- **ADR-0012 step 6 has a precondition.** It applies "after the tick-backed board has run". Only the PR body and change fragment assert this, and the owner needs to confirm it. If any live service still runs on `--store`, finding 1 applies to it directly.

## Optional improvements

- `tests/project_export.rs` is named `...opens_no_board_store`, but `--tick-url http://127.0.0.1:9` opens nothing at construction (`TickHttp::new`, `tick.rs:114-126`). The test can no longer see a store being opened; only the `must-not-exist` settings directory still guards it. Use a recording fake URL, or rename the test.
- The comments in `tick.rs` disagree with each other: line 694 says comments "do not move the version", while lines 553 and 15-20 say every write moves it. Line 694 means other people's comments; saying so would help. This predates the PR.

## Failure classes checked

| Class | Result |
|---|---|
| Lost update | Ruled out for this diff. `parts` is a read. `replace` still re-reads and fences every request with `If-Match` or `If-Task-Match` (`tick.rs:678-719`). The fenced document write is atomic in rusty_tick. `list`'s mixed card is refused by the CAS at `tick.rs:690`. |
| Two-step write | Unchanged: `card_plan` priority plus deps, and an insert with an import document (`tick.rs:668-675`), are already documented. The operator steps that touched store files (restore, compaction) are gone. |
| Silent fallback or reuse | **Finding 1.** The retired `MILL_BOARD_STORE` and backup settings become an in-memory board. An empty `RUSTY_TICK_TOKEN` is still refused (`tick.rs:118`). |
| Scope leak | No authority, project or visibility code changed (`api.rs`, `identities.rs`, `board.rs` beyond removing backup and compact). |
| Token races | No token code changed. |
| UI out of step | The filter applies only to chip rendering. The dialog snapshot and save still carry every tag, so the marker is not lost on save. |
| Tests that miss the window | **Finding 3.** The new tests use `FakeTick` and no environment credentials. The export test sets no `RUSTY_TICK_TOKEN`, and with one inherited from the environment it would still need the projects JSON on stdout. |

**Board rules and agent contract:** no MCP tool, transition, claim, capability or `card_take` code changed. The only new agent-visible behaviour is a `get` that can now return `Conflict` ("card changed concurrently; retry") on a task that keeps moving. That message tells the agent to retry, which is appropriate. Its test is covered in finding 3.

## Checks and validation gaps

- `python3 .github/scripts/changes.py check`: ran, "462 fragment(s) ok".
- `cargo test -p rusty_mill_board`, clippy, rusty_tick's tests, and running the new tests at base: **not run.**
  - Building needs crates.io. The sandbox's network proxy refused `index.crates.io` ("user denied").
  - `--offline` fails: the local registry cache predates `Cargo.lock` (tokio 1.53.1 cached, 1.53.3 locked) and has no `schemars` or `dyn-clone` at all.
  - A trimmed scratch workspace (`scratch/head`, members limited to the board and rusty_tick) failed the same way.
  - So I have not confirmed that the head compiles or passes, and I have not seen any CI result.
- Browser check `tests/ui/projects.mjs` (the new marker-chip check): not run (needs Playwright and a running board).
- Whether the deployed board has actually run on `--tick-url` (the ADR step 6 precondition): needs the owner or the live host.

## Verdict: changes required
