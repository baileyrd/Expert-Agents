<!-- mediboard/5-r1 run 4, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 219 s, 596003 tokens, $0.94 -->

I'm requesting changes on PR 5. It has eight blocking findings: one P0, two P1 and five P2. All of them are confirmed by synthetic probes, apart from one that I traced through the source.

**Repository:** baileyrd/MediBoard · **PR:** 5 · **Base:** `866e4bd0c4cb65227c0da2e675ff931e6b6371d0` · **Reviewed head:** `0dd1eced4b32e416430e5cbbac5163f085c22ef7` (I fetched `pull/5/head` and it matches).

The PR adds the extraction step (T1): the prompt, the model client, the reply checker, the resumable runner and the model evaluation. I found no clinical value whose unit or code changes on the way into `extractions.jsonl`, because values are copied as strings. The problems are in what gets sent out, what text ends up in logs, what the quote check lets through, and which pages get extracted again.

## Blocking findings

**1. P0: page text goes to any HTTP proxy in the environment, and on Windows to the system proxy.**
`src/paper_work/extract/client.py:87` calls `urllib.request.urlopen`, which applies `HTTP_PROXY` by default. On Windows it also uses the proxy set in the registry's Internet Settings.
- **Trigger:** a proxy is set in the environment or in Windows (corporate network, VPN client, Fiddler). The model host never needs to change.
- **Scenario (executed):** I set `HTTP_PROXY` to a stand-in proxy on 127.0.0.1 and pointed the model URL at `192.0.2.10`. The stand-in proxy received the whole request, synthetic page text included.
- **Also:** Python's `no_proxy` does not understand CIDR ranges, so an exclusion like `192.168.0.0/16` does not protect the configured LAN host.
- **Who is hurt:** the privacy of the whole record, which goes to a third party.
- **Fix:** build the client with `urllib.request.build_opener(urllib.request.ProxyHandler({}))`, and reject a URL whose host is not on an explicit allow-list.
- **Regression test:** a test client with `HTTP_PROXY` set to a fake proxy must reach the fake model server, and the proxy must see no request.

**2. P1: text from the model reply reaches the error column and the log.**
- **Where:** `src/paper_work/domain/extraction.py:121` builds `unexpected [<keys>]` from the reply's own keys. This PR is the first to route that message into the `error` field (`validate.py:31`) and into `log.warning` (`runner.py:76`).
- **Scenario (executed):** a reply whose visit object has the key `"SYNTH-NAME 1/2/03 BP 150/95"` produces the error `schema: $.visits[0]: missing [], unexpected ['SYNTH-NAME 1/2/03 BP 150/95']`. That message is written to the jsonl, logged, and later stored as a `page_error`.
- **Trigger:** the reply comes back without the schema's grammar applied. That happens with `--no-schema`, which the model-choice workflow uses, or with a server that ignores `response_format`.
- **Contract:** this breaks `contract/README.md`: "`error` text never contains record text". It also makes the claim in the `extraction.py:4` docstring ("safe to log") false.
- **Fix:** report the number of unexpected keys, or their JSON paths by index, never the key names.
- **Regression test:** in `test_extract_validate.py`, assert that a sentinel key never appears in `str(Invalid)`.

**3. P1: a page that was transcribed again keeps facts from its old text.**
- **Where:** `runner.py:64` decides a page is done by `(pdf, page)` alone.
- **Scenario (executed):** a page was extracted with the quote "SYNTHCOND A". It was then transcribed again, which is exactly what the new `truncated` command tells the user to do. The rerun returns `skipped=1`, and the stored quote no longer appears in the new page text.
- **Effect:** T2 will pair those findings with page text that does not support them, and reviews will be made against that page.
- **Fix:** record a hash of the source text with each line and re-extract when it changes. That changes the line shape, so it needs Nano's approval of the contract change. A fallback that stays within the contract: re-extract a done page when the quotes in its stored result fail `missing_quotes` against the current source, and add a `--redo pdf:page` option.
- **Regression test:** run, change the page text, run again, and expect a new line for that page.

**4. P2: a "SUMMARY:" heading inside the transcription cuts the page short.**
- **Where:** `page.py:22` splits the page at the first "SUMMARY:" it finds.
- **Scenario (executed):** for a transcription containing a `DISCHARGE SUMMARY:` heading, the text sent to the model is `"Synthetic Hospital\nDISCHARGE"`, so every diagnosis and blood pressure after the heading is lost.
- **Also:** a cut-off page containing that heading is not flagged by `is_truncated`, so the `truncated` command never lists it.
- **Who is hurt:** the claim evidence is missing those facts.
- **Fix:** split at the last `SUMMARY:` that starts a line (a regex anchored at line start).
- **Regression test:** in `test_extract_page.py`, use a synthetic page with a `DISCHARGE SUMMARY:` heading, both with and without a summary block after it.

