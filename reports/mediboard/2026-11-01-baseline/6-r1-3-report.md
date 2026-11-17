<!-- mediboard/6-r1 run 3, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 283 s, 1059540 tokens, $1.26 -->

**Review of PR 6 — baileyrd/MediBoard ("T2: normalize and ingest extractions into SQLite")**

- Base: `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`
- Head reviewed: `5fc60c1cb4245172941758ef75880c20d87ca868`. I fetched `pull/6/head` and it matches this SHA. There is one commit and 11 files, all new. Nothing under `contract/` changed.

**Verdict: changes required.** The PR has three P1 findings and five P2 findings.

## Blocking findings

**1. P1: Re-ingest swaps in new page text under facts that were already reviewed.** (Source and review provenance; claim evidence)
- **Where:** `src/paper_work/ingest/ingest.py:72-78` (`_upsert_pages`, `ON CONFLICT … DO UPDATE SET text = excluded.text`).
- **Trigger:** `all_records.json` is re-transcribed, so a page keeps its `(pdf, page)` key but gets different text.
- **What happens (executed):**
  1. Ingest a synthetic page whose text contains the quote `SYNTHQUOTE alpha`, with one finding quoting it.
  2. Confirm that finding.
  3. Re-ingest with the same line, after the page text has been replaced.
  4. The outcome is `kept_reviewed` and the finding is still `confirmed`, but its quote is no longer in `page.text`.
- **Who is hurt:** `schema.sql` says the quote is "verbatim from the transcription", and every claim evidence item links to its page. A rater who opens the cited page will not find the quoted text. The same thing happens on unreviewed pages (executed): the old effective line is put back against the new text (`replaced`), and nothing reports it. That is the "stale source" failure class.
- **Fix:** Do not update `text` for a page that has reviewed rows. Report that page as its own outcome (for example "text changed under reviewed rows") and print it in the summary. For unreviewed pages, report the text change so that T1 re-extracts them.
- **Regression test:** confirm a finding, re-ingest with changed page text, and assert that the page text is unchanged and the new outcome is reported.

**2. P1: A condition name from config ends up in an exception.** (PHI containment)
- **Where:** `src/paper_work/domain/conditions.py:23` raises `ValueError(f"conditions.{name!r}: unknown category")`. Nothing catches it in `ingest/__main__.py:25`, so the traceback prints to the terminal.
- **What happens (executed):** `parse_category_map({"SYNTHETIC diagnosis name": "knees"})` produces the message `conditions.'SYNTHETIC diagnosis name': unknown category`.
- **Who is hurt:** The keys in `[conditions]` are the person's diagnoses as extracted. `config.py` names keys only and never their values; this new message breaks that pattern.
- **Fix:** Report the position or just the category, for example `conditions: entry N has unknown category`.
- **Regression test:** assert that the synthetic key is not in `str(e)`.

**3. P1: Text the model chose ends up in `page_error.error`.** (PHI containment; contract)
- **Where:** `src/paper_work/ingest/lines.py:74` stores `f"schema: {e}"`. The message `e` comes from `extraction.py:121`, which includes `unexpected {extra}`, and those are key names taken from the model's reply.
- **What happens (executed):** a visit that carries an extra key `"SYNTHETIC RECORD TEXT pt reports chest pain"` is stored as `error = "schema: $.visits[0]: missing [], unexpected ['SYNTHETIC RECORD TEXT pt reports chest pain']"`.
- **Who is hurt:** `contract/README.md` says "`error` text never contains record text", and `schema.sql` describes `page_error.error` as "error code … never record text". `api.md` shows this string as the review-queue `label`. A model that echoes the page back into a key name puts record text there.
- **Fix:** When ingest turns a failed result into an error, store only the path and the kind of failure, for example `schema: $.visits[0]: unexpected keys (1)`.
- **Regression test:** extend `test_result_breaking_contract_becomes_schema_error` with a synthetic extra key and assert that the key is not in the error.

**4. P2: One out-of-range integer in a model reply aborts the whole ingest, and the error doesn't name the page.** (Unbounded input; two validators accept different sets)
- **Where:** `src/paper_work/ingest/ingest.py:128-131`, which receives values that `extraction.py:147` (`_int`) accepted without any bound.
- **What happens (executed):** a line with `"sys": 1e20` on page 1, next to a valid line for page 2, raises `OverflowError: Python int too large to convert to SQLite INTEGER`. The run rolls back, 0 pages are stored, and nothing says which line caused it. Python accepts integers that SQLite cannot store.
- **Fix:** Convert values outside the 64-bit range into a `schema:` error for that page, or catch the error for that page and report it as one.
- **Regression test:** one oversized `sys` value next to a valid page; assert that the valid page is stored and the bad page becomes a `page_error`.

