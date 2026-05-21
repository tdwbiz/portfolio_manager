# Feature Spec: Performance Chart — Full Fix & Redesign

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/performance-chart-fixes

---

## Context

Spec 17 delivered the enhanced performance chart at `/portfolio/chart/`. The result is
currently broken and unusable:

1. **Portfolio Market Value is always 0** — yfinance price fetching fails silently.
   The exception in `compute_portfolio_value_series` is swallowed, `prices_df` ends up
   `None`, the market-value loop never executes, every data point stays at `Decimal('0')`.
   The cost-basis series (which does NOT call yfinance) continues to work, making the
   zero market value easy to miss in the JSON until you hover the chart tooltip.

2. **Cost Basis double-counts on every pass** — `compute_cost_basis_series` keeps a
   running `total_shares / total_cost` state **outside** the per-date loop but replays
   `transactions.filter(date__lte=tx_date)` on every iteration. Each date re-adds all
   transactions from the beginning. By month 3 the cost basis has been added 3×. This
   is why the orange bars in screenshot 1 dwarf everything else.

3. **`compute_series_by_account` is broken in the same two ways** — it calls
   `_generate_monthly_dates` per account with per-account `start_date`, generating
   different date grids. The resulting `data` arrays have different lengths from the
   `labels` array produced by the market-value series, so Chart.js renders nothing.

4. **Chart-type toggle does nothing useful** — the 'Bar' radio turns per-dataset
   `type` to `'bar'` but the root Chart.js config stays `type: 'line'`, and the UI
   radio buttons (Bootstrap `btn-check` inside `btn-group`) are visually broken
   (the active state does not highlight correctly).

5. **No granularity control** — the view accepts a `freq` param but JavaScript never
   sends it, and `_generate_monthly_dates` only supports monthly data regardless.
   The user wants daily / weekly / monthly / annual toggles.

6. **Chart is hard to read with all series on** — cost basis and market value fight for
   the same y-axis at very different scales, and income bars (correct) are invisible
   when cost-basis bars dominate.

This spec fixes all six issues and adds the granularity control.

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env (SQLite for tests)
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager
- Frontend: Chart.js (CDN, already loaded in portfolio_chart.html)

---

## Requirements

### 1. Replace `_generate_monthly_dates` with `_generate_date_series` in `core/utils.py`

Rename the existing helper and extend it to support four frequencies:

```python
def _generate_date_series(
    start_date: date,
    end_date: date,
    freq: str = 'monthly',
) -> list[date]:
    """Generate period-end dates between start_date and end_date (inclusive).

    Args:
        freq: One of 'daily', 'weekly', 'monthly', 'annual'.

    Returns:
        List of date objects. For monthly: last day of each month.
        For weekly: each Friday (or last trading day of week).
        For daily: every calendar day in range.
        For annual: last day of each calendar year (Dec 31).
    """
```

Implementation rules per frequency:

- **`'daily'`**: `start_date + timedelta(days=i)` for i in range, every calendar day,
  capped at `end_date`. No weekday filtering — the price lookup will handle missing
  trading days via `asof()`.
- **`'weekly'`**: advance in 7-day steps from the Friday on or after `start_date`.
  To find the first Friday: `start_date + timedelta(days=(4 - start_date.weekday()) % 7)`.
  Then step by 7 days until `<= end_date`.
- **`'monthly'`**: existing logic — last day of each calendar month. Keep identical.
- **`'annual'`**: last day of each calendar year (`date(year, 12, 31)`) from
  `start_date.year` to `end_date.year`. If `end_date < date(end_date.year, 12, 31)`,
  use `end_date` for the final year instead of Dec 31.

Update all four call sites in `utils.py` (`compute_portfolio_value_series`,
`compute_cost_basis_series`, `compute_income_series`, `compute_series_by_account`)
from `_generate_monthly_dates(start_date, end_date)` to
`_generate_date_series(start_date, end_date, freq)`.

