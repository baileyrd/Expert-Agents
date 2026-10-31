<!-- remind-me/346-r1 run 4, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 230 s, 1214378 tokens, $1.21 -->

# Review of PR 346: OpenAI-compatible `/v1/embeddings` backend

**Verdict: changes required.** I found one P1 and four P2 defects. The clippy and test runs could not be done because GitHub access was denied (see Checks).

- **Repository:** Rusty-Mill/rusty-remind-me, PR 346
- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head reviewed:** `8c7e55a64821d8f411b8578d76dfa81c4b43b3b8`. The fetched `pull/346/head` matches it. One commit; it touches `embedder.rs`, a new `openai_embedder_test.rs`, `README.md` and `RELEASE_NOTES.md`.

## What the diff touches and the rule for each

| Item | Rule that must hold |
| --- | --- |
| `EmbeddingIdentity` (`embedder.rs:171`, ADR-0002 addendum #96) | Each stored vector was made by the embedder the query uses. Opening the store clears vectors only when a *real* model change happened. |
| `ROLE_PREFIXES` | A new entry must not change how existing stored vectors were made, and any change it makes for existing users is in the release notes. |
| `config_fingerprint` (`embedder.rs:823`) | Includes every variable `resolve_embedder` reads. **Holds:** both new variables were added. |
| `post_json` and `embed_batch` (new input from a server) | One checked vector per input, in input order. Bad input is refused, never coerced. |
| Tools, sync, hub | **Not touched.** Vectors and `embedding_meta` are node-local: nothing in the sync code or `remind_me_hub/src` refers to them. So the replication and contract checks don't apply. |

## Blocking findings

### P1: one process with the model unset deletes every stored vector
**Where:** `crates/remind_me_core/src/db/mod.rs:315-316`, made reachable by `embedder.rs:864-869`.

**Trace** (source-traced; the identity value was confirmed by running the PR's code):
1. A node uses `REMIND_ME_EMBEDDING_BACKEND=openai` with `REMIND_ME_OPENAI_EMBED_MODEL=Qwen3-Embedding-0.6B`. `embedding_meta` records `("openai","Qwen3-Embedding-0.6B",1024)`.
2. A second process on the same store starts with the backend variable but not the model, for example a hook, the CLI or the dashboard launched with a partial environment.
3. `resolve_embedder` still returns `OpenAiEmbedder{model:""}`. My probe confirmed its identity is `("openai","",1024)`.
4. `Database::open` calls `reconcile_embedding_meta`, which sees a mismatch and runs `Vectors::clear()`. Every vector in the store is deleted.
5. That process can never embed: `embed` refuses an empty model. So `embedding_meta` is never rewritten and nothing records why the vectors went.

**Who is hurt:** every agent's semantic search on that node falls back to keyword-only until someone runs a reindex.

The PR itself says the model is required, yet open-time reconcile treats the unset value as a real model. This is the same class of bug as `2413d3a92` ("a process without the sync settings no longer switches sync off").

**Fix:** don't reconcile against an identity the embedder would refuse. Either:
- give `Embedder` a "configuration complete" check and skip reconcile at `db/mod.rs:315` when it fails, or
- don't build an `OpenAiEmbedder` with an empty model, and report the missing variable through `embedding_status` instead.

**Regression test**, in `open_reconcile_test.rs` under `ENV_LOCK`: plant a vector with meta `("openai","m",4)`, set `BACKEND=openai` and `DIM=4`, leave the model unset, open the store, and assert `stored_vector_counts == 1`. At head it returns 0.

### P2: malformed response entries are accepted, and can attach a vector to the wrong text
**Where:** `embedder.rs:478-481` (index fallback) and `embedder.rs:376` (component coercion, now shared by both backends).

**Executed** against the PR's code:
- `{"data":[{"index":1.0,"embedding":[0,1]},{"index":0.0,"embedding":[1,0]}]}` returned `Ok([[0,1],[1,0]])`. `as_u64()` is `None` for a float, so each entry falls back to its position, and "first" is stored with "second"'s vector.
- `[null,null]` returned `Ok([[0,0]])`, a zero vector that gets stored.
- `["x",2.0]` returned `Ok([[0,1]])`.

This makes the release note's line "a repeated index … is an error, not a silent mismatch" only partly true.

**Fix:**
- If an `index` is present but is not a `u64`, return an error. Fall back to position only when every entry has no `index` at all.
- In `checked_vector`, refuse any non-numeric component.

**Regression tests:** add both cases to `openai_embedder_test.rs`; each should return an error.

### P2: the release notes say Ollama requests are unchanged, but some aren't
**Where:** `RELEASE_NOTES.md:12`, against the new `qwen3-embed` row at `embedder.rs:113-119`.

**Executed:** an `OllamaEmbedder("qwen3-embedding:0.6b")` query now sends `"Instruct: Given a search query, …\nQuery:quokkas"`. Before this PR it sent `"quokkas"`.

Stored vectors stay valid, because passages are still sent with no prefix, but search ranking changes for existing Ollama Qwen3 users. The new prefix is in the commit message and missing from `RELEASE_NOTES.md`.

**Fix:** add a Changed entry for the Qwen3 query instruction on every backend, and correct the "unchanged" claim. **Test:** add a `qwen3-embedding` case to `role_prefixes_match_known_model_families_by_substring` (`embedder.rs:1037`).

### P2: docs and the status message are out of date
- `README.md:18` still says "Optional vector search (Ollama or an in-process ONNX bi-encoder)".
- The `DEFAULT_EMBEDDING_DIM` doc (`embedder.rs:65`) says "(Ollama or ONNX)".
- `embedding_status` (`embedder.rs:950-955`): with `openai` and no model it reports "unreachable daemon, or an unconfigured/unloadable ONNX model". That points at the wrong cause and drops the embedder's own error, which names `REMIND_ME_OPENAI_EMBED_MODEL`.

**Fix:** update those three texts and have the status name the missing model. **Test:** a `status_test.rs` case, under the env lock, asserting that the reason mentions `REMIND_ME_OPENAI_EMBED_MODEL`.

### P2: environment-driven setup has no tests
`openai_embedder_test.rs` only ever builds `OpenAiEmbedder::new` directly. If any of these were reverted, no test would fail:
- the `"openai"` branch of `resolve_embedder`,
- the `DEFAULT_OPENAI_EMBED_URL` default,
- the two new `config_fingerprint` entries.

**Where they belong:**
- In `embedder_availability_cache_test.rs`: changing `REMIND_ME_OPENAI_EMBED_URL` or `REMIND_ME_OPENAI_EMBED_MODEL` invalidates a cached failure.
- In its own binary or under the env lock: `BACKEND=openai` resolves to identity `("openai", <model env>, dim)`.

## Failure classes ruled out
- **Half a change, hidden rows, time as text, who is calling:** the diff writes no memory rows or timestamps, and touches no auth or proxy code.
- **Wire versus row, concurrency:** vectors are not synced, and the availability cache's locking is unchanged.
- **Retrieval admission:** no stage or fusion logic changed. Embedder identity is covered by the P1 finding and the design question below.
- **Tool surface:** no tools, schemas or error formats changed (ADR-0028 and ADR-0030 are not affected).
- **Silent success:** a non-2xx status, a wrong count and a wrong dimension are all errors (the PR's tests cover them). A chunked response is also an error, not silent (probe 5). The holes are the coercions in the second P2 above.

## Design questions for the owner
- **The identity leaves out the endpoint.** With llama-swap, the `model` value is an alias the operator chooses. Pointing that alias at a different GGUF of the same dimension, or pointing the URL at another server, leaves the identity unchanged. Vectors from two embedding spaces then mix without anything noticing. Ollama tags have the same weakness today, and ADR-0002's addendum settles the three-part identity. Should `openai` add the URL to it?

## Optional improvements
- The comment on the `OpenAiEmbedder.url` field is now accurate, because `new` strips `/v1`. A URL with any other path (`http://h:8080/llama`) fails with "invalid port", which is unclear; a path-specific error would help.

## Checks and validation gaps
- `cargo fmt --all -- --check`: **passed**.
- `cargo clippy …` and `cargo test --locked` over the six packages: **not run**. The pinned `rusty_mill` git dependency (`libs-2026.10.0`) is not cached, `~/.cargo` is read-only, and the sandbox refused access to `github.com`. There is a local `~/dev/rusty_mill` checkout, but I didn't use it: it is the user's separate project, outside this task. So I have not seen the PR's own tests pass.
- **Executed probe:** a scratch crate under `$TMPDIR/probe` built offline from the head's `embedder.rs`, line ranges copied unchanged. It produced results 1–7 cited above.
- **Base guard:** `openai_embedder_test.rs` cannot compile at the base (there is no `OpenAiEmbedder` there), so it does guard the new code.
- **Gap, chunked responses from real servers:** this client does not decode `Transfer-Encoding: chunked` (probe 5 gives an error). If llama-swap, vLLM or LM Studio send a chunked embeddings response, this backend never comes up: search drops to keyword-only and the status says "not answering". The PR doesn't say whether that happens. To settle it, run `curl -sv` against `/v1/embeddings` on llama-swap and on llama-server with a 1024-dimension model and check for `Content-Length` versus `chunked`. The same question applies to Ollama's existing path.
- **Gap:** the workspace build and test run above. Neither my verdict nor any finding depends on it.
