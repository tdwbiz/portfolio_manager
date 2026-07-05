# Feature Spec: Dashboard Dollar Summary

**Spec:** D3 — NEXT_FEATURES #5.1
**Project name:** portfolio_manager
**Customer:** tdwdev
**Branch:** `feature_misc_outstanding_items`

---

## Goal

Add dollar-value summary cards to the dashboard at `/` showing total portfolio market value, total unrealised P&L (CAD), and year-to-date income. Currently the dashboard only shows counts (accounts, transactions, brokers).

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. Update `dashboard` view in `core/views.py`

At the top of the dashboard view, compute the following:

```python
from decimal import Decimal
from core.utils import compute_positions, enrich_positions_with_prices, fetch_last_prices

# Get positions and enrich with live prices
positions = compute_positions()
symbols = [p['symbol'] for p in positions if p['symbol']]
prices = fetch_last_prices(symbols)
positions = enrich_positions_with_prices(positions, prices)

# Total market value (CAD)
total_market_value_cad = sum(
    p['market_value_cad'] for p in positions if p.get('market_value_cad') is not None
)

# Total unrealised P&L (CAD)
total_unrealised_pnl_cad = sum(
    (p['market_value_cad'] - p['total_cost_cad'])
    for p in positions
    if p.get('market_value_cad') is not None
)

# YTD income
from django.db.models import Sum
from core.models import Transaction
from datetime import date

ytd_income_cad = Transaction.objects.filter(
    type__in=('DIV', 'DIST', 'INT'),
    date__year=date.today().year,
).aggregate(total=Sum('amount_cad'))['total'] or Decimal('0')
```

Pass to template context:
- `total_market_value_cad`
- `total_unrealised_pnl_cad`
- `ytd_income_cad`

### 2. Update `core/templates/core/dashboard.html`

Add three summary cards **at the top of the page**, before the existing "Accounts Overview" section.

Use Bootstrap cards with positive/negative styling:

```html
<div class="row mb-4">
  <div class="col-md-4">
    <div class="card">
      <div class="card-body">
        <h6 class="card-subtitle text-muted">Total Market Value (CAD)</h6>
        <h3 class="card-title">{{ total_market_value_cad|floatformat:2 }}</h3>
      </div>
    </div>
  </div>
  <div class="col-md-4">
    <div class="card">
      <div class="card-body">
        <h6 class="card-subtitle text-muted">Unrealised P&L (CAD)</h6>
        <h3 class="card-title {% if total_unrealised_pnl_cad >= 0 %}text-success{% else %}text-danger{% endif %}">
          {{ total_unrealised_pnl_cad|floatformat:2 }}
        </h3>
      </div>
    </div>
  </div>
  <div class="col-md-4">
    <div class="card">
      <div class="card-body">
        <h6 class="card-subtitle text-muted">YTD Income (CAD)</h6>
        <h3 class="card-title text-success">{{ ytd_income_cad|floatformat:2 }}</h3>
      </div>
    </div>
  </div>
</div>
```

### 3. Tests — `core/tests/test_dashboard.py` (or extend existing)

```python
def test_dashboard_shows_dollar_summary(self):
    """Dashboard page contains market value and P&L summary cards."""
    with patch('core.views.fetch_last_prices', return_value={
        'AAPL': Decimal('180.00'),
        'VFV.TO': Decimal('110.00'),
        'USDCAD=X': Decimal('1.35'),
    }):
        response = self.client.get(reverse('dashboard'))
    self.assertEqual(response.status_code, 200)
    # Check that dollar values appear in the response
    self.assertContains(response, 'Market Value')
    self.assertContains(response, 'Unrealised P&L')
    self.assertContains(response, 'YTD Income')

def test_dashboard_pnl_coloring_positive(self):
    """Positive P&L is styled green (text-success)."""
    with patch('core.views.fetch_last_prices', return_value={
        'AAPL': Decimal('180.00'),
        'VFV.TO': Decimal('110.00'),
        'USDCAD=X': Decimal('1.35'),
    }):
        response = self.client.get(reverse('dashboard'))
    self.assertEqual(response.status_code, 200)

def test_dashboard_pnl_coloring_negative(self):
    """Negative P&L is styled red (text-danger)."""
    # Create a position where market value < cost basis
    # (e.g., AAPL bought at 180, current price 150)
    with patch('core.views.fetch_last_prices', return_value={
        'AAPL': Decimal('150.00'),
        'USDCAD=X': Decimal('1.35'),
    }):
        response = self.client.get(reverse('dashboard'))
    self.assertEqual(response.status_code, 200)
```

---

## Constraints

- Do NOT change any existing dashboard content — only add dollar summary cards at the top
- Price fetch failures must degrade gracefully (show `0.00` if prices unavailable)
- Use existing `fetch_last_prices`, `compute_positions`, `enrich_positions_with_prices` utilities
- No new Python packages required
- No migrations needed
- Follow PEP 8. Add type hints.
- Do NOT push to git — see Manual Steps.

---

## Manual Steps Required

- [ ] `python manage.py test core --settings=portfolio_manager.settings_test`
- [ ] Manual browser test: verify dollar summary cards render on dashboard
- [ ] `git add -A && git commit -m "feat: dashboard dollar summary — market value, unrealised P&L, YTD income"`
- [ ] `git push origin feature_misc_outstanding_items`

---

## Human Approval Required

Human approval required before any git push.
