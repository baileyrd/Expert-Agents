<!-- remind-me/346-r1 run 4, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 836 s, 1792188 tokens, $1.41 -->

# Review of Rusty-Mill/rusty-remind-me PR #346: a new `openai` embedding backend (`/v1/embeddings`)

**Verdict: changes required.** One P1 and three P2 findings below. I reviewed and ran checks only; I changed nothing in the reviewed tree.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `8c7e55a64821d8f411b8578d76dfa81c4b43b3b8`. The fetched `pull/346/head` matches it. The PR is one commit, `8c7e55a64`.
- **Files changed:** `crates/remind_me_core/src/embedder.rs`, the new `crates/remind_me_core/tests/openai_embedder_test.rs`, `README.md` and `RELEASE_NOTES.md`.

## What the diff touches, and what must hold

- **The embedder resolver and the stored identity of vectors (`embedding_meta`).** Vectors are cleared only when the configured backend, model or dimension really differs from the one that wrote them. A configuration the code itself treats as an error must not count as a model change (ADR-0002 §model versioning, `vectors.rs:531-547`).
- **The new `/v1/embeddings` client.** Each vector must be attached to its own input text, and anything the response doesn't say clearly must be refused.
- **The query prefixes in `ROLE_PREFIXES`.** These apply to every HTTP backend, so the release notes must describe the change correctly.
- **The availability cache key (`config_fingerprint`).** It must change whenever the configuration it judged changes.
- **Out of scope here:** no tools, no sync or outbox records, no hub storage and no protocol formats change. Vectors and `embedding_meta` stay on the local node. So the sync, tool-contract and containment failure classes have nothing to act on in this diff.

## Blocking findings

### 1. P1: if `REMIND_ME_OPENAI_EMBED_MODEL` is unset, opening the store deletes every stored vector

- **Where:** `crates/remind_me_core/src/embedder.rs:864-869`, and the call in `crates/remind_me_core/src/db/mod.rs:315-317`.
- **Trigger:** any process that opens the database with `REMIND_ME_EMBEDDING_BACKEND=openai` but without the model variable. For example: a second MCP client config, a CLI shell, or a service unit missing that one variable.
- **Failure (executed):**
  - When the model variable is unset, the resolver still returns an embedder, with an empty model name (`unwrap_or_default()`).
  - Every open compares that embedder's identity with what is stored. `{openai, "", dim}` doesn't match the stored `{openai, "qwen3-embedding-0.6b", dim}`, so `Vectors::clear()` runs.
  - I probed this in a scratch copy, modelled on `open_reconcile_test`: same config → `vectors=1`; model unset → `vectors=0`.
- **Who is hurt:** every agent searching that store.
  - Semantic search quietly falls back to keyword-only until someone runs a full reindex.
  - After the operator restores the variable, status reports the embedder as `Active` even though no vectors remain. The stored identity still matches, so nothing flags the gap.
  - While the model is missing, the status reason reads "unreachable daemon, or an unconfigured/unloadable ONNX model" (`embedder.rs:952`), which hides the real cause.
- **Why this is a defect:** the PR itself calls the model "Required; there is no sensible default". Its test `a_missing_model_is_an_error_before_any_request` covers `embed()` only, not the open path.
- **Correction:**
  - Treat an empty model as "not usable": either return `None` from `resolve_embedder`, or skip the open-time reconcile when the model is empty.
  - Give the missing variable its own status reason.
- **Regression test:** in `open_reconcile_test.rs`, under its `ENV_LOCK`: plant an `openai` vector, reopen with `OPENAI_EMBED_MODEL_ENV` removed, and assert the count is still 1.

### 2. P2: a malformed `index` is quietly replaced by arrival order, though the release note says index problems are errors

- **Where:** `crates/remind_me_core/src/embedder.rs:480-483`, and `RELEASE_NOTES.md:7`.
- **Trigger:** a server that returns `index` as something other than a non-negative integer.
- **Failure (executed):** a response with `{"index":"1"}` and `{"index":-1}` returned `Ok(2)`. When `as_u64()` fails, `map_or(position, …)` falls back to the order the entries arrived in.
  - The release note says "Response entries are placed by their `index`; … a bad index is an error".
  - If such a server also reorders its entries, each vector is stored against the wrong text. Agents then get the wrong memory back for a query.
- **Correction:** fall back to position only when `index` is absent. Any `index` that is present but not a `u64` is an error.
- **Regression test:** in `openai_embedder_test.rs`, a string index and a negative index each produce an error containing "bad index".

### 3. P2: the release notes are wrong about Ollama and leave out the Qwen3 prefix

- **Where:** `crates/remind_me_core/src/embedder.rs:113-119`, `RELEASE_NOTES.md:11`, and `README.md:18`.
- **Trigger:** an Ollama user whose model name contains `qwen3-embed` (for example `qwen3-embedding:0.6b`).
- **Failure (source-traced):**
  - The new `qwen3-embed` entry in `ROLE_PREFIXES` is matched by substring against the lowercased model name, for every backend (`prefix_for`, `embedder.rs:122-133`).
  - For such an Ollama user, every query embedding now carries the `Instruct: …\nQuery:` prefix. That contradicts "Ollama requests and error messages are unchanged".
  - The "Added" section never mentions the prefix, so a person comparing search results before and after the upgrade has nothing to explain the change.
  - `README.md:18` still says vector search uses "Ollama or an in-process ONNX bi-encoder" and doesn't mention the new backend.
