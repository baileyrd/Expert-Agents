<!-- remind-me/346-r1 run 2, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 180 s, 1073251 tokens, $0.97 -->

I'm requesting changes on PR 346: I found one P1 and three P2 defects. I couldn't build the workspace or run any tests, so every finding below is traced from the source, not executed.

**Pinned:** Rusty-Mill/rusty-remind-me #346, base `d5528b1347054f348b3dac96f54619e603f4594c`, head `8c7e55a64821d8f411b8578d76dfa81c4b43b3b8`. `FETCH_HEAD` matches the head SHA, and there is one commit, `8c7e55a64 embedder: OpenAI-compatible /v1/embeddings backend`.

## What the diff touches, and the rule each must keep

| Item | Rule it must keep |
|---|---|
| `resolve_embedder` / `EmbeddingIdentity` | The identity used at store open must name the model that wrote the stored vectors. `db/mod.rs:315-316` deletes every vector when it differs (`vectors.rs:548`). An unusable configuration must not count as a model change. |
| `config_fingerprint` | Lists every variable `resolve_embedder` reads. The new URL and model variables are added (`embedder.rs:829-830`). |
| `/v1/embeddings` wire format | One vector per input, placed by `index`, at the configured dimension. Bad input is refused, not guessed at. |
| `ROLE_PREFIXES` (query prefixes) | The same prefix for a model on every backend, and stored passages unchanged unless the identity changes. Passage prefixes are unchanged here, so no reindex is needed. |
| `embedding_status` / `server_status` | They report the real reason the embedder can't be used. |
| README / RELEASE_NOTES | They match the code. |

Sync, the outbox, the hub, tools, and the containment and identity paths aren't touched; those failure classes don't apply. No new locks or threads. The availability cache is keyed on the full fingerprint, so it is still correct. No timestamps are involved. The URL still has to be `http://` with a port, otherwise it's an error.

## Blocking findings

### P1: a missing model name deletes every stored vector the next time any process opens the store
- **Where:** `crates/remind_me_core/src/embedder.rs:864-869` (the model defaults to `""`), together with `crates/remind_me_core/src/db/mod.rs:315-316` and `vectors.rs:548-557`.
- **Failure scenario (traced from source):**
  1. A node runs `REMIND_ME_EMBEDDING_BACKEND=openai` with `REMIND_ME_OPENAI_EMBED_MODEL=qwen3-embedding`, and `embedding_meta` holds `{openai, qwen3-embedding, 1024}`.
  2. A second process sets the backend variable but not the model, for example a hook or CLI started from a different MCP client config.
  3. `resolve_embedder` still returns an embedder, with identity `{openai, "", 1024}`.
  4. `Database::open` → `reconcile_embedding_meta` sees a mismatch and calls `Vectors::clear()`.
  5. Every embed then fails with "needs a model", so nothing re-embeds. Each agent silently gets keyword-only search until someone fixes the env and reruns `remind_me_reindex` over the whole store.
- **Why it's this PR's defect:** the PR itself calls the model "Required; there is no sensible default" and refuses it in `embed`, yet lets it count as a real identity.
- **Pre-existing sibling:** ONNX with an empty model path already does the same (`embedder.rs:608`). Worth fixing in the same change.
- **Fix:** treat an empty model as not configured for the store-open check. Either `resolve_embedder` returns `None` (or a state that is never reconciled) when the model is empty, or `from_tables` skips the check when `identity().model` is empty.
- **Regression test:** in `open_reconcile_test.rs`, under `ENV_LOCK`, plant a vector stored as `{openai, m, 4}`. Reopen with `BACKEND=openai` and the model unset, and assert that `stored_vector_counts == 1`.

### P2: with no model set, status reports a reachability problem
- **Where:** `embedder.rs:944-956` and `status.rs:158`.
- **Failure scenario (traced from source):** with `openai` set and no model, `embedding_status` says "configured but not answering (unreachable daemon, or an unconfigured/unloadable ONNX model)". The real error (`embedder.rs:506`, "set REMIND_ME_OPENAI_EMBED_MODEL") is thrown away by the availability probe's `.is_ok()`. The static `server_status` says `Active`. An operator goes looking for a network fault.
- **Fix:** report the missing model by name. This follows from the P1 fix.
- **Regression test:** with `BACKEND=openai` and the model unset, assert that the `embedding_status()` reason contains `REMIND_ME_OPENAI_EMBED_MODEL`.

