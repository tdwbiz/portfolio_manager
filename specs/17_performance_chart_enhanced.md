# Feature Spec: Performance Chart Enhancements

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/performance-chart-enhanced

---

## Context

Specs 01–14 are complete. Spec 11 delivered the initial portfolio performance chart at
`/portfolio/chart/` — a single monthly line chart showing total portfolio market value
over time, rendered with Chart.js and fed by a JSON endpoint at `/portfolio/chart/data/`.

The chart is currently a bare-minimum implementation:
- Single line (total market value only)
- No cost basis line
- No per-account or per-category breakdown
- No benchmark comparison
- No date range selector
- No chart type options
- Monthly frequency only
- No dividend/income overlay

`core/utils.py` has `compute_portfolio_value_series(freq='monthly')` which already
fetches historical prices from yfinance and builds the monthly series.

This spec adds controls and multiple data series to turn the chart into a genuinely
useful performance visualisation. Spec 15 (account categories) is not a hard dependency
but is assumed merged so that `account.category` is available.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager
- Frontend: Chart.js (CDN, already loaded in portfolio_chart.html)

---

## Requirements

### 1. Extend `compute_portfolio_value_series()` in `core/utils.py`

The existing function signature is `compute_portfolio_value_series(freq='monthly')`.
Extend it to accept two additional optional parameters:

```python
def compute_portfolio_value_series(
    freq: str = 'monthly',
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
```

- `date_from` and `date_to` are ISO date strings (`'YYYY-MM-DD'`). When provided, clip
  the generated date range so that only dates within the window are included in the output.
- If `date_from` is earlier than the first transaction date, use the first transaction
  date instead.
- If `date_to` is later than today, use today.
- No change to the return format: `[{'date': 'YYYY-MM-DD', 'market_value_cad': Decimal}]`.
- Existing callers that pass no arguments must continue to work unchanged.

### 2. Add `compute_cost_basis_series()` in `core/utils.py`

```python
def compute_cost_basis_series(
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Compute total invested cost basis over time (monthly series).

    For each monthly date point, sum the amount_cad of all BUY transactions
    (absolute value) up to and including that date, minus the cost of all
    sold shares (approximated as average cost at time of sale, same as the
    avg-cost method in compute_realised_pnl).

    Returns a list of dicts: [{'date': 'YYYY-MM-DD', 'cost_basis_cad': Decimal}]
    Uses the same monthly date grid as compute_portfolio_value_series.
    """
```

- Must use the same monthly date generation logic as `compute_portfolio_value_series`.
- Does NOT call yfinance — cost basis is computed entirely from transaction records.
- Respects the same `date_from` / `date_to` clipping rules.
- Cache the result under key `'cost_basis_series'` for `settings.HISTORICAL_PRICE_CACHE_TIMEOUT` seconds.

### 3. Add `compute_income_series()` in `core/utils.py`

```python
def compute_income_series(
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Compute monthly income (dividends, distributions, interest) over time.

    For each calendar month, sums amount_cad of transactions with type in
    ('DIV', 'DIST', 'INT'). Returns each month as a separate bar value.

    Returns a list of dicts: [{'date': 'YYYY-MM-DD', 'income_cad': Decimal}]
    where 'date' is the last day of the month (matching other series).
    """
```

- Does NOT call yfinance.
- Respects `date_from` / `date_to` clipping.

### 4. Add `compute_series_by_account()` in `core/utils.py`

```python
def compute_series_by_account(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, list[dict]]:
    """Compute monthly market value series for each account independently.

    Returns a dict keyed by account_id, each value being a list of
    {'date': 'YYYY-MM-DD', 'market_value_cad': Decimal} dicts.
    Uses the same historical price data as compute_portfolio_value_series
    (served from cache if already fetched).
    """
```

- Reuses the cached historical prices DataFrame from `compute_portfolio_value_series`
  (same cache key `'hist_prices'`).
- Only includes accounts that have at least one BUY transaction.

### 5. Update `portfolio_chart_data` view in `core/views.py`

Replace the current simple view with one that accepts query parameters and returns
multiple datasets:

**Request parameters (all optional):**
- `date_from` — ISO date string, start of range
- `date_to` — ISO date string, end of range
- `freq` — `'monthly'` (default) or `'weekly'`
- `series` — comma-separated list of series to include. Valid values:
  - `market_value` (always included, default)
  - `cost_basis`
  - `income`
  - `benchmark`
  - `by_account`
- `benchmark` — ticker symbol to use as benchmark (default: `'^GSPC'`; S&P 500)

**Response format:**
```json
{
  "labels": ["2024-01-31", "2024-02-29", ...],
  "datasets": [
    {
      "id": "market_value",
      "label": "Portfolio Market Value (CAD)",
      "data": [125000.00, 127500.00, ...],
      "type": "line",
      "yAxisID": "y"
    },
    {
      "id": "cost_basis",
      "label": "Cost Basis (CAD)",
      "data": [110000.00, 112000.00, ...],
      "type": "line",
      "yAxisID": "y"
    },
    {
      "id": "income",
      "label": "Monthly Income (CAD)",
      "data": [450.00, 320.00, ...],
      "type": "bar",
      "yAxisID": "y2"
    },
    {
      "id": "benchmark_GSPC",
      "label": "S&P 500 (rebased)",
      "data": [100.0, 102.3, ...],
      "type": "line",
      "yAxisID": "y3"
    }
  ]
}
```

