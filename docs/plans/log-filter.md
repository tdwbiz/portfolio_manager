# Plan: log filter and safe file names (phase 2 step 11; audit 4 batches C + D)

## Goal

Nothing that leaves the app in text form — log lines, JSON import logs, moved/processed file names, stored `file_name` rows, error messages — contains a real account number or its last 4 digits.

Three pieces:

1. A `logging.Filter` built on `scrub_text` (fingerprint match → account **name**, exactly-15-digit runs → `BANK_ACCOUNT`, dates/amounts untouched because only exact fingerprint matches are replaced), attached to the app's log handlers — including the `imports.duplicates` file logger — and the JSON import logs scrubbed at write time (they are `json.dump` writes, not `logging` records, so "attached to the JSON import log" is implemented as payload scrubbing in step 5).
2. Fix the remaining call sites that put the parsed number into a log line, a result record or an error message: `imports/auto_import.py` (the `imports.duplicates` line and the two `'account': normalized_id` result records — currently lines 200, 207, 246; the task's 189/196/235 predate the scrub step) and `core/management/commands/manage_inbox_import.py` (the `Cannot resolve account for account_number=…` error — currently line 424, the task's 415).
3. Safe file names: files moved to `processed/` / `processed/errors/` at import get `<date>_<type>_<ACCOUNT_NAME>_<hash8>.<ext>` (audit 4 batch D, Q4 decision: the **name**, not even last-4), and a one-off management command rewrites the stored `RbcMonthly.file_name` / `RbcAnnual.file_name` values (which today mostly carry a last-4 like `-9999`) to the same pattern. The command has a `--dry-run` that prints **counts only**; tdw runs it on mercury after a backup.

`scrub_text(text, name_map)` in `core/utils/account_fingerprint.py` already does the replacement and requires the `name_map` (`build_fingerprint_name_map()`); this plan only consumes it.

## What changes

- `core/utils/safe_names.py` (new) — `safe_file_name(original_name, account_name, kind, date=None)` → `YYYY-MM-DD_monthly_TOM_TFSA_a1b2c3d4.pdf` / `2026_annual_TOM_TFSA_a1b2c3d4.pdf` (`hash8` = first 8 hex chars of `sha256(original_name)`, so the mapping is deterministic and re-imports still dedupe); `is_safe_file_name(name)` for idempotency.
- `core/utils/log_filter.py` (new) — `ScrubFilter(logging.Filter)`: caches `build_fingerprint_name_map()` (TTL `LOG_SCRUB_TTL_SECONDS` env, default 300), rewrites `record.msg = scrub_text(record.getMessage(), name_map)[0]` and clears `record.args` when anything changed. **Fail-open**: any exception (no/short `ACCOUNT_FINGERPRINT_KEY`, no DB, table missing) leaves the record untouched and never raises inside `logging`.
- `portfolio_manager/settings.py` — `LOGGING`: add `filters: {"scrub": {"()": "core.utils.log_filter.ScrubFilter"}}` and `"filters": ["scrub"]` on the `console` and `file` handlers (covers every app logger via the root handlers; `settings_test.py` inherits `LOGGING`).
- `imports/auto_import.py` — attach `ScrubFilter` to the `imports.duplicates` `FileHandler` in `_setup_duplicate_logger()`; replace `normalized_id` with the account **name** (fallback `UNKNOWN_ACCOUNT`) in the dup-logger line (~line 200) and in the two result records `'account': normalized_id` (~lines 207, 246).
- `core/management/commands/manage_inbox_import.py` — error message without the number (`Cannot resolve account for the parsed account number (UNKNOWN_ACCOUNT)`); the three stdout progress/dry-run lines that print `account_number` (lines ~278, ~406, ~514) use `account.name` (see Open question 3); safe file names everywhere: PDF moved to `processed/`/`errors/` renamed via `safe_file_name`, JSON log file name = safe stem (`+_ERROR` when errors), JSON payload = `json.dumps(...)` run through `scrub_text` with the already-built `name_map` (kills `account_number` in the VL payload), `'filename'` field and `RbcMonthly`/`RbcAnnual.file_name` and `_log_import` filename all store the safe name, stdout messages print the safe name.
- `core/management/commands/scrub_statement_file_names.py` (new) — one-off rewrite of `RbcMonthly.file_name` and `RbcAnnual.file_name` to the safe pattern using each row's `account.name`, `statement_date` / `report_year` and `hash8` of the stored name; skips rows already matching `is_safe_file_name`; `--dry-run` prints counts only (per model: to-rename / already safe), never names.
- Tests: `core/tests/test_safe_names.py`, `core/tests/test_log_filter.py`, `core/tests/test_scrub_statement_file_names.py` (new), updates in `imports/tests/` for the call-site and rename changes.

**No migrations** — no model changes (see Open question 1).

## Out of scope

- Git history (audit 4 batch A — closed by Q1), UI/URLs/admin/exports keep full numbers (Q3/Q6).
- `BrokerStatement.raw_filename` and the CSV (activity/holdings) names in `processed/` — see Open question 4.
- Renaming the physical files already sitting in `processed/` / `processed/errors/` — the command touches DB rows only (Open question 2).
- The other audit §2.3 call sites (`imports/parsers.py` debug logs, `core/views_transactions.py`, `classify_pdf_vl`/`watcher` path logs, parked scripts): the filter scrubs them at runtime; no call-site edits.
- `mask_account()` (audit batch B) — superseded by the fingerprint design; do not add it.
- The optional Q2 fixture swap (fake numbers in `test_parsers_pdf.py` / `test_vl_parsers.py`) — already `9000000x`-style fakes.

