# Feature Spec: Portfolio Summary Grouped by Account and Category with Allocation %

**Spec:** D1 — Spec 16
**Project name:** portfolio_manager
**Customer:** tdwdev
**Branch:** `feature_misc_outstanding_items`

---

## Goal

Add subtotals grouped by account and by account category (RRSP/TFSA/Taxable), an allocation % column showing each holding's weight in the portfolio, and a currency breakdown panel on the portfolio summary page at `/portfolio/`.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager
- Frontend: Bootstrap 5 (already loaded in base.html)

---

## Requirements

### 1. Update `compute_positions()` in `core/utils.py`

Currently `compute_positions()` returns position dicts with keys: `symbol`, `broker`, `account_name`, `currency`, `total_shares`, `avg_cost_cad`, `total_cost_cad`.

Add the following keys to each position dict:
- `account_id`: `tx.account.account_id if tx.account else ''`
- `category`: `tx.account.category if tx.account else 'TAXABLE'`
- `category_display`: `tx.account.get_category_display() if tx.account else 'Taxable'`
- `owner`: `tx.account.owner if tx.account else ''`

The grouping key must change from `(tx.symbol, tx.statement.source, account_name)` to:
`(tx.symbol, tx.statement.source, tx.account.account_id if tx.account else '')`

**Do not remove or rename any existing keys.** Only add new ones.

### 2. Add `group_positions_by_account(positions: list[dict]) -> list[dict]` in `core/utils.py`

Group an enriched positions list by account, computing per-account subtotals.

Returns a list of account-group dicts, each with:
```
account_id, account_name, broker, category, category_display, owner,
positions (list of position dicts for that account),
subtotal_cost_cad, subtotal_market_value_cad, subtotal_unrealised_pnl_cad
```

Logic:
- Group positions by `account_id`
- `subtotal_cost_cad` = sum of `total_cost_cad` for all positions in group
- `subtotal_market_value_cad` = sum of `market_value_cad` (skip Nones)
- `subtotal_unrealised_pnl_cad` = `subtotal_market_value_cad - sum(total_cost_cad for positions where market_value_cad is not None)`
- Sort account groups by `(category, broker, account_id)`

### 3. Add `group_positions_by_category(positions: list[dict]) -> list[dict]` in `core/utils.py`

Group an enriched positions list by account category, computing per-category subtotals.

Returns a list of category-group dicts, each with:
```
category, category_display,
positions (list of position dicts for that category),
subtotal_cost_cad, subtotal_market_value_cad, subtotal_unrealised_pnl_cad,
allocation_pct
```

Logic:
- Group positions by `category`
- Same subtotal logic as `group_positions_by_account`
- Sort categories in fixed order: RRSP, LIRA, TFSA, RESP, TAXABLE, OTHER
- Use a sort key mapping: `{'RRSP': 0, 'LIRA': 1, 'TFSA': 2, 'RESP': 3, 'TAXABLE': 4, 'OTHER': 5}`

### 4. Update `portfolio_summary` view in `core/views.py`

After the existing code that computes positions and enriches with prices:

**A.** Compute `allocation_pct` for each position:
```python
for pos in positions:
    pos['allocation_pct'] = (
        round(pos['market_value_cad'] / total_market_value_cad * 100, 2)
        if pos.get('market_value_cad') and total_market_value_cad
        else None
    )
```

**B.** Build `account_groups`:
```python
account_groups = group_positions_by_account(positions)
```

**C.** Build `category_groups`:
```python
category_groups = group_positions_by_category(positions)
for grp in category_groups:
    grp['allocation_pct'] = (
        round(grp['subtotal_market_value_cad'] / total_market_value_cad * 100, 2)
        if grp['subtotal_market_value_cad'] and total_market_value_cad
        else None
    )
```

**D.** Build `currency_breakdown`:
```python
currency_data = {}
for pos in positions:
    cur = pos['currency']
    if cur not in currency_data:
        currency_data[cur] = {'cost_cad': Decimal('0'), 'market_value_cad': Decimal('0')}
    currency_data[cur]['cost_cad'] += pos['total_cost_cad']
    if pos.get('market_value_cad'):
        currency_data[cur]['market_value_cad'] += pos['market_value_cad']
currency_breakdown = [
    {'currency': k, **v} for k, v in sorted(currency_data.items())
]
```

