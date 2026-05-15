# Portfolio Manager — Application Status Report

**Date:** May 14, 2026  
**Branch:** dev  
**Stack:** Django 5.1, SQL Server, Python 3.10+, Bootstrap 5, Chart.js, yfinance

---

## 1. Architecture Overview

The application is a personal Django web application for tracking investment portfolios across multiple brokerage accounts. It is designed for family use on a private network, with a focus on import automation, multi-broker support, and consolidated reporting in CAD.

**Apps:**
- `core` — Models, views, templates, and all user-facing functionality
- `imports` — Broker CSV parsers, auto-import logic, inbox watcher, and management commands

**Database:** Microsoft SQL Server (via mssql-django + ODBC Driver 18)  
**External Data:** Yahoo Finance (yfinance) for live and historical prices  
**Configuration:** Environment variables (.env) → config.ini → hardcoded defaults

---

## 2. Data Model

### Account
Represents a single brokerage account. Fields:
- `account_id` — Unique identifier (e.g. `TOM IB`, `TS JOINT`, `RBC-123456`)
- `name` — Human-readable display name
- `broker` — `IB` (Interactive Brokers) or `RBC` (RBC Brokerage)
- `currency` — Base currency (USD or CAD)
- `starting_balance` / `starting_balance_date` — Optional opening balance for cash tracking

> ⚠️ **Gap:** No account category field (e.g. Retirement, Tax-Free, Taxable). All accounts are treated the same type.

### Transaction
Core financial activity record. Fields:
- `date`, `symbol`, `type`, `quantity`, `price`, `commission`, `amount`, `currency`
- `exchange_rate_cad`, `amount_cad` — CAD conversion
- `tx_hash` — SHA-256 deduplication fingerprint (prevents duplicate imports)
- `notes` — Free-text notes
- Linked to `Account` and `BrokerStatement`

### TransactionType
Lookup table for transaction codes (`BUY`, `SELL`, `DIV`, `DIST`, `INT`, `WITHTAX`, `TAX`, `FEE`, `DEP`, `WDRAW`, `TRANSFER`, `OTHER`). New types are auto-created on import.

### BrokerStatement
Represents a single imported CSV file from a broker. Tracks `source`, `file_date`, and `raw_filename`.

### FileImportLog
Import audit log. Tracks `filename`, `broker`, `imported_at`, `records_imported`, `records_skipped`, `status`, `error_message`, and `content_hash` for duplicate file detection.

### BrandingConfig
Singleton model for UI theme customisation. Fields: company name, logo URL, tagline, color scheme (primary, secondary, success, danger, navbar, footer), and display toggles.

---

## 3. Functionality Inventory

### 3.1 Authentication
| Feature | Status |
|---|---|
| Username/password login | ✅ Working |
| Logout | ✅ Working |
| Login required on all pages | ✅ Working |
| Rate limiting (5 attempts / 15 min) | ✅ Working |
| Open redirect prevention | ✅ Working |
| IP logging on login attempts | ✅ Working |

### 3.2 Dashboard
| Feature | Status |
|---|---|
| Total accounts count | ✅ Working |
| Total transactions count | ✅ Working |
| Total brokers count | ✅ Working |
| Accounts overview table with transaction counts | ✅ Working |
| Recent transactions list (configurable limit) | ✅ Working |
| Recent imports list (configurable limit) | ✅ Working |

> ⚠️ **Gap:** No portfolio-level dollar summary on the dashboard (no total market value, no total P&L card).

### 3.3 Transaction List
| Feature | Status |
|---|---|
| Paginated transaction table | ✅ Working |
| Filter by broker | ✅ Working |
| Filter by symbol | ✅ Working |
| Filter by transaction type | ✅ Working |
| Filter by account | ✅ Working |
| Filter by date range | ✅ Working |
| Export to CSV (with active filters) | ✅ Working |
| Export to Excel (with active filters) | ✅ Working |
| Input validation & length limits on all filters | ✅ Working |

