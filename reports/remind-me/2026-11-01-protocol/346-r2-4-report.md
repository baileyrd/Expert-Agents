<!-- remind-me/346-r2 run 4, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 1019 s, 1478104 tokens, $1.34 -->

# Review of Rusty-Mill/rusty-remind-me PR 346: verdict **changes required**

Both earlier findings are fixed at this head and their tests guard the fixes. Four new P2 defects remain:
- A `null` value inside an embedding is silently stored as 0.0.
- The release notes say Ollama requests are unchanged, but they now change for Qwen3 models.
- Two docs still list only two backends.
- No test covers the new `openai` setting being read from the environment.

## The PR

- **Repository / PR:** Rusty-Mill/rusty-remind-me #346
- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head reviewed:** `9daba9f88644bffd4c41612121cf5e1eeccda7e5`. A fetch of `pull/346/head` returned this SHA, and so does the local HEAD.
- **Commits:** `8c7e55a64` adds the OpenAI-compatible backend. `9daba9f88` requires valid indexes and decodes chunked responses.
- **Files touched:** only `remind_me_core/src/embedder.rs`, a new `tests/openai_embedder_test.rs`, `README.md` and `RELEASE_NOTES.md`. No sync, outbox, hub, tool or schema code changed.

## What each touched item must guarantee

| Item | What must hold |
|---|---|
| `OpenAiEmbedder` (`/v1/embeddings`) | It returns exactly one vector per input, in input order, each made by the configured backend, model and dimension. Anything else is an error, never a silent mismatch. |
| Shared HTTP reader (`response_body`, `dechunk`, `post_json`) | Anything a conforming HTTP/1.1 server may send (RFC 9112 framing, chunked encoding, any header case, error bodies) is either decoded correctly or refused with an error. It is never mis-decoded into success. |
| `checked_vector` (used by both Ollama and OpenAI) | A stored vector is exactly what the model produced, normalised, with the configured dimension. |
| `ROLE_PREFIXES` / `prefix_for` | The query/document prefixes match what the model was trained on. Any change to what is sent is documented, because query vectors move without the stored identity changing. |
| `resolve_embedder` / `config_fingerprint` | Every environment variable the resolver reads is also part of the cache key. Otherwise a changed setting keeps getting an old availability verdict. |
| `EmbeddingIdentity` | Switching backends triggers the existing model-change clear (ADR-0002, `vectors.rs`). The `openai` backend has its own identity, so switching from Ollama does clear. |

No memory row or wire format changed, and nothing new crosses the outbox, push or hub.

## Earlier findings, re-checked at this head

- **K1 (P2, a malformed index fell back to array position): fixed.** `embedder.rs:520-532` now requires an integer index of zero or more (`as_u64`). A `null`, string, fractional, negative or missing index is an error. A repeated or out-of-range index is refused at `:536-539`, and a wrong entry count at `:507-517`. `a_missing_or_malformed_index_is_an_error` fails against 8c7e55a6 and passes at this head (executed).
- **K2 (blocking, chunked responses not decoded): fixed.** `response_body` and `dechunk` (`embedder.rs:340-397`) recognise `Transfer-Encoding: chunked` in any case, accept chunk extensions and refuse a truncated body. `a_chunked_response_is_decoded` and `a_truncated_chunked_response_is_an_error` both fail against 8c7e55a6 and pass at this head (executed).

## Blocking findings

### F1 (P2): a `null` or non-numeric value inside an embedding is stored as 0.0
- **Where:** `crates/remind_me_core/src/embedder.rs:418`, `.map(|v| v.as_f64().unwrap_or(0.0) as f32)`.
- **This PR makes it reachable:** the code is shared with Ollama, but Ollama is written in Go and errors rather than encoding NaN. llama.cpp's server, the main target of this PR, builds JSON with nlohmann::json, which writes NaN and infinity as `null`.
- **Scenario (executed):** a scratch probe at this head sent a response with embedding `[null,0.5,null]` (dimension 3).
  - Result: `Ok([[0.0, 1.0, 0.0]])`.
  - With `[null,null,null]` the result is `Ok([[0.0, 0.0, 0.0]])`, an all-zero vector, reported as success.
  - That vector is stored under the correct identity, so it is never cleared. The memory then ranks wrongly or never appears in semantic search.
