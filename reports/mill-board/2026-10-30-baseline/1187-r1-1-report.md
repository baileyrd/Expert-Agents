<!-- mill-board/1187-r1 run 1, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 248 s, 1434131 tokens, $1.22 -->

# Review of Rusty-Mill/rusty_mill PR #1187: OAuth sign-in for Mill Board's `/mcp`

The PR adds OAuth sign-in, but two authority defects come in with it. A grant can act with more authority than the token that approved it, and a grant meant for `/mcp` also reaches the admin-only settings routes. I couldn't compile anything in this environment, so every trace below comes from reading the code, not from a test run.

- **Base:** `55e5f3c65762cfd71c8f10883bcee20ea972ce7b`
- **Reviewed head:** `2017c08f1f83ab7a181b69574b354376f9ad5e2a`. `git fetch origin pull/1187/head` returned this same SHA. It is one commit, `2017c08f1 rusty_mill_board: OAuth sign-in for /mcp`.

## What the diff touches, and what must hold
- **Login:** `Web::identify` (`web.rs:184`) now also accepts OAuth access tokens, on both `/api/*` and `/mcp`. A grant must act with exactly the authority of the token that approved it, decided from one unique key, and failing closed.
- **Consent check** (`with_oauth`, `web.rs:117-149`): only the board's own tokens may approve. The PR's test covers this, and the code holds.
- **OAuth routes** (`/register`, `/authorize`, `/consent`, `/token`, revoke, metadata): served behind `host_ok`. That holds (`web.rs:318-324`).
- **`/mcp` 401:** now carries the RFC 9728 `resource_metadata` challenge. That holds.
- **Settings** (`/api/settings_*`): Admin-only over HTTP. ADR-0004 also says "They are not MCP tools … a model should not mint credentials."
- **Startup** (`main.rs:162-180`, `319-325`): the issuer's host is added to the allowed `Host` names, the grant file has a default location, and an open board is refused.
- **Refactor:** rusty_tick's private OAuth adapter moves into `rusty_oauth_server::serve`. Behaviour is unchanged apart from the log prefix.

## Blocking findings

### 1. P0: a grant resolves to whichever credential has the same actor name, not the one that approved it
- **Where:** `src/identities.rs:276-278` (`by_actor`), used at `src/web.rs:194-206`.
- **Trigger:** two entries in `identities.json` with the same `actor`.
- **Why it's possible:** the subject stored with a grant is just the actor name. Each request looks it up with `by_actor`, which returns the *first* entry with that name. Actor names are not unique. `Credentials::parse` (`identities.rs:94-136`) only rejects reused *tokens*. `add` refuses a duplicate actor, but ADR-0004 makes the hand-edited file the source of truth, so the file can contain duplicates.
- **Scenario (source-traced):**
  1. `identities.json` holds two entries for the same person:
     - `{"token":T1,"actor":"nano","role":"human"}`
     - `{"token":T2,"actor":"nano","role":"qa","projects":["x"]}`

     The narrow token T2 is the one meant for claude.ai.
  2. The person signs the claude.ai connector in with T2. The consent check `identify(T2)` gives subject `"nano"`.
  3. Every `/mcp` or `/api` call with the resulting access token runs `by_actor("nano")`, which returns the T1 entry: role `human`, Admin, all projects.
- **Who is hurt:** an agent over MCP gains Admin and access to every project, which is a scope leak across projects. It also gains settings access through finding 2. A role or project change made later to T2's entry never applies to the grant.
- **Correction:** pick one of these.
  - Bind the grant to the approving credential, not the name. For example, store a stable per-credential id, or a hash of the approving token, as the subject.
  - Or make the actor a unique key: reject duplicate actors in `Credentials::parse` and refuse to start. Silently picking one must not happen.
- **Regression test:** in `tests/web.rs`, credentials with two `nano` entries (human first, then a qa entry limited to project `x`). Sign in with the qa token and assert that `board_whoami` returns `qa`/`["x"]`, or that `parse` errors. It goes red at head.

### 2. P0: an OAuth access token issued for `/mcp` is accepted on `/api/settings_*`, so a connector's grant can create and rotate tokens
- **Where:** `src/web.rs:332-337` and `web.rs:184-188`, which call `identify` and so accept OAuth tokens. The intent is stated at `web.rs:118-120`.
- **Trigger:** a grant approved with any token whose role holds Admin. This is the expected case: the first start prints only the `admin` token (`settings.rs:129-153`, `connecting-models.md:29`), so it is the token a person naturally pastes into "Sign in now".
- **What it contradicts:**
  - ADR-0004: settings "are not MCP tools … a model should not mint credentials". It rejects settings over MCP because "an agent could grant itself access".
  - The resource the grant was issued for. The protected-resource metadata names `"resource": <issuer>/mcp` (`rusty_oauth_server/src/endpoints.rs:238-243`). The MCP authorization spec requires a server to accept only tokens issued for it as the audience (RFC 8707).
  - The PR's own docs: the fragment, README and deploy README all say "OAuth sign-in for `/mcp`".
