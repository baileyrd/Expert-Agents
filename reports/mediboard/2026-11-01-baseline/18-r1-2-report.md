<!-- mediboard/18-r1 run 2, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 418 s, 2216721 tokens, $1.92 -->

# Review of baileyrd/MediBoard PR 18: Apple Health import (contract v2)

- **Base:** `0fa26d7cd4b7f3b6459eb7a578528af73d764145`
- **Reviewed head:** `27b56fe16d4cb560841e972e68ca3b4f798181e0`. I fetched `pull/18/head` and it matches this SHA.
- **Verdict: changes required.** There are 2 P1 and 6 P2 findings.

No record data was opened. Every probe below used synthetic values only, in `$TMPDIR`.

## Blocking findings

### 1. P1: Re-running the Health import can make a review land on a different row
- **Where:** `src/paper_work/health/ingest.py:56-62` and `:66-80`. The `measurement`, `visit` and `bp_reading` tables use `INTEGER PRIMARY KEY` without AUTOINCREMENT, so SQLite reuses ids. The only stale-page guard is the per-restart `instance` id (`contract/api.md` Review, "Stale pages").
- **Trigger:** this happens on most re-imports. The last day of an export is a partial day. On first import it gets the highest ids. A newer export changes that day, and its rows are normally still unreviewed, so the import deletes and re-inserts them. The new rows reuse the same ids, sorted by type.
- **Failure scenario (executed through the API):**
  1. Import a day holding `heart_rate` and `step_count`.
  2. The review queue shows id 1 as `heart_rate: mean 72 count/min`.
  3. Settings → Import Apple Health runs with a newer export that adds `body_mass`. The server is not restarted.
  4. `PATCH /api/measurement/1 {"status":"confirmed"}` with the valid instance header returns `200` and confirms `{type: body_mass, unit: kg, value: 80}`.
- **Who is hurt:** the person's review record. A confirmation they never made now protects that row from re-import. Visit ids are reused the same way. Scanned ingest has the same mechanism at the base, but this step makes it reachable on most re-imports.
- **Correction:** when a job that writes the database finishes, rotate `app.state.instance`, or otherwise make PATCHes from before the job return `409`. Alternatively, stop reusing ids, which needs a contract change. Add an API test: read the queue, run `import_days` on the same database with a changed day, PATCH the old id, and expect `409` with nothing changed. That test fails at the head.

### 2. P1: The export.xml the PR accepts is not covered by `.gitignore` or `phi_guard`
- **Where:** `.gitignore:7-11` and `scripts/phi_guard.py:12-23` block `*.zip` only. `health/export.py:48` accepts a bare `.xml`, and `config.py:57` / `contract/README.md` say "export.zip (or the export XML inside it)".
- **Failure scenario (executed):** the person unzips the export anywhere in the repo outside `data/` (unzipping creates `apple_health_export/`).
  - `is_blocked()` returns False, and `git check-ignore` does not match, for `apple_health_export/export.xml`, `export_cda.xml`, `workout-routes/*.gpx` and `clinical-records/*.json`.
  - A `git add -A` would commit the full health history, including GPS routes and FHIR records.
- **Who is hurt:** the privacy of the record.
- **Correction:** add `apple_health_export/`, `export*.xml` (or `*.xml` outside known paths) and `*.gpx` to both files, keeping them in sync. Add a `phi_guard` test for those paths.

### 3. P2: Percent quantities are stored and shown 100× too small
- **Where:** `health/export.py:126-127` keeps the raw value with unit `%`.
- **Why it's wrong:** HealthKit's percent unit holds a fraction (1.0 = 100%), and the export writes `unit="%" value="0.97"`. In UCUM, `%` means 10⁻², so the stored pair says 0.97 percent.
- **Executed:** two oxygen-saturation samples (0.97, 0.89) render as `oxygen_saturation: mean 0.93 % (2 samples, 0.89 to 0.97)`. The same applies to body fat, walking asymmetry, walking double support and blood alcohol.
- **Who is hurt:** the person reading the drawer, the queue, or any future vitals chart. A reviewer who "corrects" one value to 93 also leaves two scales in a single series.
- **Correction:** scale HealthKit `%` values by 100 on import, or store a unit that says it is a fraction. Either way, document it in `contract/README.md`. Add a test with a `%` record that expects 97 / `%`.

### 4. P2: Some types are aggregated against their HealthKit meaning
- **Where:** `domain/health.py:22-27`.
- **Executed:**
  - `HKQuantityTypeIdentifierInsulinDelivery` (IU, cumulative in HealthKit) is averaged: two doses of 4 and 6 IU show as `insulin_delivery: mean 5 IU`. The daily insulin total is the clinically relevant number (38 CFR 4.119, DC 7913).
  - `physical_effort` has unit kcal/hr·kg, which is a rate, yet it is summed into `sum 8 kcal/hr·kg`. A sum of a rate has no meaning.
- **Correction:** add `insulin_delivery` to the cumulative set, remove `physical_effort` from it, and check the list against HealthKit's aggregation style for each type. Add a test for each.

