<!-- mediboard/5-r1 run 2, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 269 s, 844865 tokens, $1.17 -->

# Review of PR 5 (baileyrd/MediBoard): T1 extraction pipeline

**Verdict: changes required.** I found 1 P0, 2 P1 and 6 P2 defects.

- **Repository:** baileyrd/MediBoard, PR #5
- **Base:** `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`
- **Head reviewed:** `0dd1eced4b32e416430e5cbbac5163f085c22ef7`. I fetched `pull/5/head` and it equals the head SHA.
- **Scope:** 1 commit, 17 files, all new: `src/paper_work/extract/*`, `tests/fake_openai.py`, `tests/fixtures/gold.json` and `tests/test_extract_*.py`. `contract/` is not changed.

**What must hold.** The PR adds the T1 path: `all_records.json` page text, then the model prompt, then the model reply, then the validator, then `extractions.jsonl`. These rules must hold along it:
- `error` text never contains record text (`contract/README.md:40`).
- `result` passes the schema and quote checks (`:35`).
- The effective line is the last valid line, and an error line never replaces an earlier valid one (`:26-28`).
- No PHI beyond `127.0.0.1` or in logs (`:15`).
- Every quote must come from the page, and the extraction keeps the page's units and values "as written". The prompt honours units and values; the defects below are in what reaches the model and what is checked.

## Blocking findings

### 1. P0: page text is sent to any HTTP proxy the machine is configured to use
- **Where:** `src/paper_work/extract/client.py:85-87`. It calls `urllib.request.urlopen` with the default opener.
- **Trigger:** `http_proxy`/`HTTP_PROXY` is set. On Windows, which `run.ps1` targets, urllib also reads the system proxy from Internet Options in the registry, and its bypass for local addresses does not cover dotted IPs like `192.168.10.104`.
- **Scenario (executed):** I started a local listener, set `http_proxy` to it, and called `OpenAIClient("http://192.168.10.104:8080/...").complete([... "SYNTHETIC PAGE TEXT BP 150/95"])`. The proxy received `POST http://192.168.10.104:8080/v1/chat/completions` with the full page text. Every page of the corpus would go in plain HTTP to a third host the owner never chose.
- **Who is hurt:** the privacy of the whole record.
- **Fix:** build the opener with `urllib.request.build_opener(urllib.request.ProxyHandler({}))`, so no proxy is ever used.
- **Test:** set `monkeypatch.setenv("http_proxy", <fake listener>)`, point the client at a second fake server, and assert the proxy saw zero requests. It goes red today.

### 2. P1: text the model writes reaches the log and the `error` field
- **Where:** `validate.py:30-31` puts `f"schema: {e}"` into the error. For an extra key, `domain/extraction.py:121` produces `unexpected {extra}`, which is the model's own key names. That string is logged at `runner.py:76` and written to `extractions.jsonl`, where it becomes the `page_error` item in the review queue.
- **Trigger:** the reply has an extra key. The `response_format` grammar normally prevents this. It is not prevented with `--no-schema` (an option this PR adds), or when a server ignores `json_schema`.
- **Scenario (executed):** a stub replies `{"visits": [], "Synthetic Patient SSN 000-00-0000": 1}`. The log shows `LOG synthetic_a.pdf p1: schema: $: missing [], unexpected ['Synthetic Patient SSN 000-00-0000']`, and the same text is stored in `error`. A key inside a visit (`'Dx: synthetic secret condition'`) behaves the same way.
- **Contract text broken:** `contract/README.md:40` ("`error` text never contains record text"), and the validator's own docstring (`validate.py:4`).
- **Fix:** report counts or paths only, e.g. `schema: $.visits[0]: 1 unexpected key`. Do this in `_obj`, or map `ExtractionError` to a safe message in `check`. `tests/test_extraction.py:75` pins the current message text and needs updating with it.
- **Test:** add a case to `test_extract_validate.py` with an extra key holding a sentinel string, and assert the sentinel is not in `str(Invalid)`.

