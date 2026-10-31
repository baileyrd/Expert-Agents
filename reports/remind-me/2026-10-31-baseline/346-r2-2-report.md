<!-- remind-me/346-r2 run 2, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 212 s, 938257 tokens, $1.05 -->

# Review of PR 346: Rusty-Mill/rusty-remind-me, changes required

The PR adds an `openai` embedding backend that calls `POST /v1/embeddings`. Both earlier findings (K1, K2) are fixed. Four new defects block it, all P2. The most serious lets a broken embedding from llama.cpp be stored silently. I couldn't build or test the workspace: GitHub access was denied in the sandbox.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Reviewed head:** `9daba9f88644bffd4c41612121cf5e1eeccda7e5` (fetched `pull/346/head`; it equals the given head SHA)
- **Commits:** 8c7e55a64 (the new `openai` backend, plus a query prefix for Qwen3-Embedding models), 9daba9f88 (fixes for K1 and K2)

## What the diff touches and what must hold

| Touched | Invariant |
|---|---|
| Vectors from `OpenAiEmbedder` | One correctly placed vector per input, exactly `REMIND_ME_EMBEDDING_DIM` long, from the model it claims; malformed replies are refused. |
| `EmbeddingIdentity` `("openai", model, dim)` | Written to `embedding_meta` on store (`vectors.rs:138`) and checked at open (`db/mod.rs:316`). A query is never compared against vectors from a different embedder. |
| Role-prefix table (`embedder.rs:103-120`) | Matching is by substring of the model name, so the new `qwen3-embed` entry applies to every backend, Ollama included. Documents still go in with no prefix, so stored vectors stay valid. |
| `config_fingerprint` (availability cache key) | Must list every variable `resolve_embedder` reads. The two new ones are listed. |
| Shared HTTP client `response_body` / `dechunk` | Ollama's behaviour is unchanged apart from now decoding chunked responses. Malformed framing is an error. |
| `embedding_status` | Its reason text must say why the backend is unavailable. |

The sync, outbox, hub, tool, auth and memory-truth axes are not touched: no row, wire field or tool schema changes. Most failure classes in the checklist don't apply for that reason. I traced the ones that do: retrieval admission, silent success, status that lies, and input validated where it enters.

## Earlier findings, re-checked at this head

- **K1 (P2), a malformed index fell back to array position: resolved.** At 8c7e55a6 the code was `.map_or(position, …)` (line 483). At this head (`embedder.rs:522-528`) an entry needs an integer index of 0 or more, or the batch fails. Repeats and out-of-range values are rejected at `:532-536`, and the entry count must equal the batch size (`:511`), so every slot gets filled. The test `a_missing_or_malformed_index_is_an_error` would fail at 8c7e55a6.
- **K2 (blocking), chunked responses were not decoded: resolved.** `response_body` and `dechunk` (`embedder.rs:340-397`) are now in place. I ran verbatim copies in a scratch crate:
  - a body split in the middle of a UTF-8 character, with chunk extensions and trailers, decoded correctly;
  - truncated input and an oversized hex chunk size returned `None`, with no panic;
  - a chunked 500 kept its status and body.

  The test `a_chunked_response_is_decoded` would fail at 8c7e55a6, where the chunk framing reached the JSON parser.

## Blocking findings

### F1 (P2): a null or non-numeric number in an embedding is stored as 0.0
- **Where:** `embedder.rs:419` (`checked_vector`), `.map(|v| v.as_f64().unwrap_or(0.0) as f32)`. The new backend calls it at `:537`.
- **Trigger:** llama.cpp's server writes JSON with nlohmann::json, which writes NaN and Inf as `null`. A model or quantisation that produces NaN therefore sends `"embedding":[null,…]`. Go's JSON encoder refuses NaN, so the Ollama backend could not reach this case; the new backend makes it reachable.
- **What happens (*executed*, verbatim copy):**
  - `[null,null]` gives `Ok([0.0,0.0])`.
  - `["0.6","0.8"]` gives `Ok([0.0,0.0])`.
  - `[0.6,null]` gives `Ok([1.0,0.0])`, which is a different direction, not just a weaker vector.

  `embed_and_store` (`vectors.rs:109-138`) stores that vector and marks the identity current. The memory now has a `vec_chunks` row, so `reindex` never repairs it: it only embeds memories with no rows (`vectors.rs:391-416`).
- **Who is hurt:** every agent searching. That memory is missing from semantic results, or ranked against unrelated queries, permanently and with nothing to show it. Keyword search still finds it, so I graded this P2 (malformed input accepted).
- **Fix:** refuse any element that is not a finite number. Use `v.as_f64().filter(|x| x.is_finite())` and return an `EmbedError` naming the entry otherwise.
- **Regression test:** in `openai_embedder_test.rs`, a fake server replies `{"data":[{"index":0,"embedding":[0.6,null]}]}` and the test asserts `embed` returns an error. It should go red at this head.

