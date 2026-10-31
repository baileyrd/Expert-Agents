<!-- remind-me/346-r1 run 3, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 205 s, 962574 tokens, $1.00 -->

# Review of PR 346: OpenAI-compatible `/v1/embeddings` backend

**Verdict: changes required.** The PR adds a third embedding backend. It shares a cleaned-up HTTP client and dimension check with Ollama, and adds a Qwen3 query prefix. The fake-server tests cover the main path, but the index handling and the embedder identity let wrong vectors reach the search index without an error. I could not compile or run anything in the workspace (see Checks).

- **Repository:** Rusty-Mill/rusty-remind-me, PR #346
- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `8c7e55a64821d8f411b8578d76dfa81c4b43b3b8`. The fetched `pull/346/head` matches it. One commit; changes `embedder.rs`, a new test file, README and RELEASE_NOTES.

## What the change has to preserve
- **Stored vectors (`vec_chunks`, `embedding_meta`):** every stored vector must come from the embedder the query uses. The only guard is `EmbeddingIdentity {backend, model, dim}`. When it differs from what is recorded, `Db::from_tables` → `reconcile_embedding_meta` clears all vectors (`db/mod.rs:314-316`, `vectors.rs:548-557`; ADR-0002 "model-change clear stands").
- **Batch output:** the vector at position *i* must belong to input text *i*. Malformed input must be refused, not coerced (the release notes promise exactly this).
- **Availability cache:** keyed on every variable `resolve_embedder` reads (`embedder.rs:820-822`). The PR adds both new variables (`:829-830`). ✔
- **Status:** must say why an embedder is unavailable.
- **Out of scope:** no sync, outbox, tool schema or containment surface is touched. Vectors are local and never replicated. No tool is added, so ADR-0028 and ADR-0030 are not engaged.

## Blocking findings

### 1. P1: a malformed `index` silently swaps vectors between memories
- **Where:** `crates/remind_me_core/src/embedder.rs:480-483`
- **What:** `entry.get("index").and_then(|i| i.as_u64()).map_or(position, …)` falls back to the entry's arrival position whenever `index` is not a u64. That covers a string (`"1"`), a negative number (`-1`) or a float (`1.0`). Only an `index` that is missing entirely should fall back.
- **Trace (executed):** I copied the PR's parsing code verbatim into a scratch crate with serde_json:
  - `data:[{"index":"1",embedding:A},{"index":"0",embedding:B}]` returns `Ok([A, B])`. Text 0 gets text 1's vector and the other way round, with no error.
  - `[{"index":-1,…},{"index":1,…}]` returns `Ok`.
- **Who is hurt:** each memory gets stored under its neighbour's embedding. Semantic search then returns the wrong memory for a query, and every agent acts on that ranking. The release notes say "a repeated index … is an error, not a silent mismatch", which is false for these inputs.
- **Fix:** if `index` is present but not a u64, return an error. Only fall back to position when the key is absent.
- **Regression test:** in `openai_embedder_test.rs`, out-of-order string indexes and a `-1` index should return `Err("bad index")`. It goes red at this head.

### 2. P1: the `openai` identity is a routing alias, so swapping the model never clears old vectors
- **Where:** `embedder.rs:521-527` (identity), `:864-868`; README line 527 ("the model name the server routes on").
- **What:** for this backend, `identity.model` is whatever text the operator put in `REMIND_ME_OPENAI_EMBED_MODEL`. The README itself describes it as the name the server or llama-swap routes on, not the weights. A bare `llama-server --embeddings -m X.gguf` serves its loaded model whatever `model` the request names.
- **Trace (source-traced):**
  1. The operator runs `llama-server -m bge-m3.gguf` with `MODEL=embed` and `DIM=1024`, then indexes memories.
  2. Later they restart it with `Qwen3-Embedding-0.6B.gguf` (also 1024-dim), or point `REMIND_ME_OPENAI_EMBED_URL` at another server. Either way the model name stays `embed`.
  3. `identity()` is still `("openai","embed",1024)`. `embedding_mismatch_info` (`vectors.rs:523`) sees no change, so nothing is cleared.
  4. Every query is now compared with cosine similarity against vectors from a different embedder. Semantic search returns confident nonsense and nothing reports it.
- **Who is hurt:** every agent searching the store.
- **Why this PR:** Ollama has the same URL gap, but there the model string is the pulled tag. This PR adds a backend whose own docs say the model string is an alias, which makes the gap easy to hit.
- **Fix:** include the endpoint in the identity for `openai` (and ideally Ollama), and/or record the model name the server reports. At minimum, the README and the `REMIND_ME_OPENAI_EMBED_MODEL` doc should say the name must change whenever the weights change, followed by a reindex.
- **Regression test:** in `open_reconcile_test.rs`, open with one `openai` URL, store vectors, change the URL and reopen. The vectors should be cleared.

