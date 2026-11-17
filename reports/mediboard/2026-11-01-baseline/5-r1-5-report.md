<!-- mediboard/5-r1 run 5, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 193 s, 603189 tokens, $0.88 -->

# Review of PR 5 (baileyrd/MediBoard): T1 extraction pipeline

- **Base:** `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`
- **Head reviewed:** `0dd1eced4b32e416430e5cbbac5163f085c22ef7`. The fetched `pull/5/head` matches this SHA.
- **Scope:** one commit adding `src/paper_work/extract/*`, `tests/fake_openai.py`, `tests/fixtures/gold.json` and six test files. Nothing under `contract/` changed.

**Verdict: changes required.** One P0, three P1 and four P2 findings. Five of the eight came out of probes I ran; the other three are traced from the source.

## Blocking findings

### 1. P0: Page text goes through any configured HTTP proxy
- **Where:** `src/paper_work/extract/client.py:85-87`
- **Trigger:** `http_proxy` or `HTTP_PROXY` is set. On Windows, which `run.ps1` targets, a proxy in the system Internet Settings also counts: when the environment has none, `urllib` reads the registry. A `<local>` bypass doesn't cover a dotted IP like `192.168.10.104`.
- **Cause:** `urllib.request.urlopen` installs a `ProxyHandler` by default.
- **Probe (executed):** I set `http_proxy` to a local listener and pointed the client at `192.0.2.10:8080`. The listener received the full POST, including the synthetic page text, and the request never went to the configured host.
- **Who is hurt:** the privacy of the whole record. Every page is sent to a third party with no warning.
- **Fix:** build the opener with `urllib.request.build_opener(urllib.request.ProxyHandler({}))`. Optionally, also refuse any URL scheme other than `http` or `https`.
- **Test to add:** a variant of my probe in `tests/test_extract_client.py`. Set `http_proxy` with `monkeypatch` to a second fake server and assert that server gets no requests.

### 2. P1: Record text from a model reply reaches `extractions.jsonl` errors and the log
- **Where:** `runner.py:84-88` and `:76`, through `validate.py:30-31` into `domain/extraction.py` `_obj`, which writes `unexpected {extra}`.
- **Trigger:** the model emits an unexpected key. That's possible with `--no-schema`, or with any server that ignores `response_format`.
- **Cause:** the schema error message includes the key names verbatim. This PR makes that path reachable from model output.
- **Probe (executed):** a reply with the extra key `"SYNTHETIC_NAME Jane Q Testcase DOB 1/1/80"` produced:
  - the error line `schema: $.visits[0]: missing [], unexpected ['SYNTHETIC_NAME Jane Q Testcase DOB 1/1/80']`;
  - the same string in the `WARNING` log line.
- **Contract:** `contract/README.md` says "`error` text never contains record text", and `api.md` says the same for messages.
- **Who is hurt:** privacy. The text lands in logs and error lines, which flow into the review queue as `page_error`.
- **Fix:** report a count or the JSON path only, e.g. `unexpected 1 key(s)`. Either fix it in `validate.check` by mapping the message, or (with owner sign-off, since the domain parser is T0 code) change `_obj` itself.
- **Test to add:** in `test_extract_validate.py`, an extra key holding a marker string; assert the marker is absent from `str(Invalid)`.

### 3. P1: A re-transcribed page keeps its old extraction
- **Where:** `runner.py:64-66`
- **Cause:** a page counts as done if its `(pdf, page)` key appears in the output. Nothing ties the stored line to the text it was taken from.
- **Probe (executed):**
  1. Extract a truncated page whose text contains "knee pain".
  2. Replace that page's text with a complete transcription that doesn't mention it.
  3. Re-run: the result is `RunStats(skipped=1, ok=0)`. The model isn't called again, and the stored condition still quotes "knee pain".
- **Why it matters:** the PR's own `truncated` command tells the user to re-transcribe these pages, which leads straight into this path.
- **Who is hurt:** a rater. T2 will pair the new page text with a quote that page doesn't contain, so a fact gets shown as evidence its page doesn't support.
- **Fix:** record a hash of the source text and redo the page when the hash differs. Adding a field to the line shape is a contract change and needs Nano's approval. A sidecar file keyed by `(pdf, page)` avoids that. Until then, at minimum re-check the stored quotes against the current source and redo the page if any are missing.
- **Test to add:** in `test_extract_runner.py`, the two-run scenario above; assert the second run calls the model and appends a new line.

