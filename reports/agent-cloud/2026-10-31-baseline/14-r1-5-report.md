<!-- agent-cloud/14-r1 run 5, variant baseline, prompt 7c00a3eee28643720845867db321dcd6e32967b9, model claude-opus-5-5, 2.1.314 (Claude Code); 330 s, 1911937 tokens, $1.50 -->

# Review: baileyrd/agent_cloud PR 14, "jobs: each started job's changed paths in the agent's history (ADR-019)"

- **Base:** `e51a23df087ca9d9c056fd8727da71ba1b76cbc1`
- **Head reviewed:** `bb1b6b382b44cb29d430f9bcfefb70881a556cc7`. I fetched `pull/14/head` and it matches this SHA. The head is a merge of `29689c7` with `main`; the conflict was only in `docs/decisions.md`.

**Verdict: changes required.** There is one P1 finding and two P2 findings.

## Blocking findings

### 1. P1: A tenant's job can make the gateway use unlimited memory, and its diff blocks the lifecycle lock, the tick thread and startup
**Where:**
- `crates/ac-ctr/src/snap.rs:345-366` and `crates/ac-ctr/src/lib.rs:678` (`read_all` has no cap)
- `crates/ac-gateway/src/jobs.rs:991`, `997`, `905-917`
- `crates/ac-gateway/src/lib.rs:530`, called with `web.rs:227` holding `api.lock()`
- `crates/ac-gateway/src/main.rs:490`, which runs before `READY` at `:559`

**Trigger (source-traced):** a prompt-injected job runs `for i in $(seq 1000000); do : > f$i; done` (or 10M empty files; there is no disk quota, security.md:44).

What the code does:
- `btrfs send --no-data` writes a stream of about 250 B per new file to `<data>/checkpoints/`. That disk is outside any tenant limit.
- `receive --dump` prints about 5 lines per file. `read_all` collects all of that output into one `Vec`, so a million files is hundreds of MB.
- `paths()` then builds a `BTreeSet` of every path.
- Only after that does `change_note` apply the 2 KiB cap.
- `ac-gateway.service` has no `MemoryMax`. The gateway is the control plane for every tenant, so this is one tenant using more than its limits.

The diff is also synchronous inside `transition()`. Each `end` is up to four btrfs commands at the 120 s `command_timeout` (snapshot, send, dump, deletes):
- **Tick thread:** while a diff runs, other agents' job timeouts are not enforced and nothing starts. This undercuts ADR-013 and the job deadlines.
- **Lifecycle lock:** `?force=true` pause or destroy runs `interrupt` inside `vacate` while holding the API mutex. Every agent's create, pause and list waits, as do job submits (`web.rs:310`) and the watchdog (`main.rs:537`). ADR-019 states that this happens under the lock but does not account for the cost.
- **Startup:** after a reboot, `jobs.reconcile()` interrupts every running job and diffs each one before `READY=1`. ADR-015 sized `TimeoutStartSec=180` for one external command at 120 s. Several diffs can now exceed it. systemd then kills the gateway. Because the job state was persisted before `note_changes`, the `.start` snapshot is left behind with no record, holding deleted data until the agent is purged.

**Required correction:**
- Bound the dump. Stream `receive --dump` stdout through a line parser that stops collecting after N paths or bytes and only counts the rest, or cap the bytes read and report "(listing truncated)".
- Run `Changes::end` off the tick thread, the lifecycle lock and the startup path. For example, queue `(agent, job)` to a worker. At startup, enqueue instead of diffing.
- Have reconcile delete `.start` snapshots of jobs that are already terminal.

**Tests that should go red without the fix:**
- An `ac-ctr` test whose fake `btrfs receive` prints, say, 50 MB, asserting bounded output and a count.
- A `tests/jobs.rs` test whose `Changes::end` blocks, asserting that `tick()` still times out another agent's job and that `reconcile()` returns promptly.

### 2. P2: A file named like send's temporary names is hidden from the note wherever it is and whatever is done to it
**Where:** `crates/ac-domain/src/changes.rs:15`, `:29`, `:81`

