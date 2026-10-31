# Grounding: mediboard

Target: baileyrd/MediBoard at `6febbed` (main, 2026-10-31). Held out as bench cases: PRs
3, 5, 6 and 18 (`bench/cases/mediboard/`). PR 20 carries 18's fixes and its review
restates 18's findings, so it is held out too: neither its commits nor its review threads
were read for this file. `mine` matched 16 commits; MediBoard's fix commits rarely say
"review" in the subject, so the classes below also come from the review threads of
PRs 4, 7, 8, 9, 15, 16, 17, 24, 26 and 27 and the commits that answered them. PR 17's
commits sit inside PR 18's held-out range, so its review is used only for classes that
other commits also back.

## Defect classes

Each class names the commits or reviews that back it.

**Clinical fidelity** (the rarest trait)
1. **A unit or code loses its identity.** Lab series grouped by a case-folded unit
   merged distinct UCUM units without conversion: review of PR 4, `f02d81316377`.
2. **A derived flag outlives the value it judged.** Correcting a swapped BP or an
   ambiguous date left the parse-time flag set: review of PR 7, `a5ae9f55ea`.

**Source and review provenance**
3. **A review lands on a row the person did not see.** A late response, a database
   switch, a server restart or a second tab lets an action carry an id that now names
   another row or dataset: reviews of PRs 8 and 15; `8d81ee4470`, `2805b15d06`,
   `80b3ce7b6b` (late responses in drawer, router and queue), `11be124386`
   (server-instance id on every review), `c5f6db6`, `40b6e63492` (no database hot swap).
4. **Check-then-act outside the write lock.** A review committed from another connection
   between the reviewed-row check and the writes is overwritten: review of PR 26,
   `6cc5104519` (`BEGIN IMMEDIATE` across the check and re-ingest).
5. **A changed source is not re-derived.** A page transcribed again after a cut-off keeps
   its key, so extract skips it and stale findings stay against new text: review of
   PR 24 (open on main).
6. **A run mixes two configurations.** A Settings save between job start and the child
   reading `config.toml` paired input B with output A: review of PR 15, `1a6f092`,
   `c5f6db6` (pin `--out`), `40b6e63492` (saves wait for running steps).
7. **Stop loses finished work.** Buffered results flushed only after the executor
   shuts down; the GUI kill at 10 s drops them: review of PR 24 (open on main),
   `c5931e8` (Windows tree kill).

**PHI containment**
8. **Record text in an error or log.** Client- or file-supplied keys echoed into API
   errors and TOML diagnostics: review of PR 7, `a5ae9f55ea`; review of PR 17.
   `contract/api.md` Conventions: "Messages never contain record text".
9. **Hostile input unbounded.** A zip that unpacks past any limit (`dc0be3c`, review of
   PR 27); a PDF symlink that leaves the scans root and is rendered and sent to the
   model (review of PR 24, open on main).
10. **A bounded request silently widened.** A limit of 0, a negative, or a digit string
    that becomes `Infinity` and serializes to `null` turned a bounded extract into an
    unlimited one: review of PR 15, `a47f46b`, `4e749ad`.

**Claim evidence**
11. **Two validators disagree.** SQLite `GLOB` stops at NUL, so the schema accepted a
    category the parser refused: review of PR 9, `845dd32`.
12. **Contract drift.** A frozen-contract change without Nano's approval, or a route
    left as a stub that returns an empty, plausible answer (`api/claim.py` returned
    `gaps=[]`): reviews of PRs 7, 9 and 17; `abe38c31e7`, `6b14719614`, `96dec1c`.

## Oracles

- **This repo**: `contract/` (schema, migrations, `api.md`, `extraction.schema.json`,
  synthetic `mock/`), `tests/` and `tests/fixtures/` (synthetic only), the design docs
  in `docs/designs/`.
- **Clinical vocabularies**: Apple HealthKit type documentation for each imported type
  and its unit; UCUM (units are case-sensitive codes); LOINC for lab identity; RxNorm for
  drug names, strength and dose form; ICD-10-CM and SNOMED CT for conditions; FHIR R4
  `Observation` (a BP is one observation with systolic and diastolic components and an
  effective time).