### 3.4 Portfolio Summary
| Feature | Status |
|---|---|
| Current open positions (all accounts combined) | ✅ Working |
| Total cost basis in CAD | ✅ Working |
| Total market value in CAD (via yfinance) | ✅ Working |
| Total unrealised P&L in CAD | ✅ Working |
| Per-position: shares, avg cost, last price, market value, P&L, P&L % | ✅ Working |
| Broker and account name shown per position | ✅ Working |
| Price fetch from yfinance with caching | ✅ Working |

> ⚠️ **Gaps:**
> - No subtotals grouped by account
> - No subtotals grouped by account category (Retirement / Tax-Free / Taxable)
> - No sector or asset-class breakdown
> - No allocation % column (position weight in portfolio)
> - No currency breakdown (CAD vs USD holdings)

### 3.5 Portfolio Performance Chart
| Feature | Status |
|---|---|
| Monthly market value series (line chart) | ✅ Working |
| Historical prices from yfinance with caching | ✅ Working |
| USD→CAD conversion via USDCAD=X rate | ✅ Working |
| Chart.js rendering | ✅ Working |

> ⚠️ **Gaps:**
> - Single line chart only (total portfolio market value)
> - No cost basis line overlaid
> - No per-account or per-category performance lines
> - No benchmark comparison (e.g. S&P 500)
> - No chart type selection (bar, area, etc.)
> - No date range selector on the chart page
> - Monthly frequency only (no weekly or daily toggle)

### 3.6 Realised P&L
| Feature | Status |
|---|---|
| FIFO cost basis calculation | ✅ Working |
| Average cost basis calculation | ✅ Working |
| Per-trade breakdown (sell date, symbol, account, qty) | ✅ Working |
| Total P&L summary (FIFO and avg cost) | ✅ Working |
| Filter by account | ✅ Working |

> ⚠️ **Gap:** No filter by date range or symbol. No grouping by year for tax reporting.

### 3.7 Income Summary (Dividends, Interest, Distributions)
| Feature | Status |
|---|---|
| Grouped by year, symbol, and type | ✅ Working |
| Filter by year | ✅ Working |
| Filter by type (DIV, DIST, INT, WITHTAX, TAX) | ✅ Working |
| Gross income total | ✅ Working |
| Tax withheld total | ✅ Working |
| Net income total (gross + tax) | ✅ Working |

> ⚠️ **Gap:** No filter by account. No per-account income breakdown.

### 3.8 Accounts
| Feature | Status |
|---|---|
| Account list | ✅ Working |
| Add new account | ✅ Working |
| Edit account | ✅ Working |
| Delete account | ✅ Working |
| Account detail: balance summary (starting + net activity + ending) | ✅ Working |
| Account detail: activity grouped by type | ✅ Working |
| Account detail: current holdings table | ✅ Working |
| Account detail: paginated transaction list | ✅ Working |

> ⚠️ **Gap:** No `category` field on accounts (Retirement, Tax-Free, Taxable, etc.).

### 3.9 Import System
| Feature | Status |
|---|---|
| Interactive Brokers CSV parser (Activity Statement format) | ✅ Working |
| Interactive Brokers CSV parser (Transaction History flat format) | ✅ Working |
| RBC Brokerage CSV parser | ✅ Working |
| Duplicate transaction prevention (SHA-256 tx_hash) | ✅ Working |
| Duplicate file prevention (content_hash on FileImportLog) | ✅ Working |
| Rejection log CSV output for rows that failed to import | ✅ Working |
| Exchange rate capture during import | ✅ Working |
| Auto type normalisation (maps broker-specific strings → canonical codes) | ✅ Working |
| Management command: `import_statements` (manual) | ✅ Working |
| Management command: `auto_import` (process inbox) | ✅ Working |
| Management command: `watch_inbox` (daemon) | ✅ Working |
| Background inbox watcher thread (starts with `runserver`) | ✅ Working |
| Management command: `audit_fx` (find/fix amount_cad = 0 records) | ✅ Working |

