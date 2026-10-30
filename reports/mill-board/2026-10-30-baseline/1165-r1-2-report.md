<!-- mill-board/1165-r1 run 2, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 229 s, 1332984 tokens, $1.23 -->

I found one data-loss defect, so the verdict is **changes required**. I couldn't build or run any Rust test: crates.io was blocked and the offline registry was missing `boxcars`. Every finding below is traced from the source, not executed.

## PR
- Repository: Rusty-Mill/rusty_mill, PR **1165**
- Base: `c4a0d031b437e9140d192f172838f4261da5b889`
- Reviewed head: `453dd9f21e5c005b13f21d59fd30093af1bfbc01`. The fetched `pull/1165/head` matches it.
- One commit, `453dd9f21`: "Mill Board: keep the cards only in rusty_tick (ADR-0012 step 6)".

## Predicted invariants (step 1)
| Touched item | Invariant | Source |
|---|---|---|
| Choosing the store at start (`main.rs:197-204`) | A missing or retired store setting is refused, never quietly replaced by a default that loses data. | Review brief, "Silent fallback"; ADR-0012 §1 says the in-memory store "stays for tests" |
| `TickStore::parts` / `get` (`tick.rs:487-507`) | A card's documents and its revision come from one task version. | ADR-0012 §2 "Commit order"; change fragment "Fixed" |
| `TickStore::list` | It may mix versions, and a write made from it is refused. | `tick.rs:18-25`, change fragment |
| `/health` | 200 while rusty_tick answers, 503 while it does not. | `deploy/README.md:13`, `tick.rs:739-748` |
| SIGTERM drain | Running requests and MCP calls finish, bounded by `DRAIN`, then exit 0. | ADR-0005, `drain.rs` |
| Settings backups (removed) | The operator still gets a consistent, restorable copy of `settings/`. | `deploy/README.md:49` |
| UI chip filter (`ui.html:144`) | Hides only the chip; saving the card keeps the tag. | Change fragment "Changed" |

## Blocking findings

### 1. P0: a leftover `MILL_BOARD_STORE` is silently ignored, and the service comes up as an empty in-memory board
- **Where:** `src/main.rs:197-204` (`None => Box::new(MemStore::default())`). The `store` argument and its `env = "MILL_BOARD_STORE"` were deleted, along with `MILL_BOARD_BACKUP_*`. The upgrade steps in `deploy/README.md:52-55` are just "Replace the binary and `systemctl restart`".
- **Trigger:** any install made from the base `deploy/board.env.example`. It sets `MILL_BOARD_STORE=…/board.mmap` and has no `MILL_BOARD_TICK_URL`. The upgrade follows `deploy/README.md`.
- **Failure scenario (source-traced):**
  1. The operator replaces the binary and restarts, as the upgrade section says.
  2. systemd still exports `MILL_BOARD_STORE` and `MILL_BOARD_BACKUP_DIR`. clap only reads the env names an argument declares, so both are now ignored without any message.
  3. `cli.tick_url` is `None`, so the store is `MemStore`. Credentials still load from `settings/`, and `/health` answers 200 because `MemStore` uses the default `Ok(())`.
  4. For people and agents, the board looks empty. `card_take` returns nothing, new `card_add` / `card_comment` calls succeed, and every one is lost at the next restart (`Restart=always`).
  5. If GitHub sync is set up (`github.json` or `MILL_BOARD_GITHUB_REPO`), each open issue is imported as a Backlog card and its label is rewritten to `board:backlog` (`sync.rs:673-676`). That wipes the column mirror on GitHub.
  6. The unmigrated `board.mmap` stays on disk, but nothing says it is being ignored.