### F2 (P2): the release note is false about Ollama and omits the Qwen3 query prefix
- **Where:** `RELEASE_NOTES.md:13` says "Ollama requests and error messages are otherwise unchanged". The new prefix entry is at `embedder.rs:113-119`.
- **What happens (*source-traced*):** an operator on `REMIND_ME_EMBEDDING_BACKEND=ollama` with `REMIND_ME_OLLAMA_EMBED_MODEL=qwen3-embedding:0.6b` upgrades. `prefix_for` matches `qwen3-embed` by substring (`:122-133`). Every query sent to `/api/embed` now begins `Instruct: Given a search query…\nQuery:`, so their search ranking changes.
- **Who is hurt:** the person reading the release notes. The change appears nowhere in them or in the README (the only mention is in commit 8c7e55a6's message), and the note says the opposite.
- **Fix:** add a "Changed" entry: Qwen3-Embedding queries now carry the model card's instruction on every backend, documents are unchanged so no reindex is needed. Drop "otherwise unchanged", or narrow it.
- **Regression test:** an `ollama_embedder_test.rs` case asserting the query sent for a `qwen3-embedding` model carries the instruction. The existing Qwen3 test only covers `OpenAiEmbedder`.

### F3 (P2): server status blames an unreachable daemon when the model is unset
- **Where:** `embedder.rs:907-913` (resolution succeeds without a model), `:964-966` (`available_embedder` drops the error), `:992-999` (status reason).
- **What happens (*source-traced*):** set `REMIND_ME_EMBEDDING_BACKEND=openai` and leave `REMIND_ME_OPENAI_EMBED_MODEL` unset. `resolve_embedder` still returns an embedder. Its probe fails with the useful message "set REMIND_ME_OPENAI_EMBED_MODEL", but `.is_ok()` throws that away. `remind_me_server_status` then reports "configured but not answering (unreachable daemon, or an unconfigured/unloadable ONNX model)", and `remind_me_reindex` reports "degraded".
- **Who is hurt:** the operator. No surface ever shows the real cause; the status points at the network.
- **Fix:** either return `None` from the `openai` arm when the model is empty and say why in `embedding_status`, or keep the probe's error in the availability cache and use it as the reason.
- **Regression test:** in `status_test.rs`, under the env lock, set the backend to `openai` with no model and assert the reason contains `REMIND_ME_OPENAI_EMBED_MODEL`.

### F4 (P2): resolving the `openai` backend and its cache key are untested
- **Where:** `embedder.rs:909-914` (the `openai` arm of `resolve_embedder`) and `:874-875` (the two new variables in `config_fingerprint`).
- **What happens (*source-traced*):** every new test builds `OpenAiEmbedder` directly. Deleting the `openai` arm, changing the default URL, or dropping `OPENAI_EMBED_URL_ENV` from the fingerprint turns nothing red. Dropping it from the fingerprint brings back exactly the stale-verdict bug that `config_fingerprint`'s doc comment says it exists to prevent.
- **Fix and test:**
  - In `embedder_availability_cache_test.rs` (its own binary, env lock): with `openai`, a failing URL followed by a working fake server must report available at once.
  - Next to `onnx_embedder_test.rs:40-48`: `REMIND_ME_EMBEDDING_BACKEND=openai` resolves to identity `("openai", model, dim)`.

## Design questions for the owner
1. **The identity doesn't include the endpoint.** It is `(backend, model, dim)`, decided under #96 / ADR-0002. llama-swap model names are operator-chosen aliases. If `REMIND_ME_OPENAI_EMBED_URL` is pointed at a different server, or the alias is pointed at a different GGUF file, no mismatch is detected. The search then compares new query vectors against stored vectors from another model. Should the `openai` identity record the URL, or the server's reported model?
2. **ADR-0002 is out of date.** Its Decision section (`docs/adr/0002…md:79-83`) still says only `"ollama"` enables embeddings. It was already out of date once ONNX was added, and this PR adds a third backend without amending it.

## Optional improvements (not blocking)
- `Transfer-Encoding: identity, chunked` isn't recognised: matching needs the exact value `chunked`, while RFC 9112 §6.3 says the last coding decides. *Executed:* this gives a JSON parse error, not silent corruption, and llama.cpp and llama-swap send plain `chunked`.
- `README.md:18` still lists only "Ollama or an in-process ONNX bi-encoder" as vector-search backends.
- `status.rs:162` (not touched by this PR) still only suggests `=ollama`.
- The `Host` header leaves out a non-default port (RFC 9110 §7.2). This predates the PR; servers that route by path don't care.
- Add an Ollama test with a chunked response, since the release note claims that behaviour for Ollama.

## Checks and validation gaps
- `cargo fmt --all -- --check`: **passed**.
- `cargo clippy --locked …` over the six packages: **not run**. `~/.cargo` is read-only. With a scratch `CARGO_HOME` the build needs the `rusty_mill` git dependency at `libs-2026.10.0`, and github.com was denied in this session. Nothing was cached, so `--offline` failed too.
- `cargo test --locked` (including `--test openai_embedder_test`): **not run**, same cause. I could not run the new tests at the base or at 8c7e55a6. My claims that they would go red there come from reading the source, as above.
- Scratch probe (verbatim copies of `response_body`, `dechunk`, `checked_vector` and `l2_normalize`, built against `serde_json` from crates.io): run; the results are quoted in K2, F1 and the first optional item.
- **Still to run:**
  - clippy and the tests once the GitHub dependency can be fetched;
  - a live `llama-server --embeddings` behind llama-swap, to confirm the NaN-as-`null` output that triggers F1 and the chunked framing in practice.

## Verdict: **changes required**
