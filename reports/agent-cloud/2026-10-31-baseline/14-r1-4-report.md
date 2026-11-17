<!-- agent-cloud/14-r1 run 4, variant baseline, prompt 7c00a3eee28643720845867db321dcd6e32967b9, model claude-opus-5-5, 2.1.314 (Claude Code); 330 s, 1803040 tokens, $1.55 -->

I'm requesting changes on PR 14 of baileyrd/agent_cloud. Three defects block it (one P1, two P2), plus a lower-priority race.

**Pin**
- Base: `e51a23df087ca9d9c056fd8727da71ba1b76cbc1`. Reviewed head: `bb1b6b382b44cb29d430f9bcfefb70881a556cc7`. I fetched `pull/14/head` and it matches the head SHA.
- What the PR adds (ADR-019): a btrfs snapshot of the workspace when a job starts and another when it ends. It compares them with `btrfs send --no-data` and `btrfs receive --dump`, parses the result in `ac_domain::changes::paths`, and writes a line to the agent's history: `job <id> changed <n> paths: …`.

## Blocking findings

### 1. P1 — An agent can stall the job supervisor and the global lifecycle lock, and fill gateway memory (source-traced)
- **Where:**
  - `crates/ac-gateway/src/jobs.rs:990` (`transition` → `note_changes`)
  - `crates/ac-gateway/src/jobs.rs:906` (`begin` inside `start`)
  - `crates/ac-ctr/src/snap.rs:319-346` and `:357-371`
  - `crates/ac-ctr/src/lib.rs:238` (`spawn_wait` reads stdout with no cap)
- **Trigger:** during a job, the agent creates a very large number of empty files, for example 10⁶ `touch`es. Nothing stops this: there is no per-agent disk quota (`docs/security.md:44` is unchecked, and ADR-018 keeps qgroups off).
- **What happens:**
  - When the job ends, `note_changes` runs synchronously. The diff is one snapshot, one send and one `receive --dump`, then two deletes. Each command may run up to `command_timeout` = 120 s (`main.rs:405`), so up to about 10 minutes per job end.
  - It runs on the single `jobs.tick()` thread (`main.rs:508`). That thread supervises every agent's jobs, so other tenants' timeouts and starts stop being handled until it finishes.
  - On a forced pause or destroy, it runs inside `Api::vacate` under the global `api` mutex (`web.rs:309`). That blocks every agent's lifecycle calls, job submits and the ADR-012 watchdog.
  - The whole `receive --dump` output (around 5 lines per new file, hundreds of MB here) is read into the gateway's memory with no cap. `paths` then builds a `BTreeSet` of every path. `ac-gateway.service` sets no `MemoryMax`, and `spawn_wait`'s own comment assumes "the small outputs of these programs".
  - The send stream is also written uncapped to the host disk under `checkpoints/<id>/`.
- **What it should do:** one tenant's job should not hold any shared thread or lock for long, and the output read should be bounded.
- **Fix:**
  - Run the diff on a worker outside the tick thread and outside the `api` lock. For pause or destroy, take the end snapshot under the lock and do the diff afterwards.
  - Stream `receive --dump` and stop after `CHANGE_NOTE_CAP` worth of paths plus a counter, killing the child once the cap is reached.
  - Use a shorter timeout for these commands.
- **Regression tests:**
  - A `jobs.rs` test whose fake `Changes::end` blocks: `tick()` should still supervise another agent's job, and a forced pause should not wait on it.
  - An `ac-ctr` test where the fake `btrfs receive` prints more than the cap: memory stays bounded and the note says `(and N more)`.

### 2. P2 — An agent can hide changes from the summary by naming files like send's temporary names (executed)
- **Where:** `crates/ac-domain/src/changes.rs:263-269` (`note` sends every temp-looking path into `temps`) and the doc comment at `:248-250`.
- **Trigger:** the agent names a file `o<digits>-<digits>-<digits>`.
- **Probe:** I copied `changes.rs` from the head into a scratch crate and ran `paths` on hostile listings:
  - Creating `o1-2-3`, then writing and chmod-ing it, gives `[]`.
  - `unlink ./s/o7-7-7` gives `[]`.
  - A real btrfs-progs 7.1 dump of a stream I built by hand containing `chmod`, `update_extent` on `o1-2-3` and `unlink o4-5-6` gives only `["keep","link"]`.
