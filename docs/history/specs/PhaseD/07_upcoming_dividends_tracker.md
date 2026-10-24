# Feature Spec: Upcoming Dividends Tracker

**Spec:** D7 — NEXT_FEATURES #5.3
**Project name:** portfolio_manager
**Customer:** tdwdev
**Branch:** `feature_misc_outstanding_items`

---

## Goal

Add a widget to the dashboard showing expected dividend/income payments in the next 30–90 days. This supports cash flow planning for income investors.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. Create `DividendSchedule` model (optional, use existing data if possible)

Since this is a personal app and dividend schedules can vary, we'll use a simple approach:

**Option A (preferred): Manual schedule table**

Add a new model in `core/models.py`:

```python
class DividendSchedule(models.Model):
    """Manually tracked expected dividend/income payments."""
    symbol = models.CharField(max_length=50)
    pay_date = models.DateField()
    amount_per_share = models.DecimalField(max_digits=10, decimal_places=6)
    currency = models.CharField(max_length=3, default='USD')
    exchange_rate_cad = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal('1.0'))
    type = models.CharField(max_length=10, choices=[
        ('DIV', 'Dividend'),
        ('DIST', 'Distribution'),
        ('INT', 'Interest'),
    ], default='DIV')
    notes = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['pay_date', 'symbol']
        unique_together = [('symbol', 'pay_date', 'type')]

    def __str__(self):
        return f"{self.symbol} - {self.pay_date} ({self.get_type_display})"

    def amount_cad_per_share(self):
        return self.amount_per_share * self.exchange_rate_cad
```

**Option B (alternative): Use existing dividend payment frequency from transactions**

If you prefer no model changes, derive expected upcoming dividends from historical DIV transaction patterns (monthly/quarterly/semi-annual frequency). This is less reliable but requires no schema changes. **Recommend Option A** for accuracy.

### 2. Add helper function `get_upcoming_dividends(days_ahead=30)` in `core/utils.py`

```python
def get_upcoming_dividends(days_ahead: int = 30) -> list[dict]:
    """Return upcoming dividend/income payments in the next N days.

    For each scheduled payment, multiply amount_per_share by current
    holdings (from compute_positions) to get total expected amount.

    Returns a list of dicts:
    [
        {'symbol': 'AAPL', 'pay_date': date, 'type': 'DIV',
         'amount_per_share': Decimal, 'amount_cad': Decimal,
         'shares_held': Decimal, 'total_cad': Decimal},
        ...
    ]

    Returns empty list if no schedules exist or yfinance fails.
    """
```

Logic:
- Query `DividendSchedule.objects.filter(pay_date__gte=today, pay_date__lte=today + days_ahead, is_active=True)`
- For each schedule, look up shares_held from `compute_positions()` for the symbol
- Compute `total_cad = shares_held * amount_cad_per_share()`
- Sort by `pay_date` ascending
- Return up to next 90 days by default (30 days for the widget)

### 3. Update `dashboard` view in `core/views.py`

After existing dashboard logic, add:

```python
from core.utils import get_upcoming_dividends

upcoming_dividends = get_upcoming_dividends(days_ahead=30)
```

Pass to template context: `upcoming_dividends`

### 4. Update `core/templates/core/dashboard.html`

Add the upcoming dividends widget **below** the top movers section:

```html
{% if upcoming_dividends %}
<div class="card mb-4">
  <div class="card-header">
    <i class="bi bi-calendar-event"></i> Upcoming Dividends (Next 30 Days)
  </div>
  <table class="table table-sm">
    <thead>
      <tr>
        <th>Symbol</th>
        <th>Type</th>
        <th>Pay Date</th>
        <th>Shares</th>
        <th>Per Share (CAD)</th>
        <th class="text-end">Total (CAD)</th>
      </tr>
    </thead>
    <tbody>
      {% for d in upcoming_dividends %}
      <tr>
        <td>{{ d.symbol }}</td>
        <td>{{ d.get_type_display }}</td>
        <td>{{ d.pay_date|date:"M d, Y" }}</td>
        <td>{{ d.shares_held|floatformat:0 }}</td>
        <td>{{ d.amount_cad|floatformat:2 }}</td>
        <td class="text-success text-end">{{ d.total_cad|floatformat:2 }}</td>
      </tr>
      {% endfor %}
    </tbody>
    <tfoot>
      <tr class="table-light">
        <td colspan="5" class="text-end"><strong>Total Expected (CAD):</strong></td>
        <td class="text-success text-end">
          <strong>{{ upcoming_dividends|sum:'total_cad'|floatformat:2 }}</strong>
        </td>
      </tr>
    </tfoot>
  </table>
</div>
{% endif %}
```