### P2: the release note says Ollama requests are unchanged, but this PR changes them
- **Where:** `RELEASE_NOTES.md:11` and `embedder.rs:114-119`.
- **Failure scenario (traced from source):** the new `qwen3-embed` prefix entry is matched by model name, not by backend. An Ollama user on `qwen3-embedding:0.6b` now has every search query sent with the `Instruct: …\nQuery:` prefix. Their query vectors change, while the note says "Ollama requests and error messages are unchanged" and doesn't mention the prefix at all. The new test only checks the OpenAI path.
- **Fix:** add a note under Changed saying Qwen3-Embedding queries on both backends now carry the model card's instruction, and drop the claim that Ollama requests are unchanged.
- **Regression test:** an Ollama fake-server test (in `ollama_embedder_test.rs`) asserting the prefix on a query for `qwen3-embedding`.

### P2: a malformed `/v1/embeddings` response can be stored as if it were valid
- **Where:** `embedder.rs:480-483` and `embedder.rs:376`.
- **Failure scenarios (traced from source):**
  - **Bad index:** if `index` is present but isn't an integer (`"1"`, `-1`), the code quietly uses arrival order. If the entries also arrive out of order, vectors land on the wrong inputs, which is the exact case the release note says is guarded.
  - **Non-numbers:** any element that isn't a number, such as the `null` llama.cpp's JSON writer produces for NaN, becomes `0.0`. A corrupted vector is then stored as if it were valid.
  - The `0.0` handling is inherited from the Ollama path, but this PR makes it reachable from a new server family.
- **Fix:** if `index` is present and not a `u64`, return an error. If any element isn't a finite number, return an error.
- **Regression tests:** in `openai_embedder_test.rs`, add `{"index":"1",…}` and `{"embedding":[null,1.0]}`, each expected to be an error.

### P2: the new environment wiring has no tests
None of the new tests covers the `"openai"` branch of `resolve_embedder`, the two new `config_fingerprint` entries, or store open with the `openai` backend. Deleting `embedder.rs:829-830` or `:864-869` turns no test red.
- **Where they belong:**
  - A `resolve_embedder` test with `openai`, like `onnx_embedder_test.rs:40-48`.
  - A cache test where changing `REMIND_ME_OPENAI_EMBED_URL` drops the cached verdict, in `embedder_availability_cache_test.rs`.
  - An `openai` case in `open_reconcile_test.rs`.

On the new tests: none can pass at base, since `OpenAiEmbedder` doesn't exist there. They use a hand-built fake server, which is the existing pattern.

## Design questions
- **ADR-0002 is out of date.** Its Decision says "only `"ollama"` enables it". ONNX was added without amending it, and this PR adds a third backend the same way. Should the ADR get an addendum listing the backends?
- **`EmbeddingIdentity` still leaves out the endpoint**, as `RELEASE_NOTES.md:1117` already notes. With `openai` this matters more: the model name is just a llama-swap routing name, so changing what that name points to on the server, or moving to a different server, changes the vectors with no identity change and no clear.

## Optional improvements
- The `Host` header leaves out the port (RFC 9110 §7.2). This comes from the existing Ollama code and is harmless for llama-swap, which routes on the model name.
- The `OPENAI_EMBED_URL_ENV` doc example uses a personal hostname (`http://baileyai:8080`).
- The doc on `DEFAULT_EMBEDDING_DIM` says "(Ollama or ONNX)", and `status.rs:162` names only `ollama`.

## Checks run
- `cargo fmt --all -- --check`: **passed**, exit 0.
- `cargo clippy` and `cargo test`: **not run**.
  - Offline, the git dependency `rusty_mill@libs-2026.10.0` isn't cached.
  - Online, the sandbox blocks `github.com:443`, and `~/.cargo` is read-only.
  - So the PR's claim that the Ollama, availability-cache and status suites still pass is unverified, and none of the new tests has been run.

## Gaps that still need a run
- **Tests:** the six-crate clippy and test commands from the brief, with network access to github.com (or a pre-populated `CARGO_HOME`).
- **Chunked responses:** whether vLLM, LM Studio or llama-swap ever send a chunked (`Transfer-Encoding: chunked`) reply. The hand-rolled reader would fail on one; it would be a visible error, not silent. To settle it, embed against each server live with a batch of 32 inputs.
- **Real server behaviour:** llama.cpp's actual `index` and NaN output, by running `llama-server --embeddings` with a Qwen3-Embedding GGUF.

**Verdict: changes required**
