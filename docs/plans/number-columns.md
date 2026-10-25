# Plan: Phase 2 step 10 — old account-number columns out, one-off free-text scrub

Branch: `refactor/number-columns`. Reference: `docs/architecture.md` §5 and §12 step 10.

## Goal

No account number is stored anywhere in the database after this step:

1. `Account.account_id` (the old number/label column) is removed; accounts are `name` + `fingerprint` + `last_4`.
2. `RbcMonthly.account_number`, `RbcAnnual.account_number`, `LedgerAccount.account_id_str` and the string account copies in the parked risk tables are verified/re-pointed by fingerprint in a data migration, then the columns are dropped in a separate migration.
3. A reusable scrub utility (and a `scrub_account_numbers` command) replaces real account numbers found in stored free text (notes, PDF descriptions, stored file names) with the account name. Dry run by default; never prints a value.

Transaction-hash change (task item 3) is **not coded** — see Open questions Q1 (`row_number` is a hash input but is not stored on the transaction row).

## What changes (files)

- `core/utils/account_fingerprint.py` — add `scrub_text()` (candidate regex + fingerprint lookup; reuses `normalize_account_number` / `fingerprint_account_number`).
- `imports/auto_import.py` — scrub `trade['notes']` before the hash/create.
- `core/management/commands/manage_inbox_import.py` — scrub `RbcMonthlyTransaction.description` before save; stop passing `account_number=` to `RbcMonthly`/`RbcAnnual` creates (step 6).
- `core/management/commands/scrub_account_numbers.py` — new command (dry run by default, `--apply`).
- `core/models.py` — drop `Account.account_id`, `RbcMonthly.account_number`, `RbcAnnual.account_number`, `LedgerAccount.account_id_str`; `compute_tx_hash` unchanged (Q1). `AccountCurrency.__str__` and `compute_realised_pnl` grouping key (`core/utils/portfolio.py`) switch to `account.name`.
- `core/forms.py` (fields + `clean_account_id`), `core/admin.py`, `core/management/commands/merge_duplicate_accounts.py` (match by name/last_4 instead of `account_id`), remaining `Account.account_id` references in `core/views*.py`, `core/urls.py` (ledger route param → name lookup), `core/management/commands/*` (`write_ledger`, `audit_rbc_data`, …).
- `risk/` code — every `Account.objects.*account_id*` / `acc.account_id` lookup switches to `name` (views, services, management commands; ~70 refs). The parked risk tables' string columns stay `CharField` (they store labels like `IB_SUSAN`, never real numbers) — see Q2.
- Migrations `core/migrations/0042_*`, `0043_*` (data), `0044_*` (drops).
- Tests: `core/tests/*` (accounts created with `name` + `fingerprint` + `last_4`, fake numbers `90000001`–`90000008`), `imports/tests/*`, new `core/tests/test_scrub.py`. There are no fixture directories; tests create accounts inline.

Scrub targets (command + one-off): `Transaction.notes`, `RbcMonthlyTransaction.description`, `FileImportLog.filename`, `FileImportLog.error_message`, `BrokerStatement.raw_filename`, `RbcMonthly.file_name`, `RbcAnnual.file_name`.

Out of scope: log filter, renaming files on disk, account number in `auto_import` log lines (step 11); "Amount Invested" template text; the IB import; `compute_tx_hash` (Q1).

## Migrations

- `core 0042` — `RemoveField Account.account_id` (schema; step 4).
- `core 0043` — **data only** (step 5). Contains its own private copies of `normalize_account_number` and the HMAC code (module-level constants/functions inside the migration file, keyed by `settings.ACCOUNT_FINGERPRINT_KEY` at run time), so later changes to the util cannot change the migration. For `RbcMonthly` and `RbcAnnual`: for each distinct non-empty stored number, fingerprint it and find the Account; if the FK points elsewhere, re-point it; a value matching no account aborts with **counts only** (`table, row-count` pairs — never the values). Blank values are skipped. `LedgerAccount` (0 rows): same check, trivially passes. Parked risk tables: delete all rows of the tables listed in Q2 (test/computed data — see Q2). Reverse is a no-op (documented in the migration).
- `core 0044` — `RemoveField RbcMonthly.account_number`, `RbcAnnual.account_number`, `LedgerAccount.account_id_str` (step 6). Not reversible; rollback is tdw's PortfolioDEV backup.

No add-column migration is needed: `RbcMonthly`, `RbcAnnual` and `LedgerAccount` already have non-null `account` FKs, so the links are verified/re-pointed, not added. 0043 and 0044 are separate commits so tdw can check in SSMS between them.

## Steps (one commit each)

1. **Scrub utility.** Add `scrub_text(text) -> (cleaned, matched, replaced, unmatched)` to `core/utils/account_fingerprint.py`. Candidates: runs of 8+ digits with optional dashes/spaces, and IB ids (`U`/`F` + digits). Each candidate is normalised, fingerprinted and looked up; **only a real fingerprint match is replaced** (with the account name), so dates (`2026-10-21`), amounts and other look-alikes stay. Never returns or prints the original value. Unit tests in `core/tests/test_account_fingerprint.py` (fake accounts `90000001`–`90000008`: `900-00003-1-8`, `9000000318`, `U12345678`; a date and a large amount must be untouched).
   Check: `/opt/pm-venv/bin/python manage.py test core.tests.test_account_fingerprint --settings=portfolio_manager.settings_test`
2. **Scrub at import.** Scrub transaction notes in `imports/auto_import.py` (before `compute_tx_hash` — notes are not a hash input, but scrub-before-store is the rule) and `RbcMonthlyTransaction.description` in the `manage_inbox_import` save path.
   Check: `/opt/pm-venv/bin/python -m pytest imports/tests -q`