- **Who is hurt:** agents over MCP, the person in the UI, and the GitHub issue tracker.
- **Required correction:**
  - Keep `--store` / `MILL_BOARD_STORE`, and ideally `MILL_BOARD_BACKUP_DIR`, as retired settings that refuse to start, for example: "`--store` was removed (ADR-0012 step 6): migrate the board, then set `--tick-url`".
  - Make an in-memory HTTP board an explicit opt-in (e.g. `--in-memory`) instead of what you get when `--tick-url` is missing.
  - Add an upgrade note to `deploy/README.md`: set `MILL_BOARD_TICK_URL` and `RUSTY_TICK_TOKEN`, and remove `MILL_BOARD_STORE`.
- **Regression test:** a process test that spawns `CARGO_BIN_EXE_rusty_mill_board` with `MILL_BOARD_STORE=x` in its environment and `--http`. It should expect a non-zero exit with the message on stderr. At head it would start serving instead.

### 2. P2: the only test that ran the real binary as an HTTP service was deleted and not replaced
- **Where:** base `tests/service.rs:689-805`, `the_server_reports_health_keeps_its_first_credential_off_stderr_and_stops_on_sigterm`.
- **What is lost:** only its "second process on the same store is refused" step is obsolete. The rest still describes how the head should behave:
  - `/health` answers 200;
  - `first-admin.token` is mode 0600 and its secret never appears on stderr (`announce_first_admin`);
  - SIGTERM through `stop_on_signal` exits 0 within the drain.
- **Nothing else covers it (source-traced):** `tests/shutdown.rs` drives `serve_until_drained` in process with `MemStore`. It never runs `main`'s store selection, `announce_first_admin` or the signal thread. A regression in any of those now goes unnoticed.
- **Correction:** port the process test to the head binary, with no `--tick-url` or with a stand-in rusty_tick on a local port. Pair it with the finding 1 test.

### 3. P2: the fix to `parts` has no test that pauses at the racing step
- **Where:** `src/tick.rs:487-507`.
- **What exists:** the fix is guarded only by `tests/execution.rs:24-65`, where eight threads race `bind_execution` and the test asserts `adopted.rev == 1`.
- **Why that misses the window:** `FakeTick::send` (`tests/fake_tick/mod.rs:253-264`) has no hook between requests. The stale pairing happens only if another thread's document `PUT` lands between this thread's `GET /tasks/{id}` and its `GET /docs/board`. Whether that happens is up to the scheduler, so the test can pass at base. This is the "tests that miss the window" failure class.
- **Also untested:** the `READ_ATTEMPTS` path that gives up with `Conflict`.
- **Correction:** in the `src/tick.rs` tests, wrap the fake in a `TickApi` that, once armed, writes the board document through the inner fake (with `If-Task-Match`) right after the first `GET /docs/board`. Then:
  - assert that `get` returns the new document with `rev == version - 1` (red at base, where it returns the older revision);
  - with a wrapper that moves the task on every read, assert `Err(BoardError::Conflict)`.

### 4. P2: the main.rs module doc this PR edited still describes the removed store
- **Where:** `src/main.rs:12-13`: "`GET /health` answers 200 while the store can commit and 503 once it needs a restart."
- **Why it is false:** that was mmdb's unsettled-append behaviour. With rusty_tick, health is 503 while rusty_tick does not answer (`tick.rs:739-748`, `deploy/README.md:13`). In memory, it is always 200.
- **Correction:** match the wording in `deploy/README.md`.

### 5. P2: the new backup instruction copies a running service's settings with no consistency step
- **Where:** `deploy/README.md:49`: "Back up rusty_tick's data directory, and `settings/` here."
- **What was removed:** `Settings::snapshot` took the settings lock so that `identities.json`, `roles.json` and `projects.json` were copied as one set and checked by loading them. Base `tests/service.rs:276` and `:375` tested that guarantee.
- **Failure scenario (source-traced):**
  1. The operator runs `cp -r settings/` while an admin changes agents or roles in the ⚙ settings view.
  2. That change writes the agent file and the role file one after the other.
  3. The copy gets new identities with old roles, or the reverse.
  4. On restore, `Credentials::load` refuses an unknown role or project, so the board will not start. It fails closed, but the backup is unusable.
