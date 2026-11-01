<!-- mediboard/6-r1 run 5, variant baseline, prompt ab404a7bc9802730d0833303ae93c97e93397506, model claude-opus-5-5, 2.1.316 (Claude Code); 298 s, 871722 tokens, $1.26 -->

# Review: baileyrd/MediBoard PR 6, "T2: normalize and ingest extractions into SQLite"

- **Base:** `866e4bd0c4cb65227c0da2e675ff931e6b6371d0`
- **Reviewed head:** `5fc60c1cb4245172941758ef75880c20d87ca868` (fetched with `pull/6/head`; it matches the given SHA)
- **Verdict: changes required.** Two P1 problems lead the list: a review can land on a row the reviewer never saw, and a confirmed quote can stay attached to page text that no longer contains it.

The PR adds date, BP, provider, condition and page normalizers, plus the ingest step and its command-line entry point. Every probe below used synthetic values in a scratch copy, never real data or a model. No finding quotes record text.

## Blocking findings

### 1. P1: Re-ingest reuses row ids for different facts, so a review can land on the wrong row
- **Where:** `src/paper_work/ingest/ingest.py:90` deletes a page's unreviewed rows, and lines 110–131 re-insert them. The tables use `INTEGER PRIMARY KEY` without `AUTOINCREMENT`, so SQLite hands the deleted top ids out again.
- **Executed:** A page was ingested with one visit dated 1/1/20 and BP 120/80, which became visit id 1 and `bp_reading` id 1. After a re-extraction gave the page date 2/2/20 and BP 160/100, ingest stored **visit 1 = 2020-02-02 and bp 1 = 160/100**.
- **Who is hurt:** A review queue or visit page opened before the run still shows "BP 120/80, id 1". `PATCH /api/bp/1 {"status":"confirmed"}` then confirms 160/100, a reading the reviewer never saw. The same applies to a finding id that moves from a knee condition to a hypertension one: a fact the reviewer never saw then reaches a rater's claim export.
- **Also executed:** Re-running with *unchanged* input renumbers every unreviewed row (visits 1,2 became 3,4; findings 3,4 became 5,6). Any open tab now holds dead ids or reused ones. This makes the README's "idempotent ingest" (`README.md:24`) false. `test_reingest_is_idempotent` compares row counts only, so it can't catch this.
- **Fix:**
  - If the stored unreviewed rows already equal the effective extraction, skip the page.
  - For pages whose extraction changed, ids must never be reused. That needs either `AUTOINCREMENT` on the extracted tables, which changes `contract/schema.sql` and needs Nano's approval, or a review PATCH that names the row it expects. That choice belongs to the owner.
- **Tests that should go red today:**
  - Ingest L1, keep `bp_reading` id N, ingest L2 with different values: id N must return 404 or still hold 120/80.
  - Ingest the same lines twice: every id must be unchanged.

### 2. P1: Page text is replaced under reviewed rows, so a confirmed quote can point at a page that no longer contains it
- **Where:** `ingest.py:72-77`. `_upsert_pages` overwrites `page.text` whenever it differs, even when the page holds reviewed rows.
- **Executed (G1):** A confirmed finding with quote "SYNTH QUOTE ALPHA" sat on a.pdf p1. all_records.json was then re-supplied with different text for that page. The outcome was `kept_reviewed`, the finding stayed `confirmed`, and the quote was no longer in its page's text. Nothing was logged or reported about the text change.
- **Who is hurt:** A rater reading the claim export. Its row cites pdf/page as the source, but that page now shows text that does not support the quote. The schema calls `finding.quote` "verbatim from the transcription".
- **Executed (G2):** Unreviewed rows from the old extraction are re-inserted against the new text with outcome `replaced`; their quotes aren't on the page either. Nothing can tell from the extraction line which text it was made from.
- **Fix:**
  - When a page with reviewed rows has changed text, don't overwrite it silently. Keep the old text and report a new outcome (or fail the run). What should happen next is an owner decision.
  - Before inserting, check that each finding's `quote` appears in the stored page text. If it doesn't, store a `quote:` page_error for that page.
- **Tests:** G1 and G2 as above. The finding's quote must remain a substring of its page text, or the run must report the change.

### 3. P1: A config key (a condition name) is echoed into an exception
- **Where:** `src/paper_work/domain/conditions.py:23`, which raises `f"conditions.{name!r}: unknown category"`.
- **Executed:** `parse_category_map({"SYNTH-CONDITION-NAME": "kneee"})` raised a `ValueError` whose message contains the name.
- **Why it matters:** The `[conditions]` keys are condition names "as extracted" (the `config.toml` comment), i.e. the person's diagnoses. `contract/api.md` says messages never contain record text, and this PR's own docstrings say the same. Privacy of the record is what's at stake.
- **Fix:** Name the entry by position or count only. Add a test asserting the key is absent from the message.