- **Why it matters:** the doc comment says such a file "is left out only where it is created or renamed to", but every action on it is hidden. That contradicts ADR-019's claim that "what a job did is known from the filesystem, not from the agent's account".
- **Fix:** treat a name as temporary only while this stream itself introduced it (a `mk*` command, or a rename target) and has not yet renamed it away. Report every other temp-looking name.
- **Regression test:** add the three probe listings above to `changes.rs` tests.

### 3. P2 — The docs still say history holds only ids and states (source-traced)
- **Where:** `docs/security.md:190` says "history records only job ids and states". ADR-010 at `docs/decisions.md:249-250` says "ids and states only … no prompt or output".
- **Problem:** history now contains file names the agent chose. Those reach `viewer:read` holders and the MCP `get_history` tool.
- **Fix:** update both passages and the Task API section of security.md: change notes carry agent-chosen file names, escaped by btrfs, capped at 2 KiB, and must be treated as untrusted text.

## Non-blocking finding (P2 — counts toward "changes required")

### 4. Snapshots left by a crash are never cleaned up except by purge, and purge can collide with a running diff
- **Where:** `jobs.rs:990`, and `snap.rs:474-483` (`purge`).
- **Crash leftovers:** if the gateway restarts after a job's end state is saved but before `end()` runs, or while the diff is running, `<job>.start`, `<job>.end` or `<job>.send` stay on disk:
  - They hold everything the job deleted or rewrote.
  - They are not listed and not counted under the checkpoint cap.
  - Nothing at startup reconciles them, so they stay until the agent is purged.
  - This makes ADR-019's "the end snapshot lives only for the diff" false.
- **Race with purge:** `DELETE ?purge=true` can run while the tick thread is still in `note_changes`, because the job is already terminal so `busy()` is false. If `purge` lists the job snapshots and then `diff` creates `<job>.end`, `remove_dir_all` hits a read-only subvolume and purge returns 500 with partial state. A retry recovers.
- **Fix:**
  - At startup, drop job snapshots whose job is terminal or unknown.
  - Serialise `note_changes` with purge, for example with a per-agent guard.
- **Test:** an `ac-ctr` test that leaves `<job>.end` and `<job>.send` behind; a sweep of an agent with no active job should remove them.

## Design questions
- **Lifecycle lock:** ADR-019 deliberately diffs "under the lifecycle lock" during pause or destroy. That lock is global across agents, so the owner should decide whether the end snapshot alone, not the diff, should run under it.
- **Cost of a refused start:** when a start is refused, the snapshot is dropped by running the full diff through `Changes::end`. A plain `drop` would avoid the snapshot, send and dump.

## Ruled out
- **Line injection through the dump:** btrfs-progs 7.1 escapes symlink targets and xattr data containing `\n` and ESC. I ran `receive --dump` on a stream I built by hand, so a tenant cannot forge extra dump lines.
- **Job snapshots treated as checkpoints:** names with a `.` fail `CheckpointId::parse`. So the job snapshots are not listed, counted, deletable through the API, or usable as a fork or restore source.
- **Secrets on a command line:** none. The argv holds only host paths.

## Checks
- **`scripts/ci-local.sh rust`: could not run.** The sandbox denied network access to github.com, which the pinned `rusty_mill` git dependency needs, so clippy and the workspace tests were not run.
- **`rustfmt --check` on the four changed Rust files:** passes.
- **`changes.rs` unit tests,** run in a scratch crate: 5 of 5 pass, plus my probe test.
- **`scripts/ci-local.sh scripts`:** "13 files pass". Dash is not installed here, so it used bash `sh -n`, which is laxer than CI.
- **New code:** no `unwrap` or `expect` outside tests, and no I/O in `ac-domain`.
- **Tests at base:** the new tests use `with_changes`, `Changes`, `change_note` and `job_begin`, none of which exist at base, so they cannot pass there.

## Validation gaps
- **`on_real_btrfs` (`#[ignore]`, run by `scripts/btrfs-checkpoint-test.sh`)** needs root and was not run. ADR-019's evidence is from btrfs-progs 6.6.3, but this Fedora 44 machine has 7.1 (whose dump no longer prints `data=` for xattrs). Rerunning that script with 7.1 would settle it.
- **Live check on BaileyServ:** not done, as the roadmap notes. Still to measure there: how long a snapshot takes on the rotational disk under load, and the job-end diff time.

**Verdict: changes required**
