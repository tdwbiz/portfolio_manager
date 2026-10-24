# Import paths — phase 1 step 5 (audit 3 batch 1)

Branch: `refactor/import-paths`. Source: `docs/architecture.md` §6 + §12 phase 1 step 5, and
`docs/audit/03-commands-scripts-docs.md` batch 1 as amended by the "Decisions" section below
(Q3: `imports/parsers_pdf.py` and `docs/TEXT_PARSER_IMPLEMENTATION.md` stay).

## Goal

One import path per source, and the inbox watcher calls only those paths:

| Source | Canonical path |
|---|---|
| Activity CSV | `import_csv` |
| Holdings CSV | `import_csv --holdings` |
| PDFs (monthly + annual) | `manage_inbox_import` |

Delete the duplicate/dead import commands, the web upload pages and the dead annual text parser.
`imports/parsers_pdf.py` stays (checked in step 12).

## What changes (files)

Deleted:

- `core/management/commands/manage_pdf_import.py`
- `core/management/commands/manage_annual_report_import.py`
- `imports/management/commands/auto_import.py` (the **command**; the function `imports/auto_import.py` stays — `import_csv` uses it)
- `imports/management/commands/import_statements.py`
- `imports/views.py` (web upload pages: `pdf_import_view`, `annual_report_import_view`, rate-limit helpers)
- `imports/templates/core/pdf_import.html`
- `imports/parsers_annual.py` (dead text parser) + `imports/tests/test_annual_report_parser.py`
- `imports/tests/test_import_statements.py`
- `tests/e2e/test_pdf_import.py` (audit decision Q1)

Edited:

- `core/urls.py` — drop `pdf-import/` and `annual-report-import/` routes and the `imports import views` import
- `core/templates/core/annual_reports.html` — remove the "Import one now" link to the deleted page
- `imports/watcher.py` — call `import_csv`, `import_csv --holdings`, `manage_inbox_import`; drop `_classify_pdf` (manage_inbox_import classifies PDFs itself)
- `imports/tests/test_parsers_pdf.py` — tests 5/6 call `manage_pdf_import`; port them to `manage_inbox_import`
- `imports/tests/test_auto_import.py` — port the RBC cases to `import_csv`, drop the IB / `--broker` cases
- `imports/__init__.py` — `__all__` drops `views`, `parsers_annual`
- comment/docstring mentions in `imports/parsers_monthly_vl.py`, `imports/parsers_annual_vl.py`
- `tests/test_vl_parsers.py` — delete the `WatcherClassificationTest` class (it tests the removed `_classify_pdf`; the canonical path uses `classify_pdf`, already tested by `FilenameHeuristicTest`)
- `tests/e2e/test_e2e_user_journey.py` — remove the two `pdf-import/` tests
- `docs/IMPORT_COMMANDS.md`, `docs/README.md`, `AGENTS.md` — remove the deprecated commands and the upload pages

Not touched: `imports/parsers_pdf.py`, `docs/TEXT_PARSER_IMPLEMENTATION.md`, `imports/auto_import.py`
(the function), `imports/parsers_holdings.py`, `core/management/commands/manage_inbox_import.py`,
`imports/management/commands/watch_inbox.py` (only calls `run_watcher_loop`).

## Out of scope

- Step 12: the `parsers_pdf.py` text-parser check, PDF holdings/cash/totals, VL fallback.
- Audit 3 batch 2 (junk artifacts), batches 3/4/7 (history moves), batch 5 (stale docs, mkdocs), batch 6 (parked labels), batch 8 (`DividendSchedule`).
- Account numbers / fingerprints (phase 2), duplicate detection changes, sanity checks (step 13).
- Any behaviour change in `import_csv`, `import_csv --holdings` or `manage_inbox_import`.
- Playwright setup on saturn (batch 9, tdw).

## Steps

1. **Remove the web upload pages.** Delete `imports/views.py`, `imports/templates/core/pdf_import.html`,
   `tests/e2e/test_pdf_import.py`; remove the two routes + `import_views` import in `core/urls.py`, the
   link in `annual_reports.html`, the two `pdf-import/` tests in `tests/e2e/test_e2e_user_journey.py`.
   Check: `/opt/pm-venv/bin/python manage.py check --settings=portfolio_manager.settings_test` and
   `git grep -n "pdf_import_view\|annual_report_import_view"` → no hits.
2. **Rewire the watcher.** In `imports/watcher.py`: CSVs → `call_command('import_csv')` then
   `call_command('import_csv', '--holdings')`; PDFs → `call_command('manage_inbox_import', inbox_dir=...)`;
   delete `_classify_pdf` and the annual/statement splitting.
   Check: `git grep -n "_classify_pdf\|manage_pdf_import\|manage_annual_report_import" -- imports/watcher.py` → no hits.
3. **Delete the deprecated PDF commands.** Remove `manage_pdf_import.py` and `manage_annual_report_import.py`;
   port `imports/tests/test_parsers_pdf.py` tests 5/6 to `manage_inbox_import` with a temp inbox dir
   (keep the `parse_rbc_monthly_vl` mock); fix the stale command mentions in `imports/parsers_monthly_vl.py`
   and `imports/parsers_annual_vl.py`.
   Check: `/opt/pm-venv/bin/python -m pytest imports/tests/test_parsers_pdf.py --collect-only -q` and
   `git grep -ln "manage_pdf_import" -- '*.py'` → no hits.
