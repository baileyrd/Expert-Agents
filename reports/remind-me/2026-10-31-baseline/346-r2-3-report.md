<!-- remind-me/346-r2 run 3, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 185 s, 874771 tokens, $0.89 -->

# Review of PR 346 (Rusty-Mill/rusty-remind-me): changes required

- **Base:** d5528b1347054f348b3dac96f54619e603f4594c
- **Reviewed head:** 9daba9f88644bffd4c41612121cf5e1eeccda7e5. A fresh `git fetch origin pull/346/head` returned this SHA, so the review is pinned.
- **Commits:** 8c7e55a6 adds the OpenAI-compatible backend; 9daba9f8 is the fix for the earlier review.
- **Touched:** `crates/remind_me_core/src/embedder.rs`, the new `tests/openai_embedder_test.rs`, `README.md` and `RELEASE_NOTES.md`.

I could not build or run the crate. Cargo had to fetch the pinned `rusty_mill` dependency (tag `libs-2026.10.0`) from github.com, and that connection was refused. The checks section has the details.

## Prior findings, re-checked at this head

- **K1 (P2), an entry with a malformed index fell back to its array position: fixed (source-traced).** `embedder.rs:522-528` now reads the index with `as_u64` and turns any failure into an error saying the entry "has no valid index". That rejects a missing index, `null`, a string, a fraction and a negative number; the fallback to the array position is gone. A repeated or out-of-range index still fails at `:532-535`. The test `a_missing_or_malformed_index_is_an_error` covers all five cases. At 8c7e55a6 that code fell back to the position, so this test would have failed there; I traced that in the source but could not run it.
- **K2 (blocking), chunked responses were not decoded: fixed (executed).** `response_body` and `dechunk` (`embedder.rs:340-397`) now de-chunk the body. I copied both functions unchanged into a standalone `rustc` program and ran them:
  - Chunks with extensions and trailers decode to `"hello world"`.
  - A truncated body, a body with no final `0` chunk, and an oversized chunk size are each refused as malformed.
  - A chunked 500 still reports its status.

  The test `a_chunked_response_is_decoded` would have failed at 8c7e55a6, where the chunk framing reached the JSON parser.

## Invariants for what the diff touches

- **Embedding identity** (`embedding_meta`, recorded by `vectors.rs:485`): every stored vector comes from the identity the query uses. The new backend reports backend `openai`, the model and the dimension (`:566-572`), so a switch from Ollama is detected as a mismatch. The endpoint is not part of the identity; see design question 1.
- **Vectors in input order** (`embed_batch`): vector *n* belongs to input *n*. The count, index and dimension checks hold this.
- **Availability cache** (`config_fingerprint`): the fingerprint covers every variable `resolve_embedder` reads. Both new variables are included at `:874-875`, but no test covers that (finding 3).
- **Instruction prefixes:** a document is embedded with the same prefix every time, so stored vectors stay comparable. Qwen3's document prefix is empty, the same as before this PR, so stored vectors do not change. Query vectors are not cached anywhere (I found no query cache), so nothing persisted goes stale.
- **No change to:** sync or the outbox, tools and tool schemas, sensitivity handling or project scoping. The new backend makes blocking `std::net` calls inside `remind_me_core`, which matches that crate's thread model.

## Blocking findings

**1. P2: an embedding element that is `null` or not a number is silently stored as 0.0** (`embedder.rs:418`, `checked_vector`)

- **Trigger:** an OpenAI-compatible server returns an `embedding` array that contains `null`.
- **Scenario (source-traced):** llama.cpp's server writes its JSON with nlohmann::json, which (by my reading of that library) prints NaN as `null`. NaN embeddings are a known failure of some quantised models. Each `null` passes `as_f64().unwrap_or(0.0)` and becomes 0.0. The length check still passes because the array has the right number of elements. `l2_normalize` then divides the zero vector by `max(1e-9)`, and `embed` returns `Ok`. The memory is stored with a zero vector that never ranks in semantic search, and nothing reports a problem.
- **Who is hurt:** agents searching, who silently miss that memory.
- **Why this PR:** the lenient parse existed before for Ollama, but this PR moved it into a helper shared with the new backend and so made it reachable from llama.cpp. The release note says malformed responses are errors, not silent mismatches, which is not true here.
- **Fix:** reject any element that is not a finite number, naming the entry.
- **Test that should go red:** in `openai_embedder_test.rs`, a response such as `{"data":[{"index":0,"embedding":[null,1.0]}]}` must return `Err`.

**2. P2: RELEASE_NOTES.md says Ollama requests are unchanged, but the new Qwen3 prefix changes them** (`RELEASE_NOTES.md:11`, `embedder.rs:113-119`)

- **Trigger:** an Ollama user whose model is `qwen3-embedding:*`.
- **Scenario (source-traced):** `prefix_for` matches by substring on the lowercased model name, and `qwen3-embedding:0.6b` contains `qwen3-embed`. Each search query sent to `/api/embed` therefore now starts with `Instruct: …\nQuery:`. Rankings change for every existing Ollama or ONNX Qwen3 user.
- **Docs:** the release note says "Ollama requests … are otherwise unchanged". It never mentions the Qwen3 prefix at all, though `CONTRIBUTING`/RELEASE_NOTES require an entry for every user-visible change.
- **Who is hurt:** an operator comparing search quality before and after the upgrade.
- **Fix:** add a "Changed" entry saying that queries to any `qwen3-embed*` model on any backend now carry Qwen3's instruction, that documents are unchanged so no reindex is needed, and drop "otherwise unchanged". Add a `qwen3-embedding:0.6b` case to the `prefix_for` unit test at `embedder.rs:1080-1106` so the Ollama-style tag is covered.

