<!-- mill-board/1165-r1 run 1, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 558 s, 1226176 tokens, $1.12 -->

# Review of Rusty-Mill/rusty_mill PR #1165: Mill Board keeps its cards only in rusty_tick (ADR-0012 step 6)

- **Base:** `c4a0d031b437e9140d192f172838f4261da5b889`
- **Reviewed head:** `453dd9f21e5c005b13f21d59fd30093af1bfbc01`. `git fetch origin pull/1165/head` returned this same SHA. The range is one commit.

**Verdict: changes required.** One P0 and three P2 defects. I couldn't build or run any Rust tests here (crates.io access was denied), so every trace below is from reading the source; none is from a test run.

## Step 1: what the diff touches, and the rule each part must keep

| Touched | Invariant |
|---|---|
| Store selection in `main.rs:197-204` (`--store` / `MILL_BOARD_STORE` removed) | A service started with durable settings never quietly runs on a store that loses data. ADR-0012:40 says "the in-memory adapter stays for tests." |
| Card read `TickStore::parts`, `tick.rs:487-506` | A card never pairs a board document with a revision older than that document. A read that cannot settle refuses. `Board::apply` (`board.rs:477`) relies on this for idempotent replay. |
| `Store` trait doc and the now-private `Log` in `store.rs` | Commits stay compare-and-swap. `MemStore` behaviour is unchanged. |
| Removed `backup.rs`, `Settings::snapshot*`, `Store::backup` and `compact` | Nothing still calls them. No doc still promises backups. |
| `/health`, SIGTERM drain (`drain.rs`, `web.rs` comments) | Same behaviour as before; the docs describe the tick-backed health. |
| UI chip render, `ui.html:144` | Chips hide only what they mean to hide; the dialog still round-trips tags. |
| Docs: README, `deploy/*`, ADR-0005 status line, connecting-models, change fragment | They match the code. |
| Tests: `execution.rs` (moved), `commit_order.rs`, `take.rs`, `store_contract.rs`, `service.rs` (deleted) | Each changed behaviour has a test that goes red without the change. |

No MCP tool, HTTP route, role, capability or transition changes.

## Blocking findings

### 1. [P0] A leftover `MILL_BOARD_STORE` silently starts an in-memory service; every card written after the upgrade is lost at the next restart

**Where:** `crates/apps/rusty_mill_board/src/main.rs:197-204`, with `deploy/README.md:52-55` and `README.md:9`.

**Trigger:** a `board.env` built from the `board.env.example` shipped at base. That file sets `MILL_BOARD_STORE=/home/baileyrd/.mill-board/board.mmap` and has no `MILL_BOARD_TICK_URL`.

**Failure scenario** (source-traced):
1. The operator follows `deploy/README.md` "Upgrade and rollback": replace the binary, then `systemctl restart mill-board`. That section says nothing about editing `board.env`.
2. systemd still exports `MILL_BOARD_STORE` and `MILL_BOARD_BACKUP_*`. No clap argument reads them any more, so nothing complains.
3. `cli.tick_url` is `None`, so `main.rs:203` picks `MemStore::default()`. `/health` answers 200 (the default `Store::health`), so systemd and monitoring see a healthy service.
4. The existing cards in `board.mmap` are invisible. People in the UI and agents over MCP see an empty board, and `card_take` returns nothing.
5. New `card_add`, claims and comments succeed. All of them vanish at the next restart (`Restart=always` makes that likely). Backups also stop without any notice.

**Who is hurt:** people in the UI and agents over MCP, through lost cards and claims.

At base the same env gave a durable store. The PR turns a durable configuration into an in-memory one without an error. That goes against ADR-0012:40, and against the brief's rule that a retired setting must be refused, not quietly turned into a default that loses data.

