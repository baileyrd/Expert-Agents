<!-- remind-me/346-r2 run 5, variant baseline, prompt 9ebbf17270d0951f7da4838b44f4b1ca5ffbb629, model claude-opus-5-5, 2.1.314 (Claude Code); 180 s, 887609 tokens, $0.91 -->

I'm requesting changes on PR 346: both earlier findings are fixed, but I found one P1 and four P2 defects. I couldn't build the workspace here because GitHub fetches are denied, so none of the repo's tests or clippy have been run.

**Repository:** Rusty-Mill/rusty-remind-me · **PR:** 346 · **Base:** d5528b1347054f348b3dac96f54619e603f4594c · **Head:** 9daba9f88644bffd4c41612121cf5e1eeccda7e5 (I fetched `pull/346/head` and it matches the head SHA). The PR has two commits (8c7e55a6, 9daba9f8) and changes `embedder.rs`, the new `openai_embedder_test.rs`, `README.md` and `RELEASE_NOTES.md`.

### Earlier findings, re-checked at this head
- **K1, malformed index falls back to array position: fixed.** `embedder.rs:524-530` now requires `as_u64`. I ran the head's code in a scratch copy: `"0"`, `0.5`, `-1`, `null` and `0.0` are all refused with "entry 0 has no valid index". `a_missing_or_malformed_index_is_an_error` covers this.
- **K2, chunked responses not decoded: fixed.** `response_body` and `dechunk` are at `embedder.rs:340-393`. Run against the head's code, a chunked body with chunk extensions, a trailer and out-of-order indexes decodes to `[[1,0],[0,1]]`, and a truncated one is an error. `a_chunked_response_is_decoded` and `a_truncated_chunked_response_is_an_error` cover it.

### What must hold
- **Vectors and `embedding_meta`:** every stored vector comes from the same embedder the query uses (backend, model, dimension, endpoint). Otherwise `reconcile_embedding_meta` (`vectors.rs:551`) has to clear the stored vectors.
- **Embedder output:** one finite, L2-normalised vector per input, in input order. Anything malformed is an error, never zero-filled.
- **Status:** `embedding_status` names the real reason the backend is down.
- **Sync, hub, tools and auth:** none of these change. Vectors aren't replicated, and the PR adds no tool or argument, so the replication, containment and tool-contract classes don't apply.

### Blocking findings

**F1 (P1): The `openai` embedder's identity leaves out the endpoint, so vectors from a different server are never cleared** (`embedder.rs:566-572`)
- **Trigger** (*executed*): `OpenAiEmbedder::new("embed","http://hostA:8080",1024).identity() == …("http://hostB:8080"…)` returns `true`.
- **Scenario:** an operator indexes through llama-swap on host A with the alias `embed`. They then point `REMIND_ME_OPENAI_EMBED_URL` at host B, whose `embed` is a different 1024-dim model. The identity matches, so `reconcile_embedding_meta` keeps A's vectors. B's query vectors are then ranked against them. Semantic search returns unrelated memories with no error or status, and agents act on what it returns.
- **Why this backend is exposed:** the PR's own docs call the model "the name the server (or llama-swap's config) routes on". That is a user-chosen alias, not a canonical tag like Ollama's.
- **Fix:** put the normalised URL into the `openai` identity, for example by recording `url` in `embedding_meta` or folding it into `model`.
- **Regression test:** two `OpenAiEmbedder`s with the same model and dim but different URLs must have different identities. Plus an open-reconcile test showing that changing the URL clears `vec_chunks`.

**F2 (P2): Non-numeric embedding elements are stored as 0.0** (`embedder.rs:418`, `checked_vector`)
- **Trigger** (*executed*): `[null,1.0]` becomes `Ok([[0,1]])`, `[null,null]` becomes `Ok([[0,0]])` (a zero vector), and `["x",1.0]` becomes `Ok([[0,1]])`.
- **Why the new backend reaches it:** llama.cpp writes JSON with nlohmann::json, which serialises NaN as `null`. That is from the library's documentation; I didn't run a live server. Ollama's Go encoder refuses NaN, so the old path never saw this.
- **Impact:** the bad vector is normalised and stored (`vectors.rs` has no finiteness check). The memory then becomes unreachable or mis-ranked in semantic search, silently.
- **Fix:** refuse any element that isn't a finite number, and refuse an all-zero vector.
- **Regression test:** `a_null_or_non_numeric_element_is_an_error` in `openai_embedder_test.rs`.

