<!-- bbp-swf/763-r2 run 4, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 310 s, 2715077 tokens, $2.02 -->

# Review of PR 763: BBP run guards

**Verdict: changes required.** Six blocking findings (two P1, four P2); the four earlier findings (K1 to K4) are all fixed.

- **Repository:** Rusty-Mill/rusty_mill, PR 763
- **Base:** `5dbb633f9f2db1459002fc8fefee59eb4f9f2298`
- **Reviewed head:** `fa4a8a8a3bf8daff05ccb967d65ba9317b7e2aec`. I fetched `pull/763/head` and it equals that SHA.

## Earlier findings, re-checked at this head
- **K1 (fixed):** `facts` now reads each run's own `bbp_build_sha` from its saved `bbp mod` output (`pairs.rs:551`, `build_sha_of` at `pairs.rs:482-499`). It refuses one that is missing, `-dirty`, `unknown` or not a commit id. `check_builds` (`pairs.rs:559-582`) also refuses runs built from different commits, or from a commit other than the pinned HEAD.
- **K2 (fixed):** `build.rs:76` makes cargo rerun the build script on every build, and the script asks `git status --porcelain` each time. `tests/build_sha.rs` checks this against real cargo for an unstaged edit, an edited dependency, an untracked file and a moved branch; all 6 tests pass.
- **K3 (fixed):** `check_settings` refuses a run with no model evidence, a role with no model observed, or a turn with no model observation (`pairs.rs:600-614`).
- **K4 (fixed):** the cap is checked before the terminal return (`moderator.rs:412-427`), and `check_terminal` runs again after the harnesses are killed. An over-cap run now ends as a `Stop`, not a success.

## Blocking findings

### 1. P1: an invocation that never gets the lock still writes the evidence marker, so an uncapped run counts as evidence
- **Where:** `moderator.rs:372-373`; `check_evidence_marker` at `moderator.rs:592-622`, writing at 613.
- **What happens:** `check_evidence_marker` reads the marker, checks for turn grants and writes the marker, all before `lock()` is taken.
- **Interleaving (executed):**
  1. Moderator U starts without `--max-usd`. It finds no marker and takes the lock. It has not granted a turn yet, and if no turn can be granted yet, that gap is long.
  2. Moderator C starts with `--max-usd 1000 --criteria F`. It sees no grant and writes the marker.
  3. C then fails with "another moderator holds task".
  4. U carries on with `cost_cap: None`. That means no pre-run pin check, no live cap, no `CACHE_TTL` pins and no `CLAUDE_CODE_SUBAGENT_MODEL`.
  5. `bbp pairs` → `require_evidence_marker` accepts the run.
- **Probe:** `probe_a_capped_invocation_that_never_gets_the_lock_still_writes_the_marker` (scratch copy of `moderator_e2e.rs`) holds the lock, runs C, gets the lock error, and then `require_evidence_marker(...)` is `Ok`.
- **Doc this makes false:** the new criteria-note text "Only a task nothing has run on can start as an evidence run" (the per-run cap paragraph).
- **Fix:** take the lock first, then check and write the marker while holding it.
- **Regression test:** the probe above, asserting the marker is absent.

### 2. P1: a log line caught mid-write stops a healthy capped run and records it as a cap failure
- **Where:** `tokens.rs:643-649` (`scan` counts any line starting with `{` that fails to parse) → `tokens::of` returns `Err` → `enforce_cap` at `moderator.rs:562` → stop with kind `Cap`.
- **What happens:** while the run is live, the last line of a harness log may still be being written. A partial line is a normal state, not an unreadable log.
- **Probe (executed):** `probe_a_line_still_being_written_stops_a_capped_run`. A fake `claude` prints half of an assistant line, sleeps 4 s, then finishes it. `bbp mod` stopped after 90 ms with `"stop":"cap"`, reason "1 line(s) of JSON could not be read".
- **Effect on the decision:** `pairs` marks the run `over_cap`, so it is a failed pair. That feeds criterion 2. The N=2 arm runs more harnesses at once and prints large `user`/tool lines, so it is likely to be hit more often, which skews the arms.
- **Trigger:** any poll that reads a log while a write longer than a page is in progress.
- **Fix:** in the live check, ignore a final line with no trailing newline. Keep the refusal for complete lines, and at the end of the run.
- **Regression test:** the probe, expecting no stop.

### 3. P2: a log that exists but cannot be read is counted as "no output", so the live cap does not see it
- **Where:** `tokens.rs:638-642`.
- **What happens:** any `read_to_string` error, including invalid UTF-8 or `EACCES`, is treated like a missing file. The turn is not added to `turns`, so `enforce_cap`'s check for logs it cannot read (`moderator.rs:572-583`) never sees it, and the floor adds zero for it.
- **Probe (executed):** `probe_a_log_with_one_invalid_utf8_byte_...`. A $10 assistant line plus one `0xff` byte gives `without_output: 1`, `turns: []`, `usd_floor: Some(0.0)`.
- **Why P2:** `bbp pairs` still refuses the evaluation later (through `no_model_turns`), but the live cap ignores that harness for the whole run.
- **Fix:** treat only `NotFound` as no output. Any other error should be an `Err`, or a turn marked as unreadable.

