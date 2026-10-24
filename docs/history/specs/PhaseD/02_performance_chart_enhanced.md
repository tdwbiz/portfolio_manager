# Feature Spec: Performance Chart Enhancements

**Spec:** D2a — Spec 17
**Project name:** portfolio_manager
**Customer:** tdwdev
**Branch:** `feature_misc_outstanding_items`

---

## Goal

Extend the portfolio performance chart at `/portfolio/chart/` with cost basis overlay, income bars, per-account lines, benchmark comparison, date range selector, chart type options, and interactive controls — turning a bare-minimum single line into a full performance visualisation.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager
- Frontend: Chart.js (CDN, already loaded in base.html)

---

## Requirements

### 1. Extend `compute_portfolio_value_series()` in `core/utils.py`

Current signature: `compute_portfolio_value_series(freq='monthly')`

Extend to accept optional parameters:
```python
def compute_portfolio_value_series(
    freq: str = 'monthly',
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
```

- `date_from` and `date_to` are ISO date strings (`'YYYY-MM-DD'`). When provided, clip the generated date range so only dates within the window are included.
- If `date_from` is earlier than the first transaction date, use the first transaction date instead.
- If `date_to` is later than today, use today.
- No change to the return format: `[{'date': 'YYYY-MM-DD', 'market_value_cad': Decimal}]`
- Existing callers with no arguments must continue to work unchanged.

### 2. Add `compute_cost_basis_series()` in `core/utils.py`

```python
def compute_cost_basis_series(
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Compute total invested cost basis over time (monthly series).

    For each monthly date point, sum abs(amount_cad) of all BUY transactions
    up to and including that date, minus the cost of all sold shares
    (approximated as average cost at time of sale).

    Returns [{'date': 'YYYY-MM-DD', 'cost_basis_cad': Decimal}]
    Uses the same monthly date grid as compute_portfolio_value_series.
    """
```

- Must use the same monthly date generation logic as `compute_portfolio_value_series`
- Does NOT call yfinance — cost basis is computed entirely from transaction records
- Respects `date_from` / `date_to` clipping rules
- Cache under key `'cost_basis_series'` for `settings.HISTORICAL_PRICE_CACHE_TIMEOUT` seconds

### 3. Add `compute_income_series()` in `core/utils.py`

```python
def compute_income_series(
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Compute monthly income (dividends, distributions, interest) over time.

    For each calendar month, sums amount_cad of transactions with type
    in ('DIV', 'DIST', 'INT'). Returns each month as a separate bar value.

    Returns [{'date': 'YYYY-MM-DD', 'income_cad': Decimal}]
    """
```

- Does NOT call yfinance
- Respects `date_from` / `date_to` clipping

### 4. Add `compute_series_by_account()` in `core/utils.py`

```python
def compute_series_by_account(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, list[dict]]:
    """Compute monthly market value series for each account independently.

    Returns dict keyed by account_id, each value being a list of
    {'date': 'YYYY-MM-DD', 'market_value_cad': Decimal} dicts.
    Reuses the cached historical prices DataFrame from compute_portfolio_value_series.
    Only includes accounts with at least one BUY transaction.
    """
```

### 5. Update `portfolio_chart_data` view in `core/views.py`

Replace the current simple view with one that accepts query parameters and returns multiple datasets.

**Request parameters (all optional):**
- `date_from` — ISO date string, start of range
- `date_to` — ISO date string, end of range
- `freq` — `'monthly'` (default) or `'weekly'`
- `series` — comma-separated list: `market_value`, `cost_basis`, `income`, `benchmark`, `by_account`
- `benchmark` — ticker symbol (default: `'^GSPC'`; S&P 500)

**Validation:**
- `date_from` and `date_to` must be valid ISO dates; return 400 JSON error if not. Use existing `_validate_date_param()` from views.py.
- `benchmark` ticker: strip, max 20 characters, sanitise non-alphanumeric except `.`, `^`, `=`.

**Benchmark logic:**
- Fetch benchmark historical prices from yfinance using `yf.download()`
- Rebase to 100 at the first date in the portfolio's series
- Cache under key `f'benchmark_{benchmark_ticker}'` for `settings.HISTORICAL_PRICE_CACHE_TIMEOUT` seconds

**Response format:**
```json
{
  "labels": ["2024-01-31", "2024-02-29", ...],
  "datasets": [
    {"id": "market_value", "label": "Market Value (CAD)", "data": [...], "type": "line", "yAxisID": "y"},
    {"id": "cost_basis", "label": "Cost Basis (CAD)", "data": [...], "type": "line", "yAxisID": "y"},
    {"id": "income", "label": "Monthly Income (CAD)", "data": [...], "type": "bar", "yAxisID": "y2"},
    {"id": "benchmark_GSPC", "label": "S&P 500 (rebased)", "data": [...], "type": "line", "yAxisID": "y3"}
  ]
}
```

When `by_account` is in series: add one dataset per account, using account_id as label.

### 6. Update `core/templates/core/portfolio_chart.html`

Replace the current minimal template with an interactive chart page.

**Controls panel** (above the chart):

