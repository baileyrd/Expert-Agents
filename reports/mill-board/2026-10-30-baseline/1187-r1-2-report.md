<!-- mill-board/1187-r1 run 2, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 251 s, 1450766 tokens, $1.25 -->

I couldn't run any build or test in this sandbox, so the duplicate-actor finding below is traced from source. I found one blocking authority defect, two smaller defects and a coverage gap. **Verdict: changes required.**

## Review pin
- **Repository:** Rusty-Mill/rusty_mill, PR **#1187**
- **Base:** `55e5f3c65762cfd71c8f10883bcee20ea972ce7b`
- **Head:** `2017c08f1f83ab7a181b69574b354376f9ad5e2a`. `git fetch origin pull/1187/head` returned exactly this SHA. It is one commit: `2017c08f1 rusty_mill_board: OAuth sign-in for /mcp`.

## What the diff touches, and the rule each must keep
| Item | Rule it must keep |
|---|---|
| `/mcp` and `/api/*` identity (`web.rs:184-227`) | A credential fixes exactly one actor, role and project scope (`identities.rs:7-9`; `connecting-models.md:33`). |
| OAuth endpoints (`web.rs:321-324`) | Only after the `Host` check; refused on an open board (`web.rs:124-151`). |
| Consent check (the closure in `with_oauth`) | Accepts only the board's live tokens, not OAuth access tokens. |
| Grant → identity (`oauth_identity`, `by_actor`) | The grant gets exactly the authority of the token that approved it, looked up by a unique key; it fails closed once the agent is removed. |
| Issuer host added to allowed hosts (`main.rs:320-323`) | The `/mcp` guard and the board's own guard treat `Host` the same way. |
| `/mcp` 401 | Carries the RFC 9728 `resource_metadata` challenge when OAuth is on. |
| Grant file `oauth.json` (`main.rs:163-180`) | Owner-only; never shared between issuers. |
| rusty_tick moved onto `serve::handle` / `serve::unauthorized` | Same behaviour as its private adapter before. |

The diff touches no card writes, columns, claims, `card_take`, MCP tool definitions or UI views.

## Blocking findings

### 1. P0: a grant approved with one token can act with a different token's role and projects
- **Where:** `crates/apps/rusty_mill_board/src/identities.rs:276-278` (`by_actor`), called from `src/web.rs:200-205`.
- **Cause:** `Credentials::parse` (`identities.rs:94-136`) refuses duplicate *tokens* but not duplicate *actor names*. Only `add` from the settings view checks names (`:172`). A hand-written `--identities` file can legitimately give one person a full token and a narrow one.
- **What happens:** the grant stores only the actor name, and every request picks the *first* entry with that name.
- **Trigger and trace (source-traced):**
  1. `identities.json` is `[{"token":A,"actor":"nano","role":"human"}, {"token":S,"actor":"nano","role":"qa","projects":["x"]}]`. `parse` accepts it.
  2. The owner signs a claude.ai connector in with the narrow token S. The consent check maps S to the subject `"nano"`.
  3. On every `/mcp` or `/api` call, `oauth_identity` runs `by_actor("nano")` and gets entry A: role `human`, all projects.
  4. The agent can now see and move cards in every project, and reach the Admin-only settings routes.
- **Who is hurt:** an agent over MCP gains authority it was never given. If the entries are in the other order, a connector approved with the full token silently loses access instead.
- **Docs this makes false:** the changelog ("a grant acts as the agent whose token approved it"), `deploy/README.md` ("acts as that token's agent") and `identities.rs:7` ("cannot act as any other actor or role").
- **Required correction:** make the grant subject unique. Either refuse duplicate actor names in `Credentials::parse` (which matches `add` and fails closed at startup), or give each credential a stable unique id to use as the subject.
- **Regression tests:**
  - In `identities.rs` tests: `parse` rejects two entries with the same actor.
  - In `tests/web.rs`: a grant approved with the narrower token never reports `role: human` from `board_whoami`.
- **Probe status:** I wrote this test in a scratch copy (`scratch/head/.../tests/probe_oauth.rs`) but could not build it (see Checks). The finding stands on the code.

