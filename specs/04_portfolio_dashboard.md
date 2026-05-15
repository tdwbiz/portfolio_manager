# Feature Spec: Portfolio Dashboard & Position Tracking

Project name: portfolio_manager
Customer: tdwdev
**Database:** Microsoft SQL Server (MSSQL) — existing DB `PortfolioDEV`, connection via `.env`
**Django version:** 5.1 (existing project — do NOT recreate, update in place)
**Feature branch:** `feature/portfolio-dashboard`

---

## Context

Specs 01–03 built the data pipeline: IB and RBC CSV import, auto-inbox processing, exchange rate
capture, and duplicate detection. The `core_transaction` table now holds real trade data from both
brokers.

This spec adds the first user-facing layer: a web dashboard to view the portfolio. No external
CSS framework — use plain HTML with minimal inline or `<style>` blocks until a design system is
chosen. The goal is working, readable pages with real data, not polish.

---

## Requirements

### 1. Position Model (computed, no extra table required yet)

A "position" is the aggregated view of all transactions for a given symbol:
- `symbol`
- `total_shares` — sum of BUY quantities minus SELL quantities
- `avg_cost_cad` — weighted average cost per share in CAD (`amount_cad / quantity` weighted across all BUY rows)
- `total_cost_cad` — total CAD invested (sum of `amount_cad` for BUY rows minus SELL proceeds in CAD)
- `broker` — IB or RBC (if a symbol appears in both, show both rows separately)
- `currency` — original currency of the transactions

Compute positions in Python from the `Transaction` queryset — do NOT create a new model or
migration for this spec. A helper function `compute_positions(broker=None)` in `core/views.py`
(or a new `core/utils.py`) is acceptable.

### 2. Dashboard View — `/` (root URL)

File: `core/views.py`

- Show a summary table of all open positions (total_shares > 0) grouped by broker
- Columns: Symbol | Shares | Avg Cost (CAD) | Total Cost (CAD) | Currency | Broker
- Show total portfolio value (sum of `total_cost_cad` across all open positions) at the top
- Show a second table: "Recent Imports" — last 10 `FileImportLog` rows ordered by `imported_at` desc
  - Columns: Filename | Broker | Imported At | Records Imported | Records Skipped | Status
- No login required for this spec

URL: `core/urls.py` → `path('', views.dashboard, name='dashboard')`
Include `core/urls.py` in `portfolio_manager/urls.py`

### 3. Transaction List View — `/transactions/`

File: `core/views.py`

- Paginated list of all transactions, 50 per page, ordered by date desc
- Columns: Date | Symbol | Type | Qty | Price | Commission | Amount | Currency | Amount (CAD) | Broker
- Filter controls (GET params): `?broker=IB`, `?broker=RBC`, `?symbol=AAPL`, `?type=BUY`
  - Filters stack (all applied simultaneously if present)
- Link from dashboard summary table symbol column to pre-filtered transaction list: `/transactions/?symbol=AAPL`

URL: `path('transactions/', views.transaction_list, name='transaction_list')`

### 4. Templates

Location: `core/templates/core/`

Files:
- `base.html` — minimal base with `<title>`, nav links (Dashboard | Transactions), `{% block content %}`
- `dashboard.html` — extends base, positions table + recent imports table
- `transaction_list.html` — extends base, filter form (GET), paginated table

No static files setup required. Inline `<style>` in `base.html` is fine.
Tables must be readable — use `border-collapse: collapse`, padding, alternating row shading.

### 5. Tests

File: `core/tests/test_views.py` (create `core/tests/__init__.py` too)

Use SQLite in-memory via `portfolio_manager/settings_test.py`.

Tests to write:
- `test_dashboard_loads` — GET `/` returns 200
- `test_dashboard_shows_positions` — seed 2 BUY transactions, assert symbols appear in response
- `test_dashboard_recent_imports` — seed a `FileImportLog`, assert filename appears
- `test_transaction_list_loads` — GET `/transactions/` returns 200
- `test_transaction_list_filter_by_broker` — seed IB + RBC transactions, filter `?broker=IB`, assert only IB rows shown
- `test_transaction_list_filter_by_symbol` — filter `?symbol=AAPL`, assert only AAPL rows shown
- `test_transaction_list_pagination` — seed 55 transactions, assert page 1 has 50 rows and page 2 has 5

All 7 tests must pass. Run with:
```
C:\AI-DevTeam\ai-env\Scripts\python.exe manage.py test core --settings=portfolio_manager.settings_test
```

---

## Out of Scope for This Spec

- Authentication / login
- Current market prices or P&L (no external API calls)
- Charts or graphs
- Edit / delete transactions via UI
- CSS frameworks (Bootstrap, Tailwind, etc.)
- REST API endpoints

---

## Acceptance Criteria

1. `python manage.py runserver` → visit `http://127.0.0.1:8000/` → dashboard loads with real position data from PortfolioDEV
2. Positions table shows aggregated shares and CAD cost per symbol per broker
3. Recent imports table shows last 10 file import logs
4. `/transactions/` shows paginated list with working broker/symbol/type filters
5. All 7 tests pass on SQLite in-memory
6. No new migrations required (no new models)
