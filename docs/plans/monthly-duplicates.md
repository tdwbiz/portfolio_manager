# Plan: dedupe_monthly_statements (phase 3, step 13a)

Branch: `fix/monthly-duplicates`. Parent doc: `docs/architecture.md` §12 step 13 ("PDF duplicate check, date sanity check, …"). This is **13a only**: a management command to report and remove the duplicate `RbcMonthly` rows found in step 11. The import-side duplicate check is **13b, not here**.

## Goal

Step 11 found **30 duplicate monthly statement pairs**: two `RbcMonthly` rows with the same account, `statement_date` and `currency` but different `file_name` (the same PDF imported twice under two names). 24 pairs have the same `end_total_market_value`, 6 differ.

Add `manage.py dedupe_monthly_statements`:

- **Default: report only.** One line per duplicate pair: account **name**, statement date, currency, both row ids, then `DELETE` or `KEEP`; a kept pair lists its reason(s) as field names only (e.g. `begin_differs`, `end_differs`, `tx_count_differs`, `bal_count_differs`, `holdings_differ`). The report ends with totals: pairs found, would delete, kept, not-a-pair. Never print values, file names or any account digits.
- **`--apply`:** for a pair, delete the duplicate row (and its cascade-linked rows) **only if** all of: `begin_total_market_value` equal, `end_total_market_value` equal, transaction count equal, balance count equal, and the linked holdings are identical (same multiset of holding rows ignoring `id` and `rbc_monthly` FK: `currency, security, symbol, quantity, book_cost, market_price, market_value`). The other row of the pair is kept untouched. Any pair that fails any check is never touched — it stays report-only for manual review. A second run finds nothing and changes nothing (idempotent).

Linked rows are removed by the existing `on_delete=models.CASCADE` FKs from `RbcMonthlyBalance` (`balances`), `RbcMonthlyTransaction` (`transactions`) and `RbcMonthlyHolding` (`holdings`) — no manual child deletes.

## What changes

| File | Change |
|---|---|
| `core/management/commands/dedupe_monthly_statements.py` | new command |
| `core/tests/test_dedupe_monthly_statements.py` | new tests (SQLite fixtures, fake accounts `90000001`+) |
| `docs/IMPORT_COMMANDS.md` | one row in the command table (if a table of commands exists there) |

No model changes, **no migrations**, no changes to importers, parsers, `unique_together`, or the step-13b duplicate check.

## Command design

- Group: `RbcMonthly` grouped by `(account, statement_date, currency)` with `count > 1`.
- **Pairs only (exactly 2 rows).** A group with 3+ rows is reported with a `NOT-A-PAIR` marker and never auto-deleted (expected: none; the audit found pairs).
- Row choice under `--apply`: keep the **lower id** (first imported), delete the higher id.
- Deletable test (all must hold): pair of exactly 2 rows, equal `begin_total_market_value`, equal `end_total_market_value`, equal transaction count, equal balance count, holdings sets equal as multisets of the field tuple above.
- Report line (no values, no file names, no digits of account numbers):
  `TOM_TFSA 2025-03-31 CAD ids 101|247 DELETE`
  or
  `TOM_TFSA 2025-03-31 CAD ids 101|247 KEEP begin_differs,tx_count_differs`
  (reasons are field names only; a deletable pair has no reasons.)
- The report ends with a totals line: `pairs found: N  would delete: N  kept: N  not-a-pair: N`.
- `--apply` runs each deletion inside `transaction.atomic()` and prints the same lines as the report plus one summary line with the number of rows deleted per table (`rbcmonthly`, `holdings`, `transactions`, `balances`).
- Nothing is ever deleted in a report run. Pi never runs this against mercury; tdw runs it on mercury **after a backup**.

## Out of scope

- Step 13b: the import-time duplicate check / `BrokerStatement` periods / date sanity — untouched.
- Any pair that fails the deletable test (differing begin/end value, tx/bal counts or holdings — including the 6 pairs found in step 11): reported `KEEP …`, not fixed.
- `FileImportLog` rows for the duplicate files; `processed/` files on disk; `CoreMonthlyStatement` or `Transaction` rows.
- Any change to `unique_together` on `RbcMonthly` (a real unique constraint would need a migration and belongs to 13b discussion).

## Steps (one commit each)