**5. P2: a quote of only whitespace passes the quote check.**
- **Where:** `validate.py:47`. A quote of `" "` passes the schema's `minLength`, normalises to an empty string, and an empty string is found in any text.
- **Scenario (executed):** a condition with the quote `" "` was accepted as validated.
- **Effect:** a condition the page does not support goes to review looking like it passed the quote check.
- **Fix:** reject a quote that is empty after normalising. A minimum length is worth considering too.
- **Regression test:** in `test_extract_validate.py`.

**6. P2: some model replies crash the run instead of being recorded as error lines.**
- **Where:** `validate.py:64` and `runner.py` `_ask` catch too little, and `client.py:103` passes on content that is not a string.
- **Scenario (executed):** each of these escaped `extract_page` uncaught:
  - an integer with more than 4300 digits (`ValueError`);
  - deeply nested JSON (`RecursionError`);
  - message content that is an object rather than a string (`TypeError`).
- **Effect:** with temperature 0, the same page fails the same way on every rerun, so the run is permanently stuck on it. The contract says error lines are never dropped.
- **Fix:** put a size cap on the content, check it is a string, and turn `ValueError` and `RecursionError` into a `schema:` error line.
- **Regression test:** the three replies above, run through the fake server.

**7. P2: the evaluation counts a tenfold lab error as a match.**
- **Where:** `evaluate.py:26` strips leading and trailing "." from values.
- **Scenario (executed):** a gold value of ".5" against a predicted "5" scored `tp=1, fp=0`. Leading-dot values like ".9" are common in older lab records.
- **Effect:** the score used to choose the model is inflated, and the docstring's statement that the score "is a floor" is false. Case-folding units (UCUM units are case-sensitive) has the same effect on a smaller scale.
- **Fix:** do not strip punctuation from lab values, and do not case-fold units.
- **Regression test:** in `test_extract_eval.py`.

**8. P2: `--limit -1` runs almost everything.**
- **Where:** `cli.py:102` accepts any integer, and `runner.py:67` uses it as `pending[:limit]`.
- **Scenario (executed):** with `limit=-1` on 10 pages, 9 were processed. That is a bulk run on the shared model server, which `config.toml` warns against.
- **Fix:** reject a limit below 1.
- **Regression test:** in `test_extract_runner.py` or a CLI test.

**Also blocking (P2, docs):** `README.md:4` says "PHI never leaves the machine". With the committed default (`config.toml:27`, plain HTTP to `192.168.10.104`), this PR sends every page to another host. Either the README or the default has to change.

**Coverage:** none of the behaviours above has a test. The PR's new tests fail at the base only because the extract modules don't exist there yet, which says nothing about these behaviours. Each finding names where its test belongs.

## Design questions for Nano
- **Model on another machine:** extraction sends page text, unencrypted, to a model server on another machine on the LAN. The contract says "no network service beyond 127.0.0.1, no cloud APIs", which arguably rules this out. Should it require loopback or an explicit opt-in, and should it use TLS?
- **Pages with no transcription:** quotes are checked against the vision model's own summary (`page.py` `source_text`). A quote taken from a model-written digest is not text from the record that a rater can trace to the page.
- **Prompt example values:** blood pressure, medications and labs have no check against the page. The prompt's example values (BP 124/76, Omeprazole, LDL 162, "knee pain") could be copied into results the same way the vision model copied its example list. One option is to use non-clinical placeholders or add substring checks for those fields.
- **Negated findings:** the quote check is a plain substring match, so "denies chest pain" would support a "chest pain" condition.

## Optional improvements
- `jsonl.from_json` (`cls(**raw)`) and `cli.load_pages` don't check field types. A hand-edited line or record raises an unhelpful exception.
- Add `*.lock` and `gold*.json` to the PHI guard as a backstop for files kept outside `data/`.

## Checks
- `python scripts/phi_guard.py --all`: exit 0.
- `python -m pytest -q`: 115 passed. This ran on Python 3.14.8 in a scratch virtual environment with only pytest installed, not the specified 3.13 with `pip install -e ".[dev]"`.
- The six new test files run against the base: they fail to import, because the modules are new.
- Probes `probe1.py` and `probe_proxy.py`, run in scratch space with synthetic values only: results as quoted above.
- `node --test tests/web/*.test.mjs`: there is no `tests/web` folder at this head.

## Validation gaps
- **Python 3.13 and FastAPI:** not available, so the full dev install was not tested.
- **Windows system proxy:** I traced the registry proxy behaviour in finding 1 from the source but did not run it. Settling it needs a Windows host with a system proxy set.
- **Multi-line condition lists in summaries:** if the real summary puts its condition list on separate lines, only the label is removed and the list reaches the model, where quotes would pass the check. I confirmed this with a probe. The fixture README calls the summary format "a best guess", so it needs checking against the real format.
- **Copied prompt examples:** whether the model copies the prompt's example values needs a gold-set run against the live model.

## Verdict
**Changes required.**
