# Plan: import checks (phase 3, step 13b)

Branch: `feature/import-checks`. Implements step 13 of `docs/architecture.md` §12, minus PDF transactions (that is step 13c).

## Goal

Before the full re-import of the backup PDFs, the import paths refuse bad files:

1. **Duplicate check by content** — SHA-256 of each imported file is stored; the same content again is skipped as already imported; the same account + statement date + currency with *different* content is **not** imported but reported as a **conflict** for tdw (a corrected statement). The file name is dropped from the duplicate check.
2. **Date sanity** on both the text and the VL path — statement date not in the future, not before 2000, and 1–100 days after the *Date of Last Statement* printed on the same PDF when that line is present (never compared against earlier database rows — import order does not matter; a gap check across months is step 14); a failure sends the file to `errors/` with the reason.
3. **`BrokerStatement` periods** — `period_start` / `period_end` filled from the first and last transaction date in the file (architecture §6).
4. **Logging of holdings imports** — per file: parser used, sections, holdings count and the duplicate or date verdict, in the output and the JSON log. Names and counts only, never values or account digits.

## What changes

- `core/utils/import_checks.py` (new) — shared helpers:
  - `content_hash_of(path) -> str` — SHA-256 hex of the file bytes (same digest as `FileImportLog.content_hash` and `hash8_of` uses).
  - `statement_date_problems(statement_date, *, today, last_known) -> list[str]` — returns reasons: future date, before 2000-01-01, and (when `last_known` is given) not 1–100 days after it. Empty list = OK. `last_known` is the *Date of Last Statement* printed on the same PDF (a `date` or `None`) — never a database value (tdw, Oct 27).
  - `is_content_imported(content_hash) -> bool` — True when a successful `FileImportLog` (`status` in `success`/`ok`) already has that `content_hash`.
- `core/models.py` + one migration — `content_hash = CharField(max_length=64, blank=True, default="")` on **`RbcMonthly`** and **`RbcAnnual`** (needed to tell "same content" from "same key, different content" per section). `FileImportLog.content_hash` already exists; `BrokerStatement.period_start/period_end` already exist. Migration is applied by tdw on mercury after a backup; Pi only runs it on SQLite.
- `core/management/commands/manage_inbox_import.py`:
  - compute the file's SHA-256 once per PDF (before classification; `hash8` becomes its first 8 chars); if a successful `FileImportLog` with that `content_hash` exists, skip the file as a duplicate (no classify, no VL call): `FileImportLog` row with `status='duplicate'`, JSON log `"verdict": "duplicate"`, and the PDF moves to `processed/` under a uniquified safe name (reusing the previously stored safe name when possible, never overwriting the original). Store `content_hash` in every `_log_import` call.
  - monthly sections, **two-pass** (a file is imported all or nothing — tdw, Oct 27): first validate **every** section (date sanity, then duplicate/conflict on account + `statement_date` + `currency`; `file_name` leaves the check); only when no section is a conflict or a date failure are the non-duplicate sections stored. A stored row whose `content_hash` equals the file's hash → duplicate section (reported in stdout/JSON, not re-stored); any other hit — including a stored row with an empty `content_hash` — → **conflict**: nothing from the file is stored, error message names account/currency/date only, file goes to `errors/`. Store `content_hash` on create.
  - annual sections: same two-pass pattern on account + `report_year` + `currency`.
  - date sanity: after parsing (so it covers the text **and** the VL result), run `statement_date_problems` per monthly section with `last_known` = the *Date of Last Statement* printed on the same PDF for that section (`parsed['last_statement_date']`, as in the step 12 self-check) — never a database lookup (tdw, Oct 27). For annuals: `report_period_end` not in the future and not before 2000 (no 1–100-day rule). Any failure rejects the whole file → `errors/` with the reason in the JSON log and `FileImportLog.error_message`.
  - add the verdict per file to the JSON log (`"verdict": "imported" | "duplicate" | "conflict" | "date_error" | "error"`): `duplicate` when nothing is stored because the content is already imported, `imported` when at least one section was stored, `error` for parse/section failures. Parser and section counts are already logged.
- `imports/auto_import.py` — after the transactions are imported, set `statement.period_start` / `period_end` to the min/max transaction date and save (one `BrokerStatement` per file, unchanged otherwise).
- `core/management/commands/import_csv.py` (`--holdings` path) and `imports/parsers_holdings.py`:
  - content-hash duplicate check via `FileImportLog` (same rule as `auto_import`: a successful log with that hash → skip before parsing, move to `processed/` under a uniquified name), `FileImportLog` entry per holdings file (architecture §6: holdings imports are logged too), snapshot-date sanity (not future, not before 2000; no 1–100-day rule) → `errors/` with reason. The checks run in the command; `import_holdings_snapshot` keeps its signature (existing tests call it directly), and a new `holdings_import_report(file_path)` in `parsers_holdings.py` returns the parsed positions, the snapshot date and per-(account name, currency) counts.
  - per file, in stdout and a JSON log written to `IMPORT_JSON_DIR`: parser used (`holdings_csv`), sections (account name + currency groups), holdings count, and the duplicate/date verdict. Names and counts only — never quantities, prices or account digits.

## Out of scope

- PDF transactions / `RbcMonthlyTransaction` (step 13c).
- No change to `dedupe_monthly_statements` or `compare_monthly_pdf`.
- Activity-CSV dedupe (already content-hash based in `auto_import`), the exceptions page (step 14), the watcher, `reconcile`, ledger, anything parked.
- No new data is imported; Pi never touches mercury.

