# Portfolio Manager — User Manual

**Version**: 1.0  
**Last Updated**: 2026-06-11  
**Django Version**: 5.1.15  
**Python Version**: 3.12

---

## Table of Contents

1. [Getting Started](#1-getting-started)
   - [System Requirements](#11-system-requirements)
   - [Installation](#12-installation)
   - [Initial Configuration](#13-initial-configuration)
   - [First Launch](#14-first-launch)
2. [Configuration](#2-configuration)
   - [Environment Variables (.env)](#21-environment-variables-env)
   - [Application Settings (config.ini)](#22-application-settings-configini)
   - [Database Setup](#23-database-setup)
   - [Branding Configuration](#24-branding-configuration)
3. [Core Features](#3-core-features)
   - [Dashboard](#31-dashboard)
   - [Accounts](#32-accounts)
   - [Transactions](#33-transactions)
   - [Portfolio Summary](#34-portfolio-summary)
   - [Performance Charts](#35-performance-charts)
   - [Reports](#36-reports)
4. [Statement Import](#4-statement-import)
   - [Automatic Import (File Watcher)](#41-automatic-import-file-watcher)
   - [Manual PDF Upload](#42-manual-pdf-upload)
   - [Annual Report Import](#43-annual-report-import)
   - [Import Commands](#44-import-commands)
5. [Risk Analytics](#5-risk-analytics)
   - [Risk Dashboard](#51-risk-dashboard)
   - [Security Details](#52-security-details)
   - [Strategy Recommendations](#53-strategy-recommendations)
   - [Alternative Portfolios](#54-alternative-portfolios)
   - [Portfolio Comparison](#55-portfolio-comparison)
   - [Stress Testing](#56-stress-testing)
   - [Risk Heatmap](#57-risk-heatmap)
6. [Management Commands](#6-management-commands)
   - [Data Fetching](#61-data-fetching)
   - [Risk Calculations](#62-risk-calculations)
   - [Seed Data](#63-seed-data)
   - [Maintenance](#64-maintenance)
   - [Data Audit](#65-data-audit)
7. [Admin Interface](#7-admin-interface)
8. [Troubleshooting](#8-troubleshooting)
9. [Security](#9-security)
10. [Testing](#10-testing)

---

## 1. Getting Started

### 1.1 System Requirements

| Component | Requirement |
|-----------|-------------|
| **OS** | Windows 10/11 (development), Linux Ubuntu 20.04+ (production) |
| **Python** | 3.12 |
| **Database** | Microsoft SQL Server 2025 (dev) / 2022 (prod) |
| **ODBC Driver** | SQL Server ODBC Driver 18 |
| **Memory** | 4 GB minimum, 8 GB recommended |
| **Disk** | 2 GB free space (plus database storage) |
| **Network** | Internet access for yfinance price data |

### 1.2 Installation

#### Step 1: Clone or Access the Project

```powershell
cd C:\AI-DevTeam\projects\portfolio_manager
```

#### Step 2: Create Virtual Environment

```powershell
python -m venv venv
venv\Scripts\activate
```

#### Step 3: Install Dependencies

```powershell
pip install -r requirements.txt
```

**Key Dependencies**:
- `django>=5.1` — Web framework
- `django-mssql-backend>=2.0` — SQL Server backend
- `python-dotenv>=1.0` — Environment variable loading
- `openpyxl>=3.1` — Excel file support
- `pandas>=2.0` — Data manipulation
- `yfinance>=0.2.32,<0.5.0` — Yahoo Finance price data
- `PyMuPDF>=1.24.0` — PDF parsing
- `weasyprint>=60.0` — PDF report generation
- `python-dateutil>=2.8.0` — Date parsing
- `hypothesis` — Property-based testing

### 1.3 Initial Configuration

#### Step 1: Create .env File

```powershell
copy .env.example .env
```

Edit `.env` with your settings (see [Section 2.1](#21-environment-variables-env) for details).

#### Step 2: Create Database

```sql
CREATE DATABASE PortfolioDev;
```

#### Step 3: Run Migrations

```powershell
python manage.py migrate
```

#### Step 4: Create Superuser

```powershell
python manage.py createsuperuser
```

### 1.4 First Launch

```powershell
python manage.py runserver
```

Open your browser:
- **Main Application**: http://localhost:8000/
- **Admin Interface**: http://localhost:8000/admin/

---

## 2. Configuration

### 2.1 Environment Variables (.env)

The `.env` file contains sensitive configuration and is **never committed to git**.

#### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `DJANGO_SECRET_KEY` | Django secret key for cryptographic signing | Generate with: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |

#### Database Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_HOST` | SQL Server hostname | `localhost` |
| `DB_PORT` | SQL Server port | `1433` |
| `DB_NAME` | Database name | `PortfolioDev` |
| `DB_USER` | Database username | *(required in production)* |
| `DB_PASSWORD` | Database password | *(required in production)* |
| `TRUST_SERVER_CERTIFICATE` | Trust self-signed SQL certs | `false` |

#### Security Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DEBUG` | Debug mode (`true`/`false`) | `false` |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `SESSION_COOKIE_SECURE` | Require HTTPS for session cookies | `false` |
| `CSRF_COOKIE_SECURE` | Require HTTPS for CSRF cookies | `false` |
| `SECURE_SSL_REDIRECT` | Redirect HTTP to HTTPS | `false` |
| `SECURE_HSTS_SECONDS` | HSTS max age in seconds | `0` |
| `LOGIN_ATTEMPT_LIMIT` | Max failed login attempts | `5` |
| `LOGIN_ATTEMPT_WINDOW` | Login attempt window (seconds) | `900` (15 min) |

#### UI & Cache Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TRANSACTIONS_PAGE_SIZE` | Transactions per page | `50` |
| `ACCOUNT_DETAIL_PAGE_SIZE` | Account detail items per page | `50` |
| `DASHBOARD_RECENT_LIMIT` | Recent items on dashboard | `10` |
| `CACHE_BACKEND` | Django cache backend class | `django.core.cache.backends.locmem.LocMemCache` |
| `CACHE_LOCATION` | Cache connection string | *(varies by backend)* |

### 2.2 Application Settings (config.ini)

The `config.ini` file contains non-secret settings and **is committed to git**.

```ini
[import]
inbox_dir =                    # Absolute path to inbox (blank = <project>/inbox)
processed_dir =                # Absolute path to processed (blank = <project>/processed)
logs_dir =                     # Absolute path to logs (blank = <project>/logs)
poll_interval = 60             # File watcher check interval (seconds, 0 = disabled)

[ui]
transactions_page_size = 50    # Transaction list pagination
account_detail_page_size = 50  # Account detail pagination

[cache]
price_timeout_seconds = 900              # Quote cache duration
historical_price_timeout_seconds = 3600  # Chart cache duration

[logging]
file_max_bytes = 5242880     # 5 MB log file size limit
file_backup_count = 5        # Keep 5 rotated log files
```

### 2.3 Database Setup

#### Development Database

```sql
CREATE DATABASE PortfolioDev;
```

#### Production Database

```sql
CREATE DATABASE TaxPortfolioPRD;

-- Create application user (never use 'sa')
CREATE USER portfolio_user WITH PASSWORD 'StrongPassword123!@#';
GRANT CONNECT ON DATABASE [TaxPortfolioPRD] TO portfolio_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::dbo TO portfolio_user;
```

#### Key Tables

| Table | Purpose |
|-------|---------|
| `core_account` | Brokerage accounts (IB, RBC) |
| `core_transaction` | Individual transactions |
| `core_brokerstatement` | Imported statement files |
| `core_rbc_monthly` | RBC monthly PDF statements |
| `core_rbc_annual` | RBC annual reports |
| `core_position_history` | Historical position snapshots |
| `rm_security_universe` | Tracked securities |
| `rm_market_price_history` | OHLCV price data |
| `rm_volatility_summary` | Precomputed volatility |
| `rm_fx_rates` | USD/CAD exchange rates |
| `rm_economic_indicators` | Macro indicators |
| `rm_strategy_recommendations` | Risk strategy alerts |

### 2.4 Branding Configuration

Branding is configured through the **Admin Interface** (`/admin/`).

1. Navigate to `/admin/`
2. Click **Branding Configuration**
3. Edit the existing record or create a new one

#### Branding Options

| Field | Description | Default |
|-------|-------------|---------|
| **Company Name** | Displayed in navbar and page titles | "Portfolio Manager" |
| **Company Logo URL** | Full URL to logo image (PNG, SVG, JPG) | — |
| **Tagline** | Slogan below navbar | — |
| **Show Logo** | Toggle logo visibility | On |
| **Show Tagline** | Toggle tagline visibility | Off |
| **Primary Color** | Main brand color (hex) | `#0d6efd` (blue) |
| **Secondary Color** | Supporting color (hex) | `#6c757d` (gray) |
| **Success Color** | Positive values color (hex) | `#198754` (green) |
| **Danger Color** | Negative values color (hex) | `#dc3545` (red) |
| **Navbar Background** | Top nav bar color (hex) | `#212529` (dark) |
| **Navbar Text** | Nav bar text color (hex) | `#ffffff` (white) |
| **Footer Background** | Footer color (hex) | `#f8f9fa` (light gray) |
| **Footer Text** | Copyright/custom message | — |

---

## 3. Core Features

### 3.1 Dashboard

The dashboard provides an at-a-glance view of your portfolio:

**Access**: http://localhost:8000/dashboard/

**Displays**:
- Total accounts count
- Total transactions count
- Number of brokers
- Recent activity summary
- Quick navigation to key sections

### 3.2 Accounts

Manage brokerage accounts across different owners and categories.

**Access**: http://localhost:8000/accounts/

#### Account Fields

| Field | Description |
|-------|-------------|
| **Account ID** | Unique identifier (e.g., "TOM IB", "TS JOINT", "RBC-123456") |
| **Name** | Human-readable display name |
| **Broker** | Interactive Brokers or RBC |
| **Currency** | Default currency (CAD, USD) |
| **Category** | RRSP, LIRA, TFSA, TAXABLE, RESP, OTHER |
| **Owner** | Person/entity (Tom, Susan, Joint) |
| **Starting Balance** | Initial account value |
| **Starting Balance Date** | Date of starting balance |
| **Exclude from Risk** | When enabled, excludes from risk calculations |

#### Account Actions

- **List**: View all accounts with summary stats
- **Detail**: View account positions, transactions, and performance
- **Create**: Add new account
- **Edit**: Modify account details
- **Delete**: Remove account (with cascade handling)

### 3.3 Transactions

View and manage all portfolio transactions.

**Access**: http://localhost:8000/transactions/

#### Transaction Fields

| Field | Description |
|-------|-------------|
| **Date** | Transaction date |
| **Symbol** | Stock ticker (e.g., AAPL, QQQ, VDY) |
| **Type** | BUY, SELL, DIV, DIST, INT, WITHTAX, TAX |
| **Quantity** | Number of shares/units |
| **Price** | Price per share |
| **Commission** | Trading commission/fee |
| **Amount** | Total transaction amount |
| **Currency** | Transaction currency |
| **Amount (CAD)** | Converted to CAD |
| **Exchange Rate** | FX rate used for conversion |
| **Account** | Source account |
| **Notes** | Additional notes |

#### Transaction Actions

- **List**: Filter by account, type, date range, symbol
- **Export CSV**: Download transactions as CSV
- **Export Excel**: Download transactions as Excel (.xlsx)

### 3.4 Portfolio Summary

View current portfolio holdings across all accounts.

**Access**: http://localhost:8000/portfolio/

**Displays**:
- Symbol, quantity, cost basis, market value
- Unrealized P&L (gain/loss)
- Percentage allocation
- Currency breakdown (CAD vs USD)
- Realized P&L (FIFO and average-cost methods)

### 3.5 Performance Charts

Visualize portfolio performance over time.

**Access**: http://localhost:8000/portfolio/chart/

**Features**:
- Interactive Chart.js line chart
- Historical portfolio value over time
- Monthly data points
- AJAX-powered data loading

### 3.6 Reports

#### Income Summary

**Access**: http://localhost:8000/income/

Shows dividend, distribution, and interest income by:
- Account
- Symbol
- Type (DIV, DIST, INT)
- Date range

#### Tax Summary

**Access**: http://localhost:8000/reports/tax-summary/

Consolidates tax-relevant information:
- Realized gains/losses
- Dividend types (eligible, non-eligible, foreign)
- Foreign tax withheld
- Total income

#### Import History

**Access**: http://localhost:8000/imports/

Track all import operations:
- Success/error status
- Records imported
- Skipped duplicates
- Timestamps

#### Rejection Logs

Download rejection logs for failed import records:
- Access via: `/imports/rejection-log/<month>/`
- CSV format with error details

---

## 4. Statement Import

### 4.1 Automatic Import (File Watcher)

The application monitors an inbox directory for new statement files and automatically processes them.

#### Setup

1. **Configure inbox path** in `config.ini`:
   ```ini
   [import]
   inbox_dir = C:\path\to\inbox
   processed_dir = C:\path\to\processed
   poll_interval = 60
   ```

2. **Place statement files** in the inbox directory:
   - Supported formats: PDF, CSV, Excel
   - Files are moved to `processed/` after successful import
   - Failed imports are logged to `logs/`

3. **Start the watcher**:
   ```powershell
   python manage.py watch_inbox
   ```

   Or enable automatic watcher in `runserver` by setting `poll_interval > 0` in `config.ini`.

#### Supported Brokers

| Broker | Format | Parser |
|--------|--------|--------|
| Interactive Brokers | CSV, Excel | `parsers.py` |
| RBC Royal Bank | PDF (monthly) | `parsers_pdf.py` |
| RBC Royal Bank | PDF (annual report) | `parsers_annual_vl.py` |

### 4.2 Manual PDF Upload

Upload RBC monthly statements directly through the browser.

**Access**: http://localhost:8000/pdf-import/

**Steps**:
1. Click "Upload PDF Statement"
2. Select a PDF file (must have `.pdf` extension)
3. Click "Import"
4. View results: parsed transactions, balances, any errors

**Validation**:
- File must have `.pdf` extension
- File must contain valid PDF magic bytes (`%PDF-`)
- Statement date must be extractable from PDF
- Account number is matched to existing accounts

### 4.3 Annual Report Import

Import RBC annual reports with Vision Language model (VLM) parsing.

**Access**: http://localhost:8000/annual-report-import/

**Features**:
- Uses Qwen Vision Language model for PDF text extraction
- Handles complex RBC annual report layouts
- Extracts holdings, transactions, and summary data

**Management Command**:
```powershell
python manage.py manage_annual_report_import
```

### 4.4 Import Commands

| Command | Description | Example |
|---------|-------------|---------|
| `import_statements` | Import broker statements | `python manage.py import_statements` |
| `auto_import` | Run one-time auto-import | `python manage.py auto_import` |
| `watch_inbox` | Start file watcher | `python manage.py watch_inbox` |
| `audit_fx` | Audit FX rate data quality | `python manage.py audit_fx` |

---

## 5. Risk Analytics

The Risk Analytics module provides advanced portfolio analysis tools.

**Base URL**: http://localhost:8000/risk/

### 5.1 Risk Dashboard

**Access**: http://localhost:8000/risk/dashboard/

**Displays**:
- Portfolio risk metrics
- Market conditions summary
- Regime detection results
- Active alerts and recommendations
- Volatility summaries

### 5.2 Security Details

**Access**: http://localhost:8000/risk/security/<symbol>/

**Displays**:
- Security information (name, type, sector)
- Price history and trends
- Volatility metrics
- Risk scores and drivers
- Export options (PDF, Excel)

### 5.3 Strategy Recommendations

**Access**: http://localhost:8000/risk/strategy/<rec_id>/

**Features**:
- AI-generated strategy recommendations
- Urgency scoring (1–10 scale)
- Acknowledge and action tracking
- Explainability (audit trail for each recommendation)

**Actions**:
- **Acknowledge**: Mark recommendation as reviewed
- **Mark Actioned**: Mark recommendation as implemented
- **Export**: Download strategy report (PDF/Excel)

### 5.4 Alternative Portfolios

**Access**: http://localhost:8000/risk/alternatives/

**Features**:
- Create alternative portfolio scenarios
- Define target weights for holdings
- Compare against current portfolio
- Account-specific alternatives

**Actions**:
- **Create**: New alternative portfolio
- **Edit**: Modify target weights
- **Account View**: View alternatives by account

### 5.5 Portfolio Comparison

**Access**: http://localhost:8000/risk/compare/

**Features**:
- Compare current portfolio against alternatives
- Side-by-side metric comparison
- Allocation differences
- Risk metric comparison

### 5.6 Stress Testing

**Access**: http://localhost:8000/risk/stress/<run_id>/

**Features**:
- Historical scenario testing
- Custom stress scenarios
- Monte Carlo simulation results
- Jump-diffusion modeling

**Management Commands**:
```powershell
python manage.py run_stress_test
python manage.py run_montecarlo
python manage.py run_backtest
```

### 5.7 Risk Heatmap

**Access**: http://localhost:8000/risk/heatmap/

**Displays**:
- Visual risk heatmap by sector/asset
- Correlation matrix
- Risk concentration indicators

---

## 6. Management Commands

All commands are run from the project root directory.

### 6.1 Data Fetching

| Command | Description |
|---------|-------------|
| `fetch_yfinance_prices` | Fetch current prices from yfinance |
| `fetch_historical_prices` | Fetch OHLCV historical data |
| `fetch_fx_rates` | Fetch USD/CAD exchange rates |
| `fetch_economic_indicators` | Fetch macro economic indicators |
| `import_amibroker_prices` | Import Amibroker CSV price data |
| `refresh_market_data` | Refresh all market data sources |

**Example**:
```powershell
python manage.py fetch_yfinance_prices
python manage.py refresh_market_data
```

### 6.2 Risk Calculations

| Command | Description |
|---------|-------------|
| `daily_risk_refresh` | Full daily risk calculation refresh |
| `calc_portfolio_metrics` | Calculate portfolio risk metrics |
| `calc_security_metrics` | Calculate per-security metrics |
| `calc_market_conditions` | Calculate market conditions |
| `calc_regime` | Detect current market regime |
| `compute_volatility` | Compute volatility summaries |
| `calc_urgency` | Calculate urgency scores |
| `calc_strategy_recommendations` | Generate strategy recommendations |
| `evaluate_alerts` | Evaluate and trigger alerts |

**Example**:
```powershell
python manage.py daily_risk_refresh
```

### 6.3 Seed Data

| Command | Description |
|---------|-------------|
| `seed_rm_universe` | Seed security universe with symbols |
| `seed_rm_universe_update` | Update risk universe |
| `seed_instrument_catalogue` | Seed instrument catalogue |
| `seed_owner_config` | Seed owner configurations |
| `seed_alert_configs` | Seed alert configurations |
| `seed_scoring_config` | Seed scoring configurations |
| `seed_security_drivers` | Seed security drivers |
| `seed_regime_sensitivity` | Seed regime sensitivity data |
| `seed_historical_scenarios` | Seed historical scenarios |

### 6.4 Maintenance

| Command | Description |
|---------|-------------|
| `rebuild_position_history` | Rebuild position history from transactions |
| `merge_duplicate_accounts` | Merge duplicate account records |
| `audit_retention_check` | Audit data retention compliance |

### 6.5 Data Audit

The audit command runs **10 comprehensive checks** to verify data integrity after RBC PDF imports. Run this after every import to catch issues early.

| Command | Description |
|---------|-------------|
| `audit_rbc_data` | Run all 10 audit checks |
| `audit_rbc_data --verbose` | Show detailed output with all fields |
| `audit_rbc_data --json` | Output audit report in JSON format |
| `audit_rbc_data --md` | Generate markdown report file |
| `audit_rbc_data --md --output-dir processed\audit` | Custom output directory |
| `audit_rbc_data --fix` | Auto-fix reversible issues |

**The 10 Audit Checks:**

| # | Check | Description |
|---|-------|-------------|
| 1 | Statement Continuity | Detect temporal gaps, overlapping dates, begin/end total mismatches |
| 2 | Balance Sum Reconciliation | Verify balance sums match `end_total_market_value` |
| 3 | Annual vs Monthly | Cross-check annual ending values against December monthly statements |
| 4 | Currency Validation | Ensure currency fields are valid (CAD/USD) and consistent |
| 5 | Exchange Rate Validation | Flag USD statements with `fx_rate=1.000000` (parser bug) |
| 6 | Account Matching | Detect duplicate accounts and auto-created accounts |
| 7 | Transaction Integrity | Check for negative quantities, prices, commissions |
| 8 | Orphaned Records | Find orphaned balances, transactions, or statements |
| 9 | Market Value Trends | Detect extreme market value changes (>50%) |
| 10 | Data Completeness | Verify required fields are populated |

**Example Usage:**

```powershell
# Run audit with terminal output
python manage.py audit_rbc_data

# Generate markdown report (saved to processed/audit/)
python manage.py audit_rbc_data --md

# Generate markdown report with custom directory
python manage.py audit_rbc_data --md --output-dir processed\audit

# Generate JSON report
python manage.py audit_rbc_data --json > audit_report.json

# Verbose output with details
python manage.py audit_rbc_data --verbose

# Auto-fix reversible issues
python manage.py audit_rbc_data --fix
```

**Markdown Report Format:**

Reports are saved as `RBC_pdf_import_audit_YYYYMMDDHHMMSS.md` in the `processed/audit/` directory (configurable via `--output-dir`).

Example report structure:
```markdown
# RBC PDF Import Audit Report

## Summary
| Metric | Value |
|--------|-------|
| Checks Run | 10 |
| Errors | 15 |
| Warnings | 42 |
| Passed | 7 |

**Status**: ⚠️ **WARNINGS** — 42 warnings found

## Database Statistics
| Table | Count |
|-------|-------|
| RbcMonthly | 1,203 |
| RbcMonthlyBalance | 6,070 |

## Errors
### 5. Exchange Rate Validation
1. 90000004 USD 2026-04-30: fx_rate=1.000000 (should be ~1.20-1.50 for USD)
   - **account**: `90000004`
   - **date**: `2026-04-30`
   - **fx_rate**: `1.000000`

## Recommendations
### Immediate Actions Required
- **Exchange Rates**: USD statements with fx_rate=1.000000 need correction.
- **Balance Sums**: Reconcile balance discrepancies.
```

**Best Practices:**

1. **Run after every import** — Catch issues before they corrupt reports
2. **Review errors first** — Fix parser issues, then re-import
3. **Check exchange rates** — USD statements with fx_rate=1.000000 indicate parser bugs
4. **Monitor trends** — Large market value changes may be legitimate (LIRA distributions) but should be reviewed
5. **Maintain continuity** — Missing statements create gaps in reporting

---

## 7. Admin Interface

**Access**: http://localhost:8000/admin/

### Creating a Superuser

```powershell
python manage.py createsuperuser
```

### Admin Sections

| Section | Purpose |
|---------|---------|
| **Branding Configuration** | Customize colors, logo, tagline, footer |
| **Accounts** | Manage brokerage accounts |
| **Transactions** | View/edit all transactions |
| **Transaction Types** | Configure transaction type definitions |
| **Broker Statements** | Track imported statements |
| **Security Universe** | Manage tracked securities |
| **Market Price History** | View price data |
| **Volatility Summary** | View volatility calculations |
| **FX Rates** | Manage exchange rates |
| **Economic Indicators** | Manage macro indicators |
| **Strategy Recommendations** | View risk recommendations |
| **Alternative Portfolios** | Manage alternative scenarios |
| **Alert Configurations** | Configure alert thresholds |
| **Audit Trail** | View system audit logs |

### Admin Best Practices

1. **Regular Audits**: Run `audit_retention_check` monthly
2. **Data Quality**: Monitor rejection logs for import errors
3. **Security**: Review login attempts and access logs
4. **Branding**: Update branding when company identity changes

---

## 8. Troubleshooting

### Common Issues

#### All Prices Show Dashes (—)

**Check logs**:
```powershell
Select-String -Path logs/portfolio_manager.log -Pattern "fetch_last_prices" -Context 5
```

**Possible Causes**:
1. **Internet Connection**: Look for `ConnectionError`, `timeout`
2. **Invalid Tickers**: Look for `✗ No price available for SYMBOL`
3. **yfinance Issue**: Look for `ModuleNotFoundError`

**Solution**:
```powershell
pip install --upgrade yfinance
```

#### Import Fails

**Check rejection logs**:
```powershell
Get-ChildItem logs\rejection_*.csv
```

**Common Issues**:
- Invalid PDF format
- Missing statement date
- Account not found
- Duplicate transactions

#### Database Connection Errors

**Check**:
1. SQL Server is running
2. `.env` credentials are correct
3. ODBC Driver 18 is installed
4. Firewall allows port 1433

#### Login Rate Limited

**Check**:
```powershell
# Clear cache
python manage.py shell -c "from django.core.cache import cache; cache.clear()"
```

### Log Locations

| Log | Path |
|-----|------|
| Main Application | `logs/portfolio_manager.log` |
| Rotated Logs | `logs/portfolio_manager.log.1`, `.2`, etc. |
| Rejection Logs | `logs/rejection_YYYY_MM.csv` |
| JSON Logs | `logs/json_logs/` |

### Log Monitoring

```powershell
# Real-time monitoring
Get-Content logs/portfolio_manager.log -Wait -Tail 50

# View recent entries
Get-Content logs/portfolio_manager.log -Tail 100

# Search for errors
Select-String -Path logs/portfolio_manager.log -Pattern "ERROR|✗"
```

---

## 9. Security

### Authentication

- **Rate Limiting**: Configurable login attempt limits (default: 5 attempts per 15 minutes)
- **Session Security**: HTTP-only cookies, CSRF protection
- **Password Policy**: Enforced by Django

### Production Security Checklist

| Setting | Production Value |
|---------|-----------------|
| `DEBUG` | `false` |
| `DJANGO_SECRET_KEY` | Secure random key |
| `ALLOWED_HOSTS` | Your domain names |
| `DB_PASSWORD` | Strong password |
| `SESSION_COOKIE_SECURE` | `true` |
| `CSRF_COOKIE_SECURE` | `true` |
| `SECURE_SSL_REDIRECT` | `true` |
| `SECURE_HSTS_SECONDS` | `31536000` |
| `CACHE_BACKEND` | Redis backend |

### Data Protection

- `.env` file is gitignored (never committed)
- Database user has minimal permissions (never use `sa`)
- File uploads validated (PDF magic bytes check)
- Path traversal prevention in file handlers

---

## 10. Testing

### Run All Tests

```powershell
python manage.py test --settings=portfolio_manager.settings_test
```

### Test Categories

| Category | Location | Count |
|----------|----------|-------|
| Core Tests | `core/tests/` | ~300 |
| Import Tests | `imports/tests/` | ~150 |
| Risk Tests | `risk/tests/` | ~100 |
| Property-Based | `risk/tests/test_property_based.py` | 17 |
| Performance | `risk/tests/test_performance.py` | 7 |
| **Total** | | **611** |

### Property-Based Tests

Use Hypothesis to verify mathematical properties:
- Black-Scholes pricing correctness
- VaR reduction bounds
- Technical indicator validity
- Option cost estimation accuracy

### Performance Regression Tests

Fail if performance degrades >10% from baselines:
- Monte Carlo simulation speed
- Volatility calculation speed
- Technical indicator speed
- Black-Scholes pricing speed

---

## Appendix A: Account Categories

| Category | Description | Tax Treatment |
|----------|-------------|---------------|
| **RRSP** | Registered Retirement Savings Plan | Tax-deferred |
| **LIRA** | Locked-In Retirement Account | Tax-deferred, locked |
| **TFSA** | Tax-Free Savings Account | Tax-free growth |
| **TAXABLE** | Taxable Investment Account | Taxable annually |
| **RESP** | Registered Education Savings Plan | Tax-deferred |
| **OTHER** | Other Account Type | Varies |

## Appendix B: Transaction Types

| Code | Label | Affects Position | Affects Cash |
|------|-------|-----------------|--------------|
| BUY | Buy | Yes | Yes |
| SELL | Sell | Yes | Yes |
| DIV | Dividend | No | Yes |
| DIST | Distribution | No | Yes |
| INT | Interest | No | Yes |
| WITHTAX | Withholding Tax | No | Yes |
| TAX | Tax | No | Yes |

## Appendix C: Supported Symbols

The security universe tracks symbols including:

**US ETFs/Stocks**: AAPL, GOOG, MA, NVDA, QQQ, RBF2010, RBF2014, SGOV, SMH, SOXX, SPY, V  
**Canadian ETFs**: CGDV, ETHA, IGM, IWY, ICSH, VDY, VFV, VOO, XDIV, XEQT  
**Hedges**: SOXS, SOXL, PSQ, SPXS  
**Crypto**: IBIT, BTC-USD  
**FX**: USDCAD=X  

*Update the security universe using*:
```powershell
python manage.py seed_rm_universe
```

---

**End of User Manual**

For additional support, refer to:
- [Admin & Branding Guide](ADMIN_BRANDING_GUIDE.md)
- [Deployment Guide](DEPLOYMENT.md)
- [Security Guide](SECURITY.md)
- [Logging Guide](LOGGING_GUIDE.md)
- [Implementation Plan](IMPLEMENTATION_PLAN.md)