### 2. P2: the board's `Host` guard rejects an IPv6-literal issuer that the docs say is accepted
- **Where:** `src/main.rs:320-323` with `src/web.rs:172-181`.
- **Cause:** `Issuer::host()` strips the brackets and lowercases. `rusty_mcp_server`'s `host_allowed` (`http.rs:216-233`) also strips brackets and ignores case, but `Web::host_ok` compares the exact string with brackets kept.
- **Trace (source-traced):**
  1. Start with `--issuer https://[2001:db8::5]`.
  2. `/mcp` accepts `Host: [2001:db8::5]`.
  3. `/.well-known/…`, `/authorize`, `/token` and `/consent` all go through `host_ok`, which compares `"[2001:db8::5]"` with `"2001:db8::5"` and returns 403.
  4. The connector cannot sign in, which contradicts `deploy/README.md` ("Its host is accepted as a `Host` name").
- **Same split for case:** a `Host` header in a different case passes `/mcp` but gets 403 from the OAuth routes.
- **Who is hurt:** a person setting up a connector.
- **Required correction:** make `host_ok` use the same normalisation as `host_only` / `host_allowed`.
- **Regression test:** a `web.rs` test with an IPv6 or mixed-case issuer that reaches `/.well-known/oauth-authorization-server` through the issuer's own `Host`.

### 3. P2: misplaced doc comment states the wrong thing about two functions
- **Where:** `src/identities.rs:272-280`.
- **Problem:** the comment that belonged to `identify` ("Compares against every entry, so timing does not reveal…") now sits on top of `by_actor`. `by_actor` stops at the first match, so the comment is false for it, and `identify` has lost its timing note.
- **Correction:** move the two sentences back onto `identify`.

### 4. P2: coverage gap
- **Untested:** that the issuer's host is accepted (`main.rs:320-323`), and how the default `oauth.json` location is chosen (`main.rs:163-180`).
- **Why the tests miss it:** they use issuer `localhost` but send `Host: 127.0.0.1`, so they never go through the issuer-host path.
- **Where tests belong:** a process test for the CLI (`--issuer` with no settings dir or identities fails; `--oauth-state` without `--issuer` is refused), plus finding 2's web test.
- **Do the new tests guard anything?** The three tests in `tests/web.rs` call `with_oauth`, which does not exist at the base, so they cannot pass there.

## Design questions (not blocking)
- **Rotating a token doesn't end grants it approved, and re-adding an agent name brings back its old grants.** This is documented, and matches rusty_tick's settled lifecycle (`rusty_tick/src/oauth.rs:12-21`, #1184). It still hurts:
  - Rotating after a leak doesn't cut off a connector the attacker approved.
  - Refresh tokens rotate on use, so that connector can keep refreshing indefinitely.
  - To end one agent's grants, the admin must remove and rename the agent, or stop the board and delete `oauth.json`, which ends everyone's.
  - A new agent who reuses a removed name inherits a stranger's grants.

  A simpler alternative: settings `remove` and `rotate` call `revoke_grant` for that subject.
- **A corrupt or unreadable `oauth.json` is read as empty.** That is how `StateStore` already works in the library (it has a test for it), and this PR now uses it in the board. The next write then silently drops every grant and client registration. Consider refusing to start when the file exists but doesn't parse.

## Optional improvements
- **Two boards can share one `oauth.json` by default.** It goes "beside `--identities`", and `connecting-models.md` puts that file at `~/mill-identities.json`. `StateStore` has no lock across processes, so concurrent writes can lose a grant, and a token from one board's issuer is accepted by the other. Prefer requiring `--settings-dir` or `--oauth-state` explicitly.

## Checks and validation gaps
| Check | Result |
|---|---|
| `python3 .github/scripts/changes.py check` | Ran: **488 fragment(s) ok**, exit 0. |
| `cargo test -p rusty_mill_board` | **Not run.** The repo pins Rust 1.98.1 and rustup could not write to `~/.rustup` (read-only). The installed stable toolchain is also 1.98.1, but the workspace needs crates that aren't in the local cache (`boxcars`, then `tokio 1.53.3`, then `dyn-clone`), and crates.io is blocked. Trimming the workspace and regenerating the lockfile in the scratch copy didn't get past this. |
| Probe test for finding 1 | Written in the scratch copy, not run (same build problem). |
| `rusty_oauth_server` and `rusty_tick` tests (both touched), clippy | Not run, same reason. |
| Live claude.ai connector sign-in, browser | Not run; the PR also says this isn't testable from CI. |

To settle the gaps: in an environment with network access or a full crate cache, run `cargo test -p rusty_mill_board -p rusty_oauth_server --features rusty_oauth_server/serve -p rusty_tick`, `cargo clippy` on those crates, and the probe test.

## Verdict
**Changes required.**
