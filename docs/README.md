# Portfolio Manager

Modern, professional Django-based portfolio management application for tracking broker statements, transactions, and risk analytics across multiple brokerage accounts.

## ✨ Features

- 📊 **Multi-Account Portfolio Tracking** — Manage accounts across owners (Tom, Susan, Joint) and categories (RRSP, TFSA, LIRA, Taxable, RESP)
- 💼 **Multi-Broker Support** — Interactive Brokers (IB) and RBC Royal Bank
- 📈 **Real-Time Portfolio Values** — Yahoo Finance (yfinance) price fetching with caching
- 📉 **Risk Analytics Engine** — Monte Carlo simulation, stress testing, backtesting, regime detection, volatility analysis
- 📥 **Automated Statement Import** — File watching, PDF parsing (RBC monthly/annual), inbox monitoring
- 📋 **Comprehensive Reporting** — Portfolio summary, P&L (FIFO & avg-cost), income summary, tax summary, performance charts
- 🔔 **Alerting System** — Strategy recommendations, urgency scoring, audit trail
- 🎨 **Fully Customizable Branding** — Colors, logos, themes via admin interface
- 📱 **Modern Responsive UI** — Bootstrap 5 with Chart.js visualizations
- 🔐 **Secure Admin Interface** — Rate-limited login, audit logging, role-based access

## Project Overview

This application provides a comprehensive solution for importing, managing, and analyzing financial transaction data from brokerage statements. The system is designed to:

- Store and track broker statements (IB or RBC sources)
- Parse and import transaction data automatically via file watchers or manual PDF upload
- Calculate real-time portfolio values using Yahoo Finance
- Generate P&L reports with FIFO and average-cost methods
- Track income (dividends, interest, distributions)
- Visualize performance with interactive charts
- Perform advanced risk analytics (Monte Carlo, stress testing, backtesting)
- Provide customizable branding for professional deployment

## Tech Stack

- **Django**: 5.1.x (latest stable)
- **Database**: Microsoft SQL Server 2025 (dev: PortfolioDEV) / 2022 (prod: TaxPortfolioPRD)
- **Python**: 3.12
- **UI Framework**: Bootstrap 5 with responsive design
- **Price Data**: yfinance (Yahoo Finance API)
- **Charts**: Chart.js
- **Icons**: Bootstrap Icons
- **PDF Parsing**: PyMuPDF + Vision Language models (Qwen) for RBC statements
- **Testing**: Django TestCase + Hypothesis (property-based) + performance regression tests

## Quick Start

### 1. Clone or Access the Project

```powershell
cd C:\AI-DevTeam\projects\portfolio_manager
```

### 2. Create Virtual Environment

```powershell
python -m venv venv
venv\Scripts\activate
```

### 3. Install Dependencies

