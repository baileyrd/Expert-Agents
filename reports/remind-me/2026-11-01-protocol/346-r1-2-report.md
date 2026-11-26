<!-- remind-me/346-r1 run 2, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 708 s, 1540860 tokens, $1.35 -->

# Review of PR 346: OpenAI-compatible `/v1/embeddings` embedding backend

**Verdict: changes required.** The most serious problem is that an incomplete `openai` configuration deletes every stored vector the next time any process opens the store. I reproduced it.

- **Repository:** Rusty-Mill/rusty-remind-me, PR #346
- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head:** `8c7e55a64821d8f411b8578d76dfa81c4b43b3b8` (confirmed: `FETCH_HEAD` of `pull/346/head` equals it)
- **Diff:** one commit, 4 files: `embedder.rs`, a new `tests/openai_embedder_test.rs`, `README.md`, `RELEASE_NOTES.md`

## What the diff touches, and the rule each part must follow

| Item | Rule | Basis |
|---|---|---|
| `EmbeddingIdentity` from `resolve_embedder()` | It names the embedder that will actually write vectors. On every open, `reconcile_embedding_meta` deletes all stored vectors when this identity differs from the one recorded. | `db/mod.rs:315-316`, `vectors.rs:548` |
| `OpenAiEmbedder::embed` | One normalised vector per input, in input order, with length `dim`. Bad replies are errors. | `Embedder` trait docs (`embedder.rs:150`), the PR's own release note |
| `post_json` (hand-written HTTP/1.1 client, now shared by both backends) | It must read anything a conforming HTTP/1.1 server may send in reply to an HTTP/1.1 request. | RFC 9112 §6, §7.1 |
| `config_fingerprint` (key for the availability cache) | Lists every variable `resolve_embedder` reads. | The doc comment at `embedder.rs:811-813` |
| `ROLE_PREFIXES` | Changing a query prefix changes query vectors for every backend serving that model. Passage vectors must stay stable. | `prefix_for`, `embedder.rs:120` |
| Sync and wire formats | Not touched. `vec_chunks` and `embedding_meta` are local, and the outbox and hub are unchanged. | Ruled out |

## Blocking findings

### 1. P1: `openai` with the model unset wipes the whole vector store when it is opened (executed)

- **Where:** `embedder.rs:864-869`, with the clear at `db/mod.rs:315-316`.
- **Cause:** when `REMIND_ME_OPENAI_EMBED_MODEL` is unset, `resolve_embedder()` still returns an `OpenAiEmbedder`, with identity `{openai, "", dim}`. `Database::from_tables` compares that with the recorded identity, sees a mismatch, and clears `vec_chunks`. Only afterwards does `embed()` (`embedder.rs:504`) refuse that same configuration as invalid.
- **Trigger:** any process that opens the store with `REMIND_ME_EMBEDDING_BACKEND=openai` but without the model variable. Examples are a hook, a CLI run, or an MCP server started from a shell that exported only one of the two variables. The README marks the model as required, so this is an easy mistake.
- **Probe result (scratch copy):**
  - Stored meta was `{openai, qwen3-embedding-0.6b, 4}` with one vector.
  - Reopening with the same configuration left the count at 1.
  - Unsetting the model gave identity `{backend:"openai", model:"", dim:4}`, and the next open dropped the count to 0.
  - `embed` then returns "needs a model".
- **Effects:**
  - Nothing re-embeds automatically. Only an explicit reindex refills the vectors (`vectors.rs:400ff`).
  - `embedding_meta` still holds the old identity. So when the correct configuration comes back, nothing notices that vectors are missing.
  - Every agent silently gets keyword-only search over the whole store.
  - `remind_me_server_status` blames an "unreachable daemon" (`embedder.rs:952`).
- **Grade:** P1, not P0. No memory content is lost and the vectors can be rebuilt, but the whole semantic index is destroyed by a configuration the PR itself calls invalid.
- **Correction:**
  - `resolve_embedder` should return `None`, or a "not configured" state that `from_tables` skips, when the model is empty or blank.
  - The status reason should name the missing variable.
- **Regression test:** in `open_reconcile_test.rs`, under `ENV_LOCK`, plant `openai`/model-X vectors, reopen with `BACKEND=openai` and no model, and assert the vector count is still 1. This fails at the head.

### 2. P2: malformed `/v1/embeddings` replies are accepted as success (executed)

- **Where:** `embedder.rs:480-483` and `embedder.rs:376`.
- **Index fallback:** `.map_or(position, …)` falls back to arrival order when `index` is present but is not a u64, for example `"1"` or `-1`.
  - Probe: entries `{"index":"1",[0,1]}, {"index":"0",[1,0]}` returned `Ok([[0,1],[1,0]])`. Each input got the other input's vector, so the wrong memory would be ranked for a query.
- **Non-numeric elements:** `checked_vector` turns any non-number element into `0.0`. Before this PR that applied only to Ollama replies; the PR now feeds it a new family of servers.
  - Probe: `[null,"x"]` returned `Ok([[0.0,0.0]])`, and that zero vector would be stored as if it were valid.
