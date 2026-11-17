<!-- mediboard/18-r1 run 5, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 330 s, 1750850 tokens, $1.55 -->

# PR review: baileyrd/MediBoard #18, Apple Health import (contract v2)

- **Base:** `0fa26d7cd4b7f3b6459eb7a578528af73d764145`
- **Reviewed head:** `27b56fe16d4cb560841e972e68ca3b4f798181e0`. The fetched `pull/18/head` matches it.

**Verdict: changes required.** One P1 and six P2 findings follow. All probes used synthetic values only. I opened no data, database or export files.

## Blocking findings

### 1. P1: a re-import can move a review onto a different measurement (wrong-row review)
- **Where:** `src/paper_work/health/ingest.py:60` and `:69–79`, with `contract/schema.sql:137` (`id INTEGER PRIMARY KEY`, no `AUTOINCREMENT`).
- **How it happens:** when a day changes, the import deletes that day's visit and inserts the rows again. SQLite hands out the freed ids again when they were the highest in the table, which is the case for the most recent day. Rows within a day are sorted by type, so if the type set changes, the same id now names a different type. The server-restart guard from PR #16 doesn't help: the import runs as a job under the same server, so the instance header doesn't change.
- **Executed trace** (synthetic):
  1. Import day 2020-01-02 with `heart_rate` (id 3) and `step_count` (id 4).
  2. The open drawer is showing id 3.
  3. Import a newer export where that day also has `active_energy_burned`. Result: `replaced`, and the rows are now 3=`active_energy_burned`, 4=`heart_rate`, 5=`step_count`.
  4. `PATCH /api/measurement/3 {"status":"confirmed"}` confirms **active_energy_burned**.
- **Why it's likely:** a newer export almost always changes the last, partial day of the previous one, and the Settings button invites re-importing "any time". Visit and `bp_reading` ids on that day are reused the same way. Scanned ingest has the same mechanism, but this PR makes it a routine, repeated path.
- **Who is hurt:** the person's review record. A confirmation they never made sits on a different row.
- **Fix:** do one of these:
  - Make ids non-reusable (`AUTOINCREMENT` on `measurement`, and on `visit`/`bp_reading`). This changes the contract, which this PR already does.
  - Have the PATCH carry an expected row identity (for example `type`, `unit` and `visit_id`, or a row hash) and answer 409 when it doesn't match.
  - Bump a database generation counter after each job and refuse reviews from before it.
- **Regression test** (`tests/test_health_ingest.py` or `test_api.py`): import, hold id N, re-import with an added type that sorts first, PATCH N, and expect it not to confirm a different type. This goes red at the head.

### 2. P2: two HealthKit types are aggregated the wrong way (meaning drift)
- **Where:** `src/paper_work/domain/health.py:22–27`.
- **`insulin_delivery` (IU)** is cumulative in HealthKit, but it isn't in `_CUMULATIVE`. *Executed:* doses of 4, 6 and 10 IU are stored as `agg=mean value=6.67`, not a daily total of 20.
- **`physical_effort`** is listed as cumulative, but HealthKit treats it as a discrete value, and its export unit is a rate (`kcal/hr·kg`). *Executed:* readings of 3.0 and 5.0 are stored as `sum 8.0`, a number with no physical meaning.
- **Who is hurt:** anyone reading a future vitals chart or `/api/measurements`.
- **Fix:** take the aggregation style from HealthKit's documented style for each type. Tests in `tests/test_health_domain.py` should assert `insulin_delivery → sum` and `physical_effort → mean`.

### 3. P2: the day's page text rounds values to 2 decimals, which hides changes and doesn't match stored values (stale source)
- **Where:** `src/paper_work/domain/health.py:120–126`, and `src/paper_work/health/ingest.py:53`, which uses that text as the "unchanged" test.
- **Executed:** a `blood_alcohol_content` value of 0.0008 % renders as "mean 0 %". A newer export where the value is 0.0012 (same sample count) produces the same text, so the outcome is `unchanged` and the stored value stays 0.0008.
- **Effect:** the drawer's "source" text can't support the stored value, and small-magnitude types (BAC, perfusion index, fractional percentages) are never refreshed.
- **Fix:** render with full precision (`repr` or `.6g`), or compare the stored rows rather than the text.
- **Test:** a re-import with a changed sub-0.005 value should come back `replaced`.

