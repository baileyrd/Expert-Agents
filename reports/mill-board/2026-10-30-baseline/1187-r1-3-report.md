<!-- mill-board/1187-r1 run 3, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 197 s, 1110860 tokens, $1.04 -->

## Review of Rusty-Mill/rusty_mill PR #1187: "rusty_mill_board: OAuth sign-in for /mcp"

- **Base:** `55e5f3c65762cfd71c8f10883bcee20ea972ce7b`
- **Reviewed head:** `2017c08f1f83ab7a181b69574b354376f9ad5e2a`. I fetched `pull/1187/head` and it matches this SHA. The PR is one commit.

**Verdict: changes required.** A grant that is resolved by actor name can act with a stronger credential than the one that approved it. I could not run any Rust tests, so every finding below is traced from source, not executed.

### What the diff touches, and the rule each part must keep
- **`Web::identify` → `oauth_identity` (`/mcp` and `/api/*`):** a grant must act as exactly the credential that approved it, re-checked on each request, failing closed. This is the PR's own claim: "The grant then acts as that token's agent" (`deploy/README.md:50`).
- **`with_oauth` verifier (the `/consent` page):** only the board's own tokens are accepted.
- **OAuth routes (`/register`, `/authorize`, `/consent`, `/token`, `/revoke`, well-known metadata):** they sit behind `host_ok`, and the issuer's host is accepted (`main.rs:323`, README line 48).
- **`/mcp` 401:** carries the RFC 9728 `resource_metadata` challenge.
- **Grant store `oauth.json`:** owner-only, and its default path must not collide with other files.
- **`rusty_tick`:** the move into `rusty_oauth_server::serve` must not change behaviour. I diffed it and it is the same code apart from the log prefix.
- **No card writes, moves, claims, `card_take`, MCP tool schemas or UI views change.** The store and concurrency axes are only reached through `oauth.json`. That file belongs to the library and is per board, so it is not shared through rusty_tick.

## Blocking findings

### 1. P0: a grant acts as whichever credential is listed first for that actor name, not the one that approved it
- **Where:** `src/web.rs:194-207` together with `src/identities.rs:276` (`by_actor`) and `src/identities.rs:112`.
- **Trigger:** an identities file in which one actor name has two entries. `Credentials::parse` rejects only duplicate *tokens* (`seen.insert(e.token…)`, line 112), so a hand-edited file like this loads fine. ADR-0004 says "The files stay the source of truth", so hand-editing is a supported path.
- **What goes wrong:** the consent verifier (`web.rs:139-143`) stores only `Subject(actor)`. On each request, `oauth_identity` calls `by_actor(actor)`, which returns the **first** entry with that name.

**Failure scenario (source-traced):**
1. `identities.json` is `[{"token":T1,"actor":"nano","role":"human"},{"token":T2,"actor":"nano","role":"qa","projects":["rl"]}]`.
2. A model holding only T2 (qa, limited to project `rl`) runs claude.ai "Sign in now" and pastes T2.
3. `identify(T2)` matches entry 2, and the grant is stored with subject `"nano"`.
4. Every later `/mcp` or `/api` call with that access token resolves through `by_actor("nano")` to entry 1: role `human`, all projects, including Admin `settings_*` over `/api`. Admin lets it mint and rotate credentials.

- **Who is hurt:** every project on the board. A scoped agent gains Admin and loses its project limit.
- **The key isn't unique, and the PR relies on it as if it were.** `Settings::add` refuses duplicate names, but `update` and `rotate` act on the first match and `remove` drops all of them.
- **Required correction:** make actor names unique when credentials load. Reject duplicate actors in `Credentials::parse`, and apply `valid_actor` there too, so a file can never hold an ambiguous subject. Alternatively, bind a grant to a stable per-credential identifier instead of the display name. Unique actors are the smaller change.
- **Regression tests:**
  - A unit test in `identities.rs` that `parse` rejects two entries with the same actor.
  - A test in `tests/web.rs` that signs in with the second entry's token and asserts `board_whoami` returns that entry's role. I wrote this as `probe_duplicate_actor_grant_escalates`, but could not run it.

### 2. P1: a grant approved with the old shared token acts as an agent named `shared` after a switch to `--identities`
- **Where:** `src/web.rs:33` and `src/web.rs:136`, `199` and `203`.
- **Trigger:** an operator moves from `--auth-token` to `--identities`, keeps the default `oauth.json` in `--settings-dir`, and has (or later adds) an agent named `shared`. `valid_actor("shared")` is true. The `--auth-token` and `--identities` modes use the same subject namespace.

**Failure scenario (source-traced):**
1. With `--auth-token S`, a connector signs in and its grant subject is `"shared"`.
2. The operator retires S by restarting with `--identities`. That file contains `{"actor":"shared","role":"human"}`, or an admin adds that agent through the UI later.
3. The old connector's access and refresh tokens now resolve through `by_actor("shared")` and act as that agent.

- **Who is hurt:** the operator, who thinks the retired shared secret is gone, while its connector keeps an identity it never approved as.
- **Required correction:** keep the two kinds of subject apart. For example, prefix the subjects (`shared:` and `agent:<name>`), or use a subject that cannot be a valid actor name (such as one containing a character `valid_actor` rejects), and refuse grants of the other kind.
- **Regression test:** two `Web`s on one state file, first `Auth::Shared` and then `Auth::Credentials` with actor `shared`. The old access token must get 401. This is `probe_shared_grant_becomes_agent_named_shared` (also unexecuted).

