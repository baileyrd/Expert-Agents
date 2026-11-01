<!-- remind-me/346-r2 run 2, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 834 s, 1574494 tokens, $1.43 -->

# Review of PR 346: Rusty-Mill/rusty-remind-me

- **Base:** d5528b1347054f348b3dac96f54619e603f4594c
- **Reviewed head:** 9daba9f88644bffd4c41612121cf5e1eeccda7e5. `git fetch origin pull/346/head` returned this SHA.
- **Commits:** 8c7e55a6 adds the OpenAI-compatible `/v1/embeddings` backend. 9daba9f8 requires valid indexes and decodes chunked responses.

**Verdict: changes required.** Both prior findings are fixed, but there are three new P2 defects.

## Prior findings at this head

- **K1 (P2), malformed index falls back to array position: fixed.** `embedder.rs:522-528` now refuses an index that is missing, null, a string, fractional or negative. The test `a_missing_or_malformed_index_is_an_error` fails at 8c7e55a6 and passes at head (executed).
- **K2 (blocking), chunked responses not decoded: fixed.** `response_body`/`dechunk` (`embedder.rs:340-395`) de-chunk the body before the status check. Matching on the header name and on `chunked` ignores case, chunk extensions are skipped, trailers are ignored, and a body that is cut short is an error. Two tests cover it:
  - `a_chunked_response_is_decoded` fails at 8c7e55a6 and passes at head (executed).
  - `a_truncated_chunked_response_is_an_error` fails at 8c7e55a6 and passes at head (executed).
  - An oversized hex size is safe: `body.get(..size)` returns `None` before `size + 2` is computed.

## Blocking findings

### F1 (P2): Malformed or non-finite embedding values are stored as success
- **Where:** `crates/remind_me_core/src/embedder.rs:418`, inside `checked_vector`, which the new backend now uses.
- **Problem:** `v.as_f64().unwrap_or(0.0) as f32` turns `null` or a string into `0.0`. A value beyond the f32 range becomes `inf`, and `l2_normalize` (`:820`) then turns that into `NaN`.
- **Trigger (executed probe at head):** llama.cpp's server writes its JSON with nlohmann::json, which turns NaN into `null`. Some quantised or misconfigured models emit NaN embeddings. What the probe returned:
  - `[null,null,null]` → `Ok([[0.0, 0.0, 0.0]])`
  - `[1.0,null,"x"]` → `Ok([[1.0, 0.0, 0.0]])`
  - `[1e39, 1.0]` → `Ok([[NaN, 0.0]])`
- **Who is hurt:** an agent searching memory. The vector is written as if valid, so that memory silently drops out of semantic search. Reindex won't repair it, because it "only generates missing ones".
- **Why it's this PR's defect:** the code predates the PR in the Ollama path. This PR makes it reachable from the new backend, and its release note (`RELEASE_NOTES.md:8`) claims malformed responses are "an error, not a silent mismatch".
- **Fix:** reject any element that isn't a number, or isn't finite after the `f32` cast. Reject a zero-norm vector.
- **Regression test:** in `openai_embedder_test.rs`, a response with a `null` element, a string element and `1e39` must each return `Err`. All three return `Ok` today.

### F2 (P2): Release note is false; the Qwen3 query prefix also changes Ollama requests
- **Where:** `RELEASE_NOTES.md:11` and `embedder.rs:114-119`.
- **Problem:** `ROLE_PREFIXES` gains a `qwen3-embed` entry, and `prefix_for` is shared by all backends. The note says "Ollama requests and error messages are otherwise unchanged", and the prefix is not mentioned in any release note.
- **Trace (executed probe at head):** `OllamaEmbedder::new("qwen3-embedding:0.6b", …)` with `EmbedRole::Query` sent `"Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery:quokkas"`. At base there is no such entry, so no prefix was sent (source-traced).
- **Who is hurt:** an operator running `qwen3-embedding` through Ollama. Their query embeddings change with no notice. Stored passage vectors are unaffected, since passages still go in bare.
- **Fix:** add a release-note entry for the Qwen3 instruction on every backend, and correct the "otherwise unchanged" sentence.
- **Regression test:** an Ollama-side twin of `qwen3_embedding_queries_carry_its_instruction_and_passages_go_bare` in `ollama_embedder_test.rs`.

