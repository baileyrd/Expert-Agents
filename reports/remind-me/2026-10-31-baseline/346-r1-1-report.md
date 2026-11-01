<!-- remind-me/346-r1 run 1, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 191 s, 814122 tokens, $0.91 -->

# Review of PR 346 (Rusty-Mill/rusty-remind-me): add an OpenAI-compatible `/v1/embeddings` backend

**Verdict: changes required.** I found five defects (one P1, four P2) and no P0. I could not compile anything because the network to github.com was refused, so every finding below comes from reading the code, not from running it.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `8c7e55a64821d8f411b8578d76dfa81c4b43b3b8`. A fresh fetch of `pull/346/head` matches it.
- **Diff:** one commit touching `crates/remind_me_core/src/embedder.rs`, a new `tests/openai_embedder_test.rs`, `README.md` and `RELEASE_NOTES.md`.

## What the change touches, and the rule each part must keep

- **The embedder's identity (`embedding_meta`: backend, model, dimension; ADR-0002 addendum).** If the identity recorded for stored vectors differs from the configured embedder's identity, every vector is cleared. So the identity must belong to an embedder that can actually produce vectors.
- **The vectors themselves.** Each one is exactly the server's output for its own input: placed by `index`, the right length and finite. Malformed responses are refused when they arrive.
- **Role prefixes (`ROLE_PREFIXES`).** These are shared by Ollama, OpenAI and ONNX. Any change alters query or passage vectors for every backend, so the release notes must say so.
- **Availability fingerprint and status.** The cache key must cover every variable `resolve_embedder` reads, and the status must name the real reason a backend is unavailable.
- **Sync, tools, hub.** Nothing changed. Vectors are node-local and don't replicate.

## Blocking findings

### 1. [P1] A missing `REMIND_ME_OPENAI_EMBED_MODEL` wipes every stored vector when the database opens (source-traced)

- **Where:** `embedder.rs:864-869` and `:504`, reached through `db/mod.rs:314-316` and `vectors.rs` `reconcile_embedding_meta`.
- **Trigger:** a node has vectors recorded as `{openai, "Qwen3-Embedding-0.6B", 1024}`. A second process (a CLI call, a hook, or another MCP client whose env lacks the model variable) starts with `REMIND_ME_EMBEDDING_BACKEND=openai` and `REMIND_ME_EMBEDDING_DIM=1024` but no `REMIND_ME_OPENAI_EMBED_MODEL`.
- **What happens:**
  1. `resolve_embedder` still returns `Some(OpenAiEmbedder{model:""})`.
  2. `Db::from_tables` calls `reconcile_embedding_meta` with `{openai, "", 1024}`.
  3. That doesn't match what is stored, so `Vectors::clear()` deletes every vector.
- **Why it's wrong:** the PR itself treats an empty model as invalid. `embed` refuses it, and the docs call the variable "Required". Yet that same invalid config is allowed to act as a model change and destroy the index.
- **Who is hurt:** every agent on that node. Semantic search silently drops to keyword-only until someone runs a full re-embed. The stored meta still names the old model, so restoring the correct config does not bring the vectors back.
- **Note:** ONNX already has the same pattern when its path is empty. This PR copies it to a backend where it is easy to trigger.
- **Fix:** have `resolve_embedder` return `None` (unconfigured) when the openai model is empty, or skip the reconcile when the resolved embedder cannot embed.
- **Regression test:** under the env lock, set backend `openai` and dimension `2` with the model unset, record meta `{openai,"m",2}`, store one vector, open the database, and assert the vector survives. It goes red at head.

### 2. [P2] Response entries with a malformed `index` fall back to arrival order (source-traced)

- **Where:** `embedder.rs:480-483`, `.and_then(|i| i.as_u64()).map_or(position, …)`.
- **Trigger:** a server returns `[{index:"1",…},{index:"0",…}]`, or uses negative or float indexes.
- **What happens:** both entries fall back to their position. The batch is accepted with memory A holding B's vector, and agents get the wrong memories back for semantic queries.
- **Why it's wrong:** the release note promises that entries "are placed by their `index`" and that a bad index is an error.
- **Fix:** accept only a missing `index` (fall back to position, and only if none of the entries has one). Refuse an `index` that is present but not a `u64`.
- **Regression test:** extend `a_repeated_or_out_of_range_index_is_an_error` with string and negative `index` cases.

### 3. [P2] Non-numeric embedding components quietly become `0.0` (source-traced)