- **VA claims**: 38 CFR Part 4 (rating schedule) and Part 3 §3.159, §3.303, §3.304 (duty
  to assist, service connection, evidence); the M21-1 Adjudication Procedures Manual.
- **Platform**: SQLite documentation (transactions, rowid allocation, `GLOB`), Python
  `zipfile`, PyMuPDF, FastAPI.

## Settled decisions

- `CLAUDE.md`: PHI never leaves the machine; synthetic fixtures only; `data/` is PHI;
  `contract/` changes need Nano's approval.
- `README.md`: local only on `127.0.0.1`; only confirmed and corrected items reach the
  claim view and CSV export; reviewed days are kept on re-import.
- `contract/README.md`: frozen at M0; ingest re-ingest rule; open-ended category slugs;
  optional claim module; store notes.
- `contract/api.md`: conventions (errors never contain record text, Host/Origin checks,
  status and kind sets), review, claim and admin sections.
- `docs/designs/apple-health-import.md`: decided v1 and rejected options.

## Kept out of the prompt

The held-out findings: PR 3's claim count, PR 5's three extraction findings, PR 6's page
text under reviewed rows, and PR 18's six Health import findings. HealthKit is cited as
an oracle by name only; no type, aggregation rule or timestamp rule from PR 18's review
is restated. Class 3 is backed by PRs 8, 15 and 16 independently of PR 18.

## Mined table (raw)

16 commits answer a review; 27 held-out commits removed.
Read each one's diff (`git show <sha>`) for the defect class it fixed.

| Commit | Subject | Files |
|---|---|---|
| 6cc5104519 | Ingest: hold the write lock across review checks and re-ingest | `src/paper_work/ingest/ingest.py` |
| ef78cf3a8d | Transcribe scans from the GUI: PDFs -> vision model -> all_records.json | `contract/README.md`, `contract/api.md`, `src/paper_work/admin/jobs.py` … |
| 57a143824d | Docs: review wording fixes from #21; deny agent reads of zip/xml | `src/paper_work/web/README.md` |
| 11be124386 | Reviews require the server run's instance id (stale-page guard) | `contract/api.md`, `contract/mock/config.json`, `src/paper_work/api/app.py` … |
| 40b6e63492 | Settings: no database hot swap; saves wait for running steps | `contract/api.md`, `src/paper_work/admin/jobs.py`, `src/paper_work/api/admin.py` … |
| ebf1d08991 | contract: Settings section matches pinned --out and the review hold | `contract/api.md` |
| daf272d1c5 | Settings view: run extract/ingest and edit [model]/[paths] from the web app | `src/paper_work/admin/__init__.py`, `src/paper_work/admin/jobs.py`, `src/paper_work/admin/settings.py` … |
| 6b14719614 | Wire claim routes to T6 domain.claim; drop the api/claim.py stub | `src/paper_work/api/claim.py`, `src/paper_work/api/routes.py`, `src/paper_work/store/repo.py` |
| 80b3ce7b6b | Decide queue refresh selection and reopen at completion, not at call time | `src/paper_work/web/views.js` |
| 2805b15d06 | Keep newer focus after late reviews; adopt open-ended categories and /api/config | `src/paper_work/web/app.js`, `src/paper_work/web/dom.js`, `src/paper_work/web/drawer.js` … |
| a5ae9f55ea | Review fixes: clear misread flags on correction; never echo client keys in errors | `src/paper_work/store/review.py` |
| 8d81ee4470 | Discard late responses in the drawer, router, and review queue | `src/paper_work/web/README.md`, `src/paper_work/web/app.js`, `src/paper_work/web/drawer.js` … |
| abe38c31e7 | Contract: open-ended condition categories, optional VA claim module | `contract/README.md`, `contract/api.md`, `contract/mock/conditions.json` … |
| d7a0cff030 | T4: web shell, timeline, drawer, review queue | `src/paper_work/web/README.md`, `src/paper_work/web/api.js`, `src/paper_work/web/app.js` … |
| 8f530b325b | T3: SQLite store and FastAPI app for contract v1 | `src/paper_work/api/__main__.py`, `src/paper_work/api/app.py`, `src/paper_work/api/claim.py` … |
| 866e4bd0c4 | T0: contract and scaffold (#1) | `contract/README.md`, `contract/api.md`, `contract/extraction.schema.json` … |