## Steps (one commit each)

1. **Helpers** — add `core/utils/import_checks.py` with `content_hash_of`, `statement_date_problems` and `is_content_imported`; unit tests in `tests/test_import_checks.py` for the three date rules and the hash (fake accounts only).
   Check: `/opt/pm-venv/bin/python -m pytest tests/ --collect-only -q` collects the new tests (no test run yet — tests run once, in step 8).
2. **Migration** — `content_hash` on `RbcMonthly` and `RbcAnnual`; run `makemigrations` once, never hand-edit.
   Check: `/opt/pm-venv/bin/python manage.py makemigrations --check --settings=portfolio_manager.settings_test` is clean.
3. **File-level content duplicate check** in `manage_inbox_import` (skip before classify — no classify, no VL call; `FileImportLog` row with `status='duplicate'` and the `content_hash`; verdict in output + JSON; PDF to `processed/` under a uniquified safe name).
   Check: `pytest --collect-only -q` still collects; `git status` shows only the expected files.
4. **Section-level duplicate/conflict, two-pass** for monthly and annual (validate every section first; drop `file_name` from the query; compare `content_hash`; any conflict → nothing stored, whole file to `errors/` + reason; a stored row without `content_hash` counts as different content).
   Check: `rg -n "file_name=safe_name" core/management/commands/manage_inbox_import.py` finds no duplicate-query hit; `pytest --collect-only -q`.
5. **Date sanity** on the monthly (text + VL) and annual paths, using `statement_date_problems` with `last_known` from the PDF's own *Date of Last Statement* line (never the DB); any failure → whole file to `errors/` with reason.
   Check: `rg -l "statement_date_problems"` lists helper + command + tests; `git status`.
6. **`BrokerStatement` periods** in `auto_import` (min/max transaction date).
   Check: `rg -n "period_start" imports/auto_import.py` shows the assignment; `git status`.
7. **Holdings import logging + protections** in `import_csv --holdings` (content-hash dedupe, `FileImportLog`, snapshot-date check, JSON log with parser/sections/counts/verdict, names and counts only).
   Check: `rg -n "FileImportLog" core/management/commands/import_csv.py` matches; `pytest --collect-only -q`.
8. **Full tests, once:**
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`
   Then commit any fixes; summary for tdw including the migration note (apply on mercury after backup).

## Stop conditions

- Stop and ask if: the conflict semantics would need a new model/table; a step needs a second migration number clash (see AGENTS.md gotcha); the text parser turns out to expose no per-section date on some format; anything would touch `dedupe_monthly_statements`, `compare_monthly_pdf` or PDF transaction storage; a test fails against production settings (never do that); a needed package is missing.
- Never print or commit `.env`, statement files, or real account numbers; test fixtures are the fake accounts `90000001`–`90000008` on SQLite.

## Open questions

1. **What does "logging of holdings imports" mean — the Holdings CSV import (`import_csv --holdings`) or the holdings sections of the monthly PDFs?**
   Recommended: the Holdings CSV import — it currently writes no `FileImportLog` and no JSON log at all, while the monthly PDF JSON log already has parser, sections and counts (it only gains the verdict in step 3–5). The plan covers both cheaply.
2. **Where does a conflict file go?**
   Recommended: `processed/errors/` with the reason (same as a date failure), so tdw sees corrected statements in one place; the JSON log gets `"verdict": "conflict"`. No exceptions-page entry yet (that is step 14).
3. **Scope of the 1–100-day rule: per account or per account+currency?**
   Recommended: per account **and currency** — `RbcMonthly` is one row per currency section, and CAD/USD sections of one file share a statement date anyway.
4. **Annual reports: which date gets the sanity check?**
   Recommended: `report_period_end` — not in the future, not before 2000; the 1–100-day rule does not apply to annuals.
5. **Is a section-level duplicate enough to also call the whole file a duplicate?**
   Recommended: no — the file-level `content_hash` check (step 3) is the only "already imported" skip; section-level key matches with the same hash are reported as duplicates in the JSON log but the file still moves to `processed/` normally.

## Decisions (tdw, Oct 27)

Answers to the open questions; the plan above is updated to match.

1. **Logging of holdings imports — which path?** Both: the Holdings CSV import (`import_csv --holdings`, step 7) and the monthly PDF holdings (they ride on the monthly JSON log, which gains the verdict in steps 3–5), as the plan covers them.
2. **Where does a conflict file go?** Yes, as recommended: `processed/errors/` with the reason; JSON log `"verdict": "conflict"`. No exceptions-page entry yet (that is step 14).
3. **Scope of the 1–100-day rule.** No — not per account and not per account+currency from the database: the rule compares the statement date with the *Date of Last Statement* printed on the same PDF (as in the step 12 self-check), never with earlier rows in the database, so the import order does not matter. A gap check across months is step 14.
4. **Annual reports: which date gets the sanity check?** Yes, as recommended: `report_period_end` — not in the future, not before 2000; the 1–100-day rule does not apply to annuals.
5. **Is a section-level duplicate enough to also call the whole file a duplicate?** As recommended, plus: a file is imported all or nothing — if any section is a conflict the whole file goes to `errors/` and nothing from it is stored (the same applies to a date failure); a stored row without a `content_hash` counts as different content.