**F3 (P2): A release note is now false, and an Ollama behaviour change is undocumented** (`RELEASE_NOTES.md:11`, `embedder.rs:113-119`)
- The new `qwen3-embed` prefix matches by substring for every backend. I ran `prefix_for("qwen3-embedding:0.6b", Query)` and it returns the `Instruct: …\nQuery:` prefix. So Ollama users on the standard `qwen3-embedding:*` tags now send different query text.
- That contradicts the note's "Ollama requests … are otherwise unchanged". The prefix isn't mentioned in RELEASE_NOTES or the README at all.
- **Fix:** add a "Changed" entry naming the prefix and both backends. Also add an `ollama_embedder_test.rs` case asserting the Ollama query text for `qwen3-embedding:0.6b`.

**F4 (P2): Status gives the wrong reason when the required model is unset** (`embedder.rs:993-1000`)
- *Source-traced:* with `BACKEND=openai` and no `REMIND_ME_OPENAI_EMBED_MODEL`, `embed` fails with a message naming that variable. `available_embedder` throws that message away with `.is_ok()`.
- `remind_me_server_status` then reports "configured but not answering (unreachable daemon, or an unconfigured/unloadable ONNX model)". That sends the operator to check the daemon instead of the missing model.
- **Fix:** keep the probe's `EmbedError` in the cache and show it, or check for an empty model in `embedding_status`.
- **Regression test:** an env-locked status test asserting that the reason names `REMIND_ME_OPENAI_EMBED_MODEL`.

**F5 (P2): The `openai` arm of `resolve_embedder` and the two new fingerprint variables are untested** (`embedder.rs:909-914`, `874-875`)
- You can delete the `"openai"` arm, the `DEFAULT_OPENAI_EMBED_URL` fallback, or `OPENAI_EMBED_URL_ENV` from `config_fingerprint`, and no test goes red.
- **Where the tests belong:** `embedder_availability_cache_test.rs`, under its `ENV_LOCK`. Cover resolving with only `BACKEND=openai` (default URL, identity `openai`), and that changing `REMIND_ME_OPENAI_EMBED_URL` invalidates the cached verdict.

### Design questions
- ADR-0002's decision names Ollama `/api/embed` as the embedding backend. This PR adds a third backend without an ADR or an amendment to that one. Should the owner record it?
- Model names are matched by substring (`contains("qwen3-embed")`), so a renamed or fine-tuned GGUF can silently gain or lose the prefix. Would an explicit prefix setting suit agents better?

### Optional improvements
- The Ollama backend now decodes chunked responses, but only the openai suite exercises the shared function. A one-line Ollama chunked test would pin it.
- A non-2xx response whose chunked body is malformed reports "malformed HTTP response" and hides the status code (*executed* with a truncated 503). It's still an error, just a less useful one.
- A URL with a path prefix (`http://h:8080/llama/v1`) fails with "invalid port". It's refused, not dropped, but the README could say only `host:port[/v1]` is supported.
- `status.rs:162` still says `=ollama` only. That predates this PR (onnx was already missing).

### Checks and validation gaps
| Check | Result |
|---|---|
| `cargo fmt --all -- --check` | **passed** (exit 0) |
| `cargo clippy …` (six packages) | **not run**: GitHub fetches for `rusty_mill`/`rusty_oauth` were denied and `~/.cargo` is read-only |
| `cargo test --locked` (six packages, or `--test openai_embedder_test` etc.) | **not run**, same reason |
| Scratch probe: the head's `embedder.rs` code copied verbatim into a crate built against `serde_json`, run against loopback fake servers | **ran**; results quoted in K1, K2 and F1–F3 |
| New tests at base | `openai_embedder_test.rs` can't compile at base because `OpenAiEmbedder` doesn't exist there, so it does guard the change (I didn't run it there) |

**Gaps:**
- Run the repo suites (`-p remind_me_core --test openai_embedder_test --test ollama_embedder_test --test embedder_availability_cache_test --test status_test`) and clippy somewhere with GitHub access.
- Run a live llama.cpp or llama-swap server, including a NaN-producing model, to confirm F2 end to end.

**Verdict: changes required**