## Steps

Each step = one commit on `feature/log-filter`.

1. **`core/utils/safe_names.py`** + `core/tests/test_safe_names.py`: pattern, deterministic `hash8`, extension kept, `is_safe_file_name` round-trip (a safe name stays stable).
   Check: `/opt/pm-venv/bin/python manage.py test core.tests.test_safe_names --settings=portfolio_manager.settings_test`
2. **`core/utils/log_filter.py`** + `core/tests/test_log_filter.py`: fake accounts `90000001`/`90000002` (names `ACCOUNT_A`/`ACCOUNT_B`, test key in `settings_test.py`); a record containing `90000001` and a dash variant becomes `ACCOUNT_A`; a 15-digit run becomes `BANK_ACCOUNT`; a date (`2026-10-21`) and amounts (`1234.56`, `12345678.90`) pass through; unknown 8-digit run untouched; fail-open with a blank key and with no DB rows. Not attached yet.
   Check: `/opt/pm-venv/bin/python manage.py test core.tests.test_log_filter --settings=portfolio_manager.settings_test`
3. **Attach the filter**: `settings.LOGGING` filters + `filters: ["scrub"]` on `console` and `file`; `dup_handler.addFilter(ScrubFilter())` in `auto_import._setup_duplicate_logger()`.
   Check: `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/ --collect-only -q` (settings import stays clean) and `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
4. **Call-site fixes**: `auto_import.py` dup line + two `'account'` records → account name (`UNKNOWN_ACCOUNT` when no account); `manage_inbox_import.py` error message without the parsed number; the three stdout lines that print `account_number` use `account.name`.
   Check: `rg -n "normalized_id" imports/auto_import.py` (hits only in hash/lookup code paths), `rg -n "account_number" core/management/commands/manage_inbox_import.py` (no stdout/error f-string hits), then `/opt/pm-venv/bin/python -m pytest imports/tests/ -q`.
5. **Safe names at import** (`manage_inbox_import.py`): compute the safe name once per PDF after the account is resolved; use it for the `processed/`/`errors/` move, the JSON log stem, the `'filename'` field, `RbcMonthly`/`RbcAnnual.file_name`, `_log_import`, and the stdout lines; scrub the JSON payload with `scrub_text(..., name_map)` before writing. Add/extend a test that imports a fake PDF and asserts no digit-run of the fake number appears in any produced file name or JSON file.
   Check: `/opt/pm-venv/bin/python -m pytest imports/tests/ -q`
6. **`scrub_statement_file_names` command** + tests: rows with number-bearing names are rewritten to the safe pattern; `--dry-run` prints counts only (assert no names/numbers in output); second run is a no-op (idempotent).
   Check: `/opt/pm-venv/bin/python manage.py test core.tests.test_scrub_statement_file_names --settings=portfolio_manager.settings_test`
7. **Full test pass** (only here, at the end):
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`
   plus `/opt/pm-venv/bin/python manage.py makemigrations --check --settings=portfolio_manager.settings_test` (expect: no changes). Commit; summarise.

## Stop conditions

- Stop if the monthly/annual **duplicate check** (keyed on `account + statement_date + currency + file_name`) stops recognising a re-import after step 5 — the safe name must stay a deterministic function of the original file name; if it can't, ask before changing the dedupe key.
- Stop if anything else parses `RbcMonthly.file_name` / `RbcAnnual.file_name` back into an account or date (`rg -n "file_name" imports core` first; `merge_duplicate_accounts.py:134` copies it — verify it just copies).
- Stop if the filter raises inside `logging` (recursion, `raiseExceptions` noise) or if scrubbing every record measurably slows imports — then narrow to per-logger filters and revise the plan.
- Stop if a step seems to need a **model change** (e.g. an `original_filename` column) — that is a plan revision, not an implementation detail.
- Never print or paste real numbers, `.env`, logs or `docs/reconciliation/` content while testing.

## Open questions

1. **`original_filename` column?** Audit 4 batch D suggested keeping the original name in a new DB column. **Recommended: no** — the original name is exactly the thing containing the number, the account is already identified by FK + fingerprint, and a mercury migration for it costs more than the traceability is worth. The `hash8` still lets tdw match a safe name to an original name offline.
2. **Should the command also rename the physical files in `processed/` / `processed/errors/`?** **Recommended: no** — those dirs are gitignored and local; the requirement targets logs, JSON logs, stored names and messages. tdw can rename on disk later if wanted.
3. **The three stdout progress lines in `manage_inbox_import.py` (~278, ~406, ~514) print the parsed number** but are stdout, not log records. **Recommended: fix them in step 4** — `watch_inbox` runs inside `runserver`, so its stdout can end up captured, and `account.name` is already resolved at those points.
4. **CSV names and `BrokerStatement.raw_filename`**: holdings CSV names embed the account number and `raw_filename` (admin `list_display`) keeps the original CSV name. **Recommended: leave for a follow-up branch** — Q3 keeps full numbers in admin anyway, activity CSV names don't carry the number, and moving the CSV rename into `import_csv`/`auto_import` (which don't know the account per file at move time) would blow up this branch.
5. **Filter cache knob**: `LOG_SCRUB_TTL_SECONDS` (env, default 300) for the `name_map` cache. **Recommended: yes** — env-driven per the config rule; account names change rarely, and the filter fails open when the key/DB is unavailable.