### 4. P2: the agents-file check reads only the first occurrence of a repeated flag
- **Where:** `pins.rs:562-569` (`--output-format`), `578-585` (`--setting-sources`), and `list_values` at `512-528` (`--tools`, `--disallowedTools`).
- **Probe (executed):** `bbp agents-check` accepted a launcher with `--setting-sources "" --setting-sources user,project --tools Read --tools Agent` and exited 0.
- **Effect:** if the harness's argument parser takes the last value, or adds up a list flag that is repeated, settings files and subagents come back. Whether Claude Code does this is a validation gap (see below).
- **Why P2:** the damage is bounded. The models actually used are still checked after the run, and every priced turn must agree with `total_cost_usd`.
- **Fix:** refuse a repeated `--model`, `--output-format`, `--setting-sources`, `--tools` or `--disallowedTools`.
- **Regression test:** each of those flags given twice is refused, and a single one still passes.

### 5. P2: `bbp grade release` can be re-run, so approved scores can be "re-sealed" after the unused versions were seen
- **Where:** `grade.rs:372-383` writes `release.json` without checking whether one already exists.
- **Probe (executed):** an extended `grade_pack_and_join_work_on_real_run_dirs`. After `join` refused the edited scores, re-running `release` succeeded, and `join` then accepted the edited scores.
- **Doc this makes false:** "so they cannot be edited after the unused versions were seen" (`docs/bbp-concurrency-design.md:289`), and the same line in the change fragment.
- **Fix:** refuse when `release.json` or `unused.json` already exists, and state in the doc that the seal holds only if the hash is recorded somewhere the grader cannot change (for example, posted before the release).
- **Regression test:** a second `release` into the same directory is refused.

### 6. P2: a new test fails intermittently under plain `cargo test`
- **Where:** `tests/review_findings.rs:19-24` (`scratch`) and the tests at 201-213 and 366-400.
- **Executed:** 3 failures in 7 runs of `cargo test -p rusty_bbp_host --test review_findings`. Panics at `:213`, `:377` and `:37`.
- **Cause:** `scratch` names its directory by tag and process id, then deletes it first. Tests running in parallel use the same tags (fixture names such as `completed-ttl-1h`), so they delete each other's directories.
- **Scope:** CI runs nextest, one process per test, so CI is not affected. Local and documented `cargo test` runs are.
- **Fix:** give each test its own directory, for example a per-test unique suffix.

## Questions for the owner (not findings)
- The PR adds two decision-table rows ("1 or 2 not yet known to hold", and "3 holds, but 7, 4 or 5 is not yet known to hold") and the grading-order procedure. It also changes the price file (`cache_read` for Sonnet 0.10, the 5-minute and 1-hour write prices). The fragment cites owner decisions for some of these, and the note is still marked "proposal". Please confirm the new rows are approved.

## Optional improvements
- `bin/bbp.rs` reads the criteria file twice, once for the prices and once for the sha, so the two could come from different contents. Read it once.
- `unused.held.json` is written next to `pack.json` and `key.json`. Writing it to a separate directory would make handing the pack directory to the grader safe.

## Checks run
- `cargo fmt --all --check`: pass.
- `cargo clippy --locked -p rusty_bbp_host --all-targets -- -D warnings`: pass.
- `cargo test --locked -p rusty_bbp_host --no-fail-fast`:
  - All other test binaries pass: lib 113, build_sha 6, moderator_e2e 30, pairs_load 9, terminal_cap 7, and the rest.
  - `review_findings`: fails intermittently (finding 6).
  - `runner_e2e::sandboxed_workload_reaches_no_socket_endpoint`: fails because my sandbox refuses binding a Unix socket (EPERM). The PR doesn't touch that file, so this is a validation gap, not a pass.
- `python3 .github/scripts/changes.py check`: pass (124 fragments).
- Probes in a scratch copy (`git archive` of the head): the four executed traces above.
- Not run: `orch_bbp_acceptance.py` (the PR doesn't touch Orch); any real provider.
- Not done: running the new tests at the base. The PR adds `pins`, `release` and the cap, which do not exist at the base, so most new tests cannot compile there.

## Validation gaps
- Whether Claude Code 2.1.300 takes the last value of a repeated `--setting-sources`, or adds up repeated `--tools`. A capture of `claude` run with both duplicates would settle it.
- How often a real harness's log is read mid-line during a run. A pilot run with polling logged would show it.
- The Landlock and socket test, which needs a host that allows binding a Unix socket.
