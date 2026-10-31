<!-- agent-cloud/14-r1 run 2, variant baseline, prompt 7c00a3eee28643720845867db321dcd6e32967b9, model claude-opus-5-5, 2.1.314 (Claude Code); 387 s, 1635000 tokens, $1.61 -->

# Review of PR 14 (baileyrd/agent_cloud): each job's changed paths in the agent's history (ADR-019)

- **Base:** `e51a23df087ca9d9c056fd8727da71ba1b76cbc1`
- **Head reviewed:** `bb1b6b382b44cb29d430f9bcfefb70881a556cc7`. The fetched `pull/14/head` matches this SHA.
- **Commits:** `29689c7` (the feature) and `bb1b6b3` (merge of main).

**Verdict: changes required.** There is one P1 and three P2 findings.

## Blocking findings

### P1: The diff buffers its whole output in gateway memory, and writes it to the host disk, with no cap
- **Where:** `crates/ac-ctr/src/snap.rs:325`, `:345-366`, `crates/ac-ctr/src/lib.rs:678-687` (`read_all`), `crates/ac-domain/src/changes.rs:16-53`
- **What happens (part executed, part source-traced):**
  - `btrfs send` writes its whole stream to `<data>/checkpoints/<id>/<job>.send` on the host's data disk. No agent limit covers that file; ADR-018:801 says per-agent disk quotas are still open.
  - `receive --dump` stdout is then read whole into a `Vec<u8>` by `read_all`, which has no cap.
  - `changes::paths` then keeps every path in a `BTreeSet`. `CHANGE_NOTE_CAP` only trims the note afterwards.
  - Path length is under the tenant's control.
- **Measured here:** I hand-built a send stream and ran it through `btrfs receive --dump` (btrfs-progs v7.1, no root needed). With a 3.8 KB directory prefix and 2,000 new empty files, the stream was 15.3 MB and the dump 15.5 MB. That is about 7.7 KB per file, plus about 3.8 KB per path held in the set.
- **Attack:** a job (or a prompt-injected agent) creates 200k empty files under a deep directory chain. That costs the workspace only metadata. The gateway then holds about 2.3 GB at once and leaves a 1.5 GB `.send` file on the host disk. The kernel should send that many inodes well inside the 120 s timeout.
  - `ac-gateway.service` sets no `MemoryMax`, so the OOM killer picks a victim host-wide: the gateway, or another tenant's QEMU.
  - Every later job repeats it. The tenant ends up using host memory and disk outside its limits.
- **Required correction:**
  - Pipe `send` into `receive --dump` instead of writing a file, or cap the file's size.
  - Read the dump as a stream with a byte cap, and keep at most N distinct paths, reporting "more than N".
  - Kill the pipeline when a cap is hit, then clean up.
  - Add the threat ("job metadata churn consumes gateway memory and host disk") to the `docs/security.md` threat table.
- **Regression tests that should go red without the fix:**
  - In `snap.rs` tests: a fake `btrfs` whose `receive --dump` prints more than the cap. Assert `job_changes` returns a bounded result and leaves nothing behind.
  - In `ac-domain`: a property that `paths` never keeps more than N entries.

### P2: The temp-name filter hides any agent change to a file named `o<n>-<n>-<n>`
- **Where:** `crates/ac-domain/src/changes.rs:28-33`, `:81-91`
- **Executed:** I added a probe test to a scratch copy. `paths()` returned `[]` for this input:
  - `update_extent ./s/o1-2-3`
  - `rmdir ./s/o9-9-9`
  - `mkfile`, then `rename` to `./s/o5-5-5`, then `update_extent ./s/o5-5-5`
  - `write ./s/sub/o7-7-7`
- **Why it matters:**
  - The doc comment at `changes.rs:13-15` says a name like that is left out "only where it is created or renamed to". In fact every operation on it is dropped, at any depth.
  - So an agent can hide what it edits or deletes by its choice of names. ADR-019's point is that the record comes "from the filesystem, not from the agent's account of it".
  - `send` only gives orphans temporary names at the subvolume root, and it avoids clashing with existing directory entries.
- **Correction:** treat a name as temporary only if it sits at the root and was introduced by a create command (`mkfile`/`mkdir`/`mknod`/`mkfifo`/`mksock`/`symlink`) or as the destination of a `rename`. Everything else should be listed.
- **Test:** the probe above, asserting `["o1-2-3", "o5-5-5", "o9-9-9", "sub/o7-7-7"]`.

