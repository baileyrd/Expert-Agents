<!-- mediboard/5-r1 run 1, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 298 s, 935212 tokens, $1.29 -->

# Review of baileyrd/MediBoard PR 5: T1 extraction pipeline

- **Base:** `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`
- **Reviewed head:** `0dd1eced4b32e416430e5cbbac5163f085c22ef7`. I fetched `pull/5/head` and it matches.
- **Scope:** 17 files, all new, under `src/paper_work/extract/` and `tests/`. Nothing in `contract/` changed.

**Verdict: changes required.** I found 10 defects: 2 P0, 3 P1 and 5 P2. "Executed" means I reproduced it in a scratch copy using only made-up data.

## Blocking findings

**1. P0: Page text goes in plain HTTP to another machine by default, and the model URL is never checked.**
- **Where:** `cli.py:47`, `client.py:85-87`, with the default from `config.toml:27`.
- **What the docs say:** `README.md:4` says "PHI never leaves the machine", and `contract/README.md:15` says "no cloud APIs". This PR is the first code that sends record text over the network.
- **Trigger: the default config.** `python -m paper_work.extract run` sends every page's transcription, unencrypted, to `192.168.10.104:8080`. Anyone on that network path can read it. `model.url` accepts any string, so a cloud address like `https://api.openai.com/...` would be accepted too. The repo has no recorded decision from Nano that the LAN box is allowed.
- **Who is hurt:** the privacy of the record.
- **Fix:** either limit `model.url` to loopback (reach the GPU box through an SSH tunnel), or record Nano's approval and update `README.md` and the contract. Either way, reject any host that isn't on an explicit allowlist.
- **Test:** a `parse_config` / `_run` test that a non-loopback URL such as `https://example.com/v1` is refused.

**2. P0: The client sends page text through any proxy set in the environment.** *(Executed.)*
- **Where:** `client.py:87`. `urllib.request.urlopen` uses the default opener, which reads `http_proxy` (and on Windows the system proxy).
- **Trigger:** `http_proxy` is set and `no_proxy` doesn't cover the model host.
- **What I ran:** I pointed `http_proxy` at a local listener with `no_proxy` cleared, and called `complete()` with a fake page text against the default-style `192.168.10.104` URL. The listener received `POST http://192.168.10.104:…/v1/chat/completions` with the page text in the body. The model server received nothing.
- **Fix:** use `urllib.request.build_opener(urllib.request.ProxyHandler({}))`.
- **Test:** a client test that sets `http_proxy` to a recording listener and asserts the fake server gets the request and the listener gets none.

**3. P1: Key names the model invents end up in the `error` field and in the log.** *(Executed.)*
- **Where:** `validate.py:31` turns `ExtractionError` into a string. That string includes `unexpected [<keys>]` from `domain/extraction.py:120-121`. `runner.py:76` then logs it.
- **What the contract says:** `contract/README.md:40` says error text never contains record text, and `api.md:17` says the same for API messages.
- **What I ran:** a reply with an extra visit key `"Synthetic Q. Patient"` produced the log line `WARNING synthetic_a.pdf p1: schema: $.visits[0]: missing [], unexpected ['Synthetic Q. Patient']`, and the same text was written to the jsonl `error`. Later steps would carry it into the review-queue label.
- **Trigger:** a model that ignores the grammar, or a run with `--no-schema`.
- **Fix:** report how many unexpected keys there were, or their JSON path, never the key text.
- **Test:** a runner test with a key `SYNTHETIC_SECRET` that asserts the string appears in neither `line.error` nor `caplog`.

**4. P1: The "CONDITIONS MENTIONED" summary field is only removed when it fits on one line.** *(Executed.)*
- **Where:** `page.py:15-17`. Without DOTALL, `.*?` stops at the end of the line.
- **What I ran:** given `SUMMARY:\nDATE: 1/2/03\nCONDITIONS MENTIONED:\nknee pain, hypertension, sleep apnea\nMEDICATIONS: …`, the text sent to the model still contained `knee pain, hypertension, sleep apnea`. A condition `{"name":"sleep apnea","quote":"sleep apnea"}` then passed `check`.
- **Why it matters:** the module's own docstring says this field is the vision model's echo of its example list and is "never sent". A claim-category condition can now pass quote checking with a quote the page never contained.
- **Trigger:** summary-only pages whose field wraps onto the next line.
- **Who is hurt:** a rater, through category chips and the claim view once the item is confirmed.
- **Fix:** remove the field up to the next label at the start of a line, or to the end of the summary, across line breaks.
- **Test:** a `test_extract_page.py` case with a multi-line field.

**5. P1: A quote made only of whitespace passes the quote check.** *(Executed.)*
- **Where:** `validate.py:47`. `_norm(" \n ")` is `""`, and `"" in haystack` is always true.
- **What I ran:** `{"name":"PTSD","quote":" \n "}` was accepted for a page that says only "flu shot given". The schema's `minLength: 1` allows `" "`.
- **Impact:** a condition with no supporting text reaches the database as a finding.
- **Fix:** reject quotes that are empty after normalising (and consider a minimum length).
- **Test:** in `test_extract_validate.py`.

