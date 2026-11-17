<!-- agent-cloud/14-r1 run 1, variant baseline, prompt 7c00a3eee28643720845867db321dcd6e32967b9, model claude-opus-5-5, 2.1.314 (Claude Code); 311 s, 1904958 tokens, $1.58 -->

I'm requiring changes on PR 14: one P1 and three P2 findings. The P1 is that the new job diff runs on shared gateway threads, so one agent's job can stall the job timeouts and lifecycle calls of every other agent.

**PR:** baileyrd/agent_cloud #14
**Base:** `e51a23df087ca9d9c056fd8727da71ba1b76cbc1`
**Reviewed head:** `bb1b6b382b44cb29d430f9bcfefb70881a556cc7` (the fetched `pull/14/head` matches it)
**Scope:** ADR-019. A job takes a btrfs snapshot of the workspace when it starts and another when it ends. The paths that changed between them, found with `btrfs send --no-data` and `btrfs receive --dump`, go into the agent's history as one line.

## Blocking findings

### P1: the end-of-job diff runs on the gateway's shared paths, and the agent controls how long it takes
**Where:** `crates/ac-gateway/src/jobs.rs:990-992` (`transition` → `note_changes`) and `crates/ac-ctr/src/snap.rs:75-124`. The callers are `jobs.rs:902` (tick via `supervise`), `jobs.rs:858` (`reconcile`), `jobs.rs:1087` (`interrupt`), `crates/ac-gateway/src/lib.rs:530` and `crates/ac-gateway/src/main.rs:490,510`.

**Scenario (source-traced):** `transition` calls `note_changes` in the caller's thread. That runs five `btrfs` commands in a row: a snapshot, `send`, `receive --dump`, and two deletes. Each has its own 120 s `timeout` (`main.rs:405`). A send's cost grows with the inodes and extents that changed, and the agent decides that. For example, a job that creates a million small files or writes a sparse file in 4 KiB pieces. The diff then lands in three places:
- **The tick thread** (`main.rs:507-511`): the only thread that supervises all agents' jobs. While it runs one agent's diff, no other agent's job is timed out, probed, collected or started. Job timeouts (ADR-010) are a limit, and here one tenant stops them being applied to the others for up to about 10 minutes per job, again for every job it submits.
- **`vacate`**: pause or destroy with `?force=true` runs `jobs.interrupt` inside the one global `Mutex<Api>` (`web.rs:227`). Every lifecycle call for every agent waits, and so does the watchdog (`main.rs:537`). ADR-019's Consequences ("diffed under the lifecycle lock") describe this, but that lock is the whole gateway's.
- **Startup reconcile** (`main.rs:490`): this runs before `READY=1`. After a host reboot, every running job's agent is down, so each job is diffed one after another on a cold disk. The unit gives `TimeoutStartSec=180`, and its comment (`deploy/ac-gateway.service`) budgets for "one external command hitting its 120 s timeout". One job's diff is already three or more such commands. systemd kills the gateway mid-diff, and the snapshots leak (see the third P2).
- **Memory:** `receive --dump` stdout is read whole into the root gateway (`lib.rs:238`, `spawn_wait`), and the unit sets no `MemoryMax`.

**Should:** take the `.end` snapshot (cheap) at the transition. Run send, dump and cleanup on a separate worker, outside the tick, the Api lock and startup. Cap the dump that is read in. At startup, either queue the diffs or drop the snapshots.

**Regression tests:** a `Changes` fake whose `end` blocks. Then:
- one agent's job ending must not delay another agent's `Timeout` in the same `tick`;
- `reconcile()` must return while `end` is still blocked.

### P2: a file named like a btrfs temporary name hides every change to it
**Where:** `crates/ac-domain/src/changes.rs:263-269` and `:316-326`; the claim it breaks is at `changes.rs:248-250` and in ADR-019's "Paths" bullet.

**Scenario (executed in a scratch copy of the module):** btrfs gives new files temporary names shaped `o<ino>-<gen>-<seq>`, which the parser drops. But `note` puts any path whose last part has that shape into the dropped set, whatever the command. Probe results:
- `update_extent ./s/o1-2-3` → `[]`
- `unlink ./s/o1-2-3` → `[]`
- `chmod ./s/d/o7-7-7 mode=4755` → `[]` (a setuid mode change, unreported)
- a new file created, renamed to `o1-2-3` and written → `[]`
- control: `update_extent ./s/a` → `["a"]`

