<!-- remind-me/346-r2 run 4, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 198 s, 972253 tokens, $0.95 -->

# Review of Rusty-Mill/rusty-remind-me PR 346: OpenAI-compatible `/v1/embeddings` backend

- **Base:** d5528b1347054f348b3dac96f54619e603f4594c
- **Reviewed head:** 9daba9f88644bffd4c41612121cf5e1eeccda7e5 (`FETCH_HEAD` from `pull/346/head` matches)
- **Commits:** 8c7e55a6 adds the backend; 9daba9f8 fixes K1 and K2.
- **Verdict: changes required.** Both earlier findings are fixed, but three new P2 defects block.

## Earlier findings at this head

- **K1 (P2), fixed.** At `embedder.rs:523-529` an entry now needs an `index` that `as_u64` accepts. A missing, null, string, fractional or negative index is an error. I ran `a_missing_or_malformed_index_is_an_error`: it passes at this head and fails at 8c7e55a6.
- **K2 (blocking), fixed.** `response_body`/`dechunk` (`embedder.rs:340-394`) decode `Transfer-Encoding: chunked`, ignore chunk extensions and trailers, and reject a cut-off body. A non-2xx chunked body still reports its status (probe: `returned 500: boom!`). `a_chunked_response_is_decoded` and `a_truncated_chunked_response_is_an_error` pass at this head and fail at 8c7e55a6. A probe also showed the Ollama path decoding a chunked body correctly, since it shares the same function.

## What the diff touches, and what must hold

- **Vectors and the `embedding_meta` identity.** Every stored vector must come from the embedder that queries use. The new backend reports `{backend:"openai", model, dim}` (`embedder.rs:562-568`). Moving from Ollama to the new backend therefore changes the identity, so the existing mismatch check flags it.
- **Availability cache.** It is keyed on every environment variable `resolve_embedder` reads. The two new variables were added (`embedder.rs:874-875`), so this holds.
- **Wire format.** One request per batch of up to 32 texts. Each returned vector must land in the slot of its input. Index placement plus the count and duplicate checks guarantee that; `slots.flatten()` cannot drop a slot.
- **Not touched:** sync, outbox, hub, tool surface, auth, scrubbing. No row, column or tool schema changes. For each failure class:
  - **Already checked:** wire versus row, concurrency, and who is calling don't apply (no record or wire format changes). Time as text doesn't apply either.
  - **Status that lies:** this is F3.
  - **Boundary skipped:** this is F1; the vector values themselves aren't checked.
  - **Retrieval admission:** only the query prefix changes (F2). Passages stay bare, so stored vectors still match. `EmbedRole::Query` is only used for search queries (`vectors.rs:195`), never for stored text.

## Blocking findings

### F1 (P2): broken embedding values are silently stored as zeros
- **Where:** `crates/remind_me_core/src/embedder.rs:418`, `checked_vector`: `.map(|v| v.as_f64().unwrap_or(0.0) as f32)`
- **What happens (executed):**
  - `{"data":[{"index":0,"embedding":[null,1.0]}]}` returns `Ok([[0.0, 1.0]])`.
  - `["x",1.0]` also returns `Ok([[0.0, 1.0]])`.
  - `[null,null]` returns `Ok([[0.0, 0.0]])`, an all-zero vector.
- **Why it can happen:** llama.cpp's server writes JSON with nlohmann, which turns NaN and Infinity into `null`.
- **Who is hurt:** that memory's vector is stored under the current, valid identity, so the mismatch check never flags it. The memory is mis-ranked or never found by semantic search, and no error says so. Agents miss the fact.
- **Introduced here:** this PR moves the old Ollama loop into a shared helper and routes the new backend through it. The release note (`RELEASE_NOTES.md:8`) promises a malformed response is "an error, not a silent mismatch."
- **Fix:** reject any element that isn't a finite number, with an error naming the entry.
- **Test:** `a_non_numeric_embedding_element_is_an_error` in `openai_embedder_test.rs`, covering `null`, a string, and `[null,null]`.