For `compute_cost_basis_series` and `compute_income_series`, add a `freq: str = 'monthly'`
parameter and pass it through.

### 2. Fix `compute_portfolio_value_series` in `core/utils.py`

**Problem A — Silent yfinance failure, zero market values**

The existing bare `except Exception: pass` in the price-lookup inner loop swallows NaN
and lookup errors without trace. Replace the inner per-symbol loop with explicit NaN
checking and improved logging:

```python
import math

for symbol in symbols:
    try:
        if symbol not in prices_df.columns:
            logger.warning(f"Symbol {symbol!r} not in prices_df columns; skipping.")
            continue
        price_series = prices_df[symbol].dropna()
        if price_series.empty:
            logger.warning(f"No price data for {symbol!r} on {tx_date}; skipping.")
            continue
        raw_price = price_series.asof(pd.Timestamp(tx_date))
        if raw_price is None or (isinstance(raw_price, float) and math.isnan(raw_price)):
            logger.warning(f"NaN/None price for {symbol!r} on {tx_date}; skipping.")
            continue
        price = Decimal(str(raw_price))

        if symbol.endswith('.TO') or symbol.endswith('.AX') or symbol.endswith('.V'):
            # Already CAD-denominated TSX/ASX/TSXV stock
            fx_rate = Decimal('1')
        elif 'USDCAD=X' in prices_df.columns:
            raw_fx = prices_df['USDCAD=X'].dropna().asof(pd.Timestamp(tx_date))
            if raw_fx is None or (isinstance(raw_fx, float) and math.isnan(raw_fx)):
                logger.warning(f"NaN FX rate on {tx_date}; using 1.0 fallback.")
                fx_rate = Decimal('1')
            else:
                fx_rate = Decimal(str(raw_fx))
        else:
            logger.warning("USDCAD=X not in prices_df; using fx_rate=1")
            fx_rate = Decimal('1')

        market_value += price * holdings[symbol] * fx_rate
    except Exception as sym_err:
        logger.warning(f"Price lookup error for {symbol!r} on {tx_date}: {sym_err}")
```

Note: `import pandas as pd` must be added at the top of `utils.py` (required for
`pd.Timestamp`). Check if it is already imported before adding.

**Problem B — yfinance DataFrame format with multiple symbols**

With yfinance ≥ 0.2.x and a list of tickers, `yf.download()` may return a DataFrame
with a `MultiIndex` of `(field, ticker)`. The `['Close']` slice then returns a DataFrame
with ticker columns, which is correct. But with a **single ticker**, `['Close']` returns
a `Series`. Guard against this:

```python
raw_df = yf.download(
    ' '.join(symbols_with_usdcad),
    start=start_date.strftime('%Y-%m-%d'),
    end=(end_date + timedelta(days=1)).strftime('%Y-%m-%d'),
    auto_adjust=True,
    progress=False,
    group_by='ticker',   # force consistent MultiIndex format
)
# Normalise to a flat DataFrame with tickers as columns
if isinstance(raw_df.columns, pd.MultiIndex):
    prices_df = raw_df['Close'] if 'Close' in raw_df.columns.get_level_values(0) else raw_df.xs('Close', axis=1, level=0)
elif len(symbols_with_usdcad) == 1:
    # Single ticker: raw_df has simple columns; rename the 'Close' column
    prices_df = raw_df[['Close']].rename(columns={'Close': symbols_with_usdcad[0]})
else:
    prices_df = raw_df.get('Close', raw_df)
```

**Problem C — Pass `freq` into date generation**

Update the function signature:
```python
def compute_portfolio_value_series(
    freq: str = 'monthly',
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
```
Replace the `_generate_monthly_dates(start_date, end_date)` call with
`_generate_date_series(start_date, end_date, freq)`.

**Problem D — Cache key must include freq and date range**

