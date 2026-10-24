# Phase D — Implementation Plan

**Goal:** Deliver all remaining features from Phase A specs that were promised but not yet completed, plus high-value items from NEXT_FEATURES.md.

**Branch:** `feature_misc_outstanding_items` (from `dev`)

---

## Summary of Outstanding Items

| # | Item | Spec | Priority | Effort |
|---|------|------|----------|--------|
| D1 | Portfolio Summary: subtotals by account & category, allocation %, currency breakdown | Spec 16 | 🔴 High | 1 session |
| D2 | Performance Chart: cost basis, benchmark, income overlay, date range, chart type | Spec 17 + Spec 19 | 🔴 High | 2 sessions |
| D3 | Dashboard dollar summary (market value, P&L, YTD income) | NEXT_FEATURES #5.1 | 🟡 Medium | 0.5 session |
| D4 | Total return calculation on portfolio page | Spec 18 §5 | 🟡 Medium | 0.5 session |
| D5 | Brand & UI consistency (CSS, favicon, banners, page headers) | Spec 20 | 🟡 Medium | 1 session |
| D6 | Top movers widget on dashboard | NEXT_FEATURES #5.2 | 🟢 Lower | 0.5 session |
| D7 | Upcoming dividends tracker widget | NEXT_FEATURES #5.3 | 🟢 Lower | 0.5 session |

**Estimated total: ~6 sessions**

---

## Dependencies

| Spec | Depends On |
|------|-----------|
| D1 (Portfolio Summary enhancements) | None — uses existing models & utils |
| D2 (Performance Chart enhancements) | None — extends existing `compute_portfolio_value_series` |
| D3 (Dashboard dollar summary) | D1 (needs enriched position data with market values) |
| D4 (Total return) | D1 (needs `compute_realised_pnl` + income data) |
| D5 (Brand & UI consistency) | None — standalone CSS/template changes |
| D6 (Top movers) | D1 (needs live prices) |
| D7 (Dividends tracker) | None — reads existing Transaction data |

**Suggested execution order:** D1 → D2 → D3 → D4 → D5 → D6 → D7

---

## Detailed Spec-by-Spec Breakdown

### D1: Portfolio Summary Enhancements (Spec 16)

**Spec file:** `01_portfolio_summary_enhanced.md`

**What's missing:**
- `group_positions_by_account()` utility function
- `group_positions_by_category()` utility function
- `allocation_pct` on each position
- Currency breakdown helper in `portfolio_summary` view
- Collapsible Bootstrap cards for "Summary by Category" and "Summary by Account"
- Allocation % column in positions table
- Currency breakdown panel

**What already exists:**
- `compute_positions()` — returns flat position list
- `enrich_positions_with_prices()` — adds last_price, market_value_cad, unrealised_pnl_cad
- `Account.category` and `Account.owner` fields (from Spec 15)
- Bootstrap 5 loaded in base.html

**Impact:** Users see per-account and per-category breakdowns, allocation weights, and currency exposure — essential for tax planning and concentration risk analysis.

---

### D2: Performance Chart Enhancements (Specs 17 + 19)

**Spec files:** `02_performance_chart_enhanced.md`, `03_performance_chart_fixes.md`

**What's missing:**
- `compute_cost_basis_series()` — total invested cost over time
- `compute_income_series()` — monthly dividend/interest bars
- `compute_series_by_account()` — per-account market value lines
- Extended `compute_portfolio_value_series()` with `date_from`/`date_to`/`freq` params
- Benchmark overlay (S&P 500, TSX, custom ticker)
- Date range selector buttons (1M/3M/6M/YTD/1Y/All + custom)
- Series toggle checkboxes (Cost Basis, Income, Benchmark)
- Chart type selector (Line/Area/Bar)
- By-account toggle
- Sanitised benchmark ticker input
- Multiple Y-axes for income (secondary) and benchmark (tertiary)

**What already exists:**
- `compute_portfolio_value_series()` — single line chart
- `/portfolio/chart/` and `/portfolio/chart/data/` endpoints
- Chart.js loaded from CDN
- Historical price caching via `HistoricalPrice` model

**Impact:** Chart goes from a single-line chart to a full performance visualisation with cost basis overlay, benchmark comparison, income bars, and interactive controls.

---

### D3: Dashboard Dollar Summary (NEXT_FEATURES #5.1)

**Spec file:** `04_dashboard_dollar_summary.md`

**What's missing:**
- Total portfolio market value card on dashboard
- Total unrealised P&L card on dashboard
- YTD income card on dashboard
- Positive/negative coloring (green/red)

**What already exists:**
- Dashboard view (`dashboard` in `core/views.py`)
- `compute_positions()` with enrichment
- yfinance price fetching

**Impact:** Dashboard becomes a proper financial overview instead of just showing counts.

