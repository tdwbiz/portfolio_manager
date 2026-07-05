# Portfolio Manager — Implementation Status Report

**Date:** July 3, 2026
**Stack:** Django 5.1, MSSQL, Python 3.12, Bootstrap 5, Chart.js, yfinance

---

## 1. What the App Does

Portfolio Manager is a Django-based web application for tracking investment portfolios across multiple brokerage accounts (Interactive Brokers and RBC Royal Bank). It imports broker statements (CSV and PDF), calculates portfolio values using Yahoo Finance live prices, generates P&L reports, tracks income, and provides advanced risk analytics including Monte Carlo simulation and stress testing.

---

## 2. Currently Implemented Features

### 2.1 Authentication & Security
- ✅ Username/password login (custom view)
- ✅ Logout
- ✅ Login required on all views (`@login_required`)
- ✅ Rate limiting (5 attempts / 15 min)
- ✅ CSRF protection, session cookies (HTTPOnly, SameSite)
- ✅ Login rate limiting cache
- ✅ Open redirect prevention
- ✅ IP logging on failed login attempts
- ✅ Input validation & length limits on all filters
- ✅ Activity logging (logins, deletes, exports, downloads)
- ✅ Path traversal protection on file downloads
- ✅ HTTPS redirect / HSTS (env-controlled)
- ✅ `DJANGO_SECRET_KEY` required
- ✅ `DEBUG` defaults to False

### 2.2 Dashboard (`/`)
- ✅ Total accounts count
- ✅ Total transactions count
- ✅ Total brokers count
- ✅ Accounts overview table with transaction counts
- ✅ Recent transactions list (configurable limit)
- ✅ Recent imports list (configurable limit)
- ✅ Category and owner columns on accounts table
- ✅ Health check endpoint (`/health/`)

### 2.3 Account Management (`/accounts/`)
- ✅ Account list page with category and owner columns
- ✅ Add new account (CRUD form)
- ✅ Edit account (disabled `account_id` field)
- ✅ Delete account (with JS confirmation)
- ✅ Account detail page: balance summary, activity by type, current holdings, transaction list
- ✅ Category choices: RRSP, LIRA, TFSA, TAXABLE, RESP, OTHER
- ✅ Owner field (optional)

### 2.4 Transaction List (`/transactions/`)
- ✅ Paginated transaction table (configurable page size)
- ✅ Filter by broker
- ✅ Filter by symbol
- ✅ Filter by transaction type
- ✅ Filter by account
- ✅ Filter by date range
- ✅ Filters stack (multiple applied simultaneously)
- ✅ Export to CSV (respects active filters)
- ✅ Export to Excel (respects active filters, uses openpyxl)
- ✅ Account column in transaction table

### 2.5 Portfolio Summary (`/portfolio/`)
- ✅ Open positions for all accounts combined
- ✅ Shares, avg cost, total cost (CAD)
- ✅ Live market prices via yfinance (cached, 15 min)
- ✅ Market value (CAD), unrealised P&L (CAD), P&L %
- ✅ USD→CAD conversion via USDCAD=X rate
- ✅ Price fetch failures handled gracefully (shows `—`)
- ✅ Currency breakdown panel (CAD vs USD holdings)

### 2.6 Portfolio Performance Chart (`/portfolio/chart/`)
- ✅ Monthly market value line chart (Chart.js)
- ✅ Historical prices from yfinance (cached, 1 hour)
- ✅ USD→CAD conversion via USDCAD=X rate
- ✅ JSON data endpoint (`/portfolio/chart/data/`)

### 2.7 Realised P&L (`/realised-pnl/`)
- ✅ FIFO cost basis calculation
- ✅ Average cost basis calculation
- ✅ Per-trade breakdown (sell date, symbol, account, qty)
- ✅ Total P&L summary (FIFO and avg cost)
- ✅ Filter by account
- ✅ Year grouping toggle (flat / by year)
- ✅ Date range and symbol filters
- ✅ Green/red P&L coloring

### 2.8 Income Summary (`/income/`)
- ✅ Grouped by year, symbol, and type
- ✅ DIV, DIST, INT (income) and WITHTAX, TAX (tax)
- ✅ Filter by year
- ✅ Filter by type
- ✅ Gross income, tax withheld, net income totals
- ✅ Account filter