- **Scenario (source-traced):**
  1. An admin signs a claude.ai connector, or any self-registered MCP client, in with the admin token.
  2. The client holding the access token sends `POST /api/settings_agent_add` with that bearer token. `identify` → `oauth_identity` → `by_actor("admin")` gives the admin identity. `Settings::admin` checks for Admin and passes (`settings.rs:298-311`). A new bearer token is returned.
  3. `settings_agent_rotate {"name":"admin"}` also works: it hands the client the admin's new token and invalidates the admin's old one.
  4. Tokens created this way survive the connector's disconnect and the revocation of its grant.
- **Who is hurt:** the board owner. Authority is gained beyond the consented resource, and credentials are exposed to a third-party client.
- **Correction:** accept OAuth access tokens only on the MCP path. In `/api` use `own_identity` alone, or at minimum refuse `settings_*` to OAuth-derived identities. Then make `with_oauth`'s doc comment say `/mcp` only.
- **Regression test:** in `tests/web.rs`, sign in with an Admin credential's token, then `POST /api/settings_agent_add` (and `board_whoami` on `/api`) with the access token. Assert 401 or 403. It goes red at head.

### 3. P2: a misplaced doc comment
- **Where:** `src/identities.rs:272-280`.
- **What happened:** `by_actor` was inserted inside `identify`'s doc comment. `by_actor` is now documented as "Whose token this is. Compares against every entry, so timing does not reveal which entry matched…", which is false for a short-circuiting `find` on names. `identify` lost its constant-time note, which a later edit could rely on.
- **Correction:** move the two lines back above `identify`. This is a doc fix; no test is needed.

### 4. P2: two behaviours the PR claims have no tests
- **Issuer host accepted as a `Host`:** the fragment and `deploy/README.md` claim it, implemented at `main.rs:321-324`. The tests use `Host: 127.0.0.1` with issuer `http://localhost:8787`, so the push of the issuer's host is never exercised. Without the push, a real deployment's `/authorize` and `/mcp` would get 403 and no test would notice.
- **Default grant-file location:** `--oauth-state` falling back to `--settings-dir` or beside `--identities` (`main.rs:163-180`) is untested.
- **Where the test belongs:** a process test in `main.rs`'s tests or `tests/`. Start with `--issuer https://board.test` and default allowed hosts, send `GET /.well-known/oauth-authorization-server` with `Host: board.test`, and expect 200; also assert that `oauth.json` is created in the settings directory.

## Design questions (documented in the PR, not counted as defects)
- **Rotating a token does not end grants made with it.** Rotation is the documented response to a leaked token. A leaked agent token can be turned into a grant through open client registration, and that grant survives rotation and refreshes indefinitely. The only bulk remedy is to stop the board and delete `oauth.json`, which ends everyone's grants. A simpler fix: store a credential generation or token hash with the grant and suspend it when the credential changes.
- **Re-adding a removed agent's name restores their grants.** The new agent may be a different model with a different role. An identifier is reused after its owner is gone; finding 1's fix (binding grants to a credential id) also removes this.
- **A removed agent's connector cannot tell why it is refused.** It gets the generic `invalid_token` / "Authentication required" challenge, and refresh still succeeds (the provider does not consult `verify`). The client therefore loops between refresh and 401 instead of being told the grant is suspended.

## Optional improvements
- **`SHARED_SUBJECT = "shared"` can be a real actor name.** If a board moves from `--auth-token` to `--identities` and keeps its grant file, shared-token grants become actor `shared` if one exists. A prefix that can't be an actor name (for example `:shared`) avoids that.
- **Test temp directories are never cleaned up.** The OAuth tests create directories under `std::env::temp_dir()` and never remove them; `tempfile::tempdir()` would.

## Checks and validation gaps
- **Ran `python3 .github/scripts/changes.py check`** (in the scratch copy, with `.github` copied in): "488 fragment(s) ok".
- **Could not run `cargo test -p rusty_mill_board`, the `rusty_oauth_server` or rusty_tick tests, or clippy.**
  - The pinned toolchain 1.98.1 is not installed and rustup's directory is read-only. The installed stable is also 1.98.1.
  - With `--offline` the build fails: crates such as `dyn-clone` are missing from the local cargo cache.
  - The request to `index.crates.io` was denied by the sandbox's network filter.
  - None of these checks is passing as far as this review knows. Neither probe test (findings 1 and 2) was executed, and the new tests were not run at the base. They would not compile there, since `with_oauth` doesn't exist at the base.
  - Enabling crates.io access, or providing a pre-filled cargo cache, would allow the probes to be run.
- **Not testable here:** a live claude.ai connector, the deployed TLS and Host setup, and the consent page in a browser.
- **Out of this PR's scope:** two boards sharing one `oauth.json`, for example two boards given the same `--identities` file and no `--settings-dir`. `StateStore` does not lock across processes (`store.rs:118-119`).

## Verdict: **changes required**