The current cache key `'hist_prices'` ignores the freq and date params. This means
a daily fetch gets served from a monthly cache and vice versa. Update the price-data
cache key to be fixed (`'hist_prices'` is fine — it stores the raw prices, not the
series). But update the **series** cache key for `compute_portfolio_value_series`:
there is currently no caching on the series output — this is fine for correctness,
but log a warning if the result is empty after successful price fetch.

### 3. Fix `compute_cost_basis_series` in `core/utils.py`

**Bug**: running `total_shares / total_cost` state is initialised once before the
date loop but the inner queryset `transactions.filter(date__lte=tx_date)` re-replays
all transactions from the beginning on every date iteration. State accumulates N times
by the N-th date.

**Fix**: replay transactions incrementally — keep track of the last replayed transaction
`id` and only process NEW transactions in each iteration:

```python
def compute_cost_basis_series(
    date_from: str | None = None,
    date_to: str | None = None,
    freq: str = 'monthly',
) -> list[dict]:
    ...
    transactions = Transaction.objects.filter(
        type__in=POSITION_TRANSACTION_TYPES
    ).order_by('date', 'id')
    ...
    dates = _generate_date_series(start_date, end_date, freq)

    total_shares: dict[str, Decimal] = {}   # keyed by (symbol, account_id)
    total_cost: dict[str, Decimal] = {}

    # Use a pointer into the transactions list to avoid re-querying
    all_txs = list(transactions)
    tx_idx = 0

    results = []
    for tx_date in dates:
        # Advance pointer: process only transactions up to tx_date
        while tx_idx < len(all_txs) and all_txs[tx_idx].date <= tx_date:
            tx = all_txs[tx_idx]
            key = (tx.symbol, tx.account_id if tx.account_id else '')
            cost = abs(tx.amount_cad)
            if tx.type == 'BUY':
                total_shares[key] = total_shares.get(key, Decimal('0')) + tx.quantity
                total_cost[key] = total_cost.get(key, Decimal('0')) + cost
            elif tx.type == 'SELL':
                shares = total_shares.get(key, Decimal('0'))
                avg = total_cost[key] / shares if shares > 0 else Decimal('0')
                sold_cost = tx.quantity * avg
                total_shares[key] = max(shares - tx.quantity, Decimal('0'))
                total_cost[key] = max(total_cost.get(key, Decimal('0')) - sold_cost, Decimal('0'))
            tx_idx += 1

        running_cost = sum(total_cost.values())
        results.append({
            'date': tx_date.strftime('%Y-%m-%d'),
            'cost_basis_cad': running_cost,
        })

    return results
```

Remove the existing cache on `cost_basis_series` entirely — caching a date-range-
and freq-dependent series under a fixed key causes stale data when controls change.

### 4. Fix `compute_series_by_account` in `core/utils.py`

**Bug**: Each account generates its own `dates` list using its own `start_date` (first
BUY of that account). When "by_account" and "market_value" are both requested, the
per-account arrays have different lengths from the labels array, so Chart.js renders
empty lines.

**Fix**: force all accounts to share the same `dates` list generated from the
**global** first transaction date and `end_date`:

```python
def compute_series_by_account(
    date_from: str | None = None,
    date_to: str | None = None,
    freq: str = 'monthly',
) -> dict[str, list[dict]]:
```

Changes:
- Compute the global `start_date` (first BUY across all accounts) and `end_date` once.
- Apply `date_from` / `date_to` clipping to these global dates.
- Generate `dates = _generate_date_series(start_date, end_date, freq)` once.
- For each account, use the same `dates` list. Accounts with no holdings on a given
  date should emit `market_value_cad: Decimal('0')` (not be absent).
- Reuse the same price fetching code pattern as the fixed `compute_portfolio_value_series`
  (Problem A + B above). Fetch prices once using ALL symbols across ALL accounts, not
  per-account. Cache under `'hist_prices'` so a preceding call to
  `compute_portfolio_value_series` populates it.