### 2.9 Annual Tax Summary (`/reports/tax-summary/`)
- ✅ Year selector (defaults to previous year)
- ✅ Section A: Capital Gains/Losses (FIFO + avg cost)
- ✅ Section B: Income by type
- ✅ Section C: Withholding Tax
- ✅ Section D: Breakdown by account category
- ✅ Print / export instructions
- ✅ Collapsible sections (Bootstrap cards)

### 2.10 Import System
- ✅ IB CSV parser — Activity Statement format (multi-section with "Trades")
- ✅ IB CSV parser — Transaction History format (flat)
- ✅ RBC CSV parser — Activity Export format
- ✅ Auto-detection of IB format from file content (no filename dependency)
- ✅ Exchange rate capture from RBC CSV header
- ✅ `amount_cad` calculation on import
- ✅ SHA-256 transaction hash dedup (`tx_hash`)
- ✅ File content hash dedup (`content_hash` on `FileImportLog`)
- ✅ Rejection log CSV output for failed rows
- ✅ Auto type normalisation (broker-specific strings → canonical codes)
- ✅ Management command: `import_statements` (manual, with `--dry-run`)
- ✅ Management command: `auto_import` (process inbox)
- ✅ Management command: `audit_fx` (find/fix broken `amount_cad`)
- ✅ Management command: `watch_inbox` (daemon mode)
- ✅ Background inbox watcher thread (starts with `runserver`)
- ✅ Inbox → processed folder with timestamped filenames

### 2.11 PDF Import System
- ✅ RBC monthly PDF parser (PyMuPDF)
- ✅ RBC annual report PDF parser (PyMuPDF)
- ✅ RBC annual report parser with Vision Language model (Qwen)
- ✅ RBC monthly parser with Vision Language model
- ✅ Management command: `manage_pdf_import`
- ✅ Management command: `manage_annual_report_import`
- ✅ Web UI: PDF import upload form (`/pdf-import/`)
- ✅ Web UI: Annual report import form (`/annual-report-import/`)
- ✅ Web UI: Annual reports view (`/annual-reports/`)

### 2.12 Import History UI (`/imports/`)
- ✅ Paginated table of all import runs
- ✅ Records imported / skipped per run
- ✅ Status (success / partial / error)
- ✅ Error message display (truncated to 80 chars)
- ✅ Download rejection log CSV (per month)
- ✅ Path traversal protection on downloads

### 2.13 Branding & Theme
- ✅ `BrandingConfig` model (singleton): company name, logo, tagline, colors
- ✅ Configurable via Django admin
- ✅ CSS variables applied on all pages via context processor
- ✅ Full color scheme: primary, secondary, success, danger, navbar, footer
- ✅ Display toggles (logo, banner)
- ✅ Bootstrap 5 responsive design
- ✅ Bootstrap Icons integration

### 2.14 Risk Analytics Module (`/risk/`)
- ✅ Security Universe management
- ✅ Market Price History
- ✅ Volatility Summary
- ✅ FX Rate tracking
- ✅ Monte Carlo simulation (GBM + jump-diffusion)
- ✅ Stress testing (multiple scenarios)
- ✅ Backtesting engine
- ✅ Regime detection
- ✅ Strategy recommendations
- ✅ Urgency scoring
- ✅ Alerts system
- ✅ Security detail pages with metrics
- ✅ Portfolio comparison
- ✅ Export strategy reports
- ✅ Explainability / audit trail
- ✅ 26 management commands for risk calculations
- ✅ 611 total tests (core + imports + risk + property-based + performance)

### 2.15 Additional Models & Features
- ✅ `PositionHistory` — historical position tracking
- ✅ `HistoricalPrice` — cached daily closing prices
- ✅ `RbcMonthly` / `RbcMonthlyBalance` / `RbcMonthlyTransaction` — parsed PDF data
- ✅ `RbcAnnual` — parsed annual report data
- ✅ `TransactionType` — lookup table (auto-created on import)
- ✅ Management command: `fetch_historical_prices`
- ✅ Management command: `rebuild_position_history`
- ✅ Management command: `seed_rm_universe`
- ✅ Management command: `merge_duplicate_accounts`
- ✅ Management command: `audit_rbc_data` (10 comprehensive checks)
- ✅ Management command: `daily_risk_refresh`
- ✅ Management command: `calc_portfolio_metrics`