3. **`scrub_account_numbers` command.** Dry run by default; prints only `table, column, matched, replaced, unmatched-look-alikes`. `--apply` writes. No hash recompute (Q1: scrub cannot change hashes — notes/descriptions are not hash inputs). Test with fake accounts.
   Check: `/opt/pm-venv/bin/python manage.py test core.tests.test_scrub --settings=portfolio_manager.settings_test`
4. **`Account.account_id` out.** Remove the field; update `AccountForm`, admin, `merge_duplicate_accounts` (match by name/last_4), `AccountCurrency.__str__`, `compute_realised_pnl` key → `account.name`, remaining core views/urls/commands, all risk lookups → `name`, and every test that creates an `Account` (name + fingerprint + last_4). Add migration 0042.
   Check: `rg -l "Account.objects\w*\([^)]*account_id|\.\s*account_id\b" core risk -g '*.py' -g '!core/migrations/*'` returns nothing meaningful (FK attname uses like `tx.account_id` on other models are fine); `/opt/pm-venv/bin/python manage.py check --settings=portfolio_manager.settings_test`; `makemigrations --check` clean.
5. **Data migration 0043** (fill/verify + risk row wipe, self-contained fingerprint code, counts-only failure).
   Check: `manage.py migrate --plan --settings=portfolio_manager.settings_test` and a fresh SQLite `migrate` run (empty DB → no-ops pass).
6. **Drop columns.** Remove the three model fields + migration 0044; stop writing `account_number` in `manage_inbox_import` and `account_id_str` in `write_ledger` (parsers may keep the dict key — it is used for lookup, not storage).
   Check: `rg -n "account_number=|account_id_str" core imports -g '*.py' -g '!*/migrations/*'` shows no model writes; `manage.py check`.
7. **Full tests** (only here): `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test` and `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`; then `makemigrations --check --settings=portfolio_manager.settings_test` and `git status` clean.

## tdw's runbook on mercury (after merge, in order)

1. Fresh backup of `PortfolioDEV`.
2. SSMS checks, counts only: row counts of `core_transaction`, `core_rbcmonthly`/`core_rbcannual` rows with non-empty `account_number`; and `SELECT COUNT(*) FROM core_account WHERE name <> account_id` (must be 0 — see Q3).
3. `manage.py sqlmigrate core 0042`, `0043`, `0044` — read the SQL Server SQL.
4. `manage.py migrate --plan`.
5. `migrate core 0042` → SSMS check (column gone, account count unchanged) → `migrate core 0043` → SSMS check (counts per table; risk tables empty) → `migrate core 0044` → SSMS check (three columns gone).
6. `scrub_account_numbers` dry run → review counts → `--apply` → final SSMS check (row counts unchanged; spot `LIKE '%[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]%'` counts on the scrubbed columns).

## Stop conditions

- `ACCOUNT_FINGERPRINT_KEY` missing/short when a migration or the command runs → stop (do not work around it).
- Data migration 0043 aborts with unmatched counts → stop, report table + counts only; tdw decides.
- Q3 check fails (production `name` ≠ old `account_id` for any account) → stop before merging step 4; risk lookups need a mapping instead.
- Any step needs a package that is not installed, or touches `.env` / production DB → stop.
- If a step turns out wrong during implementation: stop, do not edit the plan; revise via tdw.

## Open questions

- **Q1 — hash inputs are not all stored (task item 3 not coded).** `compute_tx_hash` takes `row_number` (supplied by the CSV parser for every row) and `broker`; `broker` is recoverable via `statement.source`, but **`row_number` is not stored on `Transaction`**, so stored hashes cannot be reproduced from stored values. Per the task rule ("if not, say so and don't code it") the pk-based hash and the recompute are **not** implemented. Recommended: leave `compute_tx_hash` as-is (the number is a transient import input, never stored, so nothing leaks); revisit only if a future re-import adds a `source_row` column. Also verified: `tx_hash` is a plain index, not unique (no unique-index care); no other table stores transaction hashes (`FileImportLog.content_hash` is a file-content hash); scrubbing cannot change any hash because notes/descriptions are not hash inputs — hence no recompute in the scrub command.
- **Q2 — parked risk tables: emptied, not linked (recommended).** The string account copies live in `PortfolioRiskMetrics.entity_id`, `UrgencyScore.entity_id/account_id`, `StrategyRecommendation.account_id`, `BacktestRun.account_id`, `SimulationRun.entity_id`, `CustomStressScenario.account_id`, `AlertConfig.account_id`, `AlertHistory.entity_id`, `AuditScoreSnapshot.account_id`, `AuditRecommendationLog.account_id`. Recommendation: **empty their rows** in migration 0043 (all test/computed data; `AlertConfig` is re-seeded with `seed_alert_configs`), and leave the columns as `CharField` — they store old labels like `IB_SUSAN`, never real numbers, and the `entity_id` columns are polymorphic (owner/portfolio) so they cannot be FKs. FK conversion belongs to the risk redesign (architecture §11).
- **Q3 — risk code switches from `account_id` to `name`.** This is only safe if production `Account.name` equals the old `account_id` string for every account. Recommended: the SSMS pre-check in the runbook (`name <> account_id` count must be 0); if not 0, stop and map first.
- **Q4 — `LedgerAccount.account_id_str` with 0 rows.** Recommended: still run the (trivially passing) check in 0043 for symmetry, then drop the column in 0044; no special handling.
- **Q5 — `FileImportLog.error_message`.** Recommended: include it in the scrub targets (free text that has echoed file names); it is cheap and step 11 reuses the same utility.
