<!-- remind-me/346-r2 run 3, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 750 s, 1451983 tokens, $1.18 -->

# Review: Rusty-Mill/rusty-remind-me PR #346

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Reviewed head:** `9daba9f88644bffd4c41612121cf5e1eeccda7e5`. `git fetch origin pull/346/head` returned this SHA, so the review is pinned.
- **Commits:** `8c7e55a64` (adds the OpenAI-compatible `/v1/embeddings` backend) and `9daba9f88` (the fixes for K1 and K2).
- **Files changed:** `crates/remind_me_core/src/embedder.rs`, `crates/remind_me_core/tests/openai_embedder_test.rs`, `README.md`, `RELEASE_NOTES.md`.

**Verdict: changes required.** Both earlier findings are fixed, but there are three P2 defects (below).

## Prior findings, checked at this head

- **K1 (P2), a bad index fell back to array position: fixed.** At `embedder.rs:522-528` the index must now be a non-negative integer that fits in `usize`. Anything else gives the error "entry N has no valid index". A repeated or out-of-range index is still refused at `:532-535`. The test `a_missing_or_malformed_index_is_an_error` tries a missing index, `null`, `"0"`, `0.5` and `-1`. It fails at 8c7e55a6 and passes at the head (*executed*).
- **K2 (blocking), chunked responses were not decoded: fixed.** `response_body` (`:340`) detects `Transfer-Encoding: chunked` in any letter case, and `dechunk` (`:377`) decodes the body. I probed the decoder against what RFC 9112 §7.1 lets a server send (*executed*):
  - Chunk extensions are skipped, including ones with whitespace before the `;`.
  - Trailer fields are ignored.
  - Upper- and lower-case hex sizes both work.
  - A body that is cut short, or a chunk not followed by CRLF, is an error.
  - The Ollama backend now decodes chunked replies through the same code.

  `a_chunked_response_is_decoded` and `a_truncated_chunked_response_is_an_error` both fail at 8c7e55a6 and pass at the head.

## Blocking findings

### F1 (P2): non-numeric or overflowing vector elements are stored as 0 or NaN, and the call reports success
- **Where:** `crates/remind_me_core/src/embedder.rs:418`, in `checked_vector`: `.map(|v| v.as_f64().unwrap_or(0.0) as f32)`.
- **Trigger and scenario** (*executed*, scratch probe against the head):
  - **Null elements.** llama.cpp's server writes JSON through nlohmann::json, which turns NaN into `null`. Some models and quantisations do produce NaN embeddings. With `REMIND_ME_EMBEDDING_BACKEND=openai`, a reply of `{"data":[{"index":0,"embedding":[null,0.5]}]}` gives `Ok([[0.0, 1.0]])`. A reply of `[null,null]` gives `Ok([[0.0, 0.0]])`, a zero vector.
  - **Overflow.** `[1e300, 1.0]` gives `Ok([[NaN, 0.0]])`, because the value becomes `inf` when cast to f32 and the L2 normalisation then produces NaN.
  - **Nested arrays.** A server running with `--pooling none` returns arrays of arrays. Each inner array becomes `0.0`, so the call fails only when the token count happens to differ from the configured dimension.
- **Who is hurt:** the made-up vector is written to `vec_chunks` as if it were real. Agents then get semantic results ranked against noise. A NaN score also reaches `vectors.rs:335`, where `partial_cmp(...).unwrap_or(Equal)` sorts with an ordering that is not consistent.
- **Why this PR:** this loose parse already existed for Ollama. This PR moves it into a shared helper, uses it for a new backend, and adds a release note (line 8) saying a malformed response "is an error, not a silent mismatch". That claim is false for the vector contents.
- **Required correction:** refuse any element that is not a finite number after the cast to f32, and refuse a vector whose norm is zero. Return an `EmbedError` that names the backend and the model.
- **Regression tests:** in `openai_embedder_test.rs`, a test that `[null, 0.5]`, `["x", 0.5]`, `[1e300, 1.0]` and `[0.0, 0.0]` are each errors. Add an Ollama twin in `ollama_embedder_test.rs`.

### F2 (P2): the new Qwen3 query prefix also changes Ollama and ONNX requests, but the release notes say Ollama requests are unchanged
- **Where:** `embedder.rs:113-119` adds the `qwen3-embed` row to `ROLE_PREFIXES`. That table is shared by every backend: Ollama at `:435`, OpenAI at `:554` and ONNX at `:618`. The false claim is at `RELEASE_NOTES.md:11`: "Ollama requests and error messages are otherwise unchanged". The Added entry never mentions the prefix.
- **Scenario** (*executed*): with an Ollama model named `qwen3-embedding:0.6b`, a search query now sends `"Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery:quokkas"`. At the base it sent `"quokkas"`. Stored passages stay bare, so existing vectors remain compatible. Query embeddings, and so the ranking existing Ollama and ONNX users get, change without notice.
- **Who is hurt:** an operator who reads the release notes and doesn't expect search results to shift.
- **Required correction:** add a release-note line saying Qwen3-Embedding queries get the model card's instruction prefix on every backend, and drop the "otherwise unchanged" claim. Add `qwen3` to the family list in the `EmbedRole` doc comment (`:91-94`).
- **Regression tests:** an Ollama-path test, next to `embed_sends_the_models_role_prefix_and_the_configured_model_name`, that checks a `qwen3-embedding:*` query carries the instruction and a passage does not. Add a `prefix_for("Qwen3-Embedding-0.6B", …)` case to the unit tests at `embedder.rs:1084ff`. At the moment only the OpenAI path is tested.

