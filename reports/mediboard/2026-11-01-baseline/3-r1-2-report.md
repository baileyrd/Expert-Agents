<!-- mediboard/3-r1 run 2, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 136 s, 492236 tokens, $0.62 -->

# PR review: baileyrd/MediBoard #3, claim view, treatment gaps and CSV export (T6)

**Verdict: changes required.** One P1 finding: a confirmed medication, lab or BP reading can reach a claim export under a condition that nobody has confirmed. One P2 finding: the "unconfirmed items" count does not follow the contract.

- **Repository:** baileyrd/MediBoard, PR 3
- **Base:** `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`
- **Reviewed head:** `b9dcf05dc4eecaca7db8987355b44186f5128388`. The fetched `pull/3/head` matches this SHA.
- **Diff:** 4 new files and no change to `contract/`: `src/paper_work/domain/claim.py`, `src/paper_work/domain/gaps.py`, `tests/test_claim.py`, `tests/test_gaps.py`. The store and API layers are not built yet, so nothing calls `build_claim` so far.

## Blocking findings

### 1. P1: A confirmed med, lab or BP reading appears as evidence for a condition that only an unreviewed finding links it to
- **Where:** `src/paper_work/domain/claim.py:83-96` (`categories_for`, whose docstring at line 89 says to use `VisitSummary.categories`) and `:49` (`Candidate.categories`).
- **Rule broken:**
  - `contract/api.md` "Claim (T6)": "Only `confirmed`/`corrected` rows appear."
  - `contract/README.md` "Categories": labs, meds and BP belong to a category "only through their visit's findings".
  - `VisitSummary.categories` is defined as "the distinct condition categories of the visit's **non-rejected** findings" (`api.md:32`). Non-rejected includes `unreviewed`.
- **The gap:** The PR tells callers to build the category of a med, lab or BP item from that set. So which claim an item lands in is decided by model output nobody has checked. The finding that justifies the link is itself left out of the export, because it is unreviewed.
- **Trigger (executed, synthetic probe in `$TMPDIR/probe/test_probe.py`):**
  1. Visit 1 is confirmed.
  2. It has one finding, mapped to `knee`, still unreviewed.
  3. Its med "Drug 10 mg (listed)" is confirmed.
  4. `build_claim(KNEE, …)` returns the med as the only evidence. The CSV row is `2015-01-01,,med,Drug 10 mg (listed),synthetic.pdf,1`, with no knee finding beside it.
- **Who is hurt:** a VA rater reads the knee export as listing a medication as knee evidence. Neither the reviewed record nor anything the person confirmed supports that link. If the model mislabelled the condition, the export says something the page does not.
- **Required correction:** For claim scoping, derive a med/lab/BP item's categories only from findings whose `status ∈ REVIEWED`. Say so in the `categories_for` docstring and the `Candidate` comment instead of pointing at `VisitSummary.categories`.
  - Separately, decide whether such an item counts toward `unconfirmed_count` while its finding is pending, so the banner still shows. That is a contract question for Nano.
- **Regression test** (in `tests/test_claim.py`): an unreviewed knee finding plus a confirmed med on a confirmed visit gives `evidence == ()` for `KNEE`. It should then pass once the finding is confirmed.

### 2. P2: `unconfirmed_count` departs from the contract
- **Where:** `src/paper_work/domain/claim.py:154-157` (`_is_pending`) and `:109`.
- **Contract (`api.md`):** "`unconfirmed_count` is the number of `unreviewed` items in the category."
- **What the code counts instead:** items that are not rejected and have not passed the gate (item and visit both reviewed).
- **Trace (executed, same probe file):**
  - An `unreviewed` BP on a `rejected` visit gives 0. The contract gives 1.
  - A `confirmed` BP on an `unreviewed` visit gives 1. The contract gives 0.
  - `test_unreviewed_or_rejected_visit_blocks_reviewed_item` asserts 1 only because the two differences cancel out.
- **Who is hurt:** the person relying on the banner gets a count the published API does not define, and the T3 contract-shape tests are built from `api.md`.
- **Required correction:** The code's rule is arguably better: it shows the banner when confirmed items are hidden behind an unreviewed visit. Either get Nano's recorded approval to amend `api.md` to that rule, or count `status == unreviewed` literally.
- **Regression test:** the two single-item cases above, each asserted on its own.

## Design questions for the owner
- **BP flag left out of the evidence text.** `bp_text` drops the stored `swapped`/`out_of_range` flag, while `lab_text` includes the lab flag. A confirmed 88/142 reading marked `swapped` exports as plain "BP 88/142". Under 38 CFR 4.104 DC 7101 the diastolic value drives the rating, so it is worth deciding whether the rater should see the flag.
- **No gap measured from separation.** Gaps are measured only between dates of reviewed evidence. Nothing measures the stretch from `service.separation` to the first evidence after service, which matters for continuity under 38 CFR 3.303(b). The contract doesn't define this either way.
- **Ambiguous dates feed `in_service`.** An `ambiguous` visit date that the reviewer confirmed still drives `in_service` and the gaps. `Candidate` has no `date_flag` field to show this.

## Optional improvements
- **Formula defusing:** `_defuse` matches the contract's four prefixes. OWASP CSV-injection guidance also lists a leading TAB and CR, so the contract could add them.
- **Docstring overclaims:** the module docstring (line 6) says the only way to get an `Evidence` is through `build_claim`. That isn't enforced, because `Evidence` is a public dataclass.
- **Categories not checked against config:** `build_claim` doesn't check `category` against the configured `claim.categories`. The upcoming API route must do this.
- **Missing test:** add one where `visit_status=CORRECTED` passes the gate.

## Checks performed
- **`python3 scripts/phi_guard.py --all`:** exit 0, no output.
- **`pytest -q`, full suite at the head:** 102 passed, 1 skipped. This ran on Python 3.14.8 with pytest 9.2.0 in a scratch venv, with `PYTHONPATH=src`. Python 3.13 isn't available here, so a run on 3.13 with the package installed is still a gap.
- **New tests at the base commit:** both files fail at collection because the modules don't exist yet, so they guard the change.
- **New tests at the first PR commit (6219003):** `test_categories_for` fails there, so it guards the BP→hypertension fix in b9dcf05.
- **Probe (`$TMPDIR/probe/test_probe.py`):** synthetic values only. It reproduced findings 1 and 2.
- **Not run:**
  - `node --test tests/web/*.test.mjs`: there is no `tests/web/` in the tree.
  - The Playwright race suite: it doesn't exist, and this PR has no web code.
- **Validation gap:** the store/API code that builds `Candidate.categories`, the visit date and the corrected condition doesn't exist yet. The race-type checks (wrong-row review, check-then-act, stale derivation) need that later PR to run against.

No record data, databases or files under `data/` were opened.