### 4. P2: One hostile line aborts the whole ingest run
- **Where:** `ingest.py:130` and `src/paper_work/ingest/lines.py:60-62`.
- **Executed:**
  - A BP `sys` of `10**20` (allowed by the schema's unbounded `integer`) raised `OverflowError` at insert. The run rolled back and **0 pages** were stored.
  - A deeply nested JSON line raised `RecursionError`. `parse_line` only catches `JSONDecodeError`, so the whole run failed instead of counting it as a bad line.
- **Why it matters:** Every model reply should be treated as hostile input. Here a single bad reply blocks ingest of every page. No data is lost, but nothing can be ingested until the line is removed by hand.
- **Fix:**
  - In `parse_line`, catch `RecursionError` and `ValueError` and turn them into a bad line.
  - In ingest, turn a BP value outside the int64 range into a page_error for that page. Don't do this in `parse_extraction`, which must accept exactly what the schema accepts.
- **Tests:** both inputs above. Other pages must still ingest.

### 5. P2: Superseded conditions and providers are never removed
- **Where:** `ingest.py:151` and `ingest.py:160`. Rows are only ever inserted with `DO NOTHING`, and nothing deletes orphans.
- **Executed:** After a re-extraction replaced "knee pain" with "headache", `condition` still held `knee pain / knee` with 0 findings. A misread provider "Dr. Misread Name" stayed with 0 visits.
- **Who is hurt:** `/api/conditions` and `/api/providers` will list a knee condition and a provider the record no longer supports. A merged provider's display name can also come from a superseded misread.
- **Fix:** In the same transaction, delete conditions with no findings and providers with no visits. Neither table has review columns, so this is safe.
- **Test:** the scenario above must leave no orphan rows.

### 6. P2: Duplicate category keys are accepted silently
- **Where:** `conditions.py:24`.
- **Executed:** `{"Knee Pain": "knee", "knee pain": "other"}` produced `{'knee pain': other}`. The last entry wins with no warning, so the condition drops out of the knee claim category.
- **Fix:** Reject keys that collide after canonicalization (the error must not name them). Add a test.

### 7. P2: `config.toml` is read twice per run
- **Where:** `src/paper_work/ingest/__main__.py:23-25`. Paths and pivot come from `load_config`; `[conditions]` comes from a second `tomllib.load`.
- **Source-traced:** If the file is saved between the two reads (paths moved to another DB along with its condition map), the old DB gets the new map. That gives wrong categories, and so wrong claim membership.
- **Fix:** Load the file once and pass the same dict to `parse_config` and `parse_category_map`.

### 8. P2: Coverage gaps
None of the behaviours in findings 1–6 has a test. Each test belongs in `tests/test_ingest.py`, or in `tests/test_normalize.py` for findings 3 and 6.

## Ruled out, with reasons
- **Check-then-act:** The first `INSERT` (`ingest.py:64`) takes the write lock before the reviewed-row check. When the pages list is empty, a concurrent review makes the delete triggers abort the run loudly; nothing is lost.
- **Meaning drift in lab and medication values:** Values, units and flags are stored as written; `_clean` only trims whitespace, so unit case is kept. BP is stored as read and only flagged.
- **Contract compliance:** Effective-line selection matches `contract/README.md`. The reviewed-row check uses `<> 'unreviewed'`, which agrees with the triggers. Foreign keys are on, and FTS is updated through the schema triggers.
- **Logs:** They name pages by pdf/page only.

## Design questions for Nano
- **Diagnoses in git:** `config.toml` is committed and says "No PHI here", but its `[conditions]` table is meant to hold condition names as extracted. `phi_guard` doesn't cover it. This predates the PR, but this PR makes the table live.
- **Provider merging:** `provider_key` sorts name tokens, so "Lee Martin" and "Martin Lee" merge. Surname-only "Dr. Smith" merges across facilities. The schema comment only lists stripping case, punctuation and titles.
- **Year pivot:** A pivot of 30 maps 27–30 to future years (2027–2030).
- **Stale dates on kept pages:** Unreviewed visits on a kept (partly reviewed) page aren't re-derived when the pivot changes.

## Optional improvements
- The schema path is resolved from the config file's directory (`__main__.py:28`), so a config file kept elsewhere breaks creating a new DB.
- `schema.sql` sets `user_version` before creating the tables. If `executescript` fails partway, the DB is left half-built but marked as version 1.
- A line with a valid pdf/page but a bad envelope appears only as a line number, not as a page_error for that page.
- Medication and condition names aren't trimmed of whitespace.

## Checks and validation gaps
- `python scripts/phi_guard.py --all`: **passed** (exit 0).
- `python -m pytest -q` at the head (scratch copy): **160 passed, 1 skipped**. The skip is `test_extraction.py:90`, because `jsonschema` isn't installed.
- The three new test files run at the base: **collection errors**. The modules don't exist there, so the tests do guard the new code.
- My probes (`$TMPDIR/rev6/probes/probe1.py` and `probe2.py`) produced the traces above, all synthetic.
- **Python version:** Only Python 3.14 was available, not 3.13, and only `pytest` was installed (not the full dev extra).
- **Web and browser tests:** `node --test tests/web/*.test.mjs` could not run because `tests/web` doesn't exist at the head. There is no Playwright, so the race tests didn't run either.
- **Not run:** Windows / `run.ps1`, real scans, a live model, a real Health export.
- **Missing docs:** `CLAUDE.md`, `docs/designs/` and `contract/migrations/` are absent from the repo, and `contract/api.md` has no Settings section.
