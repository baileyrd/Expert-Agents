<!-- mediboard/18-r1 run 3, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 398 s, 2404257 tokens, $1.92 -->

# Review of PR 18 (baileyrd/MediBoard): Apple Health import, contract v2

- **Base:** `0fa26d7cd4b7f3b6459eb7a578528af73d764145`
- **Head reviewed:** `27b56fe16d4cb560841e972e68ca3b4f798181e0`. I fetched `pull/18/head` and it matches.
- **Verdict:** **changes required**. There are two P1 and seven P2 findings.

All probes used synthetic values only. They are in `$TMPDIR/probes/test_probes.py`. I opened no PHI paths. The working tree has local changes to `.claude/settings.json`, but the PR does not touch it, so I reviewed the version committed at the head.

## Blocking findings

### 1. P1: A review can land on a different row after a Health re-import
**Where:** `src/paper_work/health/ingest.py:60-62`, together with `src/paper_work/api/routes.py:199-201` and `contract/api.md:92-97`.

- **Cause:** When a day's data changes, the import deletes that day's visit and inserts it again. Row ids are plain `INTEGER PRIMARY KEY`, so SQLite reuses the highest ids that were just freed. The stale-page guard only changes its instance id when the server restarts, but the `health` step runs inside the live server.
- **Typical trigger:** This is the ordinary re-import case. The newest day in an export is usually incomplete, so a newer export changes it, and its rows hold the highest ids.
- **Executed scenario:**
  1. Import an export where 2020-01-02 has only `step_count` = 4000. The reviewer's page shows id 1 as `step_count 4000`.
  2. Import a newer export where the same day also has `active_energy_burned`.
  3. The open page sends `PATCH /api/measurement/1 {"status":"confirmed"}` with the current instance header.
  4. Result: `200`, and the row confirmed is `active_energy_burned 350`.
- **Who is hurt:** The reviewer's decision is applied to a value they never saw. Even when the day keeps the same types, the value under a reused id changes (4000 becomes 9000).
- **Scope:** The same exposure already existed for T2 ingest. This PR makes it routine for every re-import.
- **Correction:** One option is for the server to rotate `instance` whenever a step finishes that may have changed rows. The web app already reloads on a 409. Another option is to stop ids being reused, for example `AUTOINCREMENT` in `002.sql` and `schema.sql`, which needs a contract decision.
- **Test that should go red without the fix:** an API test that runs `import_days` between a GET and a PATCH, and asserts a 409 or 404 rather than a 200 on the other row.

### 2. P1: An unzipped export inside the checkout is not covered by git, the PHI guard, or the read-deny list
**Where:** `scripts/phi_guard.py:12-23`, `.gitignore:10-11`, and `.claude/settings.json` as committed.

- The PR supports pointing `health_export` directly at the export's XML file (`health/export.py:47-50`, `contract/README.md`).
- The PR adds `*.zip` to `.gitignore` and the guard only.
- **Executed:** `is_blocked` returns False, and `git check-ignore` reports not ignored, for:
  - `apple_health_export/export.xml`
  - `export_cda.xml`
  - `clinical-records/x.json`
  - `workout-routes/*.gpx`
- `.claude/settings.json` denies neither `*.zip` nor these files.
- **Trigger:** The user unzips `export.zip` anywhere in the checkout outside `data/`. Their full Health record, including clinical records and GPS routes, can then be committed, and an agent can read it.
- **Correction:**
  - Block the `apple_health_export/` folder in `.gitignore` and in `BLOCKED_DIRS`.
  - Block `export*.xml` / `*_cda.xml` / `*.gpx`, or refuse a `health_export` path outside `data/`.
  - Add `Read(./**/*.zip)` and `Read(./**/apple_health_export/**)` to `.claude/settings.json`.
- **Test:** cases for these paths in `tests/test_phi_guard.py`.

### 3. P2: Contract changed without Nano's recorded approval
**Where:** `contract/schema.sql`, `contract/migrations/002.sql`, `contract/api.md`, `contract/README.md`, `contract/mock/*`.

- The PR's own commit message says "Contract v2 (needs Nano's approval)".
- Earlier contract changes recorded the approval in the commit, for example "Contract change approved by Nano 2026-10-25".
- Nano's answers in `docs/designs/apple-health-import.md` cover scope and review, not the schema or API change. Yet line 3 of that doc says "decided … implemented as contract v2", which claims more than the record shows.
- **Correction:** record the approval, or hold the contract changes until it exists.

### 4. P2: Two HealthKit types use the wrong daily aggregation
**Where:** `src/paper_work/domain/health.py:22-27`.

- **Executed results:**
  - Two `InsulinDelivery` doses of 4 IU and 6 IU are stored as `mean 5 IU`. HealthKit defines insulin delivery as a cumulative amount, so the day's total is 10 IU.
  - `PhysicalEffort` in `kcal/hr·kg` is a rate (METs), but the code sums it: 3.5 + 4.5 is stored as `sum 8`, which means nothing.
- **Who is hurt:** anyone reading the day's value, or a future vitals chart.
- **Correction:** move `insulin_delivery` into `_CUMULATIVE` and remove `physical_effort`.
- **Test:** extend `test_cumulative_types` in `tests/test_health_domain.py`.

### 5. P2: Rounded page text hides small values and blocks updates on re-import
**Where:** `src/paper_work/domain/health.py:120-126` (`_num` keeps 2 decimals), `src/paper_work/health/ingest.py:53`, and `store/labels.py`.

