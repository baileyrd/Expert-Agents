<!-- mediboard/18-r1 run 4, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 358 s, 2015497 tokens, $1.68 -->

# Review of PR 18 in baileyrd/MediBoard: Apple Health import (contract v2)

- **Base:** `0fa26d7cd4b7f3b6459eb7a578528af73d764145`
- **Reviewed head:** `27b56fe16d4cb560841e972e68ca3b4f798181e0`. I fetched `pull/18/head` and it matches this SHA.
- **Commits:** `c3d8681` (design doc) and `27b56fe` (implementation and contract v2).

**Verdict: changes required.** There are 2 P1 findings and 6 P2 findings. The worst is that re-importing a newer export while the review page is open lets a correction land on a different measurement row. I reproduced that with a synthetic probe.

## Predictions checked
- **Claim view:** unaffected. Apple Health visits have no findings, so their blood pressure readings get no category and stay out of the claim view (`repo.py:304-317`, `claim.py:82-94`).
- **Review gate and keep-reviewed rule:** the new `measurement` table is covered. `has_reviewed` now checks it (`ingest/ingest.py:25`) and a new trigger blocks deleting reviewed rows (`schema.sql:196`).
- **Migration:** `002.sql` builds the same objects as a fresh `schema.sql` (`test_migration_matches_fresh_schema`).
- **Python and SQL validators:** they agree on `type` (`_snake` only produces `[a-z0-9_]`).
- **Import output:** prints counts only. Errors carry no record values.

## Blocking findings

### 1. P1: a re-import reuses row ids, so a review from the open page corrects the wrong measurement
- **Where:** `src/paper_work/health/ingest.py:60-67`. A changed day is deleted and re-inserted, and both `visit` and `measurement` use `INTEGER PRIMARY KEY` without AUTOINCREMENT, so SQLite reuses the highest freed ids.
- **What the contract says:** `contract/api.md` "Stale pages" names this hazard ("can't change a row that only shares an id"). Its guard only covers a server restart. The import runs as a job while the server stays up, so the guard never fires.
- **Trigger:** this is the normal case. The last day of every export is partial, has the highest ids, and changes in the next export.
- **Executed trace, synthetic:**
  1. Import one day containing `heart_rate` and `step_count`.
  2. The client reads `step_count` as id 2.
  3. Import a newer export where the same unreviewed day also has `active_energy_burned`.
  4. `PATCH /api/measurement/2 {"status":"corrected","fields":{"value":8000}}` returns `200` with `heart_rate count/min 8000.0`.
- **Who is hurt:** the person reading their own record. The step count they meant to fix stays unreviewed, and heart rate becomes "corrected" to 8000 bpm.
- **Required correction:** make an id permanent across imports. Options: `AUTOINCREMENT` on `visit` and `measurement`; update the day's rows in place keyed by `(visit, type, unit)`; or have the Review contract reject a PATCH whose `(kind, id)` no longer names the row the client loaded (for example, send the row's `pdf`/`page`/`type` back with it).
- **Regression test:** the probe above, as a test in `tests/test_api.py` or `tests/test_health_ingest.py`. It must get 404 or 409 and leave `heart_rate` unchanged.

### 2. P1: an unzipped export is not covered by `.gitignore` or `phi_guard`
- **Where:** `src/paper_work/health/export.py:48`, `config.py:57`, `health/__main__.py:3`. The code and docs invite pointing `health_export` at the raw `export.xml` instead of the zip.
- **What I checked:** `.gitignore` and `scripts/phi_guard.py` cover `*.zip` and `data/` only. `phi_guard.is_blocked` returns False for all of these:
  - `apple_health_export/export.xml`
  - `apple_health_export/export_cda.xml`
  - `apple_health_export/workout-routes/route_1.gpx`
  - `apple_health_export/clinical-records/a.json`

  `git check-ignore` does not ignore `export.xml` either.
- **Trigger (source-traced):** the person unzips the export inside the checkout (anywhere but `data/`) and sets the path to the XML. `git add -A` then commits every health sample, clinical CDA document and GPS route, and the guard passes.
- **Who is hurt:** the privacy of the record.
- **Required correction:** add `apple_health_export/` (and `*.gpx`, `export*.xml`) to both lists, or refuse an XML path outside `data/`.
- **Regression test:** extend the phi_guard test with these paths.

### 3. P2: a percentage stored as a fraction is shown as "0.97 %"
- **Where:** `export.py:108,127`.
- **The vocabulary:** in HealthKit, `HKUnit.percent()` is a fraction from 0 to 1, but the export writes the unit string `"%"`. In UCUM, `%` means 1/100. Affected types include `OxygenSaturation`, `BodyFatPercentage` and walking asymmetry.
- **Executed:** a record with `unit="%" value="0.97"` is stored as `0.97` with unit `%`. The synthetic page text then reads `oxygen_saturation: mean 0.97 % (1 samples)`.
- **Who is hurt:** the person reading the chart or drawer. The page the reviewer checks against says SpO₂ was 0.97 %.
- **Required correction:** for `unit == "%"`, either scale by 100 or store a fraction unit (e.g. `1`), and document which.
- **Regression test:** an SpO₂ record imports as 97 `%` (or 0.97 `1`).
- **Still open:** confirming this on a real export (see Validation gaps).