- Apply the same NaN-safe price lookup with `pd.Timestamp(tx_date)` and explicit
  `math.isnan()` guard.

### 5. Update `portfolio_chart_data` view in `core/views.py`

Add `freq` as a validated query parameter and pass it through to all four series
functions:

```python
freq = request.GET.get('freq', 'monthly')
VALID_FREQS = {'daily', 'weekly', 'monthly', 'annual'}
if freq not in VALID_FREQS:
    return JsonResponse({'error': f'Invalid freq: {freq}. Must be one of {sorted(VALID_FREQS)}'}, status=400)
```

Then pass `freq=freq` to:
- `compute_portfolio_value_series(freq=freq, date_from=..., date_to=...)`
- `compute_cost_basis_series(date_from=..., date_to=..., freq=freq)`
- `compute_income_series(date_from=..., date_to=..., freq=freq)` (note: income always
  stays monthly regardless of freq — see note in §3 of compute_income_series below)
- `compute_series_by_account(date_from=..., date_to=..., freq=freq)`

**Note on income with non-monthly freq**: when `freq='daily'` or `'weekly'`, income
bars would be tiny and mostly zero. For `freq='daily'` and `'weekly'`, pass
`freq='monthly'` to `compute_income_series` so income always aggregates by month and
remains readable. Only pass `freq='annual'` to income when `freq='annual'` is selected.

Add `freq` to the `params` dict that the view echoes back in the response:
```json
{ "freq": "monthly", "labels": [...], "datasets": [...] }
```
This lets the frontend confirm what granularity was used.

### 6. Rewrite `core/templates/core/portfolio_chart.html`

Replace the entire template. Key changes:

**Controls panel**

Keep the existing controls (date range buttons, custom range, series checkboxes,
benchmark ticker, chart type). Add a **Granularity** control between the date range
buttons and the custom range section:

```html
<div class="mb-3">
  <label class="form-label fw-bold">Granularity:</label>
  <div class="btn-group" role="group" id="granularity_group">
    <button type="button" class="btn btn-outline-secondary btn-sm granularity-btn active"
            data-freq="daily">Daily</button>
    <button type="button" class="btn btn-outline-secondary btn-sm granularity-btn"
            data-freq="weekly">Weekly</button>
    <button type="button" class="btn btn-outline-secondary btn-sm granularity-btn active"
            data-freq="monthly">Monthly</button>
    <button type="button" class="btn btn-outline-secondary btn-sm granularity-btn"
            data-freq="annual">Annual</button>
  </div>
</div>
```

Only one granularity button should be active at a time (default: **Monthly**).

**Chart Type selector — fix the visual bug**

Replace the Bootstrap `btn-check` radio pattern with plain `btn-group` toggle buttons
using `active` class toggling in JavaScript (same pattern as the date-range buttons).
This avoids the known Bootstrap `btn-check` z-index conflict with `btn-group`:

```html
<div class="btn-group" role="group" id="chart_type_group">
  <button type="button" class="btn btn-outline-secondary btn-sm chart-type-btn active"
          data-type="line">Line</button>
  <button type="button" class="btn btn-outline-secondary btn-sm chart-type-btn"
          data-type="area">Area</button>
  <button type="button" class="btn btn-outline-secondary btn-sm chart-type-btn"
          data-type="bar">Bar</button>
</div>
```

**JavaScript — key changes**

1. **Add `activeFreq` state variable** (default `'monthly'`).

2. **Granularity button wiring**: same click pattern as date-range buttons — toggle
   the `active` class, update `activeFreq`, call `fetchChartData()`.

3. **Chart type button wiring**: replace the `querySelector('input[name=chart_type']:checked)`
   pattern with a plain `activeChartType` variable set on button click.

4. **Pass `freq` in query params**:
   ```javascript
   params.freq = activeFreq;
   ```