### 3. P1: the summary's copied conditions list can survive and pass the quote check
- **Where:** `page.py:15-17`. `_CONDITIONS_FIELD` stops at the next label or at the end of the line (`re.MULTILINE` `$`), so a list that wraps onto a second line keeps its tail.
- **Trigger:** a page with a summary but no transcription, where the `CONDITIONS MENTIONED` list wraps.
- **Scenario (executed):** the page is `"SUMMARY:\nDATE: 2/3/10\nCONDITIONS MENTIONED: hypertension, diabetes,\n  sleep apnea, depression\nMEDICATIONS: ..."`.
  - `source_text` returns `'DATE: 2/3/10\n\n  sleep apnea, depression\nMEDICATIONS: ...'`.
  - A condition `{"name": "sleep apnea", "quote": "sleep apnea"}` passes `missing_quotes`.
  - `page.py:4-5` says this field was copied from the vision prompt's example list. The leaked tail names claim categories (sleep apnea, depression), so a condition the page never states enters the review queue looking like a verified quote.
- **Who is hurt:** a rater, if a reviewer confirms the finding.
- **Fix:** drop the field up to the next `LABEL:` across lines (DOTALL, ending only at a label or the end of the text).
- **Test:** a wrapped-field case in `test_extract_page.py` asserting `"sleep apnea" not in source`.

### 4. P2: a `SUMMARY:` heading inside the transcription cuts the page short
- **Where:** `page.py:22` uses `text.partition("SUMMARY:")`, which splits at the first match anywhere, including a heading such as "DISCHARGE SUMMARY:".
- **Scenario (executed):**
  - `"TRANSCRIPTION:\nSynthetic Hospital\nDISCHARGE SUMMARY: admitted 1/2/03\nDx: ... BP 150/95. Start ..."` gives the source `'Synthetic Hospital\nDISCHARGE'`. The diagnosis, BP and medication never reach the model. The page is stored as an `ok` line, so it is never retried.
  - A truncated page containing that heading gives `is_truncated = False`, so the `truncated` command misses it.
- **Who is hurt:** a reader of the chart or claim view. Evidence from discharge summaries is lost without any warning.
- **Fix:** split on the last `SUMMARY:` that starts a line, or on a line-anchored marker.
- **Test:** the two cases above, in `test_extract_page.py`.

### 5. P2: a re-transcribed page or a new prompt is never re-extracted
- **Where:** `runner.py:64`. The set of finished pages is keyed on `(pdf, page)` only. It ignores the source text, `prompt_version` and the model.
- **Scenario (executed):** run a truncated page, re-transcribe it (which `cli.py` `truncated` tells the user to do), then run again. Result: `RunStats(skipped=1, ok=0)` with 1 model call in total. The extraction from the cut-off text stays effective, and facts from the rest of the page never arrive. The same happens when the prompt moves to v3.
- **Fix:** store a hash of the source text in the line, or in a sidecar file if the contract's line shape must not change. Adding a field to the line needs Nano's approval, because `from_json` rejects extra keys. Re-extract when the hash or `PROMPT_VERSION` differs. At minimum, give the user a `--redo` option driven by the `truncated` list.
- **Test:** the two-run case above, asserting a second model call.

### 6. P2: some hostile replies crash the run, and it then stops at the same page every time
- **Where:**
  - `validate.py:63-65` catches only `JSONDecodeError`.
  - `client.py:103` accepts a `content` that is not a string.
  - `jsonl.py:68` encodes with `ensure_ascii=False`.
  - `client.py:88` (`json.load(resp)`) has no size cap.
- **Scenario (executed)**, all with a stub Chat:
  - A `sys` value of 5000 digits raises `ValueError` (the 4300-digit limit).
  - A `"provider": "\ud800"` raises `UnicodeEncodeError` in `append_line`.
  - `content: 123` raises `TypeError`.
  - Each one crashes `run`. The lock file is cleaned up and earlier pages are kept, but at temperature 0 every rerun hits the same page and crashes again, so the rest of the corpus cannot be extracted.
- **Fix:**
  - Treat any decode or encode failure as `Invalid("schema: ...")`.
  - Require `content` to be a `str`.
  - Read at most N bytes of the response.
- **Test:** add the three cases to `test_extract_runner.py`, each asserting a single error line and a run that finishes.

### 7. P2: the eval scores numbers that differ as matches, and skips fields its docs promise
- **Where:** `evaluate.py:26` strips edge `.` from values, so lab `".5"` equals gold `"5"`. `FIELDS` (`:34-44`) does not score `dose`, lab `flag` or `quote`.
- **Scenario (executed):** gold is A1c `5` with flag `H` and dose `10 mg`. The prediction is `.5`, no flag, `100 mg`. Both `lab` and `medication` score `tp=1, fp=0`.
- **Docs made false:** `evaluate.py:5-6` ("nothing fuzzier, so a score is a floor") and `cli.py:107` ("per-field precision/recall").
- **Impact:** the model is chosen from this report, so a model that misreads decimals or drops doses or flags looks equal to one that gets them right.
- **Fix:** do not strip punctuation from `value`, `unit` or `dose`, and add `dose` and `lab_flag` fields.
- **Test:** the case above, in `test_extract_eval.py`.