Note: The `sum` template filter above is a custom filter. If not available, compute the total in the view:

```python
total_upcoming_cad = sum(d['total_cad'] for d in upcoming_dividends)
```

### 5. Tests — `core/tests/test_dividend_schedule.py` (new file)

```python
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal
from datetime import date, timedelta
from core.models import Account, BrokerStatement, Transaction, DividendSchedule
from unittest.mock import patch


class DividendScheduleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('testuser', 'test@test.com', 'testpass')
        self.account = Account.objects.create(
            account_id='DIVTEST', name='Dividend Test', broker='IB',
            currency='USD',
        )
        self.statement = BrokerStatement.objects.create(
            filename='test.csv', source='IB', broker='IB',
        )
        # Create holdings of 100 shares AAPL
        Transaction.objects.create(
            statement=self.statement, account=self.account,
            date='2024-01-15', type='BUY', symbol='AAPL',
            quantity=Decimal('100'), price=Decimal('150.00'),
            amount=Decimal('-15000.00'), currency='USD',
            amount_cad=Decimal('-20000.00'), exchange_rate_cad=Decimal('1.333'),
            tx_hash='div_test_aapl_buy',
        )
        # Create a dividend schedule 10 days from now
        self.schedule = DividendSchedule.objects.create(
            symbol='AAPL',
            pay_date=date.today() + timedelta(days=10),
            amount_per_share=Decimal('0.96'),
            currency='USD',
            exchange_rate_cad=Decimal('1.35'),
            type='DIV',
        )
        self.client.login(username='testuser', password='testpass')

    def test_upcoming_dividends_includes_schedule(self):
        """Dashboard shows upcoming dividends when schedules exist."""
        with patch('core.views.fetch_last_prices', return_value={
            'AAPL': Decimal('180.00'),
            'USDCAD=X': Decimal('1.35'),
        }):
            response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upcoming Dividends')
        self.assertContains(response, 'AAPL')

    def test_upcoming_dividends_excludes_past_schedules(self):
        """Past dividend schedules are not shown."""
        past_schedule = DividendSchedule.objects.create(
            symbol='MSFT',
            pay_date=date.today() - timedelta(days=5),
            amount_per_share=Decimal('0.75'),
            type='DIV',
        )
        with patch('core.views.fetch_last_prices', return_value={
            'AAPL': Decimal('180.00'),
            'USDCAD=X': Decimal('1.35'),
        }):
            response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'AAPL')
        self.assertNotContains(response, 'MSFT')
```

---

## Constraints

- The widget is optional — hidden when no schedules exist
- If Option A (new model) is chosen, a migration is required
- If Option B (no new model), skip this spec or implement as a manual notes field
- Never raise unhandled exceptions
- No new Python packages required
- Follow PEP 8. Add type hints.
- Do NOT push to git — see Manual Steps.

---

## Manual Steps Required

- [ ] `python manage.py makemigrations core` (if using DividendSchedule model)
- [ ] `python manage.py migrate`
- [ ] `python manage.py test core --settings=portfolio_manager.settings_test`
- [ ] Manual browser test: verify upcoming dividends appear on dashboard
- [ ] `git add -A && git commit -m "feat: upcoming dividends tracker widget on dashboard"`
- [ ] `git push origin feature_misc_outstanding_items`

---

## Human Approval Required

Human approval required before any git push and any migration.