- **Doc claim this makes false:** the release note says entries "are placed by their `index`" and that bad replies are "an error, not a silent mismatch".
- **Correction:** treat an `index` that is present but not a non-negative integer as an error, and treat any non-numeric element as an error.
- **Regression test:** both cases in `openai_embedder_test.rs`.

### 3. P2: a chunked HTTP/1.1 reply is rejected, with a misleading error (executed)

- **Where:** `embedder.rs:299-347`.
- **Cause:** the client sends an `HTTP/1.1` request but ignores `Transfer-Encoding` (and `Content-Length`). RFC 9112 §7.1 says a recipient MUST be able to parse chunked encoding.
- **Probe result:** a valid chunked reply produced `Err("OpenAI-compatible server response was not JSON: trailing characters …")`. Semantic search then falls back to keyword-only, and status reports "not answering".
- **Scope:** the limitation already existed for Ollama. This PR exposes it to every server the release note advertises (llama.cpp, llama-swap, vLLM, LM Studio).
- **Correction:** decode chunked replies and honour `Content-Length`, or send `HTTP/1.0` so a conforming server cannot use chunked encoding.
- **Regression test:** a fake server that answers with a chunked reply, in `openai_embedder_test.rs`.

### 4. P2: the release note is wrong about Ollama and omits the Qwen3 prefix (executed)

- **Where:** `RELEASE_NOTES.md:11` and `embedder.rs:114-119`.
- **Cause:** prefix matching applies to every backend. With `OllamaEmbedder("qwen3-embedding:0.6b")`, the probe's query body is now `"Instruct: Given a search query, …\nQuery:quokkas"`. The base sends the bare query.
- **Effects:**
  - "Ollama requests … are unchanged" is false.
  - The Qwen3 prefix appears only in the commit message, not in `RELEASE_NOTES.md`. CONTRIBUTING requires an entry for every user-visible change, and existing Ollama Qwen3 users will see search ranking change.
  - Stored vectors stay valid, because the passage prefix is still `""`.
- **Correction:** add a release-note line for the prefix, and narrow the "unchanged" claim.
- **Regression test:** a `prefix_for("qwen3-embedding:0.6b", Query)` and `Passage` assertion next to the existing prefix-table unit test.

### 5. P2: no test covers the `openai` setting resolution or the new availability-cache key

- **Gap:** `openai` appears only in `embedder.rs` and the new test file. Nothing exercises the `"openai"` arm of `resolve_embedder` (the default URL, reading the model variable) or the new `OPENAI_*` entries in `config_fingerprint`.
- **Why it matters:** if either new variable were removed from the fingerprint list, every test would still pass. That is exactly the regression the doc comment on `config_fingerprint` warns about.
- **Regression test:** in `embedder_availability_cache_test.rs` under `ENV_LOCK`, point `REMIND_ME_OPENAI_EMBED_URL` at a dead port, then at a live fake server within the failure TTL, and expect `Some`. Add a resolve test for the defaults.

## Design questions for the owner (not findings)

- **Identity has no endpoint.** It is the triple backend/model/dim. With llama-swap, the `model` is an alias the operator controls. Re-pointing the alias, or pointing the URL at another server with the same alias, keeps the identity equal, so vectors from two embedding spaces get mixed. Should `openai` identity include the URL, or does the operator take responsibility for this?
- **Plain `http://`, no API key.** This matches `ollama`. But the release note's vLLM/LM Studio framing invites remote hosts, and memory content (including rows marked sensitive) would travel in cleartext to them.

## Optional improvements

- `OpenAiEmbedder.url` is a `pub` field. Building the struct directly skips the trailing-slash and `/v1` normalisation that `new()` does.
- The status message at `embedder.rs:952` could list the `openai` causes (unreachable server, missing model).

## Checks

| Check | Result |
|---|---|
| Head pin | `pull/346/head` equals `8c7e55a6…` ✔ |
| `cargo fmt --all -- --check` | pass |
| `cargo clippy --locked -p (six packages) --all-targets -- -D warnings` | pass, no warnings |
| `cargo test --locked -p remind_me_core --test openai_embedder_test` | 10/10 pass |
| New tests at base | cannot compile there (`OpenAiEmbedder`, `OPENAI_EMBED_MODEL_ENV` and `qwen3` are absent at base), so they do guard the change |
| `cargo test --locked --no-fail-fast` over the six packages | 168 test binaries; 2485 passed, 3 failed |
| The 3 failures | all in `peer_discovery_test`. The sandbox refuses the `UnixListener::bind` at line 45 with `PermissionDenied`. The PR does not touch that area. |
| `remind_me_api` `import_export_test` | failed on the first run because the sandbox's `$HOME` is read-only. It passed on the rerun with `HOME` pointed at a scratch directory. |
| Probes | 4 probes in a scratch copy (`$TMPDIR/pr346`), not in the reviewed tree. Results are quoted in findings 1–4. |

## Validation gaps

- I had no real llama-server, llama-swap, vLLM or LM Studio, so I could not check whether any of them actually sends chunked replies, or what `index` and element types they emit.
- The `local-embed` feature was not built. The PR changes only comments in that code path.
- Postgres and the hub are not relevant: no sync-carried record changed.

**Verdict: changes required.**
