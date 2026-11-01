<!-- mediboard/6-r1 run 4, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 246 s, 769100 tokens, $1.12 -->

**Review of PR 6, baileyrd/MediBoard ("T2: normalize and ingest extractions into SQLite")**

- Base `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`, reviewed head `5fc60c1cb4245172941758ef75880c20d87ca868`. The fetched `pull/6/head` equals that head SHA.

**Verdict: changes required.** I found 3 P1 and 5 P2 defects. Each was confirmed by running synthetic data through the head code.

## Blocking findings

**1. P1: the page text is replaced under findings that were already reviewed, so the claim view shows evidence its page no longer contains.** `src/paper_work/ingest/ingest.py:72-78`, `:87-89`
- *Trigger:* `all_records.json` is regenerated (for example, the page is re-transcribed) after a review.
- `_upsert_pages` changes `page.text` in place for every page, including pages the ingest then marks `KEPT_REVIEWED`. The extraction line is keyed only on `(pdf, page)`, so an unreviewed page also gets its old extraction re-applied to the new text.
- *Executed:* the page text is "…knee pain left…". Ingest, set the finding to `confirmed`, then re-ingest the same line with page text "…no complaints…". Result: outcome `kept_reviewed`, and the confirmed finding's quote "knee pain left" now has `instr(page.text, quote) = 0`.
- *Who is hurt:* a rater reading the claim export. The confirmed finding points at a page that no longer supports it, and `quote` is documented as "verbatim from the transcription" (`schema.sql`).
- *Fix:* don't overwrite a page's text while it has reviewed rows. Report it as changed instead, by `pdf`/`page` only. For unreviewed pages, don't apply an extraction whose quotes are missing from the current text (record those as a `quote:` page_error).
- *Regression test:* in `tests/test_ingest.py`, change the page text after confirming a finding, and assert that the page text is unchanged and the outcome is reported.

**2. P1: re-ingest reuses row ids, so an id a client already holds can come to name a different finding.** `ingest.py:90-95`
- *Trigger:* the last-inserted rows belong to an unreviewed page that T1 re-extracts. This is common late in review, when only a few pages are left.
- The schema has no `AUTOINCREMENT`, and every run deletes and re-inserts every unreviewed page. When the deleted rows held the highest ids, SQLite hands those same ids to the new rows.
- *Executed:* page A is reviewed, and page B's finding id 2 is "knee pain". A new line for B is appended and ingest runs. Finding id 2 is now "hypertension".
- *Consequence:* an open review tab sends `PATCH /api/finding/2 {"status":"confirmed"}` and confirms a finding the reviewer never saw. That finding then reaches the claim view under a different category.
- *Fix:*
  - Skip pages whose effective line is unchanged, so ids stop churning on every run.
  - For changed lines, ids must never be reused. That needs `AUTOINCREMENT` or a high-water mark. Note that a schema change is a contract change and needs Nano's approval.
- *Regression test:* the two-page sequence above; assert that no new finding id equals an id from the previous run.

**3. P1: the text of a schema error can carry record text into `page_error.error`.** `src/paper_work/ingest/lines.py:74`, by way of `src/paper_work/domain/extraction.py:121`
- *Trigger:* a model reply with a key that is not in the schema.
- `_obj` lists the model's unexpected keys in its message, and this PR stores that message verbatim (`schema: {e}`).
- *Executed:* a reply with the extra key `"SYNTHETIC RECORD SENTENCE"` produced `schema: $.visits[0]: missing [], unexpected ['SYNTHETIC RECORD SENTENCE']`.
- That violates `schema.sql` ("error code from the line; never record text") and `contract/README.md` ("`error` text never contains record text"). The string then becomes the review-queue `label`.
- *Fix:* store only the path and a count, for example `schema: $.visits[0]: 1 unexpected key`. Make `ExtractionError` messages carry paths only.
- *Regression test:* assert that an extra key's text does not appear in `parse_line(...).error`.

**4. P2: a single valid line containing U+2028 or NEL is split into "bad lines", and an older extraction silently becomes effective.** `src/paper_work/ingest/__main__.py:60`
- `str.splitlines()` also splits on `\u2028`, `\u2029` and `\x85`. JSON lets those appear unescaped inside strings, and `json.dumps(ensure_ascii=False)` emits them that way.
- *Executed:* line 1 is valid (old); line 2 is valid (new) with a quote containing U+2028. Result: `bad_lines = (2, 3)`, and the effective line is the old one.
- *Who is hurt:* the person reading charts and the reviewer, who see stale facts. The contract rule "the effective line is the last valid line" is broken.
- *Fix:* split on `"\n"` only (`read_text().split("\n")`, or iterate the file).
- *Test:* the sequence above at the CLI level.

**5. P2: a single out-of-range integer aborts the whole ingest.** `ingest.py:128-131`
- The extraction schema intentionally has no BP limits, and `_int` accepts any integer, including integral floats such as `1e20`.
- *Executed:* `sys = 10**20` on one page raised `OverflowError` and rolled back every page (0 pages stored).
- Malformed model output should not block all other pages. Turn values outside SQLite's 64-bit range into a page error such as `schema: $.visits[0].bp[0].sys out of range`, either in `parse_extraction` or before the insert.
- *Test:* one page has an overflow BP and another is valid; assert the valid page is stored and the bad page becomes a `page_error`.

