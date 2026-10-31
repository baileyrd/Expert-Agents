# Grounding: remind-me

Target: Rusty-Mill/rusty-remind-me, scope `crates/`. Held out as bench cases: PRs 343,
344 and 346 (cases under `bench/cases/remind-me/`); their commits are removed below and
nothing from their reviews appears in the prompt.

Most review fixes here were reviewed while the crates lived in the rusty_mill monorepo
(ADR-0020), so their PR numbers (#1103, #1118, #1121 …) are rusty_mill's. The fix
commits quote the reviews; the classes below rest on those messages and diffs.

## Defect classes

Each class names the commits that back it. Merged near-duplicates; feature commits that
`mine` matched on "fix" in their body without a review are not counted.

**Memory truth** (the rarest trait)
1. **Non-atomic supersession or revision.** A change that retires, supersedes or
   completes something lands in parts, or binds to a stale revision: `29fdbfddaf`
   (an add with supersedes became one transaction; a refused add queues nothing),
   `257e1033da` (claim set/revert in one transaction; claims supersede only their own
   predicate), `362c0f9329` (decompose completion bound to the observed capture
   revision), `d3751dabbd` (check-then-capture raced a hook capture).
2. **Hidden rows leak through a derived surface.** Sensitive, superseded, expired,
   dialog or other-project rows reach a brief, a check, a lookup or a list:
   `9148aa0467`, `7b1d18a57d`, `580be19246`, `29fdbfddaf`, `78427dd182`.
3. **Time as text.** Expiry and due dates compared as strings, fixtures that decay with
   the calendar: `a1e2461e6e`, `7b1d18a57d`, `5df5589f1d`, `a068a11ab5`.

**Durable replication**
4. **The wire differs from the row.** A hand-kept column list, a type the receiver cannot
   decode, or a float that does not round-trip: `fd83cff338` (sensitive, remind_at,
   deleted_at dropped), `2de8a27aff` (integer booleans failed every memory in a batch,
   silently), `74b9cf835a` (float_roundtrip), `4ae16ab902` (outbox trigger and existing
   databases).
5. **Order and visibility under concurrency.** `68be7f91ef` (hub_seq handed out before
   commit; a cursor skipped rows), `cac7e95185` (store lock held across socket I/O),
   `4c422a6502` (writer starvation of pulls), `d3751dabbd`.
6. **Status that lies.** A report computed from the wrong key or a stale verdict:
   `4a5da25ed2`, `b4ab1728cc`, `e1d0c8371a` (availability cache keyed on time, not on
   the config it judged), `11909e1ae7` (search trimmed in silence).

**Retrieval fidelity**
7. **Admission and fusion.** One stage crowds out or hides another's results, or a
   stage has no bound: `7db043bf38` (raw rows took every semantic slot), `3abd00eda9`
   (search deadline gates stage entry). Embedding identity includes the endpoint:
   `e1d0c8371a`.

**Tool contract**
8. **Arguments or fields dropped silently; same name, different effect.** `fa3b13b891`
   (allow-list for reference lookups), `580be19246`, `dc74d67ccb` (compact JSON dropped
   trust fields), `0cbd65d709` (response_format ignored), `b4da8f667a` (entities parsed
   and discarded), `d0ced74122` (a lookup that wrote), `593f793816` (resultType missing
   for a newer protocol revision).
9. **Budgets.** Output exceeds the budget it reports: `ed6024f44c`, `9148aa0467`.

**Containment**
10. **A write path that skips the boundary.** Recapture, archive, revert or backfill
    stores what the first path would have scrubbed: `72b6df6755`, `9015bda2fb`,
    `a2615d0d13`.
11. **Caller identity and limits across a boundary.** `eddb48e0b1` and `8285218a5a`
    (the caller lost across the daemon proxy; thread-local scope), `57b570a097` (every
    caller in one rate-limit bucket), `3596d23423` (OAuth state written in place,
    permissions after rename, failures logged and dropped).
12. **Path containment before existence.** `78427dd182` (metadata path turned a tool
    into a filesystem oracle).

Cross-cutting: silent success. `2de8a27aff`, `11909e1ae7`, `f252c43546`, `b4da8f667a`
and `3596d23423` each fixed a failure that looked like success.

## Oracles

- This repo: the code, its tests, the fix commits above.
- The retired Python reference is gone (ADR-0023); behaviour it once fixed is recorded
  in the ADRs and commit messages, not in a source tree.
- Protocols: the MCP specification (lifecycle, tools, authorization; the revision a
  client negotiates), OAuth 2.1 and RFC 9700, RFC 9110/9112 for the hand-rolled HTTP
  clients and servers, the OpenAI embeddings API reference and Ollama's API docs for
  the embedder backends.
- rusty_mill's `rusty_multimodal_db` engine and `rusty_mcp_server` sources, pinned by
  the `libs-*` tag in the root `Cargo.toml`.

## Settled decisions

`ARCHITECTURE.md` (§1 tenets, §2 crate roles, threads vs tokio), `CONTRIBUTING.md`,
`docs/adr/`: 0004 (sync, LWW, union and shallow-merge exceptions), 0007 (outbox), 0016
(opaque ids), 0023 and 0025 (engine storage), 0024 (tombstones), 0026 and 0029
(capture and the session ladder), 0027 (project claims), 0028 (one contract for every
client), 0030 (tool groups, ten-tool core), 0031 (decision follow-ups).

## Kept out of the prompt

Specific held-out mechanisms are not restated as rules: how the redactor matches
secrets and card numbers, how a merge orders its writes against the outbox, and how the
embedder parses and frames responses. `4ae16ab902`'s trigger condition backs class 4
but is cited only as "the outbox" for that reason.

## Mined table (raw)

150 commits answer a review; 8 held-out commits removed.
Read each one's diff (`git show <sha>`) for the defect class it fixed.

| Commit | Subject | Files |
|---|---|---|
| d3751dabbd | remind-me: serialize transcript captures; keep healthy projects on a listing failure | `crates/remind_me_core/src/session_ops.rs`, `crates/remind_me_core/src/transcript.rs`, `crates/remind_me_core/src/watcher.rs` … |
| 362c0f9329 | remind-me: bind decompose completion to the observed capture revision | `crates/remind_me_core/src/capture.rs`, `crates/remind_me_core/src/db/curation.rs`, `crates/remind_me_core/src/db/engine/curation.rs` … |
| 602374d9d8 | remind-me: saved_searches, and search says it lists | `crates/remind_me_core/src/tool_profiles.rs`, `crates/remind_me_core/tests/tool_profiles_test.rs`, `crates/remind_me_mcp/src/surface.rs` |
| 435b92d304 | remind_me_core: embedder cache tests assert the stub served its probe | `crates/remind_me_core/tests/embedder_availability_cache_test.rs` |
| 8558aa956d | remind-me: hold the closed port with a bound, unconnected socket | `crates/remind_me_core/src/sidecars.rs` |
| 9164940799 | remind-me: hold the closed-port fixture's port with a bound socket | `crates/remind_me_core/src/sidecars.rs` |
| fe9cdc660c | remind-me: take #1115's closed-port fixture so both branches match | `crates/remind_me_core/src/sidecars.rs` |
| 29fdbfddaf | remind-me: keep sensitive and other-project decisions out of the check; make supersedes atomic | `crates/remind_me_core/src/contradictions.rs`, `crates/remind_me_core/src/db/engine/curation.rs`, `crates/remind_me_core/src/db/queries.rs` … |
| 7db043bf38 | fix(remind-me): exclude raw rows at semantic admission; keep fallback timing | `crates/remind_me_core/src/db/engine/memories.rs`, `crates/remind_me_core/src/db/memories.rs`, `crates/remind_me_core/src/db/queries.rs` … |
| a1e2461e6e | remind-me: expiry compares instants; decision follow-ups ADR is 0031 | `crates/remind_me_core/src/context_brief.rs`, `crates/remind_me_core/src/review.rs` |
| 3c0e174771 | fix(remind-me): move the token estimator import into the brief's tests | `crates/remind_me_core/src/context_brief.rs` |
| ed6024f44c | fix(remind-me): every brief surface stays within its token budget | `crates/remind_me_api/tests/brief_test.rs`, `crates/remind_me_cli/src/session.rs`, `crates/remind_me_core/src/context_brief.rs` … |
| fe7a3e2ce8 | fix(remind-me): restore remind_me_project lost when merging main | `crates/remind_me_mcp/src/lib.rs` |
| 9148aa0467 | fix(remind-me): brief hides hidden rows from discovery, needs a name, budgets its output | `crates/remind_me_core/src/context_brief.rs`, `crates/remind_me_mcp/tests/brief_test.rs` |
| 7b1d18a57d | remind-me: review lists use brief eligibility and sort due dates as instants | `crates/remind_me_core/src/context_brief.rs`, `crates/remind_me_core/src/review.rs` |
| be890903de | remind-me: decision follow-ups ("did it hold up?") | `crates/remind_me_cli/src/main.rs`, `crates/remind_me_core/src/capture.rs`, `crates/remind_me_core/src/context_brief.rs` … |
| a2615d0d13 | fix(remind-me): claim boundary on revert, invalid projects, bundled claims in UI | `crates/remind_me_api/src/dashboard/app.jsx`, `crates/remind_me_api/src/dashboard/stores.jsx`, `crates/remind_me_api/tests/projects_test.rs` … |
| 9015bda2fb | fix(remind-me): proposal and backfill hardening per review (ADR-0027) | `crates/remind_me_core/src/project.rs`, `crates/remind_me_core/src/project_backfill.rs`, `crates/remind_me_core/src/project_proposals.rs` |
| f60c35a2cf | fix(remind-me): satisfy clippy manual_contains and cloned_ref_to_slice_refs in project tests | `crates/remind_me_core/src/project.rs`, `crates/remind_me_core/src/project_proposals.rs` |
| 257e1033da | fix(remind-me): harden project claims per review (ADR-0027) | `crates/remind_me_core/src/db/engine/memories.rs`, `crates/remind_me_core/src/db/engine/vectors.rs`, `crates/remind_me_core/src/db/memories.rs` … |
| 53fa6121c4 | fix(remind_me_remote): compare the transcript bound with main's u64 body cap | `crates/remind_me_remote/src/server.rs` |
| fa3b13b891 | remind-me: reference lookups accept an allow-list, not a deny-list | `crates/remind_me_mcp/src/surface.rs` |
| 72b6df6755 | fix(remind-me): keep redaction on recapture and archive; bound transcripts as sent | `crates/remind_me_core/src/boundary.rs`, `crates/remind_me_core/src/transcript.rs`, `crates/remind_me_core/tests/archive_test.rs` … |
| dc74d67ccb | remind_me_search: keep trust fields in compact JSON hits | `crates/remind_me_core/src/reminders.rs`, `crates/remind_me_mcp/src/lib.rs`, `crates/remind_me_mcp/src/render.rs` |
| 580be19246 | remind-me: keep search's scope on reference lookups; get needs no memory_id | `crates/remind_me_mcp/src/lib.rs`, `crates/remind_me_mcp/src/references.rs`, `crates/remind_me_mcp/src/surface.rs` |
| eddb48e0b1 | fix(remind_me_remote): carry each call's caller across the daemon boundary | `crates/remind_me_core/src/sync/mod.rs`, `crates/remind_me_mcp/src/lib.rs`, `crates/remind_me_remote/src/handler.rs` … |
| 8285218a5a | fix(remind_me_remote): stamp each call's client and send server instructions | `crates/remind_me_core/src/sync/mod.rs`, `crates/remind_me_core/tests/provenance_test.rs`, `crates/remind_me_mcp/src/lib.rs` … |
| 5df5589f1d | test: stop six fixtures expiring with the calendar | `crates/remind_me_core/tests/context_test.rs`, `crates/remind_me_core/tests/graph_sync_test.rs`, `crates/remind_me_core/tests/sync_test.rs` |
| a068a11ab5 | fix(remind_me_core): stop the search fixture decaying with the calendar | `crates/remind_me_core/src/db/memories.rs` |
| 2b02ed6f11 | test(remind_me_remote): pin limiter config and expect 401 in peer-bucket test | `crates/remind_me_remote/tests/rate_limit_peer_test.rs` |
| 57b570a097 | fix(remind_me_remote): serve with connect-info so rate limits bucket per peer (#920) | `crates/remind_me_remote/src/lib.rs`, `crates/remind_me_remote/src/server.rs`, `crates/remind_me_remote/tests/rate_limit_peer_test.rs` |
| ee0d950394 | fix(remind-me): resolve sampled watchdog PCs without return offsets | `crates/remind_me_core/Cargo.toml`, `crates/remind_me_core/src/bin/watchdog_stack_probe.rs`, `crates/remind_me_core/src/watchdog.rs` … |
| 9407bf6e6b | fix(remind-me): avoid extending completed connection lifetimes | `crates/remind_me_core/src/daemon/server.rs` |
| c22de23d3a | fix(remind-me): acknowledge shutdown after releasing the store | `crates/remind_me_cli/src/daemon.rs`, `crates/remind_me_cli/tests/daemon_cli_test.rs`, `crates/remind_me_core/src/daemon/client.rs` … |
| 3a677ac606 | build: remove 118 unused dependencies flagged by cargo shear | `crates/remind_me_core/Cargo.toml`, `crates/remind_me_mcp/Cargo.toml` |
| ea47b83541 | fix(deps): drop legacy aws rustls client; triage deny.toml advisories | `crates/remind_me_core/Cargo.toml` |
| 46eb4c434a | feat(rusty_percent): one percent codec, eleven crates adopt it | `crates/remind_me_api/Cargo.toml`, `crates/remind_me_api/src/http.rs`, `crates/remind_me_core/Cargo.toml` … |
| ad2708199f | remind_me_mcp tests: run the live-embeddings fixture in a bounded child process | `crates/remind_me_mcp/src/lib.rs` |
| 8532cab7bf | fix(remind_me): wire the wave seams together | `crates/remind_me_api/src/lib.rs`, `crates/remind_me_core/src/context_brief.rs`, `crates/remind_me_core/src/db/queries.rs` … |
| 6eba0e43fc | fix(remind_me): commit the legacy-store fixtures the copy tests read | `crates/remind_me_core/tests/fixtures/legacy_store/legacy_v31.db`, `crates/remind_me_core/tests/fixtures/legacy_store/legacy_v32.db` |
| 7458d4fe46 | feat(remind_me): schema v32 — write context, trust and provenance columns; references and sessions on the engine | `crates/remind_me_core/src/db/derived.rs`, `crates/remind_me_core/src/db/engine/copy.rs`, `crates/remind_me_core/src/db/engine/core.rs` … |
| 8e4c3255e7 | refactor: move 16 whole-file writers onto rusty_atomic_file | `crates/remind_me_core/Cargo.toml`, `crates/remind_me_core/src/api_keys.rs`, `crates/remind_me_core/src/daemon/endpoint.rs` … |
| 3a2b188214 | remind_me: a client keeps the copy allowance through the open that follows | `crates/remind_me_core/src/daemon/client.rs` |
| 74b9cf835a | remind_me: keep floats exact on the engine (float_roundtrip) | `crates/remind_me_core/Cargo.toml`, `crates/remind_me_core/src/db/engine/copy/tests.rs` |
| e9576e72c8 | remind_me: tests reach stored rows through the repositories (ADR-0023, core PR 5a) | `crates/remind_me_core/src/db/engine/mod.rs`, `crates/remind_me_core/src/db/engine/outbox.rs`, `crates/remind_me_core/src/db/engine/testing.rs` … |
| 74a8c685a5 | remind_me: vectors and import bookkeeping on the engine core, built dark (ADR-0023, core PR 4a) | `crates/remind_me_core/src/db/engine/core.rs`, `crates/remind_me_core/src/db/engine/imports.rs`, `crates/remind_me_core/src/db/engine/mod.rs` … |
| 7cf50df21c | remind_me: curation and promotions on the engine core, built dark (ADR-0023, core PR 3c) | `crates/remind_me_core/src/db/curation.rs`, `crates/remind_me_core/src/db/engine/core.rs`, `crates/remind_me_core/src/db/engine/curation.rs` … |
| dc2755095c | remind_me: reminders, feedback and history on the engine core, built dark (ADR-0023, core PR 2c) | `crates/remind_me_core/src/db/engine/core.rs`, `crates/remind_me_core/src/db/engine/feedback.rs`, `crates/remind_me_core/src/db/engine/memories.rs` … |
| 90876edf4f | test: retry lock re-acquires that race a sibling test's fork | `crates/remind_me_core/src/daemon/endpoint.rs`, `crates/remind_me_core/src/db/engine/analytics.rs`, `crates/remind_me_core/src/db/engine/archives.rs` … |
| 25da5944a2 | remind_me: saved searches on the engine behind engine-store (ADR-0023, phase 4b) | `crates/remind_me_core/Cargo.toml`, `crates/remind_me_core/src/db/engine/mod.rs`, `crates/remind_me_core/src/db/engine/saved_searches.rs` … |
| 6b73205cdc | remind_me_core: fix ann_index_test Store path under the ann feature | `crates/remind_me_core/tests/ann_index_test.rs` |
| 1434738919 | rusty_remind_me: retire the Python reference (ADR-0023, phase 0) | `crates/remind_me_core/src/archive.rs`, `crates/remind_me_core/src/db/migrations.rs`, `crates/remind_me_core/src/db/schema_indexes.sql` … |
| 23bd95dfc4 | remind_me_core: gather history and feedback SQL behind db::history and db::feedback (ADR-0022 step 4) | `crates/remind_me_core/src/db/feedback.rs`, `crates/remind_me_core/src/db/history.rs`, `crates/remind_me_core/src/db/mod.rs` … |
| 7b54293742 | rusty_multimodal_db_engine: box records so a map regrow no longer stalls writes | `crates/remind_me_hub/README.md` |
| 24e173f5ac | remind_me_hub: make the embedded engine the default store; fix the image build (ADR-0021 phase 3) | `crates/remind_me_hub/Cargo.toml`, `crates/remind_me_hub/Containerfile`, `crates/remind_me_hub/README.md` … |
| 4c422a6502 | remind_me_hub: stop the engine store starving pulls under push load; add the latency benchmark | `crates/remind_me_hub/Cargo.toml`, `crates/remind_me_hub/examples/pull_latency.rs`, `crates/remind_me_hub/src/store/multimodal/mod.rs` |
| 99d1b0f155 | rusty_remind_me: bump to 0.2.3 to release the hub_seq fix | `crates/remind_me_api/Cargo.toml`, `crates/remind_me_cli/Cargo.toml`, `crates/remind_me_core/Cargo.toml` … |
| 68be7f91ef | remind_me_hub: stop Postgres hub_seq pulls skipping concurrently pushed rows | `crates/remind_me_hub/src/store/postgres.rs`, `crates/remind_me_hub/tests/hub_postgres_test.rs` |
| a4d9a37d9d | rusty_remind_me: fix image_import_test's intermittent SIGSEGV (env race) | `crates/remind_me_core/tests/image_import_test.rs` |
| eb8eb6846e | rusty_remind_me: bump to 0.2.2 to release the hook fixes | `crates/remind_me_api/Cargo.toml`, `crates/remind_me_cli/Cargo.toml`, `crates/remind_me_core/Cargo.toml` … |
| b75e73b03b | rusty_remind_me: join the workspace (manifests, lockfile, fixes) | `crates/remind_me_api/Cargo.toml`, `crates/remind_me_cli/Cargo.toml`, `crates/remind_me_core/Cargo.toml` … |
| 593f793816 | Stamp resultType: "complete" on every MCP reply so a 2026-07-28 client can call tools | `crates/remind_me_mcp/src/lib.rs` |
| d72e714ab1 | Close three loose ends from the standards audit | `crates/remind_me_api/src/dashboard/app.jsx`, `crates/remind_me_api/src/dashboard/stores.jsx`, `crates/remind_me_api/src/routes.rs` |
| e1d0c8371a | Expire the embedder availability cache on a config change | `crates/remind_me_core/src/embedder.rs`, `crates/remind_me_core/tests/embedder_availability_cache_test.rs` |
| 87a24ce521 | Make the wiki writable from the dashboard | `crates/remind_me_api/src/dashboard/App.jsx`, `crates/remind_me_api/src/routes.rs`, `crates/remind_me_api/tests/dashboard_test.rs` … |
| d0f0cce1d8 | Run reminders, digest, status and saved searches from the web UI | `crates/remind_me_api/src/dashboard/App.jsx`, `crates/remind_me_api/src/routes.rs`, `crates/remind_me_api/tests/dashboard_test.rs` … |
| df47869f75 | Fix a cross-module test race on SYNC_SECRET_ENV | `crates/remind_me_core/src/sync/mod.rs`, `crates/remind_me_core/src/sync/server.rs` |
| 33bb6dc6e6 | Fix Windows-only test failures in import_paths_test.rs | `crates/remind_me_core/tests/import_paths_test.rs` |
| 53ecbcf4d8 | remind_me_core now has local test coverage for metrics/import_paths/api_keys (#284) | `crates/remind_me_core/src/metrics.rs`, `crates/remind_me_core/tests/api_keys_test.rs`, `crates/remind_me_core/tests/import_paths_test.rs` … |
| d07d6a933e | Fix MCP surface consistency: id/memory_id alias, shared limit constants, backlog counts, error-prefix nit (#283) | `crates/remind_me_core/src/code_refs.rs`, `crates/remind_me_core/src/models.rs`, `crates/remind_me_core/src/promotion.rs` … |
| 0a1c12ecd9 | Document dead_code allow decision in shared test-helper modules (#287) | `crates/remind_me_api/tests/common/mod.rs`, `crates/remind_me_core/tests/support/mod.rs` |
| c30b44696c | export.rs now reuses import_paths' containment logic instead of duplicating it (#275) | `crates/remind_me_core/src/export.rs` |
| 673d27372b | Hub's busy_timeout raised to 30000ms, matching core (#281) | `crates/remind_me_hub/src/store/sqlite.rs` |
| 8609fabf6c | Background loops now report whether their thread is actually alive (#270) | `crates/remind_me_core/src/promotion.rs`, `crates/remind_me_core/src/scheduler.rs`, `crates/remind_me_core/src/status.rs` … |
| 58daddc03c | Path-list env vars no longer break on Windows drive letters (#298) | `crates/remind_me_core/src/code_refs.rs`, `crates/remind_me_core/src/export.rs`, `crates/remind_me_core/src/import_paths.rs` … |
| 66e85ae193 | Fix a data race between two query_expansion unit tests (#292) | `crates/remind_me_core/src/query_expansion.rs` |
| cac7e95185 | Peer server no longer blocks every DB call while serving a slow peer (#268) | `crates/remind_me_core/src/sync/server.rs`, `crates/remind_me_core/tests/peer_server_lock_test.rs` |
| 78427dd182 | stale_candidates re-applies write-time containment and sensitivity (#267) | `crates/remind_me_core/src/code_refs.rs`, `crates/remind_me_core/tests/code_refs_test.rs` |
| fd83cff338 | Sync no longer drops sensitive, remind_at, or (peer path) deleted_at (#265) | `crates/remind_me_core/src/sync/server.rs`, `crates/remind_me_core/tests/hub_compat_test.rs`, `crates/remind_me_core/tests/sync_test.rs` … |
| d016812082 | Record who wrote each memory, on every write path (#258) | `crates/remind_me_core/src/capture.rs`, `crates/remind_me_core/src/normalize.rs`, `crates/remind_me_core/src/promotion.rs` … |
| 3abd00eda9 | Bound search with a wall-clock deadline (#257) | `crates/remind_me_core/src/db/queries.rs`, `crates/remind_me_core/src/expansion.rs`, `crates/remind_me_core/src/retrieval.rs` … |
| c498c294b4 | Inject the persona as a search context bootstrap (#255) | `crates/remind_me_cli/src/main.rs`, `crates/remind_me_core/src/db/queries.rs`, `crates/remind_me_core/src/expansion.rs` … |
| 4f2ae99b97 | Symbolic compression: a capture skeleton with drill-down (#207) | `crates/remind_me_core/src/db/queries.rs`, `crates/remind_me_core/src/lib.rs`, `crates/remind_me_core/src/models.rs` … |
| 4a5da25ed2 | Fix two hub-sync observability bugs: false NodeAhead + phantom cursor backlog | `crates/remind_me_core/src/sync/reconcile.rs`, `crates/remind_me_core/src/sync/status.rs`, `crates/remind_me_core/tests/sync_status_test.rs` … |
| 8f7ad51c94 | Shared mock hub/node test infrastructure, plus a real remind_me_hub double | `crates/remind_me_core/Cargo.toml`, `crates/remind_me_core/tests/graph_sync_test.rs`, `crates/remind_me_core/tests/hub_compat_test.rs` … |
| 0197070b3a | Route the remaining ~/.remind_me (underscore) sites through one resolver (#228) | `crates/remind_me_core/src/api_keys.rs`, `crates/remind_me_core/src/db/mod.rs`, `crates/remind_me_core/src/ics.rs` … |
| 7089772f8a | wiki_fs_test.rs: fix a test made incompatible by the rebased HOME fix | `crates/remind_me_core/tests/wiki_fs_test.rs` |
| b1d37d9fb5 | status_test.rs: drop Database, not just its connection guard, before cleanup | `crates/remind_me_core/tests/status_test.rs` |
| f966e1809c | Fix Windows breakage in HOME-relative import/export path handling | `crates/remind_me_api/tests/import_export_test.rs`, `crates/remind_me_core/src/export.rs`, `crates/remind_me_core/src/import_paths.rs` … |
| b4ab1728cc | Fix remind_me_sync_status reporting full outbox as pending forever on namespaced entity/link remotes | `crates/remind_me_core/src/sync/mod.rs`, `crates/remind_me_core/src/sync/status.rs`, `crates/remind_me_core/tests/sync_status_test.rs` |
| fec2c709bb | Fix ics/api_keys/wiki default paths to use ~/.remind-me (hyphen) | `crates/remind_me_core/src/api_keys.rs`, `crates/remind_me_core/src/ics.rs`, `crates/remind_me_core/src/wiki_fs.rs` … |
| e919618614 | Fix connector token/oauth default paths to use ~/.remind-me (hyphen) | `crates/remind_me_core/src/remote.rs` |
| 388450c76a | Fix dashboard/sync/webhook/remote reporting ? in server_status markdown | `crates/remind_me_mcp/src/lib.rs`, `crates/remind_me_mcp/src/render.rs` |
| b2eaea9d95 | Actually write sync_log's last_attempt_at/last_push_at/last_pull_at | `crates/remind_me_core/src/sync/mod.rs`, `crates/remind_me_core/src/sync/pull.rs`, `crates/remind_me_core/src/sync/push.rs` … |
| 470ba20534 | Fix the dashboard's /api/stats to match the reference's own shape | `crates/remind_me_api/src/routes.rs`, `crates/remind_me_api/tests/protocol_test.rs`, `crates/remind_me_core/src/stats.rs` |
| 9aadfedd05 | Fix two gaps found by live interop verification, not by reading | `crates/remind_me_cli/src/main.rs`, `crates/remind_me_core/src/reminders.rs`, `crates/remind_me_mcp/src/lib.rs` |
| 0cbd65d709 | Honour `response_format` in the four tools that ignored it (#224) | `crates/remind_me_mcp/src/lib.rs`, `crates/remind_me_mcp/src/render.rs`, `crates/remind_me_mcp/tests/response_format_test.rs` |
| b1157910d1 | Record that memory ids are opaque, and pin the interop (#217) | `crates/remind_me_core/tests/id_format_test.rs` |
| 14f4f62752 | Give `add` and `search` their flags back (#216) | `crates/remind_me_cli/src/main.rs` |
| cadff9c7c3 | Resolve the database path the way the reference does (#218) | `crates/remind_me_cli/src/main.rs`, `crates/remind_me_core/src/db/mod.rs`, `crates/remind_me_core/tests/cloud_backup_test.rs` … |
| b26dc12d67 | Assert the event set, not the arrival order (#210) | `crates/remind_me_core/tests/events_test.rs` |
| 8e7c89502f | Drop three redundant borrows clippy 1.97 rejects | `crates/remind_me_mcp/tests/response_format_test.rs` |
| a17eb31aef | The folder watcher actually runs | `crates/remind_me_cli/src/main.rs`, `crates/remind_me_core/src/scheduler.rs`, `crates/remind_me_core/src/status.rs` … |
| c21d0d8ed6 | Fix clippy::field_reassign_with_default in the new tests | `crates/remind_me_core/tests/response_envelope_test.rs`, `crates/remind_me_core/tests/search_budget_test.rs` |
| 11909e1ae7 | Search reports its token budget instead of trimming in silence | `crates/remind_me_core/src/db/queries.rs`, `crates/remind_me_core/src/expansion.rs`, `crates/remind_me_core/src/retrieval.rs` … |
| afaf6d6abc | Memory JSON carries the six fields it was dropping | `crates/remind_me_core/src/db/queries.rs`, `crates/remind_me_core/src/models.rs`, `crates/remind_me_core/src/retrieval.rs` … |
| e9653f905b | Contradiction candidates report their shared entities | `crates/remind_me_core/src/contradictions.rs`, `crates/remind_me_core/src/models.rs`, `crates/remind_me_core/tests/contradictions_test.rs` |
| fbd243848e | Contradiction candidates: page past the first result | `crates/remind_me_core/src/contradictions.rs`, `crates/remind_me_core/src/models.rs`, `crates/remind_me_core/tests/contradictions_test.rs` … |
| 0cb468414e | Schema 29: the `reference` memory_type and the client sequence cursor | `crates/remind_me_core/src/db/migrations.rs`, `crates/remind_me_core/src/db/schema_tables.sql`, `crates/remind_me_core/src/sync/pull.rs` … |
| 3596d23423 | OAuth: write state atomically, and report failures | `crates/remind_me_core/src/remote.rs`, `crates/remind_me_mcp/src/lib.rs`, `crates/remind_me_remote/src/oauth/provider.rs` … |
| dd3c1300e9 | Fold the sync environment into `configure` | `crates/remind_me_cli/src/main.rs`, `crates/remind_me_hub/README.md`, `crates/remind_me_hub/client-setup.sh` |
| e4ef88370f | Port the hub's deployment packaging | `crates/remind_me_hub/Containerfile`, `crates/remind_me_hub/README.md`, `crates/remind_me_hub/client-setup.sh` … |
| 50265f4c41 | Port the sync hub, with storage behind a trait | `crates/remind_me_hub/Cargo.toml`, `crates/remind_me_hub/src/canon.rs`, `crates/remind_me_hub/src/http.rs` … |
| b1cd043788 | Link liblzma explicitly so stack-dumps builds under rust-lld | `crates/remind_me_core/Cargo.toml`, `crates/remind_me_core/build.rs` |
| 4f6424d97d | Default `remind_me_history`'s limit to 10, matching the reference | `crates/remind_me_core/src/models.rs`, `crates/remind_me_mcp/src/lib.rs` |
| 211bafcc34 | Add `response_format` to `remind_me_history` and `remind_me_stats` | `crates/remind_me_core/src/history.rs`, `crates/remind_me_core/src/models.rs`, `crates/remind_me_core/src/stats.rs` … |
| d0ced74122 | Make `remind_me_entity` read-only, move the write to its own tool | `crates/remind_me_core/src/models.rs`, `crates/remind_me_core/src/tool_profiles.rs`, `crates/remind_me_mcp/src/lib.rs` |
| bbc6a6883e | Pin the hub record's node_id in the pull test | `crates/remind_me_core/tests/sync_test.rs` |
| 459c6aa6bd | Fix a ~50% flake in the recalibration boundary test | `crates/remind_me_core/tests/recalibrate_test.rs` |
| 9f8191f7c7 | Supervise sidecar processes that live and die with the server | `crates/remind_me_core/src/lib.rs`, `crates/remind_me_core/src/sidecars.rs`, `crates/remind_me_core/src/sync/worker.rs` … |
| 473db58700 | Add image (OCR) and audio (transcription) import | `crates/remind_me_core/Cargo.toml`, `crates/remind_me_core/src/audio_import.rs`, `crates/remind_me_core/src/image_import.rs` … |
| e8c406785b | Add an ANN vector index behind an optional feature | `crates/remind_me_core/Cargo.toml`, `crates/remind_me_core/src/ann_index.rs`, `crates/remind_me_core/src/lib.rs` … |
| bbfb54544b | Add PDF import behind an optional feature | `crates/remind_me_core/Cargo.toml`, `crates/remind_me_core/src/import_paths.rs`, `crates/remind_me_core/src/importer.rs` … |
| 6f8e9699f7 | Add Readwise highlight import | `crates/remind_me_core/src/importer.rs`, `crates/remind_me_core/src/lib.rs`, `crates/remind_me_core/src/models.rs` … |
| ef8a3e5fa1 | Add Obsidian vault import | `crates/remind_me_core/src/importer.rs`, `crates/remind_me_core/src/lib.rs`, `crates/remind_me_core/src/models.rs` … |
| f252c43546 | Fix silent JSONL import no-op and stale sync-error reporting | `crates/remind_me_core/src/importer.rs`, `crates/remind_me_core/src/sync/mod.rs`, `crates/remind_me_core/src/sync/worker.rs` … |
| c31974eb9a | Add tool profiles (full / standard / core) | `crates/remind_me_core/src/lib.rs`, `crates/remind_me_core/src/tool_profiles.rs`, `crates/remind_me_core/tests/tool_profiles_test.rs` … |
| 518751994d | Stop the shared rate limiter from bleeding across the webhook tests | `crates/remind_me_core/tests/webhook_rate_limit_test.rs`, `crates/remind_me_core/tests/webhook_test.rs` |
| 2f0c7a4da9 | Add rate limiting on the webhook ingest and remote MCP endpoints | `crates/remind_me_core/src/lib.rs`, `crates/remind_me_core/src/rate_limit.rs`, `crates/remind_me_core/src/webhook.rs` … |
| e564139cd9 | Add the reminder delivery scheduler and a webhook notification channel | `crates/remind_me_cli/src/main.rs`, `crates/remind_me_core/src/lib.rs`, `crates/remind_me_core/src/notifications.rs` … |
| 0cbc122be7 | Add remind_me_sync_reconcile and remind_me_sync_reconcile_peer | `crates/remind_me_core/src/models.rs`, `crates/remind_me_core/src/sync/mod.rs`, `crates/remind_me_core/src/sync/reconcile.rs` … |
| 6e16a1948a | Add remind_me_sync_status and remind_me_sync_repair | `crates/remind_me_core/src/models.rs`, `crates/remind_me_core/src/sync/mod.rs`, `crates/remind_me_core/src/sync/status.rs` … |
| 4421b20471 | Add GET /count to the sync peer server | `crates/remind_me_core/src/sync/server.rs`, `crates/remind_me_core/tests/peer_server_test.rs` |
| 3469d80291 | Add daily analytics snapshots and GET /api/analytics/trend | `crates/remind_me_api/src/routes.rs`, `crates/remind_me_api/tests/versions_test.rs`, `crates/remind_me_core/src/analytics.rs` … |
| 1d34d7a0d9 | Add remind_me_contradiction_candidates | `crates/remind_me_core/src/contradictions.rs`, `crates/remind_me_core/src/lib.rs`, `crates/remind_me_core/src/models.rs` … |
| 0a1a14f465 | Add remind_me_history and remind_me_revert | `crates/remind_me_core/src/db/queries.rs`, `crates/remind_me_core/src/history.rs`, `crates/remind_me_core/src/lib.rs` … |
| 2de8a27aff | Accept SQLite's integer booleans for SyncRecord.sensitive | `crates/remind_me_core/src/sync/record.rs`, `crates/remind_me_core/tests/sensitive_sync_test.rs`, `crates/remind_me_core/tests/sync_test.rs` |
| 4ae16ab902 | Stop the sync outbox recording every memory read | `crates/remind_me_core/src/db/migrations.rs`, `crates/remind_me_core/src/db/schema_triggers.sql`, `crates/remind_me_core/tests/outbox_test.rs` … |
| 2a060bc7a8 | Add RRF retrieval-fusion weight/mode configurability via environment vars | `crates/remind_me_core/src/db/queries.rs`, `crates/remind_me_core/src/models.rs`, `crates/remind_me_core/src/retrieval.rs` … |
| 2d78a0e00c | Fix remind_me_server_status to reflect live dashboard/embeddings state | `crates/remind_me_cli/src/main.rs`, `crates/remind_me_core/src/embedder.rs`, `crates/remind_me_core/src/lib.rs` … |
| 4a420cac82 | Add single-user OAuth 2.1 authorization server for the remote connector (#86) | `crates/remind_me_core/src/remote.rs`, `crates/remind_me_mcp/src/lib.rs`, `crates/remind_me_remote/Cargo.toml` … |
| cf401e3769 | Fix flaky graph_sync_test via missing ENV_LOCK guard (#81) | `crates/remind_me_core/tests/graph_sync_test.rs` |
| f587a551f4 | Add knowledge-graph sync: entities, entity_relations, memory_entities (#57) | `crates/remind_me_core/src/db/schema.rs`, `crates/remind_me_core/src/entity.rs`, `crates/remind_me_core/src/sync/graph.rs` … |
| 1848574fe4 | Grow the HTTP API from 2 routes to 19 (#71) | `crates/remind_me_api/Cargo.toml`, `crates/remind_me_api/src/http.rs`, `crates/remind_me_api/src/lib.rs` … |
| 4ca6224582 | Add remind_me_server_status (#65) | `crates/remind_me_core/src/lib.rs`, `crates/remind_me_core/src/status.rs`, `crates/remind_me_core/tests/status_test.rs` … |
| 77527f02a6 | Add remind_me_export_memories (#64) | `crates/remind_me_core/src/db/queries.rs`, `crates/remind_me_core/src/export.rs`, `crates/remind_me_core/src/lib.rs` … |
| 06fffbd80a | Add remind_me_decompose and decompose_batch (#63) | `crates/remind_me_core/src/capture.rs`, `crates/remind_me_core/src/db/queries.rs`, `crates/remind_me_core/src/entity.rs` … |
| cbde968ed6 | Add remind_me_feedback (#14) | `crates/remind_me_core/src/models.rs`, `crates/remind_me_core/src/vitality.rs`, `crates/remind_me_core/tests/feedback_test.rs` … |
| b4da8f667a | Add remind_me_annotate, fix three entity-layer bugs (#7) | `crates/remind_me_core/src/db/queries.rs`, `crates/remind_me_core/src/entity.rs`, `crates/remind_me_core/src/models.rs` … |
| aa48127e1d | Add remind_me_vitality_report with read-time decay (#6) | `crates/remind_me_core/src/db/queries.rs`, `crates/remind_me_core/src/vitality.rs`, `crates/remind_me_core/tests/vitality_report_test.rs` … |
