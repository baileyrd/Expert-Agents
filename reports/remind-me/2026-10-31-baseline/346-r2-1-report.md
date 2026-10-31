<!-- remind-me/346-r2 run 1, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 249 s, 1637525 tokens, $1.36 -->

# Review of PR 346: OpenAI-compatible `/v1/embeddings` backend

**Repository:** Rusty-Mill/rusty-remind-me · **PR:** 346 · **Base:** `d5528b1347054f348b3dac96f54619e603f4594c` · **Reviewed head:** `9daba9f88644bffd4c41612121cf5e1eeccda7e5` (fetched `pull/346/head`; it equals the given head SHA)

**Verdict: changes required.** One P1 and two P2 findings. Both earlier findings (K1, K2) are fixed. I could not build or test the workspace itself: the sandbox blocks github.com, where its `rusty_mill` dependency lives. Everything marked *executed* below ran against code copied verbatim from `embedder.rs` into a scratch crate.

## Earlier findings, checked again at this head
- **K1 (P2), malformed index fell back to array position: fixed.** At `embedder.rs:521-528` a missing, null, string, fractional or negative index is now an error. The test `a_missing_or_malformed_index_is_an_error` fails against 8c7e55a6 and passes at head (*executed*).
- **K2 (blocking), chunked responses not decoded: fixed.** `response_body` and `dechunk` (`embedder.rs:340-395`) now decode chunked responses and reject one cut short. `a_chunked_response_is_decoded` and `a_truncated_chunked_response_is_an_error` both fail against 8c7e55a6 and pass at head (*executed*). This also changes the Ollama backend, which shares the same HTTP client.