**E.** Add to render context: `account_groups`, `category_groups`, `currency_breakdown`

### 5. Update `core/templates/core/portfolio.html`

Restructure into four sections. **Keep the existing aggregate summary cards** (Total Cost, Market Value, Unrealised P&L) at the top.

**Section A — Currency Breakdown panel** (below aggregate cards):

A horizontal row of small cards, one per currency:
```html
<div class="row mb-3">
  {% for cur in currency_breakdown %}
  <div class="col-auto">
    <div class="card">
      <div class="card-body py-1 px-2">
        <strong>{{ cur.currency }}</strong>: Cost ${{ cur.cost_cad|floatformat:2 }} | Market ${{ cur.market_value_cad|floatformat:2 }}
      </div>
    </div>
  </div>
  {% endfor %}
</div>
```
Only render if `currency_breakdown` has more than one currency.

**Section B — Summary by Category** (collapsible card):

```html
<div class="card mb-4">
  <div class="card-header" data-bs-toggle="collapse" data-bs-target="#collapseCategory" style="cursor:pointer;">
    <i class="bi bi-chevron-down"></i> Summary by Category
  </div>
  <div id="collapseCategory" class="collapse show">
    <table class="table table-sm">
      <thead>
        <tr>
          <th>Category</th>
          <th>Cost (CAD)</th>
          <th>Market Value (CAD)</th>
          <th>Unrealised P&L (CAD)</th>
          <th>% of Portfolio</th>
        </tr>
      </thead>
      <tbody>
        {% for grp in category_groups %}
        <tr>
          <td>{{ grp.category_display }}</td>
          <td>{{ grp.subtotal_cost_cad|floatformat:2 }}</td>
          <td>{{ grp.subtotal_market_value_cad|floatformat:2 }}</td>
          <td class="{% if grp.subtotal_unrealised_pnl_cad >= 0 %}text-success{% else %}text-danger{% endif %}">
            {{ grp.subtotal_unrealised_pnl_cad|floatformat:2 }}
          </td>
          <td>{{ grp.allocation_pct|default:'—' }}%</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
```

**Section C — Summary by Account** (collapsible card):

```html
<div class="card mb-4">
  <div class="card-header" data-bs-toggle="collapse" data-bs-target="#collapseAccount" style="cursor:pointer;">
    <i class="bi bi-chevron-down"></i> Summary by Account
  </div>
  <div id="collapseAccount" class="collapse show">
    <table class="table table-sm">
      <thead>
        <tr>
          <th>Account</th>
          <th>Category</th>
          <th>Owner</th>
          <th>Broker</th>
          <th>Cost (CAD)</th>
          <th>Market Value (CAD)</th>
          <th>Unrealised P&L (CAD)</th>
        </tr>
      </thead>
      <tbody>
        {% for grp in account_groups %}
        <tr>
          <td>{{ grp.account_name }}</td>
          <td>{{ grp.category_display }}</td>
          <td>{{ grp.owner|default:'&mdash;' }}</td>
          <td>{{ grp.broker }}</td>
          <td>{{ grp.subtotal_cost_cad|floatformat:2 }}</td>
          <td>{{ grp.subtotal_market_value_cad|floatformat:2 }}</td>
          <td class="{% if grp.subtotal_unrealised_pnl_cad >= 0 %}text-success{% else %}text-danger{% endif %}">
            {{ grp.subtotal_unrealised_pnl_cad|floatformat:2 }}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
```

**Section D — Positions Table** (existing, keep as-is):

Add one new column after the "P&L %" column:
```html
<th>% of Portfolio</th>
```

And in the body:
```html
<td>{{ pos.allocation_pct|default:'&mdash;' }}%</td>
```

### 6. Tests — `core/tests/test_portfolio_summary.py`

Add to the existing `PortfolioSummaryTests` class:

```python
def test_positions_include_account_category(self):
    """Positions include account_id, category, category_display, owner."""
    self.account.category = 'RRSP'
    self.account.owner = 'Tom'
    self.account.save()
    positions = compute_positions()
    aapl = [p for p in positions if p['symbol'] == 'AAPL'][0]
    self.assertEqual(aapl['account_id'], 'PORT001')
    self.assertEqual(aapl['category'], 'RRSP')
    self.assertEqual(aapl['category_display'], 'RRSP (Retirement)')
    self.assertEqual(aapl['owner'], 'Tom')

def test_group_by_account_subtotals(self):
    """group_positions_by_account computes correct subtotals."""
    # Create a second account
    acct2 = Account.objects.create(
        account_id='PORT002', name='Portfolio 2', broker='RBC',
        currency='CAD', starting_balance=Decimal('0'),
    )
    stmt2 = BrokerStatement.objects.create(filename='test2.csv', source='RBC', broker='RBC')
    self.tx3 = Transaction.objects.create(
        statement=stmt2, account=acct2,
        date='2024-01-20', type='BUY', symbol='MSFT',
        quantity=Decimal('5'), price=Decimal('400.00'),
        amount=Decimal('-2000.00'), currency='CAD',
        amount_cad=Decimal('-2000.00'), exchange_rate_cad=Decimal('1.0'),
        tx_hash='port_test_msft_buy',
    )
    positions = enrich_positions_with_prices(compute_positions(), {})
    groups = group_positions_by_account(positions)
    self.assertEqual(len(groups), 2)
    port002_grp = [g for g in groups if g['account_id'] == 'PORT002'][0]
    self.assertAlmostEqual(float(port002_grp['subtotal_cost_cad']), 2000.0)

def test_group_by_category_ordering(self):
    """group_positions_by_category returns categories in fixed order."""
    acct2 = Account.objects.create(
        account_id='PORT002', name='Portfolio 2', broker='RBC',
        currency='CAD', starting_balance=Decimal('0'), category='TAXABLE',
    )
    stmt2 = BrokerStatement.objects.create(filename='test2.csv', source='RBC', broker='RBC')
    self.tx3 = Transaction.objects.create(
        statement=stmt2, account=acct2,
        date='2024-01-20', type='BUY', symbol='MSFT',
        quantity=Decimal('5'), price=Decimal('400.00'),
        amount=Decimal('-2000.00'), currency='CAD',
        amount_cad=Decimal('-2000.00'), exchange_rate_cad=Decimal('1.0'),
        tx_hash='port_test_msft_taxable',
    )
    positions = enrich_positions_with_prices(compute_positions(), {})
    groups = group_positions_by_category(positions)
    categories = [g['category'] for g in groups]
    rrsp_idx = categories.index('RRSP')
    taxable_idx = categories.index('TAXABLE')
    self.assertLess(rrsp_idx, taxable_idx)

def test_portfolio_summary_page_shows_category_section(self):
    """Portfolio page contains 'Summary by Category' heading."""
    with patch('core.views.fetch_last_prices', return_value={}):
        response = self.client.get(reverse('portfolio_summary'))
    self.assertContains(response, 'Summary by Category')

def test_portfolio_summary_page_shows_account_section(self):
    """Portfolio page contains 'Summary by Account' heading."""
    with patch('core.views.fetch_last_prices', return_value={}):
        response = self.client.get(reverse('portfolio_summary'))
    self.assertContains(response, 'Summary by Account')

def test_allocation_pct_sums_to_100(self):
    """Allocation % across positions with prices sums to ~100%."""
    mock_prices = {
        'AAPL': Decimal('180.00'),
        'VFV.TO': Decimal('110.00'),
        'USDCAD=X': Decimal('1.35'),
    }
    with patch('core.views.fetch_last_prices', return_value=mock_prices):
        response = self.client.get(reverse('portfolio_summary'))
    # Parse the response and check allocation_pct values sum to ~100
    self.assertEqual(response.status_code, 200)
```

---

## Constraints

- Do NOT change or remove any existing fields returned by `compute_positions()` — only add new fields
- Do NOT create any migrations — Spec 15 migration handles the model change
- `group_positions_by_account` and `group_positions_by_category` must be importable from `core.utils`
- The existing flat positions table must remain fully intact — do not remove any existing columns
- No new Python packages required
- Follow PEP 8. Add type hints to all new functions.
- Do NOT push to git — see Manual Steps.

---

## Manual Steps Required

- [ ] `python manage.py test core --settings=portfolio_manager.settings_test`
- [ ] Manual browser test: verify all four portfolio summary sections render correctly
- [ ] `git add -A && git commit -m "feat: portfolio summary grouped by account and category with allocation %"`
- [ ] `git push origin feature_misc_outstanding_items`

---

## Human Approval Required

Human approval required before any git push.