- **Correction:** say to stop the service (or pause settings changes) before copying `settings/`, or keep a settings-only snapshot under the `changing` lock.

## Failure classes checked (step 2)
- **Lost update:** no new read-derive-write. `parts` only reads, and every write is still fenced by `If-Match` / `If-Task-Match` (`tick.rs:535-567`). Ruled out.
- **Two-step write:** no new writes, and the `card_plan` two-step is unchanged. Restarts or backups that touch running store files are gone. The new operator step is finding 5.
- **Silent fallback or reuse:** finding 1.
- **Scope leak:** there are no changes to `api.rs`, `board.rs` scope, `identities.rs` or projects. Ruled out.
- **Token races:** no OAuth or credential code changed. Ruled out.
- **UI out of step:** the chip filter at `ui.html:144` only affects display. The dialog still sends the full tag list (`ui.html:195`, `:219`), so saving keeps `migrated:*`. Ruled out.
- **Tests that miss the window:** finding 3. The new `tick_store_never_skips_an_event` and `racing_workers_never_share_a_card_in_rusty_tick` reuse existing harnesses. I could not run them, so their stability is unknown.

## Board rules (step 3) and agent contract (step 4)
- No transition, claim, capability, reserved-tag, `card_take` or settings code changed.
- `card_get` can now return `Conflict` ("card changed concurrently; retry", `domain.rs:340`) after 8 reads that each found the task moved. The message tells the agent to retry, which is acceptable.
- No MCP tool or API reply shape changed.

## Docs (step 5)
- README, `deploy/`, ADR-0005's status line and `connecting-models.md` match the head, except findings 1 (no upgrade step), 4 and 5.
- The ADR-0005 body still describes backups. Its status line marks them as removed, so that is history, not a defect.

## Design questions
- ADR-0012 §7 says a deployed crate `--store` is "exported with `board_list` and imported the same way; its store file is kept until the tick-backed board has run".
  - After this PR, only the `.prev` binary can read such a file.
  - The fragment says the deployed board already runs on `--tick-url`. I could not check that.
  - Should the PR state the precondition, or ship an export path?

## Optional improvements
- `tests/project_export.rs` now passes `--tick-url http://127.0.0.1:9`. Its name still says it "opens no board store", but nothing checks that it makes no rusty_tick request. A `fail_next`-style stand-in, or asserting that nothing listens on port 9, would make the check real.
- `ui.html:144` hides every tag that starts with `migrated:`. Matching exactly `migrated:mill-board`, the marker from ADR-0012 §7, would hide less.

## Checks performed
- `git fetch origin pull/1165/head`: FETCH_HEAD equals the head SHA. **Pass.**
- `python3 -I .github/scripts/changes.py check`: "462 fragment(s) ok", exit 0. **Pass.**
- `cargo test -p rusty_mill_board`: **not run.**
  - Through rustup, it failed because `~/.rustup/tmp` is read-only in the sandbox.
  - With the toolchain's own cargo 1.98.1 (the pinned version), `--offline` failed with "no matching package named `boxcars`", and fetching from crates.io was refused by the sandbox's network policy.

## Validation gaps
- These were not run: the `rusty_mill_board` tests at head and at base, clippy, the new tests at base, and the browser check `tests/ui/projects.mjs`.
- To settle them, run `cargo test -p rusty_mill_board`, `cargo clippy -p rusty_mill_board --all-targets -- -D warnings` and the Playwright `projects.mjs` with crates.io access. Run `tests/execution.rs` at base on `FakeTick` too.
- Whether the live deployment's `board.env` still sets `MILL_BOARD_STORE` needs a look at the host. It decides whether finding 1 is already live after the upgrade.

## Verdict
**changes required**: one P0 (finding 1) and four P2 (findings 2–5).
