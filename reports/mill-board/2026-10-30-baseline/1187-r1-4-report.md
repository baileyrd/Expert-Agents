<!-- mill-board/1187-r1 run 4, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 246 s, 1496644 tokens, $1.26 -->

**Review of Rusty-Mill/rusty_mill PR 1187: OAuth sign-in for Mill Board's `/mcp`**

- **Base:** `55e5f3c65762cfd71c8f10883bcee20ea972ce7b`
- **Head reviewed:** `2017c08f1f83ab7a181b69574b354376f9ad5e2a`. `git fetch origin pull/1187/head` returned this same SHA. The PR is one commit.

**Verdict: changes required.** One P0 and two P2 findings below. I could not build or run any Rust tests in this sandbox, so every trace is source-traced, not executed.

## Invariants for the touched items
- **Consent check (`Verify`, `web.rs:125-146`):** only one of the board's own tokens can approve a grant. An open board refuses OAuth.
- **Request identity (`Web::identify`/`oauth_identity`, `web.rs:184-207`, used for `/mcp` and `/api`):** an OAuth grant acts with exactly the authority of the credential that approved it, from a unique key, and fails closed. The module docs and change fragment promise "a grant acts as the agent whose token approved it".
- **`/mcp` 401 (`web.rs:229-235`):** with OAuth on, it carries a `resource_metadata` challenge (RFC 9728).
- **OAuth routes (`web.rs:321-324`):** they sit behind the Host check, inside the in-flight drain, and don't collide with board routes. Checked: `/authorize`, `/token`, `/register`, `/revoke`, `/consent` and `/.well-known/*` don't collide with `/`, `/api/*` or `/health`.
- **Startup (`main.rs:163-180, 320-324`):** the issuer's host is added to the accepted Host names, and the grant file has a location or the board refuses to start.
- **`rusty_tick`:** behaviour unchanged. Only the adapter moved into `rusty_oauth_server::serve`.

## Blocking findings

### 1. P0: a grant's authority comes from the actor name, which is not unique
- **Where:** `crates/apps/rusty_mill_board/src/identities.rs:276-278` (`by_actor`) and `src/web.rs:200-205`, against `identities.rs:100-114`.
- **Trigger:** an identities file with two entries for the same actor. `Credentials::parse` rejects a repeated *token* (line 112) but never a repeated *actor*. Only the settings UI's `add` checks the name (line 172). The documented hand-written `--identities` file (`docs/connecting-models.md:12-20`) is accepted with duplicates.
- **Failure scenario (source-traced):**
  1. `identities.json` holds `{token A, actor "nano", role "human"}` and then `{token B, actor "nano", role "qa", projects ["x"]}`. For example, an owner keeps a limited token for a model.
  2. On the consent page, whoever holds B approves a claude.ai connector. `Verify` calls `identify(B)`, gets `"nano"` and stores `Subject("nano")` (`web.rs:142-145`).
  3. On every `/mcp` or `/api` call, `oauth_identity` calls `by_actor("nano")` and gets the **first** entry: role `human`, no project limit.
  4. The connector now has every capability, including `Admin` (so all of `/api/settings_*`) and every project. Token B alone never had any of that.
- **Who is hurt:** every project and the settings. An agent over MCP gains authority it was never given.
- **This contradicts:** the change fragment ("a grant acts as the agent whose token approved it") and the `web.rs` module docs.
- **Required correction:** resolve the grant from a unique key. Either:
  - reject duplicate actors in `Credentials::parse` so startup fails closed, or
  - make the subject a per-credential identifier, not the name.
- **Regression tests:**
  - in `identities.rs`, `parse` refuses two entries with one actor;
  - in `tests/web.rs`, sign in with the second, limited token of a duplicated actor and assert that `board_whoami` returns *that* token's role and projects (or that the setup is refused).

### 2. P2: the constant-time doc comment now sits on the wrong function
- **Where:** `identities.rs:272-280`.
- **Problem:** the new function was inserted between `identify` and its doc comment. `by_actor` now opens with "Whose token this is. Compares against every entry, so timing does not reveal which entry matched…". That is false: `by_actor` looks up a name and stops at the first match. Meanwhile `identify`, the function that really does the constant-time comparison, lost that statement and now just says "The identity a token belongs to."
- **Correction:** move the two lines back above `identify`. This is a docs-only fix, so no test is needed.