- **Correction:**
  - Add a release-note line saying the Qwen3-Embedding query instruction now applies to the Ollama and OpenAI-compatible backends, and remove the "unchanged" claim.
  - Update `README.md:18`.
  - Add a `prefix_for` unit test for a Qwen3 model name used through Ollama.

### 4. P2: no test checks that the `openai` setting selects the new backend, or that its settings are part of the cache key

- **Where:** `crates/remind_me_core/src/embedder.rs:829-830` and `864-869`.
- **Failure (executed by mutation):** in a scratch copy I deleted the `"openai"` arm of `resolve_embedder` and both `OPENAI_*` entries from `config_fingerprint`. The `remind_me_core` and `remind_me_mcp` test runs still produced no failure beyond the sandbox ones: all 105 were read-only-filesystem or permission errors.
  - Every new test builds `OpenAiEmbedder` directly, so the configuration path that real users take is never tested.
  - If those fingerprint entries are dropped, a failed check against one model is reused for another for up to 30 seconds, and status reports a corrected configuration as broken.
- **Correction:**
  - Add an env-locked test that `REMIND_ME_EMBEDDING_BACKEND=openai` gives identity backend `openai` and the configured model. It fits beside `resolve_embedder_is_none_for_a_backend_that_is_neither_ollama_nor_onnx` in `onnx_embedder_test.rs`, or in its own binary.
  - Add an `openai` variant of `a_failed_probe_does_not_answer_for_a_different_backend_address` in `embedder_availability_cache_test.rs`.

## Design questions for the owner

- **Identity doesn't include the endpoint.** Stored vectors are identified by backend, model and dimension, as ADR-0002 settles. With llama-swap, the model name is an alias in its config. If the alias is pointed at a different model file of the same dimension, or the URL is pointed at another server, old and new vectors mix and nothing detects it. Ollama tags already have the same exposure, but aliases make it more likely here.
- **Same model, new backend, all vectors cleared.** Moving from Ollama to llama-swap while serving the same model clears every vector, because `backend` is part of the identity. That is consistent with the ADR, but worth a sentence in the release note so operators expect the reindex.

## Optional improvements

- **Chunked responses aren't parsed.** The shared `post_json` sends an HTTP/1.1 request but can't read a chunked response. I sent one from a fake server and got the error "response was not JSON: trailing characters". RFC 9112 §7.1 requires a recipient to be able to parse chunked transfer coding. The Ollama path already behaved this way; the change makes it fail loudly rather than silently. Decoding chunked bodies would remove the risk for every server behind the shared client.
- **Missing numbers become 0.0.** `checked_vector` (`embedder.rs:376`) turns non-numeric components into `0.0`; my probe showed `[null, 1.0]` becoming `[0.0, 1.0]`. This is existing Ollama behaviour, now shared with the new backend. Refusing such vectors would be stricter.
- **`Host` header has no port.** The header is sent without the port, which RFC 9110 §7.2 expects for a non-default port. This was already the case before the PR.

## Checks and results

| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | passed (exit 0) |
| `cargo clippy --locked -p` (all six packages) `--all-targets -- -D warnings` | passed (exit 0) |
| `cargo test --locked --no-fail-fast`, all six packages | exit 101. All 71 failures are sandbox errors: 68 read-only home directory, 3 `PermissionDenied` in `peer_discovery_test`. They are in importer, watcher, export, archive and peer-discovery tests, none of which this PR touches. `openai_embedder_test` (10), `ollama_embedder_test` (7), `onnx_embedder_test` (5) and `open_reconcile_test` (8) all passed. |
| Scratch probes (outside the reviewed tree) | Vectors wiped on open with the model unset; malformed `index` accepted; `null` component turned into 0.0; chunked response refused. |
| Mutation run in scratch | Deleting the `openai` arm and its fingerprint entries caused no new failures. |
| New tests at the base commit | Not run. They cannot compile there, because `OpenAiEmbedder` and `OPENAI_EMBED_MODEL_ENV` don't exist at the base, so they go red there by construction. |

Cargo also printed two `invalid character '{' in package name: '{{plugin-name}}'` errors from an untracked template directory in the checkout. They didn't stop any run.

## Validation gaps

- **No real servers.** I had no llama.cpp `llama-server`, llama-swap, vLLM or LM Studio to test against. Two things are unconfirmed:
  - whether any of them sends chunked or compressed responses to this request;
  - the real `index` and `data` format, and `/v1` path handling behind llama-swap.

  Settling this needs a run of `embed()` against each server with a batch of 32 texts.
- **The six `remind_me_api` import/export tests** and the other suites that need a writable home directory couldn't run in this sandbox.
