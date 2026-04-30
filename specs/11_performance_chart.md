# Feature Spec: Portfolio Performance Chart

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/performance-chart

---

## Context

Specs 01–07 are complete. The portfolio summary page (Spec 07) shows current market
value using yfinance live prices. This spec adds a performance chart showing portfolio
market value over time as a monthly line chart using Chart.js and yfinance historical
price data.

`core/utils.py` already has `fetch_last_prices()` using `yf.Tickers`. Historical data
will use `yf.download()` from the same library (already installed).

The chart computation is expensive (fetches multi-year price history for each held
symbol), so results are cached in Django's in-memory cache for 1 hour.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager
- Frontend: Chart.js loaded from CDN (no npm)

---

## Requirements

1. **New function `compute_portfolio_value_series(freq='monthly')` in `core/utils.py`**
   - Fetches all BUY/SELL transactions ordered by `date` ascending.
   - Determines the date range: `start_date` = date of first transaction, `end_date` = today.
   - Generates a list of monthly period-end dates between `start_date` and `end_date` (use the last day of each month).
   - For each monthly date point, computes holdings by replaying all transactions up to and including that date:
     - Maintains a per-symbol share count (BUY adds, SELL subtracts).
     - Collects the set of symbols with `shares > 0` at that date.
   - Fetches historical closing prices:
     - Use `yf.download(tickers, start=start_date, end=end_date + 1 day, auto_adjust=True, progress=False)['Close']` to get a DataFrame of daily closes for all unique symbols ever held.
     - Cache the raw DataFrame under key `'hist_prices'` for 3600 seconds. Check cache before fetching.
   - For each monthly date point and each held symbol:
     - Look up the closing price from the DataFrame for that date (use `.asof(date)` or the nearest prior date).
     - For USD-denominated symbols (currency != 'CAD'), multiply by the USDCAD rate on that date (look up `'USDCAD=X'` from the same DataFrame).
     - Sum market values across all held symbols.
   - Returns a list of dicts: `[{'date': 'YYYY-MM-DD', 'market_value_cad': Decimal}, ...]`
   - If there are no transactions, return `[]`.
   - Any exception during price lookup for a single symbol must be caught silently; treat that symbol's contribution as 0 for that date point.
   - Add `'USDCAD=X'` to the tickers list passed to `yf.download`.

2. **New JSON endpoint view `portfolio_chart_data` in `core/views.py`**
   - URL: `/portfolio/chart-data/`
   - Calls `compute_portfolio_value_series()`.
   - Returns `JsonResponse({'labels': [...dates...], 'values': [...float values...]})`.
   - Values are rounded to 2 decimal places and cast to `float` (not Decimal, which is not JSON-serialisable).
   - Import `JsonResponse` from `django.http`.

3. **New view `portfolio_chart` in `core/views.py`**
   - URL: `/portfolio/chart/`
   - Renders `core/templates/core/portfolio_chart.html` with no context variables needed (data fetched via JS).

4. **New URLs in `core/urls.py`**
   - Add after `portfolio/`:
     ```python
     path('portfolio/chart/', views.portfolio_chart, name='portfolio_chart'),
     path('portfolio/chart-data/', views.portfolio_chart_data, name='portfolio_chart_data'),
     ```
   - **Important:** `portfolio/chart/` and `portfolio/chart-data/` must be defined BEFORE `portfolio/` would cause a conflict — since they are sub-paths, add them immediately after `portfolio/` in the urlpatterns list.

5. **Add nav link in `core/templates/core/base.html`**
   - Add after the "Portfolio" link:
     ```html
     <a href="{% url 'portfolio_chart' %}">Performance</a>
     ```

6. **New template `core/templates/core/portfolio_chart.html`**
   - Extends `core/base.html`.
   - Loads Chart.js from CDN:
     ```html
     <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
     ```
   - Contains a `<canvas id="portfolioChart">`.
   - JavaScript that:
     - Fetches `{% url 'portfolio_chart_data' %}` using `fetch()`.
     - On success, renders a Chart.js line chart with:
       - `labels`: the date array
       - `datasets`: one dataset "Market Value (CAD)", `borderColor: '#4e79a7'`, `fill: false`
     - While loading, shows a `<p id="loading">Loading chart data…</p>` which is hidden once chart renders.
     - On error, shows `<p>Failed to load chart data.</p>`.

7. **Tests in `core/tests/test_performance_chart.py`**
   - `TestCase` class `PerformanceChartTests`.
   - `setUp`: create Account, BrokerStatement, one BUY transaction for AAPL dated 2024-01-15, `tx_hash='perf_buy1'`, `commission=Decimal('0')`.
   - `test_chart_page_loads`: GET `/portfolio/chart/` returns 200.
   - `test_chart_data_endpoint`: mock `compute_portfolio_value_series` to return
     `[{'date': '2024-01-31', 'market_value_cad': Decimal('2500.00')}]`.
     GET `/portfolio/chart-data/` returns 200 and JSON `{'labels': ['2024-01-31'], 'values': [2500.0]}`.
   - `test_chart_data_empty`: mock `compute_portfolio_value_series` to return `[]`.
     GET returns 200 with `{'labels': [], 'values': []}`.
   - Mock pattern: `patch('core.views.compute_portfolio_value_series', return_value=[...])`

---

## Constraints

- Do NOT change `core/models.py`.
- Do NOT create any migrations.
- `compute_positions()`, `fetch_last_prices()`, and `enrich_positions_with_prices()` must remain unchanged.
- All yfinance calls in `compute_portfolio_value_series` must be wrapped in try/except.
- No Chart.js in pip/requirements — load from CDN only.
- Run `python manage.py test core --settings=portfolio_manager.settings_test`. All tests must pass.
- After all tests pass: `git add -A && git commit -m "feat: portfolio performance chart with historical market value"`

---

## Manual Steps Required

- [ ] Review and push feature branch: `git push origin feature/performance-chart`
- [ ] Merge to dev after review.
