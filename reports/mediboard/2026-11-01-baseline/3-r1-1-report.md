<!-- mediboard/3-r1 run 1, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 129 s, 391397 tokens, $0.59 -->

# Review of baileyrd/MediBoard PR 3: claim view, treatment gaps and CSV export

**Verdict: changes required.** There is one P2 finding: the "unconfirmed items" banner count doesn't follow the contract. Everything else in the review gate, ordering, `in_service`, gaps and CSV escaping matches the contract.

- **Base:** `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`
- **Reviewed head:** `b9dcf05dc4eecaca7db8987355b44186f5128388`. I fetched `pull/3/head` and it matches this SHA.
- **Scope:** two new pure-domain modules, `src/paper_work/domain/claim.py` and `src/paper_work/domain/gaps.py`, plus their tests. Nothing calls them yet: no store, route or web code changes, and `contract/` is untouched.

## What must hold (predicted before reading the PR text)
- **Review gate:** only confirmed or corrected rows reach the view and the CSV (`contract/api.md:91`). The code also requires the visit to be confirmed or corrected, because the visit carries the date and page. That is stricter than the contract and does no harm.
- **Banner count:** `unconfirmed_count` is "the number of `unreviewed` items in the category" (`contract/api.md:91-92`).
- **Categories:** labs, meds and BP belong to a category only through their visit's findings (`contract/README.md:49-50`).
- **`in_service` and gaps:** `in_service` is null while the `[service]` dates are unset, and `gaps` is empty while `gap_months` is unset. The CSV has the columns the contract lists, and cells starting with `= + - @` get a `'` prefix.
- **Clinical meaning:**
  - BP is shown as `sys/dia` integers with no unit change.
  - Labs are shown as `test: value unit [flag]`, built from the stored strings with no case-folding or conversion.
  - Med text uses the stored name and dose unchanged.
  - Evidence dates come from the visit.
  - No value is merged, split or converted.

## Blocking findings