### 4. P2: some types are summed or averaged wrongly
- **Where:** `domain/health.py:22-27,68`.
- **Insulin delivery:** HealthKit makes `HKQuantityTypeIdentifierInsulinDelivery` cumulative, but it is not in `_CUMULATIVE`. Executed: two boluses of 4 and 6 IU give `insulin_delivery: mean 5 IU` instead of a daily total of 10.
- **Physical effort:** `physical_effort` has unit `kcal/hr·kg`, an intensity rate, yet it is summed. Executed: two samples of 3 give `sum 6 kcal/hr·kg`, which does not correspond to any measured quantity.
- **Why it matters:** this breaks the contract README rule that `agg = sum` applies to cumulative types.
- **Required correction:** move `insulin_delivery` to cumulative and `physical_effort` to mean. Check the list against the HealthKit aggregation style of each type.
- **Regression test:** add both cases to `test_cumulative_types`.

### 5. P2: a config without `paths.health_export` now stops the whole app
- **Where:** `config.py:57,94`. Every `Paths` field is required.
- **Executed:** removing the key from `config.toml` raises `ConfigError("paths.health_export: missing")`. That stops the server, extract and ingest, not just the optional import.
- **Trigger:** any `config.toml` that was saved locally (Settings rewrites it) and does not pick up the new line.
- **Required correction:** make `health_export` optional with a default (the claim module already shows the pattern), and have only `paths.health` complain when it is missing.
- **Regression test:** `parse_config` succeeds without the key.

### 6. P2: no limits on what the export can contain
- **Where:** `export.py:47-56,108`; `domain/health.py:151-157`.
- **The gap:** nothing caps uncompressed size, the number of distinct `(day, type, unit, source)` keys, or the length of `type`, `unit` or `sourceName`. Memory and rows grow with whatever a corrupt or crafted XML holds. One million distinct types or source names means one million accumulators and one million unreviewed rows, and the stored `unit` is arbitrary text.
- **Source-traced:** I did not run this.
- **Required correction:** cap attribute lengths, distinct keys and uncompressed bytes, and turn an overflow into an `ExportError`.
- **Regression test:** an oversized attribute or too many keys raises `ExportError`.

### 7. P2: contract changed without recorded approval
- **What changed:** `contract/schema.sql`, `migrations/002.sql`, `api.md` (v2), `README.md` and `mock/`.
- **The problem:** the contract is frozen and needs Nano's recorded approval. The commit message itself says "Contract v2 (needs Nano's approval)". The design doc records Nano's answers on scope (all data, re-import, review), not approval of the schema or API change.
- **Required correction:** record the approval before merge.

### 8. P2: docs disagree with the code
- `README.md` says nothing about the health step, `src/paper_work/health/` or the schema upgrade. Lines 12-20 still say Settings runs only extract and ingest.
- `src/paper_work/web/README.md:14` still says "run extract / ingest".
- `store/db.py` says "run ingest to upgrade it". The health import upgrades the database too.

## Design questions for the owner
- **A reviewed partial day is frozen.** The last day of an export is always partial. Once any row of it is reviewed, a newer export can never complete it, and steps or sleep stay understated. This is documented behaviour, but it gives the person stale values.
- **Home and office blood pressure are mixed.** Imported home readings share `bp_reading`, the BP chart and office-based flags with clinic readings. Home thresholds are lower, and a VA rater weighs readings under 38 CFR 4.104 (DC 7101, Note 1) by setting. Should imported readings be marked as their own series?
- **Volume.** One "Apple Health" visit per day floods the visit list and timeline, and roughly 30 rows per day go into the review queue (the design doc lists bulk confirm only as a follow-up).
- **Sleep across midnight.** Sleep is dated by the start of each segment, so one night is split across two days.

## Optional improvements
- Open the import transaction with `BEGIN IMMEDIATE`. Today the first day's check runs before any write; the delete trigger catches a race, but the result is a crashed import.
- Check the unit on blood pressure parts and skip anything that is not `mmHg`. Today the unit is ignored: executed, `kPa` 16/10.7 is stored as 16/11.
- Count orphan top-level systolic or diastolic records in `skipped`.
- `parseInput('number', '0x1F')` returns 31.
- Unrelated to this diff: `pyproject.toml` lists the dev dependency `httpx2`, but FastAPI's test client uses `httpx`. I did not install it; please check that name.

## Checks run
- `python scripts/phi_guard.py --all`: passed (exit 0).
- `python -m pytest -q` on a scratch copy of the head: 462 passed, 1 skipped. On the base: 431 passed, 1 skipped.
  - At the head, one test (`test_ingest_runs_from_the_api`) first failed because I set a relative `PYTHONPATH`; it passed with an absolute path.
  - Environment: Python 3.14 in a scratch venv with real `httpx`, not the required 3.13.
- `node --test tests/web/*.test.mjs`: 33 of 33 passed.
- `node --test tests/web/races.e2e.mjs`: all 19 skipped, because Playwright is not installed.
- Synthetic probes in scratch space for findings 1, 3, 4 and 5 and the blood pressure unit: each reproduced the behaviour described.
- Every new health test fails at the base by construction, since the modules do not exist there.

## Validation gaps
- Python 3.13 itself was not run.
- The browser race suite needs Playwright.
- Finding 3 needs one real export with an SpO₂ record to confirm how the value and `%` unit are written.
- Whether Health ever exports blood pressure in a unit other than `mmHg` needs a real export with a non-US locale.
- Windows Stop behaviour for the `health` job was not tested.
