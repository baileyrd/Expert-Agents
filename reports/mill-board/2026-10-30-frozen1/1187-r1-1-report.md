# Review: PR #1187, rusty_mill_board OAuth sign-in for /mcp

- Repository: rusty_mill (worktree /home/claude/trial-mb-1187)
- PR: #1187
- Base: 55e5f3c65762cfd71c8f10883bcee20ea972ce7b
- Reviewed head: 2017c08f1f83ab7a181b69574b354376f9ad5e2a (worktree HEAD, pinned by the caller; the fetch step was not re-run)
- Diff: `git diff 55e5f3c6...2017c08f`, one commit, 17 files

## 1. Predicted surface and invariants

| Touched item | Invariant |
|---|---|
| `Web::identify` / `oauth_identity` (web.rs:184-207), used by `/mcp` and `/api/*` | An OAuth token resolves to exactly the identity whose token approved it, read fresh at each request. It fails closed when that identity is gone (identities.rs:7 "A model that holds a token cannot act as any other actor or role"). |
| `Credentials::by_actor` (identities.rs:276) | It resolves authority from a unique key. |
| `Web::with_oauth` verifier (web.rs:124-150) | Consent accepts only the board's own tokens. An open board has no OAuth. |
| OAuth routes `/authorize`, `/consent`, `/token`, `/register`, `/revoke`, `/.well-known/*` (web.rs:321-324) | They sit behind the same Host guard as the rest. The issuer's Host is accepted the same way by every guard (`Web::host_ok`, `rusty_mcp_server` `host_allowed`). |
| `/mcp` 401 (web.rs:229-235) | It carries the RFC 9728 `resource_metadata` challenge when OAuth is on and is unchanged when it is off. |
| CLI `--issuer`, `--oauth-state`, issuer host added to allowed hosts (main.rs:134-142, 162-180, 320-359) | The issuer is validated. The grant file is owner-only and lives in settings. Startup refuses an open board. |
| `rusty_oauth_server::serve` (serve.rs) and the rusty_tick switch to it | Behaviour is identical to the adapter tick carried before. |
| Docs: README, deploy/README, board.env.example, change fragment | Each one matches the code. |

## 2. Blocking findings

### F1 - P0 - A grant made with one token acts as a different credential of the same actor (authority gained)

- **Location:** crates/apps/rusty_mill_board/src/identities.rs:276-278 (`by_actor`) with web.rs:141-144 and web.rs:200-205. The cause is that `Credentials::parse` (identities.rs:94-139) does not reject a repeated `actor`. It rejects only a repeated token.
- **Trigger:** a hand-written `identities.json`, the documented format in docs/connecting-models.md:12-17, with two entries for one actor. The narrow trigger does not lower the grade.
- **Trace (executed):** this probe is `probe_duplicate_actor_grant_escalates_to_first_entry` in the scratch copy.
  1. The file is `[{"token":HIGH,"actor":"nano","role":"human"},{"token":LOW,"actor":"nano","role":"builder"}]`.
  2. `parse` accepts it.
  3. `POST /api/board_whoami` with LOW returns `role: builder`.
  4. The connector signs in and consents with LOW. The verifier stores subject `"nano"`.
  5. On each request `by_actor("nano")` returns the first entry.
  6. `board_whoami` with the OAuth access token returns `{"actor":"nano","role":"human","capabilities":[...,"admin"]}`.

  The holder of a scoped builder token gets human/admin rights over MCP, `/api` and `settings_*`. The same applies to project scope: a `projects`-limited token can become an unscoped one.
- **Who is hurt:** every board whose identities file repeats an actor. An agent gains authority the operator did not grant.
- **Required correction:** make the grant's key unique and fail closed. Either:
  - reject a repeated actor in `Credentials::parse`, and keep `add` refusing it, or
  - have `by_actor` return `None` when more than one entry matches.

  The change fragment's "a grant acts as the agent whose token approved it" only holds once the key is unique.
- **Regression test:** use the probe above as written in tests/web.rs. Expect the grant to be refused, or `parse` to return an "actor ... twice" error. Add a `rejects_bad_files` case in identities.rs for a repeated actor.

### F2 - P0 - Grant subjects are not namespaced by auth mode: the `"shared"` subject is also a valid actor name (authority crosses modes)

