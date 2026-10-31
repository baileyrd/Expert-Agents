<!-- agent-cloud/14-r1 run 3, variant baseline, prompt 7c00a3eee28643720845867db321dcd6e32967b9, model claude-opus-5-5, 2.1.314 (Claude Code); 388 s, 2256729 tokens, $1.82 -->

# Review of PR 14 (baileyrd/agent_cloud): each job's changed paths in the agent's history (ADR-019)

**Verdict: changes required.** One P1 and four P2 findings.

- **Base:** `e51a23df087ca9d9c056fd8727da71ba1b76cbc1`
- **Head:** `bb1b6b382b44cb29d430f9bcfefb70881a556cc7`. I fetched `pull/14/head` and it matches.
- **Commits:** `29689c7` (the feature) plus a merge of main that only resolved a conflict in `docs/decisions.md`.

## Blocking findings

### 1. P1: The gateway reads a listing whose size the tenant controls, with no cap, on its control paths
**Where:** `crates/ac-ctr/src/snap.rs:365`, `crates/ac-gateway/src/jobs.rs:990-1006`, `crates/ac-gateway/src/main.rs:490`, `crates/ac-gateway/src/lib.rs:530`

**What happens** (source-traced; parser cost executed):
- `diff` reads all of `btrfs receive --dump` stdout into memory through `run` → `spawn_wait`. That helper reads output in full; its doc says it is meant "for the small outputs of these programs" (`lib.rs:185-187`). Here the output grows with the number of inodes the job touched, which the agent chooses.
- **Memory:** a job runs `seq 1000000 | xargs touch d/f`. I built a 1M-file listing in the real dump format: 423 MB. The PR's `changes::paths` alone peaked at 520 MB RSS on it. The gateway's growing buffer adds up to twice the listing on top of that.
  - Both `send` and `receive` are bounded only by `command_timeout`, 120 s each.
  - `ac-gateway.service` has no `MemoryMax`, so this pressure lands on the host. That host also runs other tenants' QEMU processes, which the OOM killer may pick.
  - The `<job>.send` stream is written to `<data>/checkpoints`, which no tenant limit covers.
- **Where it runs:** `note_changes` runs inline inside `transition`:
  - On the single tick thread, so every agent's job supervision and timeouts stall for up to about 240 s per job end.
  - Under the lifecycle `Api` lock for a forced pause or destroy (`vacate`), which also blocks the watchdog.
  - In the startup `jobs.reconcile()` before READY=1. After a host reboot every running job is diffed there, one after another. One job's `send` plus `dump` can take 2×120 s, which is more than `TimeoutStartSec=180`. So one tenant can make the gateway's start after a reboot fail.
- **What it should do:** keep the cost within a fixed budget, outside the tick thread, lifecycle lock and readiness path.

**Fix:**
- Stream the dump through a `BufRead`, stop and kill the child after N paths or N bytes, and report "(and more)" as a count instead of storing every path.
- Pipe `send` into `receive --dump` rather than writing a stream file, or cap the stream file's size.
- Run change notes on a worker thread, and defer the startup ones until after READY.

**Tests that should go red without the fix:**
- A `snap.rs` test whose fake `btrfs` prints an endless dump: `job_changes` must return within the cap.
- A `tests/jobs.rs` test with a slow `Changes` fake: another agent's job must still time out, and `reconcile` must return promptly.

### 2. P2: A file the agent names like send's temporary names is hidden for every operation, not just creation
**Where:** `crates/ac-domain/src/changes.rs:28-34` and `:81`; doc comment at `:13-14`; ADR-019 at `docs/decisions.md:828`

