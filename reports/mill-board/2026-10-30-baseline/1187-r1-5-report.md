<!-- mill-board/1187-r1 run 5, variant baseline, prompt 6f8b297a8d8b51ce008e641769215a46032faff9; 267 s, 1554538 tokens, $1.34 -->

I found two blocking problems in PR 1187: verdict is **changes required**. One is an authority escalation; the other means OAuth sign-in cannot work with an IPv6 issuer. I could not build or run anything here, so every finding is traced from the code, not executed.

**Repository:** Rusty-Mill/rusty_mill · **PR:** 1187 · **Base:** `55e5f3c65762cfd71c8f10883bcee20ea972ce7b` · **Reviewed head:** `2017c08f1f83ab7a181b69574b354376f9ad5e2a`. The fetched PR head matches this SHA. The PR is one commit: "rusty_mill_board: OAuth sign-in for /mcp".

## What the diff touches, and the rule each must keep
- **Consent check** (`web.rs:124-150`): consent accepts only the board's own tokens, and the grant's subject identifies exactly the credential that approved it.
- **Request authentication** (`web.rs:184-227`): on `/mcp` and `/api`, an OAuth token acts with exactly the approving credential's current authority. It fails closed when that holder is gone.
- **`/mcp` 401** (`web.rs:230-235`): with OAuth on, it carries the RFC 9728 `resource_metadata` challenge.
- **OAuth endpoints** (`web.rs:321-324`): they sit behind the same Host guard and are counted for shutdown draining.
- **Host acceptance** (`main.rs:320-324`): the issuer's host is accepted the same way by both Host guards (the board's own and the MCP server's).
- **Grant file** (`main.rs:162-180`): grants go to an owner-only file chosen from one unambiguous rule.
- **rusty_tick adapter** (`rusty_tick/src/mcp.rs`): behaviour is unchanged.
- **Untouched**: board rules (moves, claims, `card_take`, settings) and `ui.html`.

## Blocking findings

### 1. P0: an OAuth grant can act with a different credential's authority when two credentials share an actor name
**Where:** `src/identities.rs:276-278` (`by_actor`), used at `src/web.rs:200-205`; consent subject set at `src/web.rs:141-145`.

**Trigger:** an identities file in which one actor name has two tokens. `Credentials::parse` (`identities.rs:94-135`) rejects a repeated token but not a repeated actor. Only the settings UI's `add` (`identities.rs:172`) checks names.

**What happens (source-traced):**
1. `identities.json` has `{T1,"claude","human"}` first, then `{T2,"claude","builder","projects":["rl"]}`. The board accepts it at startup.
2. The holder of T2 (a builder limited to `rl`) approves a claude.ai connector at `/consent`. The check matches T2 and records only the actor name, `"claude"`.
3. On every later `/mcp` or `/api` call, `by_actor("claude")` returns the *first* entry. The connector now acts as `human`: Admin, every project, including `/api/settings_*`.

Before this PR each token resolved to its own entry, so a repeated actor name gave no one extra authority. The PR resolves authority from a key that is not unique.

**Who is hurt:** every project and the board's settings. A scoped agent gains Admin over MCP.

**Correction:** make the actor name a unique key — `parse` refuses a repeated actor at startup (fail closed). Or bind the grant to the credential entry rather than the name, and have `by_actor` return `None` when the name is ambiguous.

**Regression tests:**
- An `identities.rs` unit test that `parse` rejects two entries with the same actor.
- A `tests/web.rs` test that signs in with the scoped token of a repeated actor and expects either refusal or `board_whoami` reporting `builder` limited to `rl`.

### 2. P2: OAuth sign-in fails under an IPv6-literal issuer, though the docs say its host is accepted
**Where:** `src/main.rs:322-323` with `src/web.rs:172-181` (`host_ok`) and `rusty_oauth_server/src/issuer.rs:28-31`.

**What happens (source-traced):**
1. The operator runs with `--issuer https://[2001:db8::1]`. `validate_issuer` accepts it, and `Issuer::host()` returns `2001:db8::1` with the brackets removed and lowercased.
2. The connector sends `Host: [2001:db8::1]`. The MCP guard (`rusty_mcp_server/src/http.rs:217-233`) strips the brackets, ignores case, and accepts it.
3. `Web::host_ok` keeps the brackets and compares case-sensitively, so `/register`, `/authorize`, `/consent`, `/token` and `/.well-known/*` all answer 403 "host not allowed" (`web.rs:318`). Sign-in can never complete.

