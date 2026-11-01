<!-- remind-me/346-r1 run 1, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 1273 s, 2300404 tokens, $1.77 -->

# Review: Rusty-Mill/rusty-remind-me PR #346, "embedder: OpenAI-compatible /v1/embeddings backend"

**Verdict: changes required.** There are six P2 defects and no P0 or P1. The bad-vector checks the release note promises can be bypassed by malformed vectors, and the documented default URL fails against llama-server's default setup.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `8c7e55a64821d8f411b8578d76dfa81c4b43b3b8`. `git fetch origin pull/346/head` returned this SHA.
- **Scope:** `crates/remind_me_core/src/embedder.rs`, a new `tests/openai_embedder_test.rs`, `README.md` and `RELEASE_NOTES.md`. No sync, outbox, hub, tool-schema or storage code changes.

**The working tree changed during the review.** `embedder.rs` and `openai_embedder_test.rs` were rewritten at 15:08 CST while my test runs were going. I did not write to the reviewed tree. The content now matches the head blob (`8deaacad…`). Afterwards a build in the working tree failed with "no `OpenAiEmbedder` in `embedder`", so cargo's cache held a build of other content. I read the diff from git objects. All probes and all results under Checks were run on a `git archive` of the pinned head (or base) with fresh target dirs, not on the working tree.

## What the diff touches and what must hold
- **The embedder's identity** (`backend/model/dim`, stored in `embedding_meta` as the `EmbeddingIdentity` triple): every stored vector must come from the embedder that queries use. The new backend reports itself as `openai`. Passage prefixes are unchanged, so existing Ollama vectors stay valid.
- **The response from `/v1/embeddings`**: each input gets exactly one vector of the configured dimension, in input order. Malformed input must be refused, never coerced. The release note promises "a wrong count, a repeated index or a wrong dimension is an error, not a silent mismatch."
- **The shared HTTP client `post_json`** (now used by both backends): it must accept or refuse whatever a conforming HTTP/1.1 server may send (RFC 9110/9112).
- **The availability cache and status**: the cache key (`config_fingerprint`) must cover every variable `resolve_embedder` reads, and `embedding_status` must say why the backend isn't working.
- **Replication, containment, tool contract**: not touched. Vectors are not synced, there is no secret, and no tool or schema changed.

## Blocking findings (all P2)

**1. P2: a `null` or non-number vector component silently becomes 0.0** (`embedder.rs:376`). *Executed.*
- **Trigger:** the server returns `{"data":[{"index":0,"embedding":[null,1.0,1.0]}]}`. llama.cpp's JSON library writes NaN as `null`.
- **Result:** `Ok([[0.0, 0.707, 0.707]])`. A corrupted vector is stored with no error. If every component is `null`, the result is an all-zero vector (the normalisation clamps the zero norm), which matches nothing.
- **Who is hurt:** agents searching, because that memory is mis-ranked or never found until a reindex.
- **Context:** the code came from the Ollama path, but this PR feeds it a new input source and its release note promises no silent mismatch.
- **Fix:** refuse any entry that is not a finite number (`as_f64()` → error, plus `is_finite()` after the cast to f32).
- **Test:** `a_null_or_non_finite_component_is_an_error` in `openai_embedder_test.rs`.

**2. P2: an `index` that isn't an integer is ignored and arrival order is used** (`embedder.rs:483`, `map_or(position, …)`). *Executed.*
- **Trigger:** the server returns `"index":"1"`, then `"index":"0"`, for inputs `["first","second"]`.
- **Result:** `Ok([[0,1],[1,0]])`. Each memory gets the other's vector, with no error. This contradicts the release-note claim at `RELEASE_NOTES.md:8`.
- **Fix:** allow the positional fallback only when `index` is absent. If `index` is present but not a `u64`, return an error.
- **Test:** add a non-integer-index case to `a_repeated_or_out_of_range_index_is_an_error`.

**3. P2: the documented default URL can't reach llama-server's default bind** (`embedder.rs:60` default; `311–313` uses only the first resolved address). *Executed.*
- **Cause:** on this host `localhost` resolves to `[::1, 127.0.0.1]`. `post_json` tries only `::1`. llama-server listens on `127.0.0.1` by default, and the README pairs this backend with it.
- **Result:** with the server bound to `127.0.0.1` and the URL `http://localhost:<port>`, the call fails with "Connection refused". Semantic search silently drops to keyword-only, and status says "configured but not answering".
- **Context:** the same limitation exists on the Ollama path. This PR makes it the out-of-the-box result for the new backend.
- **Fix:** try each resolved address in turn with `connect_timeout`, or default to `http://127.0.0.1:8080`.
- **Test:** bind a fake server on `127.0.0.1` only and embed through `http://localhost:<port>`.

**4. P2: status gives the wrong reason when the model is unset** (`embedder.rs:864–869`, `953`). *Executed.*
- **Trigger:** `REMIND_ME_EMBEDDING_BACKEND=openai` with `REMIND_ME_OPENAI_EMBED_MODEL` unset.
- **Result:** `remind_me_server_status` reports "configured but not answering (unreachable daemon, or an unconfigured/unloadable ONNX model)". The real cause, the missing model variable, isn't named, and the probe's error is thrown away. The operator is sent to debug a daemon that is up.
- **Fix:** name the missing `REMIND_ME_OPENAI_EMBED_MODEL` in the status reason, for example by checking it in `embedding_status` before probing.
- **Test:** in `status_test.rs` (under its env lock), set backend `openai` with no model and assert the reason contains `OPENAI_EMBED_MODEL_ENV`.