---

## 3. Promised But Not Yet Delivered

### 3.1 Portfolio Summary Enhancements (Spec 16)
| Feature | Status | Notes |
|---------|--------|-------|
| Subtotals grouped by Account | ❌ Not implemented | Spec 16 defines `group_positions_by_account()` |
| Subtotals grouped by Category | ❌ Not implemented | Spec 16 defines `group_positions_by_category()` |
| Allocation % column | ❌ Not implemented | Spec 16 defines `allocation_pct` per position |
| Positions table by account/category sections | ❌ Not implemented | Spec 16 defines collapsible Bootstrap cards |

### 3.2 Performance Chart Enhancements (Spec 17)
| Feature | Status | Notes |
|---------|--------|-------|
| Cost basis overlay line | ❌ Not implemented | Spec 17 defines `compute_cost_basis_series()` |
| Income overlay (dividend bars) | ❌ Not implemented | Spec 17 defines `compute_income_series()` |
| Per-account performance lines | ❌ Not implemented | Spec 17 defines `compute_series_by_account()` |
| Benchmark comparison (S&P 500) | ❌ Not implemented | Spec 17 defines benchmark ticker input |
| Date range selector (1M/3M/6M/YTD/1Y/All) | ❌ Not implemented | Spec 17 defines date range buttons |
| Custom date range picker | ❌ Not implemented | Spec 17 defines date_from/date_to inputs |
| Series toggle checkboxes | ❌ Not implemented | Spec 17 defines Cost Basis/Income/Benchmark toggles |
| Chart type selector (Line/Area/Bar) | ❌ Not implemented | Spec 17 defines radio buttons |
| `freq` parameter (monthly/weekly) | ❌ Not implemented | Spec 17 extends `compute_portfolio_value_series()` |

### 3.3 Reporting Improvements (Spec 18)
| Feature | Status | Notes |
|---------|--------|-------|
| ✅ Date range filter on Realised P&L | ✅ Already implemented | Implemented outside spec |
| ✅ Symbol filter on Realised P&L | ✅ Already implemented | Implemented outside spec |
| ✅ Year grouping on Realised P&L | ✅ Already implemented | Implemented outside spec |
| ✅ Account filter on Income Summary | ✅ Already implemented | Implemented outside spec |
| ✅ Annual Tax Summary page | ✅ Already implemented | Implemented outside spec |
| ✅ Total Return calculation | ❌ Not implemented | Spec 18 defines total_return_cad = unrealised + realised + income |
| ✅ Dashboard dollar summary | ❌ Not implemented | NEXT_FEATURES.md #5.1 — market value, unrealised P&L on dashboard |

### 3.4 Dashboard Enhancements (NEXT_FEATURES.md)
| Feature | Status | Notes |
|---------|--------|-------|
| Portfolio dollar summary | ❌ Not implemented | #5.1 — total market value, unrealised P&L, YTD income |
| Top movers widget | ❌ Not implemented | #5.2 — best/worst positions |
| Upcoming dividends tracker | ❌ Not implemented | #5.3 — expected dividend payments |

### 3.5 Brand & UI Consistency (Spec 20)
| Feature | Status | Notes |
|---------|--------|-------|
| Favicon | ❌ Not confirmed | Spec 20 expects favicon in head |
| CSS variables (`:root`) | ❌ Not confirmed | Spec 20 expects `tsmoney.css` with CSS variables |
| Site banner with logo | ❌ Not confirmed | Spec 20 expects configurable banner |
| Page headers on all pages | ❌ Not confirmed | Spec 20 expects `.page-header` class |
| Stat cards with positive/negative classes | ❌ May be partial | Spec 20 defines `.stat-card`, `.positive`, `.negative` |
| Filter panel styling | ❌ Not confirmed | Spec 20 defines `.filter-panel` class |

### 3.6 RBC PDF Import (Spec 21)
| Feature | Status | Notes |
|---------|--------|-------|
| ✅ PDF parser | ✅ Implemented | PyMuPDF-based parser exists |
| ✅ Management command | ✅ Implemented | `manage_pdf_import` exists |
| ✅ Web upload view | ✅ Implemented | `/pdf-import/` exists |
| ✅ Tests | ⚠️ Unclear | Spec requires specific tests |