### 5. P2: Rounded page text hides real changes and misstates stored values
- **Where:** `domain/health.py:118-124` rounds to 2 decimals. `health/ingest.py:53-55` uses that text as the change key, while the database stores the unrounded REAL. The README claim "unchanged text means unchanged rows" is false.
- **Executed:** a day stored with `blood_alcohol_content` 0.0004 is re-imported with 0.0001. The outcome is `UNCHANGED`, the stored value stays 0.0004, and the page shown as the source reads `mean 0 %`.
- **Who is hurt:** anyone checking a row against its source page.
- **Correction:** render with enough significant digits (e.g. `repr`/`:g` at full precision), or compare the rows themselves. Add a re-import test with a change below 0.005.

### 6. P2: The contract was changed without Nano's recorded approval
- **What changed:** `contract/schema.sql`, `migrations/002.sql`, `api.md` (retitled "API contract v2", new kind, routes and Settings shape), `README.md` and the mocks.
- **What the record says:** the commit message says "needs Nano's approval". The design doc records Nano's answers to four scope questions, not approval of the schema or routes, but its status line reads "implemented as contract v2". The earlier precedent (`api.md:115`, PR #15) labelled the amendment "Proposed … needs Nano's approval".
- **Correction:** record the approval, or mark the v2 text as proposed until it is given.

### 7. P2: The README doesn't cover the new step or the database upgrade
- `README.md:8-24` still lists only extract and ingest, and the Settings text says "runs extract and ingest".
- It never mentions `[paths] health_export`, `python -m paper_work.health`, or that after this PR the server refuses an existing v1 database (`store/db.py:27-28`) until ingest or the import runs.

### 8. P2: No size, ratio or per-element limits on the export
- **Where:** `health/export.py:48-66` and `:70-83`.
- **Gap (source-traced):** the zip member is streamed with no limit on uncompressed size or compression ratio. Memory is only freed when a top-level element closes, so a single `<Record>` or `<Workout>` with an unbounded number of children grows without limit until the job process runs out of memory. A highly compressed member also keeps the step busy for an unbounded time.
- **Correction:** check `ZipInfo.file_size` and the ratio before opening, and cap the number of children and depth per element. Add tests with a synthetic oversized member and a deeply nested element.

### Coverage
The new tests fail at the base only because the modules don't exist yet (collection errors), so on their own they guard nothing beyond that. Each finding above needs its named test:
- `tests/test_api.py`: finding 1.
- `tests/test_phi_guard*`: finding 2.
- `tests/test_health_export.py`: findings 3, 4 and 8.
- `tests/test_health_ingest.py`: finding 5.

## Ruled out
- **Claim evidence:** Apple Health visits have no findings, so their BP and measurements never reach the claim view, the export or `unconfirmed_count` (`repo.py:304-317`).
- **Check-then-act:** a review committed between `has_reviewed` and the `DELETE` hits the keep-reviewed triggers. The single-transaction import then aborts loudly and saves nothing.
- **Text in diagnostics:** errors and summaries contain counts, element tags and line numbers only.
- **Other paths:** the 409 stale-page guard covers restarts. The Python and JS `value` validators agree on finite numbers. The migration matches `schema.sql`, and the routes match the mocks.

## Design questions for the owner
- **Mixed BP chart:** home Apple Health BP readings appear in the same chart series as clinic readings. That was decided, but a reader can't tell them apart without the citation.
- **Symptom categories:** these become summed "minutes". Headache severity, for example, is often 0 min, and the episode count survives only in `n`. Migraine claims (DC 8100) depend on how often attacks happen.
- **Timeline and queue volume:** one "Apple Health" visit per day floods the timeline, and roughly 30 rows per day go into the review queue.
- **Day attribution:** the local date of `startDate` may follow the export device's time zone rather than the sample's.

## Optional improvements
- `health_export` is required in `config.py:91`. A config without it fails for every step, including extract and ingest (executed: `ConfigError paths.health_export: missing`). It could be optional.
- The `(type, unit, agg)` accumulator key is wider than the database's `UNIQUE(visit_id, type, unit)`. A quantity record and a NotApplicable category record with the same base name crash the import with an IntegrityError (executed, synthetic). This is unlikely with real exports.
- Workout minutes use end minus start and ignore pauses; the `duration` attribute would be more accurate.

## Checks
- `python scripts/phi_guard.py --all`: passed (exit 0).
- `python -m pytest -q`: 462 passed, 1 skipped. This was on **Python 3.14** in a scratch venv; Python 3.13 isn't installed here.
- `node --test tests/web/*.test.mjs`: 33 passed.
- `node --test tests/web/races.e2e.mjs`: all 19 skipped because Playwright isn't installed (validation gap).
- Synthetic probes (`$TMPDIR/probes/probe_health.py`, `probe_api.py`): results as cited above.
- PR tests run against the base in a scratch worktree: collection errors as expected. The worktree has been removed.

## Validation gaps
- Python 3.13 and the Playwright race suite were not run.
- No real Health export was available to confirm:
  - the export's `%` convention on the current iOS;
  - the time zone written into `startDate`;
  - the unit written for BP;
  - whether food correlations are duplicated at the top level.

  Each needs one run against a synthetic-but-real-format export from a test device.