### 1. P2: `unconfirmed_count` counts something other than what the contract says
- **Where:** `src/paper_work/domain/claim.py:154-157` (`_is_pending`), used at `claim.py:109`.
- **Contract:** "`unconfirmed_count` is the number of `unreviewed` items in the category" (`contract/api.md:91-92`).
- **Code:** it counts candidates where neither the item nor its visit is rejected and the item hasn't passed the gate. That differs from the contract in two ways.
- **Failure scenario (executed: a scratch probe on synthetic candidates, with the head's `src` on the path):**
  - **A.** One *unreviewed* hypertension finding whose visit is *rejected*: count is **0**; the contract gives 1.
    - Trigger: the person rejects a visit (for example because its date was misread) instead of correcting it.
    - Effect: the finding stays unreviewed and still sits in `/api/review/queue`. But the claim banner says nothing is pending, and the finding can never reach the claim, so the person isn't prompted to recover it.
  - **B.** Three *confirmed* BP readings on one *unreviewed* visit: count is **3**. The contract gives 0 if only child items count, or 1 if the visit counts. The banner number no longer matches anything the review queue shows.
  - **Who is hurt:** the person preparing the claim, who relies on the banner to know whether the category is fully reviewed.
- **Coverage gap:** `tests/test_claim.py:56-64` mixes three visit statuses under one `visit_id=1`. The contract reading and the code both happen to give `1` there, so the test can't tell them apart.
- **Fix:** count items whose own `status` is `unreviewed` in the category, and say how an unreviewed visit is counted. If the intended meaning is "not yet through the gate", that is a contract change and needs Nano's approval.
- **Regression tests:** `build_claim(HTN, [finding(UNREVIEWED, visit=REJECTED)])` should give count 1, and three confirmed items on one unreviewed visit should give the agreed value. Both go red at the head.

No other failure class produced a trace:
- **Meaning drift:** none. The text builders don't touch units or values.
- **Stale derivation:** ruled out because the code is pure. Categories and status are passed in, and wiring them correctly is the store's job.
- **Wrong-row review, check-then-act and mixed runs:** no I/O and no ids are issued.
- **Text in diagnostics:** every `ValueError` message is a fixed string.
- **Widened input:** `gap_months <= 0` raises, and `config._opt_positive_int` already rejects 0, negatives and `true`. Service dates in the wrong order are rejected in `config._service`.
- **Two validators:** none apply here.
- **Contract drift:** only finding 1. The JSON shape equals `contract/mock/claim.json`, and the CSV columns and escaping match `contract/api.md`.

## Design questions for Nano (documented behaviour that may hurt the claim)
1. **BP only through hypertension findings** (commit `b9dcf05`, following `contract/README.md:49-50`).
   - Under 38 CFR 4.104, DC 7101 Note 1, hypertension has to be confirmed by readings taken two or more times on at least three different days. The rating depends on the history of diastolic and systolic readings.
   - Routine vitals recorded at visits with no hypertension finding (a knee visit, say, and especially in service) are left out of the hypertension view and CSV.
   - Hypertension is also a chronic disease under 3.309(a) with a one-year presumptive window (3.307). Readings in that window are evidence even without a diagnosis on the same page.
2. **Category membership through unreviewed findings.**
   - `categories_for` uses `VisitSummary.categories`, which covers all *non-rejected* findings (`contract/api.md`), including unreviewed ones.
   - So a confirmed BP reading or med can show up as hypertension evidence because of a model-extracted hypertension finding nobody has reviewed yet. The rater sees the reading, but not the finding that links it to the category.
   - Question: should membership require that finding to be confirmed or corrected?
3. **Gaps are measured only between evidence dates.**
   - The gap from separation to the first treatment after service is never reported, and that is the stretch a rater looks at for continuity (3.303(b)) and the 3.307 window.

## Optional improvements
- **Flags dropped from the text:** `bp_text` leaves out the BP flag (`swapped` / `out_of_range`), and the evidence carries no visit `date_flag`. A rater can't see that a reading or date was machine-flagged, even though a human confirmed it.
- **Unit without a value:** `lab_text` can print a unit with no value (`"TSH: mg/dL"`). It would be better to drop the unit when the value is missing.
- **More CSV escaping:** OWASP also lists leading tab and carriage return. Adding them would be a contract change, so it's for Nano to decide.
- **End-of-month edge case:** `months_between(2020-01-31, 2020-02-29)` returns 0 even though February is complete. This only matters right at the threshold.
- **Test data:** `tests/test_claim.py:56-64` should give each visit status its own `visit_id`.

## Checks run
| Check | Result |
|---|---|
| `python3 scripts/phi_guard.py --all` | exit 0 (passed) |
| `python -m pytest -q` (whole suite, scratch venv with pytest 9.2.0, `PYTHONPATH=src`) | **102 passed, 1 skipped** |
| New tests at base | `claim.py` and `gaps.py` don't exist at `866e4bd`, so every new test fails at import there. They do guard the new behaviour, but none of them covers finding 1. |
| Scratch probe (synthetic candidates) | Reproduced scenarios A and B above |
| `node --test tests/web/*.test.mjs`, Playwright races | Not applicable: the repo has no `tests/web/` and this PR has no web code |

## Validation gaps
- **Python version:** only Python 3.14.8 is available; the project asks for 3.13, so the suite hasn't been run on 3.13.
- **Not a full install:** I didn't run `pip install -e ".[dev]"`, so FastAPI, uvicorn and PyMuPDF aren't installed. The one skipped test was not investigated.
- **End to end:** the claim route, the store queries that build `Candidate` and the categories, and the CSV route don't exist yet. The gate, category and banner behaviour from a real database through the API can only be checked once T3 wires them in, with a test that seeds synthetic rows through `tests/seed.py`.
