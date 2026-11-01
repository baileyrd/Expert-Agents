<!-- mediboard/18-r1 run 1, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 484 s, 3123672 tokens, $2.29 -->

# Review of PR #18 in baileyrd/MediBoard: Apple Health import (contract v2)

- **Base:** `0fa26d7cd4b7f3b6459eb7a578528af73d764145`
- **Head:** `27b56fe16d4cb560841e972e68ca3b4f798181e0`. I fetched `pull/18/head` and it matches this SHA.
- **Commits:** `c3d8681` (design doc) and `27b56fe` (the implementation).

**Verdict: changes required.** There are 2 P1 and 8 P2 findings. All probes used synthetic values only, ran in a scratch copy outside the reviewed tree, and no finding quotes record text.

## Blocking findings

### P1-1. Unzipped Health export files are not covered by git ignore, the PHI guard, or the agent read-deny list
- **Where:** `.gitignore:10`, `scripts/phi_guard.py:12-23`, `.claude/settings.json` (unchanged); the bare-XML input is invited at `contract/README.md:58-59` and `config.toml:52`.
- **What goes wrong:** The PR blocks `*.zip` only. The import also accepts "the export XML inside it", and unzipping `export.zip` creates an `apple_health_export/` folder. That folder holds `export.xml`, `export_cda.xml`, `clinical-records/*.json` (FHIR) and `workout-routes/*.gpx` (GPS tracks).
- **Trigger (executed):** The user unzips the export anywhere in the checkout outside `data/`, for example to point `health_export` at the XML.
  - `phi_guard.is_blocked` returns ALLOWED for `apple_health_export/export.xml`, `export_cda.xml`, `clinical-records/r.json`, `workout-routes/route_1.gpx` and a bare `export.xml`.
  - `git check-ignore` reports all three paths I tried as NOT ignored.
  - The committed `.claude/settings.json` deny list has no `*.zip`, `*.xml` or `apple_health_export` entry.
- **Who is hurt:** The privacy of the full Health history and location tracks. It can be committed, and agents can read it.
- **Correction:** Block `apple_health_export/`, `export*.xml` (or every `*.xml` outside `tests/fixtures`) and `*.gpx` in `.gitignore` and `BLOCKED_NAMES`/`BLOCKED_DIRS`. Add matching `Read(...)` denies, including `*.zip`, to `.claude/settings.json`.
- **Regression test:** A `phi_guard` test that `is_blocked` holds for each of those paths. It goes red at head.

### P1-2. A re-import during a server run lets an open page's review land on a different row
- **Where:** `src/paper_work/health/ingest.py:57-62` (delete and reinsert of a changed day); guard at `src/paper_work/api/app.py:49`.
- **Why it happens:** `id INTEGER PRIMARY KEY` without AUTOINCREMENT reuses `max(id)+1`. On a re-import, the last day of the previous export is usually partial, so it always comes back as REPLACED. Its rows hold the highest ids, so they are reissued to the reinserted rows, ordered by (type, unit). The stale-page guard rotates `instance` only on a server restart. A job started from Settings runs inside the same server run, and the web app does not invalidate anything when a job finishes (`settings.js` only shows its state).
- **Probe (executed):**
  1. Import D1 and D2 with D2 = `step_count 50`. Measurement id 2 and visit id 2 belong to D2.
  2. Re-import with D2 = `active_energy_burned 300, step_count 4000`.
  3. Measurement id 2 is now `active_energy_burned 300` and visit id 2 is reused.
- **Rest of the scenario (source-traced):** Tab A has the queue open on id 2 (steps). Tab B runs "Import Apple Health". Pressing `c` in tab A PATCHes `/api/measurement/2` with a valid instance, which confirms an energy row the person never saw. The same happens for the reused visit and bp_reading ids.
- **Scope:** Ingest shares this mechanism, but this PR adds a step whose normal use replaces the newest day every time.
- **Correction:** Rotate `app.state.instance` when a step that writes the database (ingest or health) finishes, so open pages get the 409 and reload. Alternatively, make the PATCH carry and check something that identifies the row.
- **Regression test:** An API test that starts from a seeded server, runs `import_days` with a changed last day, then PATCHes the old id with the pre-import instance. It should get 409.