The same mismatch refuses a mixed-case Host header such as `Board.Example.com`. Both contradict the change fragment and `deploy/README.md` ("Its host is accepted as a `Host` name"). The failure is closed, and the workaround is `--allowed-host '[…]'`.

**Who is hurt:** a person setting up the claude.ai connector.

**Correction:** have `host_ok` normalise the header as `host_only` does (strip brackets, ignore case), or push the issuer host in the form `host_ok` compares.

**Regression test:** a `tests/web.rs` test with `ISSUER = "http://[::1]:8787"` and hosts from the same assembly as `main.rs`, sending `Host: [::1]:8787` to `/.well-known/oauth-authorization-server` and expecting 200.

### 3. P2: `identify`'s doc comment now sits on `by_actor` and states the opposite of its behaviour
**Where:** `src/identities.rs:272-280`.

The inserted `by_actor` landed between `identify`'s doc lines. The rustdoc "Whose token this is. Compares against every entry, so timing does not reveal which entry matched…" now documents `by_actor`, which stops at the first match. `identify` loses its documented constant-time guarantee.

**Correction:** move lines 272-273 back above `identify`. No test is needed; it is documentation.

### 4. P2: no test covers the issuer-host and grant-file wiring in `main.rs`
**Where:** `src/main.rs:162-180` and `320-324`.

The new web tests pass hosts by hand (`["127.0.0.1"]`) and never send `Host` equal to the issuer's host. They also never exercise how the grant-file location is chosen (`--oauth-state`, then `--settings-dir`, then beside `--identities`, else an error). Deleting the `allowed_hosts.push(issuer.host())` line would turn no test red.

**Correction:** extract the host/state assembly into a function and unit-test it, or add a process test that starts the binary with `--issuer` and fetches the metadata through the issuer's host.

## Design questions for the owner
1. **Grants outlive token rotation, and come back when an agent is re-added.** The PR documents both as known limitations. Rotating is the usual response to a leaked token, but a grant approved with the leaked token stays live through refresh. With the board running there is no way to end one agent's grants: removing the agent suspends them, and re-adding the same name hands the old connector the new agent's authority. The only remedy is stopping the board and deleting `oauth.json`, an operator step on a running service's files. A simpler alternative that also fixes Finding 1 is to make the subject a per-credential id that changes on add and on rotate.
2. **OAuth tokens also work on `/api`.** That includes `settings_*` (add, remove and rotate agents) when the approving agent is Admin. The README and the change fragment describe sign-in "for `/mcp`", and the token's resource is `…/mcp` (`endpoints.rs:240`). Accepting tokens only on `/mcp` would match that audience.
3. **A corrupt or unreadable `oauth.json` reads as empty, and the next write overwrites it** (`rusty_oauth_server/src/store.rs:157-158`). Every grant is lost silently. Unlike `identities.json`, the board does not check this file at startup (`read_private`).

## Optional improvements
- The subject `"shared"` (`web.rs:33`) is also a valid agent name. Grants made under `--auth-token` would act as an agent named `shared` if the same grant file were later used with `--identities`. Prefixing subjects by mode would rule this out.
- `--issuer` is silently ignored in stdio mode (`main.rs:311-314`); a warning would help.
- `docs/connecting-models.md` does not mention the claude.ai OAuth route; a link to the deploy README section would.

## Checks and validation gaps
| Check | Result |
|---|---|
| `python3 .github/scripts/changes.py check` | Ran: "488 fragment(s) ok" |
| `cargo test -p rusty_mill_board`, `-p rusty_oauth_server --features serve`, `-p rusty_tick` | **Not run.** The pinned toolchain 1.98.1 is not installed and `~/.rustup` is read-only in the sandbox. On `stable`, `--offline` cannot resolve `boxcars`. The crates.io connection was refused (user denied). A trimmed scratch workspace also failed: a crate version `rusty_http` needs is not cached. |
| clippy | Not run, same reason |
| Probes (repeated-actor escalation, IPv6 Host) | Written up as traces only; not executed |
| Do the new tests guard anything at base? | They call `Web::with_oauth`, which does not exist at base, so they cannot compile there; I could not run them. |
| Live claude.ai connector, browser consent page | Not testable here |

To settle the gaps, run the three `cargo test` commands and clippy on toolchain 1.98.1 with network access, plus the regression tests named above.

**Verdict: changes required.**