### 4. P2: an oversized correction value gives a 500 instead of a 422 (contract drift / malformed input accepted)
- **Where:** `src/paper_work/store/review.py:45` (`_number`) and `:144`, which catches only `IntegrityError`.
- **Executed:** `PATCH /api/measurement/2 {"status":"corrected","fields":{"value":1e30 as an int}}` raises `OverflowError` and the server answers 500. `contract/api.md` Review requires a finite number and a 422 for anything invalid. The web client's `Number()` and Python's validator also accept different sets of values.
- **Fix:** coerce to `float` or reject ints beyond SQLite's REAL/INTEGER range, and return 422. The existing `_int` has the same gap.
- **Test:** add `{"value": 10**30}` to the bad values in `test_measurement_filters_and_review`.

### 5. P2: the export is read with no limits (unbounded input)
- **Where:** `src/paper_work/health/export.py:53` and `:83`, and `src/paper_work/domain/health.py:154`.
- **What's missing:** there is no cap on the zip member's uncompressed size or compression ratio. `root.clear()` only frees direct children of `<HealthData>`, so one element with millions of nested children grows memory without limit. The per-day accumulators are keyed by `sourceName`, so a file with a distinct source on every record grows memory with the number of samples, which contradicts the docstring ("not with samples").
- **Status:** source-traced only.
- **Fix:** cap the member's `file_size` and its ratio to `compress_size`, cap nesting and child counts, and cap distinct sources, types and days. Report each cap as a skip count.
- **Test:** a synthetic bomb or deep-nesting fixture should raise `ExportError`.

### 6. P2: the contract change has no recorded approval
- **What changed:** `contract/schema.sql`, `migrations/002.sql`, `api.md` (now v2), `README.md` and the mocks.
- **Why it blocks:** `contract/` is frozen and changes need Nano's recorded approval. The commit message itself says "Contract v2 (**needs Nano's approval**)". The design doc records Nano's answers on scope only. Question 1 noted that "all of it" means a contract change, but nothing records approval of this specific text: the routes, the migration-on-write policy, and the API refusing a v1 database.
- **Fix:** record the approval before merging.

### 7. P2: README.md no longer matches the code
- `README.md:16` still says Settings "runs extract and ingest". It doesn't mention the Import Apple Health step or `[paths] health_export`.
- The Layout table (`:32–44`) has no row for `src/paper_work/health/`.
- The inputs paragraph doesn't mention `export.zip`.

## Design questions (for the owner)
- **Home and clinic blood pressure on one chart.** Apple Health readings are stored in `bp_reading` and appear in `/api/bp` and the BP chart with nothing marking them as home-device readings. They don't reach the claim view, because BP counts only through a visit's findings and Apple Health visits have none (`domain/claim.py:82–94`). A chart reader can't tell the two series apart. Should they be marked or filtered?
- **Category samples become minutes.** The contract decides that category samples are durations in minutes. So `apple_stand_hour.stood` ends up as "sum 60 min" per hour in which the person stood (executed), which reads as 60 minutes of standing. Event and symptom categories have the same problem.
- **Partial days freeze once reviewed.** If any row on the export's last, partial day is reviewed, the rest of that day's data never arrives in later exports (documented as kept-if-reviewed).
- **Review load.** One unreviewed visit per day plus N measurements could flood the queue and the timeline. Bulk confirm is listed only as a follow-up.

## Optional improvements
- The drawer tries to load a scan image for `apple-health` pages, gets a 404, and only then falls back to text. It could open on the text pane directly.
- `StoreError` tells the user to "run ingest to upgrade it", but the health import upgrades the database too.

## Checks performed
I couldn't get Python 3.13 (see gaps), so every Python check ran under Python 3.14.8 in a scratch clone of the head, outside the reviewed tree.

| Check | Result |
|---|---|
| `python -m pytest -q` | 462 passed, 1 skipped |
| `python scripts/phi_guard.py --all` | exit 0 |
| `node --test tests/web/*.test.mjs` | 33 passed |
| `node --test tests/web/races.e2e.mjs` | all 19 skipped (Playwright not installed) |
| Probes (synthetic, scratch only) | findings 1–4 confirmed as described; `datetime.fromisoformat` parses Apple's `YYYY-MM-DD HH:MM:SS -0500` format |

Tests for findings 1–5: none exists yet. Each finding above names the test that would go red at the head. The new test modules import code that doesn't exist at the base, so they fail there by construction.

## Validation gaps
- **Python 3.13:** downloading it was blocked by the sandbox network rules (github.com and releases.astral.sh were denied).
- **Playwright:** not installed, so the browser race tests didn't run.
- **HealthKit aggregation styles** (finding 2) are from HealthKit's documentation as I know it. I couldn't fetch the docs to confirm them, though summing a rate unit is wrong regardless.
- **Not tested:** a real Health export (localized XML name, real nesting depth and sources), Windows Stop/kill behaviour for the new `health` step, and the timeline view's performance with thousands of Apple Health visits.
