<!-- bbp-swf/988-r1 run 4, variant baseline, prompt e4b237b0026cd618246788f721016ce7e8a92784, model claude-opus-5-5, 2.1.314 (Claude Code); 269 s, 2211062 tokens, $1.52 -->

I found two blocking defects and two smaller gaps in PR 988, so the verdict is **changes required**.

**Pinned review:** Rusty-Mill/rusty_mill PR 988. Base `90ee60b9e7bdaccedd8c22c04e67e47b9d2900ab`, head `54b9024e2b79bceccd09465005630256b531f6a4`. The fetched `pull/988/head` equals that head SHA.

## Blocking findings

### 1. P1: one member can redirect another member's sandbox path outside the lease
**Where:** `crates/apps/rusty_orch/crates/orch-bbp/src/lease.rs:24-38` (`confine`) and `:44-62` (`bounded`). The ADR repeats the claim at `docs/adr/0017-permission-leases.md:23-25`.

**What the PR claims:** "Nothing is kept beneath a kept write root, so a harness cannot swap a path another rule names." `bounded` only enforces this within one member's own paths. Nothing stops member B's read path from sitting under member A's write root.

**Scenario (executed in a scratch copy):**
- Lease: `write: [<dir>/src]`.
- Writer sandbox: `write: [<dir>/src]`. Judge sandbox: `read: [<dir>/src/docs]`.
- `confine` keeps writer `write=[…/src]` and judge `read=[…/src/docs]`.
- Acting as the writer, the probe removes `src/docs` and replaces it with a symlink to `<dir>/secret`. `canonicalize` on the judge's rule path then returns `…/secret`, which is outside the lease. Probe output: `PROBE judge rule now opens …/secret`.

**Why this reaches the host (source-traced):** each harness launch opens rule paths with `open(path, O_PATH | O_CLOEXEC)` and no `O_NOFOLLOW` (`crates/platform/rustils/crates/platform-linux/src/sys/security.rs:162`). The judge's next turn therefore gets Landlock read access to whatever the writer pointed at, such as `~/.ssh`. The lease, and the judge's own sandbox, never granted that path.

The swap itself already existed with operator-written sandboxes. This PR introduces a guarantee that claims to close it, and it does not. If any agent turn ever plants that symlink, a harness reads host files outside its grant.

**Required correction:** in `confine`, take the union of every assigned member's kept write roots and either drop or refuse any member's kept read or write path that lies strictly beneath a root in that union. Alternatively, pin rule paths so a later swap cannot redirect them.

**Regression test:** two assigned members where one's read path sits under the other's write root. It must be refused or dropped; it is kept today.

### 2. P2: cancel can be refused because of the lease, contradicting the docs
**Where:** `crates/apps/rusty_orch/crates/orch-bbp/src/lib.rs:837`. `cancel` calls `self.freeze(…, fence)`, and on a task that was never frozen, `freeze` calls `lease::confine` (`lib.rs:274-277`).

**What the docs promise:** the ADR ("Cancel is never refused for an ended or different lease"), the change fragment ("Cancel is never refused for a lease") and the doc comment at `lib.rs:818`.

**Scenario (executed):** the hosting config has a lease and an assigned member without a sandbox. The first `open` fails permanently at `confine`, so nothing is frozen. `run --cancel` then takes over with the same lease, and `host.cancel` returns `Err(Permanent("pool member judge is unconfined; a lease needs every assigned member sandboxed"))`. The goal cannot be cancelled until the operator removes the lease from the config.

**Required correction:** when `cancel` freezes a task, do not apply `confine`. It starts no harness, so confinement is not needed there. Any manifest it publishes must still refuse a later leased `open`; recording the fence's lease paths would do that.

**Regression test:** this probe, which should return `Cancelled`. Add a second case: cancel under an expired lease and under a different lease, both on an already-frozen task. Neither case has a test today, although the ADR promises both.

### 3. P2: the docs overstate what the lease bounds
**Where:** `changes/2026-10-24-orch-permission-leases.md` (Known limitations), `docs/adr/0017-permission-leases.md` and `RELEASE_NOTES.md` ("confines a hosted goal").

**Problem (source-traced):** the lease only narrows pool members' sandboxes. Validator runs execute code the agents wrote, for example `./.bbp-tests` compiled from the candidate (`rusty_bbp_host/src/profiles.rs:80-97`). They run with the profile set's `read_roots` plus the task's work directory (`rusty_bbp_host/src/runner.rs:323-350`), and the lease never touches those. The limitations list names system roots, the turn workspace and the object store, but not profile read roots or runner work.

**Required correction:** either list this limitation explicitly, or check the frozen `profiles.read_roots` against the lease.

### 4. P2: missing tests for changed behaviour
- **Relay:** nothing covers the new `leased` check on `relay` (`lib.rs:732`). Removing that line keeps every test green. The test belongs in `orch-bbp/src/tests.rs` beside `a_leased_task_runs_confined_and_refuses_changed_or_ended_leases`.
- **Cancel:** nothing checks that cancel ignores an expired or different lease (see finding 2).
- **Pre-PR tests:** the new tests use APIs added by this PR, so they cannot compile at the base. That is expected and not a problem.

## Design questions for the owner
- `leased` compares lease path vectors exactly as strings. Listing the same paths in a different order, or with a different spelling of one path, permanently fails the task instead of being treated as the same lease. Should the comparison canonicalize and sort the paths?
- The documented limitation that "a renewal with an earlier end does not shorten a running moderator" also covers this case: switching `ttl_secs` from unset to set keeps the earlier moderator, so its harnesses run past the new lease's end until it exits. That is accepted as written, but worth confirming it is acceptable.

## Optional improvements
- `lifetime` measures time left on the wall clock, while the moderator counts `max_wall` on a monotonic clock that does not advance during suspend. After a host suspend the moderator can outlive the lease. This is minor.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all --check` | passed |
| `cargo clippy --locked -p orch-bbp -p orch-core -p orch-store -p orch-dispatch -p rusty_orch --all-targets -- -D warnings` | passed, rerun in a fresh target dir |
| `cargo test` for those five crates | all pass: orch-core 56, orch-dispatch 1+23, orch-store 5+10, orch-bbp 16 (19 ignored), rusty_orch 36+13+6+4+3+20+1 |
| `python3 .github/scripts/changes.py check` | passed |
| Scratch probes for findings 1 and 2 | both reproduce the defect |
| Panicking `unwrap`/`expect` in new non-test code | none found |

Two notes on the test runs:
- **Stale build cache:** my first clippy and test run used a shared temp target dir with stale artifacts, and orch-dispatch failed to compile against an old orch-core. I discarded those results; only the fresh-dir results above count.
- **Flaky test:** in the fresh run, `maintenance::tests::repository_lock_survives_file_presence_and_releases_on_process_end` failed once with `WouldBlock`, then passed on the rerun. This PR doesn't touch it, and I did not run it at the base.

## Validation gaps
- **Acceptance script not run:** I did not run `orch_bbp_acceptance.py` or the new ignored test `lease-confined`. They need built `bbp`/`rusty_orch` binaries and Landlock enforced, which this sandbox lacks.
- **Finding 1 on a real kernel:** I showed the redirect with `canonicalize`, not under real Landlock. To confirm it end to end, run the two-member swap scenario on a Landlock-capable host.

## Verdict
**Changes required.**