## What the PR touches, and the rule each must keep
- **`/v1/embeddings` response → stored vector:** one finite, L2-normalised vector per input, in input order, of length `dim`. Bad input is refused, never stored.
- **`embedding_meta` identity** (backend, model, dimension; ADR-0002 addendum on #96): `openai` is a separate backend, so switching from `ollama` clears the vectors. That holds (`identity()`, `embedder.rs:566`).
- **Availability cache key** (`config_fingerprint`): must list every variable `resolve_embedder` reads. The two new variables are added (`:873-874`).
- **Query and passage prefixes:** passages are unchanged, so stored vectors stay valid.
- **Not touched:** no rows, outbox, sync wire format, tool schemas or hub code. Memory truth, durable replication and tool-surface checks therefore have nothing to trace.

## Blocking findings

### F1 (P1): bad numbers inside an embedding are stored as 0 or NaN, and a NaN score can panic search
- **Where:** `crates/remind_me_core/src/embedder.rs:418` (`v.as_f64().unwrap_or(0.0) as f32`). The panic happens later, in the sort at `crates/remind_me_core/src/vectors.rs:335`.
- **Trigger:** a server returns an embedding element that is not a finite number in f32 range. llama.cpp's server writes a NaN as JSON `null`.
- **What happens:**
  - **Nulls and strings (*executed*):** `{"embedding":[null,null,null]}` becomes `Ok([[0.0,0.0,0.0]])`. A partly-null response becomes a skewed vector. `embed_and_store` stores it and marks `embedding_meta` current (`vectors.rs:131-138`), and the `"ping"` probe passes, so status shows Active. That memory then never surfaces in semantic search, and nothing reports why. Agents searching for it are hurt.
  - **Out of f32 range (*executed*):** `["x",1e39,0.5]` becomes `Ok([[0.0, NaN, 0.0]])`. A stored NaN vector gives that memory a NaN score in `scan_and_score`. The sort comparator `partial_cmp(..).unwrap_or(Equal)` then has no consistent order.
  - **The panic (*executed* on the sort alone; the path from stored vector to sort is *source-traced*):** on rustc 1.98, sorting 200 scores with about 1 in 7 NaN using that comparator panicked in 49 of 2000 trials ("user-provided comparison function does not correctly implement a total order"). So it is a panic on a read path. MCP catches it (`remind_me_mcp/src/lib.rs:3632`), but the search still fails.
- **Scope:** this coercion was already on the Ollama path. This PR moves it into the shared `checked_vector` and makes it the input check for a new kind of server, one that does emit `null` for NaN.
- **Fix:** in `checked_vector`, refuse any element that is not a JSON number, or whose `as f32` is not finite, with an `EmbedError` naming the entry. Optionally also make the sort at `vectors.rs:335` use `f32::total_cmp`, or drop non-finite scores.
- **Regression tests:** in `openai_embedder_test.rs`, add `a_null_string_or_out_of_range_element_is_an_error` (entries `[null,…]`, `["x",…]`, `[1e39,…]`). It goes red at head. Add a `vectors_test.rs` case that stores a NaN vector next to finite ones and searches.

### F2 (P2): the release note says Ollama requests are unchanged, but this PR changes them, and the Qwen3 prefix isn't mentioned
- **Where:** `RELEASE_NOTES.md:11` and `crates/remind_me_core/src/embedder.rs:113-119`.
- **What happens (*executed*):** the new `qwen3-embed` row is matched by substring against any backend's model name. An Ollama user with `REMIND_ME_OLLAMA_EMBED_MODEL=qwen3-embedding:0.6b` now sends `{"input":["Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery:quokkas"],...}`, where before the PR the query went bare. Search ranking changes for that user with no release note, and the note says the opposite. The prefix appears only in the commit message, and no `RELEASE_NOTES.md` entry mentions it (CONTRIBUTING requires one for every user-visible change).
- **Fix:** add a `### Changed` bullet saying that queries to models whose name contains `qwen3-embed` now carry Qwen3-Embedding's instruction on every backend, Ollama included, and that passages are unchanged. Drop or correct "Ollama requests … are otherwise unchanged".
- **Regression test:** extend `ollama_embedder_test.rs` with a Qwen3 query-prefix case. This change is to documentation, so no test goes red without it.

### F3 (P2): the backend selection and the cache-key additions have no test
- **Where:** `crates/remind_me_core/src/embedder.rs:909-914` (the `"openai"` branch of `resolve_embedder`) and `:873-874` (fingerprint).
- **Failure scenario (*source-traced*):** remove `OPENAI_EMBED_URL_ENV` from `config_fingerprint`, or break the `"openai"` match branch, and all 13 new tests still pass. They only build `OpenAiEmbedder` directly. The comment at `:864-866` names exactly this as the bug the fingerprint list exists to prevent: a failed probe of one URL keeps answering for a corrected URL for 30 seconds.
- **Fix and tests:** in `embedder_availability_cache_test.rs`, under its environment lock, add `an_openai_failed_probe_does_not_answer_for_a_different_url`, which mirrors the Ollama test at `:102` using `REMIND_ME_OPENAI_EMBED_URL`. Also add an environment-driven test that `REMIND_ME_EMBEDDING_BACKEND=openai` resolves to identity backend `openai` with the configured model.

## Failure classes checked
- **Half a change, wire versus row, concurrency (locks and threads), who is calling:** nothing to trace. The diff writes no rows and adds no sync fields, locks, threads or callers. The availability cache's locking is unchanged.
- **Hidden rows surface, time as text:** ruled out. No query, filter, list or timestamp code is touched.
- **Retrieval admission and embedder identity:** moving between `ollama` and `openai` clears vectors because the backend is part of the identity. The endpoint is not part of it; see design question 1.
- **Status that lies:** the ping probe reports Active for an embedder that returns zero or NaN vectors (part of F1). `status.rs:162` is stale (optional improvement 1).
- **Silent success:** F1. The count and index checks hold (*executed* tests). A non-2xx reply is an error. A non-2xx reply whose chunked body is malformed reports "malformed", not the status code; that is minor.
- **Tool surface:** no tools added. Only the status reason string changes (`:987`), and no test asserts on it.
- **Boundary skipped / containment:** no API key is stored. Memory text goes over plain HTTP to the configured host, as with Ollama, which ADR-0002 settles.
- **Panics in the HTTP code:** `dechunk` cannot overflow. `get(..size)?` caps `size` at the body length before `size + 2` is computed.

## Design questions (for the owner, not findings)
1. **Endpoint is not part of the identity.** The identity is backend, model and dimension (ADR-0002 addendum). llama.cpp's `llama-server` ignores the request's `model` field, and llama-swap's model names are aliases the operator picks. If someone swaps the GGUF behind an unchanged name, or points the URL at a different host, the identity still matches. Old vectors are then searched with query vectors from a different model, and nothing flags it. Should the `openai` identity include the URL or the `model` the response reports?
2. **A missing model wipes the vectors.** With `backend=openai` and no `REMIND_ME_OPENAI_EMBED_MODEL`, `resolve_embedder` still returns an embedder with model `""`. Opening the database then clears the vectors of a working index (`db/mod.rs:315-316`), even though this embedder can never produce any. ONNX with an empty path behaves the same way, so this follows existing practice. Should a backend that is configured but unusable skip the reconciliation?

## Optional improvements
1. `status.rs:162` still tells users to set `REMIND_ME_EMBEDDING_BACKEND=ollama`. It already omitted onnx before this PR. The README's feature list at line 18 still says "Ollama or an in-process ONNX bi-encoder".
2. The `Host` header has no port (`embedder.rs:324`). RFC 9110 §7.2 expects the port when it isn't the default. This predates the PR.
3. A `Transfer-Encoding` header with more than one coding (e.g. `gzip, chunked`) isn't de-chunked. Harmless while no `Accept-Encoding` is sent.

## Checks run
- `cargo fmt --all -- --check`: **passed** (exit 0).
- `cargo clippy` / `cargo test --locked` over the six packages: **not run.** The `rusty_mill` git dependency at `libs-2026.10.0` can't be fetched (github.com is denied in the sandbox), `--offline` has no copy of it, and `~/.cargo` is read-only. This is a validation gap; settle it by running the CONTRIBUTING commands where github.com is reachable.
- **Scratch crate, head code:** `embedder.rs` lines 1–573 plus `l2_normalize`, built against serde_json 1.0.151 from the local cache. The PR's `openai_embedder_test.rs` ran **13 of 13 passed**.
- **Scratch crate, 8c7e55a6 code:** the same tests gave **10 passed, 3 failed** (the K1 and K2 tests). My first run of this showed a false 13/13 because both scratch crates shared one target directory; I reran with separate directories.
- **Against the base:** the new test file can't compile there because `OpenAiEmbedder` doesn't exist, so it is red by construction.
- **Probes (*executed*):** null, string and 1e39 elements accepted as 0 / NaN (F1); the Ollama Qwen3 request body now prefixed (F2); the NaN sort panic (49 of 2000 trials).
- **Validation gaps:** the release note's claim that "the Ollama, availability-cache and status suites pass unchanged" is unverified because those suites need the workspace build. No live llama.cpp, llama-swap or vLLM server was available to confirm their real `index` and `null` output.
