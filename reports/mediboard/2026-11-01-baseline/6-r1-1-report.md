<!-- mediboard/6-r1 run 1, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 255 s, 538196 tokens, $1.09 -->

I found 6 defects in PR 6, 3 of them P1, so the verdict is **changes required**.

**PR:** baileyrd/MediBoard #6 · base `866e4bd0c4cb65227c0da2e675ff931e6b6371d0` · head `5fc60c1cb4245172941758ef75880c20d87ca868`. I fetched `pull/6/head` and it matches the head SHA.

The PR adds step 2 (ingest): code that turns page text and model extractions into the SQLite database, plus helpers for dates, blood pressure, providers, conditions and pages. It does not change `contract/`. There is no `CLAUDE.md`, `docs/designs/` or `contract/migrations/` at this head, and `api.md` has no Settings or pipeline section yet.

All probes used synthetic values (`synthetic_*.pdf`, made-up names) in a scratch copy. I never opened `data/` or any real record.

## Blocking findings

**1. P1 — A reviewed fact stays cited to a page whose text has been replaced** (`src/paper_work/ingest/ingest.py:72-78`, `:90-95`)
- **What happens:** Page text is overwritten whenever `all_records.json` changes, including pages that hold reviewed rows. Unreviewed pages also get the old extraction re-inserted against the new text. Nothing checks that a finding's quote is still on its page.
- **Trigger (executed):** page `synthetic_a.pdf` p1 says "knee pain". Ingest, then confirm the finding. Re-transcribe the page so it says "shoulder strain" and ingest again. The page is kept as reviewed and the finding stays confirmed, but its quote is no longer in the page text (`quote in current page text: False`). A second run with only unreviewed rows also inserts a finding whose quote is not on its page.
- **Who is hurt:** a rater opening the cited page finds no support for the confirmed evidence.
- **Fix:** don't change the text of a page that has reviewed rows; report it as a conflict instead. For pages being replaced, check each finding's quote against the current text and store failures as a `quote:` page_error.
- **Tests:** `test_page_text_change_keeps_reviewed_evidence_supported` and `test_stale_quote_becomes_page_error` in `tests/test_ingest.py`.

**2. P1 — Re-ingest renumbers rows and can reuse an old id for a different fact** (`ingest.py:90`, `:95`)
- **What happens:** Every unreviewed page is deleted and re-inserted on every run, even when nothing changed. The schema uses `INTEGER PRIMARY KEY` without AUTOINCREMENT, so SQLite reuses the highest freed id.
- **Trigger (executed):** two pages. Run 1 gives finding 2 = "knee pain" on p2. Confirm visit p1. Run 2 with a new extraction for p2: finding 2 is now "hypertension". An unchanged rerun also moves visit ids from [1,2] to [3,4].
- **Who is hurt:** any client still holding id 2 (an open review tab, an Undo) would apply its `PATCH /api/finding/2` to a different condition. The API (step 3) isn't in this repo yet; the ids are produced here.
- **Fix:** leave pages whose effective extraction and page text are unchanged alone. For changed pages, stable ids need AUTOINCREMENT or a generation column, which is a schema change and needs Nano's approval.
- **Test:** ids are stable across an identical rerun, and a freed id never names a new row.

**3. P1 — Text from the model reaches `page_error.error`** (`src/paper_work/ingest/lines.py:74`, via `domain/extraction.py:121`)
- **What happens:** A result that fails the schema is stored as `f"schema: {e}"`. That message lists the unexpected JSON keys verbatim, and those keys come from the model's reply.
- **Trigger (executed):** a result with an extra key named after a synthetic patient is stored as `schema: $: missing [], unexpected ['SYNTH Jane Testperson']`.
- **Why it matters:** this breaks `contract/README.md` ("`error` text never contains record text") and the schema comment on `page_error.error`. The API serves this field as the review queue `label`.
- **Fix:** report only counts or JSON paths of unexpected keys, never the key text.
- **Test:** a line with a sentinel key, asserting the sentinel is absent from `page_error.error`.