### 3. P2: no test covers the new startup wiring
- **Where:** `main.rs:163-180` (`oauth()`: picks the grant-file path, or refuses when there's nowhere to keep grants) and `main.rs:320-324` (adds the issuer's host to the accepted hosts).
- **Problem:** the new tests in `tests/web.rs` build `Web` directly. They allow host `127.0.0.1` with issuer `localhost` and never send `Host: <issuer host>`. Removing `allowed_hosts.push(issuer.host())`, or the "needs `--oauth-state`/`--settings-dir`/`--identities`" refusal, would turn nothing red. A deployment would then answer 403 "host not allowed" at `/authorize`.
- **Correction:** add a test in `tests/process.rs` that:
  - starts the binary with `--issuer http://localhost:<port>`, without adding `localhost` to the allowed hosts;
  - asserts that `GET /.well-known/oauth-authorization-server` with `Host: localhost` returns 200;
  - asserts that `--issuer` with `--auth-token` and no `--oauth-state` refuses to start.

## Design questions (not blocking; the PR documents these)
- **Rotating a token doesn't end grants** (`deploy/README.md:54-57`, change fragment). Rotation is how an admin responds to a leaked token. But whoever holds the leaked token can approve a grant first, and that grant survives the rotation. The only remedy is to stop the board and delete `oauth.json`, which ends every grant. The UI offers no way to end one grant, and its Remove prompt only says "Its token stops working" (`ui.html:322`). `rusty_tick` made the same choice (`rusty_tick/src/oauth.rs:12-21`).
  - **Simpler alternative:** call the store's existing `delete_grant` for that subject under the credentials write lock in `agent_rotate` and `agent_remove`.
- **Re-adding a removed name restores its grants.** A *different* agent later given the same name hands its authority to the old grant holders, so an identifier is reused while its owner still exists. Ending grants on remove would close this too.
- **OAuth tokens also work on `/api`, including `settings_*`** (`web.rs:120`, `web.rs:335`). The protected resource is advertised as `{issuer}/mcp`, and the changelog says "for `/mcp`". On a fresh board the only token is `admin`'s, so a connector approved with it gets Admin over HTTP. ADR-0004 kept settings off MCP so "a model should not mint credentials".
  - **Simpler alternative:** accept OAuth tokens only on `/mcp`, and have `deploy/README.md` recommend approving with a dedicated, non-admin agent's token.

## Optional improvements
- `--issuer` without `--http` is silently ignored: stdio mode returns before `oauth()` runs. Consider refusing that combination.
- `rusty_tick/README.md:129-130` still says `oauth` "adapts" the endpoints. The adapter now lives in `rusty_oauth_server::serve`.
- The new tests leave `mill-board-oauth-*` directories behind in the system temp dir.
- An IPv6 issuer gets host `::1` without brackets (`issuer.rs:132-133`), but `host_ok` compares `[::1]`. This is harmless today only because `[::1]` is in the default allowed list.

## Failure classes checked
- **Lost update / two boards on one store:** no card writes changed. `oauth.json` is per board and defaults to that board's settings dir, so it isn't shared with `rusty_tick`. Two boards sharing one settings dir already share `identities.json`, so that risk isn't new here.
- **Two-step writes:** the grant-store writes belong to `rusty_oauth_server`, which this PR doesn't change.
- **Silent fallback:** an open board refuses OAuth (`web.rs:126-131`), and a missing grant-file location refuses to start. The only silent case is the stdio one above.
- **Scope leak:** finding 1.
- **Token races:** identity is resolved and cloned under the credentials read lock, the same as a bearer token. Removing an agent makes its grant 401 on the next request.
- **UI out of step:** `ui.html` is unchanged.
- **Tests that miss the window:** finding 3. The new tests can't pass at base, because `with_oauth` doesn't exist there, so they do guard the new behaviour.

## Checks and validation gaps
- **`cargo test -p rusty_mill_board`, the `rusty_oauth_server` and `rusty_tick` tests, clippy, and my probe test for finding 1:** not run. Dependency resolution failed offline (`boxcars` and `dyn-clone` aren't in the local cargo cache), and the sandbox denied network access to `index.crates.io`. None of these checks has a result I saw.
- **`python3 .github/scripts/changes.py check`** on an exported copy of the head tree: printed "488 fragment(s) ok".
- **Gaps that need a live run:**
  - the build and test suite above, plus the probe test in `tests/web.rs` for finding 1;
  - interoperability with a live claude.ai connector, which the PR also says is untested;
  - a deployed check that the issuer's host gets through the Host guard behind TLS.
