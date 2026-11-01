<!-- remind-me/346-r1 run 5, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 802 s, 1670955 tokens, $1.46 -->

# Review of PR 346: OpenAI-compatible `/v1/embeddings` backend

**Repository:** Rusty-Mill/rusty-remind-me · **PR:** 346
**Base:** `d5528b1347054f348b3dac96f54619e603f4594c` · **Reviewed head:** `8c7e55a64821d8f411b8578d76dfa81c4b43b3b8`. I fetched `pull/346/head` and it equals the head SHA. There is one commit, "embedder: OpenAI-compatible /v1/embeddings backend".

**Verdict: changes required.** The PR has two P1 defects and two P2 defects, plus one coverage gap.

## What the diff touches, and the rule each part must keep

| Touched | Rule it must keep | Source |
|---|---|---|
| `resolve_embedder` gets a new `openai` branch | Whatever it returns becomes the "current" identity at every database open. A mismatch with the stored identity deletes every stored vector. So it must never return an identity the backend itself refuses to use. | `db/mod.rs:314-317`, `vectors.rs:548-557` |
| `EmbeddingIdentity` `("openai", model, dim)` | One identity per embedding space. A vector is only compared with vectors from the same embedder. | `embedder.rs:161-168` (#96) |
| `OpenAiEmbedder::embed_batch` | Vector *i* belongs to input *i*. A bad server response is an error, never a silent mismatch. | Its own doc comment and the release-note claim |
| `ROLE_PREFIXES` gets a `qwen3-embed` entry | Applies to every backend, because matching is a substring test on the model name (`prefix_for`). | `embedder.rs:122-133` |
| `post_json` and `host_port` moved out to shared functions | Ollama behaviour stays the same. The hand-rolled HTTP/1.1 client must accept or clearly refuse anything a conforming HTTP/1.1 server may send back. | RFC 9112 |
| `config_fingerprint` | Must list every variable `resolve_embedder` reads. | Its own doc comment, `embedder.rs:806-822` |
| README and RELEASE_NOTES | Must describe the code accurately. | CONTRIBUTING / persona |

There is no sync, outbox, hub, tool-surface or containment change. Vectors and `embedding_meta` are local to each node, and no secret is accepted (plain `http://`, no API key). I ruled out those failure classes for that reason.

## Blocking findings

### 1. P1: Opening the store with `BACKEND=openai` and no model deletes every stored vector, and they don't come back when the model is restored
- **Where:** `crates/remind_me_core/src/embedder.rs:864-869` (the `openai` branch returns `Some` with an empty model), together with `crates/remind_me_core/src/db/mod.rs:315-316`.
- **What happens (executed probe):**
  1. A store has vectors written under `openai/qwen3-embedding/4`. Reopening with the same configuration leaves 1 vector.
  2. A second process opens the store with `REMIND_ME_OPENAI_EMBED_MODEL` unset. That could be a hook, the CLI, or another MCP client whose environment is missing the variable.
  3. `resolve_embedder().is_some()` is `true`, with identity `("openai", "", 4)`.
  4. `reconcile_embedding_meta` sees a mismatch and clears every vector. Probe output: **after open with model unset: 0; after reopen with model restored: 0.**
- **Why it stays broken:** `embed()` refuses to run with an empty model, so nothing re-embeds. `embedding_meta` still says `qwen3-embedding`, so the correctly configured process sees no mismatch and does not reindex. Semantic search silently falls back to keyword-only for every agent until someone reindexes by hand.
- **Why the PR's own design makes this worse:** the PR documents the model as "Required; there is no sensible default", but still builds an identity from the missing value and lets it trigger the destructive open-time check. (The `onnx` branch has the same pattern with empty paths. That is older code, but this PR adds a new case of it.)
- **Fix:** `resolve_embedder` should return `None` for `openai` with an empty model, or open-time reconcile should be skipped when the identity is unusable.
- **Regression test:** in `open_reconcile_test.rs`, plant an `openai/qwen3-embedding` vector and its meta, reopen with the model variable removed, and assert the count is still 1. It goes red at head.

### 2. P1: An `index` that isn't a non-negative integer silently falls back to arrival order, so vectors get stored against the wrong text
- **Where:** `embedder.rs:478-481`. The code `.and_then(|i| i.as_u64()).map_or(position, ...)`.
- **What happens (executed probe):** the response is `data: [{"index":"1","embedding":[0,1]}, {"index":"0","embedding":[1,0]}]` for inputs `["first","second"]`. The result is `Ok([[0,1],[1,0]])`: the two vectors are swapped and no error is reported.
- **Who is hurt:** the swapped chunk vectors are stored as truth for their memories. Semantic search then returns memory A for queries about B, and agents act on the wrong result until a reindex.
- **Docs made false:** the release note says "a repeated index … is an error, not a silent mismatch". Also, `index: -1` together with `index: 0` is reported as "bad index 0", which points at the wrong entry.
- **Fix:** when an entry has an `index` key, require it to be a `u64` and error otherwise. Fall back to position only when no entry carries `index` at all. Don't allow a mix of entries with and without it.
- **Regression test:** in `openai_embedder_test.rs`, string indexes in reversed order should produce an error. It goes red at head.

### 3. P2: The new Qwen3 query prefix changes Ollama requests, but the release notes say they are unchanged and don't mention the prefix at all
- **Where:** `embedder.rs:113-119` and `RELEASE_NOTES.md:11`.
- **What happens (executed probe):** `OllamaEmbedder::new("qwen3-embedding:0.6b", …)` with a Query now sends `"Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery:quokkas"`. At base the table had no such entry, so the text was sent bare.
- **Effect:** existing Ollama users of `qwen3-embedding` get different query vectors after upgrading. Documents still go in bare and there is no query-vector cache, so the index stays consistent and the change is probably an improvement. But the user-visible change is undocumented, and "Ollama requests … are unchanged" is false.
- **Fix:** add a `Changed` entry naming the prefix and both backends it affects.
- **Regression test:** in `ollama_embedder_test.rs`, assert the Qwen3 query prefix. The existing Qwen3 test only covers the OpenAI path.

### 4. P2: The HTTP/1.1 client can't read a chunked response, and the error it gives is misleading
- **Where:** `embedder.rs:333-345` (`post_json`). The code moved here in this PR and now serves new servers.
- **What happens (executed probe):** the request says `HTTP/1.1`, so under RFC 9112 §6.1 a conforming server may answer with `Transfer-Encoding: chunked`. With a chunked 200 the result is `Err("OpenAI-compatible server response was not JSON: trailing characters at line 1 column 2")`. It fails loudly, but the message points the operator at the wrong cause.
- **Docs at risk:** the release note claims the backend works with vLLM and LM Studio. I have not checked their response framing (see validation gaps).
- **Fix:** decode chunked bodies, or detect `Transfer-Encoding` and refuse it with a clear message. Sending `HTTP/1.0` would also stop servers from chunking.
- **Regression test:** a fake server that sends a chunked 200.

### 5. P2 (coverage): the `openai` configuration path has no test
- **What's missing:** no test covers the `openai` branch of `resolve_embedder`, its default URL, or the two new `config_fingerprint` entries. Of the test files that reference `"openai"` or the new variables, only `openai_embedder_test.rs` mentions them, and only for the identity string and an error message. `embedder_availability_cache_test.rs` only exercises `OLLAMA_URL_ENV`.
- **Consequence:** removing `OPENAI_EMBED_URL_ENV` from `config_fingerprint` would bring back the stale-availability bug that function's own comment describes, and every test would still pass.
- **Where the tests belong:** `embedder_availability_cache_test.rs` (changing the OpenAI URL invalidates the cached verdict) and `onnx_embedder_test.rs` (the resolve tests), each under the existing env lock.

## Design questions
- **Endpoint isn't part of the identity.** The identity is still the triple (backend, model, dim) from #96 and ADR-0002. With llama-swap, the `model` is a routing alias, so pointing the URL at another host that routes the same alias to a different model goes undetected. This is settled behaviour and only raised here for the owner.
- **ADR-0002 is out of date.** It still records Ollama `/api/embed` as *the* backend. A third backend may deserve an addendum to that ADR.

## Optional improvements
- `status.rs:162` still says "set REMIND_ME_EMBEDDING_BACKEND=ollama" even though this PR updated the matching message in `embedder.rs`. The message was already stale before this PR.
- The `Host` header leaves out the port, although RFC 9110 §7.2 asks for the full authority. This is older code, now shared by both backends.
- When a JSON number fails to parse, `checked_vector`'s `as_f64().unwrap_or(0.0)` stores 0.0 silently. This is older behaviour, now shared by both backends.

## Checks performed
- **Commit:** pinned. `FETCH_HEAD` equals the head SHA.
- **`cargo fmt --all -- --check`:** passed (exit 0).
- **`cargo clippy --locked -p` (all six packages) `--all-targets -D warnings`:** passed (exit 0). I set `CARGO_TARGET_DIR` to scratch space because the configured target directory is read-only here.
- **`cargo test --locked`** on `openai_embedder_test` (10) and `ollama_embedder_test` (7): all passed. The new test file can't compile at base because `OpenAiEmbedder` doesn't exist there, so it does go red without the change.
- **`cargo test --locked --no-fail-fast`** over the six packages: 167 test binaries passed. The one failure is `peer_discovery_test` (3 tests): `UnixListener::bind` gets "Operation not permitted" from the sandbox. That is environmental, and the PR doesn't touch peer discovery. On the first run, `remind_me_api`'s `import_export_test` also failed with "Read-only file system" when writing into `$HOME`. Its binary passed on the second run, where `HOME` pointed at scratch space.
- **Probes** were run in a scratch copy at `$TMPDIR/probe346`, with all five executed as described in the findings above:
  - string index (finding 2)
  - negative index (finding 2)
  - chunked response (finding 4)
  - Ollama Qwen3 prefix (finding 3)
  - open-time vector deletion (finding 1)

## Validation gaps
- I didn't test against real llama.cpp, llama-swap, vLLM or LM Studio servers. I couldn't confirm whether any of them sends chunked responses, how they format `index`, or the actual Qwen3 dimension. Running each live with a 32-input batch would settle finding 4's practical reach.
- `peer_discovery_test` couldn't run under this sandbox.
- `--features local-embed` wasn't built because the PR doesn't touch the ONNX path.

**Verdict: changes required.**