### P2: The whole diff runs synchronously on the shared tick thread, and under the global lifecycle lock on a forced pause or destroy
- **Where:** `crates/ac-gateway/src/jobs.rs:991`, `:997-1006`; `crates/ac-gateway/src/lib.rs:530`; `docs/decisions.md:843-844`
- **Source-traced:**
  - `note_changes` runs inside `transition`. When called from `supervise`/`start`, that is the single `tick` thread (`main.rs:508-511`).
  - When called from `vacate → interrupt`, it runs under `App`'s `Mutex<Api>`.
  - One `job_changes` is up to five `btrfs` commands, each allowed 120 s, so up to about 10 minutes.
  - The tenant sets how long `send` takes, through how much metadata it churns.
- **Effect:** while it runs, other agents' job deadlines are not enforced, no queued job starts, and every lifecycle call waits.
- **What ADR-019 records:** that the diff runs "under the lifecycle lock", and its cost. It does not record that one tenant can stall the scheduler and lifecycle for everyone else.
- **Correction:** run the diff off the tick thread and outside the lock, for example on a per-agent worker. When the cleanup must finish before the VM stops, take the end snapshot under the lock and run send/dump afterwards.
- **Test:** a `Changes` fake whose `end` blocks. Assert that another agent's job still times out on the next `tick`.

### P2: `docs/security.md` still says history records only job ids and states
- **Where:** `docs/security.md:190`; `crates/ac-gateway/tests/jobs.rs:408-422`
- **Source-traced:** the PR now writes agent-chosen file names into the agent's history (`job … changed n paths: …`), which `viewer:read` and `get_history` serve. The test still named `history_notes_carry_ids_and_states_only` now asserts paths.
- **Correction:**
  - Update the Task API paragraph: names are escaped by btrfs and capped at 2 KiB, and they are agent-chosen text.
  - Rename the test, or split the path note into its own test.

## Design questions (for the owner)
- **Crash leftovers stay until purge.** If the gateway restarts during a diff (for example `systemctl restart` on deploy), the `<job>.start` / `.end` / `.send` for an already-terminal job stay until the agent is purged (`snap.rs:473-484`; ADR-019 "a purge deletes any a crash left behind"). They are not listed and not under the cap, and they keep the data the tenant deleted.
  - This is documented behaviour, but a reconcile at startup could clean them up for terminal jobs.
- **Agent-writable names are filtered.** `.jobs/…` and root `.job-*` are filtered as "the runner's own files" (`jobs.rs:241-247`), but the agent can write there too, so changes there are invisible.

## Optional improvements
- No test covers the cancel-while-running, timeout and interrupt paths producing a note, nor the snapshot happening before `runner.start`. The `was == Running` guard covers them in code.
- `job_begin` after a crash between snapshot and `Start` targets an existing read-only `.start`. Today it is only logged; it could remove the stale one first.

## Checks run
- **`scripts/ci-local.sh scripts`:** 13 files pass. dash is not installed, so these ran under bash, which is laxer than CI's dash.
- **`scripts/ci-local.sh rust`:** it could not fetch the pinned rusty_mill git dependency because `~/.cargo` is read-only in the sandbox. I ran the same steps in a scratch `git archive` copy with `CARGO_HOME` in `$TMPDIR`, after `cargo fetch` from crates.io, and `RUSTFLAGS=-D warnings`:
  - `cargo fmt --check`: pass.
  - `cargo clippy --workspace --all-targets --locked`: finished with no warnings. It also prints a `{{plugin-name}}` template error, which comes from outside this diff.
  - `cargo test --workspace --locked --no-fail-fast`: 387 pass, 1 ignored (`on_real_btrfs`), and 4 fail in `ac-gateway/tests/notify.rs` with EPERM, which is the sandbox blocking those sockets. Not related to this PR.
  - All new tests pass. They use the new API, so none of them compiles at base.
- **New Rust:** no `unwrap`/`expect` outside tests, and no I/O in `ac-domain::changes`.
- **Executed probes:** the hand-built send streams through `btrfs receive --dump` v7.1, and the parser probe test.

## Validation gaps
- **btrfs-progs 6.6.3 escaping.** On v7.1, symlink targets, xattr names and xattr data with `\n` and ESC came out escaped, so I could not forge dump lines. ADR-019 relies on btrfs-progs 6.6.3, which I could not test.
  - How to settle it: on BaileyServ, create a symlink with target `x\nunlink ./<job>.end/FORGED` and a `user.*` xattr with a newline, run a job, and read the note.
- **Real btrfs.** `snap::tests::on_real_btrfs` / `scripts/btrfs-checkpoint-test.sh` need root and btrfs. I did not see them run, so the ADR's "ran against real btrfs" is unverified here.
- **Live VM.** The roadmap's "not yet on BaileyServ" stands. Nothing has shown a job with a live VM producing a note through `get_history`.
- **Send timing.** How long `send` takes for 200k files on BaileyServ's rotational disk decides how far the P1 memory spike and the P2 stall reach.