**Required correction:**
- Exit non-zero at start when `MILL_BOARD_STORE`, `MILL_BOARD_BACKUP_DIR`, `MILL_BOARD_BACKUP_SECS` or `MILL_BOARD_BACKUP_KEEP` is set. The message should name the migration and `MILL_BOARD_TICK_URL`.
- Make the in-memory board an explicit opt-in (for example `--in-memory`) rather than the default whenever `--tick-url` is absent, or at least refuse it in HTTP mode without the flag.
- Add the `board.env` edit to the deploy README's upgrade step.

**Regression test:** a process test in `tests/` that spawns `CARGO_BIN_EXE_rusty_mill_board` with `MILL_BOARD_STORE=…` and `--http 127.0.0.1:0`, and expects a non-zero exit whose stderr names the retired setting. It goes red at head, where the process serves.

### 2. [P2] The PR deletes the only process-level test of health, first-admin token secrecy and SIGTERM exit, and replaces none of it

**Where:** `crates/apps/rusty_mill_board/tests/service.rs` (deleted). The test lived at base, around lines 745-805: `the_server_reports_health_keeps_its_first_credential_off_stderr_and_stops_on_sigterm`.

**What it covered:** with the real binary, `/health` goes to 200; `first-admin.token` has mode 0600; the secret never appears on stderr; and SIGTERM exits 0.

**What remains at head** (source-traced):
- `tests/settings.rs:137` checks the mode of `identities.json` at library level only. It never runs `main`, so it cannot see `announce_first_admin` (`main.rs:134-142`) or stderr.
- `tests/shutdown.rs` drives `serve_until_drained`, not the signal handler and exit code.
- The only test that runs the binary is `project_export.rs`.

A regression that prints the first admin's token to the journal, or exits non-zero on `systemctl stop`, would now pass CI. Only the "second process refused" assertion belonged to mmdb.

**Required correction:** keep the process test. Spawn it without `--store` (in memory, or on a stand-in tick URL) and drop only the one-owner-lock assertion. It should go red if `announce_first_admin` writes the secret to stderr.

### 3. [P2] The changelog's "Fixed" claim (`parts()` re-reads the task version) has no test that pauses at the racing step

**Where:** `crates/apps/rusty_mill_board/src/tick.rs:487-506` and `changes/2026-10-27-board-drop-own-store.md:17-19`.

**Is the fix sound?** Yes, as far as I can trace. A document or comment `PUT` under `If-Task-Match` moves the task version under one lock (`tick.rs:14-20`, `fake_tick/mod.rs:208`). Re-reading the version after the documents therefore detects the newer-document / older-revision mix. That mix fed `Board::apply`'s replay path (`board.rs:486-487`), which returned an adopted link at a stale revision.

**The gap:** nothing pins this deterministically.
- The only test that would notice is `execution.rs::identical_execution_binding_is_concurrent_and_restart_idempotent`. It asserts `adopted.rev == 1` across 8 barrier-started threads, so it catches the mix only if the scheduler happens to land a write between the task GET and the board-docs GET.
- At base this test ran on mmdb, where the window does not exist. So the test that guarded this before guards nothing now.
- Neither the window nor the `READ_ATTEMPTS` exhaustion path (`Err(Conflict)`, `tick.rs:506`) has a test.

**Required correction:** add a unit test in `tick.rs`. Wrap a `TickApi` so it performs a fenced board-document `PUT` right after the first `GET` of the task. Assert that `get` returns the document at the moved revision. A second case moves the task on every read and expects `Conflict`. Both go red at base.

### 4. [P2] The module doc in `main.rs` still describes the removed store's health

**Where:** `crates/apps/rusty_mill_board/src/main.rs:12-13`.

The doc says "`GET /health` answers 200 while the store can commit and 503 once it needs a restart." The "needs a restart" state belonged to `MmdbStore` and is gone. The README and `deploy/README.md` now say "503 while rusty_tick is unreachable" (which is also restored when rusty_tick comes back, with no restart). The doc disagrees with the code and the other docs.

**Correction:** use the deploy README's wording. This is docs only; no test is needed.

## Failure classes (step 2)

