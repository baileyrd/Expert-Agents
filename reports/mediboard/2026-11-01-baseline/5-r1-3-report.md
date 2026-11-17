<!-- mediboard/5-r1 run 3, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 250 s, 509938 tokens, $1.00 -->

# Review of PR 5 in baileyrd/MediBoard: T1 extraction pipeline

- **Base:** `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`
- **Reviewed head:** `0dd1eced4b32e416430e5cbbac5163f085c22ef7`. I fetched `pull/5/head` and it resolves to this SHA.
- **Scope:** one commit adding `src/paper_work/extract/*`, `tests/fake_openai.py`, `tests/fixtures/gold.json` and six test files. Nothing under `contract/` changed.

**Verdict: changes required.** I found one P0, three P1 and three P2 defects. All but one were confirmed by running probes with synthetic data; the other was traced in the code.

## Blocking findings

### 1. P0: page text goes through any configured HTTP proxy
- **Where:** `src/paper_work/extract/client.py:85-87`.
- **Cause:** `urllib.request.urlopen` uses the default opener, which honours the `http_proxy` environment variable. On Windows it also reads the system proxy from the registry.
- **Trigger:** any machine with a proxy configured, such as a corporate VPN, a debugging proxy or a system proxy. Python does not understand CIDR entries in `no_proxy`, so writing `192.168.0.0/16` there does not stop it.
- **Executed:** with `http_proxy=<local fake proxy>` and `no_proxy=localhost,127.0.0.1,192.168.0.0/16`, a `complete()` call to `http://192.168.10.104:8080/...` sent the whole request, including the synthetic page text, to the proxy.
- **Who is hurt:** the privacy of the record. Every page leaves for a third-party host.
- **Fix:** build the opener with `urllib.request.build_opener(urllib.request.ProxyHandler({}))`, and do not follow redirects.
- **Test:** set `http_proxy` to a fake proxy and assert it receives nothing while the fake model receives the request.

### 2. P1: a key chosen by the model reaches the error line and the log
- **Where:** `domain/extraction.py:120-121`. It echoes unexpected keys (`unexpected {extra}`). This PR makes that reachable from model replies through `validate.py:29-31`. The message is then written to `extractions.jsonl` (`runner.py:71`) and logged (`runner.py:76`).
- **Contract broken:** `contract/README.md` says "`error` text never contains record text". The domain docstring promises the same.
- **Executed:** the reply was a visit with one extra key, the synthetic string `SYNTH_PATIENT_Q_TESTPERSON`. The stored error was `schema: $.visits[0]: missing [], unexpected ['SYNTH_PATIENT_Q_TESTPERSON']`, and the same text appeared in the log.
- **Trigger:** a model that puts text from the page into a key. This is likely with `--no-schema`, or with a server that ignores `response_format`.
- **Fix:** report only the count of unexpected keys, or their JSON paths by index.
- **Test:** a validator test that the extra key's name appears in neither `str(Invalid)` nor the log.

### 3. P1: a re-transcribed page keeps its old facts
- **Where:** `runner.py:64`. A page counts as done by `(pdf, page)` alone, with no hash of the source text.
- **Why it matters:** `cli.py:5` tells the user to re-transcribe truncated pages. A re-transcribed page keeps its key, so `run` skips it, and `--retry-errors` skips it too because it has a valid line.
- **Executed:** old text `Dx: knee pian / BP 150/95` produced condition "knee pain" and BP 150/95. Both runs over the new text then reported `skipped=1` and sent 0 requests. The effective line still holds the old quote and BP, and the quote is no longer in the page text that T2 will store.
- **Who is hurt:** a rater, who sees a condition and BP shown as evidence that the stored page does not support.
- **Fix:** store a hash of `source_text` in each line, or keep a sidecar map, and re-extract when it changes. If the line format has to change, that needs Nano's approval.
- **Test:** run once, change the page text, run again, and assert one request is sent.

### 4. P1: a multi-line `CONDITIONS MENTIONED` field still reaches the model
- **Where:** `page.py:15-17` and `:46-47`. Without DOTALL, `.*?` stops at the end of the first line.
- **Executed:** for a summary-only page `SUMMARY:\nDATE: 1/1/20\nCONDITIONS MENTIONED:\n- hypertension\n- knee pain\nMEDICATIONS: none`, the text sent to the model was `'DATE: 1/1/20\n\n- hypertension\n- knee pain\nMEDICATIONS: none'`.
- **Why it matters:** the decoy list is exactly what the docstring says is never sent. Quotes taken from it also pass quote validation, which checks against this same source.
- **Who is hurt:** a rater, who sees conditions supported only by the vision model's copied example list.
- **Fix:** remove the field up to the next label even when that label is on a later line (DOTALL, with the label lookahead).
- **Test:** the multi-line case, asserting that neither listed condition appears in `source_text`.