**5. P2: the docs no longer match the code.**
- `RELEASE_NOTES.md:11` says "Ollama requests and error messages are unchanged." That is false: the new `qwen3-embed` prefix (`embedder.rs:116`) matches by substring on any backend. An Ollama `qwen3-embedding:0.6b` query now sends `"Instruct: Given a search query…\nQuery:quokka"` (*executed*), so ranking changes for those users.
- The Qwen3 query-instruction change has no release-note entry at all.
- `README.md:18` still says "Ollama or an in-process ONNX bi-encoder".
- **Fix:** correct the "unchanged" line, add a Changed entry for the Qwen3 query prefix (all backends; stored passages are unaffected) and update the README summary.

**6. P2: coverage is missing for the resolver and cache changes.** *Source-traced.*
- No test anywhere references `"openai"` as a backend value, `OPENAI_EMBED_URL_ENV` or `DEFAULT_OPENAI_EMBED_URL`. All new tests construct `OpenAiEmbedder` directly.
- So the following could be deleted or broken with every test still green:
  - the `"openai"` arm of `resolve_embedder` (`:864`);
  - its environment reads and default URL;
  - both `config_fingerprint` entries (`:829–830`), which recreates the stale-verdict bug that function's comment warns about.
- **Fix:** add a resolve test in the style of `onnx_embedder_test.rs::resolve_embedder_dispatches_to_onnx…`. Add an openai case to `embedder_availability_cache_test.rs` (its own binary, under `ENV_LOCK`): a dead `OPENAI_EMBED_URL`, then a live one, must flip the verdict with no wait.
- **Base check:** the new test file doesn't compile at base (`OpenAiEmbedder` is absent there), so every new test is red without the change.

## Ruled out
- **Replication, containment, tool surface:** no records, outbox, wire format or tool schema changed. `identity()` stays a backend/model/dim triple.
- **Ollama refactor:** every error string is byte-identical, including the dim message, whose `\` continuation joins the lines correctly. The order of the empty-check and the error is preserved.
- **Index handling:** count, repeat and out-of-range checks are right. Placement is one-to-one, so the `flatten()` in `embed_batch` cannot drop a slot.
- **Concurrency:** the cache lock is not held across the probe's socket I/O (unchanged).
- **Chunked responses:** `Transfer-Encoding: chunked` is refused, not mis-parsed. *Executed*: "response was not JSON: trailing characters".

## Design questions (for the owner)
1. **Identity without the endpoint.** For `openai`, the "model" is the name the server routes on, often a llama-swap alias. If an alias is repointed to a different model with the same dimension, the `embedding_meta` mismatch check misses it and old and new vectors get mixed. Should the identity include the URL, or the model name the server reports back?
2. **ADR-0002** (Accepted) records Ollama as the embedding backend and says "the Ollama backend… stands". A third backend has no amendment. Should one be added?
3. **HyDE expansion** still posts to Ollama's `/api/generate` at `REMIND_ME_OLLAMA_URL`. A llama-swap-only user who enables `hyde` gets a quiet fallback with no expansion.

## Optional improvements
- The `Host` header omits the port (`:321`). RFC 9110 §7.2 requires it to match the URL's authority, including the port. This predates the PR; I found no server it breaks.
- Send HTTP/1.0, or decode chunked bodies, so a chunking proxy works. At minimum, name the cause in the error.
- There is no overall deadline on the call: the 60 s timeout applies to each read, so a slow-dripping server can stall a query embedding.
- The Qwen3 code comment says it uses the model card's "exact" form. The format is exact, but the task text differs from the card's "Given a web search query".

## Checks
Run on a clean `git archive` of the head with a fresh target dir:
- **`cargo fmt --all -- --check`:** passes.
- **`cargo clippy --locked … --all-targets -- -D warnings`** (all six packages): passes.
- **`cargo test --locked --no-fail-fast`** (six packages): 2376 passed, 112 failed.
  - 108 fail with a read-only filesystem error (the tests write under `$HOME`, which is read-only here) and 3 with permission denied (`peer_discovery_test`). These are sandbox limits in tests the PR doesn't touch.
  - 1 is `remind_me_mcp`'s `test_server_status_reports_embeddings_active…`: the fake daemon wasn't contacted within 20 s while the full suite ran in parallel. It passes alone at head (3/3) and at base (2/2), and the whole `remind_me_mcp --lib` passes at head (105/105). I read it as a load flake, not caused by this PR.
- **Embedder-related targets** all pass: `openai_embedder_test` 10/10, `ollama_embedder_test`, `embedder_availability_cache_test`, `status_test`, `vectors_test`, `open_reconcile_test`, `onnx_embedder_test`, `query_expansion_test`, `injectable_embedder_test`, `search_raw_test`, and the `embedder` lib unit tests.
- **Probes:** written in a scratch copy and run there. The results are quoted in the findings above.

**Validation gaps:**
- I didn't run against real llama.cpp, llama-swap, vLLM or LM Studio servers. Whether any of them sends chunked or compressed bodies, or `null` for NaN in practice, needs a live run.
- Finding 3 depends on how the host resolves `localhost`; it's confirmed only on this Fedora host. Windows is untested.
- The `$HOME`-writing tests couldn't run in this sandbox.
- The local-embed feature was not built.
