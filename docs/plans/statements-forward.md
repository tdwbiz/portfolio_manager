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
  calendar month end, chained from the previous row, `source=GAP`. The same rule applies
  after the last PDF statement: every calendar month up to the last CSV effective date gets
  a row — GAP when it has no transactions, REPLAY when it does (decision 3) — and no rows
  are generated once both holdings and cash are zero.
- No rows after the later of the last PDF statement date and the last CSV effective date.
- Quantities come from the replay (prev PDF holdings + Σ signed tx qty, normalised keys).
- Prices: PDF holdings of the same statement where one exists, else `core_historicalprice`
  (latest close ≤ statement date), else the last PDF price with the row marked stale.
- Holdings are netted per account (across currencies — the DLR journal: +448 DLR CAD,
  −448 DLR USD nets to zero and drops out) before being placed in a currency section: a
  symbol goes to the section where the PDF holds it, else the currency of its transactions.
- Flows per row from the period transactions: deposits, withdrawals, transfers, income
  (DIV, DIST, INT), fees and tax.
- Built-in check: for every statement currency whose live `check_statement` verdict is
  PASS, the generated `ending_market_value` must equal the PDF `end_total_market_value` to
  the cent; every mismatch is reported (account name, date, currency, difference) and
  `--apply` refuses.
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
    (Decimal, default 0), `has_stale_prices` (bool, default False) and `statement_date`
    (DateField — the RBC statement date, or calendar month end for gap and replay rows).
    `statement_month` keeps its current meaning (first of month); readers stay untouched
    (decision 4).
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

## Decisions (final — tdw, Oct 30; recorded at the end of this plan)

- **Verdicts for the built-in check**: always run `check_statement(...)` live per group;
  never use stored `MonthlyCheckResult` verdicts (they can be stale after transaction
  changes).
- **Money basis**: per-currency rows use native `tx.amount` (what `check_cash` sums), not
  `amount_cad` — required for the PASS rows to equal the PDF to the cent.
- **After the last PDF statement**: one calendar-month row per month up to the last CSV
  effective date — including months without transactions, same rule as gap months (GAP
  when no transactions, REPLAY when there are some) — until both holdings and cash are
  zero; no rows once both are zero.
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
- The live `check_statement` runs are too slow for a full-run built-in check — stop and
  ask before caching anything.
- `test_reconciliation.py` turns out to depend on generation functions (not just direct row
  creation) — stop; the parked reconcile's tests must not be silently dropped.
- Any mismatch between this plan and reality during implementation: stop, do not edit the
  plan mid-branch (`docs/plans/README.md`).

## Decisions (tdw, Oct 30)

1. **Verdict source for the built-in check** — always run `check_statement` live; no
   stored results (they can be stale after transaction changes).
2. **Money basis** — accept: native `tx.amount`.
3. **Rows after the last PDF statement** — yes, but a row for every calendar month up to
   the last CSV date, not only months with transactions (same rule as gap months); no rows
   once holdings and cash are both zero.
4. **statement_month semantics** — keep `statement_month` as first of month with its
   current meaning; add a new column `statement_date` (RBC statement date, or month end
   for gap and replay rows); readers stay untouched.
5. **Annual reports** — accept: no, out of scope here; `generate_annual_reports` keeps
   working off the rows.
6. **The generate-monthly-statements page** — accept: keep as a read-only display; remove
   the regenerate/batch buttons and delete the reverse-statements page.

## Decisions (tdw, step 7 stop)

7. **`AnnualReportGenerationTests.test_generate_annual_from_monthly`** — adapt it: build its
   `CoreMonthlyStatement` rows directly in its setup instead of calling
   `generate_monthly_statements`; keep the test and its assertions.
8. **The four calls at the step 7 stop are accepted as they are**: per-currency money is
   native `tx.amount`; WITHTAX counts in `total_tax`; a new symbol with no PDF holding and
   no historical close falls back to the last trade price with the row marked stale
   (`price_source=LAST_PDF`, `has_stale_prices`); `--apply` with `--from/--to` only replaces
   rows inside the window. `statement_date` stays nullable.

## Decisions (Claude review fixes, Nov 1)

9. **Re-anchor on the PDF after every stored statement** — in the REPLAY branch,
   after the rows are emitted and checked, the chain state resets to this
   statement's PDF: closing cash per currency (`RbcMonthlyBalance`) and the
   holdings pool (`RbcMonthlyHolding`), exactly as FIRST / NO_CSV does. Each
   period starts from the previous stored PDF statement, so a FAIL month's
   replayed drift never carries into the next period (the docstring already
   promised this).
10. **Gap fill starts in the calendar month after the anchor month** — a
    statement dated mid-month (e.g. Friday 2026-01-30) never gets a GAP row
    for the rest of its own month; the first gap-fill candidate is the end of
    the next calendar month, and the same rule holds after the last PDF.
    No two rows share (account, statement_month, currency).
11. **Migration 0047 backfill** — not applied on mercury yet, so edited in
    place: `backfill_statement_date` reads the (pk, statement_month) keys
    first and bulk_updates in batches of 200 (SQL Server allows 2,100
    parameters per statement).
12. **Position symbol width / apply batching** —
    `CoreMonthlyStatementPosition.symbol` is max_length 200 in the model and
    in 0047 (the "(no symbol: <security name>)" fallback can exceed 50);
    `--apply` creates the positions with `bulk_create(batch_size=200)`.
13. **HistoricalPrice is not used for pricing** — it records no currency, so
    a close cannot be matched to the row's section currency (symbol mapping
    is step 16): positions without a PDF holding price from the last known
    PDF price (`price_source=LAST_PDF`), and GAP rows carry positions as
    LAST_PDF, never PDF. The report keeps `history_priced_rows` (always 0
    until step 16) for output compatibility.

## Decisions (tdw, dry run on mercury, Oct 31)

14. **Rows after the last PDF statement need whole-month CSV coverage** —
    after an account's last stored PDF statement, a row (GAP or REPLAY) is
    emitted for a calendar month only when the CSV covers that whole month,
    i.e. the last CSV transaction date by `Transaction.date` (the export end,
    not the settlement-based effective date) is on or after that month end.
    With today's data the CSV ends 2026-07-31 and the last PDFs are dated
    2026-07-31, so no account may get a row after July 2026; trades dated up
    to 2026-07-31 that settle in August stay unplaced until a later PDF or
    CSV covers August. This refines decision 3's "up to the last CSV date"
    (which read the settlement-based effective date).
