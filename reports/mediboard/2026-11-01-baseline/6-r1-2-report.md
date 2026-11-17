<!-- mediboard/6-r1 run 2, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 255 s, 632070 tokens, $1.09 -->

# Review of PR 6, baileyrd/MediBoard: changes required

- **Base:** `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`
- **Reviewed head:** `5fc60c1cb4245172941758ef75880c20d87ca868`. `git fetch origin pull/6/head` gave the same SHA.
- **Scope:** one commit, "T2: normalize and ingest extractions into SQLite". It adds `domain/{bp,conditions,dates,pages,providers}.py`, `ingest/{__main__,ingest,lines}.py` and three test files. Nothing under `contract/` changed. `CLAUDE.md` and `docs/designs/` don't exist at this head. All probes used synthetic values in `$TMPDIR`; no data, database or record file was opened.

## Blocking findings

**1. P1: New page text replaces the old text under facts that were extracted, and possibly confirmed, against the old text (stale source).**
`src/paper_work/ingest/ingest.py:72-78` and `:82-96`
- **What happens:** `_upsert_pages` overwrites `page.text` in place, keyed only on `(pdf, page)`. Nothing compares the new text with the text the existing visits and findings came from.
  - If the page has reviewed rows, they stay attached to the new text.
  - If it doesn't, the old extraction line is re-inserted against the new text as fresh rows.
  - Nothing is logged in either case.
- **Trigger** (executed): page `syn.pdf` p1 with text containing `SYNTHQUOTE`, ingested with a finding quoting it. Confirm the finding. Re-ingest with page text `OTHER PAGE` and the same line. The result is `KEPT_REVIEWED`, the finding is still `confirmed`, and `instr(page.text, quote) = 0`. Without the review the result is `REPLACED`, again with the quote missing from the page.
- **Who is hurt:** a rater who opens the cited page and doesn't find the evidence. Re-transcribing or re-paginating `all_records.json` moves confirmed facts onto the wrong text.
- **Fix:** when `text` changes, do not just overwrite it.
  - For a page with reviewed rows, keep the old text and report the page by `pdf`/page only, or flag it for re-review.
  - For an unreviewed page, delete its rows and do not apply an extraction made from the old text. That needs a text hash or `extracted_at` comparison, so it may need an owner decision.
- **Regression test:** the two-step scenario above. It must assert that no confirmed finding ends up on a page whose text lacks its quote.

**2. P1: Record text from a model reply can be stored in `page_error.error`.**
`src/paper_work/ingest/lines.py:74`, which builds its message with `src/paper_work/domain/extraction.py:121`
- **What happens:** `f"schema: {e}"` embeds `ExtractionError`, and that message lists the *keys* found in the model's object. The keys are chosen by the model, so they are hostile input.
- **Trigger** (executed): a result `{"visits": [], "SYNTH Patient Name 01/02/1970": 1}` produces `"schema: $: missing [], unexpected ['SYNTH Patient Name 01/02/1970']"`. That string goes into `page_error.error` and from there into the review-queue `label`.
- **Contract:** `contract/README.md` says "`error` text never contains record text". `schema.sql:141` says `-- error code from the line; never record text`.
- **Who is hurt:** the privacy of the record. This PR is what makes the base message reachable as a stored, displayed error.
- **Fix:** emit only the JSON path and counts (for example `unexpected 1 key`), either in `lines.py` or in `_obj`.
- **Regression test:** a line with a sentinel extra key, asserting the sentinel is absent from `ExtractionLine.error` and from the `page_error` row.

**3. P1: Condition names from `config.toml` appear in an exception message.**
`src/paper_work/domain/conditions.py:23`
- **What happens:** `f"conditions.{name!r}: unknown category"` puts the `[conditions]` key into the error. That key is one of the person's conditions as extracted.
- **Trigger** (executed): `{"SYNTH condition name": "knees"}` produces `"conditions.'SYNTH condition name': unknown category"`. That is an uncaught traceback on the console when running `python -m paper_work.ingest`.
- **Who is hurt:** the privacy of the record.
- **Fix:** give the position or index of the bad entry, not the key.
- **Regression test:** extend `test_category_map_rejects_unknown_categories` to assert the key text is absent from the message.

**4. P2: An oversized integer in one model reply aborts the whole ingest.**
`src/paper_work/ingest/ingest.py:129`. Nothing bounds the input before it gets here.
- **Trigger** (executed): a reply with `bp.sys = 10**20` (a float such as `1e20` behaves the same once `_int` converts it) raises `OverflowError: Python int too large to convert to SQLite INTEGER`. The run rolls back and stores 0 pages. Every later run fails the same way until someone edits `extractions.jsonl` by hand.
- **Who is hurt:** the person, who can't load any record.
- **Fix:** bound `sys`/`dia` to the SQLite 64-bit range when the line is parsed, and turn a violation into a `schema:` page error for that page.
- **Regression test:** a huge-integer line alongside a valid line for another page. The valid page must still ingest, and the bad one must become a `page_error`.

