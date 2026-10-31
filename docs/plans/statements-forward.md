# Plan: statements forward — one forward generator (phase 4, step 14d)

Clean-up order (`docs/architecture.md` §8): "Monthly statements: one method, forward from
transactions, checked against the PDF. The reverse and batch generators go."

## Goal

Replace all `CoreMonthlyStatement` generation (forward `generate_monthly_statements`,
`reverse_generate_monthly_statements`, `batch_generate_all_statements` + placeholder helper,
their commands and views) with ONE forward generator:

- One row per (account, currency, statement period). Periods and transaction placement are
  exactly `monthly_check`'s: reuse `core/utils/monthly_check.py` (`group_statements`,
  `previous_groups`, `period_transactions` — settlement date, PAY date in notes, option-code
  and symbol-alias normalisation, `_pdf_qty_map`, `_cash_closing`, `check_statement`). Do not
  copy that logic.
- Anchoring: each statement period starts from the previous stored PDF statement — holdings
  (`RbcMonthlyHolding` of the previous group) and Cash closing balance
  (`RbcMonthlyBalance`) — then adds the period's transactions, exactly like the check.
- FIRST and NO_CSV statements (per `check_statement` verdicts): take the PDF figures as they
  are (begin/end totals, Cash closing balance), marked `source=PDF`.
- Gap months (no PDF statement and no transactions, per the gap rule) get a row at the
  calendar month end, chained from the previous row, `source=GAP`.
- No rows after the later of the last PDF statement date and the last CSV effective date.
- Quantities come from the replay (prev PDF holdings + Σ signed tx qty, normalised keys).
- Prices: PDF holdings of the same statement where one exists, else `core_historicalprice`
  (latest close ≤ statement date), else the last PDF price with the row marked stale.
- Holdings are netted per account (across currencies — the DLR journal: +448 DLR CAD,
  −448 DLR USD nets to zero and drops out) before being placed in a currency section: a
  symbol goes to the section where the PDF holds it, else the currency of its transactions.
- Flows per row from the period transactions: deposits, withdrawals, transfers, income
  (DIV, DIST, INT), fees and tax.
- Built-in check: for every statement whose monthly check verdict is PASS, the generated
  `ending_market_value` must equal the PDF `end_total_market_value` to the cent; every
  mismatch is reported (account name, date, currency, difference) and `--apply` refuses.
- New command `generate_statements [--account NAME] [--from YYYY-MM] [--to YYYY-MM]
  [--apply] [--out FILE]`: dry run by default; per-account counts by source (replay/PDF/gap),
  PASS rows equal to the PDF, rows priced from history, stale rows. `--apply` replaces the
  rows of the selected accounts in one transaction.

Output anywhere: account names, dates, currencies, symbols and amounts only — never digits.

## Readers of CoreMonthlyStatement (keep the fields they use working)

Found with `git grep -l CoreMonthlyStatement`:

- `core/views.py` `generate_monthly_statements_view` (display path) +
  `core/templates/core/generate_statements.html`: statement_month, currency,
  beginning/ending_market_value, total_buys/sells/dividends/interest/commission,
  total_transactions.
- `core/admin.py` `CoreMonthlyStatementAdmin`.
- `core/utils/reconciliation.py` (parked `reconcile`): beginning/ending_market_value,
  total_buys, total_sells, total_dividends, total_interest, total_transactions.
- `generate_annual_reports` (stays in `core/utils/statements.py`): all totals, net_flow,
  statement_month (year extraction).
- Parked scripts: `recon_report.py`, `scripts/reconcile_data.py`,
  `scripts/data_quality_audit.py` (ending/beginning_market_value).
- `core/tests/test_reconciliation.py` creates rows directly — unaffected.

All existing columns stay and keep being filled (`total_dividends` = DIV+DIST,
`total_interest` = INT, `total_buys`/`total_sells` = BUY/SELL magnitudes, `net_flow` =
Σ signed amounts, `total_transactions` = row count).

## What changes

- `core/utils/monthly_check.py` — small refactor only: extract the symbol normaliser that
  `check_holdings` builds inline (option-code map + `normalize_option_text` +
  `SYMBOL_ALIASES`) into a module-level `make_symbol_key(account_pk)` used by both files.
  No behaviour change.
- `core/models.py` (+1 migration):
  - `CoreMonthlyStatement`: add `source` (REPLAY / PDF / GAP), `total_deposits`,
    `total_withdrawals`, `total_transfers`, `total_income`, `total_fees`, `total_tax`
    (Decimal, default 0), `has_stale_prices` (bool, default False). `statement_month`
    keeps its name but now holds the **statement date** (PDF statement date, or calendar
    month end for gap/post-PDF rows) — help_text update only, no schema change.
  - New model `CoreMonthlyStatementPosition` (FK statement, symbol, quantity, price,
    price_source PDF/HISTORY/LAST_PDF, value): the per-row holdings section. A detail
    model instead of JSONField (SQL Server safety, queryable).
- `core/utils/statements.py` — delete `generate_monthly_statements`,
  `reverse_generate_monthly_statements`, `batch_generate_all_statements`,
  `_create_placeholder_statements`; add `generate_forward_statements(account, from_date,
  to_date)` (the one method) returning (rows, report). `generate_annual_reports` unchanged.