**6. P2: A "SUMMARY:" heading inside the page text cuts off the rest of the page.** *(Executed.)*
- **Where:** `page.py:22` splits at the first `SUMMARY:` anywhere in the text.
- **What I ran:** for a transcription starting `DISCHARGE SUMMARY: …`, the text sent to the model was just `"DISCHARGE"`. Everything after it on the page was never sent, but the page is still recorded as a successful extraction with no error. Separately, `is_truncated` returns False for a cut-off page that contains that heading.
- **Fix:** split on the last `SUMMARY:` that starts a line, and anchor `TRANSCRIPTION:` the same way.
- **Test:** in `test_extract_page.py`.

**7. P2: A page that is re-transcribed is never extracted again.** *(Executed.)*
- **Where:** `runner.py:64` treats a page as done by `(pdf, page)` alone. `cli.py:5` tells the user to re-transcribe pages flagged as cut off, and `test_extract_runner.py` currently requires cut-off pages to be extracted as `ok`.
- **What I ran:** I extracted a cut-off page, replaced its text with the full transcription, and ran again. Result: `RunStats(skipped=1, ok=0)` and no new request to the model. The old, partial facts stay, and nothing from the rest of the page is ever extracted.
- **Fix:** leave cut-off pages pending (count them, write no line), and/or re-extract when the page text changes. Re-extracting on change needs a source hash in the line, which is a contract change and needs Nano's approval.

**8. P2: The evaluation scores a value ten times too large as a correct answer.** *(Executed.)*
- **Where:** `evaluate.py:26` strips `.` from both ends of values.
- **What I ran:** a gold creatinine of `.9` matched a prediction of `9` as a true positive (`.5` and `5.` matched the same way). The docstring's claim that "a score is a floor" is false, and this score is what decides which model to use.
- **Fix:** don't strip punctuation from lab values.
- **Test:** in `test_extract_eval.py`.

**9. P2: One bad reply stops every run at the same page.** *(Executed.)*
- **Where:** `check` accepts a reply containing a lone `\ud800` escape. `jsonl.py:68` then raises `UnicodeEncodeError` before anything is written.
- **What I ran:** two runs in a row both aborted on page 1, wrote 0 lines, and never reached page 2.
- **Fix:** in `check`, turn text that can't be encoded as UTF-8 into a `schema:` error.
- **Test:** a runner test with a fake server returning that reply.

**10. P2: A negative `--limit` is accepted and processes almost everything.** *(Executed.)*
- **Where:** `cli.py:102`, `runner.py:67`.
- **What I ran:** `--limit -1` parsed fine, and the runner then processed 4 of 5 pages (`pending[:-1]`).
- **Fix:** accept only integers ≥ 1 in argparse, and reject negatives in `run`.
- **Test:** a CLI/runner test for a negative limit.

**Tests:** all 6 new test files fail to even load at the base commit, so they do protect the new modules. None of them covers findings 1–10, and each "Test" line above is the test that should go red without the fix.

## Design questions for the owner
- **The LAN model host (see finding 1):** is the BaileyAi box meant to be inside the privacy boundary? If so, the README and contract need to say so, and the link should be encrypted or tunnelled.
- **Summary-only fallback** (`page.py:37-47`): on these pages, quotes are checked against the vision model's own summary, not the page itself. A rater could be shown model wording as if it were a "verbatim" quote. Should these pages be skipped, or flagged in review instead?
- **No check that BP, medication and lab values appear on the page.** The prompt's example values (`124/76`, `LDL 162 mg/dL H`, `Omeprazole`, `5/21/07`) look realistic, and the vision pass has already copied example text once. A model that does the same here would produce readings that nothing catches.
- **What the evaluation leaves out:** it ignores lab `flag` and medication `dose`. A model that drops every H/L flag scores the same as one that keeps them.
- **One page that always times out halts the whole run.** It raises `Unavailable` every time, so no later page is ever reached.

## Optional improvements
- **Response size:** cap the read in `client.py:88` rather than reading the whole response.
- **Page numbers:** `cli.py:81` uses `int(r["page"])`, which silently turns `3.7` into 3 and `true` into 1.
- **Uncaught errors:** `http.client.IncompleteRead` and `UnicodeDecodeError` escape the client's error handling and crash with a raw traceback (the message has no record text).
- **CI dependency:** add `jsonschema` to the dev dependencies. Without it, CI skips the test that checks `parse_extraction` against the JSON schema.

## Checks run
- `python scripts/phi_guard.py --all` on the checkout: **passed** (exit 0).
- `python -m pytest -q` on an exported copy of the head: **114 passed, 1 skipped**. The skip is the schema-parity test, which needs `jsonschema`. After installing it in the scratch venv, `tests/test_extraction.py` passed 16/16.
- The new `tests/test_extract_*.py` at the base commit: 6 errors when loading the tests, as expected.
- Probes: everything marked *executed* above, using made-up pages and the PR's `tests/fake_openai.py` on `127.0.0.1`.
- `node --test tests/web/*.test.mjs` and the Playwright suite: **not applicable**. The repo has no `tests/web`, and this PR has no web code.

## Validation gaps
- **Python version:** the tests ran on 3.14.8, not 3.13, because 3.13 isn't installed here.
- **Windows (not tested):** append and fsync behaviour, the registry/system proxy (finding 2), and `run.ps1`.
- **Real llama-server:** whether it enforces the `strict` `response_format`. That decides how often findings 3 and 9 happen in practice.
- **Real summary format:** the fixtures README says the labels are a guess. Comparing against real summaries would show how often finding 4 actually triggers.

**Verdict: changes required.**
