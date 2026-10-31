<!-- remind-me/346-r1 run 5, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 193 s, 866097 tokens, $0.93 -->

# Review of PR 346: Rusty-Mill/rusty-remind-me (OpenAI-compatible `/v1/embeddings` embedder backend)

**Verdict: changes required.** I found five P2 defects and no P0 or P1. I couldn't compile anything, so every finding is traced from the source; none was run.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head reviewed:** `8c7e55a64821d8f411b8578d76dfa81c4b43b3b8`. The fetched `pull/346/head` matches this head, and the diff is one commit, `8c7e55a64`.

## What the PR touches, and the rule each part must keep
- **`OpenAiEmbedder` (new) and the HTTP client now shared with Ollama.** Every vector must be stored against the text that produced it. A malformed response must be refused, never accepted silently.
- **`resolve_embedder` and `config_fingerprint`.** The availability cache must be keyed on every variable the resolver reads. The doc comment at `embedder.rs:807-823` requires this.
- **`EmbeddingIdentity` (`backend`, `model`, `dim`).** ADR-0002's addendum settles this triple. On a mismatch, stored vectors are cleared when the database opens.
- **`ROLE_PREFIXES`, which now includes `qwen3-embed`.** Query and passage prefixes must not change for an existing deployment without a release note.
- **`RELEASE_NOTES.md` and `README.md`.** They must agree with the code.

The PR adds no sync, outbox, hub, tool-schema or other storage change. Vectors and embedding metadata are local to each node.

## Blocking findings

### P2-1: The release note says Ollama requests are unchanged, but they change for every Qwen3-Embedding model, and the new prefix isn't in the notes
- **Where:** `RELEASE_NOTES.md:12` ("Ollama requests and error messages are unchanged"); `crates/remind_me_core/src/embedder.rs:113-119` and `122-133`.
- **Trace (source-traced):**
  - `prefix_for` lowercases the model name and matches it as a substring, and it is shared by both backends.
  - So an existing node with `REMIND_ME_EMBEDDING_BACKEND=ollama` and `REMIND_ME_OLLAMA_EMBED_MODEL=qwen3-embedding:0.6b` changes behaviour after this PR. Every query it sends to `/api/embed` now starts with `Instruct: Given a search query…\nQuery:`.
  - Passages are still sent bare, so stored vectors stay valid. But the ranking of semantic search results changes with nothing in the notes about it.
  - The commit message mentions the prefix; `RELEASE_NOTES.md` doesn't.
- **Who is hurt:** an operator debugging a change in retrieval, and any agent relying on the old ranking.
- **Fix:** add a `### Changed` entry for the Qwen3 query instruction that names both backends, and qualify the "unchanged" sentence. Add an Ollama test asserting the prefixed `input` for `qwen3-embedding:0.6b` in `tests/ollama_embedder_test.rs`. It fails at base because base sends the query bare.

### P2-2: A response `index` that isn't an integer is silently replaced by arrival order, so vectors can land on the wrong text
- **Where:** `embedder.rs:479-482`.
- **The code:** `entry.get("index").and_then(|i| i.as_u64()).map_or(position, …)`.
- **Trace (source-traced):**
  - The fallback to arrival order applies whenever `as_u64()` returns `None`. That covers a missing index, but also one present as `1.0`, `"1"` or `-1`.
  - Example: a server answers a two-text batch with `[{index:1.0, embedding:A}, {index:0.0, embedding:B}]`. Both indexes fall back to position, so A goes into slot 0 and B into slot 1, which swaps them.
  - `embed_and_store` then stores text 1's vector against chunk 0. Semantic search returns that memory for queries that match the other text, and the response says nothing about it.
  - This also makes the release note's "Response entries are placed by their `index`" false for such input.
- **Who is hurt:** agents reading search results (retrieval fidelity). Stored facts themselves are unchanged.
- **Fix:** fall back to position only when `index` is absent. An `index` that is present but not a `u64` must return an `EmbedError`.
- **Regression test:** add a case to `openai_embedder_test.rs` that answers `[(1.0, [0,1]), (0.0, [1,0])]` and expects an error. It passes wrongly at head.

### P2-3: A missing `REMIND_ME_OPENAI_EMBED_MODEL` is reported as an unreachable daemon
- **Where:** `embedder.rs:946-956`, with `available_embedder` at `embedder.rs:911-913` and `status.rs:157-164`.
- **Trace (source-traced):**
  1. The operator sets `REMIND_ME_EMBEDDING_BACKEND=openai` but not the model.
  2. `resolve_embedder` still returns `Some`.
  3. The availability probe's `embed` fails with an error that names `REMIND_ME_OPENAI_EMBED_MODEL` (`embedder.rs:505-509`), but `available_embedder` throws that error away (`.is_ok()`).
  4. `remind_me_server_status` then reports "configured but not answering (unreachable daemon, or an unconfigured/unloadable ONNX model)".
  5. The daemon may be up. The real cause, an unset model variable, appears nowhere.