- `core/management/commands/generate_statements.py` — new command.
- Delete: `core/management/commands/generate_monthly_statements.py`,
  `core/management/commands/batch_generate_statements.py`, `reverse_statements_view` +
  its url + `core/templates/core/reverse_statements.html`; strip the regenerate/batch
  paths from `generate_monthly_statements_view` (page stays read-only display).
- Delete tests: `core/tests/test_batch_statements.py`, `core/tests/test_reverse_statements.py`
  (and any generation tests inside `core/tests/test_reconciliation.py`, if present).
- Docs: `AGENTS.md` ("Where things are" rows for reverse/batch statements, key commands),
  `docs/IMPORT_COMMANDS.md`.

## Decisions (recommended answers baked in)

- **Verdicts for the built-in check**: use stored `MonthlyCheckResult` verdicts when present
  (they reflect accepted exceptions from 14b); fall back to a live `check_statement(...)`
  call per group when nothing is stored.
- **Money basis**: per-currency rows use native `tx.amount` (what `check_cash` sums), not
  `amount_cad` — required for the PASS rows to equal the PDF to the cent.
- **After the last PDF statement**: generate one calendar-month row (month end, source
  REPLAY, prices from history) per month that has transactions, up to the last CSV date.
- **Gap rows** carry the previous row's positions at their last known prices, marked stale.
- **Net-zero symbols after per-account netting** (DLR journal) are dropped from positions.

## Out of scope

- Annual reports (`generate_annual_reports`) — unchanged; regenerating them from the new
  command is a later step.
- The exceptions page, `MonthlyCheckResult` storage, `write_ledger`, the parked
  reconciliation, anything IB-specific, any change to imported data.
- ACB / performance views (phase 6).

## Steps

1. **Commit this plan.** Check: `git status` clean after commit.
2. **monthly_check refactor**: extract `make_symbol_key(account_pk)`; `check_holdings` uses
   it. No behaviour change. Check: `/opt/pm-venv/bin/python -m pytest tests/test_monthly_check.py -q`.
3. **Models + migration**: new fields + `CoreMonthlyStatementPosition`.
   Check: `/opt/pm-venv/bin/python manage.py makemigrations --check --settings=portfolio_manager.settings_test` (clean), `pytest --collect-only -q tests/`.
4. **Generator**: `generate_forward_statements` in `core/utils/statements.py`, reusing
   monthly_check; builds rows + report (counts, PASS mismatches) without writing.
   Check: `/opt/pm-venv/bin/python -c "import core.utils.statements"` via `manage.py shell -c`, `pytest --collect-only -q tests/`.
5. **Command, dry run**: `generate_statements` with `--account/--from/--to/--out`, prints
   the report (names, dates, currencies, symbols, amounts only).
   Check: `manage.py generate_statements --help --settings=portfolio_manager.settings_test`.
6. **--apply + built-in check**: PASS-vs-PDF mismatch makes `--apply` exit non-zero without
   writing; otherwise delete-and-replace the selected accounts' rows (and positions) in one
   `transaction.atomic()`. Check: `manage.py generate_statements --help`, `git status`.
7. **Delete the old line**: functions, commands, reverse view/url/template, regenerate/batch
   paths in the view + template buttons, old test files; update `AGENTS.md` and
   `docs/IMPORT_COMMANDS.md`. Check: `git grep -l "batch_generate_all_statements\|reverse_generate_monthly_statements\|generate_monthly_statements"` → only
   history/docs-history hits; `manage.py check --settings=portfolio_manager.settings_test`.
8. **Tests** (synthetic data, `tests/test_statements_forward.py`, one file, written once at
   the end): a gap month; a new currency section; a DLR journal across two sections (nets
   to zero); a FAIL month that differs from the PDF by exactly its exception; a re-run that
   changes nothing (byte-identical rows). Then run, once:
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   and `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`.

## Stop conditions

- A PASS row's generated closing value cannot equal `end_total_market_value` to the cent
  because the PDF total includes/excludes cash differently than `check_cash` assumes — stop,
  show the numbers, ask tdw.
- `MonthlyCheckResult` verdicts are absent in production and the live fallback is too slow
  for a full run — stop and ask before caching anything.
- `test_reconciliation.py` turns out to depend on generation functions (not just direct row
  creation) — stop; the parked reconcile's tests must not be silently dropped.
- Any mismatch between this plan and reality during implementation: stop, do not edit the
  plan mid-branch (`docs/plans/README.md`).

## Open questions

1. **Verdict source for the built-in check** — stored `MonthlyCheckResult` or live
   `check_statement`? *Recommended: stored when present, live fallback per group.*
2. **Money basis** — `tx.amount` (native) or `tx.amount_cad` in per-currency rows?
   *Recommended: native `tx.amount`; it is what the check sums and what the PDF totals are.*
3. **Rows after the last PDF statement** — generate monthly replay rows for CSV-only
   months? *Recommended: yes, calendar-month rows for months with transactions, up to the
   last CSV date.*
4. **statement_month semantics** change from first-of-month to statement date. Rename the
   column? *Recommended: keep the name (readers and templates stay untouched), update
   help_text; `--apply` replaces old rows anyway.*
5. **Annual reports** — regenerate them inside `generate_statements --apply`?
   *Recommended: no, out of scope here; `generate_annual_reports` keeps working off the rows.*
6. **The generate-monthly-statements page** — keep or delete? *Recommended: keep as a
   read-only display (it is a reader of the fields), remove regenerate/batch buttons and
   delete the reverse-statements page.*
