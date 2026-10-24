# Feature Spec: Top Movers Widget

**Spec:** D6 — NEXT_FEATURES #5.2
**Project name:** portfolio_manager
**Customer:** tdwdev
**Branch:** `feature_misc_outstanding_items`

---

## Goal

Add a small widget to the dashboard showing the top 5 gaining and top 5 losing positions by percentage change over a configurable period (default 30 days).

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager
- External data: yfinance (already installed)

---

## Requirements

### 1. Add `get_top_movers(positions: list[dict], lookback_days: int = 30) -> dict` in `core/utils.py`

```python
def get_top_movers(
    positions: list[dict],
    lookback_days: int = 30,
) -> dict:
    """Compute the top 5 gainers and top 5 losers from a list of positions.

    Uses yfinance historical data to determine the percentage change
    over the lookback period. Returns:
    {
        'gainers': [{'symbol': ..., 'pct_change': Decimal}, ...],
        'losers': [{'symbol': ..., 'pct_change': Decimal}, ...],
    }

    If yfinance is unavailable, returns empty lists (never raises).
    """
```

Logic:
- Collect unique symbols from positions where `total_shares > 0`
- For each symbol, fetch historical price `lookback_days` ago using `yf.download()`
- Compute percentage change: `(current_price - historical_price) / historical_price * 100`
- Sort gainers descending, losers ascending
- Return top 5 of each (fewer if not enough data)

### 2. Update `dashboard` view in `core/views.py`

After computing positions and enriching with prices (from Spec 03), add:

```python
from core.utils import get_top_movers

top_movers = get_top_movers(positions, lookback_days=30)
```

Pass to template context: `top_movers`

### 3. Update `core/templates/core/dashboard.html`

Add the top movers widget **below** the dollar summary cards and **above** the accounts overview section:

```html
{% if top_movers.gainers or top_movers.losers %}
<div class="row mb-4">
  <div class="col-md-6">
    <div class="card mb-3">
      <div class="card-header bg-success text-white">
        <i class="bi bi-trophy"></i> Top Gainers (30d)
      </div>
      <table class="table table-sm mb-0">
        <thead>
          <tr><th>Symbol</th><th class="text-end">Change</th></tr>
        </thead>
        <tbody>
          {% for m in top_movers.gainers %}
          <tr>
            <td>{{ m.symbol }}</td>
            <td class="text-success text-end">{{ m.pct_change|floatformat:1 }}%</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  <div class="col-md-6">
    <div class="card mb-3">
      <div class="card-header bg-danger text-white">
        <i class="bi bi-arrow-down-circle"></i> Top Losers (30d)
      </div>
      <table class="table table-sm mb-0">
        <thead>
          <tr><th>Symbol</th><th class="text-end">Change</th></tr>
        </thead>
        <tbody>
          {% for m in top_movers.losers %}
          <tr>
            <td>{{ m.symbol }}</td>
            <td class="text-danger text-end">{{ m.pct_change|floatformat:1 }}%</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endif %}
```

### 4. Tests — `core/tests/test_dashboard.py`

Extend existing `DashboardTests`:

```python
def test_dashboard_shows_top_movers(self):
    """Dashboard page renders top movers section when data is available."""
    with patch('core.views.fetch_last_prices', return_value={
        'AAPL': Decimal('180.00'),
        'VFV.TO': Decimal('110.00'),
        'USDCAD=X': Decimal('1.35'),
    }):
        with patch('core.utils.get_top_movers', return_value={
            'gainers': [{'symbol': 'AAPL', 'pct_change': Decimal('12.5')}],
            'losers': [{'symbol': 'MSFT', 'pct_change': Decimal('-3.2')}],
        }):
            response = self.client.get(reverse('dashboard'))
    self.assertEqual(response.status_code, 200)
    self.assertContains(response, 'Top Gainers')
    self.assertContains(response, 'Top Losers')

def test_top_movers_empty_when_no_positions(self):
    """Top movers widget is hidden when no positions."""
    with patch('core.views.fetch_last_prices', return_value={}):
        with patch('core.utils.get_top_movers', return_value={
            'gainers': [],
            'losers': [],
        }):
            response = self.client.get(reverse('dashboard'))
    self.assertEqual(response.status_code, 200)
    self.assertNotContains(response, 'Top Gainers')
```

---

## Constraints

- Never raise unhandled exceptions from yfinance — always return empty lists on failure
- No new Python packages required
- No migrations needed
- The widget is optional — if no data, the section is simply hidden
- Follow PEP 8. Add type hints.
- Do NOT push to git — see Manual Steps.

---

## Manual Steps Required

- [ ] `python manage.py test core --settings=portfolio_manager.settings_test`
- [ ] Manual browser test: verify top movers appear on dashboard with live data
- [ ] `git add -A && git commit -m "feat: top movers widget on dashboard"`
- [ ] `git push origin feature_misc_outstanding_items`

---

## Human Approval Required

Human approval required before any git push.