The doc says such a file is "left out only where it is created or renamed to". In fact an agent can write, delete or chmod such files and the note never shows it. That undercuts ADR-019's claim that what a job did is "known from the filesystem, not from the agent's account".

**Should:** treat a name as temporary only between btrfs creating it (a `mk*` or `symlink`, or a rename destination) and btrfs renaming it away. Credit operations in that window to the final name, and report any temp-shaped name still unresolved at the end.

**Regression test:** the probe cases above, in `changes::tests`.

### P2: job snapshots a crash leaves behind are never cleaned up except by a purge
**Where:** `jobs.rs:978-992` (`put_job` saves the terminal state before `note_changes`) and `snap.rs:84-93`.

**Scenario (source-traced):** a crash, a `systemctl restart`, or the start timeout above lands between saving the end state and the cleanup. That leaves `<job>.start` (and possibly `<job>.end` or `<job>.send`) in `checkpoints/<id>/`. The job is now finished, so nothing ever looks at it again, and only `purge` (destroy with purge) removes these files. Meanwhile they:
- are not listed;
- are not counted under `AC_CHECKPOINT_MAX`;
- keep data the agent deleted, and its disk space, with no time limit.

`docs/security.md:17` says that kind of retention is "capped per agent and deleted by purge".

**Should:** have `reconcile` (or startup) sweep job snapshots that belong to no running job. The `job_snapshots` helper already exists for this.

**Regression test:** a leftover `<job>.start` for a finished job is removed by reconcile.

### P2: the docs and an earlier ADR now contradict the code
- `docs/security.md:190` says "history records only job ids and states", and ADR-010's Events rule (`docs/decisions.md:248-250`) says "ids and states only … no prompt or output". History lines now carry file names the agent chose. Neither document was amended, and the threat table has no row for it. Holders of `viewer:read` and callers of MCP `get_history` read these names. The viewer itself bans `innerHTML`, so this is not an HTML-injection risk.
- The test `history_notes_carry_ids_and_states_only` (`tests/jobs.rs:417`) now asserts a line containing paths, which contradicts its own name.
- **Fix:** amend ADR-010, `security.md:190` and the threat row at line 17 (to cover job snapshots), and rename the test.

## Design questions for the owner
- **What the note credits to the job:** the `.end` snapshot is taken when the gateway notices the job ended, not when it exited. That can be up to one probe interval later. Any edits made in that time, or by an interactive session or a viewer typing during the job, are credited to the job.
- **Unverified on real btrfs:** when a new directory is moved under a newer one, btrfs may create its children under a temporary parent path. The parser would then report something like `o300-…/f` instead of the real path. This needs the loopback probe below.

## Optional improvements
- `start()`'s refused-start branch calls `end()`, which takes a full end snapshot and send just to throw them away. A cheaper "drop" call would do.

## Checks performed
- **`scripts/ci-local.sh rust`:**
  - The first run failed because `~/.cargo` is read-only in the sandbox. I re-ran with a scratch `CARGO_HOME`.
  - `cargo fmt --check` and `cargo clippy --workspace --all-targets --locked` (with `-D warnings`) passed.
  - `cargo test --workspace --locked --no-fail-fast` passed everywhere except `ac-gateway --test notify`: four tests fail with "Operation not permitted" at `notify.rs:18` when run in this sandbox. The PR doesn't touch that file, so I count it as an environment problem and a validation gap, not a pass.
  - All of the PR's new tests passed.
- **`scripts/ci-local.sh scripts`:** 13 files pass. dash is not installed here, so this used `bash -n`, which is less strict than CI's dash.
- **Parser probe:** compiled `changes.rs` alone with `rustc` and fed it hostile listings; results are in the second finding.
- **New Rust code:** no `unwrap` or `expect` outside tests, and no I/O in `ac-domain`.
- **New tests guard something:** all new tests are additions that cannot exist at base, so they guard the new behaviour. No test covers the tick, startup or lock paths, or crash leftovers (see the P1 and the snapshot-leak P2).

## Validation gaps (need root, btrfs or the real host)
- `sudo scripts/btrfs-checkpoint-test.sh`: the `on_real_btrfs` job-diff steps. I did not run it; the test is `#[ignore]`d and needs root.
- A loopback btrfs `send --no-data` probe for the temporary-parent-directory ordering question.
- How long a diff takes on BaileyServ's rotational disk at a cold startup after a reboot. That decides whether the 180 s start limit is actually hit.

**Verdict: changes required**
