# Mill Board reviewer: answer keys

Each key is a merged PR's first reviewed head and base, and the blocking findings the
human review raised on that head, with the commit that fixed each. The prompt's
examples are drawn from other PRs, so no key's own defect is cited in the prompt; the
failure classes themselves were mined from all reviews, keys included.

## #1238: board-wide card numbers

Head `43e0720953f356a5a1abec4fd4da8a6a9a3032a5`, base `d50375a25ced7622f88810e6d3bcad8f1986bb50`.
Review: https://github.com/Rusty-Mill/rusty_mill/pull/1238#issuecomment-6503674964

| Key | Severity | Finding | Fix |
|---|---|---|---|
| K1 | P1 | Allocation is serialized by a process-local mutex (`src/board.rs:345-353`); two boards on one rusty_tick both take `max+1`. | `22a35d8f` |
| K2 | P1 | Numbers come from `Store::list()` only (`src/board.rs:326-331`), so untag, trash or restore makes a live card's number reusable; lookup picks the first of duplicates instead of failing closed. | `22a35d8f` |

## #1187: OAuth sign-in for /mcp

Head `2017c08f1f83ab7a181b69574b354376f9ad5e2a`, base `55e5f3c65762cfd71c8f10883bcee20ea972ce7b`.

| Key | Severity | Finding | Fix |
|---|---|---|---|
| K1 | P1 | `by_actor` returns the first credential with an actor name (`src/identities.rs:276-277`); a restricted token's grant resolves to a duplicate and gains its role and projects. [review](https://github.com/Rusty-Mill/rusty_mill/pull/1187#pullrequestreview-5673783860) | `8452ee5f` |
| K2 | P2 | An IPv6 issuer's Host is bracketed differently by the web guard and the MCP guard (`issuer.rs:29-33`), so metadata and consent return 403. [review](https://github.com/Rusty-Mill/rusty_mill/pull/1187#pullrequestreview-5673796580) | `10927f9d` |
| K3 | P1 | Two boards in one directory share the default `oauth.json` (`src/main.rs:168-178`); board A's grant is valid on board B. [review](https://github.com/Rusty-Mill/rusty_mill/pull/1187#pullrequestreview-5673834930) | `1ebd8a89` |

## #1165: cards only in rusty_tick (ADR-0012 step 6)

Head `453dd9f21e5c005b13f21d59fd30093af1bfbc01`, base `c4a0d031`.
Review: https://github.com/Rusty-Mill/rusty_mill/pull/1165#pullrequestreview-5669285044

| Key | Severity | Finding | Fix |
|---|---|---|---|
| K1 | P1 | The removed `MILL_BOARD_STORE` env binding is silently ignored (`src/main.rs:197-204`): a shipped unit starts an empty, healthy in-memory board whose writes are lost at restart. | `f680de28` |
| K2 | blocking | The real-process tests (first-admin token confidentiality, SIGTERM) were deleted with `service.rs`; restore them on a synthetic backend. | `f680de28` |
| K3 | blocking | Store docs claim a card and its event become visible atomically, in commit order; the tick HTTP commit precedes local event recording. | `f680de28` |

The round-2 P2 (process tests clear only six env vars, fixed in `1a1e58b2`) was raised on
`f680de28`, after the tests were restored, so it is not on this key's head.