### 3. P2: non-numeric embedding elements become zeros instead of an error
- **Where:** `embedder.rs:376` (`v.as_f64().unwrap_or(0.0)`), now reached from the new backend at `:487`.
- **Trace (executed):** `"embedding":["a",null]` with dim 2 returns `Ok([0.0, 0.0])`. `l2_normalize` leaves it at zero, and it is stored as a vector that never matches anything.
- **Fix:** return an error on any element that is not a number.
- **Regression test:** a non-numeric element should produce `Err`.

### 4. P2: release notes are wrong about Ollama and leave out the Qwen3 prefix
- **Where:** `RELEASE_NOTES.md:11`; prefix at `embedder.rs:113-119`.
- **What:** `prefix_for` matches by lowercase substring. Ollama's `qwen3-embedding` / `qwen3-embedding:0.6b` contains `qwen3-embed`, so Ollama users with that model now send `Instruct: …\nQuery:` on every search. That contradicts "Ollama requests … are unchanged".
- **Impact on stored data:** none. The passage prefix is empty, so stored vectors stay valid. Search ranking does change for those users.
- **Missing from notes:** the prefix is a user-visible change with no entry; CONTRIBUTING requires one. It appears only in the commit message.
- **Fix:** add an entry and correct line 11.

### 5. P2: status text doesn't cover the new failure mode
- **Where:** `embedder.rs:952-954`
- **What:** with `BACKEND=openai` and no model set, `resolve_embedder` still returns `Some` (consistent with the onnx convention). `embedding_status` then reports "configured but not answering (unreachable daemon, or an unconfigured/unloadable ONNX model)". It never names `REMIND_ME_OPENAI_EMBED_MODEL`, the actual cause.
- **Fix:** extend the reason text, or surface the probe's `EmbedError`.

### 6. P2: missing tests for changed behaviour
- **`resolve_embedder` wiring:** no test covers the `"openai"` arm (`:864-868`): the default URL, reading the model variable, and the resulting identity. Deleting the arm leaves every test green. It belongs in an env-locked test, like `onnx_embedder_test.rs:40-48`.
- **Cache invalidation:** no test shows that changing `REMIND_ME_OPENAI_EMBED_URL` or `_MODEL` invalidates the availability cache. It belongs in `embedder_availability_cache_test.rs`.
- **Qwen3 prefix on Ollama:** the prefix is tested only through the new backend. Its effect on Ollama has no test, in `ollama_embedder_test.rs` or anywhere else.
- **Running the new tests at base:** pointless. They name `OpenAiEmbedder`, so they fail to compile there and guard the new type by construction.

## Design questions
- **Status reason in `status.rs:162-163`:** it still says only `=ollama` (and already left out onnx before this PR). Should `server_status` share `embedding_status`'s wording?
- **Missing model:** should a missing `REMIND_ME_OPENAI_EMBED_MODEL` make `resolve_embedder` return `None` (disabled)? Today it returns a backend that is known to fail. Its identity `("openai","",dim)` then clears existing vectors at the next open even though nothing can re-embed them. Ollama and onnx misconfigurations behave the same way, so this is a design question rather than a finding.

## Failure classes ruled out
- **Half a change, concurrency, wire versus row:** the change has no store writes and no shared state beyond the existing fingerprint-keyed cache. Vectors are not replicated.
- **Time as text, containment, caller identity:** no timestamps, secrets or caller identity are involved. Errors include the server's response body (pre-existing behaviour).
- **Retrieval admission:** the new backend uses the same `EMBED_FORWARD_BATCH` chunking and the same timeouts. Admission is unchanged.
- **Ollama refactor:** reading the code, the messages are identical (`"Ollama"` label, the same wording), and the empty-embeddings check is the same through `.filter(!is_empty)`.
- **Count check:** a wrong count is refused (`:470`).

## Checks
| Check | Result |
| --- | --- |
| `cargo fmt --all -- --check` | **pass** (ran) |
| `cargo clippy …` (6 packages) | **not run** |
| `cargo test --locked` (6 packages, including the new `openai_embedder_test`) | **not run** |
| Probe: the PR's `checked_vector` and response parsing copied verbatim into a scratch crate, built offline | **ran**; results quoted in findings 1 and 3 |

**Why clippy and tests didn't run:** the default `~/.cargo` is read-only, and `github.com` is denied by the sandbox (user-denied). That blocks fetching the `rusty_mill` git dependencies at `libs-2026.10.0`. Allowing github.com, or providing a writable `CARGO_HOME` with those dependencies pre-fetched, would settle it.

**Validation gaps:**
- **Real servers:** no llama.cpp, llama-swap, vLLM or LM Studio server was available to check the response shape, whether `index` is always an integer, or whether `Content-Length` is used rather than chunked encoding. `post_json` does not decode chunked bodies. The fix is a live run against each server named in the release notes.
- **Existing suites:** the Ollama, availability-cache and status suites are claimed in the PR to pass unchanged; that claim is unverified.

## Optional improvements
- **`Host` header:** include the port (RFC 9110 §7.2), since proxies may route on it. This is pre-existing code.
- **URL with a path:** a URL like `http://host:8080/llama` fails with "invalid port". A clearer message would help.
- **Docs:** `ARCHITECTURE.md` and the ADR-0002 header could mention that a third backend exists.

## Verdict: **changes required**