**6. P2: two-digit years past today are stored unflagged, contrary to `DateFlag.AMBIGUOUS`'s own definition.** `src/paper_work/domain/dates.py:63` and `:37`, against `src/paper_work/domain/models.py:38`
- `models.py` defines ambiguous as "parses more than one way (e.g. 2-digit year near the pivot)". The parser never flags that case.
- *Executed:* with pivot 30, `5/21/29` gives `(2029-05-21, None)`. That is a visit dated in the future, with no flag.
- `tests/test_dates.py:31` locks this in with `("30", 2030)` unflagged.
- *Who is hurt:* the person and the rater. Timeline order, in-service status and gaps all come from `visit.date`.
- *Fix:* flag `ambiguous` when the 20yy reading is after today (or within a set margin of the pivot), and keep storing the date.
- *Test:* `parse_visit_date("5/21/29", 30)[1] is DateFlag.AMBIGUOUS`.

**7. P2: names that are also titles are dropped as providers.** `src/paper_work/domain/providers.py:6`
- `do`, `pa`, `ms` and `col` are real surnames (for example "Do").
- *Executed:* `provider_key("Dr. Do")` returns `None`, so the visit stores no provider even though the page names one. `"Anh Do, MD"` collapses to `"anh"`.
- Provider identity matters for nexus and treatment evidence.
- *Fix:* strip titles only by position (leading honorifics or trailing credentials after a comma), or never strip the last remaining token.
- *Test:* `provider_key("Dr. Do") == "do"`.

**8. P2: `parse_category_map` silently merges colliding keys and echoes the config key in its error.** `src/paper_work/domain/conditions.py:23-24`
- *Collision (executed):* `{"Hypertension": "heart", "hypertension": "hypertension"}` gives a single entry, and the last one wins. The condition can end up in the wrong claim category with no error. Reject keys that collide after `condition_key`.
- *Error text (executed):* the error is `conditions.'SYNTHETIC CONDITION NAME': unknown category`. The key is a condition name copied from the records, and the persona's diagnostics rule forbids config keys in errors. I graded this P2 rather than P1 because `config.toml` is committed and `config.py:_categories` already echoes values.
- *Fix:* name the entry by position and drop the key from the message.
- *Tests:* the collision raises; the message does not contain the key.

**Coverage gaps (part of the P2s above):** nothing tests:
- page text changing under reviewed rows (finding 1);
- id stability across re-ingest (finding 2);
- error text from a schema failure (finding 3);
- line splitting inside the CLI (finding 4).

## Ruled out
- **Check-then-act:** the `_REVIEWED` check and the delete run in one `with conn:` transaction that has already written, and the keep-reviewed triggers abort the whole run if a review lands in between.
- **Mixed runs:** `main` reads `config.toml` twice, but contract v1 has no Settings route that writes it.
- **Widened request:** this PR adds no limits or filters.
- **Contract files:** no changes in `contract/`.
- **Unit changes:** none. Lab value, unit and flag are only stripped, and stay case-preserved.
- **Duplicated dates:** collapsed correctly.
- **Day-first readings:** flagged.

## Design questions (for Nano)
- `provider.facility` is whichever facility was seen first, and two clinicians with the same name at different facilities merge into one provider (`name_norm UNIQUE`). The visit keeps its own facility, but `/api/providers` will show only the first one.
- An extraction line has no hash of the page text, so T2 cannot tell when an extraction was made from older text. A `text_sha` on the line would settle finding 1 properly, but that needs a contract change.

## Optional improvements
- Conditions and providers left with no findings or visits after a re-ingest remain, and appear in `/api/conditions` and `/api/providers` with a count of 0.
- `connect` finds `schema.sql` relative to the config file's folder; loading it as a package resource would be more robust.
- Parse `config.toml` once in `main`.

## Checks
- `python -m pytest -q` at the head, in a scratch export: **160 passed, 1 skipped**. I used Python 3.14.8 in a scratch venv with only pytest installed; Python 3.13 isn't available here.
- The three new test files at the base: they fail to collect because the modules don't exist there, so they do guard against reverting the change.
- `python scripts/phi_guard.py --all`: **exit 0**. No changed path is blocked.
- Synthetic probes (`$TMPDIR/rev6/probes/probe.py`): they confirmed findings 1–8 as described.
- `node --test tests/web/*.test.mjs` and the Playwright race suite: not applicable, because `tests/web/` doesn't exist at the head and the PR has no web changes.

## Validation gaps
- Python 3.13 itself wasn't used, and FastAPI, uvicorn and PyMuPDF weren't installed (this PR doesn't need them).
- No Windows run.
- No real T1 output: whether T1 writes U+2028 unescaped (finding 4) depends on its `ensure_ascii` setting once T1 lands.
- The working directory has untracked files (`.mcp.json`, `.bashrc`, `.gitmodules` and others) that are not part of the PR. I didn't open or run them.