- **Lost update:** ruled out for this diff. No new read-derive-write. `parts()` only adds a second read, and writes stay fenced by `If-Match` / `If-Task-Match`. Removing the one-owner lock is the settled ADR-0012 design: many boards share rusty_tick, fenced at rusty_tick.
- **Two-step write:** none added. `card_plan`'s two fenced steps are unchanged, and the store trait doc now states them. The operator step that touched a running store's files (the restore section) is removed.
- **Silent fallback or reuse:** **Finding 1.**
- **Scope leak:** none. `api.rs`, `identities.rs` and `board.rs` scope checks are untouched. `apply` still checks `may_work_in` on the card it re-reads (`board.rs:479`).
- **Token races:** none touched. `RUSTY_TICK_TOKEN` is still read only from the environment. `board.env.example` now holds it, and that file is mode 600.
- **UI out of step:** only the chip filter changed. Chips and dialog can now disagree; see design question 2.
- **Tests that miss the window:** **Finding 3.** `tick_store_never_skips_an_event` now runs on FakeTick; I couldn't run it to look for flakiness.

## Board rules and agent contract (steps 3-4)

- No transition, capability, claim, `card_take` order, reserved-tag or settings change.
- `Settings` loses only `snapshot*` and the `waiting` test counter. `call` is still Admin-gated under `changing`.
- MCP and API replies are unchanged. The one new error is `Conflict` from a read that cannot settle, and its text is "card changed concurrently; retry". That tells an agent what to do. `Board::apply` passes it straight up (`board.rs:477`, `self.get(id)?`) rather than spending a retry, which is acceptable.

## Design questions (not blocking)

1. **ADR-0012 vs this PR on the in-memory board.** ADR-0012:40 keeps the in-memory adapter "for tests", but this PR documents it as a way to run (`README.md:9`, the change fragment). Should a production binary run in memory without an explicit flag at all? The simpler option is a required `--in-memory`. This is also the fix for Finding 1.
2. **Hidden but settable `migrated:` tags.** `ui.html:144` hides every `migrated:*` chip, but `domain::reserved_tag` (`domain.rs:163-166`) does not reserve `migrated:`. The migration script, however, treats it as reserved (`tools/migrate_artifact_board.py:57`). So a person's own tag such as `migrated:from-jira` disappears from cards but still shows in the dialog. Also, an edit that deletes `migrated:mill-board` from the tags field breaks the script's "re-run creates nothing" guarantee. The owner should decide whether `migrated:` joins the reserved tags (refused on cards, preserved on update) or stays visible.

## Optional improvements

- ADR-0005's body (lines 25, 38, 48-97) still states backups as current decisions under the new status note. A short "Superseded in part" header per section would make that clearer.
- `deploy/mill-board.service` could add `After=`/`Wants=` on the rusty_tick unit, since the README says "run rusty_tick first".

## Checks performed

| Check | Result |
|---|---|
| `git fetch origin pull/1165/head` vs head SHA | Match (`453dd9f2…`) |
| `python3 -I .github/scripts/changes.py check` | **Passed**: "462 fragment(s) ok", exit 0 |
| `cargo test -p rusty_mill_board` | **Not run.** The offline build is missing the `boxcars` crate, and network access to `index.crates.io` was denied. |
| `cargo clippy -p rusty_mill_board` | **Not run**, same reason. |
| Leftover references to `MmdbStore`, `backup`, `snapshot`, `waiting()`, `store::Log` in Rust sources | None found (grep) |

## Validation gaps

- **Unit and integration tests:** the whole `cargo test -p rusty_mill_board` suite and clippy at head, plus the new and moved tests (`execution.rs`, `tick_store_never_skips_an_event`, `racing_workers_never_share_a_card_in_rusty_tick`) at both head and base. Run them where crates.io is reachable.
- **Browser check:** `tests/ui/projects.mjs`'s new chip check needs Playwright, which I didn't run.
- **Live deployment:** an upgrade with the shipped `board.env` would confirm Finding 1 directly. I traced it from source.
