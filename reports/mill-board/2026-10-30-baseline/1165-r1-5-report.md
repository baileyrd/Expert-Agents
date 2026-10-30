<!-- mill-board/1165-r1 run 5, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 552 s, 1175762 tokens, $1.04 -->

I found three blocking problems, so the verdict is **changes required**. I couldn't build or run the Rust tests here, so the findings below come from reading the code, not from running it.

**PR:** Rusty-Mill/rusty_mill #1165
- Base: `c4a0d031b437e9140d192f172838f4261da5b889`
- Reviewed head: `453dd9f21e5c005b13f21d59fd30093af1bfbc01` (the fetched `pull/1165/head` matches it; one commit)

## What the diff touches, and the rule each must keep
- **Choosing the store at startup** (`main.rs:197-204`): a deployment that kept its cards on disk must never end up holding them only in memory without saying so. This is the "silent fallback" failure class.
- **Reading one card from rusty_tick** (`tick.rs:483-507`, `parts`): a card's revision must match the board document read with it (module comment, `tick.rs:14-25`).
- **Card chips in the web UI** (`ui.html:144`): the chip is only hidden; `migrated:*` stays on the task, and the edit dialog still sends it, so a save keeps it (`ui.html:195,219`). This holds.
- **Removed:** the board file, its lock, the in-process backups, `Settings::snapshot*`, and the `Store::backup`/`compact` methods. Nothing else in the workspace uses them (I searched `runner/`, `tools/` and the other crates).
- **No changes to:** MCP tools, HTTP routes, moves, claims, `card_take` or credentials.

## Blocking findings

### 1. P0: an old service config silently starts an in-memory board, and its cards are lost
- **Where:** `src/main.rs:197-204` with `deploy/README.md:52-55`.
- **Trigger:** a service whose `board.env` was written from the base example. That file sets `MILL_BOARD_STORE` (and `MILL_BOARD_BACKUP_*`) and has no `MILL_BOARD_TICK_URL`. The deploy README's upgrade step is just "Replace the binary and `systemctl restart mill-board`".
- **What happens (traced from source):**
  1. The new binary no longer declares the `MILL_BOARD_STORE` environment variable, so it is ignored without any message. A `--store` command-line argument would be refused, but systemd passes these settings through the environment.
  2. `cli.tick_url` is `None`, so the board starts on `MemStore`.
  3. `MemStore::health` returns `Ok`, so `/health` answers 200 and nothing looks wrong.
  4. People in the UI and agents over MCP add, claim and move cards. All of it is lost at the next restart (`Restart=always`, the next upgrade, an OOM). Backups also stop without a message, because `MILL_BOARD_BACKUP_DIR` is ignored too.
- **Why it's this PR:** at the base, the same file kept the cards on disk. The fragment says the owner's own deployment already runs on `--tick-url`, but ADR-0012 (line 224) expects "a deployed crate board `--store`, if any".
- **Fix:**
  - Refuse to start when `MILL_BOARD_STORE`, `MILL_BOARD_BACKUP_DIR`, `MILL_BOARD_BACKUP_SECS` or `MILL_BOARD_BACKUP_KEEP` is set. The error should say the option was removed and point to `MILL_BOARD_TICK_URL`.
  - Better still, make an in-memory board in `--http` mode opt-in with an explicit flag.
  - Add an "edit `board.env`: drop `MILL_BOARD_STORE`, set `MILL_BOARD_TICK_URL` and `RUSTY_TICK_TOKEN`" step to the deploy README's upgrade section.
- **Regression test:** a process test that spawns `CARGO_BIN_EXE_rusty_mill_board` with `MILL_BOARD_STORE=/tmp/x` set and no tick URL, and expects a non-zero exit with that message. Today it starts and serves.

### 2. P2: deleting `tests/service.rs` also removed the only check that the first admin token stays out of the log
- **Where:** `tests/service.rs` (deleted). The code it guarded is `src/main.rs:134-151` and `src/main.rs:153-185`.
- **What it checked:** `the_server_reports_health_keeps_its_first_credential_off_stderr_and_stops_on_sigterm` checked four things that this PR does not change:
  - the first admin token is written to a mode-0600 `first-admin.token` file;
  - the token never appears on stderr (which the service journal keeps);
  - `/health` answers 200;
  - the binary exits 0 on SIGTERM.

  Only the "second process on the same store is refused" part belonged to the removed feature.