- **Date range buttons:** `1M | 3M | 6M | YTD | 1Y | All` — each sets `date_from`/`date_to` query params and reloads chart
- **Custom date range:** two date inputs + "Apply" button
- **Series toggles** (checkboxes): `Cost Basis`, `Income`, `Benchmark` — all unchecked by default (Market Value always shown)
- **By Account** toggle checkbox — when checked, replaces single line with per-account lines
- **Benchmark ticker** input: text field pre-filled with `^GSPC`, only visible when Benchmark checkbox is checked
- **Chart type selector:** `Line | Area | Bar` radio buttons

**Chart canvas:**
- Single `<canvas id="portfolioChart">`
- Chart.js instance created in JavaScript
- When Area selected: `fill: true` on market_value dataset
- When Bar selected: market_value dataset type becomes `'bar'`
- Income dataset (if enabled): `type: 'bar'` on secondary Y-axis (`yAxisID: 'y2'`)
- Benchmark (if enabled): on third Y-axis (`yAxisID: 'y3'`) with label "Benchmark (rebased to 100)"

**JavaScript behaviour:**
- On page load: fetch with default params (market_value only, no date filter)
- On any control change: re-fetch from JSON endpoint with updated params and re-render
- Destroy previous chart instance before creating a new one
- Show loading indicator while fetching
- On error: show inline error message without destroying previous chart

**Colours:**
- Market value: `#417690`
- Cost basis: `#447e9b`
- Income bars: `#28a745` at 60% opacity
- Benchmark: `#f4a261`
- Per-account lines: cycle through `['#4e79a7','#f28e2b','#e15759','#76b7b2','#59a14f','#edc948']`

### 7. Tests — `core/tests/test_performance_chart.py`

Extend the existing `PerformanceChartTests` class:

```python
def test_chart_data_with_date_range(self):
    with patch('core.views.compute_portfolio_value_series',
               return_value=[{'date': '2024-01-31', 'market_value_cad': Decimal('2500.00')},
                             {'date': '2024-02-29', 'market_value_cad': Decimal('2600.00')}]):
        response = self.client.get(
            reverse('portfolio_chart_data') + '?date_from=2024-01-01&date_to=2024-06-30')
    self.assertEqual(response.status_code, 200)
    data = json.loads(response.content)
    self.assertEqual(len(data['labels']), 2)
    self.assertTrue(any(d['id'] == 'market_value' for d in data['datasets']))

def test_chart_data_with_cost_basis(self):
    with patch('core.views.compute_portfolio_value_series', return_value=[
        {'date': '2024-01-31', 'market_value_cad': Decimal('2500.00')}]):
        with patch('core.views.compute_cost_basis_series', return_value=[
            {'date': '2024-01-31', 'cost_basis_cad': Decimal('2300.00')}]):
            response = self.client.get(
                reverse('portfolio_chart_data') + '?series=market_value,cost_basis')
    self.assertEqual(response.status_code, 200)
    data = json.loads(response.content)
    self.assertTrue(any(d['id'] == 'cost_basis' for d in data['datasets']))

def test_chart_data_with_income(self):
    with patch('core.views.compute_portfolio_value_series', return_value=[
        {'date': '2024-01-31', 'market_value_cad': Decimal('2500.00')}]):
        with patch('core.views.compute_income_series', return_value=[
            {'date': '2024-01-31', 'income_cad': Decimal('450.00')}]):
            response = self.client.get(
                reverse('portfolio_chart_data') + '?series=market_value,income')
    data = json.loads(response.content)
    income_ds = [d for d in data['datasets'] if d['id'] == 'income'][0]
    self.assertEqual(income_ds['type'], 'bar')

def test_chart_data_invalid_date(self):
    response = self.client.get(
        reverse('portfolio_chart_data') + '?date_from=not-a-date')
    self.assertEqual(response.status_code, 400)

def test_chart_data_benchmark_sanitise(self):
    # Path traversal attempt should be sanitised
    response = self.client.get(
        reverse('portfolio_chart_data') + '?benchmark=../../etc/passwd')
    # Should return 400 or sanitise before yfinance call
    self.assertEqual(response.status_code, 400)

def test_chart_page_has_controls(self):
    response = self.client.get(reverse('portfolio_chart'))
    self.assertContains(response, 'Cost Basis')
    self.assertContains(response, 'Benchmark')
```

---

## Constraints

- Do NOT change the existing `compute_portfolio_value_series` return format — only extend its signature with new optional parameters
- Do NOT remove or rename the existing `portfolio_chart_data` URL
- The existing `/portfolio/chart/` must still work with no query params (default behaviour unchanged)
- All yfinance calls must be wrapped in try/except
- Benchmark ticker must be sanitised before being passed to yfinance (security requirement)
- No new Python pip packages required (yfinance, Chart.js already in use)
- Follow PEP 8. Add type hints to all new functions.
- Do NOT push to git — see Manual Steps.

---

## Manual Steps Required

- [ ] `python manage.py test core --settings=portfolio_manager.settings_test`
- [ ] Manual browser test: verify all chart controls render and respond correctly
- [ ] Manual browser test: verify benchmark overlay does not break chart when yfinance is slow or unavailable
- [ ] `git add -A && git commit -m "feat: enhanced performance chart with multiple series, date range, and benchmark"`
- [ ] `git push origin feature_misc_outstanding_items`

---

## Human Approval Required

Human approval required before any git push.
