<!-- remind-me/346-r1 run 3, variant baseline, prompt 055f36b9a039f4c275080a0ecf8f48289f342132, model claude-opus-5-5, 2.1.316 (Claude Code); 949 s, 2726627 tokens, $1.77 -->

# Review of Rusty-Mill/rusty-remind-me PR 346: an OpenAI-compatible embedding backend

**Verdict: changes required.** I found four P2 defects and nothing at P0 or P1. Formatting, clippy and the full test run all pass, apart from 3 tests that fail the same way at base.

- **Base:** `d5528b1347054f348b3dac96f54619e603f4594c`
- **Head reviewed:** `8c7e55a64821d8f411b8578d76dfa81c4b43b3b8`. The fetched `pull/346/head` matched it.
- **Diff:** `crates/remind_me_core/src/embedder.rs`, the new `crates/remind_me_core/tests/openai_embedder_test.rs`, `README.md` and `RELEASE_NOTES.md`.

## What the change touches, and what must hold

- **Stored chunk vectors (`vec_chunks`) and their recorded identity (backend, model, dim; ADR-0002 #96 addendum).** Every stored vector must be what the configured model actually produced. When the identity changes, opening the store clears the old vectors. `openai` is a new backend value, so switching to it clears vectors as designed.
- **The new backend's wire format (`POST /v1/embeddings`).** The request is `{model, input[]}` and the reply is `data[]` of `{index, embedding}`. Entries must be placed by `index`, the count must match, the dimension must match, and an HTTP/1.1 reply from any conforming server must be either parsed or refused (RFC 9112).
- **The shared HTTP client used by Ollama.** Ollama requests and error messages must be unchanged, as the release note claims.
- **The availability cache.** It is keyed on every environment variable `resolve_embedder` reads. The PR adds both new variables to that key at `embedder.rs:829-830`.
- **Instruction prefixes.** Passage prefixes decide what is stored; query prefixes only change ranking.
- **Not touched:** sync and the outbox, the hub, tools and their schemas, and authentication. Embeddings never go over the wire (ADR-0004/0007), and no MCP or REST tool surface changes (ADR-0028/0030).

## Blocking findings

### 1. P2: a malformed vector element is stored as `0.0` and reported as success (executed)

**Where:** `embedder.rs:376`, `.map(|v| v.as_f64().unwrap_or(0.0) as f32)` inside the new `checked_vector`. Its doc comment promises "never a silent truncation or pad".

**Trigger:** llama.cpp's server serialises NaN or infinity as JSON `null` (nlohmann::json does this), and some quantised or overflowing models produce NaN.

**What happens:** In a probe against a fake server:
- `"embedding":[null,4.0]` returned `Ok([[0.0, 1.0]])`.
- `"embedding":[null,null]` returned `Ok([[0.0, 0.0]])`.

`vectors::embed_and_store` (`vectors.rs:131-139`) then stores that vector and marks the identity current. Reindex only fills memories that have no vector, so it skips this one, and nothing records that anything went wrong.

**Who is hurt:** an agent searching. The memory never comes up in semantic search, or ranks against made-up components, and nothing reports why. The Ollama path shares the code, but Go's encoder cannot emit `null` for a float. This PR is what makes the case reachable.

**Correction:** treat a non-finite or non-numeric element as an `EmbedError`, and refuse a vector whose norm is zero.

**Regression test:** in `openai_embedder_test.rs`, a reply with `null` (and a string) inside `embedding` must return `unwrap_err()`.

### 2. P2: a chunked reply is rejected with a misleading error (executed)

**Where:** `embedder.rs:304-345`, `post_json`, which this PR now uses for llama-swap, vLLM and LM Studio. It reads to EOF, splits on the first `\r\n\r\n`, and parses what follows as JSON. It never looks at `Transfer-Encoding`.

**The rule:** RFC 9112 §7.1 says a recipient MUST be able to parse the chunked transfer coding. RFC 9110 §15.2 likewise requires a client to accept 1xx interim responses.

**What happens:** a probe that sent a valid body with `Transfer-Encoding: chunked` got `Err("OpenAI-compatible server response was not JSON: trailing characters at line 1 column 2")`. The availability probe then caches "unavailable", and status reports "configured but not answering (unreachable daemon …)" (`embedder.rs:952`), which sends the operator looking for a network problem. Semantic search stays off.

**Who is hurt:** an operator whose server, or a Go proxy in front of it, chunks large replies.

**Correction:** either decode chunked bodies and skip 1xx responses, or refuse them explicitly with a message that names the cause. The README should say which servers are supported.

**Regression test:** a fake server that sends a chunked 200 reply must either produce vectors or produce an error that names the transfer coding.

### 3. P2: the release note says Ollama requests are unchanged, which is false for Qwen3 models (source-traced)

**Where:**
- `RELEASE_NOTES.md:11` says "Ollama requests and error messages are unchanged".
- `embedder.rs:114-119` adds a `"qwen3-embed"` row to `ROLE_PREFIXES`. That table is shared by every backend and matched by substring against the lowercased model name (`embedder.rs:120-131`).

**What happens:** an Ollama user with `REMIND_ME_OLLAMA_EMBED_MODEL=qwen3-embedding:0.6b` had bare queries at base. They now send `Instruct: …\nQuery:` before every query, so search ranking changes. The commit message mentions the prefix, but the release note does not, and CONTRIBUTING requires an entry for every user-visible change.

**Also stale:** `README.md:18` still lists only "Ollama or an in-process ONNX bi-encoder".

**Correction:** add a release-note line for the Qwen3 query instruction (all backends, including Ollama), reword the "unchanged" claim, and update README:18. No regression test is needed beyond the existing `qwen3_…` test.

### 4. P2: nothing tests the `openai` branch of `resolve_embedder` or its cache key (source-traced)

**Where:** `embedder.rs:864-869` (the `openai` branch) and `embedder.rs:829-830` (the cache key).

**The gap:**
- No test sets `REMIND_ME_EMBEDDING_BACKEND=openai`. `grep` finds no test that references `OPENAI_EMBED_URL_ENV`.
- Deleting either new variable from `config_fingerprint` would pass the whole suite. That is exactly the regression the function's doc comment warns about: an availability verdict reached for one URL answering for another.
- The default URL and the handling of an unset model are also untested.

**Correction:** extend `embedder_availability_cache_test.rs`, under its environment lock, with an `openai` case. A failed probe against one `REMIND_ME_OPENAI_EMBED_URL` must not answer for a different one. Add a resolve test that checks the default URL and the `identity()` the resolved embedder reports.

## Design questions for the owner (not findings)

1. **The stored identity is the model alias, not the model.** With llama-swap, `REMIND_ME_OPENAI_EMBED_MODEL` is an alias the operator defines. If the operator points `embed` at a different model with the same dimension, the identity is unchanged. The open-time check therefore keeps the old model's vectors and compares them with new-model queries, and search quietly gets worse. ADR-0002's identity triple assumed the model name identifies the model (the ADR says "this crate's only real backend is Ollama"). The endpoint is not part of the identity either. llama.cpp echoes the requested alias back in its reply, so the reply cannot detect this. Options are to document it or to record more in the identity.
2. **A misconfigured backend still clears vectors when the store opens.** `REMIND_ME_EMBEDDING_BACKEND=openai` without a model resolves to an embedder that can never work, yet the open-time check still runs and clears every stored vector. `onnx` with empty paths already behaves this way. It is worth deciding whether `resolve_embedder` should refuse a configuration it knows is unusable.

## Optional improvements

- `index` values that are not integers, such as `"1"`, silently fall back to arrival order (executed: two string indexes in reversed order were placed by position). Refusing them would be stricter.
- The `Host:` header leaves out a non-default port (RFC 9110 §7.2). This predates the PR.
- `status.rs:162` still says "set REMIND_ME_EMBEDDING_BACKEND=ollama". It already left out `onnx` before this PR.
- Add a `qwen3` row to the unit tests of `prefix_for`, next to the other model families (`embedder.rs` near line 1039).

## Failure classes checked

- **Ruled out by scope** (the diff does not touch these): half-applied changes, hidden rows surfacing, timestamps compared as text, wire versus stored row, concurrency, caller identity, and the tool surface.
- **Status that lies:** covered by finding 2 (misleading status) and finding 4 (cache key untested).
- **Retrieval admission:** covered by finding 1 and design question 1. Requests are bounded by a 5 s connect timeout and a 60 s I/O timeout.
- **Boundary skipped:** embedding writes only vectors, never memory content, so no secret-scrubbing path is involved.
- **Protocol subset:** covered by finding 2. URLs with a path, uppercase `/V1` or an IPv6 host are refused with a clear error (executed).
- **Silent success:** covered by finding 1. The count, duplicate-index, out-of-range-index, dimension, non-2xx and missing-model cases are all refused and tested.

## Checks run

| Check | Result |
| --- | --- |
| `cargo fmt --all -- --check` | Passed. |
| `cargo clippy --locked -p` (all six packages) `--all-targets -- -D warnings` | Passed. The `{{plugin-name}}` manifest errors come from a template inside the rusty_mill git checkout and do not affect the build. |
| `cargo test --locked --no-fail-fast` (all six packages) | 168 test binaries; 2485 passed and 3 failed. The failures are all in `peer_discovery_test` (`PermissionDenied`) and fail identically at base, so they are environmental. `openai_embedder_test` passed 10/10. The Ollama, availability-cache, open-reconcile and status suites passed. |
| New test file run at base | Does not compile (`OpenAiEmbedder` is unresolved), so the tests would catch removal of the backend. |
| Probes in a scratch worktree, executed | Results as quoted in findings 1 and 2 and the optional improvements. The scratch worktrees were removed afterwards. |

How the test run was set up: the full test run needed `HOME`, `RUSTUP_HOME` and `CARGO_TARGET_DIR` pointed at scratch or existing directories, because the sandboxed `$HOME` and the configured target directory are read-only. An earlier run without those settings had import tests failing with `ReadOnlyFilesystem`; they passed once the settings were in place.

## Validation gaps

- I had no real llama.cpp, llama-swap, vLLM or LM Studio server. Two things need one:
  - Whether any of them chunks `/v1/embeddings` replies (finding 2): run `curl -sv` against each and check the `Transfer-Encoding` and `Content-Length` headers.
  - Whether llama.cpp actually sends `null` for NaN (finding 1): needs a model that produces NaN.
