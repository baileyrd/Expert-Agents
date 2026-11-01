<!-- mediboard/3-r1 run 5, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 143 s, 436301 tokens, $0.65 -->

# PR 3 review (baileyrd/MediBoard): claim view, treatment gaps and CSV export

**Verdict: changes required.** I found two problems, neither related to unit handling. A medication, lab or blood-pressure reading can land in a claim's evidence because of a diagnosis the reviewer hasn't checked yet. And the "unconfirmed items" count on the claim view doesn't match what the contract says it counts.

- **Repository:** baileyrd/MediBoard, PR #3
- **Base:** `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`
- **Reviewed head:** `b9dcf05dc4eecaca7db8987355b44186f5128388`. I fetched it with `git fetch origin pull/3/head` and the fetched commit matched this SHA.
- **Diff:** four new files: `src/paper_work/domain/claim.py`, `src/paper_work/domain/gaps.py`, `tests/test_claim.py`, `tests/test_gaps.py`. Nothing under `contract/` changed. There is no store, API or web code yet, so the routes, the Settings flow and the browser behaviour are not part of this PR.

## What I checked it against
- **Review gate:** only confirmed or corrected rows reach the claim view or export (`contract/schema.sql:3-4`, `contract/api.md:91`). The code also requires the item's visit to be confirmed or corrected. That is stricter than the contract and allowed, because the visit supplies the date and the page.
- **Category scope:** meds, labs and BP readings belong to a category "only through their visit's findings" (`contract/README.md:50`).
- **Unconfirmed count:** `unconfirmed_count` is "the number of `unreviewed` items in the category" (`api.md:91`).
- **Unset settings:** `in_service` is null and `gaps` is empty while the service dates or `gap_months` are unset. The code does this.
- **CSV formulas:** cells starting `=`, `+`, `-` or `@` get a `'` prefix. The code does exactly this.
- **Display text:** BP, med and lab text keeps each value as stored. Units are not case-folded or converted, and lab flags are shown as written. BP flags are added by our ingest, not read from the page, so leaving them out of the text is correct.
- **Error messages:** the four `ValueError` messages contain no record text.

## Blocking findings

### 1. P1: Med, lab and BP evidence can be assigned to a claim by a finding nobody has reviewed
- **Where:** `src/paper_work/domain/claim.py:83-95`, with the instruction at line 89 ("i.e. `VisitSummary.categories`").
- **The rule broken:** `api.md:32` defines `VisitSummary.categories` from the visit's *non-rejected* findings, and that includes unreviewed ones. So the category of a confirmed med or reading can come from model output that no person has checked. `schema.sql:3-4` says claim views and exports read only confirmed or corrected rows.
- **Scenario (executed, synthetic):**
  1. A visit is confirmed.
  2. Its only finding is a depression finding the model got wrong, and it is still unreviewed.
  3. A confirmed med `SynthMed 10 mg (started)` sits on the same visit.
  4. `build_claim(DEPRESSION, …)` puts the med in `evidence`, and `claim_csv` writes it out as `2015-03-09,,med,SynthMed 10 mg (started),synthetic.pdf,1`.
- **Who is hurt:** a VA rater reading the export sees a med listed as depression evidence that the page doesn't support. The on-screen banner would show 1 unconfirmed item, but the CSV has no banner.
- **Fix:** for claim scope, take a med, lab or BP reading's categories only from findings whose own status is confirmed or corrected (on a reviewed visit). Change the docstring and the store contract for `categories_for` to match. Keep `VisitSummary.categories` for the timeline only.
- **Regression test:** a confirmed med on a confirmed visit whose only depression finding is unreviewed must not appear in `build_claim(DEPRESSION)`. Run the same case with the finding confirmed: the med must appear.

### 2. P2: `unconfirmed_count` departs from `api.md:91`
- **Where:** `claim.py:154-157`, `claim.py:109`. The behaviour is locked in by `tests/test_claim.py:64`.
- **What the code does:** `_is_pending` counts items that haven't passed the gate and aren't rejected. The contract counts items whose status is `unreviewed`.
- **Scenarios (executed, synthetic):**
  - **Hidden work:** an unreviewed hypertension finding on a *rejected* visit gives a count of 0. The contract gives 1, and that finding is still waiting for review.
  - **Inflated count:** an *unreviewed* visit with three confirmed children (one finding, two BP readings) and no unreviewed child gives a count of 3. Under the contract it is 0, or 1 if the visit itself counts.
- **Who is hurt:** the person relying on the banner either misses pending review work or sees an inflated number.
- **Fix:** count `status == unreviewed`, and decide explicitly whether an unreviewed visit counts once. Alternatively, get Nano's recorded approval to change `api.md:91` to "not yet through the gate".
- **Tests:** replace the assertion at `test_claim.py:64` and add the inflated-count case above.

## Design questions for the owner
- **Gaps from separation:** gaps are measured only between evidence dates. The stretch from separation to the first evidence after service is never shown, and that period matters for continuity of symptoms (38 CFR 3.303(b)) and the one-year presumptive window (3.307/3.309). Should one side of a gap be the separation date?
- **CSV warnings:** the CSV columns are fixed by the contract, so the export never shows that unconfirmed items exist or that the service dates are unset.
- **Entry date only:** with only the entry date set, `in_service` is null even for a date before entry, which is certainly not in service. The contract only covers the case where both dates are unset.

## Optional improvements
- **Sort order:** `_order` (`claim.py:165`) sorts by kind before visit, so items from two visits on the same page interleave. Adding `visit_id` (or the visit's position on the page) before kind keeps each visit's items together.
- **Date check:** `_iso` tests truthiness; `is not None` is clearer.
- **More CSV prefixes:** OWASP also lists a leading tab and carriage return as formula triggers. Adding them needs a contract change.

## Checks performed
- **PHI guard:** `python scripts/phi_guard.py --all` exited 0.
- **Tests:** `python -m pytest -q` gave **102 passed, 1 skipped**. The skip is `tests/test_extraction.py:90` because `jsonschema` isn't installed; that test is unrelated to this PR.
- **New tests at base:** I ran `test_claim.py` and `test_gaps.py` on a scratch copy of the base. Both fail to collect because the modules don't exist there, so they guard the new code.
- **Probes:** a script in scratch space used only synthetic values. It produced the results quoted in findings 1 and 2.

## Validation gaps
- **Python version:** only Python 3.14.8 was available, not the required 3.13. I ran the tests in a scratch venv with pytest only, without `pip install -e ".[dev]"`.
- **Not run:**
  - `node --test tests/web/*.test.mjs`: `tests/web/` doesn't exist at this head.
  - The Playwright browser tests: not available here.
- **Not testable yet:** whether the API checks `{category}` against `config.toml`, the CSV response's content type, and how the store builds `Candidate`. These need the T3 store and API.