### 3.10 Import History UI
| Feature | Status |
|---|---|
| Table of all import runs | ✅ Working |
| Records imported / skipped per run | ✅ Working |
| Status (success / partial / error) | ✅ Working |
| Error message display | ✅ Working |
| Download rejection log CSV (per month) | ✅ Working |
| Path traversal protection on download | ✅ Working |

### 3.11 Admin Panel (Django Admin)
| Feature | Status |
|---|---|
| Manage BrokerStatements | ✅ Working |
| Manage Transactions (searchable, filterable, date hierarchy) | ✅ Working |
| Manage Accounts | ✅ Working |
| Manage TransactionTypes | ✅ Working |
| Manage FileImportLogs (read-only fields) | ✅ Working |
| Manage BrandingConfig (singleton, UI theme) | ✅ Working |

### 3.12 Branding & Theme
| Feature | Status |
|---|---|
| Company name, logo, tagline configurable via admin | ✅ Working |
| Full color scheme configurable (primary, secondary, success, danger, navbar, footer) | ✅ Working |
| Theme applied via CSS variables and context processor on all pages | ✅ Working |

---

## 4. Security Status

| Control | Status |
|---|---|
| `DJANGO_SECRET_KEY` required environment variable | ✅ |
| `DEBUG` defaults to False, env-controlled | ✅ |
| `ALLOWED_HOSTS` required for production | ✅ |
| DB credentials via environment variables | ✅ |
| `TrustServerCertificate` defaults to off | ✅ |
| Login rate limiting (cache-based, 5/15min) | ✅ |
| CSRF protection (`SESSION_COOKIE_SAMESITE=Strict`) | ✅ |
| Session cookies `HTTPONLY=True` (hardcoded) | ✅ |
| Path traversal prevention on file downloads | ✅ |
| Input validation and length limits on all filters | ✅ |
| Activity logging (logins, deletes, exports, downloads) | ✅ |
| Open redirect prevention on login | ✅ |
| All sensitive views behind `@login_required` | ✅ |
| HTTPS redirect / HSTS (env-controlled, off by default for dev) | ✅ |

---

## 5. Configuration

All settings are environment-variable driven. Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | **Required** | Session encryption |
| `DEBUG` | `false` | Debug mode |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Request host validation |
| `DB_HOST / DB_NAME / DB_USER / DB_PASSWORD` | — | Database connection |
| `TRUST_SERVER_CERTIFICATE` | `false` | SQL Server cert validation |
| `LOGIN_ATTEMPT_LIMIT` | `5` | Rate limit threshold |
| `LOGIN_ATTEMPT_WINDOW` | `900` | Rate limit window (seconds) |
| `TRANSACTIONS_PAGE_SIZE` | `50` | Pagination |
| `DASHBOARD_RECENT_LIMIT` | `10` | Dashboard recent items |
| `PRICE_CACHE_TIMEOUT_SECONDS` | `900` | Live price cache TTL |
| `HISTORICAL_PRICE_CACHE_TIMEOUT_SECONDS` | `3600` | Historical price cache TTL |
| `IMPORT_INBOX_POLL_INTERVAL` | `300` | Inbox watcher poll interval |
| `CACHE_BACKEND` | `locmem` | Cache backend (use Redis in prod) |

---

## 6. Known Gaps Summary

| Area | Gap |
|---|---|
| Account model | No `category` field (Retirement / Tax-Free / Taxable) |
| Portfolio Summary | No subtotals by account or category |
| Portfolio Summary | No allocation % or currency breakdown |
| Dashboard | No dollar-value summary (market value, unrealised P&L) |
| Performance Chart | Single line only, no overlays, no date range, no chart type options |
| Realised P&L | No date range or symbol filter, no year grouping |
| Income Summary | No account filter, no per-account breakdown |
| Brokers | Hard-coded to IB and RBC only |
| Reports | No printable / PDF report generation |
| Notifications | No alerts (e.g. import failures, price thresholds) |
