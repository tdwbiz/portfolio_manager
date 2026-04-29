Project name: portfolio_manager
Project path: C:\AI-DevTeam\projects\portfolio_manager

# Spec: Portfolio Summary with Live Prices & Unrealised P&L

## Scope Note
This spec covers Phase 1.2 (portfolio summary page) and pulls in Phase 2.1 (price feed)
at the same time. The rationale: `yfinance` is free, requires no API key, and takes a
single `pip install`. Building the page without prices would be pointless — the unrealised
P&L column is the whole point. Phase 2.1 on the roadmap can be crossed off after this.

---

## Goal
Add a dedicated **Portfolio** page at `/portfolio/` that shows all open positions enriched
with the latest market price, market value (CAD), unrealised P&L (CAD), and unrealised
P&L %. Prices come from `yfinance` and are cached in Django's default in-memory cache for
15 minutes. The page degrades gracefully when a price is unavailable.

---

## Files to Modify

### 1. `requirements.txt`
Append:
```
yfinance>=0.2
```

### 2. `core/utils.py`
Add two new functions **below** the existing `compute_positions()`. Do not change
`compute_positions()`.

#### `fetch_last_prices(symbols: list[str], cache_timeout: int = 900) -> dict[str, Decimal]`
- `symbols` is a list of ticker strings, e.g. `['AAPL', 'MSFT', 'VFV.TO']`.
- Also always add `'USDCAD=X'` to the fetch list (needed for FX conversion).
- Check Django cache first (`cache.get(f'price_{sym}')`). Only fetch symbols that are not
  already cached.
- Use `yf.Tickers(' '.join(to_fetch))` to batch-fetch. For each ticker, read
  `ticker.fast_info.last_price`. Skip silently on any exception.
- Store each fetched price in cache: `cache.set(f'price_{sym}', price, cache_timeout)`.
- Return a dict `{symbol: Decimal(price)}`. Missing symbols are absent from the dict.
- Import: `import yfinance as yf` and `from django.core.cache import cache` at the top of
  the file.

#### `enrich_positions_with_prices(positions: list[dict], prices: dict) -> list[dict]`
- Takes the list returned by `compute_positions()` and the dict returned by
  `fetch_last_prices()`.
- For each position, compute:
  - `last_price`: `prices.get(position['symbol'])` — `None` if missing.
  - `usdcad`: `prices.get('USDCAD=X', Decimal('1'))`.
  - `fx`: `usdcad` if `position['currency'] != 'CAD'` else `Decimal('1')`.
  - `market_value_cad`: `last_price * position['total_shares'] * fx` if `last_price` else
    `None`.
  - `unrealised_pnl_cad`: `market_value_cad - position['total_cost_cad']` if
    `market_value_cad is not None` else `None`.
  - `unrealised_pnl_pct`: `(unrealised_pnl_cad / position['total_cost_cad'] * 100)` if
    `unrealised_pnl_cad is not None and position['total_cost_cad'] != 0` else `None`.
- Add these four keys to each position dict.
- Return the enriched list.

### 3. `core/views.py`
Add a new view `portfolio_summary`:

```python
def portfolio_summary(request):
    positions = compute_positions()
    symbols = [p['symbol'] for p in positions if p['symbol']]
    prices = fetch_last_prices(symbols)
    positions = enrich_positions_with_prices(positions, prices)
    positions.sort(key=lambda p: (p['broker'], p['account_name'], p['symbol'] or ''))

    total_cost_cad = sum(p['total_cost_cad'] for p in positions)
    total_market_value_cad = sum(
        p['market_value_cad'] for p in positions if p.get('market_value_cad') is not None
    )
    total_unrealised_pnl_cad = total_market_value_cad - sum(
        p['total_cost_cad'] for p in positions if p.get('market_value_cad') is not None
    )

    return render(request, 'core/portfolio.html', {
        'positions': positions,
        'total_cost_cad': total_cost_cad,
        'total_market_value_cad': total_market_value_cad,
        'total_unrealised_pnl_cad': total_unrealised_pnl_cad,
    })
```

Import `fetch_last_prices` and `enrich_positions_with_prices` from `core.utils` at the
top of `views.py`.

### 4. `core/urls.py`
Add before the `accounts/` paths:
```python
path('portfolio/', views.portfolio_summary, name='portfolio_summary'),
```