```powershell
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Copy the example environment file:
```powershell
copy .env.example .env
```

Edit `.env` and fill in your database credentials (see `.env.example` for required variables)

### 5. Configure Database

Ensure your SQL Server database exists, or create it:

```sql
CREATE DATABASE PortfolioDev;
```

Update your `.env` file with the correct database credentials:

```
DB_HOST=localhost
DB_PORT=1433
DB_NAME=PortfolioDev
DB_USER=your_username
DB_PASSWORD=your_password
```

### 6. Run Migrations

```powershell
python manage.py migrate
```

### 7. Create Superuser

```powershell
python manage.py createsuperuser
```

### 8. Start Development Server

```powershell
python manage.py runserver
```

Visit:
- **Application**: http://localhost:8000/
- **Admin Interface**: http://localhost:8000/admin/

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [Admin & Branding Guide](ADMIN_BRANDING_GUIDE.md) | Complete guide to customization, admin features, and branding configuration |
| [Deployment Guide](DEPLOYMENT.md) | Production deployment instructions (Linux, Nginx, Gunicorn, SSL) |
| [Security Guide](history/SECURITY.md) | Security hardening, rate limiting, authentication, authorization |
| [Logging Guide](LOGGING_GUIDE.md) | Log locations, troubleshooting, yfinance diagnostics |
| [Implementation Plan](IMPLEMENTATION_PLAN.md) | Development phases and completion status |
| [Next Features](history/NEXT_FEATURES.md) | Prioritized enhancement roadmap |
| [User Manual](history/USER_MANUAL.md) | Complete user guide with audit documentation |
| [Specs Directory](specs/) | Feature specifications (M0–M8, Phase A/B) |

## Project Structure

```
portfolio_manager/
├── core/                    # Core business logic and models
│   ├── models.py            # Account, Transaction, BrokerStatement, RbcMonthly, etc.
│   ├── views.py             # Dashboard, portfolio, transactions, reports
│   ├── views_accounts.py    # Account CRUD operations
│   ├── views_auth.py        # Login/logout
│   ├── views_portfolio.py   # Portfolio summary, P&L, charts
│   ├── views_reports.py     # Income, tax, import history
│   ├── views_transactions.py # Transaction list, export (CSV/Excel)
│   ├── utils/               # Modular utilities
│   │   ├── currencies.py    # Currency conversion helpers
│   │   ├── dates.py         # Date parsing utilities
│   │   ├── portfolio.py     # Portfolio calculation helpers
│   │   └── prices.py        # Price fetching from yfinance
│   ├── management/commands/ # Core management commands (7)
│   ├── templates/           # Core app templates
│   └── tests/               # Core app tests
├── imports/                 # Statement import functionality
│   ├── parsers.py           # CSV/Excel parsers
│   ├── parsers_pdf.py       # RBC PDF monthly parser
│   ├── parsers_annual.py    # RBC annual report parser
│   ├── parsers_annual_vl.py # Annual report parser with Vision Language model
│   ├── parsers_monthly_vl.py # Monthly parser with Vision Language model
│   ├── auto_import.py       # Background file watcher
│   ├── watcher.py           # File system watcher
│   ├── rejection_log.py     # Import rejection tracking
│   ├── management/commands/ # Import commands (4)
│   └── templates/           # Import templates
├── risk/                    # Risk analytics module
│   ├── models.py            # SecurityUniverse, MarketPriceHistory, VolatilitySummary, etc.
│   ├── views/               # Risk module views
│   │   ├── security.py      # Security detail pages
│   │   ├── strategy.py      # Strategy recommendations
│   │   ├── alternatives.py  # Alternative portfolios
│   │   ├── compare.py       # Portfolio comparison
│   │   ├── stress.py        # Stress test results
│   │   ├── exports.py       # Strategy export
│   │   └── explain.py       # Audit trail explainability
│   ├── services/            # Risk calculation services
│   ├── management/commands/ # Risk commands (26)
│   ├── templates/           # Risk module templates
│   ├── templatetags/        # Custom template tags
│   └── tests/               # Risk tests (property-based + performance)
├── portfolio_manager/       # Project settings
│   ├── settings.py          # Django settings (env-driven)
│   ├── settings_test.py     # Test settings (SQLite)
│   ├── urls.py              # Project URL routing
│   └── wsgi.py              # WSGI config
├── scripts/                 # Database scripts
├── specs/                   # Feature specifications (M0–M8)
├── inbox/                   # Auto-import inbox directory
├── processed/               # Processed statements directory
├── logs/                    # Application logs
├── temp/                    # Temporary files
├── config.ini               # Non-secret app configuration
├── requirements.txt         # Python dependencies
└── manage.py                # Django management script
```

## Management Commands

The application includes **68 management commands** organized by module:

### Core Commands (7)
| Command | Description |
|---------|-------------|
| `fetch_historical_prices` | Fetch OHLCV price data from yfinance |
| `manage_annual_report_import` | Import RBC annual reports |
| `manage_inbox_import` | Process inbox files |
| `manage_pdf_import` | Process PDF files |
| `merge_duplicate_accounts` | Merge duplicate account records |
| `rebuild_position_history` | Rebuild position history from transactions |
| `seed_rm_universe` | Seed security universe with symbols |

### Import Commands (4)
| Command | Description |
|---------|-------------|
| `audit_fx` | Audit FX rate data quality |
| `auto_import` | Run background auto-import |
| `import_statements` | Import broker statements |
| `watch_inbox` | Start inbox file watcher |

### Data Audit Commands (1)
| Command | Description |
|---------|-------------|
| `audit_rbc_data` | Run 10 comprehensive data integrity checks on RBC imports |

**Quick Audit:**
```powershell
# Run all 10 checks
python manage.py audit_rbc_data