**4. P2 — One oversized integer aborts the whole ingest** (`ingest.py:129`, `lines.py` `_page`, `domain/pages.py` `_page`)
- **What happens:** the extraction schema puts no bound on integers, so a value like `sys = 10**20` passes validation and then fails in SQLite.
- **Trigger (executed):** `sys = 10**20` raises `OverflowError` and rolls back every page (`pages stored 0`). `page: 10**20` on a line fails the same way.
- **Fix:** bound integers to SQLite's 64-bit range. Out-of-range BP becomes a `schema:` error for that page; an out-of-range line page becomes a bad line.
- **Test:** a huge `sys` produces a page_error for that page while other pages still ingest.

**5. P2 — A surname that matches a title is dropped, and the visit loses its provider** (`src/paper_work/domain/providers.py:18`)
- **What happens:** "Do" is in the title list, so `"Dr. Do"` gives a key of `None` and the visit's `provider_id` is NULL. The visit table has no raw provider column, so the name written on the page is lost (executed).
- **Fix:** don't strip the last remaining name token, or strip titles only in prefix/suffix position. If no key remains, still keep the name.
- **Test:** `provider_key("Dr. Do") is not None`.

**6. P2 — Rows from superseded extractions stay** (`ingest.py:146-163`)
- **What happens:** after a re-extraction, the old condition rows remain. In probe #2, "knee pain" was left with no findings (executed). A provider's display name and facility stay fixed from the first extraction, even if that extraction was later replaced.
- **Who is hurt:** the conditions and providers lists show names and facilities the current record does not support.
- **Fix:** after replacing, delete providers and conditions that nothing references.
- **Test:** zero orphans after a page is replaced.

**Coverage:** none of the behaviours above has a test. The tests needed are named under each finding.

## Ruled out
- **Check-then-act:** the page upsert takes the write lock inside the same `with conn:` transaction before the reviewed-row check, and the delete triggers back it up.
- **Meaning drift:** lab value, unit and flag and BP values are stored as written; only whitespace is stripped and units are not case-folded.
- **Two validators:** `parse_records` agrees with the schema CHECKs.
- **Widened request:** not applicable; the PR adds no routes.

## Design questions for the owner
- The 2-digit year pivot of 30 stores future years (`1/2/29` → 2029) without a flag.
- A day-first fallback such as `17/04/2018` stores a date with only an `ambiguous` flag, while `04/05/2007` is read month-first with no flag.
- Removing titles from the provider key (as the contract says) merges "Dr. Testwell", "SGT Testwell" and "Testwell, PA-C" across roles and facilities.
- `[conditions]` in the committed `config.toml` would put the person's list of conditions into git.
- Pages removed from `all_records.json` keep their rows.

## Optional improvements
- `__main__.py:23-25` reads the config file twice. That's harmless now, but it becomes a mixed-run risk once there is a Settings route.
- `connect()` builds the schema with `executescript`, so `user_version = 1` is saved before the rest of the schema; a failure part-way leaves a half-built DB marked as current (source-traced).
- `21-May-07` (DD-MMM-YY) comes out unparsed.
- Medication `name` and lab `test` are not stripped of whitespace.

## Checks
- `python -m pytest -q` on a scratch copy of the head: **161 passed**. This used Python 3.14.8 with pytest; Python 3.13 isn't installed, so the required version is a validation gap.
- `python scripts/phi_guard.py --all` on the checkout: **exit 0**.
- `node --test tests/web/*.test.mjs` and Playwright: not applicable, since `tests/web/` doesn't exist.
- The new tests import modules that don't exist at the base, so they would fail there (source-traced, not run at the base).

**Validation gaps:** Python 3.13; real scans, real transcriptions and a live model, which would show how often extra keys, huge integers or re-transcribed pages actually occur.

**Verdict: changes required.**
