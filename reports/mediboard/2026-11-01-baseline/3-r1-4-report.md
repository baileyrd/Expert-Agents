<!-- mediboard/3-r1 run 4, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 148 s, 506396 tokens, $0.69 -->

# Review: baileyrd/MediBoard PR 3 (claim view, treatment gaps, CSV export)

**Verdict: changes required.** Two P2 findings block, and no P0 or P1 defects turned up. The PR adds only pure domain code (`domain/claim.py`, `domain/gaps.py` and their tests), with no store, API route or web code. The review gate, the inclusive in-service check, the CSV columns and the match with `mock/claim.json` all hold.

- **Base:** `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`
- **Head:** `b9dcf05dc4eecaca7db8987355b44186f5128388`. I fetched `pull/3/head` and it matches this SHA.
- **Commits:** `6219003` (T6 claim, gaps, CSV) and `b9dcf05` (removes the rule that BP always counts for hypertension).
- **Docs:** `CLAUDE.md` and `docs/designs/` do not exist in the repo. I read `README.md`, `contract/README.md`, `contract/api.md`, `contract/schema.sql`, `contract/mock/claim.json`, `domain/models.py` and `config.py`.

## What I expected each part to guarantee
- **Review gate:** only `confirmed`/`corrected` rows appear (`api.md:91`, `schema.sql:3-4`). The PR also requires the visit to be reviewed, since the visit supplies the date and page. That is stricter than the contract and consistent with it.
- **Category scope:** a finding counts for its condition's category. Meds, labs and BP count "only through their visit's findings" (`contract/README.md:49-50`), using `VisitSummary.categories`, which means non-rejected findings (`api.md:32`). The code follows this (`claim.py:83-95`).
- **Values:** BP is `sys/dia` as read. Lab value and unit are passed through as written, with no change of case or conversion. Med dose and action are as written. No value changes meaning between store and export.
- **`in_service`:** inclusive, and `null` unless both service dates are set. `gaps` is empty while `gap_months` is unset (`api.md:93-94`). Both hold.
- **CSV:** columns and formula defusing as specified in `api.md:99`. Both hold.
- **Error messages:** none of the `ValueError` messages contains input text.

## Blocking findings

### 1. P2: `unconfirmed_count` doesn't count what the contract says
- **Where:** `src/paper_work/domain/claim.py:154-157` (used at `:109`).
- **Contract:** `api.md:91-92` says `unconfirmed_count` is "the number of `unreviewed` items in the category". The code counts a different thing: candidates that are not rejected, whose visit is not rejected, and that don't pass the gate.
- **Scenario A (executed, synthetic):**
  1. A visit is rejected (by mistake, or instead of correcting its date).
  2. Under it sit an unreviewed hypertension finding and an unreviewed BP.
  3. Reviewing a visit doesn't change its children (`api.md:83`), so both items stay unreviewed and stay in the review queue.
  4. The code returns `unconfirmed_count = 0`; the contract gives 2. The "unconfirmed items exist" banner disappears while review work for the category is still pending.
- **Scenario B (executed):** one unreviewed visit with three confirmed BP readings. The code returns 3, but there are no unreviewed rows among those items, and the queue shows a single row (the visit). The banner reports a number the person can't find in the queue.
- **Who is hurt:** the person preparing the claim. Pending items for the category are hidden from them, or a count they can't reconcile is shown.
- **Test gap:** replacing `_is_pending` with the contract's literal rule (`c.status is Status.UNREVIEWED`) leaves all 30 new tests passing. `test_unreviewed_or_rejected_visit_blocks_reviewed_item` asserts `== 1` (`tests/test_claim.py:64`), and both definitions give 1 there, but for different items.
- **Required correction:** count `unreviewed` items as the contract says, or get Nano's recorded approval to change `api.md`. Then fix the module docstring to match.
- **Regression test:** Scenario A must expect 2, and Scenario B must expect the contract's number.

### 2. P2: no test covers defusing a cell that starts with `+`
- **Where:** `src/paper_work/domain/claim.py:22`; the test is `tests/test_claim.py:122` (`test_csv_shape_and_formula_defusing`).
- **Contract:** `api.md:99` requires cells starting with `=`, `+`, `-` or `@` to be prefixed with `'`. The test covers `=`, `-` and `@` only.
- **Mutation (executed):** removing `"+"` from `_FORMULA_PREFIXES` leaves every test green. A future edit could reopen formula injection in the export a rater opens in a spreadsheet, and CI would not notice.
- **Required correction:** add a `+…` text cell to that test, expecting `'+…`.

## Design questions for the owner (the contract settles these; not findings)
1. **BP outside a hypertension visit.** Commit `b9dcf05` correctly follows `contract/README.md:49-50`, so BP reaches the hypertension claim only when its visit has a hypertension finding. Hypertension is rated under diagnostic code 7101 (38 CFR 4.104), which rates on diastolic and systolic readings, and its Note (1) asks for readings taken two or more times on at least three different days. Readings taken at knee, sleep or primary-care visits without an HTN finding are exactly that evidence, and they will be missing from the hypertension export. The same rule also lists those readings as evidence for the knee claim, where the page doesn't support them as such. Should BP be treated as hypertension evidence in its own right?
2. **Categories from unreviewed findings.** `VisitSummary.categories` includes unreviewed findings. A confirmed med, lab or BP can therefore reach a category's export when its only link to that category is a finding nobody has reviewed. Should category membership for the claim view use only reviewed findings?
3. **Children of rejected visits.** Should rejecting a visit also take its unreviewed children out of the queue and the banner (the PR's behaviour), or leave them pending (the contract's behaviour)? This is the decision behind Finding 1.

## Optional improvements
- In the CSV, also defuse a leading tab or carriage return (OWASP's CSV-injection list), and a leading space before `=`.
- `lab_text("A1c", None, "%", None)` produces `"A1c: %"`, a unit with no value. Leave the unit out when the value is null.
- `bp_text` drops the `swapped`/`out_of_range` flag, so a confirmed reading flagged as swapped is exported as a plain `BP x/y`.

## Checks
| Check | Result |
|---|---|
| `python3 scripts/phi_guard.py --all` | exit 0 |
| `python -m pytest -q` (head, Python 3.14.8, pytest 9.2.0 in a scratch venv) | 102 passed, 1 skipped (`jsonschema` not installed; `tests/test_extraction.py:90`) |
| New tests run against base | fail at collection (the modules don't exist yet): the only possible base result for a new module |
| Mutations in a scratch copy | removing the visit gate, making `in_service` exclusive, restoring BP → hypertension, and ordering by date only each turn tests red. Dropping `+` and using the contract's count rule leave everything green (Findings 1 and 2) |
| Probes (synthetic only) | confirmed Scenarios A and B, `'+` defusing, and the `lab_text` output |
| `node --test tests/web/*.test.mjs`, Playwright races | not applicable: no `tests/web/` and no web code in this diff |

## Validation gaps
- **Python 3.13:** not installed here, so I ran everything under 3.14.8. CI pins 3.13, and the 3.13 run has not been done.
- **Schema validation test:** `jsonschema` is not installed, so the extraction schema test skipped. It isn't touched by this PR.
- **Untested until the store and API exist:** the store query that builds each `Candidate` (its categories, and the item date taken from the visit) and the `/api/claim` routes are not in this PR. That code will need its own review against `api.md:32` and `api.md:88-99`.