**5. P2: `provider_key` merges different people by dropping a common surname.** (Wrong derived value)
- **Where:** `src/paper_work/domain/providers.py:6`. `"do"` is on the list of titles to strip, and Do is also a common surname.
- **What happens (executed):** `"Dr. Kim Do"` and `"Dr. Kim, MD"` both become the key `kim`. Their visits end up under one provider in the timeline filter and in `/api/providers`, which shows `visit_count`, `first_date` and `last_date`.
- **Fix:** Drop `do` (and similar ambiguous tokens) only in title position, meaning after a comma or at the edges. Never drop it when it is the only remaining surname token.
- **Regression test:** `provider_key("Dr. Kim Do") != provider_key("Dr. Kim")`.

**6. P2: The ingest command reads the config file twice.** (Mixed runs)
- **Where:** `src/paper_work/ingest/__main__.py:23-25`. `load_config` reads `config.toml` once, then the file is opened again to read `[conditions]`.
- **What happens (source-traced):** if the file is saved between the two reads, the run uses the pivot and paths from one version and the categories from another.
- **Fix:** Read the file once with `tomllib` and use that one result for both.
- **Test:** Hard to make red by timing. A unit test that `main` opens the file only once is enough.

**7. P2: Conflicting `[conditions]` keys are accepted without warning.** (Malformed input accepted)
- **Where:** `src/paper_work/domain/conditions.py:21-24`.
- **What happens (executed):** `{"Sleep Apnea": "sleep_apnea", "sleep apnea.": "other"}` becomes `{'sleep apnea': other}`. The last entry wins, and that condition silently drops out of the sleep_apnea claim view.
- **Fix:** Raise an error when two keys normalize to the same name with different categories. The message must not include the name.
- **Regression test:** two such keys should raise.

**8. P2: Coverage gaps.**
- `tests/test_ingest.py:126` tests that reviewed rows survive for four tables but leaves out `lab_result`, even though `_CHILDREN` covers it.
- There is no test for a page whose text changes (finding 1), oversized integers (4), or model-chosen text in the error (3).
- Where they belong: `test_reviewed_rows_survive_reingest` and new tests in `tests/test_ingest.py`.

## Design questions for the owner

- **What "ambiguous" means for dates.** `models.py` defines AMBIGUOUS as "parses more than one way (e.g. 2-digit year near the pivot)". The PR flags only day-first fallbacks.
  - `05/06/2007` gives 2007-05-06 with no flag.
  - `1/2/29` gives 2029-01-02 with no flag, which is a future visit date.
  - Should a 2-digit year near the pivot be flagged, or a date later than today?
- **Condition names in a committed file.** `config.toml` says "No PHI here; this file is committed", yet `[conditions]`, which this PR now uses, is meant to hold the person's extracted condition names. Should that table live in a gitignored file?
- **Provider merging.** Because titles are stripped, `"Dr. Smith"` and `"Smith, RN"` merge, as the schema documents. The provider's facility is whichever one was written first. Is that acceptable for the providers view?
- **Lines with no extraction time.** A line with a valid `pdf`/`page` but no `extracted_at`, or with both `result` and `error` null, is only logged by line number. If it is the page's only line, the page never shows up in the review queue.

## Optional improvements

- When an unreviewed page is replaced, orphaned `condition` and `provider` rows remain and would appear in `/api/conditions` and `/api/providers` with a count of 0.
- `main` looks for `contract/schema.sql` next to `--config`, so a config file kept anywhere else breaks DB creation.
- Validate `extracted_at` as an ISO timestamp.
- Strip whitespace from medication `name` the same way as `dose`.

## Checks and validation gaps

| Check | Result |
|---|---|
| `python scripts/phi_guard.py --all` | ran, exit 0 |
| `python -m pytest -q` at head (scratch copy from `git archive`) | **160 passed, 1 skipped** (the skipped test needs `jsonschema`, which isn't installed) |
| The 3 new test files at base | all fail at collection because their modules don't exist at base, so they do guard the change |
| Probes (synthetic data, scratch directory) | confirmed findings 1–5 and 7; also confirmed that the delete triggers stop a cascade from removing a reviewed `lab_result` |
| `node --test tests/web/*.test.mjs`, Playwright races | not applicable: there is no `tests/web/` at head |

- **Check-then-act:** ruled out. The reviewed-row check and the deletes run in one transaction, and the delete triggers still abort if another connection commits a review in between.
- **Python version gap:** I couldn't install Python 3.13 because the sandbox refused the network download. Tests ran on Python 3.14.8, which meets `requires-python >=3.13`. The 3.13 run, and running with `jsonschema` installed so the skipped test runs, are still to do.
- **Not reviewed:** no real scans, real `all_records.json` or live model output were looked at.