# Generate markdown report
python manage.py audit_rbc_data --md

# Verbose output
python manage.py audit_rbc_data --verbose
```

The audit checks: statement continuity, balance reconciliation, annual vs monthly cross-checks, currency validation, exchange rates, account matching, transaction integrity, orphaned records, market value trends, and data completeness.

### Risk Commands (26)
| Command | Description |
|---------|-------------|
| `audit_retention_check` | Audit data retention compliance |
| `calc_market_conditions` | Calculate market conditions |
| `calc_portfolio_metrics` | Calculate portfolio risk metrics |
| `calc_regime` | Detect market regime |
| `calc_security_metrics` | Calculate per-security metrics |
| `calc_strategy_recommendations` | Generate strategy recommendations |
| `calc_urgency` | Calculate urgency scores |
| `compute_volatility` | Compute volatility summaries |
| `daily_risk_refresh` | Full daily risk calculation refresh |
| `evaluate_alerts` | Evaluate and trigger alerts |
| `fetch_economic_indicators` | Fetch macro economic indicators |
| `fetch_fx_rates` | Fetch USD/CAD exchange rates |
| `fetch_yfinance_prices` | Fetch prices from yfinance |
| `import_amibroker_prices` | Import Amibroker CSV data |
| `refresh_market_data` | Refresh all market data |
| `run_backtest` | Run backtesting engine |
| `run_montecarlo` | Run Monte Carlo simulation |
| `run_stress_test` | Run stress test scenarios |
| `seed_alert_configs` | Seed alert configurations |
| `seed_historical_scenarios` | Seed historical scenarios |
| `seed_instrument_catalogue` | Seed instrument catalogue |
| `seed_owner_config` | Seed owner configurations |
| `seed_regime_sensitivity` | Seed regime sensitivity data |
| `seed_rm_universe_update` | Update risk universe |
| `seed_scoring_config` | Seed scoring configurations |
| `seed_security_drivers` | Seed security drivers |

### Django Built-in Commands (31)
Standard Django commands: `migrate`, `createsuperuser`, `shell`, `makemigrations`, `test`, `collectstatic`, `runserver`, etc.

## Configuration

### Environment Variables (.env)

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DJANGO_SECRET_KEY` | Django secret key | — | **Yes** |
| `DEBUG` | Debug mode (true/false) | false | No |
| `DB_HOST` | SQL Server host | localhost | No |
| `DB_PORT` | SQL Server port | 1433 | No |
| `DB_NAME` | Database name | PortfolioDev | No |
| `DB_USER` | Database username | — | **Yes (prod)** |
| `DB_PASSWORD` | Database password | — | **Yes (prod)** |
| `ALLOWED_HOSTS` | Comma-separated hostnames | localhost,127.0.0.1 | No |
| `TRUST_SERVER_CERTIFICATE` | Trust SQL Server cert | false | No |
| `TRANSACTIONS_PAGE_SIZE` | Pagination size | 50 | No |
| `ACCOUNT_DETAIL_PAGE_SIZE` | Account detail pagination | 50 | No |
| `DASHBOARD_RECENT_LIMIT` | Dashboard recent items | 10 | No |
| `CACHE_BACKEND` | Cache backend class | LocMemCache | No |
| `CACHE_LOCATION` | Cache connection string | — | No |
| `SESSION_COOKIE_SECURE` | Secure session cookies | false | No |
| `CSRF_COOKIE_SECURE` | Secure CSRF cookies | false | No |
| `SECURE_SSL_REDIRECT` | Redirect HTTP to HTTPS | false | No |
| `SECURE_HSTS_SECONDS` | HSTS max age | 0 | No |
| `LOGIN_ATTEMPT_LIMIT` | Login attempt limit | 5 | No |
| `LOGIN_ATTEMPT_WINDOW` | Login window (seconds) | 900 | No |

