# Plan: import checks (phase 3, step 13b)

Branch: `feature/import-checks`. Implements step 13 of `docs/architecture.md` §12, minus PDF transactions (that is step 13c).

## Goal

Before the full re-import of the backup PDFs, the import paths refuse bad files:

1. **Duplicate check by content** — SHA-256 of each imported file is stored; the same content again is skipped as already imported; the same account + statement date + currency with *different* content is **not** imported but reported as a **conflict** for tdw (a corrected statement). The file name is dropped from the duplicate check.
2. **Date sanity** on both the text and the VL path — statement date not in the future, not before 2000, and 1–100 days after the last known statement date when one is known; a failure sends the file to `errors/` with the reason.
3. **`BrokerStatement` periods** — `period_start` / `period_end` filled from the first and last transaction date in the file (architecture §6).
4. **Logging of holdings imports** — per file: parser used, sections, holdings count and the duplicate or date verdict, in the output and the JSON log. Names and counts only, never values or account digits.

## What changes

- `core/utils/import_checks.py` (new) — shared helpers:
  - `content_hash_of(path) -> str` — SHA-256 hex of the file bytes (same digest as `FileImportLog.content_hash` and `hash8_of` uses).
  - `statement_date_problems(statement_date, *, today, last_known) -> list[str]` — returns reasons: future date, before 2000-01-01, and (when `last_known` is given) not 1–100 days after it. Empty list = OK.
- `core/models.py` + one migration — `content_hash = CharField(max_length=64, blank=True, default="")` on **`RbcMonthly`** and **`RbcAnnual`** (needed to tell "same content" from "same key, different content" per section). `FileImportLog.content_hash` already exists; `BrokerStatement.period_start/period_end` already exist. Migration is applied by tdw on mercury after a backup; Pi only runs it on SQLite.
- `core/management/commands/manage_inbox_import.py`:
  - compute the file's SHA-256 once per PDF (before classification); if a successful `FileImportLog` with that `content_hash` exists, skip the file as a duplicate (no classify, no VL call); store `content_hash` in every `_log_import` call; verdict `duplicate` in stdout and JSON log.
  - monthly section loop: replace the current duplicate query (which filters on `file_name` — the file name leaves the check) with account + `statement_date` + `currency`; on a hit, compare the stored `content_hash`: same hash → duplicate skip, different hash → **conflict** (section not imported, error message names account/currency/date only, file goes to `errors/`). Store `content_hash` on create.
  - annual section loop: same pattern on account + `report_year` + `currency`.
  - date sanity: after parsing (so it covers the text **and** the VL result), run `statement_date_problems` per monthly section; `last_known` = latest `RbcMonthly.statement_date` for the same account **and currency**. For annuals: `report_period_end` not in the future and not before 2000 (no 1–100-day rule). A failure is a file error → `errors/` with the reason in the JSON log and `FileImportLog.error_message`.
  - add the duplicate/date verdict per file to the JSON log (`"verdict": "imported" | "duplicate" | "conflict" | "date_error" | "error"`) — parser and section counts are already logged.
- `imports/auto_import.py` — after the transactions are imported, set `statement.period_start` / `period_end` to the min/max transaction date and save (one `BrokerStatement` per file, unchanged otherwise).
- `core/management/commands/import_csv.py` (`--holdings` path) and `imports/parsers_holdings.py`:
  - content-hash duplicate check via `FileImportLog` (same rule as `auto_import`), `FileImportLog` entry per holdings file (architecture §6: holdings imports are logged too), snapshot-date sanity (not future, not before 2000; no 1–100-day rule) → `errors/` with reason.
  - per file, in stdout and a JSON log written to `IMPORT_JSON_DIR`: parser used (`holdings_csv`), sections (account name + currency groups), holdings count, and the duplicate/date verdict. Names and counts only — never quantities, prices or account digits.

## Out of scope

- PDF transactions / `RbcMonthlyTransaction` (step 13c).
- No change to `dedupe_monthly_statements` or `compare_monthly_pdf`.
- Activity-CSV dedupe (already content-hash based in `auto_import`), the exceptions page (step 14), the watcher, `reconcile`, ledger, anything parked.
- No new data is imported; Pi never touches mercury.

## Steps (one commit each)

1. **Helpers** — add `core/utils/import_checks.py` with `content_hash_of` and `statement_date_problems`; unit tests for the three date rules and the hash (fake accounts only).
   Check: `/opt/pm-venv/bin/python -m pytest tests/ --collect-only -q` collects the new tests (no test run yet — tests run once, in step 8).
2. **Migration** — `content_hash` on `RbcMonthly` and `RbcAnnual`; run `makemigrations` once, never hand-edit.
   Check: `/opt/pm-venv/bin/python manage.py makemigrations --check --settings=portfolio_manager.settings_test` is clean.
3. **File-level content duplicate check** in `manage_inbox_import` (skip before classify; `content_hash` stored in `FileImportLog`; verdict in output + JSON).
   Check: `pytest --collect-only -q` still collects; `git status` shows only the expected files.
4. **Section-level duplicate/conflict** for monthly and annual (drop `file_name` from the query, compare `content_hash`, conflict → `errors/` + reason).
   Check: `rg -n "file_name=safe_name" core/management/commands/manage_inbox_import.py` finds no duplicate-query hit; `pytest --collect-only -q`.
5. **Date sanity** on the monthly (text + VL) and annual paths, using `statement_date_problems`; failure → `errors/` with reason.
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