### 5. P2: a `SUMMARY:` inside the transcription cuts the page off
- **Where:** `page.py:22`. `partition("SUMMARY:")` splits at the first occurrence anywhere in the text.
- **Executed:** a transcription starting `DISCHARGE SUMMARY: admitted for chest pain …` produced the source `'DISCHARGE'`. The page's facts are lost, and the page is marked done with a valid line. Also, `is_truncated` returns False for a truly truncated page that contains "DISCHARGE SUMMARY:".
- **Who is hurt:** a rater, because discharge summaries and similar notes silently drop out of the claim evidence.
- **Fix:** split on the last `SUMMARY:` that starts a line.
- **Test:** the case above.

### 6. P2: `--limit -1` widens the run
- **Where:** `runner.py:67` (`pending[:limit]`) and `cli.py:102`.
- **Executed:** with 5 pages and `limit=-1`, the run made 4 requests (`ok=4`). A negative limit means "all but the last few", which defeats the cap meant to keep bulk jobs off the shared model host.
- **Fix:** reject `limit < 0`, either in argparse or in `run`.
- **Test:** `limit=-1` raises an error.

### 7. P2: the eval over-credits and its docstring is false
- **Where:** `evaluate.py:26` and `:43`.
- **What goes wrong:**
  - `_n` strips a leading `.`, so the value ".5" equals "5".
  - It case-folds units, but UCUM units are case-sensitive, so `mU/L` equals `MU/L`.
  - The lab `flag` is never compared.
- **Executed:** gold `TSH .5 mU/L L` against a prediction of `TSH 5 MU/L null` scored `tp=1, fp=0, fn=0`.
- **Why it matters:** the docstring's claim that "a score is a floor" (`evaluate.py:5-6`) is false. The model is chosen with a metric that cannot see a tenfold value change, a unit change, or a dropped abnormal flag.
- **Fix:** leave values and units exactly as written apart from surrounding whitespace, and score `flag` (and arguably `dose`).
- **Test:** the case above should score `fp=1, fn=1`.

### Test coverage
None of the behaviours above has a test. Every new test file fails to import at the base (the modules don't exist there), so the new tests do guard what they cover. The regression tests named above belong in:
- `tests/test_extract_client.py`: finding 1.
- `tests/test_extract_validate.py`: finding 2.
- `tests/test_extract_runner.py`: findings 3 and 6.
- `tests/test_extract_page.py`: findings 4 and 5.
- `tests/test_extract_eval.py`: finding 7.

## Design questions for Nano
1. **Page text leaves the machine.** `README.md` says "PHI never leaves the machine", and the contract's non-goals say nothing beyond `127.0.0.1`. But `config.toml [model] url` sends every page over plain HTTP to `192.168.10.104`, and this PR is the first code that does it. The docs and the decided config need reconciling.
2. **Copied example values pass validation.** The prompt's example contains real-looking values (Omeprazole, BP 124/76, LDL 162 mg/dL H, 5/21/07). Only conditions are checked against the page. The repo itself records a model copying a prompt's example list, and a copied BP, drug or lab would pass. Possible fixes: placeholder examples, or checking that BP numbers, drug names and lab values appear on the page.
3. **Quote checks are loose.** They accept negated ("denies chest pain"), family-history and rule-out mentions. They don't check which visit a quote belongs to on a multi-visit page, and they ignore case. The stored quote is the model's string, not the page's.

## Optional improvements
- **Uncaught client and reply errors:** `http.client.IncompleteRead`, a `UnicodeDecodeError` in the response, and a non-string `content` are not caught. Each one stops the run with a traceback, and resuming hits the same page again.
- **Wrong URL burns the corpus:** a 404 or 401 from a mistyped URL marks every page as an error after sending it. Stopping after N identical 4xx responses in a row would prevent this.
- **Gold sets:** a hand-labelled gold set contains record text. It is covered only while it is saved under `data/`. Consider a guard pattern for it, or a warning in the CLI.

## Checks performed
- `pytest -q` at the head: **115 passed**. This ran on Python 3.14.8 in a scratch venv, because Python 3.13 isn't installed.
- `scripts/phi_guard.py --all`: **exit 0**.
- The new `tests/test_extract_*.py` against the base tree: all 6 fail at collection, as expected.
- Probes in scratch space with synthetic data only: findings 1–7 all reproduced as described.
- `node --test tests/web/*.test.mjs` and the Playwright race suite: not applicable, because `tests/web/` doesn't exist and this PR has no web code.

## Validation gaps
- **Python version:** not tested on Python 3.13.
- **Windows:** not tested, including the registry proxy path.
- **Real data and model:** I haven't checked the real format of the summary labels (whether `CONDITIONS MENTIONED` spans lines or uses lowercase labels). That, and whether the live model copies the prompt's example values, needs a gold-set run on the real corpus with the real model.

**Verdict: changes required.**
