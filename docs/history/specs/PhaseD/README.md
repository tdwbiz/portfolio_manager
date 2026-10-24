# Phase D — Specs Index

All specs for delivering outstanding items from Phase A and NEXT_FEATURES.

## Spec Files

| File | Spec | Description | Priority | Depends On |
|------|------|-------------|----------|------------|
| `00_spec_template.md` | — | Template for new specs | N/A | — |
| `01_portfolio_summary_enhanced.md` | D1 | Subtotals by account & category, allocation %, currency breakdown | 🔴 High | — |
| `02_performance_chart_enhanced.md` | D2a | Cost basis, benchmark, income overlay, date range, chart type | 🔴 High | — |
| `02b_performance_chart_fixes.md` | D2b | Fixes for performance chart implementation | 🔴 High | D2a |
| `03_dashboard_dollar_summary.md` | D3 | Market value, P&L, YTD income cards on dashboard | 🟡 Medium | — |
| `04_total_return.md` | D4 | Total return calculation on portfolio page | 🟡 Medium | — |
| `05_branding_ui_consistency.md` | D5 | CSS variables, page headers, footer, login styling | 🟡 Medium | — |
| `06_top_movers_widget.md` | D6 | Top 5 gainers/losers widget on dashboard | 🟢 Lower | — |
| ~~`07_upcoming_dividends_tracker.md`~~ | D7 | Upcoming dividend payments widget on dashboard — **removed** (spec deleted; `DividendSchedule` model dropped, see `docs/plans/dividend-schedule.md`) | 🟢 Lower | — |

## Execution Order

Run specs in this order to respect dependencies:

1. **D1** — `01_portfolio_summary_enhanced.md`
2. **D3** — `03_dashboard_dollar_summary.md`
3. **D2a** — `02_performance_chart_enhanced.md`
4. **D2b** — `02b_performance_chart_fixes.md`
5. **D4** — `04_total_return.md`
6. **D5** — `05_branding_ui_consistency.md`
7. **D6** — `06_top_movers_widget.md`
8. **D7** — `07_upcoming_dividends_tracker.md`

## Quick Reference

### What's being delivered

1. **Portfolio Summary** — Grouped subtotals by account and category, allocation %, currency breakdown
2. **Performance Chart** — Cost basis overlay, benchmark comparison, income bars, date range selector, chart type options, per-account lines
3. **Dashboard** — Dollar value summary cards, top movers widget, upcoming dividends tracker
4. **Portfolio Summary** — Total return calculation (unrealised + realised + income)
5. **Branding** — Consistent CSS, page headers, footer, login card styling, favicon
6. **Dashboard widgets** — Top gainers/losers, upcoming dividend schedule

### Migration impact

| Spec | Migration needed? |
|------|------------------|
| D1 | No |
| D2a + D2b | No |
| D3 | No |
| D4 | No |
| D5 | No |
| D6 | No |
| D7 | **Yes** (DividendSchedule model) |

### Test summary

| Spec | Approx new tests |
|------|-----------------|
| D1 | 6 |
| D2a + D2b | 6 |
| D3 | 3 |
| D4 | 1 |
| D5 | 8 |
| D6 | 2 |
| D7 | 2 |
| **Total** | **~28** |

### Validation

After all specs are complete:
```powershell
python manage.py test core --settings=portfolio_manager.settings_test
```

Expected: 0 failures, 0 errors (existing + new tests).