**What happens (executed in a scratch copy of `changes.rs`):**
- Writing, chmodding or unlinking an existing file named `o1-1-1` or `dir/o2-2-2` gave `paths(...) == []`.
- A new `bin/o5-5-5` with content was also hidden.

`note()` drops any path whose last component has the shape `o<n>-<n>-<n>`, not only names send generated in this stream. That contradicts:
- the doc comment, which says such a file is left out "only where it is created or renamed to";
- the ADR-019 consequence that the record comes "from the filesystem, not from the agent's account". An injected agent can name its payload `o1-1-1` and it never appears.

**Required correction:** treat a name as temporary only if:
- it is a root-level name (no `/`; send puts orphans at the subvolume root), and
- this stream introduced it with `mk*`/`symlink`, or as a rename destination.

Any other operation on that name gets noted.

**Regression test:** add the probe inputs above to `changes.rs` tests. They should expect `["o1-1-1", "dir/o2-2-2"]` and `["bin/o5-5-5"]`.

### 3. P2: The docs still say history holds only job ids and states
**Where:** `docs/security.md:190` ("history records only job ids and states") and ADR-010, `docs/decisions.md:249` ("ids and states only").

ADR-019 now writes file names the agent chose into the same history, and neither statement was amended. Those names reach `viewer:read` holders through `GET .../history` and the `get_history` MCP tool, which an LLM client reads. That makes them a prompt-injection channel.

**Required correction:**
- Amend ADR-010 and security.md: history now holds agent-chosen path names.
- Add these notes to the *Untrusted output* paragraph and the threat table: treat them as data, never as instructions.

## Design questions
- `change_note` leaves out `.jobs/` and root `.job-*`. Those paths can be written by the agent, so it can put new files there without them appearing in the note. Is that acceptable for a summary that ADR-019 says is "known from the filesystem, not from the agent's account"?
- Snapshots that a crash leaves behind are invisible: they are not listed or counted under the cap, and are only removed by purge. Should doctor or the startup log report them?

## Optional improvements
- `started_jobs_note_what_they_changed_and_drop_their_snapshot` doesn't actually test that the snapshot is taken before `runner.start`. Record the call order in the fake.
- There is no gateway test for the `?force` pause/destroy path (`interrupt` → note).

## Checks performed
- **`scripts/ci-local.sh rust`:**
  - `cargo fmt --check` passed.
  - `cargo clippy --workspace --all-targets --locked` with `-D warnings` passed with zero warnings. I used a scratch `CARGO_HOME` because `~/.cargo` is read-only here.
  - `cargo test --workspace --locked --no-fail-fast`: every suite passed, including `ac-domain` (the new `changes` tests), `ac-ctr` (the new snap tests; `on_real_btrfs` is ignored) and `tests/jobs.rs` (29/29).
  - The exception is `tests/notify.rs`, where 4 tests fail with `Operation not permitted` when creating a socket. That is a sandbox restriction on code this PR doesn't touch, so I can't call that suite passing.
- **`scripts/ci-local.sh scripts`:** 13 files pass. dash isn't installed, so this ran under `bash -n`, which is laxer than CI's dash. The PR changes no scripts.
- **New non-test Rust:** no `unwrap` or `expect`, and no I/O in `ac-domain::changes`.
- **New tests at base:** they don't compile there (the new APIs are missing), so they all go red without the change. None covers findings 1 or 2.
- **Parser probe:** executed in scratch, results under finding 2.

## Validation gaps (need root, btrfs or the real host)
- ADR-019 says "the adapter test ran against real btrfs". I did not see `on_real_btrfs` or `scripts/btrfs-checkpoint-test.sh` run. The roadmap entry itself says the change is not yet tested on BaileyServ.
- I did not check how btrfs-progs 6.6.3 `receive --dump` escapes bytes ≥ 0x80. If it prints raw UTF-8, `words()` splits on Unicode whitespace (U+0085, U+2028) and paths get split apart. To settle it, run `btrfs receive --dump` on a stream that contains such names.
- The gateway's startup time with interrupted jobs after a real reboot hasn't been measured: `post-reboot-check.sh` on BaileyServ with a large job running.