### 3.7 RBC Annual Report Import (Spec 22)
| Feature | Status | Notes |
|---------|--------|-------|
| ✅ PDF parser | ✅ Implemented | `parsers_annual.py` exists |
| ✅ Management command | ✅ Implemented | `manage_annual_report_import` exists |
| ✅ Web upload view | ✅ Implemented | `/annual-report-import/` exists |
| ✅ Annual reports view | ✅ Implemented | `/annual-reports/` exists |
| ✅ VL model parser | ✅ Implemented | `parsers_annual_vl.py` exists |
| ✅ Tests | ⚠️ Unclear | Spec requires specific tests |

### 3.8 Performance Chart Fixes (Spec 19)
| Feature | Status | Notes |
|---------|--------|-------|
| ✅ `compute_portfolio_value_series` fix | ✅ Likely implemented | Function exists, may need review |
| ✅ `compute_cost_basis_series` fix | ❓ Unclear | Spec 19 was supposed to fix spec 17 functions |
| ✅ `compute_series_by_account` fix | ❓ Unclear | Spec 19 was supposed to fix spec 17 functions |
| ✅ Chart page rewrite | ❓ Unclear | Spec 19 had a full template rewrite |
| ✅ `_generate_date_series` fix | ❓ Unclear | Spec 19 refactored date generation |

---

## 4. Gap Summary by Priority

### High Priority
| Gap | Specs | Impact |
|-----|-------|--------|
| Portfolio Summary: subtotals by account & category | Spec 16 | Users can't see per-account/per-category performance |
| Performance Chart: cost basis, benchmark, controls | Spec 17, 19 | Chart is bare-minimum (single line only) |
| Allocation % column on portfolio | Spec 16 | No visibility into concentration risk |
| Currency breakdown panel | Spec 16 | USD/CAD exposure hidden |
| Dashboard dollar summary | NEXT_FEATURES #5.1 | Dashboard shows only counts, no dollar values |

### Medium Priority
| Gap | Specs | Impact |
|-----|-------|--------|
| Total return calculation | Spec 18 | No total return metric |
| Brand & UI consistency | Spec 20 | Visual inconsistencies across pages |
| Top movers / dividends widgets | NEXT_FEATURES #5.2, #5.3 | Missing convenience widgets |
| Performance chart fixes verification | Spec 19 | Unclear if spec 17 functions were fixed |

### Lower Priority
| Gap | Specs | Impact |
|-----|-------|--------|
| Favicon and site banner | Spec 20 | Branding polish |
| Additional broker parsers | NEXT_FEATURES #6.1 | Only IB and RBC supported |
| Web-based import upload (for CSV) | NEXT_FEATURES #6.2 | Only PDF upload available |
| Import preview/dry-run UI | NEXT_FEATURES #6.3 | Dry-run only via CLI |
| FX rate management UI | NEXT_FEATURES #7.1 | Audit FX via CLI only |

---

## 5. What's NOT Needed (Already Done)

These specs from Phase A were **completed ahead of schedule** or **implemented differently**:

- ✅ Spec 08: Realised P&L with FIFO & avg-cost — done
- ✅ Spec 09: FX audit/repair command (`audit_fx`) — done
- ✅ Spec 10: Transaction export (CSV + Excel) — done
- ✅ Spec 11: Performance chart — done
- ✅ Spec 12: Income summary — done
- ✅ Spec 13: Import history UI — done
- ✅ Spec 14: Authentication — done
- ✅ Spec 15: Account categories & owners — done
- ✅ Spec 18: Reporting improvements (date/symbol filters, year grouping, tax summary) — done
- ✅ Spec 21: RBC PDF import — done
- ✅ Spec 22: RBC annual report import — done
- ✅ Spec 20: Branding & UI consistency — **partially done** (branding model exists, but CSS may not match spec)

---

## 6. Architecture Note

The application has grown significantly beyond the original Phase A specs to include:

- **Risk analytics module** (`risk/`) with Monte Carlo simulation, stress testing, backtesting, regime detection, and 26 management commands
- **PDF parsing** with both rule-based (PyMuPDF) and Vision Language model (Qwen) approaches
- **Position history tracking** for audit trails
- **Historical price caching** to avoid redundant yfinance calls
- **Comprehensive testing** (611 tests with property-based and performance regression tests)

These were **not part of the original Phase A specs** but represent significant value-add that went into the product.
