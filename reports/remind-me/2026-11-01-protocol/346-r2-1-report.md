<!-- remind-me/346-r2 run 1, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 926 s, 1927746 tokens, $1.74 -->

# Review of PR 346 (Rusty-Mill/rusty-remind-me): changes required

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `9daba9f88644bffd4c41612121cf5e1eeccda7e5`. The fetched `pull/346/head` matches this SHA.
- **Commits:** `8c7e55a64` adds the OpenAI-compatible backend; `9daba9f88` is the fix for K1 and K2.
- **Files:** `embedder.rs`, the new `tests/openai_embedder_test.rs`, `README.md`, `RELEASE_NOTES.md`.

Both earlier findings are fixed. The PR still has one P1 and five P2 defects.

## Earlier findings at this head
- **K1 (P2): fixed.** At `embedder.rs:522-528` a missing, null, string, fractional or negative index is now an error. It no longer falls back to array position. `a_missing_or_malformed_index_is_an_error` fails at 8c7e55a6 and passes at head. A probe also confirmed that `"index": 1.0` is refused.
- **K2 (blocking): fixed.** `response_body`/`dechunk` (`embedder.rs:338-394`) decode chunked bodies. I probed:
  - These decode: any header casing, chunk extensions, trailers, single and multiple chunks.
  - These are errors, not silent passes: truncated bodies, bare-LF framing, and a `Transfer-Encoding` list that doesn't end in `chunked`.
  - `a_chunked_response_is_decoded` and `a_truncated_chunked_response_is_an_error` fail at 8c7e55a6 and pass at head.

## Blocking findings

**F1 (P1): setting the `openai` backend without a model deletes every stored vector the next time the database opens.** `embedder.rs:909-914`, `db/mod.rs:315-317`. *Executed.*
- **Trigger:** the store holds vectors recorded as `{openai, "qwen3-embedding-0.6b", 4}`. A process starts with `REMIND_ME_EMBEDDING_BACKEND=openai` but without `REMIND_ME_OPENAI_EMBED_MODEL`. That could be a hook or CLI process that inherited only part of the environment, or a user who set the backend before the model.
- **What happens:** `resolve_embedder()` still returns `Some`, with identity `{openai, "", 4}`. `Database::open` → `reconcile_embedding_meta` sees a mismatch and clears `vec_chunks`.
- **Probe result:** the control reopen with the model set kept 1 vector. The reopen without it left 0.
- **Why it's wrong:** the PR itself calls this configuration unusable ("Required; there is no sensible default"; `embed` refuses it at `:549`), but it still triggers a destructive model-change clear.
- **Who's hurt:** every agent searching the store. Semantic search silently falls back to keyword-only for all memories. Because reconcile leaves `embedding_meta` alone, a later correctly configured open sees "no mismatch" and doesn't flag anything, so the store stays degraded until someone runs a reindex.
- **Why P1 and not P0:** vectors are derived data and a reindex recovers them.
- **Fix:** don't build an identity from an invalid configuration. Either `resolve_embedder` returns `None` for `openai` with an empty model, or the open-time reconcile skips invalid configurations, and `embedding_status` reports why (see F2).
- **Regression test:** in `open_reconcile_test.rs` (it already uses `ENV_LOCK`), plant openai meta plus one vector, remove the model variable, reopen, and assert the count is still 1.

**F2 (P2): the status message gives the wrong cause when the model is missing.** `embedder.rs:996-999`. *Source-traced.*
- **Trigger:** `openai` backend with no model.
- **What happens:** the availability probe fails on the model check, and `remind_me_server_status` reports "configured but not answering (unreachable daemon, or an unconfigured/unloadable ONNX model)". The operator is pointed at the network when the real cause is an unset environment variable.
- **Fix:** surface the actual `EmbedError` text, or name `REMIND_ME_OPENAI_EMBED_MODEL`.
- **Test:** an env-locked status test asserting the reason names the variable.

**F3 (P2): bad numbers in an embedding are stored instead of refused.** `embedder.rs:418`, `checked_vector`. *Executed.*
- **What happens:**
  - `null` or string elements become `0.0`.
  - `[null, null]` is stored as a zero vector.
  - `1e300` becomes `inf`, which normalises to `[NaN, 0.0]`.
- **Why it matters here:** the code itself predates the PR, but the PR routes a new, previously unvalidated server type through it. As far as I know, llama.cpp's JSON library writes NaN as `null`, so a NaN embedding would arrive as all-`null` and be stored. I didn't check that against a live server.
- **Who's hurt:** agents whose semantic scores are computed against a zero or NaN vector.
- **Fix:** refuse any element that is not a finite number in f32 range.
- **Tests:** add null, string and overflow cases to `openai_embedder_test.rs`, each asserting an error.

