# Feature Spec: FX Data-Quality Audit and Repair

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/fx-audit

---

## Context

Specs 01–07 are complete. The `Transaction` model has two FX fields:
- `exchange_rate_cad`: the CAD/foreign-currency rate at import time (default 1.0)
- `amount_cad`: the transaction amount converted to CAD (default 0.00)

Both parsers (`parse_ib_csv`, `parse_rbc_csv`) populate these fields during import.
However, early imports or records with data issues may have `amount_cad = 0` while
`amount` is non-zero, or `exchange_rate_cad = 1.0` for USD transactions (which would
be wrong for dates when USD/CAD ≠ 1).

This spec adds a management command `audit_fx` in the `imports` app that audits all
transactions for FX inconsistencies and optionally repairs them using the stored
`exchange_rate_cad` value.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

1. **New management command `imports/management/commands/audit_fx.py`**
   - Class: `Command(BaseCommand)` with `help = 'Audit and optionally repair amount_cad on Transaction records.'`
   - Arguments:
     - `--fix` flag (store_true): if present, apply repairs; otherwise report only (dry run).
     - `--account` (optional str): filter to a single account_id.
   - Logic:
     - Query all `Transaction` objects (filtered by account if `--account` given).
     - A transaction is **suspicious** if ALL of the following are true:
       - `amount != 0` (it has a real amount)
       - `amount_cad == 0` (but the CAD conversion is missing)
     - A transaction is **repairable** if it is suspicious AND `exchange_rate_cad != 0`.
     - The repair formula: `amount_cad = round(amount * exchange_rate_cad, 2)`
     - In `--fix` mode: update and save each repairable transaction.
     - Output (stdout) a summary table:
       ```
       Total transactions scanned: N
       Suspicious (amount_cad = 0, amount ≠ 0): N
       Repairable (have valid exchange_rate_cad): N
       Repaired: N  (only shown in --fix mode)
       ```
     - For each suspicious transaction, print one line:
       `  [SUSPICIOUS] id=<id> account=<account_id> date=<date> symbol=<symbol> amount=<amount> <currency> rate=<exchange_rate_cad> → would set amount_cad=<computed>`
     - In `--fix` mode replace "would set" with "set".
   - Use `self.stdout.write()` for all output, not `print()`.

2. **Tests in `imports/tests/test_audit_fx.py`**
   - `TestCase` class `AuditFxCommandTests`.
   - `setUp`: create an Account, BrokerStatement, and:
     - `tx_ok`: amount=-1500, currency=USD, exchange_rate_cad=1.35, amount_cad=-2025.00 (correct)
     - `tx_bad`: amount=-1000, currency=USD, exchange_rate_cad=1.30, amount_cad=0.00 (broken)
     - `tx_zero`: amount=0, currency=USD, exchange_rate_cad=1.0, amount_cad=0.00 (legitimate zero)
   - `test_audit_dry_run`: call command with `--fix` NOT set. Assert `tx_bad.amount_cad` is still 0 after call. Assert stdout contains `Suspicious` count of 1.
   - `test_audit_fix`: call command with `--fix`. Assert `tx_bad` is refreshed from DB and `amount_cad == Decimal('-1300.00')`. Assert stdout contains `Repaired: 1`.
   - `test_audit_zero_amount_not_flagged`: assert `tx_zero` is not listed in output (amount=0 is not suspicious).
   - `test_audit_account_filter`: add a second account with a broken transaction. Run with `--account=<first account_id>`. Assert only 1 suspicious found.
   - All Transaction creates must include `commission=Decimal('0')` and unique `tx_hash`.
   - Use `call_command('audit_fx', ...)` and `StringIO` to capture stdout.

---

## Constraints

- Do NOT change `core/models.py`.
- Do NOT create any migrations.
- The command must be idempotent: running `--fix` twice produces the same result (already-correct records are not touched).
- Do not use `print()` — use `self.stdout.write()`.
- Type hints required on all new functions.
- Run `python manage.py test imports --settings=portfolio_manager.settings_test`. All tests must pass.
- After all tests pass: `git add -A && git commit -m "feat: FX audit/repair management command"`

---

## Manual Steps Required

- [ ] After review: run against production DB to identify real issues: `python manage.py audit_fx`
- [ ] If issues found: apply repair: `python manage.py audit_fx --fix`
- [ ] Review and push feature branch: `git push origin feature/fx-audit`
- [ ] Merge to dev after review.
