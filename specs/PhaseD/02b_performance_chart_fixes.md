# Feature Spec: Performance Chart — Full Fix & Redesign

**Spec:** D2b — Spec 19 (applied after Spec 17 enhancements)
**Project name:** portfolio_manager
**Customer:** tdwdev
**Branch:** `feature_misc_outstanding_items`

---

## Context

Spec 17 added the high-level enhancements. This spec fixes the underlying implementation in `core/utils.py` to ensure correct date generation, proper handling of historical prices, and a robust chart data endpoint.

---

## Requirements

### 1. Replace `_generate_monthly_dates` with `_generate_date_series` in `core/utils.py`

Create a shared helper for generating date grids used by all series functions:

```python
def _generate_date_series(
    start_date: date,
    end_date: date,
    freq: str = 'monthly',
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[date]:
    """Generate a list of date points between start_date and end_date.

    If freq='monthly', returns last-day-of-month dates.
    If freq='weekly', returns Fridays.
    Applies date_from/date_to clipping.
    """
```

### 2. Fix `compute_portfolio_value_series` in `core/utils.py`

Ensure the function:
- Uses `_generate_date_series` for the date grid
- Correctly replays transactions to determine holdings at each date point
- Uses `.asof()` from the historical prices DataFrame for nearest-date lookups
- Wraps ALL yfinance calls in try/except — single symbol failure must not crash the endpoint
- Always includes `'USDCAD=X'` in the tickers list
- Returns `[{'date': 'YYYY-MM-DD', 'market_value_cad': Decimal}, ...]`

### 3. Fix `compute_cost_basis_series` in `core/utils.py`

Ensure:
- Uses `_generate_date_series` with the same date grid as `compute_portfolio_value_series`
- Computes cost basis correctly: sum of abs(amount_cad) for BUYs minus average cost of SELLs
- Does NOT call yfinance
- Uses the same `date_from`/`date_to` clipping
- Caches under key `'cost_basis_series'`

### 4. Fix `compute_series_by_account` in `core/utils.py`

Ensure:
- Reuses the cached historical prices DataFrame from `'hist_prices'` cache key
- Computes one series per account independently
- Only includes accounts with at least one BUY transaction
- Uses the same `_generate_date_series` date grid

### 5. Update `portfolio_chart_data` view in `core/views.py`

Ensure the view:
- Accepts all query parameters defined in Spec 17
- Returns proper JSON with `labels` and `datasets` arrays
- Validates `date_from`/`date_to` with `_validate_date_param()`
- Sanitises benchmark ticker: strip, max 20 chars, keep alphanumeric + `.`, `^`, `=`
- Handles empty transaction set gracefully (returns empty arrays)

### 6. Rewrite `core/templates/core/portfolio_chart.html`

The template must match the controls specification in Spec 17 exactly:
- Date range buttons with correct `?date_from=&date_to=` query params
- Custom date range inputs
- Series toggle checkboxes
- By Account toggle
- Benchmark ticker input (conditional visibility)
- Chart type radio buttons
- Chart.js initialization with proper error handling

### 7. Tests

Run the same tests from Spec 17 (`02_performance_chart_enhanced.md`). These tests must pass after both specs are implemented.

---

## Constraints

- Do NOT change the return format of any series function
- Do NOT remove existing URLs
- All yfinance calls wrapped in try/except
- Benchmark ticker sanitised before yfinance call
- No new Python packages required
- Follow PEP 8. Add type hints.
- Do NOT push to git — see Manual Steps.

---

## Manual Steps Required

- [ ] Run all tests from Spec 17 (02_performance_chart_enhanced.md) — they must pass
- [ ] Manual browser test of all chart controls
- [ ] `git add -A && git commit -m "fix: performance chart implementation corrections and date grid fixes"`
- [ ] `git push origin feature_misc_outstanding_items`

---

## Human Approval Required

Human approval required before any git push.