1. **Command skeleton + report.** Create `dedupe_monthly_statements.py`: `--apply` flag, grouping query, pair detection, deletable test (begin equal, end equal, tx count equal, bal count equal, holdings multiset equal), report line formatting (`DELETE` / `KEEP <reasons>`, ids only), totals line. No deletion yet.
   Check: `/opt/pm-venv/bin/python manage.py dedupe_monthly_statements --help` prints, and `/opt/pm-venv/bin/python manage.py check --settings=portfolio_manager.settings_test` is clean.
2. **`--apply` deletion.** Keep-lower-id, `atomic()` delete of the higher-id row (deletable test from step 1), same report lines plus a summary line with rows deleted per table (`rbcmonthly`, `holdings`, `transactions`, `balances`). Report-only stays the default.
   Check: `git status --short` shows only the command file changed; `rg -n "file_name|account_number" core/management/commands/dedupe_monthly_statements.py` returns only comments/none (nothing printed from those fields).
3. **Tests.** Add `core/tests/test_dedupe_monthly_statements.py` (fake accounts `90000001`, `call_command`):
   - report-only run lists the pair (`DELETE`) and deletes nothing, and prints the totals line;
   - identical pair (same begin/end values, same tx/bal counts, same holdings): `--apply` deletes the higher-id row and its balances/transactions/holdings (CASCADE), kept row and its children intact, and prints rows deleted per table;
   - second `--apply` run changes nothing (idempotence);
   - pair with different `begin_total_market_value` is reported `KEEP begin_differs` and never deleted;
   - pair with different `end_total_market_value` is reported `KEEP end_differs` and never deleted;
   - pair with equal values but different holdings is reported `KEEP holdings_differ` and never deleted;
   - pair with unequal transaction counts is reported `KEEP tx_count_differs`; pair with unequal balance counts is reported `KEEP bal_count_differs`; both never deleted;
   - a 3-row group is reported `NOT-A-PAIR` and untouched by `--apply`;
   - output contains no `file_name` value and no account digits (assert on captured stdout).
   Check: `/opt/pm-venv/bin/python -m pytest core/tests/test_dedupe_monthly_statements.py -q --collect-only` lists the tests.
4. **Docs + full test run.** Add the command row to `docs/IMPORT_COMMANDS.md`, then run the full tests (below), fix fallout, commit.

## Tests (once, at the end)

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
```

## Stop conditions

- Stop and ask if the grouping on mercury (tdw's report run) yields anything other than 30 pairs — e.g. groups of 3+, or a different count: the plan assumes pairs.
- Stop if a "deletable" pair would delete rows that other tables reference beyond the three cascade children (verify with `rg -n "RbcMonthly" core/models.py` before step 2 — only Balance/Transaction/Holding FKs exist today).
- Stop if any test needs real account numbers or a mercury connection — Pi has no route to mercury; fixtures only.
- Do not "fix" any pair that fails the deletable test (e.g. the 6 differing pairs from step 11); report them `KEEP …` and stop.

## Open questions

- **Which row of a deletable pair is kept?** Recommended: keep the lower id (first imported), delete the higher id — deterministic and idempotent.
- **Groups of 3+ rows with the same key?** Recommended: report with `NOT-A-PAIR`, never auto-delete; handle manually if the report run shows any.
- **Should `begin_total_market_value` also gate deletion?** Recommended: no — the task defines the rule (end value + holdings). A differing begin value shows up as a differing end value or holdings in practice; the report counts let tdw spot-check.
- **Should transactions/balances also be required identical?** Recommended: no (task says end value + holdings only); the per-row counts in the report make gross mismatches visible before tdw runs `--apply`.
- **Should `--apply` also delete the duplicate PDF/CSV files in `processed/`?** Recommended: no — file cleanup is not part of 13a; tdw can remove files by the ids in the report.

## Decisions (tdw, Oct 27)

- **Q1 — which row of a deletable pair is kept:** keep the lower id, as recommended.
- **Q2 — groups of 3+ rows with the same key:** as recommended — report with `NOT-A-PAIR`, never auto-delete.
- **Q3 — should `begin_total_market_value` also gate deletion?** Yes: `begin_total_market_value` must also be equal before a pair is deleted.
- **Q4 — should transactions/balances also be required identical?** Yes: the transaction count and the balance count must also be equal before a pair is deleted (holdings stay compared in full).
- **Q5 — should `--apply` delete files in `processed/`?** No, as recommended.
- **Report format:** each pair line ends with `DELETE` or `KEEP`, the reason(s) being field names only (for example `begin_differs`, `tx_count_differs`, `holdings_differ`). The report ends with totals: pairs found, would delete, kept, not-a-pair. `--apply` prints the same lines plus the number of rows deleted per table.