5. **`renderChart(data, chartType)` — fix Bar mode**:
   When `chartType === 'bar'`, set the **root chart type** to `'bar'` and remove the
   per-dataset type override. Exception: income dataset stays `'bar'` on `yAxisID='y2'`
   in all modes; benchmark stays `'line'` in all modes.
   ```javascript
   const rootType = (chartType === 'bar') ? 'bar' : 'line';
   chartInstance = new Chart(ctx, {
     type: rootType,
     data: { labels: data.labels, datasets: datasets },
     ...
   });
   ```
   In the datasets loop:
   - If `rootType === 'bar'`: all datasets with `yAxisID === 'y'` get `type: 'bar'`.
     Income (`yAxisID === 'y2'`) stays `type: 'bar'`. Benchmark stays `type: 'line'`.
   - If `chartType === 'area'`: market_value dataset gets `fill: true`,
     `backgroundColor: color + '33'`. All others stay unchanged.
   - If `chartType === 'line'` (default): all datasets with `yAxisID === 'y'` get
     `type: 'line'`.

6. **Auto-deselect `by_account` when switching to Bar chart type** — bar charts with
   10+ account series are unreadable. When the user clicks the Bar button, if `by_account`
   is checked, uncheck it and update the checkbox label to show a tooltip: `"By Account
   is disabled in Bar mode"`. Re-enable it when switching away from Bar.

7. **Granularity auto-limit for date range**:
   When `activeFreq === 'daily'` and the selected date range is wider than 6 months,
   display a warning banner below the controls: `"Daily granularity over a long range
   may be slow. Consider switching to Weekly or Monthly."` Use an inline `<div>` that
   is shown/hidden in JS.

8. **Labels**: for `'daily'` freq, labels will be dense. Configure Chart.js x-axis
   to auto-skip ticks:
   ```javascript
   x: {
     ticks: {
       maxTicksLimit: 12,
       autoSkip: true,
     }
   }
   ```

9. **Remove the `freq` param from the URL for `'monthly'` (default)** to keep URLs
   clean. Only append `freq=daily`, `freq=weekly`, or `freq=annual`.

**Default series on page load**: market_value only (all checkboxes unchecked except
"By Account" which should default to unchecked). This matches the current default.
Do NOT change the default — the user can opt in to extra series.

**Checkbox state defaults** (clarify existing spec ambiguity):

| Control | Default |
|---|---|
| Cost Basis | ✅ checked |
| Income | ✅ checked |
| Benchmark | ✅ checked |
| By Account | ✅ checked |
| Granularity | Monthly |
| Chart Type | Line |

Match the defaults shown in screenshot 1 (all four checkboxes were checked when the
user took that screenshot). On page load fetch with all four checked.

Actually: on page load, check the URL for `?series=...` params if the user navigated
back; otherwise default to the screenshot-1 state.
Use this simpler rule: **default state on fresh page load = all four checkboxes checked,
Monthly, Line**.

### 7. Tests — `core/tests/test_performance_chart.py`

Extend the existing test file. Add the following tests to the existing class or a new
`PerformanceChartFixesTests(TestCase)` class.

**Setup**: use the existing `setUp` from the file (add if missing):
- 1 Account, 1 BrokerStatement, 3 BUY transactions for two symbols (AAPL, SU.TO),
  1 SELL transaction, 2 DIV transactions.
- Mock `yf.download` in all price-related tests using `unittest.mock.patch`.

**New tests**:

