# Plan: fix vl-parser test failures and drop dead VL settings fallbacks

## Goal

Make `tests/test_vl_parsers.py` pass fully (currently 4 failures + 1 error), and
remove the dead `getattr(settings, ...)` fallbacks for `VL_PAGE_DPI`,
`VL_MAX_RETRIES` and `VL_TIMEOUT_SECONDS` in the three VL modules — settings
always defines them, so the fallbacks are dead weight and in one case
contradict settings (see Background).

Current state (`/opt/pm-venv/bin/python -m pytest tests/test_vl_parsers.py --maxfail=50`):
**4 failed, 15 passed, 1 error.**

## Background — what was found

### (a) `extract_statement_date` — the 4 `ExtractStatementDateTest` failures

Git archaeology (`git log -S "extract_statement_date" --all`):

- `4f8991e` (Jul 2026) added `extract_statement_date(content: str) -> date | None`
  to `imports/auto_import.py` — a regex scan of raw **CSV text** for
  "Statement Date: MM/DD/YYYY", "Period End", "Report Date", ISO dates, etc.
  The same commit added `tests/test_vl_parsers.py` with the four tests.
- `22f8e9d` (Oct 2026, "Ledger closure views, holdings/cash reconciliation
  commands, parser and import updates") **deleted the function** (the diff
  replaces it with `_setup_duplicate_logger`) but left the tests behind. That
  is the ImportError: the tests reference a function that no longer exists.
- **No caller exists or did exist**: `auto_import()` never called it; today
  `auto_import()` uses `file_date=timezone.now().date()` (`imports/auto_import.py` ~L133).
- **No current function does the same job.** Nearest neighbours, none equivalent:
  - `_parse_rbc_monthly_regex()` in `imports/parsers_monthly_vl.py` — extracts
    "As of Date" from **PDF** text (not CSV content), covered by `RegexFallbackTest`.
  - `_parse_date()` helpers in `parsers_monthly_vl.py` / `parsers_annual_vl.py` /
    `parsers_holdings.py` — parse one known date string, no searching.
- `specs/PhaseC/C2-Spec-1_statement_date_and_llm_fallback.md` planned a
  *different* `extract_statement_date(trades, file_path, broker)` that was
  never implemented in that form.

### (b) `test_classify_monthly_statement` — the 1 error

`tests/test_vl_parsers.py` ~L227: the method is indented at module level, so
pytest collects it as a standalone function; `self` becomes a missing fixture
→ "fixture 'self' not found". It belongs inside `WatcherClassificationTest`
(it uses `self._create_test_pdf`). Pure indentation fix.

### (c) `getattr(settings, ...)` fallbacks

Settings (`portfolio_manager/settings.py` L259–264) always defines
`VL_PAGE_DPI` (150), `VL_MAX_RETRIES` (2), `VL_TIMEOUT_SECONDS` (600) and
`VL_MONTHLY_MAX_PAGES` (0); `settings_test.py` imports `*` from settings.
Call sites still using `getattr` with hardcoded fallbacks:

| File | Lines | Settings |
|---|---|---|
| `imports/parsers_monthly_vl.py` | 378–380 | `VL_PAGE_DPI`, `VL_MAX_RETRIES`, `VL_TIMEOUT_SECONDS` |
| `imports/parsers_monthly_vl.py` | 424 | `VL_MONTHLY_MAX_PAGES` |
| `imports/parsers_annual_vl.py` | 171–173 | `VL_PAGE_DPI`, `VL_MAX_RETRIES`, `VL_TIMEOUT_SECONDS` |
| `imports/classify_pdf_vl.py` | 126–128 | `VL_PAGE_DPI`, `VL_MAX_RETRIES`, `VL_TIMEOUT_SECONDS` |

Note the fallbacks are not just dead, they are **wrong**: `VL_TIMEOUT_SECONDS`
fallback is 120 but settings says 600; `VL_MAX_RETRIES` fallback 2 matches.
Same pattern as the `VL_MODEL_NAME` fallbacks already removed in the
vl-halogen work (commit `27713f0`) — this finishes that cleanup.

`getattr` uses for `IMPORT_*` settings in watcher/management commands are
**out of scope** (different settings, not part of this task).

## What changes

1. **`ExtractStatementDateTest`** — resolve per tdw's choice (see Open
   questions). Recommended: **delete the whole test class** (dead code — the
   function was deliberately removed in `22f8e9d`, nothing calls it, and no
   replacement is planned; the CSV master flow does not need header-date
   extraction). If tdw prefers to keep the capability, instead restore the
   function into `imports/auto_import.py` verbatim from `git show
   4f8991e:imports/auto_import.py` (option 2) — the tests then pass unchanged.
   Option 3 (point the tests at an existing function) is **not viable**: no
   existing function takes CSV content and searches for statement-date
   patterns.
2. **`tests/test_vl_parsers.py`** — re-indent `test_classify_monthly_statement`
   into `WatcherClassificationTest` (after `test_classify_annual_report`).
3. **`imports/parsers_monthly_vl.py`, `imports/parsers_annual_vl.py`,
   `imports/classify_pdf_vl.py`** — replace
   `getattr(settings, 'VL_X', default)` with `settings.VL_X` for the three
   settings (and `VL_MONTHLY_MAX_PAGES` if tdw confirms — see Open questions).

No migrations. No model/view/template changes.

## Tests

- `/opt/pm-venv/bin/python -m pytest tests/test_vl_parsers.py --maxfail=50` →
  all pass (15 pass + the fixed one; minus 4 if the class is deleted → expect
  16 passed, 0 failed, 0 errors).
  Note: `pytest.ini` `addopts` contains `-x`; pass `--maxfail=50` to see all
  failures at once.
- `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test` →
  unchanged (220 pass).
- `/opt/pm-venv/bin/python -m pytest tests/ -v` → full pytest suite green.

## Steps

1. Ask tdw which option for (a); do not proceed on step 2 until answered.
2. Fix `test_classify_monthly_statement` indentation (b). Run pytest, confirm
   the error is gone and the test passes. Commit.
3. Apply the chosen resolution for `ExtractStatementDateTest` (a). Run pytest,
   confirm green. Commit.
4. Replace the `getattr` fallbacks with `settings.X` in the three VL modules
   (c), including `VL_MONTHLY_MAX_PAGES` if approved. Run both test suites.
   Commit.

## Open questions

1. **Which resolution for `ExtractStatementDateTest`?** Recommended: delete the
   class (option 1). Option 2 (restore `extract_statement_date` from commit
   `4f8991e`) only makes sense if tdw wants CSV-header statement dates again —
   nothing currently consumes them. Option 3 (repoint at an existing function)
   is not possible; no equivalent exists.
2. **Include `VL_MONTHLY_MAX_PAGES`** (`parsers_monthly_vl.py` L424) in the
   getattr cleanup? It is the same dead-fallback pattern and settings always
   defines it, but it was not in the original list.
3. `getattr` fallbacks for `IMPORT_INBOX_DIR`, `IMPORT_PROCESSED_DIR`, etc. in
   watcher/management commands are the same pattern but out of scope here —
   leave them (assumed yes)?