4. **Delete the deprecated CSV commands.** Remove `imports/management/commands/auto_import.py` and
   `imports/management/commands/import_statements.py` and `imports/tests/test_import_statements.py`;
   port `imports/tests/test_auto_import.py` to `import_csv` (drop the IB / `--broker` cases — Q1) and
   rename it `imports/tests/test_import_csv_command.py` to avoid a clash with
   `core/tests/test_csv_import.py`.
   Check: `git grep -ln "import_statements" -- '*.py'` → no hits, and
   `/opt/pm-venv/bin/python -m pytest imports/tests --collect-only -q`.
5. **Delete the dead annual text parser.** Remove `imports/parsers_annual.py` and
   `imports/tests/test_annual_report_parser.py`; drop `parsers_annual` from `imports/__init__.py`.
   Check: `git grep -ln "parsers_annual\b" -- '*.py'` → no hits (only `parsers_annual_vl`).
6. **Docs.** Update `docs/IMPORT_COMMANDS.md` (drop `auto_import`, `import_statements`, and the
   deprecated-command section), `docs/README.md` (command table + `imports/` tree) and `AGENTS.md`
   (the "DEPRECATED" line and the `auto_import` row in the import table).
   Check: `git grep -n "manage_pdf_import\|manage_annual_report_import\|import_statements" -- AGENTS.md docs/IMPORT_COMMANDS.md docs/README.md` → no hits.
7. **Full test run** (see Tests) and commit any leftovers found there.

## Tests

Only at the end (step 7), both must pass:

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/
```

Also worth running once: `/opt/pm-venv/bin/python -m pytest imports/tests --collect-only -q` to prove no
test module still imports a deleted module, and
`/opt/pm-venv/bin/python manage.py makemigrations --check --settings=portfolio_manager.settings_test`
(no migrations expected — no models change).

## Stop conditions

- A test or view turns out to need real statement data, an inbox path outside the test settings, or the
  mercury database → stop and ask.
- `manage_inbox_import` cannot be driven from a test with a temp `--inbox-dir` (e.g. it insists on
  `processed/json_logs` under the real project dir) → stop; decide between overriding settings in the
  test and dropping those two parser tests.
- Porting `test_auto_import.py` changes what `import_csv` does (it is RBC-only, no `--broker`) → stop and
  ask before touching `import_csv`.
- Any account number, `.env` content or statement text would have to be copied into code/docs → stop.
- A step reveals a live caller of a "dead" module that this plan did not list → stop and update the plan.

## Open questions

1. `imports/tests/test_auto_import.py` (10 tests) tests the deleted **command**, including IB broker
   detection and `--broker` override.
   **Recommended:** port the RBC/inbox/FileImportLog/duplicate-hash cases to `import_csv`, delete the IB
   and `--broker` cases (the IB import is a separate future track, architecture §6).
2. `imports/tests/test_parsers_pdf.py` tests 5 and 6 exercise `manage_pdf_import`.
   **Recommended:** port them to `manage_inbox_import` with a temp inbox dir and the existing VL mock;
   if that proves awkward, delete the two command-level tests and keep the direct `parse_rbc_pdf` tests —
   the parser itself is re-checked in step 12.
3. Must the watcher run `import_csv --holdings` on every poll?
   **Recommended:** yes, right after the activity run; both runs are incremental (only files left in
   `inbox/` are parsed, activity import skips `holdings*` files), so no double-import.
4. `docs/README.md` is also in audit 3 batch 5 (stale docs).
   **Recommended:** make only the minimal command-list/tree corrections here; leave the rest to batch 5.

## Decisions (tdw, Oct 24)

tdw accepts all four recommended answers from the open questions above:

1. **Q1 — `test_auto_import.py`:** port the RBC/inbox/FileImportLog/duplicate-hash cases to
   `import_csv`; delete the IB and `--broker` cases (IB import is a separate future track,
   architecture §6).
2. **Q2 — `test_parsers_pdf.py` tests 5/6:** port them to `manage_inbox_import` with a temp inbox dir
   and the existing VL mock; if that proves awkward, delete the two command-level tests and keep the
   direct `parse_rbc_pdf` tests (the parser is re-checked in step 12).
3. **Q3 — watcher:** yes, run `import_csv --holdings` on every poll, right after the activity run;
   both runs are incremental, so no double-import.
4. **Q4 — `docs/README.md`:** make only the minimal command-list/tree corrections in this plan; leave
   the rest of batch 5 (stale docs) to its own step.

Recorded after step 2:

5. **`tests/test_vl_parsers.py`:** delete the `WatcherClassificationTest` class. It tests the removed
   `_classify_pdf`; the canonical path uses `classify_pdf`, which is already tested by
   `FilenameHeuristicTest` in the same file.
6. **Deleted templates:** the two templates actually deleted in step 1 —
   `core/templates/core/pdf_import.html` and `imports/templates/core/annual_report_import.html` — are
   accepted as part of the web upload pages (the plan's Deleted list named only one of them).
