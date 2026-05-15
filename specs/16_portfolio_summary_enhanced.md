# Feature Spec: Portfolio Summary Enhancements

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/portfolio-summary-enhanced

---

## Context

Specs 01–14 are complete. Spec 15 (Account Categories) must be merged first — this spec
depends on `Account.category` and `Account.owner` existing in the database.

The current Portfolio Summary page (`/portfolio/`) shows a flat table of all open
positions across all accounts with three aggregate totals (total cost, market value,
unrealised P&L). There is no grouping, no allocation percentages, no currency split, and
no way to understand the portfolio at an account or tax-category level.

`core/utils.py` has `compute_positions()` which returns a flat list of position dicts,
each containing: `symbol`, `broker`, `account_name`, `currency`, `total_shares`,
`avg_cost_cad`, `total_cost_cad`. After enrichment via `enrich_positions_with_prices()`,
each dict also has `last_price`, `market_value_cad`, `unrealised_pnl_cad`.

This spec adds:
- Subtotals grouped by Account
- Subtotals grouped by Account Category (RRSP / TFSA / Taxable / etc.)
- Allocation % column (position weight in total portfolio)
- Currency breakdown summary panel (CAD holdings vs USD holdings)

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager
- Frontend: Bootstrap 5, Chart.js (already loaded in base.html)

---

## Requirements

### 1. Update `core/utils.py` — Enrich positions with account metadata

Update `compute_positions()` to also attach `account_id` and `category` to each
position dict. Currently it only stores `account_name`.

Change the key and the dict construction inside `compute_positions()`:
- Replace the grouping key `(tx.symbol, tx.statement.source, account_name)` with
  `(tx.symbol, tx.statement.source, tx.account.account_id if tx.account else '')`.
- In the position dict, add:
  - `account_id`: `tx.account.account_id if tx.account else ''`
  - `category`: `tx.account.category if tx.account else 'TAXABLE'`
  - `category_display`: `tx.account.get_category_display() if tx.account else 'Taxable'`
  - `owner`: `tx.account.owner if tx.account else ''`

No change to the return signature or field names that already exist.

### 2. Add helper function `group_positions_by_account(positions)` in `core/utils.py`

```python
def group_positions_by_account(positions: list[dict]) -> list[dict]:
    """Group an enriched positions list by account, computing per-account subtotals.

    Returns a list of account-group dicts, each with:
        account_id, account_name, broker, category, category_display, owner,
        positions (list of position dicts for that account),
        subtotal_cost_cad, subtotal_market_value_cad, subtotal_unrealised_pnl_cad
    """
```

- Groups positions by `account_id`.
- Computes `subtotal_cost_cad` as the sum of `total_cost_cad` for all positions in the group.
- Computes `subtotal_market_value_cad` as the sum of `market_value_cad` (skip Nones).
- Computes `subtotal_unrealised_pnl_cad` as `subtotal_market_value_cad - sum(total_cost_cad for positions where market_value_cad is not None)`.
- Sorts account groups by `(category, broker, account_id)`.

### 3. Add helper function `group_positions_by_category(positions)` in `core/utils.py`

```python
def group_positions_by_category(positions: list[dict]) -> list[dict]:
    """Group an enriched positions list by account category, computing per-category subtotals.

    Returns a list of category-group dicts, each with:
        category, category_display,
        positions (list of position dicts for that category),
        subtotal_cost_cad, subtotal_market_value_cad, subtotal_unrealised_pnl_cad,
        allocation_pct (percentage of total portfolio market value — computed in view)
    """
```

- Groups positions by `category`.
- Uses the same subtotal logic as `group_positions_by_account`.
- Sorts category groups in this fixed order: RRSP, LIRA, TFSA, RESP, TAXABLE, OTHER.
  Use a sort key that maps each category code to an integer index (0–5).

### 4. Add allocation % calculation in `core/views.py` — `portfolio_summary` view

After enriching positions and computing `total_market_value_cad`:

- For each position in the flat list, add an `allocation_pct` field:
  ```python
  pos['allocation_pct'] = (
      round(pos['market_value_cad'] / total_market_value_cad * 100, 2)
      if pos.get('market_value_cad') and total_market_value_cad
      else None
  )
  ```
- Call `group_positions_by_account(enriched_positions)` to produce `account_groups`.
- Call `group_positions_by_category(enriched_positions)` to produce `category_groups`.
- For each category group, set `allocation_pct = round(group['subtotal_market_value_cad'] / total_market_value_cad * 100, 2)` if `total_market_value_cad > 0` else `None`.
- Add `account_groups` and `category_groups` to the render context.

Import `group_positions_by_account` and `group_positions_by_category` from `core.utils`.

### 5. Add currency breakdown helper in `core/views.py` — `portfolio_summary` view

After computing the grouped data, build a `currency_breakdown` list for the context:

```python
currency_breakdown = {}
for pos in enriched_positions:
    cur = pos['currency']
    if cur not in currency_breakdown:
        currency_breakdown[cur] = {'cost_cad': Decimal('0'), 'market_value_cad': Decimal('0')}
    currency_breakdown[cur]['cost_cad'] += pos['total_cost_cad']
    if pos.get('market_value_cad'):
        currency_breakdown[cur]['market_value_cad'] += pos['market_value_cad']
currency_breakdown = [
    {'currency': k, **v} for k, v in sorted(currency_breakdown.items())
]
```

Add `currency_breakdown` to the render context.

### 6. Update `core/templates/core/portfolio_summary.html`

Restructure the template into four sections. Keep the existing overall aggregate summary
cards at the top (Total Cost, Market Value, Unrealised P&L) — do not remove them.

**Section A — Currency Breakdown panel** (new, below aggregate cards):

A small horizontal card row showing one card per currency (typically CAD and USD):
```
[USD: Cost $xxx,xxx | Market Value $xxx,xxx]  [CAD: Cost $xxx,xxx | Market Value $xxx,xxx]
```
Only render this section if `currency_breakdown` has more than one currency.

**Section B — Summary by Category** (new, collapsible card):

A Bootstrap card with header "Summary by Category". Inside, a table with columns:
`Category | Cost (CAD) | Market Value (CAD) | Unrealised P&L (CAD) | % of Portfolio`

One row per `category_groups` entry. Use `positive`/`negative` CSS classes on P&L cells.
Use `|floatformat:2` on all numbers. Show `—` for None values.

**Section C — Summary by Account** (new, collapsible card):

A Bootstrap card with header "Summary by Account". Inside, a table with columns:
`Account | Category | Owner | Broker | Cost (CAD) | Market Value (CAD) | Unrealised P&L (CAD)`

One row per `account_groups` entry. Category and Owner columns show the human-readable
values (`category_display`, `owner|default:"—"`).

**Section D — Positions Table** (existing, keep as-is):

The existing flat positions table remains. Add one new column after "P&L %":
`% of Portfolio` — shows `pos.allocation_pct|floatformat:1` + `%` symbol, or `—` if None.

For Sections B and C, wrap each card in a Bootstrap collapse:
```html
<div class="card mb-4">
  <div class="card-header" data-bs-toggle="collapse" data-bs-target="#collapseCategory" style="cursor:pointer;">
    <i class="bi bi-chevron-down"></i> Summary by Category
  </div>
  <div id="collapseCategory" class="collapse show">
    ...
  </div>
</div>
```

### 7. Tests — `core/tests/test_portfolio_summary.py`

Add the following test methods to the existing test class (do not replace existing tests):

- `test_positions_include_account_category`: call `compute_positions()` with a test
  account that has `category='RRSP'`; assert that the returned position dict contains
  `category='RRSP'` and `category_display='RRSP (Retirement)'`.
- `test_group_by_account_subtotals`: create two positions for two different accounts;
  call `group_positions_by_account()`; assert there are two groups and each has correct
  `subtotal_cost_cad`.
- `test_group_by_category_ordering`: create positions for accounts with categories
  `TAXABLE` and `RRSP`; call `group_positions_by_category()`; assert that `RRSP` group
  appears before `TAXABLE` group (fixed order).
- `test_portfolio_summary_page_shows_category_section`: GET `/portfolio/`; assert
  response contains `"Summary by Category"`.
- `test_portfolio_summary_page_shows_account_section`: GET `/portfolio/`; assert
  response contains `"Summary by Account"`.
- `test_allocation_pct_sums_to_100`: with two positions where both have prices, assert
  that `sum(pos['allocation_pct'] for pos in enriched if pos['allocation_pct'])` is
  approximately `100.0` (within 0.1).

Run with: `python manage.py test core --settings=portfolio_manager.settings_test`

---

## Constraints

- Do NOT change or remove any existing fields returned by `compute_positions()` —
  only add new fields to the dict.
- Do NOT create any migrations — Spec 15 migration handles the model change.
- `group_positions_by_account` and `group_positions_by_category` must be importable
  from `core.utils`.
- The existing flat positions table (Section D) must remain fully intact — do not
  remove any existing columns.
- No new Python packages required (Bootstrap 5 and Chart.js already present).
- Follow PEP 8. Add type hints to all new functions.
- Do NOT push to git — see Manual Steps.

---

## Manual Steps Required

- [ ] Ensure Spec 15 migration has been applied before testing
- [ ] `python manage.py test core --settings=portfolio_manager.settings_test`
- [ ] Manual review of all four portfolio summary sections in the browser
- [ ] `git add -A && git commit -m "feat: portfolio summary grouped by account and category with allocation %"`
- [ ] `git push origin feature/portfolio-summary-enhanced`

---

## Human Approval Required

Human approval required before any git push.