- **Where:** `embedder.rs:376` (`checked_vector`), `v.as_f64().unwrap_or(0.0)`.
- **Trigger:** llama.cpp's server writes JSON with nlohmann, which emits NaN as `null`. A model or GPU that produces NaN embeddings therefore sends `null` components.
- **What happens:** each `null` becomes `0.0`. An all-NaN vector becomes all zeros, `l2_normalize` leaves it zero, and it is stored and marked current. That memory can never match semantically, and nothing is reported.
- **Why it's wrong:** the helper's own doc says "never a silent truncation or pad". The code predates this PR on the Ollama path, but the PR moved it into the shared helper and is the first to route llama.cpp output through it.
- **Fix:** refuse any component that is not a finite number, and refuse a zero-norm vector.
- **Regression test:** the fake server returns `[null, 1.0]`, and `embed` must return an error.

### 4. [P2] The release note says Ollama requests are unchanged, but the new Qwen3 prefix changes them; the prefix is also undocumented and untested for Ollama and ONNX (source-traced)

- **Where:** `embedder.rs:113-119`, and `RELEASE_NOTES.md:11`.
- **What happens:** the new `qwen3-embed` entry in `ROLE_PREFIXES` is matched against Ollama model names and ONNX paths too. Ollama's `qwen3-embedding:0.6b` contains `qwen3-embed`, so after upgrading every query on that model carries the `Instruct: …\nQuery:` prefix. Stored passages are unaffected. ONNX gets the same prefix for any path containing `qwen3-embed`.
- **Who is hurt:** an operator comparing retrieval before and after the upgrade has nothing in the notes to explain the shift.
- **Fix:**
  - Add a release-note entry for the Qwen3 query prefix that names all three backends.
  - Correct the "unchanged" claim.
  - Add `qwen3-embed` to the unit test `role_prefixes_match_known_model_families_by_substring`.

### 5. [P2] The status message names the wrong cause, and the resolver and fingerprint changes have no tests (source-traced)

- **Status (`embedder.rs:949-955`):** with the model unset, the probe fails on the missing model. The status then reports "unreachable daemon, or an unconfigured/unloadable ONNX model" and drops the real cause, `REMIND_ME_OPENAI_EMBED_MODEL`. The operator is sent to look at the network.
  - **Fix:** name the missing variable, or carry the probe's error into the status reason.
  - **Regression test:** `embedding_status()` with backend `openai` and no model must mention `REMIND_ME_OPENAI_EMBED_MODEL`.
- **Untested `"openai"` branch:** no test calls `resolve_embedder` for `openai`, so the URL default and the model variable are never checked.
- **Untested fingerprint (`config_fingerprint`, `:829-830`):** nothing goes red if the two new variables are dropped from it. That is exactly the bug class the function's own comment warns about.
  - **Where the test belongs:** in `embedder_availability_cache_test.rs`, under the env lock. Changing `REMIND_ME_OPENAI_EMBED_URL` from a dead port to a live fake server must make the backend available immediately.

## Design questions (for the owner, not findings)

- **Identity leaves out the endpoint.** ADR-0002 settles identity as backend, model and dimension. With llama-swap, though, the model is a local alias. Pointing `REMIND_ME_OPENAI_EMBED_URL` at a different server that serves different weights under the same alias and dimension mixes vector spaces with no warning. Should the `openai` identity include the URL?
- **One oversized chunk fails a whole batch.** llama-server rejects any input longer than `n_ubatch` (512 tokens by default) instead of truncating like Ollama. A 1,600-character chunk of dense code or CJK text could exceed that and fail all 32 texts in its batch on every reindex. Should one input's failure stay isolated, or should operators be told to raise `-ub`?

## Optional improvements

- `status.rs:162` (unchanged by this PR) still tells users to set `REMIND_ME_EMBEDDING_BACKEND=ollama` and lists neither `onnx` nor `openai`.
- vLLM and LM Studio are named in the docs, but deployments that require an API key will return 401. The README should say so.

## Checks

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` (head exported to scratch) | Ran: **passed** |
| `cargo clippy …`, `cargo test --locked` (six packages) | **Not run.** The `rusty_mill` git dependency isn't cached, `~/.cargo` is read-only, and fetching from `github.com` was denied. |
| New tests run against the base SHA | **Not run.** At base, `OpenAiEmbedder` doesn't exist, so the new test file can't compile there; it does guard the new type. Nothing guards the Ollama/ONNX prefix change. |

## Validation gaps

- **Build, clippy and the test suite:** run the clippy and test commands above on a machine with the `libs-2026.10.0` checkout.
- **Real servers:** run against a real `llama-server --embeddings`, llama-swap, vLLM and LM Studio. That would confirm:
  - whether any of them answers with `Transfer-Encoding: chunked`, which the hand-rolled reader doesn't decode;
  - what NaN output looks like on the wire;
  - how llama-server behaves when an input exceeds `n_ubatch`.