- **Location:** crates/apps/rusty_mill_board/src/web.rs:33 (`SHARED_SUBJECT = "shared"`), web.rs:199 and web.rs:200-205. Grants persist in the same `oauth.json` across restarts (main.rs:169-176).
- **Trigger:** the operator changes between `--auth-token` and `--identities` while keeping the settings dir. An agent named `shared` must exist (`valid_actor` allows it, identities.rs:70-75). The narrow trigger does not lower the grade.
- **Trace (executed):** this probe is `probe_shared_grant_becomes_agent_named_shared_after_mode_switch`.
  1. Board A runs with `--auth-token` and OAuth. Anyone who held the shared token signs in, and the grant gets subject `"shared"`.
  2. The same `oauth.json` is then served by board B with `--identities`, which contains an agent named `shared`.
  3. The old access token returns 200 with `{"actor":"shared","authenticated":true,...}`. It acts as that agent with that agent's role.
  4. The reverse also holds. An identities-mode grant by agent `shared` becomes an unrestricted shared-token caller (any role, including human) after a switch to `--auth-token`.
- **Who is hurt:** the operator. Every former shared-token holder, and every connector they signed in, inherits the `shared` agent's role. In the reverse case a scoped agent's connector gains every role.
- **Required correction:** give subjects a form that cannot collide, for example `actor:<name>` and `shared:`. Anything not in the current mode's form is refused. Alternatively, store the auth mode in the grant and refuse a mismatch.
- **Regression test:** the probe above. Expect 401 at board B, and the same in the reverse direction.

### F3 - P1 - An IPv6-literal issuer is not accepted by the web guard, so sign-in cannot start

- **Location:** crates/libs/net/rusty_oauth_server/src/issuer.rs:27-30 (`Issuer::host` strips brackets), main.rs:321-324 (pushed into `allowed_hosts`), web.rs:172-181 (`host_ok` keeps brackets and compares case-sensitively). `rusty_mcp_server`'s `host_allowed` (http.rs:217-234) strips brackets and ignores case, so the two guards disagree.
- **Trigger:** `--issuer https://[fd7a:115c::1]` or any IPv6-literal https origin, which `validate_issuer` accepts.
- **Trace (executed):** this probe is `probe_ipv6_issuer_host_is_refused_by_the_web_guard`.
  - `issuer.host()` returns `"fd7a:115c::1"`.
  - `GET /.well-known/oauth-authorization-server` with `Host: [fd7a:115c::1]` returns `403 {"error":"host not allowed"}`.
  - `/mcp` with the same Host passes the guard and returns 401.

  The connector sees a challenge, but its metadata fetch, `/authorize` and `/token` are all refused. This contradicts the README and deploy/README:49 "Its host is accepted as a Host name".
- **Who is hurt:** a person setting up the connector.
- **Required correction:** compare Host the same way in both guards. Use one normalisation, bracket-stripped and lowercased, for `Web::host_ok` and for the value pushed from `Issuer::host`. Alternatively, have `host()` return the bracketed form and make `host_ok` case-insensitive.
- **Regression test:** the probe above, expecting 200, in tests/web.rs.

### F4 - P2 - The constant-time doc comment moved onto the wrong function

- **Location:** crates/apps/rusty_mill_board/src/identities.rs:272-280.
- **Claim:** the existing doc "Whose token this is. Compares against every entry, so timing does not reveal which entry matched..." now heads `by_actor`. That function takes no token and is not constant-time. `identify`, the function that really is constant-time, lost that statement. Rustdoc shows both paragraphs on `by_actor` (source-traced).
- **Fix:** move the two lines back above `identify` and drop the new "The identity a token belongs to." line.
- **Test:** none needed (docs only).

### F5 - P2 - The CLI wiring of OAuth has no test

- **Location:** crates/apps/rusty_mill_board/src/main.rs:162-180 and main.rs:320-324, 355-359.
- **Claim:** no test fails if any of these is removed:
  - `allowed_hosts.push(issuer.host())`;
  - the default grant path (settings dir, then beside identities);
  - the error when no path is available;
  - the `--oauth-state requires --issuer` rule.

  The OAuth tests in tests/web.rs build `Web` directly. They use issuer `http://localhost:8787` but send `Host: 127.0.0.1`, so the issuer host is never accepted through the new code (source-traced).
- **Fix and test:** add a process test in tests/process.rs that starts the binary with `--issuer http://localhost:<port> --settings-dir D`. It should check that:
  - `GET /.well-known/oauth-authorization-server` with `Host: localhost` answers 200 (the allowed hosts here are only 127.0.0.1);
  - `D/oauth.json` is created mode 0600 after `/register`;
  - an open board with `--issuer` exits with the "--identities" message.

## 3. Design questions (not blocking)

These are documented in the PR's own docs, but they hurt the person cleaning up after a leaked token.

1. **Rotating a token does not end grants made with it.** A leaked token's grant survives rotation. The documented way to cut it is "remove the agent", but re-adding the same name restores the grant. So the only real fix is to stop the board and delete `oauth.json`, which ends every connector's grant.
   - The same holds, undocumented for the board, when `--auth-token` is changed: tick documents it at rusty_tick/src/oauth.rs:18-19, the board's deploy/README does not.
   - Simpler alternative: record the approving token's hash, or a per-credential generation, in the grant, and refuse grants from a superseded generation. Rotation then ends that credential's grants.