### F3 (P2): A missing model under `openai` is reported as an unreachable daemon
- **Where:** `embedder.rs:551`, `:956-958` and `:997`.
- **Trace (source-traced):** with `REMIND_ME_EMBEDDING_BACKEND=openai` and no `REMIND_ME_OPENAI_EMBED_MODEL`:
  1. `resolve_embedder` returns `Some`.
  2. The availability probe fails with "needs a model: set REMIND_ME_OPENAI_EMBED_MODEL".
  3. `.is_ok()` discards that message.
  4. `remind_me_server_status` reports "configured but not answering (unreachable daemon, or an unconfigured/unloadable ONNX model)".
- **Who is hurt:** a person configuring the backend, who is pointed at the network instead of the missing variable.
- **Fix:** check the model when resolving, or carry the probe's error into the status reason, or at least name the missing `openai` model in the reason text.
- **Regression test:** in `status_test.rs`, under the env lock, set backend `openai` with no model and assert the `embedding_status()` reason contains `REMIND_ME_OPENAI_EMBED_MODEL`.

## Design question
- **Embedding identity leaves out the endpoint.** ADR-0002 settles identity as backend, model and dimension. With llama-swap, though, the model is an arbitrary route alias. If an operator points the same alias at a different model of the same dimension, or moves `REMIND_ME_OPENAI_EMBED_URL` to another server with the same alias, no mismatch is detected. Old and new vectors are then compared against each other. Whether the URL, or the model the server reports in its response, belongs in the identity for this backend is a question for the owner.

## Optional improvements
- `dechunk` (`:382`) accepts a size of `+7`, because `from_str_radix` allows a leading `+`. RFC 9112 requires `1*HEXDIG` (probe at head: `Ok`). This is harmless here because the request sends `Connection: close`.
- An interim `HTTP/1.1 100 Continue` response is misread as a final status of 100 (probe at head: `Err("…returned 100: HTTP/1.1 200 OK…")`). It surfaces as an error, not a silent failure, and the behaviour predates this PR. RFC 9110 §15.2 says a client must be able to parse a 1xx it didn't ask for.
- Send `Accept-Encoding: identity`. With no such header, a proxy in front of vLLM or LM Studio may gzip the response; that would fail as "not JSON".
- Add a test that decodes a chunked Ollama response. Go's `net/http`, which Ollama uses, chunks real-size embedding replies, so this is the case the "Changed" note fixes.
- `status.rs:162`'s static reason still says only `=ollama`. That was already stale before this PR; it now also omits `openai`.
- The `Host` header leaves out a non-default port (RFC 9110 §7.2). This also predates the PR.

## Other failure classes, ruled out
- **Sync, outbox, hub, tombstones, time-as-text, project claims:** no record, table or wire format is touched. The only new stored value is `backend="openai"`, written to the local `embedding_meta` table (`vectors.rs:492`).
- **MCP, REST and CLI tool surface:** no tool is touched, so ADR-0028 and ADR-0030 don't apply.
- **Concurrency:** the only shared state is the availability cache, whose fingerprint includes both new environment variables (`:874-875`).
- **Containment:** the new backend uses the same plain-HTTP, local-network posture as Ollama and stores no new secret.
- **Retrieval admission:** a count mismatch, a repeated or out-of-range index, or a wrong dimension is refused, and the index tests cover each case.

## Checks
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | pass |
| `cargo clippy --locked -p <six packages> --all-targets -- -D warnings` | pass (`--offline`, `CARGO_TARGET_DIR` in `$TMPDIR` because the configured target dir is read-only) |
| `cargo test --locked -p remind_me_core --test openai_embedder_test` at head | 13/13 pass |
| Same test file at 8c7e55a6 | 3 fail (K1 and both K2 tests), as intended |
| Same test file at base | does not compile (`OpenAiEmbedder` is absent), so it guards the whole addition |
| `cargo test --locked --no-fail-fast` over the six packages | 2380 pass, 111 fail |
| Probe tests (scratch copy of head) | 7 run; results cited above |

**The 111 failures are environmental:** 108 are `ReadOnlyFilesystem`, from tests that write under `$HOME`, and 3 are `EPERM` in `peer_discovery_test`. They are all in importer, watcher, archive, export and peer-discovery tests, which this diff doesn't touch. Every embedder, status, availability-cache, vectors and reconcile binary passed.

## Validation gaps
- The 111 environment-blocked tests need a run with a writable `$HOME` and network permission.
- No real llama.cpp, llama-swap, vLLM or LM Studio server was available. That run would confirm `null` for NaN, the chunked framing they actually send, and whether any of them compress responses.
- Feature-gated `local-embed` was not built; this diff doesn't change the ONNX path.
