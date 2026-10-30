<!-- mill-board/1165-r1 run 4, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 169 s, 836301 tokens, $0.84 -->

I reviewed PR 1165 of Rusty-Mill/rusty_mill. The verdict is **changes required**, based on source tracing only: I could not build or run anything (see Checks).

- **Base:** `c4a0d031b437e9140d192f172838f4261da5b889`
- **Head:** `453dd9f21e5c005b13f21d59fd30093af1bfbc01`. `git fetch origin pull/1165/head` returned exactly this SHA, and it is one commit, `453dd9f21`.

## What the diff touches, and the rule each must keep

| Item | Rule |
|---|---|
| Store choice in `main.rs:197-204` | A setting that is missing or retired is refused, never quietly turned into a default that loses data. ADR-0012 §1: "the in-memory adapter stays for tests." |
| `TickStore::parts` (`tick.rs:483-507`), which serves `get` and every read the board makes before a write | The card's version names the whole card (`tick.rs:14-25`), so a read never pairs a newer document with an older version, and a retry is bounded. |
| `Store` trait docs (`store.rs:10-21`) | They describe what the adapters actually do. |
| `Settings` (`waiting` and `snapshot` removed) | Changes stay Admin-only and one at a time (`settings.rs:307-311` is unchanged). |
| Card chips in `ui.html:144` | Only the chip is hidden. The edit dialog still carries every tag (`ui.html:195,219`), so a save keeps the `migrated:` marker. |
| Deploy docs, unit and env example | They match the binary, and an upgrade does not lose cards. |
| Tests | Each changed behaviour has a test that goes red without the change. |

No MCP tool, HTTP route, move, claim, capability or `card_take` rule changes in this PR. I checked `board.rs`: only `backup` and `compact` were removed.

## Blocking findings

### 1. P0: a deployment set up from the old env file silently runs in memory and loses its cards
**Where:** `crates/apps/rusty_mill_board/src/main.rs:197-204`, `deploy/README.md:52-55`. Source-traced.

**Trigger:** any board installed from the base `deploy/board.env.example`. That file sets `MILL_BOARD_STORE=…/board.mmap` and has no `MILL_BOARD_TICK_URL`.

**Who is hurt:** the person in the UI and every agent over MCP.

**What happens:**
1. The operator follows the PR's own "Upgrade and rollback" step: replace the binary, then `systemctl restart`.
2. clap ignores the environment variables it no longer declares, so `MILL_BOARD_STORE` (and `MILL_BOARD_BACKUP_*`) are skipped without an error. `tick_url` is `None`, so the board starts on `MemStore`.
3. `/health` answers 200, because `MemStore` uses the default `health`, which always succeeds. The UI shows an empty board, and the cards in `board.mmap` are not visible.
4. Agents call `card_add` and `card_take` and move cards. Each write succeeds.
5. The next restart (`Restart=always`, an upgrade, a crash) throws all of that work away.

The new binary also cannot open the old store any more. ADR-0012 says a deployed `--store` board "is exported with `board_list` and imported the same way". The PR's upgrade steps do not say to do that first, and they do not say to set `MILL_BOARD_TICK_URL`. Passing `--store` on the command line fails loudly; the env-file form, which is the one the service actually uses, does not.

**Required correction:**
- Refuse to start when `MILL_BOARD_STORE`, `MILL_BOARD_BACKUP_DIR`, `MILL_BOARD_BACKUP_SECS` or `MILL_BOARD_BACKUP_KEEP` is set. The message should say they were removed and point to the migration.
- Preferably, also refuse an in-memory board in `--http` mode unless an explicit opt-in flag is given.
- Add an upgrade step to `deploy/README.md`: export with `board_list`, then set `MILL_BOARD_TICK_URL` and `RUSTY_TICK_TOKEN`.

**Regression test:** a process test (`CARGO_BIN_EXE_rusty_mill_board`) that starts the binary with `MILL_BOARD_STORE=<path>` and `--http`. It should expect a non-zero exit with a message naming the variable. Today it would come up healthy.

### 2. P2: the fix to card reads has no test that pauses at the race
**Where:** `src/tick.rs:483-507`, `tests/execution.rs:23-65`. Source-traced.

**The race:** reader A reads the task at version v1. Writer B's document `PUT` with `If-Task-Match` moves it to v2. A then reads the new document. Without the second version check, A returns the new execution link at rev 0. The change fragment says this is fixed.

**Why the test doesn't guard it:** the only test is `identical_execution_binding_is_concurrent_and_restart_idempotent`, which starts 8 threads on a barrier and asserts `rev == 1`. It catches the bug only if the scheduler happens to interleave at that point. `FakeTick` has no hook to stop between `GET /tasks/{id}` and `GET /docs/board` (`tests/fake_tick/mod.rs:68-110` has only `owner_edit`, `owner_comment` and `fail_next`). The same test existed at base on the mmdb store (`tests/service.rs:67`), where this race cannot happen. Nothing covers the `Conflict` returned after `READ_ATTEMPTS` (8) tries either.