- `test_generate_date_series_daily`: call `_generate_date_series(date(2026,1,1), date(2026,1,10), 'daily')`; assert result has 10 items and first == `date(2026,1,1)`.
- `test_generate_date_series_weekly`: call with `date(2026,1,1)` to `date(2026,1,31)`, `'weekly'`; assert all results are Fridays (`.weekday() == 4`).
- `test_generate_date_series_annual`: call with `date(2024,1,1)` to `date(2026,5,20)`, `'annual'`; assert result == `[date(2024,12,31), date(2025,12,31), date(2026,5,20)]`.
- `test_cost_basis_no_double_count`: create 2 BUY transactions in Jan 2026 ($100 each) and 1 BUY in Feb 2026 ($200). Call `compute_cost_basis_series(freq='monthly')`; assert the Feb value is `$400` (Jan $200, Feb $400). Assert the Feb value is NOT $800+ (double-count symptom).
- `test_chart_data_freq_daily`: mock `compute_portfolio_value_series`; GET `/portfolio/chart/data/?freq=daily&series=market_value`; assert 200 and `data['freq'] == 'daily'`.
- `test_chart_data_freq_invalid`: GET `/portfolio/chart/data/?freq=quarterly`; assert 400.
- `test_chart_data_market_value_nonzero_with_prices`: mock `yf.download` to return a simple price DataFrame; POST with `series=market_value`; assert the returned `data` values are non-zero.
- `test_chart_data_by_account_labels_match`: mock `compute_series_by_account` to return two accounts each with 3 data points; mock `compute_portfolio_value_series` to return 3 labels; GET with `series=market_value,by_account`; assert each account dataset in the response has exactly 3 values.
- `test_chart_page_has_granularity_control`: GET `/portfolio/chart/`; assert response contains `"Daily"` and `"Weekly"` and `"Annual"` (granularity buttons).

Run with: `venv\Scripts\python manage.py test core --settings=portfolio_manager.settings_test`

---

## Constraints

- Do NOT rename the `portfolio_chart_data` URL or the `portfolio_chart` URL.
- Do NOT change the JSON response keys `labels` and `datasets` — only add the new
  top-level `freq` key.
- Do NOT change the existing `compute_portfolio_value_series` return dict format
  (`{'date': str, 'market_value_cad': Decimal}`).
- `import pandas as pd` — check if already imported in `utils.py` before adding.
  If `pd` is already used, do not add a second import.
- Do NOT modify the `HISTORICAL_PRICE_CACHE_TIMEOUT` setting or any other settings.
- Do NOT create any migrations.
- All yfinance calls must remain wrapped in try/except. A yfinance failure must return
  an empty series, NOT crash the endpoint.
- Follow PEP 8. Add type hints to all new/changed function signatures.
- Do NOT push to git — see Manual Steps.
- The existing date-range buttons (1M, 3M, 6M, YTD, 1Y, All) must continue to work.
- The existing benchmark ticker input and checkbox must continue to work.
- Do NOT install new Python packages.

---

## Manual Steps Required

After the agent completes implementation:

- [ ] `venv\Scripts\python manage.py test core --settings=portfolio_manager.settings_test`
- [ ] Open browser at `http://localhost:8000/portfolio/chart/` while dev server is running
- [ ] Verify **Portfolio Market Value** shows non-zero values (use the tooltip — hover over a data point and confirm the market value value is not 0)
- [ ] Verify **Cost Basis** bars are plausible and not exponentially growing
- [ ] Toggle granularity to **Daily** for a 1M range — chart should redraw with ~30 data points
- [ ] Toggle granularity to **Weekly** for a 6M range — chart should show ~26 points
- [ ] Toggle granularity to **Annual** for the "All" range — chart should show one bar/point per year
- [ ] Toggle **By Account** — lines for each account should all be non-zero and sum roughly to total market value
- [ ] Toggle **Chart Type: Bar** — bars should render, "By Account" should auto-uncheck
- [ ] Toggle **Chart Type: Area** — market value should fill below the line
- [ ] Test the benchmark overlay — enter `^GSPC`, check Benchmark — rebased line should appear on the right y-axis
- [ ] `git add -A && git commit -m "fix: performance chart — market value zeros, cost basis double-count, granularity control, chart type toggle"`
- [ ] `git push origin feature/performance-chart-fixes`

---

## Human Approval Required

Human approval required before any git push. Confirm the following before approving:
- Market value tooltip shows non-zero values in the main chart
- No JS console errors on page load or when toggling controls
- All tests pass