### F2 (P2): the release note claims Ollama requests are unchanged, but Qwen3 queries now get a prefix
- **Where:** `RELEASE_NOTES.md:11` says "Ollama requests and error messages are otherwise unchanged." But `embedder.rs:113-119` adds a `qwen3-embed` query prefix for every backend, and the release notes don't mention it anywhere.
- **What happens (executed):** with `OllamaEmbedder::new("qwen3-embedding:0.6b", …)`, a Query request now sends `"Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery:q"`.
- **Who is hurt:** an operator on Ollama with Qwen3-Embedding sees ranking change after upgrade with nothing in the notes. Stored vectors are unaffected because passages stay bare. CONTRIBUTING requires every user-visible change to have a release-note entry.
- **Fix:** add a "Changed" bullet for the Qwen3-Embedding query instruction on every backend, and remove the "otherwise unchanged" claim.
- **Test:** add an Ollama-path test in `ollama_embedder_test.rs` asserting the Qwen3 query prefix. The existing Qwen3 test only covers the OpenAI path.

### F3 (P2): with no model set, status gives the wrong reason
- **Where:**
  - `embedder.rs:908-913`: `resolve_embedder` returns `Some` even when `REMIND_ME_OPENAI_EMBED_MODEL` is unset.
  - `embedder.rs:549-552`: `embed` then fails with a precise error naming the variable.
  - `embedder.rs:956-958`: the availability probe throws that error away (`.is_ok()`).
  - `embedder.rs:997-998`: `remind_me_server_status` then reports "configured but not answering (unreachable daemon, or an unconfigured/unloadable ONNX model)".
- **What happens (traced through the source):** set `REMIND_ME_EMBEDDING_BACKEND=openai` without a model. Semantic search silently falls back to keyword-only, and the only diagnostic points at the daemon or ONNX. The variable that is actually missing appears nowhere a client can see.
- **Who is hurt:** an operator or agent fixing the setup chases a server that is fine.
- **Fix:** either make the status reason name `REMIND_ME_OPENAI_EMBED_MODEL` when the backend is `openai` and the model is empty, or return the probe's `EmbedError` text in the reason.
- **Test:** in `status_test.rs`, under the env lock, set the backend to `openai` without a model and assert that `embedding_status()`'s reason contains `REMIND_ME_OPENAI_EMBED_MODEL`.

## Design questions

- **The identity doesn't include the endpoint.** It is `{backend, model, dim}`, as ADR-0002 decided and as Ollama already does. On llama-swap, `model` is a name the server routes on. If the operator points the name at different weights of the same dimension, every stored vector silently stops matching what queries produce, and nothing flags it. Should the URL (or the model ID the server reports in its response) be part of the identity for `openai`?
- **The 60-second read timeout and llama-swap model swaps.** A cold model load on llama-swap can take longer than 60 seconds. When that happens, the probe caches "unavailable" for 30 seconds. Would a longer first-call budget for `openai` be better?

## Optional improvements

- `status.rs:162` still says "set REMIND_ME_EMBEDDING_BACKEND=ollama". It already left out `onnx` before this PR; it could match the new `embedder.rs:987` wording.
- `dechunk` accepts a chunk size like `+7`, because `usize::from_str_radix` allows a leading `+`, while RFC 9112 requires `1*HEXDIG`. It's harmless for a client; checking `is_ascii_hexdigit` would make it strict.
- `ADR-0002:82` ("only `ollama` enables it") was already out of date before this PR. An addendum listing the backends would help.

## Checks and validation gaps

| Check | Result |
| --- | --- |
| `cargo fmt --all -- --check` | **Passed** (exit 0). |
| `cargo clippy --locked …` (six packages) | **Not run.** The git dependency `rusty_mill@libs-2026.10.0` isn't cached, `~/.cargo` is read-only, and `github.com` is denied in this sandbox. Validation gap. |
| `cargo test --locked` (six packages) | **Not run**, same reason. Validation gap. The Ollama, availability-cache and status suites weren't executed, so the PR's claim that they "pass unchanged" is unverified. |
| Probe crate (scratch copy in `$TMPDIR`) | I copied `embedder.rs` lines 1–573 plus `l2_normalize` from this head, unmodified, and built against the cached `serde_json 1.0.154`. All 13 of the PR's `openai_embedder_test.rs` tests **passed**. My 4 probes produced the F1 and F2 traces and the chunked 500 and Ollama chunked results above. |
| PR tests at earlier commits | At 8c7e55a6, 3 tests **fail** (malformed index, chunked, truncated chunked); the other 10 pass. At base, the file doesn't compile because `OpenAiEmbedder` doesn't exist, so every test is red there. |

Still to run:
- The full clippy and test commands with access to GitHub or a vendored `rusty_mill`.
- One live run against `llama-server --embeddings` behind llama-swap, to confirm the real response shape, chunking and the Qwen3 pooling. It would also show whether the server ever writes `null` into an embedding (F1's trigger).

**Verdict: changes required** (F1, F2, F3).