**Required correction:** add a "before this request" hook to `FakeTick`, and a test in `tests/tick_store.rs` or the `tick.rs` unit tests. The hook writes the board document and moves the version after the first task read; the test asserts that `get` returns the new document at the new revision. Add a second test where the task keeps moving and `get` returns `Conflict`. The first test goes red if the re-read at `tick.rs:494-497` is removed.

### 3. P2: the only process-level test of the binary was deleted, though the behaviour it checked remains
**Where:** `tests/service.rs` was deleted. At base, `the_server_reports_health_keeps_its_first_credential_off_stderr_and_stops_on_sigterm` was `service.rs:745-804`. Source-traced.

That test checked three things against the real binary:
- `/health` answers 200;
- the first admin's token (`main.rs:134-142`) stays out of stderr;
- a real SIGTERM through `stop_on_signal` exits 0.

Only the one-owner lock part of it is obsolete. After this PR, `project_export.rs` is the only test that runs the binary, and nothing else checks that the credential stays out of the log or that a real signal exits cleanly. `shutdown.rs` drives the drain in-process, and `tests/settings.rs:137` checks only the token file's mode.

**Required correction:** keep that process test, started without `--store` (or against a `FakeTick`-backed URL), and drop only the second-process assertion. It goes red if the token is logged or if SIGTERM stops exiting 0.

### 4. P2: docs that the PR leaves false
Source-traced.
- `src/main.rs:12-13` still says `/health` "answers 200 while the store can commit and 503 once it needs a restart". The PR's README and `deploy/README.md` now say 503 means rusty_tick is unreachable.
- `src/store.rs:37` and `src/board.rs:204` still say health fails once the store "must be reopened". That described the mmdb store, which is gone.

**Required correction:** reword all three to describe rusty_tick being unreachable. This is docs only, so no test is needed.

## Failure classes

- **Lost update:** not introduced. Writes are unchanged. `parts` only adds a re-read, and any write made from a mixed read is still refused with 412 by the If-Match / If-Task-Match fence.
- **Two-step write:** unchanged (`card_plan`, task then document, each fenced). Removing backups removes the operator steps that touched a running store's files (#1170, #1173).
- **Silent fallback or reuse:** finding 1.
- **Scope leak:** no change to authority, projects or credentials. The `migrated:` filter is display-only.
- **Token races:** none touched. `RUSTY_TICK_TOKEN` handling is unchanged, and an empty token is refused (`tick.rs:118`).
- **UI out of step:** the chip filter is applied each time cards are drawn, so a poll keeps it. The dialog keeps the full tag list, so a save does not drop the marker.
- **Tests that miss the window:** finding 2. `tick_store_never_skips_an_event` now uses the in-memory `FakeTick`; I did not run it to check for flakiness (validation gap).

## Design questions (not blocking)
- ADR-0012 keeps the in-memory adapter "for tests", but the README and change fragment now present an in-memory board as a normal mode, including `--http`. Should HTTP mode need an explicit flag for that?
- The UI hides chips by the prefix `migrated:`, but the board does not reserve that prefix (the migration script does, at `tools/migrate_artifact_board.py:57`). A user tag like `migrated:foo` would be hidden too. One option is to match only `migrated:mill-board`; another is to reserve the prefix in `domain.rs`.

## Optional improvements
- `get` can now return `Conflict` on a plain read. An agent calling `card_get` would see "stale revision" without having written anything. A storage-busy error that says to retry would be clearer.
- `docs/adr/0005-service.md` still describes the backup mechanism in its body. The status line marks it removed, which is acceptable for an ADR, but a one-line "superseded sections" note would help.

## Checks
- **Ran:** `python3 -I .github/scripts/changes.py check` printed "462 fragment(s) ok".
- **Ran:** `git fetch` and SHA pin, as above.
- **Not run:** `cargo test -p rusty_mill_board`, clippy, and running the new tests at base. The repository pins toolchain 1.98.1, and rustup could not install it (read-only `~/.rustup`). With the installed stable 1.98.1, crates.io access was denied by the sandbox (`index.crates.io:443`, user denied). `--offline` failed because the local registry has no `boxcars`. So no test, clippy or probe was executed, and every trace above is source-traced.
- **Validation gaps:**
  - The full test suite and clippy at head.
  - The new tests at base, to show which ones guard anything.
  - A probe of finding 1: start the binary with `MILL_BOARD_STORE` set and `--http`, and expect it to come up healthy on memory.
  - The `tests/ui/projects.mjs` browser check (Playwright). I did not run it.

## Verdict
**Changes required.**