2. **Re-adding a removed agent of the same name restores the old grants.** The new holder may be a different model or person. A generation or id per credential, as above, avoids handing old connectors to a new holder.
3. **OAuth access tokens are accepted on `/api/*`, including `settings_*`.** RFC 9728 metadata advertises only `{issuer}/mcp` as the resource, and ADR-0004 keeps settings off the model path ("a model should not mint credentials"). No trace shows a model reaching the bearer, so this is not a finding. Accepting grants only on `/mcp` would match the advertised resource and the ADR's intent.

## 4. Optional improvements

- The tests in tests/web.rs (`start_oauth`) create `std::env::temp_dir()/mill-board-oauth-<uuid>` and never remove it. Use `tempfile::tempdir()`, as serve.rs's test does.
- `/register` is open dynamic client registration. Each call writes to `settings/oauth.json`, which is now internet-facing on the board. This is library behaviour and not new, but a cap on registered clients would bound file growth.
- `Issuer::host` returns `userinfo@host` for an issuer with userinfo (`https://u@board.example.com`), which `validate_issuer` accepts. This is harmless but unhelpful. Rejecting userinfo in `validate_issuer` would give a clear error.

## 5. Failure classes

- **Lost update:** `oauth.json` is written by one `StateStore` per process. It re-reads on every operation and holds a process-local mutex (store.rs:1-6). By default the board's file is in its settings dir and tick's is in its data dir, so the two do not share a file, and two boards do not share a settings dir. Ruled out for the default layout. A custom `--oauth-state` pointed at tick's file would be a two-writer file, but no doc suggests that.
- **Two-step write:** register, authorize, consent and token are each single library writes. A lost token reply is handled by the library (code single-use, refresh rotation, #1184). Nothing in this diff adds a two-request write. Ruled out.
- **Silent fallback or reuse:**
  - Removing the agent makes the grant fail closed (tested).
  - Grants with no subject are refused (`subject_of` returns None).
  - The subject is resolved from a key that is not unique: F1, and across modes F2.
- **Scope leak:** F1 (role and project scope), F2 (cross-mode). The project scope of a single-entry actor is read fresh on each request (test `an_oauth_grant_acts_as_the_agent_who_approved_it_until_they_are_removed`).
- **Token races:** each request resolves the identity under the credentials read lock (web.rs:200-205). A remove that lands mid-request applies from the next request, the same as bearer tokens. Consent's verifier takes the same read lock, and no path holds the provider lock and the credentials write lock together. No deadlock. Host forms differ between guards: F3.
- **UI out of step:** the diff does not touch ui.html. Not applicable.
- **Tests that miss the window:** the tests have no concurrency windows. Host acceptance through the CLI is untested: F5.

## 6. Board rules and agent contract

- The diff changes no moves, claims, capabilities or `card_take`. Settings ops are unchanged, but now reachable with an OAuth token (design question 3).
- MCP tools are unchanged. The `/mcp` 401 now has an RFC 9728 challenge and an `invalid_token` body when OAuth is on (tested), and is unchanged when it is off.
- An agent using a static bearer token sees the same behaviour as before, apart from that 401 body.

## 7. Checks performed

All checks were run in the scratch copy /tmp/claude-0/-home-claude/c6e91f6f-c04b-5b25-b0a9-c1761f25074d/scratchpad/probe-mb-1187 at 2017c08f, with a separate target dir. The reviewed worktree was not modified.

| Check | Result |
|---|---|
| `cargo test --locked -p rusty_mill_board -p rusty_oauth_server --all-features` | all passed, 0 failed. Includes the 3 new board OAuth tests, serve.rs's new test, and the 3 review probes. |
| `cargo test --locked -p rusty_tick` | all passed |
| `cargo clippy --locked -p rusty_mill_board -p rusty_oauth_server -p rusty_tick --all-features --all-targets -- -D warnings` (probes stashed) | exit 0. Only manifest warnings from rusty_h2 and rusty_tls, which this PR does not touch. |
| `cargo fmt --check` on the three crates | no diff |
| `python3 .github/scripts/changes.py check` | "488 fragment(s) ok" |
| New tests at the base | they do not compile there (`Web::with_oauth`, `rusty_oauth_server::serve` and `Issuer::host` do not exist), so they guard the change |
| Probes | F1, F2 and F3 were all reproduced (the probes assert the defective behaviour and passed) |

**Validation gaps:**
- Interop with a live claude.ai connector: needs the deployed https origin and a real "Sign in now".
- A process-level run of `--issuer` (F5 covers the missing test).
- CI's required checks on the PR head were not seen.

## 8. Verdict

**Changes required.** There are 2 P0 findings (F1, F2), 1 P1 (F3) and 2 P2 (F4, F5).