**Benchmark logic:**
- Fetch the benchmark ticker historical prices from yfinance (same `yf.download` call
  pattern as `compute_portfolio_value_series`).
- Rebase the benchmark to 100 at the first date in the portfolio's series so it is
  directly comparable regardless of price scale.
- Cache the benchmark series under key `f'benchmark_{benchmark_ticker}'` for
  `settings.HISTORICAL_PRICE_CACHE_TIMEOUT` seconds.

**By-account logic:**
- When `by_account` is in the series list, call `compute_series_by_account()` and add
  one dataset per account, using the account_id as the label.

**Validation:**
- `date_from` and `date_to` must be valid ISO dates; return a 400 JSON error if not.
- `benchmark` ticker must be a non-empty string of max 20 characters; strip non-alphanumeric
  characters except `.`, `^`, `=` before passing to yfinance.

### 6. Update `core/templates/core/portfolio_chart.html`

Replace the current minimal template with a full interactive chart page.

**Controls panel** (above the chart):
- Date range buttons: `1M | 3M | 6M | YTD | 1Y | All` — clicking each sets `date_from`
  and `date_to` query params and reloads the chart data.
- Custom date range: two date inputs (`date_from`, `date_to`) with an "Apply" button.
- Series toggles (checkboxes): `Cost Basis`, `Income`, `Benchmark`.
  - All unchecked by default. Market Value is always shown.
- `By Account` toggle checkbox — when checked, replaces the single market value line
  with per-account lines.
- Benchmark ticker input: text field pre-filled with `^GSPC`. Only visible when the
  `Benchmark` checkbox is checked.
- Chart type selector: `Line | Area | Bar` radio buttons.

**Chart canvas:**
- A single `<canvas id="portfolioChart">`.
- Chart.js chart instance created in JavaScript.
- When `Area` type is selected, set `fill: true` on the market_value dataset.
- When `Bar` type is selected, change the market_value dataset type to `'bar'`.
- The income dataset (if enabled) always renders as `type: 'bar'` on a secondary Y-axis
  (`yAxisID: 'y2'`).
- The benchmark dataset (if enabled) renders on a third Y-axis (`yAxisID: 'y3'`) with
  label "Benchmark (rebased to 100)".

**JavaScript behaviour:**
- On page load, fetch with default params (no date filter, market_value only).
- On any control change, re-fetch from the JSON endpoint with updated params and
  re-render the chart in place (destroy previous chart instance before creating a new one).
- Show a spinner/loading indicator while fetching.
- On error, show an inline error message without destroying the previous chart.

**Colours** (use CSS variables where possible, fall back to these defaults):
- Market value: `var(--primary-color, #417690)`
- Cost basis: `var(--secondary-color, #447e9b)`
- Income bars: `var(--success-color, #28a745)` at 60% opacity
- Benchmark: `#f4a261`
- Per-account lines: cycle through `['#4e79a7','#f28e2b','#e15759','#76b7b2','#59a14f','#edc948']`

### 7. Tests — `core/tests/test_performance_chart.py`

Extend the existing test class with the following new tests:

- `test_chart_data_with_date_range`: mock `compute_portfolio_value_series` to return
  two data points; GET `/portfolio/chart/data/?date_from=2024-01-01&date_to=2024-06-30`;
  assert 200 and that `labels` has 2 entries and `datasets` has at least 1 entry with
  `id='market_value'`.
- `test_chart_data_with_cost_basis`: mock both `compute_portfolio_value_series` and
  `compute_cost_basis_series`; GET with `series=market_value,cost_basis`; assert response
  `datasets` contains an entry with `id='cost_basis'`.
- `test_chart_data_with_income`: mock `compute_income_series`; GET with `series=market_value,income`;
  assert response contains a dataset with `id='income'` and `type='bar'`.
- `test_chart_data_invalid_date`: GET with `date_from=not-a-date`; assert 400 response.
- `test_chart_data_benchmark_sanitise`: GET with `benchmark=../../etc/passwd`; assert
  the ticker is sanitised before yfinance is called (assert yfinance mock was called with
  a safe string, or assert 400 if the value is entirely invalid after sanitisation).
- `test_chart_page_has_controls`: GET `/portfolio/chart/`; assert response contains
  `"Cost Basis"` and `"Benchmark"` (control label text).

Run with: `python manage.py test core --settings=portfolio_manager.settings_test`

---

## Constraints

- Do NOT change the existing `compute_portfolio_value_series` return format — only
  extend its signature with new optional parameters.
- Do NOT remove or rename the existing `portfolio_chart_data` URL.
- The existing chart page URL `/portfolio/chart/` must still work with no query params
  (default behaviour unchanged).
- All yfinance calls must be wrapped in try/except. Any single symbol failure must not
  crash the entire endpoint.
- Benchmark ticker must be sanitised before being passed to yfinance (security requirement).
- No new Python pip packages required (yfinance, Chart.js already in use).
- Follow PEP 8. Add type hints to all new functions.
- Do NOT push to git — see Manual Steps.

---

## Manual Steps Required

- [ ] `python manage.py test core --settings=portfolio_manager.settings_test`
- [ ] Manual browser test: verify all chart controls render and respond correctly
- [ ] Manual browser test: verify benchmark overlay does not break chart when yfinance
      is slow or unavailable
- [ ] `git add -A && git commit -m "feat: enhanced performance chart with multiple series, date range, and benchmark"`
- [ ] `git push origin feature/performance-chart-enhanced`

---

## Human Approval Required

Human approval required before any git push.
