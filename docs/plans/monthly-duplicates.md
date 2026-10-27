# Plan: dedupe_monthly_statements (phase 3, step 13a)

Branch: `fix/monthly-duplicates`. Parent doc: `docs/architecture.md` §12 step 13 ("PDF duplicate check, date sanity check, …"). This is **13a only**: a management command to report and remove the duplicate `RbcMonthly` rows found in step 11. The import-side duplicate check is **13b, not here**.

## Goal

Step 11 found **30 duplicate monthly statement pairs**: two `RbcMonthly` rows with the same account, `statement_date` and `currency` but different `file_name` (the same PDF imported twice under two names). 24 pairs have the same `end_total_market_value`, 6 differ.

Add `manage.py dedupe_monthly_statements`:

- **Default: report only.** One line per duplicate pair: account **name**, statement date, currency, both row ids, `end_equal=yes/no`, and the number of linked holdings / transactions / balances **per row** (e.g. `holdings 12|12`). Never print values, file names or any account digits.
- **`--apply`:** for a pair, delete the duplicate row (and its cascade-linked rows) **only if** `end_total_market_value` is equal **and** the linked holdings are identical (same set of holding rows ignoring `id` and `rbc_monthly` FK: `currency, security, symbol, quantity, book_cost, market_price, market_value`). The other row of the pair is kept untouched. Any pair that differs (end value or holdings) is never touched — it stays report-only for manual review. A second run finds nothing and changes nothing (idempotent).

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
- Deletable test (all must hold): pair of exactly 2 rows, equal `end_total_market_value`, holdings sets equal as multisets of the field tuple above.
- Report line (no values, no file names, no digits of account numbers):
  `TOM_TFSA 2025-03-31 CAD ids 101|247 end_equal=yes holdings 12|12 tx 5|5 bal 4|4 DELETABLE`
  (`end_equal=no` / `NOT-DELETABLE` for the 6 differing pairs; counts make unequal children visible to tdw.)
- `--apply` runs each deletion inside `transaction.atomic()`; prints one `DELETED …` line per removal and a summary (`pairs found / deletable / deleted`).
- Nothing is ever deleted in a report run. Pi never runs this against mercury; tdw runs it on mercury **after a backup**.

## Out of scope

- Step 13b: the import-time duplicate check / `BrokerStatement` periods / date sanity — untouched.
- The 6 pairs with different `end_total_market_value`, and any pair with differing holdings: reported, not fixed.
- `FileImportLog` rows for the duplicate files; `processed/` files on disk; `CoreMonthlyStatement` or `Transaction` rows.
- Any change to `unique_together` on `RbcMonthly` (a real unique constraint would need a migration and belongs to 13b discussion).

## Steps (one commit each)

1. **Command skeleton + report.** Create `dedupe_monthly_statements.py`: `--apply` flag, grouping query, pair detection, report line formatting (ids, yes/no, counts via `related_name` `.count()`), summary line. No deletion yet.
   Check: `/opt/pm-venv/bin/python manage.py dedupe_monthly_statements --help` prints, and `/opt/pm-venv/bin/python manage.py check --settings=portfolio_manager.settings_test` is clean.
2. **`--apply` deletion.** Implement the deletable test (end value equal + holdings multiset equal), keep-lower-id, `atomic()` delete of the higher-id row, `DELETED` lines + summary. Report-only stays the default.
   Check: `git status --short` shows only the command file changed; `rg -n "file_name|account_number" core/management/commands/dedupe_monthly_statements.py` returns only comments/none (nothing printed from those fields).
3. **Tests.** Add `core/tests/test_dedupe_monthly_statements.py` (fake accounts `90000001`, `call_command`):
   - report-only run lists the pair and deletes nothing;
   - identical pair (same end value, same holdings): `--apply` deletes the higher-id row and its balances/transactions/holdings (CASCADE), kept row and its children intact;
   - second `--apply` run changes nothing (idempotence);
   - pair with different `end_total_market_value` is reported `end_equal=no` and never deleted;
   - pair with equal end value but different holdings is never deleted;
   - unequal child counts appear in the report line;
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
- Do not "fix" the 6 differing pairs; report them and stop.

## Open questions

- **Which row of a deletable pair is kept?** Recommended: keep the lower id (first imported), delete the higher id — deterministic and idempotent.
- **Groups of 3+ rows with the same key?** Recommended: report with `NOT-A-PAIR`, never auto-delete; handle manually if the report run shows any.
- **Should `begin_total_market_value` also gate deletion?** Recommended: no — the task defines the rule (end value + holdings). A differing begin value shows up as a differing end value or holdings in practice; the report counts let tdw spot-check.
- **Should transactions/balances also be required identical?** Recommended: no (task says end value + holdings only); the per-row counts in the report make gross mismatches visible before tdw runs `--apply`.
- **Should `--apply` also delete the duplicate PDF/CSV files in `processed/`?** Recommended: no — file cleanup is not part of 13a; tdw can remove files by the ids in the report.