- **What's left:** searching `tests/` for `first-admin`, `-TERM` and `CARGO_BIN_EXE` finds only `project_export.rs`. `shutdown.rs` drives `serve_until_drained` inside the test process, not the binary.
- **Trace (source-traced):** if `announce_first_admin` later falls through to its terminal branch under systemd, the admin token goes into the journal and no test fails.
- **Fix:** keep the process test and run it in memory, without `--store`. Drop only the second-process assertion. Without the change, that test goes red as soon as the token reaches stderr or SIGTERM stops exiting 0.

### 3. P2: no reliable test for the card-read fix the change fragment claims
- **Where:** `src/tick.rs:487-506`. The only test is `tests/execution.rs:23-60`.
- **The claim:** "a card read now checks the task's version again and starts over".
- **Why the test can miss it:** its only coverage is eight threads released by a `Barrier`. Nothing pauses between the first task read and the board-document read. Whether the test fails at the base depends on how the threads happen to interleave; the window can only be hit when a thread's first task read falls before the winning document write. It is the same "test that misses the window" pattern as #1246's `tick_store_never_skips_an_event`. I couldn't run it at the base (see Checks).
- **Also untested:** what `READ_ATTEMPTS` does when every attempt sees the version move (it returns `Conflict`).
- **Fix:** give `tests/fake_tick` a hook that runs a write after a chosen request (for example, before the next `GET /docs/board`). Then add a `tests/tick_store.rs` test: the task read sees version N, the owner writes a board document, and `get` must return the newer revision. Add a second test where the task moves on every read and `get` returns `Conflict`. The first goes red at the base, which reads only once.

## Docs that disagree with the code (P2, part of the same verdict)
- `src/main.rs:12-13` still says "`GET /health` answers 200 while the store can commit and 503 once it needs a restart". That describes the removed board file. The README now says 503 while rusty_tick is unreachable, and 200 always in memory. The PR edited this same comment block and left the sentence.

## Design questions (not blocking)
- A read-only `card_get` that gives up after 8 attempts returns `BoardError::Conflict`, the same error as a stale write. An agent would read it as "re-read and retry", and the reply doesn't say the task is changing right now. A distinct "busy, retry later" error, or a clearer message, would let the agent tell the two apart.
- `list` can still return a card whose board document is newer than its revision. This is documented as a known limitation, and a write made from it is refused, so there's no data loss.

## Optional improvements
- Each `parts` attempt fetches every `board` and `comment` document (`tick.rs:433-447`), and up to 8 attempts make this O(cards × attempts) per read under contention. Fetching one document by id (`GET /docs/board/{id}`) would be cheaper.

## Failure classes checked
- **Lost update:** no new read-then-write paths. All writes are still `If-Match`/`If-Task-Match` CAS.
- **Two-step write:** nothing new. `card_plan` is unchanged.
- **Silent fallback:** finding 1.
- **Scope leak:** none. Visibility, credentials and projects are untouched. Hiding the `migrated:*` chip is display only.
- **Token races:** not touched.
- **UI out of step:** the chip filter only affects rendering, and the edit dialog round-trips all tags.
- **Tests that miss the window:** finding 3.

## Board rules and agent contract
No transition, claim, capability, reserved-tag, `card_take` or MCP/API reply changed. The default roles in `docs/connecting-models.md` were not changed, only the start command. Settings still take the Admin check and the `changing` lock; only the test-only `waiting` counter was removed.

## Checks and gaps
- `python3 -I .github/scripts/changes.py check`: passed ("462 fragment(s) ok", exit 0).
- `cargo test -p rusty_mill_board`: **not run.**
  - The `1.98.1` toolchain is installed as `stable`, but `~/.rustup` is read-only.
  - `--offline` fails because `boxcars` is not in the local cache.
  - The sandbox refused network access to `index.crates.io`.

  Allowing crates.io, or supplying a vendored cache, would fix all three.
- **Not run, same cause:** clippy, the base-SHA run of `tests/execution.rs`, and the `fake_tick` versions of `commit_order.rs` and `take.rs`.
- **Not run, by nature:** the Playwright check `tests/ui/projects.mjs` (needs a browser), and the live `tick_live.rs` (needs a running rusty_tick). I also didn't run finding 1 against a real systemd unit; the trace is from the clap declarations and `main.rs`.

## Verdict
**Changes required.**