### F3 (P2): the docs still say semantic search runs only on Ollama or ONNX
- **Where:** `README.md:18` says "Optional vector search (Ollama or an in-process ONNX bi-encoder)". `embedder.rs:65` says "(Ollama or ONNX) sets this to match". Both are now incomplete. The test name `resolve_embedder_is_none_for_a_backend_that_is_neither_ollama_nor_onnx` (`onnx_embedder_test.rs:70`) is stale too.
- **Who is hurt:** a person evaluating the feature list. Traced from the source; this is documentation that disagrees with the code.
- **Required correction:** add the OpenAI-compatible backend to both places. No test applies.

## Design questions (not findings)

- **The embedder's identity doesn't include the server URL.** `EmbeddingIdentity` (`embedder.rs:174`) is backend + model + dimension, as the reference uses and #96 recorded. For `openai`, the model string is a routing alias: the PR's own doc says it is "the name the server (or llama-swap's config) routes on". Suppose an operator moves `REMIND_ME_OPENAI_EMBED_URL` to another llama-swap host where the same alias maps to a different model of the same dimension. `reconcile_embedding_meta` sees no change, and old and new vectors end up mixed. Whether the endpoint should join the identity for this backend is for the owner to decide.
- **ADR-0002 no longer describes the backends.** It still says "only `ollama` enables it". The ONNX backend made it stale before this PR. Adding a third backend may warrant a note in the ADR or a new ADR.

## Optional improvements

- `response_body` (`:358-362`) decodes the chunked body before it checks the status. A 503 with a truncated chunked body is therefore reported as "malformed HTTP response" without the status code (*executed*). Checking the status first, or putting it in the error, would help diagnosis.
- `usize::from_str_radix` accepts a leading `+`, so a size line of `+2d` is accepted (*executed*). It also tolerates whitespace before the size. Both are lenient, not harmful.

## Failure classes, other than those above

- **Wire versus row, sync, tool surface, caller identity, hidden rows, half a change, time as text:** nothing to check. The diff doesn't touch records, the outbox, the hub, tools or authentication.
- **Concurrency:** the client opens one blocking connection per call with timeouts, and holds no lock.
- **Status that lies:** `config_fingerprint` (`:871-878`) now includes the two new environment variables, so the availability cache picks up configuration changes.
- **Retrieval admission:** batches go out in `EMBED_FORWARD_BATCH` chunks in order. Entries are placed by index and the count is enforced.
- **Containment:** plain HTTP with no key, so no secret is involved. Error bodies are echoed only into `EmbedError`, as before.

## Checks run

| Check | Result |
| --- | --- |
| `cargo fmt --all -- --check` | Passed |
| `cargo clippy --locked -p remind_me_core -p remind_me_mcp -p remind_me_api -p rusty-remind-me -p remind_me_remote -p remind_me_hub --all-targets -- -D warnings` | Passed |
| `cargo test --locked` on the same six packages, `--no-fail-fast`, `HOME` set to a scratch directory | 168 test binaries: 2488 passed, 3 failed |
| `openai_embedder_test` (13 tests) and `ollama_embedder_test` (7 tests) at the head | All pass |
| Head test file run against 8c7e55a6, with a separate target directory | 3 fail: the K1 test and the two chunked tests. At the base d5528b13 the file doesn't compile, because `OpenAiEmbedder` doesn't exist there |
| Scratch probes P1–P7, written in `$TMPDIR` and not in the reviewed tree | Results quoted in F1, F2 and the optional improvements |

Notes on the runs:
- **The three failures** are in `peer_discovery_test` (`UnixListener::bind` returns EPERM). The sandbox blocks binding Unix sockets, and the diff doesn't touch that area.
- **The `remind_me_api` import tests** failed only on the first run, which used the real `HOME`; the sandbox blocks writes there. They pass with `HOME` pointed at scratch.
- **Shared target directory:** my first attempt at the 8c7e55a6 run reused the head's target directory. `git archive` gives files the commit date as their modification time, so cargo didn't rebuild and reported a false pass. The result in the table comes from the rerun with its own target directory.

## Validation gaps

- No real llama-server, llama-swap, vLLM or LM Studio was available. Their actual framing (`Content-Length` or chunked) and their NaN output are inferred from their sources, not observed.
- No live Ollama, so its chunked replies to large batches weren't observed.
- I didn't execute whether NaN scores can make the sort at `vectors.rs:335` panic under Rust's newer sort implementation. A unit test that ranks a NaN-scored vector would settle it.
- The peer-discovery tests need an environment that allows Unix-socket binds.

**Verdict: changes required.** F1, F2 and F3 are all P2.
