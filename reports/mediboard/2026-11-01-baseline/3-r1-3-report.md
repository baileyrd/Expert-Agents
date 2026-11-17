<!-- mediboard/3-r1 run 3, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 126 s, 553711 tokens, $0.67 -->

# Review of baileyrd/MediBoard PR 3: claim view, treatment gaps and CSV export

**Verdict: changes required.** One P2 finding: `unconfirmed_count` doesn't count what the contract says it counts. The other code under review matches the contract, and the tests pass.

- **Base:** `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`
- **Head:** `b9dcf05dc4eecaca7db8987355b44186f5128388`. I fetched `pull/3/head` and it is this SHA, so the review is pinned.
- **Scope:** four new files, all pure domain code with no I/O: `src/paper_work/domain/claim.py`, `src/paper_work/domain/gaps.py`, `tests/test_claim.py` and `tests/test_gaps.py`. Nothing in `contract/` changed. There is no store or API code that uses these modules yet.

## Blocking findings

### P2: `unconfirmed_count` counts something other than "unreviewed items in the category"
- **Where:** `src/paper_work/domain/claim.py:109` and `:154-157` (`_is_pending`).
- **Contract:** `contract/api.md:91` says *"`unconfirmed_count` is the number of `unreviewed` items in the category and drives the 'unconfirmed items exist' banner."*
- **What the code does:** it counts every item that hasn't passed the review gate, unless the item or its visit is rejected. So the count depends on the visit's status as well as the item's.
- **Failure scenario (executed, synthetic data, `probes/probe_count.py` in scratch):**
  - **Case A:** one `confirmed` BP reading under an `unreviewed` visit, in hypertension. The code returns `unconfirmed_count = 1`. The contract gives 0, because no item in the category is `unreviewed`.
  - **Case B:** one `unreviewed` BP reading under a `rejected` visit. The code returns 0. The contract gives 1.
- **Who is affected:** the person relying on the banner. The banner is the only thing telling them the claim view is incomplete. The same input gives a different count here than in any other implementation of the contract, such as a T3 store test or the T4 web build against the mock.
- **Why the tests miss it:** `test_unreviewed_or_rejected_visit_blocks_reviewed_item` (`tests/test_claim.py:56-64`) mixes the two cases. The code counts item 1, the contract would count item 3, and both give a total of 1, so the test passes either way.
- **Required correction:** choose one of these:
  1. Count `c.status is Status.UNREVIEWED` exactly as the contract says.
  2. Keep the current meaning (I'd argue it suits the banner better) and get Nano's recorded approval to change the `contract/api.md:91` text to match.
- **Regression tests:** two separate single-candidate tests, one for case A and one for case B, each asserting the agreed count. Remove the mixed assertion at line 64.

## Design questions (documented behaviour that could hurt the claim)

1. **A category link that nobody has reviewed reaches the export.** Meds, labs and BP are scoped through `VisitSummary.categories`, which the contract defines as the visit's non-rejected findings (`contract/api.md:32`, `contract/README.md:50`). That includes unreviewed findings. Example: a confirmed BP under a confirmed visit becomes hypertension evidence in the CSV even though the only hypertension finding at that visit is still unreviewed. The CSV carries no banner, so the rater can't tell. The reading itself is backed by its page, so this isn't a defect, but should the claim view scope by reviewed findings only? This is for the owner to decide.
2. **The visit gate is stricter than the contract.** An item needs both its own status and its visit's status to be confirmed or corrected (`claim.py:150-151`). That is consistent with *"only confirmed/corrected rows appear"* and is documented in the module docstring. It isn't written in `contract/api.md`, so the store and web threads may not expect it.
3. **The formula guard changes `pdf` values.** A PDF whose name starts with `-` is exported as `'-name.pdf`, which no longer matches a stored `page.pdf`. The contract requires this, but it weakens tracing a row back to its page.
4. **Partly set service dates.** With `entry` set and `separation` unset, `in_service` is `null` even for dates before entry. The contract only covers the case where both are unset.

## Optional improvements
- Also defuse a leading `\t` and `\r`, as OWASP's CSV-injection guidance recommends. The contract lists only `= + - @`, so adding these needs the contract text changed too.
- `_order` (`claim.py:165`) doesn't include the visit `idx`. Two visits on the same page with the same date get their items interleaved by kind and id.
- `months_between(2020-02-29, 2021-02-28)` returns 11, so a gap ending on the last day of February falls one month short of a 12-month threshold. The contract doesn't define this; it's worth deciding on purpose.
- When the store is built, it needs a test that `Candidate.visit_status` and `categories` are read in the same transaction as the item rows. Otherwise a stale gate is possible. The risk lives in the store, which doesn't exist yet.

## Checks and results
| Check | Result |
|---|---|
| `python -I scripts/phi_guard.py --all` (repo at head) | exit 0, nothing blocked |
| `python -m pytest -q` (scratch `git archive` of head, Python 3.14.8, pytest 9.2.0, `PYTHONPATH=src`) | 102 passed, 1 skipped |
| New tests at base | They fail at base because `domain/claim.py` and `domain/gaps.py` don't exist there. The second commit's change (dropping the implicit BP → hypertension rule) is guarded by `test_categories_for` line 142. |
| Synthetic probe of `unconfirmed_count` | Confirms cases A and B above |
| `node --test tests/web/*.test.mjs` and Playwright races | Not applicable: there is no `tests/web/` at head |

## Validation gaps
- **Python 3.13 isn't installed here,** so I ran the tests on 3.14.8 with only pytest installed, not `pip install -e ".[dev]"`. FastAPI and PyMuPDF weren't installed; none of the tested modules import them.
- **Nothing wires these modules up yet.** The store query that fills `Candidate` (statuses, categories, corrected dates) and the `/api/claim` and `/api/export` routes are untested. That includes checking the requested category against `config.claim.categories`. Those checks belong to the PR that adds T3.
