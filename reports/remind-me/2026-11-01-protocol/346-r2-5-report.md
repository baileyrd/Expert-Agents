<!-- remind-me/346-r2 run 5, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 42 s, 235592 tokens, $1.45 -->

# Review: Rusty-Mill/rusty-remind-me PR 346

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `9daba9f88644bffd4c41612121cf5e1eeccda7e5`. `git fetch origin pull/346/head` gives this SHA.
- **Commits:** 8c7e55a6 adds the OpenAI-compatible backend; 9daba9f8 adds the fixes for K1 and K2.
- **Verdict: changes required.** The two prior findings are fixed. There is one new P1: a client configured with `REMIND_ME_EMBEDDING_BACKEND=openai` but no model deletes every stored vector when it opens the store, even though that client cannot embed anything. There are also four P2s.

## Prior findings

- **K1 (P2), an index that is not a whole number falls back to the entry's position: fixed.** `embedder.rs:524-531` now requires `as_u64`; anything else is an error naming the entry. I ran the head's test file against the code at 8c7e55a6, in its own target directory. `a_missing_or_malformed_index_is_an_error` failed there and passes at head.
- **K2 (blocking), chunked responses are not decoded: fixed.** `response_body` and `dechunk` (`embedder.rs:340-397`) match the header name case-insensitively, ignore chunk extensions and trailers, and return an error for a truncated body. `a_chunked_response_is_decoded` and `a_truncated_chunked_response_is_an_error` failed at 8c7e55a6 and pass at head. A chunked 404 reply is also decoded correctly (I probed it).

## Blocking findings

### F1 (P1): an openai client with no model wipes the store's vectors
**Where:** `crates/remind_me_core/src/embedder.rs:909-914`, through `db/mod.rs:314-316` and `vectors.rs:548-556`.

**Trigger:**
1. Node A embeds with `backend=openai` and model `qwen3-embedding-0.6b`. `embedding_meta` records that identity.
2. A second client opens the same database with `REMIND_ME_EMBEDDING_BACKEND=openai` but without `REMIND_ME_OPENAI_EMBED_MODEL`. Examples: a CLI shell, or an MCP client config that copied only the backend and dimension.
3. `resolve_embedder` still returns an `OpenAiEmbedder` with model `""`. Its identity `("openai", "", dim)` does not match the stored one, so `reconcile_embedding_meta` clears every vector.
4. That embedder refuses to embed at all (`embedder.rs:549-553`), so nothing replaces the vectors.

**Executed** in a scratch copy:
- Chunk count was 1 after the first store and 1 after reopening with the same config.
- After reopening with the model unset, it was 0.

**Who is hurt:** every agent that shares the store. Semantic search quietly falls back to keyword-only until someone runs a reindex with the correct config, and nothing reports the loss.

**Why it is a defect, not a settled decision:** ADR-0002's addendum skips this check when no backend is configured, because "nothing was written by this process … nothing meaningful to compare against". The same reasoning covers a backend whose required model is missing.

**Fix:** treat an empty model as "not configured". Have `resolve_embedder` return `None` for `openai` with no model, or skip the reconcile in that case.

**Regression test:** store vectors under an openai identity, unset the model under the `test_env` lock, reopen, and assert the chunk count is unchanged. Put it in its own test binary.

### F2 (P2): the release note says Ollama requests are unchanged, but some are not
**Where:** `RELEASE_NOTES.md:12` and `embedder.rs:113-119`.
- The PR adds a `qwen3-embed` entry to the instruction-prefix table, which is shared with Ollama.
- **Executed:** an `OllamaEmbedder` with model `qwen3-embedding:0.6b` now sends `"Instruct: Given a search query…\nQuery:quokkas"` for queries.
- That contradicts the note's "Ollama requests and error messages are otherwise unchanged", and the prefix change has no release-note entry of its own.
- **Who is hurt:** an operator whose search results shift with no explanation.
- **Fix:** add a Changed entry for the prefix and correct the sentence. `qwen3_embedding_queries_carry_its_instruction_and_passages_go_bare` covers only the openai path; add the same case to `ollama_embedder_test.rs`.