### 5. `core/templates/core/base.html`
Add a "Portfolio" nav link after the existing "Dashboard" link:
```html
<a href="{% url 'portfolio_summary' %}">Portfolio</a>
```

---

## Files to Create

### 6. `core/templates/core/portfolio.html`
Extends `core/base.html`. Structure:

**Header section:**
```html
<h1>Portfolio Summary</h1>
<p>
  Total Cost (CAD): <strong>{{ total_cost_cad|floatformat:2 }}</strong> &nbsp;|&nbsp;
  Market Value (CAD): <strong>{{ total_market_value_cad|floatformat:2 }}</strong> &nbsp;|&nbsp;
  Unrealised P&L (CAD): <strong>{{ total_unrealised_pnl_cad|floatformat:2 }}</strong>
</p>
```

**Positions table columns:** Broker | Account | Symbol | Shares | Avg Cost (CAD) |
Total Cost (CAD) | Last Price | Market Value (CAD) | Unrealised P&L (CAD) | P&L %

For cells where the value is `None`, display `—` (an em-dash).

For the P&L columns, use inline `style="color:green"` for positive values and
`style="color:red"` for negative.

Use `|floatformat:2` for all Decimal columns.

### 7. `core/tests/test_portfolio_summary.py`
Write a `TestCase` class `PortfolioSummaryTests`.

**setUp:**
```python
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from core.models import Account, BrokerStatement, Transaction

# create one account, one statement, two BUY transactions for different symbols
self.account = Account.objects.create(
    account_id='PORT001', name='Portfolio Test', broker='IB', currency='USD',
    starting_balance=Decimal('0'),
)
self.statement = BrokerStatement.objects.create(
    filename='test.csv', source='IB', broker='IB',
)
self.tx1 = Transaction.objects.create(
    statement=self.statement, account=self.account,
    date='2024-01-15', type='BUY', symbol='AAPL',
    quantity=Decimal('10'), price=Decimal('150.00'),
    amount=Decimal('-1500.00'), currency='USD',
    amount_cad=Decimal('-2000.00'), exchange_rate_cad=Decimal('1.333'),
    tx_hash='port_test_aapl_buy',
)
self.tx2 = Transaction.objects.create(
    statement=self.statement, account=self.account,
    date='2024-02-01', type='BUY', symbol='VFV.TO',
    quantity=Decimal('5'), price=Decimal('100.00'),
    amount=Decimal('-500.00'), currency='CAD',
    amount_cad=Decimal('-500.00'), exchange_rate_cad=Decimal('1.0'),
    tx_hash='port_test_vfv_buy',
)
```

**Tests:**

- `test_portfolio_page_loads` — with prices mocked to return `{}`, GET `/portfolio/`
  returns 200.

- `test_portfolio_shows_positions` — with prices mocked to return `{}`, response contains
  `'AAPL'` and `'VFV.TO'`.

- `test_portfolio_unrealised_pnl_calculation` — mock `fetch_last_prices` to return
  `{'AAPL': Decimal('180.00'), 'VFV.TO': Decimal('110.00'), 'USDCAD=X': Decimal('1.35')}`.
  GET `/portfolio/`. Assert response contains the string `'2430.00'`
  (AAPL: 10 × 180 × 1.35 = 2430 CAD market value).

- `test_portfolio_missing_price_shows_dash` — mock `fetch_last_prices` to return `{}`.
  Response contains `—` (em-dash, the N/A indicator).

- `test_portfolio_sold_out_position_excluded` — add a matching SELL for all AAPL shares
  (quantity 10), mock prices to `{}`, assert `'AAPL'` is NOT in response content.

**Mocking pattern:**
```python
with patch('core.views.fetch_last_prices', return_value={...}):
    response = self.client.get(reverse('portfolio_summary'))
```

---

## Constraints

- Do NOT change `core/models.py`.
- Do NOT create any migrations.
- `compute_positions()` must remain unchanged (other views depend on it).
- Price fetch failures must never raise an unhandled exception — always degrade to `None`.
- `fetch_last_prices` must be importable and testable in isolation.
- Run `python manage.py test core --settings=portfolio_manager.settings_test` to verify.
  All tests (existing + new) must pass.
- After all tests pass: `git add -A && git commit -m "feat: portfolio summary with live prices and unrealised P&L"`
