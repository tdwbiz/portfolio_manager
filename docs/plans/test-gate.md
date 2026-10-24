# Plan: test gate — collect imports/tests in the normal pytest run and green up test_csv_parsers.py

Branch: `chore/test-gate`. No parser code changes in this branch.

## Goal

`imports/tests/` becomes part of the normal (non-browser) test run, and
`imports/tests/test_csv_parsers.py` passes. Today `pytest imports/tests/` cannot even
collect (no Django settings), and 20 of 33 tests in `test_csv_parsers.py` fail.

## Evidence (gathered during planning, `pytest -q --tb=line`)

1. **Collection error**: `pytest imports/tests/` →
   `ImproperlyConfigured: Requested setting INSTALLED_APPS…` — `imports/tests/` has no
   conftest and `pytest.ini` has no `DJANGO_SETTINGS_MODULE`; only `tests/conftest.py:23`
   sets it, and it is not loaded for `imports/tests/`. The other three files in
   `imports/tests/` (test_audit_fx, test_import_csv_command, test_parsers_pdf — 20 tests)
   all pass once `DJANGO_SETTINGS_MODULE=portfolio_manager.settings_test` is in the
   environment. pytest-django 4.14 is installed; running
   `pytest tests/ imports/tests/ --collect-only -q -o DJANGO_SETTINGS_MODULE=portfolio_manager.settings_test`
   collects 76 tests cleanly, so the ini option alone is enough wiring.

2. **All 20 failures in `test_csv_parsers.py` are test-side, not parser bugs.** Two
   fixture defects (verified by re-running every failing case through the real parser
   with a correct CSV writer):
   - `_write_csv` (imports/tests/test_csv_parsers.py:17) joins cells with bare commas and
     never quotes. Real RBC CSVs quote fields, so `'October 08, 2025'` becomes two cells
     (`October 08` / ` 2025`), shifting every column; the parser logs
     `Row 2: Could not parse date 'October 08'` (imports/parsers.py:552) and drops the row.
     Same defect breaks `'-100,000.00'` (comma splits the value).
   - The IB tests call `_write_csv([content])` with one multi-line **string**;
     `','.join(row)` on a string inserts a comma between every character, so the IB
     files are garbage. Every IB test that expects ≥1 trade fails with 0.

   With a proper `csv.writer`-based helper, 16 of 20 RBC failures and all 6 IB failures
   pass unchanged. The remaining 4 are stale expectations (each verified against the
   current parser):

   | Test | Expectation | Actual (quoted CSV) | Verdict |
   |---|---|---|---|
   | `test_parses_dividend_reinvestment` | activity `'Div Reinvest'` → type `DIV REIN` | `DIV_REINVEST` (unknown-string fallback, parsers.py:96) | **Stale.** `'Div Reinvest'` is not a real RBC Activity value (not in `_RBC_TYPE_MAP`, parsers.py:52). Per AGENTS.md, real CSV reinvestments are `DIV`/`DIST` rows with `qty > 0` and negative `amount_cad`. |
   | `test_parses_deposit` | activity `'Deposit'` → `DEP` | `DEPOSIT` (fallback) | **Stale.** Real RBC label is `'Deposits & Contributions'` (in the map). |
   | `test_zero_quantity_skipped` | qty-0 `Buy` row dropped | parser keeps it (no zero-qty rule) | **Stale/aspirational** — behaviour never existed; a blanket qty-0 skip would also drop legitimate qty-0 dividends. |
   | `IbCsvParserTests::test_dividend_imported` | type `'DIVIDEND'` → `DIV` | `DIVIDEND` (fallback; map key is `'Dividend'`, parsers.py:26) | **Stale.** Real IB type value is `Dividend`. |

   No test was found that exposes a genuine parser bug against the real CSV formats;
   the map-fallback questions go to Open questions (decided — see Decisions).

## What changes