### Application Settings (config.ini)

Non-secret settings are stored in `config.ini` (committed to git):

| Section | Setting | Description |
|---------|---------|-------------|
| `[import]` | `inbox_dir` | Absolute path to inbox directory |
| `[import]` | `processed_dir` | Absolute path to processed directory |
| `[import]` | `logs_dir` | Absolute path to logs directory |
| `[import]` | `poll_interval` | Watcher poll interval (seconds) |
| `[ui]` | `transactions_page_size` | Transaction list page size |
| `[ui]` | `account_detail_page_size` | Account detail page size |
| `[cache]` | `price_timeout_seconds` | Quote cache timeout |
| `[cache]` | `historical_price_timeout_seconds` | Historical chart cache timeout |
| `[logging]` | `file_max_bytes` | Log file max size (bytes) |
| `[logging]` | `file_backup_count` | Number of backup log files |

## Testing

### Run All Tests

```powershell
python manage.py test --settings=portfolio_manager.settings_test
```

### Test Suite Summary

| Category | Tests | Status |
|----------|-------|--------|
| Core (accounts, transactions, reports) | ~300 | ✅ Passing |
| Imports (parsers, PDF, auto-import) | ~150 | ✅ Passing |
| Risk (calculations, services) | ~100 | ✅ Passing |
| Property-Based (Hypothesis) | 17 | ✅ Passing |
| Performance Regression | 7 | ✅ Passing |
| **Total** | **611** | **4 skipped, 0 failures** |

### Property-Based Tests

Located in `risk/tests/test_property_based.py` — verifies mathematical properties:
- Black-Scholes: put-call parity, monotonicity, boundary conditions
- VaR: reduction bounds, non-negativity, proportionality
- Technical indicators: SMA variance reduction, RSI bounds (0–100), MACD smoothing
- Option costs: non-negativity, quantity scaling, collar vs put costs
- Volatility: non-negativity, zero volatility for identical prices

### Performance Regression Tests

Located in `risk/tests/test_performance.py` — fails if performance degrades >10%:
- Monte Carlo GBM/jump-diffusion (10k paths)
- Volatility, SMA, RSI, MACD, Black-Scholes benchmarks

## Development Workflow

1. Create feature branches from `main`
2. Follow PEP8 coding standards
3. Add tests for new functionality
4. Submit pull requests for review
5. Human approval required before:
   - Git pushes to remote repositories
   - Database schema changes
   - Production deployments

## License

Private — tdwbiz/portfolio_manager

## Support

For issues or questions, contact the development team.

# Portfolio Manager

Django-based application for managing investment portfolios.

## Setup

1. **Create Virtual Environment**
   ```bash
   python -m venv venv
   venv\Scripts\activate  # Windows
   source venv/bin/activate  # Linux/Mac
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment**
   Copy `.env.example` to `.env` and update database credentials:
   ```bash
   copy .env.example .env  # Windows
   cp .env.example .env    # Linux/Mac
   ```

4. **Run Migrations**
   ```bash
   python manage.py migrate
   ```

5. **Create Superuser**
   ```bash
   python manage.py createsuperuser
   ```

6. **Run Server**
   ```bash
   python manage.py runserver
   ```

## Project Structure

- `core/`: Core domain models (BrokerStatement, Transaction)
- `imports/`: Statement import logic (future)
- `portfolio_manager/`: Django project settings

## Database

Uses Microsoft SQL Server via `mssql-django`.