- The page text, which stands in as the source page, rounds to 2 decimals.
- **Executed:**
  - `BloodAlcoholContent 0.0008 %` renders as `blood_alcohol_content: mean 0 % (1 samples)`.
  - Re-importing the same day with 0.0042 reports `UNCHANGED`. The stored value stays 0.0008, so the newer export is silently ignored.
- **Who is hurt:** The source text contradicts the stored value, and data that changed is never updated.
- **Correction:**
  - Render with full precision, for example `repr` or `%.6g`.
  - Or compare the stored rows rather than the text.
- **Test:** an import, then a re-import with a value change below 0.005, asserting `REPLACED`.
- **Related, not a finding:** HealthKit's `%` unit holds fractions (1.0 = 100%). Stored "as exported", SpO₂ would read "0.97 %". See validation gaps.

### 6. P2: An oversized whole number in a correction returns 500 instead of 422, and the web app sends one
**Where:** `src/paper_work/store/review.py:46` and `:144`, and `src/paper_work/web/review.js:69-73`.

- **Executed:**
  - `PATCH /api/measurement/2` with `fields.value = 2**63` returns `500`.
  - The same with `10**400` returns `500`: `math.isfinite` overflows, or the SQLite bind does.
- **How the web app reaches it:** The browser accepts `1e19` (`Number.isFinite` is true), and `JSON.stringify` sends it as `10000000000000000000`. Python parses that as an int, which hits the overflow. The browser and API checks disagree.
- **Correction:** convert to `float` first and reject anything outside the finite float range, or ints with `abs(v) > 2**53`. Mirror the same bound in `parseInput`.
- **Tests:** add both cases to `test_measurement_filters_and_review` and to the web unit test.

### 7. P2: The archive is read without size or ratio caps, and one large element is held in memory
**Where:** `src/paper_work/health/export.py:53` and `:83`.

- Nothing limits how large the XML may grow once decompressed, or how much it may expand relative to the zip.
- Memory is freed only when a direct child of `<HealthData>` ends.
- **Executed:** a single `<Workout>` holding 100k children peaked at about 34 MB, against about 2.6 MB for the same count of flat elements. Growth is linear and unbounded, from an untrusted file.
- **Correction:**
  - Add generous caps on uncompressed size and compression ratio, using `ZipInfo.file_size` and `compress_size`.
  - Add a per-element child or depth limit that raises `ExportError`.
- **Test:** an oversized or deeply nested synthetic zip should be refused.

### 8. P2: README and error hint are out of date
**Where:** `README.md:12-19, 39-42`.

- `README.md` does not mention the `health` step, `[paths] health_export`, the `paper_work/health` package, or the schema upgrade.
- After pulling, the API returns 503 on a v1 database until ingest or the Health import runs.
- The hint in `store/db.py` names only ingest.

### 9. P2: Missing tests
No test would go red for findings 1, 4, 5, 6 or 7. They belong in:
- `tests/test_api.py` (findings 1 and 6)
- `tests/test_health_domain.py` (finding 4)
- `tests/test_health_ingest.py` (finding 5)
- `tests/test_health_export.py` (finding 7)
- `tests/web/web.test.mjs` (finding 6)

## Design questions for the owner
These follow decided behaviour, but they may hurt the person using the app.

- **Home and clinic BP shown as one series.** Home-cuff readings go into `bp_reading`, so the BP chart and `/api/bp` mix them with clinic readings. The claim view is not affected: Apple Health visits have no findings, so they get no category. Should the chart distinguish the two sources?
- **Review queue flood.** Every day adds one unreviewed visit plus one row per type. Ten years of data puts tens of thousands of items in the queue and one "Apple Health" visit per day on the timeline.
- **Sleep split across days.** A sample counts on the day it starts, so one night's sleep that crosses midnight is split across two days.
- **"Largest single-source total" drops real data.** It discards genuine workouts recorded by a second source.
- **BP duplicates removed silently.** Readings with the same minute and values are merged regardless of source. The contract text does not say this happens.

## Optional improvements
- Systolic or diastolic records that are not inside a correlation are dropped without being counted (`export.py:110`).
- The 503 hint could also name the Health import.

## Checks run
| Check | Result |
|---|---|
| `python scripts/phi_guard.py --all` | exit 0 |
| `pytest -q` (scratch venv, Python **3.14**) | 462 passed, 1 skipped. The one initial failure was my relative `PYTHONPATH` in the child process; `test_admin_api.py` passes 7/7 with an absolute path |
| `node --test tests/web/*.test.mjs` | 33/33 passed |
| `node --test tests/web/races.e2e.mjs` | 19 skipped (no Playwright) |
| New PR tests run at base | `test_health_*` fail to import; the 3 new `test_api.py` tests error. So they guard the change |
| Synthetic probes | 5 written and run; they reproduce findings 1, 4, 5, 6 and 7 |

## Validation gaps
- **Python 3.13 not available.** Every run used 3.14. A 3.13 run of `pip install -e ".[dev]" && pytest -q` would settle this.
- **Browser races not run.** Run `races.e2e.mjs` under Playwright.
- **Real Health export not tested.** Three things need one:
  - whether `%` types are written as fractions;
  - whether food correlation records also appear at the top level;
  - parse time and memory on a multi-GB file.
- **Windows not tested.**

**Verdict: changes required.**