### 8. P2: a torn final line that ends mid-character crashes the reader
- **Where:** `jsonl.py:51` uses `read_text`, which decodes the whole file before splitting it into lines.
- **Scenario (executed):** append a valid line, then the first part of a line cut inside `é`. `read_lines` raises `UnicodeDecodeError`. The runner repairs the file first, but `eval` (`cli.py:66`) and T2 do not.
- **Docs made false:** `jsonl.py:4` ("Readers skip it").
- **Fix:** read the bytes, split on `\n`, then decode each complete line.
- **Test:** the multibyte-tear case, in `test_extract_jsonl.py`.

### 9. P2: a negative `--limit` is accepted
- **Where:** `cli.py:102` and `runner.py:67`, where `pending[:-1]` is used.
- **Scenario (executed):** `limit=-1` over 10 pages extracts 9.
- **Fix:** reject values below 1 in argparse.
- **Test:** a CLI test asserting a negative value is rejected.

**Coverage:** none of the behaviours in findings 1–9 has a test today. Each fix above names the test that should go red without it.

## Design questions for the owner
- **LAN egress:** page text goes in plain HTTP to `192.168.10.104`. That is decided in `config.toml` at the base, but it conflicts with "nothing beyond `127.0.0.1`". Should the client refuse non-loopback hosts unless explicitly allowed, or require HTTPS?
- **Strength of the quote check:** it is case-insensitive substring matching, so a 1-character quote (`"e"`) passes (executed), and so do negated contexts such as "no hypertension".
- **Only conditions are checked:** BP, medications, labs and dates are not checked against the source. The prompt's own example values (`124/76`, Omeprazole, `LDL 162 mg/dL H`, `5/21/07`) could be copied unchecked, which is the same failure `page.py:4-5` records for the vision model. A cheap check that the numbers appear in the source would catch BP and lab copies.
- **Summary fallback is unmarked:** pages without a transcription are extracted from the vision model's summary, and their "quotes" are text that model wrote. The line does not record this, so a rater may see a quote that is not on the scanned page.
- **Configuration 4xx errors:** an error caused by configuration, such as a wrong model name, is recorded against every page instead of stopping the run.

## Optional improvements
- `cli.py:81`: `int(r["page"])` silently truncates a float page number (3.7 becomes 3).
- `http.client.IncompleteRead` is not treated as transient.
- A `kill -9` leaves a lock file that must be deleted by hand; the message already says so.

## Checks run
| Check | Result |
|---|---|
| `python scripts/phi_guard.py --all` | exit 0 |
| `python -m pytest -q` (head, scratch copy, Python 3.14.8, pytest 9.2) | 114 passed, 1 skipped |
| New `tests/test_extract_*.py` at base | all 6 modules fail to import, so they do guard the new code |
| `git diff base head -- contract` | empty |
| `node --test tests/web/*.test.mjs`, Playwright races | not applicable: `tests/web/` does not exist |
| Probes (`probe_proxy`, `probe_reply`, `probe_page`, `probe_misc` in `$TMPDIR/probes`) | synthetic only, results quoted above |

## Validation gaps
- **Python version:** I had no Python 3.13; I used 3.14.8, which meets `>=3.13`. I did not run `pip install -e ".[dev]"`; the tests ran through the `pythonpath` setting, and FastAPI and PyMuPDF are not used by this diff.
- **Windows proxy path:** I ran the proxy probe only on Linux with environment variables. A run on Windows with a system proxy set in Internet Options would confirm the registry path.
- **Live llama-server / llama-swap:** I did not run against a real model server. A run with a strict `json_schema`, a wrong model name, and the gold set would show whether extra keys can appear (finding 2), which status codes come back, and whether the prompt's example values get copied.
- **Real transcriptions:** I did not check how often "SUMMARY:" headings or wrapped `CONDITIONS MENTIONED` lists actually occur. A count of pages matching those patterns, which reports numbers only and runs only on the owner's machine, would settle it.

**Verdict: changes required.**