- **Who is hurt:** agents searching the store, and the operator, since nothing in status shows the problem.
- **Fix:** refuse any element that is not a finite number (`as_f64().filter(|x| x.is_finite()).ok_or_else(...)`). Also refuse a vector whose norm is zero rather than storing it.
- **Regression test:** in `openai_embedder_test.rs`, `a_null_or_non_numeric_element_is_an_error`, with `[null,0.5]` and `["0.1",0.5]` both expected to return `Err`. Add the same case to `ollama_embedder_test.rs`.

### F2 (P2): the release notes say Ollama requests are unchanged, but Qwen3 queries now get a prefix
- **Where:** `RELEASE_NOTES.md:11` says "Ollama requests and error messages are otherwise unchanged". The prefix is added at `embedder.rs:113-119`.
- **The matching is shared:** `prefix_for` (`:122-133`) matches a substring of the lowercased model name for both backends. Ollama's `qwen3-embedding:*` tags contain `qwen3-embed`.
- **Scenario (executed):** the same probe with `OllamaEmbedder::new("qwen3-embedding:0.6b", …)` and a query.
  - At the base the request body was `{"input":["quokkas"],…}`.
  - At this head it is `{"input":["Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery:quokkas"],…}`.
  - Existing Ollama users of Qwen3 therefore get different query vectors with no identity change. Documents are still sent without a prefix, so no reindex is needed, but retrieval changes.
  - The prefix addition has no release-note entry at all. It appears only in commit 8c7e55a6's message.
- **Who is hurt:** operators reading the release notes.
- **Fix:** add a "Changed" bullet saying queries to models whose name contains `qwen3-embed`, on either backend, now carry the model card's instruction. Remove or qualify "otherwise unchanged".
- **Regression test:** `ollama_embedder_test.rs::a_qwen3_embedding_query_carries_the_instruction`. There is currently no Ollama-side test of this prefix.

### F3 (P2): two docs still list only Ollama and ONNX
- **Where:** `README.md:18` says "Optional vector search (Ollama or an in-process ONNX bi-encoder)". `embedder.rs:65` says "(Ollama or ONNX) sets this to match".
- **Scenario (source-traced):** the PR's own README table (`README.md:527`) and `resolve_embedder` (`:909`) now include `openai`. A reader of the feature list would conclude llama.cpp, llama-swap and vLLM are unsupported.
- **Fix:** name the OpenAI-compatible backend in both places.
- **Regression test:** none; this is a docs-only fix.

### F4 (P2, coverage): no test checks the `openai` backend is read from the environment, or that its two settings are in the availability cache key
- **Where:** `embedder.rs:909-914` (the `"openai"` branch of `resolve_embedder`) and `:874-875` (`OPENAI_EMBED_URL_ENV` and `OPENAI_EMBED_MODEL_ENV` in `config_fingerprint`).
- **Scenario (source-traced):** no test sets `REMIND_ME_EMBEDDING_BACKEND=openai` or either new setting; `grep` finds them only in `embedder.rs` and the model-name assertion. Any of these edits would leave the suite green:
  - deleting the `openai` branch, which turns the backend off with no error;
  - dropping the default URL;
  - removing either setting from the cache key, so changing the URL keeps a stale "not answering" verdict for up to 30 s. That is the bug `config_fingerprint`'s own doc comment warns about.
- **Fix:** add environment-driven tests under the `test_env` lock, in their own test binary:
  - `REMIND_ME_EMBEDDING_BACKEND=openai` resolves to identity `("openai", model, dim)`, uses the default URL, and drops a trailing `/v1` when the URL comes from the environment.
  - Add a case to `embedder_availability_cache_test.rs` where a failed probe does not carry over when `REMIND_ME_OPENAI_EMBED_URL` changes.

## Failure classes checked

