# Feature Spec: Reporting Improvements

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/reporting-improvements

---

## Context

Specs 01–14 are complete. The following reporting pages exist:

- **Realised P&L** (`/realised-pnl/`): Shows closed trades with FIFO and avg-cost P&L.
  Filter: by account only. No date range, no symbol filter, no year grouping.
- **Income Summary** (`/income/`): Shows dividends, distributions, interest by year and
  symbol. Filter: by year and type only. No account filter.

These are the primary tax-reporting tools in the application, but both are missing
filters that are essential for annual tax filing — specifically date range isolation,
account breakdown, and a single consolidated tax year view.

This spec adds:
1. Date range and symbol filters to Realised P&L
2. Year grouping on Realised P&L
3. Account filter to Income Summary
4. Annual Tax Summary report (new page)
5. Total Return calculation on portfolio summary

Spec 15 (account categories) should be merged first so that the tax summary can
group income and gains by account category.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. Realised P&L — Add date range and symbol filters

**Update `core/utils.py` — `compute_realised_pnl()` signature:**

Extend the existing function to accept additional optional parameters:

```python
def compute_realised_pnl(
    account_id: str | None = None,
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
```

- `symbol`: if provided, filter the query to transactions where `tx.symbol == symbol`
  (case-insensitive match — normalise both sides to uppercase).
- `date_from` / `date_to`: ISO date strings. Filter the returned result list to trades
  where `sell_date >= date_from` and `sell_date <= date_to` (after all FIFO/avg-cost
  processing). Do NOT filter transactions before processing — the full lot history is
  still needed for correct cost basis computation. Apply the date filter only to the
  final `result` list before returning.
- All four parameters remain optional. Existing callers with no arguments continue to work.

**Update `core/views.py` — `realised_pnl` view:**

- Read additional GET params: `symbol`, `date_from`, `date_to`.
- Validate `date_from` and `date_to` using `_validate_date_param()` (already exists in
  views.py). Silently discard invalid date strings.
- Validate `symbol`: strip, uppercase, max 20 characters.
- Pass all four params to `compute_realised_pnl()`.
- Add `symbol_filter`, `date_from_filter`, `date_to_filter` to the template context.
- Compute a new context variable `year_groups` — see requirement 2 below.

**Update `core/templates/core/realised_pnl.html`:**

- Add `Symbol` text input and `Date From` / `Date To` date inputs to the existing
  filter form.
- Persist filter values in the form inputs using the new `*_filter` context variables.
- All existing columns and the account filter dropdown remain unchanged.

### 2. Realised P&L — Year grouping

**In `core/views.py` — `realised_pnl` view**, after calling `compute_realised_pnl()`:

Group the `trades` list by calendar year:

```python
from collections import defaultdict
year_groups = defaultdict(list)
for trade in trades:
    year_groups[trade['sell_date'].year].append(trade)

year_groups_sorted = sorted(
    [
        {
            'year': year,
            'trades': trade_list,
            'total_pnl_fifo': sum(t['realised_pnl_fifo'] for t in trade_list),
            'total_pnl_avg': sum(t['realised_pnl_avg'] for t in trade_list),
        }
        for year, trade_list in year_groups.items()
    ],
    key=lambda g: g['year'],
    reverse=True,
)
```

Add `year_groups_sorted` to the template context.

**Update `core/templates/core/realised_pnl.html`:**

- Add a view toggle at the top: `View: [Flat] [By Year]` — implemented as two
  buttons/links that add `?group_by=year` or `?group_by=flat` to the current URL
  (preserve existing filters).
- When `group_by=year` is in the request, render one collapsible Bootstrap card per
  year group, each showing:
  - Card header: `{year} — FIFO P&L: ${total_pnl_fifo} | Avg P&L: ${total_pnl_avg}`
  - Card body: the trades table filtered to that year
- When `group_by=flat` (default), render the existing flat table unchanged.

### 3. Income Summary — Add account filter

**Update `core/views.py` — `income_summary` view:**

- Add a GET param `account` for filtering by `account_id`.
- Validate: strip, max 50 characters, check exists in `Account.objects`.
- Filter the transaction queryset: `qs = qs.filter(account__account_id=account)` when
  an account is provided.
- Add `account_filter` and `accounts` (all `Account` objects ordered by `account_id`)
  to the template context.

**Update `core/templates/core/income_summary.html`:**

- Add an `Account` dropdown to the existing filter form, using the same pattern as
  the account dropdown in the transaction list and realised P&L.
- Persist the selected value using `account_filter`.
- All existing filters (year, type) and the summary totals bar remain unchanged.

### 4. Annual Tax Summary page (new)

**New view `tax_summary` in `core/views.py`:**

URL: `/reports/tax-summary/`

Accepts GET param `year` (integer, required). If missing, default to the previous
calendar year. Renders a consolidated tax report for the selected year covering:

**Section A — Capital Gains / Losses:**
Call `compute_realised_pnl(date_from=f'{year}-01-01', date_to=f'{year}-12-31')`.
Compute:
- `total_gains_fifo`: sum of `realised_pnl_fifo` where positive
- `total_losses_fifo`: sum of `realised_pnl_fifo` where negative (shown as negative)
- `net_capital_gain_fifo`: `total_gains_fifo + total_losses_fifo`
- Same three values for avg-cost method

**Section B — Income:**
Query transactions for the year with types in `('DIV', 'DIST', 'INT')`.
Group by type and compute totals in CAD. Also compute:
- `total_gross_income_cad`

**Section C — Withholding Tax:**
Query transactions for the year with types in `('WITHTAX', 'TAX')`.
Compute `total_tax_withheld_cad` (sum of `amount_cad`, will be negative).