- `pytest.ini` — add `DJANGO_SETTINGS_MODULE = portfolio_manager.settings_test`
  (pytest-django reads this; `tests/conftest.py`'s `setdefault` then becomes a no-op)
  and add `imports/tests` to `testpaths` so a bare `pytest` also collects them.
- `imports/tests/test_csv_parsers.py` — fix `_write_csv` to use `csv.writer` (quotes
  fields like the real files); add a `_write_text(path, text)` helper and use it in the
  IB tests instead of `_write_csv([content])`; fix/delete the 4 stale tests per the
  table above (rewrite dividend-reinvestment to the real REI row shape; use
  `'Deposits & Contributions'` for deposit; `'Dividend'` for the IB dividend fixture;
  delete `test_zero_quantity_skipped`).
- `AGENTS.md` — test commands section: normal pytest run becomes
  `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/` (and the RUN_E2E line gets
  the same paths).

No migrations. No parser, command, or app-code changes.

## Out of scope

- Any change to `imports/parsers.py` (parser bugs found → Open questions only).
- The other `imports/tests/*` files (already green once settings are configured).
- `-x` in `pytest.ini` addopts (stops the suite at the first failure; decided untouched —
  see Decisions).
- `tests/e2e` gating (already handled by docs/plans/test-setup.md).

## Steps

1. **Wire pytest to collect imports/tests.** Edit `pytest.ini` as above.
   Check: `/opt/pm-venv/bin/python -m pytest imports/tests --collect-only -q` collects
   53 items with no errors; `git diff --stat` shows only `pytest.ini`; commit
   ("pytest: collect imports/tests with settings_test").
2. **Fix the fixture helpers** in `imports/tests/test_csv_parsers.py`
   (`csv.writer` in `_write_csv`; `_write_text` for the IB tests).
   Check: `DJANGO_SETTINGS_MODULE=portfolio_manager.settings_test /opt/pm-venv/bin/python -m pytest imports/tests/test_csv_parsers.py -q --tb=line --maxfail=100`
   leaves exactly the 4 stale failures from the table (and nothing else); commit
   ("test_csv_parsers: quote CSV cells and write IB fixtures as text").
3. **Fix/delete the 4 stale tests** per the table.
   Check: same pytest command → `33 passed`; commit
   ("test_csv_parsers: drop stale expectations (DIV REIN, DEP, zero-qty, DIVIDEND)").
4. **Update AGENTS.md** test commands to include `imports/tests`.
   Check: `rg -n "pytest tests" AGENTS.md` shows the new paths; `git status` clean after
   commit ("docs: AGENTS.md test commands include imports/tests").

## Final tests (run once, after step 3/4)

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
```

Both must pass.

## Stop conditions

- If step 2 leaves any failure beyond the 4 listed, stop and report (do not "fix"
  anything in `imports/parsers.py`).
- If pytest-django's ini-based setup conflicts with `tests/conftest.py` (collection
  errors in `tests/`), stop and report instead of restructuring the conftest.
- Any need to touch parser code, `.env`, or the database → stop.

## Open questions

1. **Case-insensitive type maps?** `_normalize_type` matches `_RBC_TYPE_MAP`/`_IB_TYPE_MAP`
   keys exactly, so `buy`, `DIVIDEND`, `FEE` fall through to the sanitising fallback and
   auto-create `TransactionType` rows with odd labels. *Recommended:* make the lookup
   case-insensitive in a follow-up branch (one-line change in `_normalize_type`); do not
   do it here (parser code is out of scope).
2. **`'Div Reinvest'` → `DIV REIN`?** If real RBC activity exports ever emit this label,
   add it to `_RBC_TYPE_MAP`. *Recommended:* tdw checks one real Activity CSV; per
   AGENTS.md the real reinvestment rows are `DIV`/`DIST` with qty > 0, so probably no
   change is needed.
3. **`'Deposit'` → `DEP`?** Same pattern as Q2 for the shorter label. *Recommended:* add
   to the map only if it appears in real exports; the test now uses the real label.
4. **Should qty-0 BUY/SELL rows with zero amount be skipped?** The deleted
   `test_zero_quantity_skipped` assumed this rule; it never existed, and a blanket qty-0
   skip would drop legitimate qty-0 dividends. *Recommended:* leave the parser as is;
   only add a BUY/SELL-specific skip if placeholder rows are actually seen.
5. **`-x` in `pytest.ini` addopts** aborts the whole gate on the first failure, which
   makes triage need `--maxfail` overrides. *Recommended:* tdw decides later whether to
   drop it; left untouched here.

## Decisions (tdw, Oct 24)

tdw accepts the recommended answer for every open question above:

1. **Case-insensitive type maps** — deferred to a follow-up branch (one-line change in
   `_normalize_type`); no parser changes on this branch.
2. **`'Div Reinvest'` → `DIV REIN`** — not added to `_RBC_TYPE_MAP`. Real reinvestment
   rows are `DIV`/`DIST` with qty > 0 and negative amount (AGENTS.md). Revisit only if
   the label is actually seen in a real Activity CSV.
3. **`'Deposit'` → `DEP`** — not added; the test uses the real label
   `'Deposits & Contributions'`. Revisit only if the short label appears in real exports.
4. **qty-0 BUY/SELL skip** — parser left as is; `test_zero_quantity_skipped` is deleted
   (step 3). A BUY/SELL-specific skip would be added only if placeholder rows are
   actually seen.
5. **`-x` in addopts** — left untouched on this branch; tdw decides later.