- **Who is hurt:** the operator, who goes looking at the server instead of the config.
- **Fix:** name the missing-model case in the status reason, or return `None` or a distinct status from `resolve_embedder` when `openai` has no model.
- **Regression test:** a status test under the env lock, alongside `embedder_availability_cache_test.rs`.

### P2-4: No test covers the new `resolve_embedder` arm or the new fingerprint entries
- **Where:** `embedder.rs:829-830` and `864-869`.
- **Trace (source-traced):**
  - The doc comment at `embedder.rs:820-823` says a variable the resolver reads but the fingerprint omits brings back exactly the stale-verdict bug it was written to close.
  - If `OPENAI_EMBED_URL_ENV` and `OPENAI_EMBED_MODEL_ENV` were deleted from the fingerprint, every test would stay green. `embedder_availability_cache_test.rs` only varies `OLLAMA_URL_ENV`.
  - Nothing tests that `openai` resolves to the default URL `http://localhost:8080`, or that it reads the URL and model from the environment.
- **Fix:** in `embedder_availability_cache_test.rs` (its own binary, under the env lock), add an `openai` version of `a_failed_probe_does_not_answer_for_a_different_backend_address`. Also add a case where the address stays the same and only `REMIND_ME_OPENAI_EMBED_MODEL` changes. Each should fail without the fingerprint lines.

### P2-5: The README feature list still names only two backends
- **Where:** `README.md:18` ("Optional vector search (Ollama or an in-process ONNX bi-encoder)").
- **Problem:** the PR adds a third backend, so this line now disagrees with the backend table at `README.md:524-528` and with the code. A smaller related gap: the `EmbedRole` doc at `embedder.rs:89-93` lists the prefixed model families and doesn't mention Qwen3.
- **Fix:** add the OpenAI-compatible backend to both.

## Design questions (for the owner, not findings)
1. **The identity doesn't include the endpoint, and for this backend the model name is only a routing alias.** ADR-0002's addendum settles identity as (`backend`, `model`, `dim`).
   - With llama-swap, `model` is whatever name the operator's config routes on.
   - If that alias is repointed to a different GGUF of the same dimension, or `REMIND_ME_OPENAI_EMBED_URL` moves to another server with the same alias, the identity doesn't change.
   - Old vectors are then kept and compared against query vectors from a different model, with no warning.
   - The risk existed with Ollama tags too, but this backend makes it much more likely. Should the identity include the URL, or the `model` field the server echoes back?
2. **An unusable `openai` configuration clears every stored vector when the database opens.**
   - With no model set, `db/mod.rs:315-317` reconciles against `{openai, "", dim}`, finds a mismatch, and clears all vectors.
   - `embedding_meta` isn't rewritten. So if the operator then goes back to their old Ollama setup, the identity matches again, but the vectors are already gone and nothing flags that a reindex is needed.
   - ONNX with an empty path already does the same, and that is documented at `embedder.rs:874-878`. Should a configuration that is known to be unusable skip the reconcile?

## Optional improvements
- `checked_vector` (`embedder.rs:375`) turns any non-numeric element into `0.0`. This was already true for Ollama and now applies to the new backend too. For example, llama.cpp with pooling turned off returns arrays nested per token. Refusing non-numeric elements would be safer.
- The `Host:` header leaves out the port (RFC 9110 §7.2), and the client doesn't handle `Transfer-Encoding: chunked`. Both are pre-existing. A server that sends chunked responses would fail loudly rather than silently, but that undercuts the release note's claim that vLLM and LM Studio work.
- `status.rs:162` (the config-only report) still says `set REMIND_ME_EMBEDDING_BACKEND=ollama`. This is pre-existing; the PR updated the live message but not this one.

## Checks and validation gaps
- `cargo fmt --all -- --check`: **passed** (exit 0).
- `cargo clippy` and `cargo test`: **could not run.**
  - Building needs the `rusty_mill` git dependency (`libs-2026.10.0`), which isn't in the local cargo cache.
  - The sandbox denies `github.com:443`, and `~/.cargo` is read-only.
  - There is a local `~/dev/rusty_mill`, but that's another of your projects outside what this task was given, so I didn't use it.
  - So none of the new tests was executed, at the head or at base. At base the new test file can't compile because `OpenAiEmbedder` doesn't exist, so every new test there is red by construction.
  - Allowing `github.com` would let me run `cargo clippy --locked -p remind_me_core … --all-targets -- -D warnings` and `cargo test --locked -p remind_me_core --test openai_embedder_test --test ollama_embedder_test --test embedder_availability_cache_test`.
- **Not verified against real servers:** llama.cpp `llama-server --embeddings`, llama-swap, vLLM and LM Studio, in particular for chunked responses and batch-size limits on 32-text batches. Settling it needs one `embed` of 32 chunks of about 1,600 characters against each.

**Verdict: changes required** (P2-1 to P2-5).