**3. P2: no test covers the new `resolve_embedder` branch or the new fingerprint keys** (`embedder.rs:874-875`, `:909-914`)

- **Trigger:** a later edit drops `OPENAI_EMBED_URL_ENV` or `OPENAI_EMBED_MODEL_ENV` from `config_fingerprint`, or breaks the `"openai"` branch of `resolve_embedder`.
- **Scenario (source-traced):** every new test builds `OpenAiEmbedder::new` directly. If a key were dropped, a failed probe against one OpenAI URL would keep answering for a corrected URL for 30 seconds. That is exactly the regression `config_fingerprint`'s own doc comment warns about, and no test would fail.
- **Fix:** add an `openai` version of `a_failed_probe_does_not_answer_for_a_different_backend_address` to `embedder_availability_cache_test.rs`, which already runs as its own binary under the env lock. Also add a test that `resolve_embedder()` with backend `openai` returns identity `("openai", <model>, dim)`.

**4. P2: the status message gives the wrong reasons when the OpenAI model is not set** (`embedder.rs:994-1001`)

- **Trigger:** `REMIND_ME_EMBEDDING_BACKEND=openai` with `REMIND_ME_OPENAI_EMBED_MODEL` unset.
- **Scenario (source-traced):** `resolve_embedder` returns `Some`. The probe fails with "needs a model: set REMIND_ME_OPENAI_EMBED_MODEL", but `available_embedder` keeps only a yes/no result. `remind_me_server_status` then says "configured but not answering (unreachable daemon, or an unconfigured/unloadable ONNX model)". The real cause appears nowhere a person can see it.
- **Fix:** add a missing OpenAI model to the listed causes, or check for the empty model at configuration time and report it directly.
- **Test that should go red:** in `status_test.rs`, under the env lock, set backend `openai` with no model and assert the reason contains `REMIND_ME_OPENAI_EMBED_MODEL`.

## Failure classes checked

| Class | Result |
| --- | --- |
| Half a change | Ruled out: no stored writes, and `embed_batch` fails the whole batch. |
| Hidden rows surface | Ruled out: no query or filter changes. |
| Time as text | Ruled out: no timestamps involved. |
| Wire versus row | Ruled out: no synced or stored field changes. |
| Concurrency | Ruled out: no new shared state, and the cache lock is not held during I/O (`:946-967`). |
| Status that lies | Finding 4. |
| Retrieval admission | Identity separates backends. The endpoint is not part of it (design question 1). Each request has a 60-second read timeout but no overall deadline, which was already true before this PR. |
| Tool surface | Ruled out: no tool changes. |
| Boundary skipped | Finding 1. |
| Who is calling | Ruled out: no authentication path changes. |
| Silent success | Finding 1. A count, index or dimension mismatch, a non-2xx status and a truncated chunked body are all errors. |

## Design questions

1. `EmbeddingIdentity` is backend, model and dimension, with no endpoint (ADR-0002, #96). With llama-swap the model is just an alias the operator configures. If the weights behind the alias change, or the URL moves to a different server under the same name, old and new vectors get mixed with no mismatch reported. Ollama has the same gap, but aliases make it more likely here. Should the URL or the server's reported `model` be part of the identity?
2. `DEFAULT_OPENAI_EMBED_URL` is `http://localhost:8080`, which is also the default port of `rusty-remind-me api` (`remind_me_cli/src/main.rs:757`). With both defaults, the embedder posts memory text to remind-me's own REST API. That fails loudly rather than silently, but the overlap may confuse operators.

## Optional improvements

- `dechunk` accepts a chunk size of `+5`, because `from_str_radix` allows a leading `+` while RFC 9112 allows only hex digits.
- A truncated chunked body on a non-2xx reply loses the HTTP status from the error message.
- `status.rs:162` and ADR-0002 line 82 still name only `ollama`. They were already stale before this PR (they omit `onnx`).
- Add an Ollama test for a chunked response, since the release note claims that fix for Ollama.

## Checks and validation gaps

- `cargo fmt --all -- --check`: **ran, passed** (exit 0).
- **My standalone probe of the copied `response_body`/`dechunk` code: ran.** The results are listed under K2.
- **Not run:** `cargo clippy` and `cargo test --locked`, both for the six packages and for `--test openai_embedder_test` alone. Cargo needs the `rusty_mill` git dependency at `libs-2026.10.0#4e04d591`. The user cache `~/.cargo` is read-only, and with a scratch `CARGO_HOME` the sandbox refused github.com:443. To settle this, run the commands from the checks section with network access to github.com, plus each new test at d5528b13 and at 8c7e55a6. That would also confirm the release note's claim that the Ollama, availability-cache and status suites pass unchanged, which I could not verify.
- **No real server tested:** I ran nothing against a live llama.cpp, llama-swap or vLLM server. Running one with a model that produces NaN would confirm the trigger for finding 1.

## Verdict: **changes required**

K1 and K2 are fixed, but four new P2 findings remain.