### P2-3. Percent types are stored and shown as fractions under the unit "%"
- **Where:** `export.py:127` stores the unit as written; `health.py:120-126` renders it.
- **Meaning:** In HealthKit, `HKUnit.percent()` is a 0–1 fraction, while in UCUM `%` means ×10⁻².
- **Probe (executed):** An `OxygenSaturation` record with `value="0.97" unit="%"` produces the page text and row `oxygen_saturation: mean 0.97 %`.
- **Who is hurt:** A person reading the chart or the drawer sees an SpO₂ of 0.97 %. The same applies to body fat, walking asymmetry and blood alcohol content (BAC). Small BAC fractions also round to `0` in the page text, so the source page no longer supports the row value. A reviewer who "corrects" the value to 97 mixes two scales in one series.
- **Correction:** Normalize `%` values to percent (×100) and say so in the contract, or store the unit as the fraction it is (UCUM `1`).
- **Regression test:** An export test where an SpO₂ value of 0.97 is stored as 97 `%`.

### P2-4. Two types are aggregated the wrong way, which departs from "sum for cumulative types"
- **Where:** `src/paper_work/domain/health.py:22-26`.
- **Insulin (executed):** HealthKit's `insulinDelivery` is cumulative, but `is_cumulative("insulin_delivery")` is False. Doses of 10 and 20 IU give `insulin_delivery: mean 15 IU` instead of a daily total of 30 IU. Insulin requirement is what DC 7913 rates.
- **Physical effort (source-traced):** `physical_effort` is in the cumulative set, but its unit is a rate (kcal/hr·kg, i.e. METs). Summing it across the day gives a number with no meaning.
- **Correction:** Derive the aggregation from HealthKit's aggregation style per identifier, as an explicit table, rather than a hand-kept set.
- **Regression test:** Parametrized `is_cumulative` cases for both types.

### P2-5. Workout minutes include pauses because the Workout's own `duration` is ignored
- **Where:** `src/paper_work/health/export.py:140-147`.
- **Probe (executed):** A Workout with `duration="30" durationUnit="min"` spanning 06:00–07:00 is stored as `workout.running: sum 60 min`.
- **Why the test missed it:** The PR's fixture uses a span equal to its duration, so it cannot tell the two apart.
- **Correction:** Use `duration` converted from `durationUnit`, and fall back to end minus start only when it is missing.
- **Regression test:** The fixture above.

### P2-6. Archive input is unbounded, and the module docstring's memory claim is false
- **Where:** `src/paper_work/health/export.py:3-4`, `53`, `83`.
- **Gaps:** There is no cap on uncompressed size or compression ratio. `root.clear()` runs only when a top-level element ends, so everything nested inside one element stays in memory. Distinct type and source keys are also unbounded.
- **Probe (executed):** A 99 KB zip that expands to 34 MB of XML, all inside one `<Workout>`, peaks at 344 MB resident memory. The same content laid out flat peaks at 16 MB. At about 3 MB of zip, a run would exhaust memory.
- **Correction:**
  - Cap `ZipInfo.file_size` and the ratio, and count bytes as they are read.
  - Clear or limit nested children.
  - Cap distinct (type, unit, source) keys and fail with a counts-only `ExportError`.
- **Regression test:** The nested probe shape should raise `ExportError`.

### P2-7. Measurement `value` beyond SQLite's integer range returns 500 instead of 422
- **Where:** `src/paper_work/store/review.py:45-46`, `:77`.
- **Contract:** `api.md` says an invalid field is 422.
- **Probe (executed):** PATCHing `value` to 9223372036854775808 or to 10⁴⁰⁰ returns 500. The web app's `parseInput('number', '1e20')` sends an integer literal that reaches the same path. `bp` `sys` shows the same 500 at base, so that part predates this PR.
- **Correction:** Coerce the value to `float` and reject ints that fail the conversion or are non-finite, with a 422.
- **Regression test:** The probe values asserting 422.