**Section D — By Account Category** (requires Spec 15):
For each category present in the data, show the capital gains and income breakdown.
Use `Account.category` joined through transactions. If an account has no category
(migrated before Spec 15), treat as `TAXABLE`.

Pass to template: `year`, `years_available` (distinct years from all relevant
transactions), plus all computed section variables.

**New URL in `core/urls.py`:**
```python
path('reports/tax-summary/', views.tax_summary, name='tax_summary'),
```

**New nav link in `core/templates/core/base.html`:**
Add a "Reports" dropdown (Bootstrap nav dropdown) containing:
- `<a href="{% url 'tax_summary' %}">Annual Tax Summary</a>`

**New template `core/templates/core/tax_summary.html`:**
- Extends `core/base.html`.
- Year selector dropdown at the top (GET form, auto-submits on change).
- Four sections as Bootstrap cards (A–D as above), each collapsible.
- All amounts in CAD with `|floatformat:2`.
- A "Print / Export to PDF" note in the footer of the page:
  `"To save as PDF, use your browser's Print function (Ctrl+P / ⌘+P) and select 'Save as PDF'."` 
  This requires no extra packages — the instruction is sufficient for a personal app.

### 5. Total Return on Portfolio Summary

**Update `core/views.py` — `portfolio_summary` view:**

After computing `total_unrealised_pnl_cad`, compute total return metrics:

```python
# Sum of all realised P&L (FIFO) ever — no date filter
all_realised = compute_realised_pnl()
total_realised_pnl_cad = sum(t['realised_pnl_fifo'] for t in all_realised)

# Sum of all income ever received
total_income_cad = Transaction.objects.filter(
    type__in=('DIV', 'DIST', 'INT')
).aggregate(total=Sum('amount_cad'))['total'] or Decimal('0')

total_return_cad = total_unrealised_pnl_cad + total_realised_pnl_cad + total_income_cad
```

Import `compute_realised_pnl` from `core.utils`.
Import `Sum` from `django.db.models`.

Add `total_realised_pnl_cad`, `total_income_cad`, `total_return_cad` to the render
context.

**Update `core/templates/core/portfolio_summary.html`:**

Add a fourth summary card (or a second row of cards) showing:
- `Total Realised P&L (CAD)` with `positive`/`negative` class
- `Total Income (CAD)` 
- `Total Return (CAD)` with `positive`/`negative` class

These appear below (or alongside) the existing three aggregate cards. Do not remove
the existing three cards.

### 6. Tests

**`core/tests/test_realised_pnl.py`** — add to existing class:

- `test_date_range_filter`: create a SELL in 2024 and a SELL in 2025; call
  `compute_realised_pnl(date_from='2024-01-01', date_to='2024-12-31')`; assert only
  the 2024 trade is returned.
- `test_symbol_filter`: create SELLs for AAPL and MSFT; call with `symbol='AAPL'`;
  assert only AAPL trades returned.
- `test_realised_pnl_year_grouping_view`: GET `/realised-pnl/?group_by=year`; assert
  response contains a year heading (e.g. the year of the test SELLs).
- `test_realised_pnl_date_filter_view`: GET `/realised-pnl/?date_from=2024-01-01&date_to=2024-12-31`;
  assert 200.
- `test_realised_pnl_invalid_date_ignored`: GET with `date_from=baddate`; assert 200
  (invalid date silently ignored, not a 400).

**`core/tests/test_income_summary.py`** — add to existing class:

- `test_income_account_filter`: create income transactions in two accounts; GET
  `/income/?account=ACCOUNT1`; assert only transactions from `ACCOUNT1` appear in
  context `rows`.
- `test_income_account_filter_invalid`: GET `/income/?account=DOESNOTEXIST`; assert 200
  and `rows` is empty (not an error).

**New `core/tests/test_tax_summary.py`:**

- `TestCase` class `TaxSummaryTests`.
- `setUp`: create Account, BrokerStatement, and a mix of transactions in 2024:
  - 1 BUY and 1 SELL of AAPL (produces realised gain)
  - 1 DIV transaction
  - 1 WITHTAX transaction
- `test_tax_summary_page_loads`: GET `/reports/tax-summary/?year=2024`; assert 200.
- `test_tax_summary_default_year`: GET `/reports/tax-summary/` (no year); assert 200.
- `test_tax_summary_shows_capital_gains`: GET with `year=2024`; assert response contains
  `"Capital Gains"`.
- `test_tax_summary_shows_income_section`: assert response contains `"Income"`.
- `test_tax_summary_shows_tax_section`: assert response contains `"Withholding Tax"`.
- `test_total_return_on_portfolio_summary`: GET `/portfolio/`; assert response contains
  `"Total Return"`.

Run with: `python manage.py test core --settings=portfolio_manager.settings_test`

---

## Constraints

- Do NOT change the existing `compute_realised_pnl()` return dict format — only extend
  the function signature with new optional parameters.
- The date range filter in `compute_realised_pnl()` must be applied AFTER full lot
  processing to preserve correct FIFO cost basis. Do not filter transactions before
  the FIFO loop.
- `_validate_date_param()` in `views.py` must be used for all date input validation
  (already exists from the security spec).
- No new Python packages required.
- Do NOT create any migrations.
- Follow PEP 8. Add type hints to all new functions.
- Do NOT push to git — see Manual Steps.

---

## Manual Steps Required

- [ ] `python manage.py test core --settings=portfolio_manager.settings_test`
- [ ] Manual browser test of all new/updated report pages with real data
- [ ] Verify tax summary PDF print layout using browser print preview
- [ ] `git add -A && git commit -m "feat: reporting improvements — P&L filters, year grouping, income by account, tax summary, total return"`
- [ ] `git push origin feature/reporting-improvements`

---

## Human Approval Required

Human approval required before any git push.