- **Half a change / concurrency / data sent between nodes:** not applicable. The change has no transaction, no shared state beyond the existing availability mutex, and no new row field. The cache is still keyed on the full configuration.
- **Hidden rows / time as text / secrets / caller identity:** not touched. The embedder only receives text that its callers already chose to embed.
- **Retrieval admission (the right embedder made each vector):** switching to `openai` changes the identity and triggers the existing clear. The weak point is that the identity has no endpoint (see the design questions below).
- **Protocol subset**, probed (executed) except where marked:
  - **Header case:** handled; the test uses `Transfer-Encoding: Chunked`.
  - **Chunk extensions:** handled.
  - **Chunked error status:** handled; a chunked 503 is reported as `returned 503: {"error":"load"}`.
  - **Truncated chunked body:** refused.
  - **Bare-LF framing:** refused. Accepting it is only a MAY in RFC 9112 §2.2.
  - **A 1xx interim response followed by 200:** refused as `returned 100`. This is not RFC 9110 §15.2's required tolerance, but it fails loudly and is the same as before this PR.
  - **Compressed bodies (source-traced):** nothing asks the server to avoid compression, so it could send a gzipped body. That would make the JSON parse fail loudly, as before this PR.
  - **Gzip combined with chunked:** fails loudly.
  - **Huge chunk sizes:** handled; `get(..size)?` returns `None`, so there is no overflow or panic.
- **Silent success:** F1. Count, index and dimension errors are all reported as errors.
- **Status that lies:** see the first optional improvement.

## Design questions for the owner

1. **ADR-0002 is out of date.** Its decision still says "only `"ollama"` enables it". This was already untrue at the base (`onnx`, which ADR-0018 mentions only as a feature gate), and `openai` widens the gap. Should ADR-0002 be amended to list the backends and the rule for adding one?
2. **The embedder identity has no endpoint.** `EmbeddingIdentity` is backend, model and dimension. llama-swap model names are aliases the operator picks. Changing the GGUF file behind an alias, or pointing `REMIND_ME_OPENAI_EMBED_URL` at another server using the same alias, leaves the identity unchanged. The model-change clear then never runs and vectors from two models get mixed. Ollama already had this behaviour with retagged models, but the alias pattern makes it much more likely.

## Optional improvements

- **Status message:** with `openai` and no model set, `embedding_status` says "configured but not answering (unreachable daemon, or … ONNX model)" (`embedder.rs:997`). It should name `REMIND_ME_OPENAI_EMBED_MODEL`.
- **Compression:** send `Accept-Encoding: identity` so a proxy never gzips the response.
- **1xx responses:** skip interim 1xx responses before reading the status.
- **Truncated error bodies:** for a chunked non-2xx whose body is cut short, keep the status code in the error instead of only "malformed".
- **Ollama chunked test:** add an Ollama-side chunked test. The release-notes claim is currently covered only through the shared code path.

## Checks performed

- **`cargo fmt --all -- --check`:** passed.
- **`cargo clippy --locked -p remind_me_core -p remind_me_mcp -p remind_me_api -p rusty-remind-me -p remind_me_remote -p remind_me_hub --all-targets -- -D warnings`:** passed. I ran it with `--offline` and `CARGO_TARGET_DIR` under `$TMPDIR`, because the configured target directory is read-only.
- **Embedder test binaries at this head:** `openai_embedder_test` 13/13, `ollama_embedder_test` 7/7, `embedder_availability_cache_test` 2/2 and `status_test` 11/11 all passed.
- **Head's tests against 8c7e55a6** (scratch copy): 10 passed and the 3 K1/K2 tests failed, as they should. At the base the new test file does not compile because `OpenAiEmbedder` does not exist yet.
- **Full suite over the six packages** (`--no-fail-fast`, with `HOME` pointed at a scratch directory): 168 test binaries, 2488 passed, 3 failed.
  - The 3 failures are `peer_discovery_test`; the sandbox does not allow binding a Unix socket (`PermissionDenied`). The PR does not touch peer discovery.
  - An earlier run with the real `HOME` also failed 6 `remind_me_api` `import_export_test` tests, because the sandbox makes `$HOME` read-only. They pass with the scratch `HOME`.
  - My first full-suite log was overwritten by another session writing to the same shared `$TMPDIR`, so I discarded it and reran into a private directory.
- **Probes** were run from scratch copies of the tree; nothing in the reviewed tree was modified.

## Validation gaps

- **Unix-socket peer-discovery tests:** need a run outside this sandbox.
- **No real servers:** I had no real llama.cpp, llama-swap or vLLM server. Testing against `llama-server --embeddings` with a large batch would confirm that it really sends chunked responses and `null` for NaN, which F1 is based on.
- **No Ollama with `qwen3-embedding`:** I could not confirm the retrieval effect of F2 against a real model.
- **Feature build and Postgres:** I did not build with the `local-embed` feature. Postgres is not relevant, since nothing in the hub changed.

## Verdict: **changes required**

F1 to F4 are all P2, and under the review rules any defect requires changes.