**5. P2: Provider dedupe drops non-ASCII names and merges distinct people.**
`src/paper_work/domain/providers.py:17` and `:5-8`
- **What happens:** `[a-z0-9]+` deletes every non-ASCII letter, and the title list removes tokens that are also real surnames (`do`, `ms`, `pa`, `col`, `lt`).
- **Trigger** (executed):
  - `"Д-р Иванов"` and `"Dr. 王伟"` both give `None`. The visit stores `provider_id NULL`, and because `visit` has no raw provider column, the name is lost entirely.
  - `"Dr. Kim Do"` and `"Kim, DO"` both give `kim`.
  - `"Dr. José Núñez"` gives `ez jos n`.
  - Sorting the tokens also merges `"John Lee"` with `"Lee John"`.
- **Who is hurt:** the person reading the provider view and timeline.
- **Fix:** use Unicode-aware tokenizing (`\w` after NFKD normalization), and strip titles only in a leading or trailing position, not anywhere in the name.
- **Regression test:** parametrize `test_provider_key_keeps_distinct_people_apart` and `test_provider_key_none_when_no_name` with the cases above.

**6. P2: Two-digit years produce future visit dates with no flag, contradicting `models.py`.**
`src/paper_work/domain/dates.py:63`
- **What happens:** with pivot 30, `1/5/28` gives `(2028-01-05, None)` and `1/5/30` gives `(2030-01-05, None)` (executed). Both are after today (2026-11-01).
- **Contract:** `models.py:38` says `AMBIGUOUS` covers "2-digit year near the pivot", but the parser never raises it for that case.
- **Who is hurt:** a rater and the person reading the timeline. An impossible date is shown, and `in_service` will later be computed from it.
- **Fix:** either flag `ambiguous` when a two-digit year resolves to a date after the run date (or within some margin of the pivot), or correct the `models.py` comment.
- **Regression test:** `parse_visit_date("1/5/28", 30)[1] == DateFlag.AMBIGUOUS`.

**7. P2: Coverage gaps (executed).**
- **`lab_result` is untested in the review gate:** `tests/test_ingest.py:126` omits `lab_result`, and `CIV_1_LINE` has no labs. I removed `"lab_result"` from `_CHILDREN` in a scratch copy and all 34 ingest tests still passed. The schema trigger would still stop the data loss, but the whole run would then abort.
- **No regression tests exist yet for:**
  - page text changing under existing rows (finding 1);
  - text leaking into error messages (findings 2 and 3);
  - an oversized integer (finding 4).
- **Where they belong:** add `lab_result` with a lab row in the fixture, plus the tests named above, in `tests/test_ingest.py` and `tests/test_normalize.py`.

## Design questions for the owner
- **Row ids can come to name a different row.** The frozen schema has no `AUTOINCREMENT`, and every ingest run deletes and re-inserts all unreviewed pages, even unchanged ones.
  - Executed: an unchanged re-run moved `bp_reading` ids 1,2 to 3,4.
  - Executed: re-extracting one page gave the new reading `sys=99` the old ids, `bp_reading` 2 and `visit` 2, which previously named `sys=150`.
  - Once T3 exists, a review queue loaded before the re-run would confirm a different reading.
  - Options: skip lines whose `extracted_at` hasn't changed, and/or approve an `AUTOINCREMENT` contract change, and/or require T3 to guard reviews with a generation or etag.
- **The person's conditions would go into git.** `[conditions]` is read from `config.toml`, which is committed ("No PHI here"), but its keys are the person's extracted condition names.
- **Dates on kept pages go stale.** When the pivot changes, a page kept for a reviewed child still has an *unreviewed* visit, and that visit's `date` is not re-derived from `date_raw`.

## Optional improvements
- `ingest/__main__.py:23,25` parses `config.toml` twice. Fold `[conditions]` into `Config` so it is read once and validated by `ConfigError`.
- `connect` loads `schema.sql` from `config_path.parent / "contract"`, so `--config` pointing anywhere else breaks DB creation.
- `two_digit_year_pivot` isn't range-checked (0–99).
- `REPLACED` also counts pages that didn't change, so the summary overstates the work done.
- A whitespace-only `test`, med `name` or condition `name` is stored as-is.

## Ruled out
- **Check-then-act:** the review check and the delete run in one transaction (an implicit BEGIN at the first upsert write), and the keep-reviewed triggers abort any delete of a reviewed row.
- **Meaning drift in units, values or BP:** lab `unit`, `value` and `flag` are only stripped, so their case is kept. BP is stored as read and only flagged.
- **Effective-line choice:** matches `contract/README.md`.
- **Categories:** re-applied on every run, as the contract requires.

## Checks
- `python3 -I scripts/phi_guard.py --all`: exit 0.
- `python -m pytest -q` (scratch venv, Python 3.14.8, pytest only, `src` on `pythonpath`): 161 passed.
- The three new test files at the base: collection errors (the modules don't exist there), so they fail without this change.
- Probes in `$TMPDIR/probe` reproduced findings 1–6 and the id-reuse trace.
- The mutation probe left `tests/test_ingest.py` at 34 passed, confirming finding 7.

## Validation gaps
- Python 3.13 is not installed here, so everything ran under 3.14.8; I did not do `pip install -e ".[dev]"`.
- Node and Playwright suites: not applicable, because `tests/web/` doesn't exist and this diff has no web code.
- `tests/seed.py` and `tests/fake_openai.py` don't exist at this head.
- I didn't read the PR description. The review is based on the repository state.

**Verdict: changes required.**