### 3. P2: an IPv6 issuer's host can never pass the board's `Host` check, which contradicts the README
- **Where:** `src/main.rs:323` and `src/web.rs:172-180`.
- **What goes wrong:** `Issuer::host()` removes the brackets (`host_of`, `issuer.rs:132-133`) and adds `2001:db8::1` to the allowed hosts. `host_ok` keeps the brackets: for `Host: [2001:db8::1]:443` it compares `[2001:db8::1]`. They never match.

**Failure scenario (source-traced):**
1. The board runs with `--issuer https://[2001:db8::1]`.
2. Every OAuth route answers 403 "host not allowed", so sign-in can never complete.

- **Docs made false:** `deploy/README.md:48` ("Its host is accepted as a `Host` name") and the `--issuer` help text.
- **Who is hurt:** an operator using an IP-literal issuer.
- **Required correction:** compare `Host` the same way on both sides (the same bracket and lowercase handling), or push the bracketed form.
- **Regression test:** a test in `main`/`web` that builds a `Web` with the issuer's `host()` in its allowed hosts and sends `Host: [..]:port` to `/.well-known/oauth-authorization-server`.

### 4. P2: no test covers the issuer's `Host` being accepted
- **Where:** `src/main.rs:321-324`.
- **The gap:** `start_oauth` in `tests/web.rs` allows only `127.0.0.1`, and `raw`/`send` always send `Host: 127.0.0.1:port`. Nothing exercises the `allowed_hosts.push(issuer.host())` behaviour the change fragment and README promise. Removing that line would leave the suite green.
- **Where the test belongs:** a process test, or a test that drives `run`'s host assembly, sending `Host: localhost` with `--issuer http://localhost:…` and no `--allowed-host localhost`.

### 5. P2: a doc comment is attached to the wrong function
- **Where:** `src/identities.rs:272-276`.
- **What goes wrong:** the existing comment on `identify`, "Whose token this is. Compares against every entry, so timing does not reveal…", now sits on `by_actor`. `by_actor` is a plain, non-constant-time `find` over actor names. `identify` lost the comment that documents its timing property.
- **Required correction:** move those two lines back onto `identify`.

## Design questions (documented, not blocking)
- **Rotating a token does not end the grants it approved.** This is documented (`deploy/README.md:55-56`, change-fragment limitations). But rotation is the board's only per-agent response to a leaked token. Someone holding a leaked token can sign in once through OAuth and survive the rotation. The UI still says "\"New token\" replaces it" (`ui.html` agents tab). The only fix is stopping the board and deleting `oauth.json`, which ends everyone's grants.
  - Simpler alternative: on rotate or remove, call `Provider`/`StateStore` to drop the grants for that subject.
- **Re-adding a removed agent with the same name restores its old grants.** This is documented too. An agent name gets reused while the grants of its previous holder still exist, which cuts against "an identifier is never reused while its owner still exists". The same drop-grants-on-remove fix would remove this.
- **A corrupt `oauth.json` silently reads as empty** (`StateStore::read`, which predates this PR). The next write then overwrites it, so every grant is lost without any error. The board now makes this reachable. Consider refusing to start if the file exists but does not parse.

## Optional improvements
- `--issuer` is silently ignored in stdio mode, because `run` returns at `main.rs:309` before `oauth()` runs. It could be refused instead.
- The new tests leave directories under `std::env::temp_dir()`. A `tempfile::TempDir` would clean them up.

## Checks performed
- **Head pinned:** `git fetch origin pull/1187/head` gave `FETCH_HEAD` = `2017c08f…`, matching the head SHA.
- **Change-fragment check:** `python3 -I .github/scripts/changes.py check` on an exported copy of the head printed "488 fragment(s) ok". The `exit=0` I echoed was from the pipe, not from the script itself.
- **Code reviewed:** the diff, plus `web.rs`, `identities.rs`, `settings.rs` (credential mutation), `main.rs` wiring, `issuer.rs` (`host_of`, `validate_issuer`), `store.rs` (`StateStore`), and ADR-0004.
- **Rust tests did not run.** In the scratch copy, `cargo test -p rusty_mill_board` (and the probe tests) failed before compiling:
  - The pinned toolchain 1.98.1 can't install, because `~/.rustup` is read-only. I used `RUSTUP_TOOLCHAIN=stable`, which is also 1.98.1.
  - The sandbox's network allowlist refuses access to crates.io.
  - Offline resolution fails: the local cache lacks `boxcars`, the locked `tokio 1.53.3`, and `proptest`. Removing Rocket League from the workspace and re-resolving in the scratch copy still did not get it to build.
- **Not run at all:** `cargo test -p rusty_mill_board`, the `rusty_tick` and `rusty_oauth_server` tests (both touched by this PR), and clippy. None of these are passing as far as I have seen.

## Validation gaps
- **All Rust tests and clippy.** They need network access to crates.io or a populated cargo cache. Running `cargo test -p rusty_mill_board -p rusty_tick -p rusty_oauth_server --all-features` and `cargo clippy` on the head would settle this. The same run should include the three probe tests in `scratch/head/crates/apps/rusty_mill_board/tests/web.rs`. I expect the first two to fail at the head, which would confirm findings 1 and 2.
- **Live claude.ai connector interop** behind TLS. The PR itself says CI can't test this.
- **The PR body was not read.** Only the head ref is served, so the PR description and its claims are unchecked beyond the change fragment and docs.

**Verdict: changes required.**