**What happens** (executed against the PR's parser):
- `note()` sends any path whose last part matches `o<n>-<n>-<n>` into `temps`, whatever the command.
- The dump `mkfile ./j.end/o9-9-9`, `rename … dest=./j.end/o1-2-3`, `update_extent ./j.end/o1-2-3` gives `[]`.
- Writes to an existing file the agent named `o1-2-3` are hidden the same way. The doc comment says such names are left out "only where it is created or renamed to", so the code hides more than it says. ADR-019's stated purpose is a summary that does not trust the agent.
- **What it should do:** a temporary name should be dropped only if send introduced it and later renamed or removed it. Any temporary name still held at the end of the stream should be reported.

**Test:** `paths("snapshot ./s u\nupdate_extent ./s/o1-2-3 offset=0 len=1\n") == ["o1-2-3"]`.

### 3. P2: Job snapshots left behind by an interrupted run are never cleaned except by purge
**Where:** `crates/ac-gateway/src/jobs.rs:970` (the end state is saved) and `:990` (the diff runs afterwards); `jobs.rs:907-921`

**What happens** (source-traced):
- If the gateway stops after the terminal state is saved but before `job_changes` has cleaned up, `<job>.start` (and maybe `.end` and `.send`) stays. Triggers: `systemctl restart`, an OOM kill, or the systemd timeout from finding 1.
- After a restart the job is no longer active, so `reconcile` never revisits it.
- The same happens after a crash between `job_begin` and the Start transition, if the job is then cancelled while queued.
- Only `purge` removes these leftovers. Until then the start snapshot keeps data the tenant deleted, indefinitely and with no limit. ADR-019 says it is kept only "until it ends".

**Fix:** have startup `reconcile` delete `<job>.{start,end,send}` for every job that is not running.

**Test:** a fake `Changes` with a leftover snapshot for a terminal job; after `reconcile()` it is gone.

### 4. P2: `docs/security.md` now contradicts the code, and the threat table misses a new path
**Where:** `docs/security.md:190` and `:182`

- Line 190 says "history records only job ids and states". History now also holds file names the agent chose.
- The Task API section says the gateway "never parses" agent output and reads it "by capped execs". This PR adds a root-side parser of tenant-shaped data with no cap.
- New threat not listed: names the agent chose now reach `viewer:read` holders and MCP `get_history` clients, which are often LLM clients. That is a prompt-injection path into the operator's tools.
- The test name `history_notes_carry_ids_and_states_only` is now inaccurate too.

**Fix:** update the Task API and History text and add a row to the threat table.

### 5. P2: A cancelled job's change note can include the next job's changes
**Where:** `crates/ac-gateway/src/jobs.rs:761` and `:988-991` (source-traced)

- When a caller cancels a running job, the job's state becomes terminal and the lock is released before its end snapshot is taken.
- Meanwhile the tick thread can start the agent's next queued job, which writes before that end snapshot exists.
- The trigger is rare: it needs a slow snapshot under load. But the result is a misreport in a summary meant to be trustworthy.

**Fix:** hold a per-agent guard from the end transition until the end snapshot exists, or block that agent's next start until it does.

## Design questions
- The change note arrives after the terminal state note and after long-polls are woken. A client that waits for the job and then reads history can miss it.
- A start snapshot keeps deleted data for the job's whole run, and there is still no per-agent disk quota (`security.md:44`).

## Optional improvements
- `started_jobs_note_what_they_changed_and_drop_their_snapshot` would still pass if `begin` ran after `runner.start`. It should record call order to check the snapshot is taken first.
- No test covers the pause/destroy (`interrupt`) or `reconcile` paths producing a change note.

## Checks run
| Check | Result |
|---|---|
| `cargo fmt --check` | passes |
| `cargo clippy --workspace --all-targets --locked` (`-D warnings`) | passes (through `ci-local.sh rust`, with `CARGO_HOME` in `$TMPDIR` because `~/.cargo` is read-only) |
| `cargo test --workspace --locked --no-fail-fast` | all pass except 4 `ac-gateway` `notify` tests failing with EPERM; they fail the same way at base (worktree I added for the run and then removed), so the sandbox causes them |
| `ci-local.sh scripts` | 13 files pass under `bash -n`; dash isn't installed, so this is laxer than CI. No scripts changed. |
| New tests at base | don't compile (new API), so they do guard the change |
| `unwrap`/`expect` outside tests; I/O in `ac-domain` | none |
| Probe: real `btrfs receive --dump` (btrfs-progs v7.1) on a hand-built send stream | escapes newlines, ESC, spaces and non-ASCII in paths, rename targets and xattr names/data; no line injection on v7.1 |

## Validation gaps
- The real-btrfs adapter test (ignored, needs root) and any BaileyServ run: I did not see them run.
- Whether btrfs-progs 6.6.3 (the version ADR-019 cites) also escapes xattr data in `--dump`. I could only check v7.1; GitHub access was denied.
- How long a real `send --no-data` takes for millions of inodes, and whether the gateway actually goes over `TimeoutStartSec` after a reboot. `scripts/post-reboot-check.sh` run after a job that creates a million files would settle it.
- Whether send, on a real filesystem, lists the deleted children of a replaced directory under its temporary `o…` name. My parser probe gives `o300-5-0/f` for such input, but I couldn't confirm send produces it.