### P2-8. Frozen contract changed without Nano's recorded approval
- **Where:** `contract/api.md:1`, `contract/schema.sql:1`, `contract/migrations/002.sql`, `contract/README.md:56-74`.
- **Evidence:** The commit body itself says "Contract v2 (needs Nano's approval)". The design doc's recorded answers cover scope, re-import, Health Records and review only, not the schema or API change. The contract text nonetheless reads as adopted ("API contract v2", `user_version = 2`).
- **Correction:** Record Nano's approval, or mark the sections "proposed" the way the Settings amendment is.

### P2-9. README disagrees with the code
- **Where:** `README.md:16-20`.
- **What is wrong:** It still says the Settings view "runs extract and ingest" and that "extract and ingest use the saved paths". It has no quick-start line for `python -m paper_work.health` and does not say what the import does or what reaches the claim view. (Imported BP does not reach the claim view: it has no findings.)
- **Correction:** Document the step.

### P2-10. Coverage gaps
None of the behaviours in P1-1 through P2-7 has a test that goes red without the fix. The proposed regression tests belong in:
- `tests/test_phi_guard*` (P1-1)
- `tests/test_api.py` (P1-2, P2-7)
- `tests/test_health_export.py` (P2-3, P2-5, P2-6)
- `tests/test_health_domain.py` (P2-4)

The new tests do fail at base, but only because the modules don't exist there.

## Design questions (decided behaviour that may hurt the person)
- **Daily visits:** Each Health day is a visit. Years of data put thousands of "Apple Health" encounters on the timeline and in the visit list, and the review queue grows by 1 visit plus N measurements per day. Bulk confirm is deferred.
- **Home BP mixed with clinic BP:** Cuff readings go into `bp_reading` and are drawn as one series with clinic readings, so the chart does not tell them apart.
- **Symptoms as minutes:** Category samples are stored as minutes, so an instantly logged symptom reads `headache.severity_moderate: sum 0 min`. For a migraine log, frequency is what matters (`n`), not minutes.
- **Day attribution:** Assigning a sample to the local date of its `startDate` splits one night's sleep across two dates.

## Optional improvements
- `paths.health_export` is now required. An older `config.toml` without it stops the server and every CLI from loading. Consider making it optional.
- Change detection compares page text rounded to 2 decimals, so small value changes in fraction-scaled types can go undetected.
- A scanned PDF named `apple-health` would collide with the synthetic source name.
- Migration 002 matches a fresh v2 schema (executed: `sqlite_master` is equal and the version is 2).

## Checks performed
- **`python -m pytest -q` at head:** 462 passed, 1 skipped (`jsonschema` was not installed). This ran under Python **3.14**, in a scratch venv with `pip install -e ".[dev]"`.
- **PR's new/changed tests against base source:** the 4 new or changed test modules fail to collect, because the modules don't exist at base.
- **`python scripts/phi_guard.py --all`:** exit 0.
- **`node --test tests/web/*.test.mjs`:** 33/33 pass.
- **`node --test tests/web/races.e2e.mjs`:** 19 skipped, because Playwright is not installed.
- **Probes (executed):** id reuse, percent, insulin, workout duration, archive memory, PATCH overflow, guard and ignore coverage, migration equality.

## Validation gaps
- **Python 3.13:** not available here; I ran 3.14.
- **Browser races:** not run, since Playwright is absent.
- **Real Health export:** needed to confirm how percent values are encoded, which units the user's preferences produce (lb/kg, mmHg/kPa), and what `Workout duration` means. The run that settles it is a local import of a real export, comparing values with the Health app.
- **Windows:** not tested.