### 4. P1: A multi-line `CONDITIONS MENTIONED` list stays in the model input
- **Where:** `page.py:15-17`
- **Cause:** with `re.MULTILINE` and no `DOTALL`, the lookahead `$` matches at the end of the label's own line. Only the text `CONDITIONS MENTIONED:` is removed, and list items on the following lines survive.
- **Probe (executed):** for a summary-only page whose list continues on the next lines (`- sleep apnea`, `- depression`), the source passed to the model kept both items. A condition quoting "sleep apnea" then passed `check`.
- **Why it matters:** the module docstring says this list was copied from the prompt's example and isn't reliable. Leaving it in turns invented conditions into validated, quoted findings, and these names match claim categories.
- **Fix:** cut to the next label or the end of the summary, i.e. `(?s)CONDITIONS MENTIONED:.*?(?=\b(?:labels):|\Z)`.
- **Test to add:** in `test_extract_page.py`, a multi-line list; assert no list item survives.
- **Caveat:** the real summary layout is unconfirmed (see Validation gaps).

### 5. P2: A `SUMMARY:` heading inside the transcription cuts the page short
- **Where:** `page.py:22`
- **Cause:** `partition` splits at the first `SUMMARY:` anywhere in the text.
- **Probe (executed):** a transcription containing `DISCHARGE SUMMARY:` came back as `'Ward note 1/2/03\nDISCHARGE'`. Everything after the heading was dropped without any warning.
- **Also:** `is_truncated` reports such a page as complete even when the vision pass was cut off.
- **Who is hurt:** a rater, through missing evidence.
- **Fix:** split only on the last `SUMMARY:` that starts a line, e.g. `rpartition` on `"\nSUMMARY:"`.
- **Test to add:** in `test_extract_page.py`, a `DISCHARGE SUMMARY:` case.

### 6. P2: `--limit` accepts negative values
- **Where:** `cli.py:102` and `runner.py:67`
- **Probe (executed):** `limit=-1` processed 4 of 5 pages (`pending[:-1]`). An invalid value becomes "almost everything", and a bulk job runs on the shared model server.
- **Fix:** reject values below 1 in argparse and in `run`.
- **Test to add:** in `test_extract_runner.py`, assert that `-1` raises.

### 7. P2: Model replies are used before they are bounded or type-checked, and one bad reply can stop the run permanently
- **Lone surrogate (executed):** a valid reply with `"\ud83d"` in a lab value makes `append_line` (`jsonl.py:68`) raise `UnicodeEncodeError` and stop the run. At temperature 0 the model gives the same reply each time, so every re-run stops at the same page.
- **Non-string content (executed):** a list in `message.content` makes `validate.py:58` raise `TypeError`, with the same effect.
- **Unbounded read (source-traced):** `client.py:88` reads the whole response with no size cap.
- **Fix:**
  - In `_parse`, treat content that isn't a string as malformed.
  - In `check`, map unencodable strings to `schema: ...`.
  - Cap the read with `resp.read(N)`.
- **Tests to add:** cases for each of these in `test_extract_validate.py` and `test_extract_client.py`.

### 8. P2: Coverage
None of findings 1–7 has a test that goes red without its fix. The tests should go where I've named them in each finding.

## Design questions for the owner
- **Plain HTTP to a LAN host.** `config.toml` (from T0) sends page text in plain HTTP to `192.168.10.104`. The contract says "no network service beyond `127.0.0.1`". The setting was decided in T0, so this is the owner's call, but every page crosses the LAN unencrypted.
- **Quotes from the vision summary.** On summary-only pages, quotes are checked against the vision model's own digest rather than the record. A rater would see a "verbatim" quote that may not appear on the scan.
- **What a quote proves.** The quote check ignores case and doesn't relate the quote to the condition name, so a quote like "no hypertension" passes for "hypertension".
- **Pages that always time out.** A page that times out every time stops the run after six attempts. Every re-run then starts on that same page, so the run can't get past it without `--limit` or editing the corpus.

## Optional improvements
- `http.client.IncompleteRead` isn't caught in `client.py:94`.
- A 408 response is treated as rejected rather than retried.
- The evaluation ignores the lab `flag` field.
- An integer too large for SQLite is accepted and would only fail later, at T2 ingest.

## Checks run
- `python scripts/phi_guard.py --all`: passed (exit 0).
- `python -m pytest -q`: 115 passed. This ran on **Python 3.14.8** in a scratch environment with only pytest installed, not Python 3.13 with `pip install -e ".[dev]"`.
- New test files at the base commit: all six fail to collect (the modules don't exist there), so each is red without this PR.
- Probes (synthetic data, scratch directory, `-I`): confirmed findings 1–7 as described above; the unbounded read in finding 7 is source-traced, not probed.
- `node --test tests/web/*.test.mjs`: not applicable, since there is no `tests/web` directory at the head.

## Validation gaps
- **Python 3.13:** not available here; the suite ran on 3.14 instead.
- **Windows registry proxy:** traced from the CPython `urllib` docs, not run on Windows.
- **Real summary layout:** findings 4 and 5 depend on how real summaries are laid out. Check them against the legacy `records.field` label list or one real page, viewed locally.
- **Live model:** not tested. It's unknown whether llama-server's grammar enforcement prevents the extra keys in finding 2 when `--no-schema` isn't used.