---

### D4: Total Return Calculation (Spec 18 §5)

**Spec file:** `05_total_return.md`

**What's missing:**
- `total_realised_pnl_cad` on portfolio summary page
- `total_income_cad` on portfolio summary page
- `total_return_cad` = unrealised + realised + income
- Fourth/fifth summary cards showing these values

**What already exists:**
- `compute_realised_pnl()` — returns list of closed trades
- Income query pattern (`Transaction.objects.filter(type__in=(...))`)
- Portfolio summary page with 3 existing summary cards

**Impact:** Users see the single most important performance metric — total return including all gains and income.

---

### D5: Brand & UI Consistency (Spec 20)

**Spec file:** `06_branding_ui_consistency.md`

**What's missing:**
- `core/static/core/css/tsmoney.css` with CSS variables and design tokens
- `:root` variables: `--primary-color`, `--secondary-color`, etc.
- `.page-header` class with icons and descriptions
- `.stat-card`, `.positive`, `.negative` utility classes
- `.filter-panel` styled filter forms
- `.btn-primary`, `.btn-success`, `.btn-outline-primary` overrides
- `.table` styling (striped rows, hover states)
- Footer styling with link
- Login card styling with logo
- `.alert-*` color overrides
- Responsive breakpoints
- Favicon link in `<head>`
- Site banner with configurable logo image
- All pages use `.page-header` consistently

**What already exists:**
- `BrandingConfig` model with color fields
- Context processor serving brand colors
- Bootstrap 5 loaded
- Some templates partially updated

**Impact:** Consistent, professional UI across all pages with proper branding integration.

---

### D6: Top Movers Widget (NEXT_FEATURES #5.2)

**Spec file:** `07_top_movers_widget.md`

**What's missing:**
- Helper function to compute best/worst performers over configurable period
- Small table widget on dashboard showing top 5 gainers and losers
- Uses existing live prices from yfinance
- Configurable period (default 30 days)

**What already exists:**
- `fetch_last_prices()` — live prices from yfinance
- Dashboard template

**Impact:** Quick visibility into what's driving portfolio performance.

---

### D7: Upcoming Dividends Tracker (NEXT_FEATURES #5.3)

**Spec file:** `08_upcoming_dividends_tracker.md`

**What's missing:**
- Dividend schedule model or manual schedule table
- Helper function to compute upcoming payments in next 30/60/90 days
- Small table widget on dashboard
- Shows symbol, expected date, expected amount (CAD)

**What already exists:**
- Transaction DIV type data
- Dashboard template

**Impact:** Cash flow planning tool for income investors.

---

## Testing Strategy

All specs follow the existing pattern:
- Use `--settings=portfolio_manager.settings_test` (SQLite in-memory)
- Write `TestCase` classes in `core/tests/`
- Run: `python manage.py test core --settings=portfolio_manager.settings_test`
- Existing tests must continue to pass — never overwrite, always append

**Test counts by spec:**
- D1: ~6 new tests
- D2: ~6 new tests
- D3: ~3 new tests
- D4: ~2 new tests
- D5: ~5 new tests
- D6: ~2 new tests
- D7: ~2 new tests

**Total new tests: ~26**

---

## Migration Impact

| Spec | Needs migration? | Notes |
|------|-----------------|-------|
| D1 | No | Uses existing models |
| D2 | No | Uses existing models |
| D3 | No | Uses existing models |
| D4 | No | Uses existing models |
| D5 | No | CSS/template only |
| D6 | No | Uses existing models |
| D7 | **Yes** | May need a `DividendSchedule` model or reuse existing |

---

## Risk & Mitigation

| Risk | Mitigation |
|------|-----------|
| yfinance rate limits during testing | Mock all price fetches in tests |
| Spec 19 fixes overlap with Spec 17 | Read Spec 19 carefully; apply fixes first, then enhancements |
| Branding CSS conflicts with Bootstrap | Use CSS variables and scoped selectors |
| Performance of `compute_realised_pnl()` on large datasets | Add pagination or limit on portfolio page; existing function already exists |

---

## Success Criteria

After all Phase D specs are complete:

1. All 26 new tests pass
2. All existing tests still pass (0 regressions)
3. Portfolio summary page shows: by-account subtotals, by-category subtotals, allocation %, currency breakdown
4. Performance chart has: cost basis overlay, benchmark comparison, income bars, date range selector, chart type options
5. Dashboard shows dollar summary cards with market value, P&L, YTD income
6. Portfolio summary shows total return (realised + unrealised + income)
7. All pages use consistent `.page-header` styling and branding CSS
8. Dashboard has top movers and upcoming dividends widgets
9. Manual browser test of every page confirms correct rendering
10. `python manage.py test core --settings=portfolio_manager.settings_test` passes with 0 failures
