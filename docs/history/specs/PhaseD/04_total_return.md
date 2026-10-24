# Feature Spec: Total Return Calculation

**Spec:** D4 — Spec 18 §5
**Project name:** portfolio_manager
**Customer:** tdwdev
**Branch:** `feature_misc_outstanding_items`

---

## Goal

Add total return metrics to the portfolio summary page at `/portfolio/`. Show:
- Total realised P&L (CAD) — from all closed trades
- Total income received (CAD) — all DIV, DIST, INT ever
- Total return (CAD) = unrealised P&L + realised P&L + income

These appear as additional summary cards on the portfolio page.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. Update `portfolio_summary` view in `core/views.py`

After the existing code that computes `total_unrealised_pnl_cad`, add:

```python
from core.utils import compute_realised_pnl
from django.db.models import Sum

# Total realised P&L (FIFO) — all time, all accounts
all_realised = compute_realised_pnl()
total_realised_pnl_cad = sum(t['realised_pnl_fifo'] for t in all_realised)

# Total income ever received
total_income_cad = Transaction.objects.filter(
    type__in=('DIV', 'DIST', 'INT')
).aggregate(total=Sum('amount_cad'))['total'] or Decimal('0')

# Total return = unrealised + realised + income
total_return_cad = total_unrealised_pnl_cad + total_realised_pnl_cad + total_income_cad
```

Pass to template context:
- `total_realised_pnl_cad`
- `total_income_cad`
- `total_return_cad`

### 2. Update `core/templates/core/portfolio.html`

Add a **second row of summary cards** below the existing three aggregate cards (Total Cost, Market Value, Unrealised P&L):

```html
<div class="row mb-4">
  <div class="col-md-4">
    <div class="card">
      <div class="card-body">
        <h6 class="card-subtitle text-muted">Total Realised P&L (CAD)</h6>
        <h3 class="card-title {% if total_realised_pnl_cad >= 0 %}text-success{% else %}text-danger{% endif %}">
          {{ total_realised_pnl_cad|floatformat:2 }}
        </h3>
      </div>
    </div>
  </div>
  <div class="col-md-4">
    <div class="card">
      <div class="card-body">
        <h6 class="card-subtitle text-muted">Total Income (CAD)</h6>
        <h3 class="card-title text-success">{{ total_income_cad|floatformat:2 }}</h3>
      </div>
    </div>
  </div>
  <div class="col-md-4">
    <div class="card">
      <div class="card-body">
        <h6 class="card-subtitle text-muted">Total Return (CAD)</h6>
        <h3 class="card-title {% if total_return_cad >= 0 %}text-success{% else %}text-danger{% endif %}">
          {{ total_return_cad|floatformat:2 }}
        </h3>
      </div>
    </div>
  </div>
</div>
```

### 3. Tests — `core/tests/test_portfolio_summary.py`

Extend existing `PortfolioSummaryTests`:

```python
def test_total_return_on_portfolio_summary(self):
    """Portfolio page shows total return section."""
    # Create a SELL transaction to produce realised P&L
    self.tx_sell = Transaction.objects.create(
        statement=self.statement, account=self.account,
        date='2024-03-01', type='SELL', symbol='AAPL',
        quantity=Decimal('5'), price=Decimal('160.00'),
        amount=Decimal('800.00'), currency='USD',
        amount_cad=Decimal('1080.00'), exchange_rate_cad=Decimal('1.35'),
        tx_hash='tr_test_sell1',
    )
    with patch('core.views.fetch_last_prices', return_value={
        'AAPL': Decimal('180.00'),
        'USDCAD=X': Decimal('1.35'),
    }):
        response = self.client.get(reverse('portfolio_summary'))
    self.assertContains(response, 'Total Return')
    self.assertContains(response, 'Total Realised')
    self.assertContains(response, 'Total Income')
```

---

## Constraints

- Do NOT remove or change the existing three aggregate summary cards on the portfolio page
- `compute_realised_pnl()` must be imported from `core.utils` — it already exists
- No new models or migrations
- No new Python packages
- Follow PEP 8. Add type hints.
- Do NOT push to git — see Manual Steps.

---

## Manual Steps Required

- [ ] `python manage.py test core --settings=portfolio_manager.settings_test`
- [ ] Manual browser test: verify total return cards render on portfolio page
- [ ] `git add -A && git commit -m "feat: total return calculation on portfolio summary page"`
- [ ] `git push origin feature_misc_outstanding_items`

---

## Human Approval Required

Human approval required before any git push.