**F4 (P2): an undocumented behaviour change for Ollama users, and a release-note claim it makes false.** `embedder.rs:113-119`, `RELEASE_NOTES.md:11`. *Source-traced.*
- **What changed:** the new `qwen3-embed` query prefix is matched by case-insensitive substring and applied by `OllamaEmbedder` too (`:435`). An Ollama model such as `qwen3-embedding:0.6b` now gets the instruction on every query, so its search rankings change.
- **The notes:** they never mention the prefix, and they state "Ollama requests … are otherwise unchanged", which is no longer true.
- **Fix:** add a Changed entry, and drop or qualify that sentence.

**F5 (P2): the PR differs from ADR-0002, which is still Accepted.** `docs/adr/0002-…:80-83`. *Source-traced.*
- **The ADR says:** "Embedding backend: Ollama's `POST /api/embed` … (only `"ollama"` enables it…)".
- **The PR adds:** a third backend over a different protocol, with no addendum. The ADR does foresee ONNX; it doesn't foresee this.
- **Fix:** add an addendum, as the 2026-09-26 storage update did.

**F6 (P2): missing tests for the resolver and the availability-cache key.** *Source-traced.*
- **The gap:** no test sets `REMIND_ME_EMBEDDING_BACKEND=openai`. The only matches for `"openai"` are in `openai_embedder_test.rs`, which never touches the environment.
- **Consequence:** you could delete the `"openai"` arm (`:909`), the default URL, or the two new `config_fingerprint` entries (`:874-875`) and every test would still pass. The fingerprint comment calls that exact omission "the bug this function exists to close".
- **Fix:** an env-locked resolver test, plus a fingerprint test in `embedder_availability_cache_test.rs` that changes `REMIND_ME_OPENAI_EMBED_URL` and checks the cached verdict is dropped.

## Design questions
- `EmbeddingIdentity` has no endpoint. Pointing `REMIND_ME_OPENAI_EMBED_URL` at a different server that serves different weights under the same model name keeps the old vectors. ADR-0002 settles the backend/model/dimension triple, so this is the owner's decision, but it's easier to hit with llama-swap aliases.
- Moving the same weights from `ollama` to `openai` clears every vector, because the backend is part of the identity. That is settled, but users switching to llama-swap should be told, ideally in the release notes.

## Optional improvements
These are all refused loudly today; none is a silent success.
- RFC 9110 §15.2 says a client must parse 1xx interim responses. A `100 Continue` before the 200 is currently reported as "returned 100". This predates the PR.
- A truncated chunked body on a 5xx is reported as "malformed HTTP response", which hides the status code. Check the status before de-chunking.
- Send `Accept-Encoding: identity`. Under RFC 9110 §12.5.3, leaving it out allows any content coding.
- `dechunk` accepts a `+` sign on the chunk size, and could accept bare-LF line endings (RFC 9112 §2.2 permits it).
- The config-only message at `status.rs:162` still says only `=ollama`. This also predates the PR.

## Failure classes not covered above
- **Ruled out:** replication, outbox, containment and tool-surface changes. The diff touches only the embedder, so no record, wire format or tool schema changes.
- **Half a change:** F1 is the only case.
- **Retrieval admission:** F1 and F3, plus the identity question above.
- **Concurrency:** unchanged. The availability cache is still check-then-probe, and it's the same code as before.
- **Time as text:** no timestamps in the diff.
- **Silent success:** F3.

## Checks
- `cargo fmt --all -- --check`: **passed**.
- `cargo clippy --locked` over the six packages, `--all-targets -D warnings`: **passed**.
- `cargo test --locked -p remind_me_core --test openai_embedder_test`: **13/13 pass** at head. The three K1/K2 tests **fail at 8c7e55a6**, so they guard the fixes.
- Full `cargo test --locked --no-fail-fast` over the six packages:
  - 167 test binaries passed; one failed (`peer_discovery_test`, 3 tests).
  - Those 3 tests fail because the sandbox refuses a Unix-socket bind (EPERM). That code is outside this diff.
  - That run used a writable `HOME`. With the real `HOME` (read-only here), several import, archive and dbs-import tests failed on EROFS.
  - The default target dir is read-only, so all builds used a target dir under `$TMPDIR`.
- **Probes:** run in `git archive` copies under `$TMPDIR`, outside the reviewed tree. My first HTTP probes showed false chunked failures. The cause was a stale 8c7e55a6 artifact in a shared target dir: `git archive` gives files old mtimes, so cargo didn't rebuild. With a clean target dir every result above reproduced.

## Validation gaps
- Not tested against a live llama.cpp, llama-swap, vLLM or LM Studio server, so real response framing and NaN-as-`null` are unconfirmed.
- No Postgres; the hub isn't touched by this PR.
- The `peer_discovery_test` failures above are sandbox-caused and outside the diff.

## Verdict: **changes required**
Fix F1 (P1) and F2–F6 (P2). K1 and K2 are fixed.