### F3 (P2): the "not answering" status blames the wrong thing when the model is missing
**Where:** `embedder.rs:993-1001`.
- **Executed:** with `backend=openai` and no model, the status reads "configured but not answering (unreachable daemon, or an unconfigured/unloadable ONNX model)".
- Neither cause applies, and the embedder's own error ("set REMIND_ME_OPENAI_EMBED_MODEL") is thrown away.
- **Fix:** name a missing openai model in the status reason, or report the probe's actual error.
- **Regression test:** one that asserts the reason names `REMIND_ME_OPENAI_EMBED_MODEL`.

### F4 (P2): non-numeric vector components are stored as 0.0
**Where:** `embedder.rs:419` (`checked_vector`, `v.as_f64().unwrap_or(0.0)`).
- This came from the Ollama code and now also applies to the new backend.
- **Executed:** the response `"embedding":[null,1.0,"x"]` is accepted as `[0.0, 1.0, 0.0]`.
- llama.cpp-based servers may write NaN components as `null`; I could not confirm this against a real server (see validation gaps).
- The release note promises that bad responses are "an error, not a silent mismatch".
- **Fix:** return an error for any component that is not a number.
- **Regression test:** one with a `null` component that expects an error.

### F5 (P2): docs still list only two backends
- `README.md:18` still says "Ollama or an in-process ONNX bi-encoder".
- The comment at `embedder.rs:65` still says "(Ollama or ONNX)".
- (`status.rs:162` naming only `ollama` was already wrong before this PR.)

## Design questions
- The embedding identity is the backend/model/dimension triple settled by ADR-0002. It leaves out the endpoint, so two openai servers serving different weights under the same model name are treated as the same embedder. The persona's retrieval rule includes the endpoint; whether the identity should is the owner's call.

## Optional improvements
- `dechunk` accepts a `+`-prefixed chunk size such as `+2c`, because `from_str_radix` allows a leading `+` (executed). Its impact is nil.
- A 1xx interim response (for example 103) is rejected as "returned 103" (executed). RFC 9110 §15.2 says clients must tolerate these. The rejection is explicit rather than silent, and this behaviour existed before the PR.
- The `Host` header leaves out a port that is not the default. This also existed before the PR.

## Failure classes
| Class | Result |
|---|---|
| Hidden rows surface | F1 |
| Status that lies | F3 |
| Silent success / validation at entry | F4 |
| Protocol subset | Chunk framing, header case, extensions, trailers, truncation and chunked error bodies all checked. A `Transfer-Encoding` list such as `gzip, chunked` is refused rather than misread. 1xx handling is listed under optional improvements. |
| Retrieval admission | Entries are now placed by index, and wrong counts, repeated indexes and dimension mismatches are rejected. |
| Wire versus row | Ruled out: vectors and `embedding_meta` never reach the sync or hub code. |
| Tool surface | Ruled out: no tools or schemas change. |
| Half a change, time as text, concurrency, who is calling | Ruled out: the diff adds no transactions, timestamps or new shared state, and the availability-cache fingerprint includes both new variables. |

## Checks
- **`cargo fmt --all -- --check`:** passes.
- **`cargo clippy --locked … --all-targets -- -D warnings`** (the six packages): passes.
- **`openai_embedder_test` and `ollama_embedder_test`:** 13 and 7 tests, all pass at head.
- **K1/K2 tests at 8c7e55a6:** the three new ones fail there and pass at head. The rest of the file covers behaviour from the first commit, so it cannot be compiled at the base, where `OpenAiEmbedder` does not exist.
- **`cargo test --locked --no-fail-fast`** (the six packages): 2380 passed, 111 failed.
  - All 111 failures are sandbox filesystem errors: 108 `ReadOnlyFilesystem` and 3 `PermissionDenied`. These tests write scratch directories under the read-only `$HOME` (importer, archive, watcher, dbs-import, export, peer-discovery, and `remind_me_api` `import_export_test`).
  - The embedder, availability-cache, ONNX, status and vectors suites all pass.
- **Probes:** in a scratch copy only. The reviewed tree is unchanged.

## Validation gaps
- **The 111 tests above:** not validated here. Rerun them where `$HOME` is writable.
- **Real servers:** I tested no real llama-server, llama-swap, vLLM or LM Studio. Two things are unconfirmed: whether they send chunked bodies, and whether they write NaN components as `null` (relevant to F4).
- **Feature-gated code:** `local-embed` was not built. The diff does not touch it.

**Verdict: changes required.** K1 and K2 are fixed. F1 (P1) and F2–F5 (P2) block.
