# Code Review — Portfolio Manager

> **Date**: 2025-06-14
> **Scope**: Full codebase review — `core/`, `imports/`, `portfolio_manager/`, templates
> **Status**: Review only — nothing has been fixed yet

---

## Table of Contents

1. [Security](#1-security)
2. [Bugs & Potential Bugs](#2-bugs--potential-bugs)
3. [Code Quality](#3-code-quality)
4. [Performance](#4-performance)
5. [Architecture & Design](#5-architecture--design)
6. [Testing](#6-testing)
7. [Documentation](#7-documentation)
8. [Dependencies & Configuration](#8-dependencies--configuration)

---

## 1. Security

### 1.1 DEBUG Mode — Environment Controlled ✅ (Already Fixed)
- **Status**: Already fixed in a previous iteration. `DEBUG` is now controlled via environment variable, defaults to `False`.
- **Reference**: `portfolio_manager/settings.py`

### 1.2 SQL Injection — Transaction Export CSV (Medium Risk) ✅ FIXED
**File**: `core/views.py` — `_apply_transaction_filters`

The export views accept a `broker` filter parameter that flows into the ORM query. While Django's ORM parameterizes queries (preventing true SQL injection), the `broker` value was not validated against known sources.

**Status**: Fixed — `_apply_transaction_filters` now validates `broker` against existing `BrokerStatement` sources and clears the filter if not found:

```python
if broker:
    broker_exists = BrokerStatement.objects.filter(source=broker).exists()
    if not broker_exists:
        logger.warning(f'Filter request for non-existent broker: {broker}')
        broker = ''
```

### 1.3 Missing CSRF Protection on Auto-Import Endpoint (Low Risk)
**File**: `imports/views.py`

If the import views accept POST requests, verify that CSRF middleware is enabled. Django's CSRF protection is enabled by default, but custom API endpoints sometimes bypass it.

### 1.4 Rate Limiting Only on Login (Medium Risk)
**File**: `core/views.py` — `_check_login_attempts`, `_record_login_attempt`

Rate limiting is implemented only on the login view. Other potentially dangerous operations (file deletion, transaction deletion, bulk imports) have no rate limiting:

- `account_delete` view
- Any file operations in `imports/views.py`

**Recommendation**: Extend rate limiting to all state-changing operations, not just authentication.

### 1.5 Sensitive Data in .env Committed to Git (High Risk)
**File**: `.env`

The `.env` file appears to be committed to the repository (it's visible in the workspace). This file contains `SECRET_KEY` and database credentials.

**Recommendation**: 
- Add `.env` to `.gitignore` immediately
- Rotate all credentials that may have been exposed
- Use a secrets manager in production

### 1.6 No Authorization Checks on Views (Medium Risk)
**File**: `core/views.py`

Most views rely on `@login_required` but there are no object-level permissions. Any authenticated user can:
- View all transactions regardless of account ownership
- Modify or delete any account
- Access all import history

**Recommendation**: Implement object-level permissions so users can only access their own data. Consider adding an `owner` field to Transaction or using Account.owner for access control.

---

## 2. Bugs & Potential Bugs

### 2.1 Bare `except Exception:` Swallows Errors (High Impact) ✅ FIXED
**Files**: `core/utils.py` (5 occurrences), `imports/parsers.py` (7 occurrences)

There were **12 bare `except Exception:` blocks** across the codebase.

**Status**: Fixed — all 5 blocks in `utils.py` now log descriptive messages via `logger.exception()` or `logger.debug()`. The parsers.py blocks already had `logger.warning()` calls (they were false positives in the original review).

```python
# Before: silent pass in compute_series_by_account inner loop
except Exception:
    pass

# After:
except Exception:
    logger.debug(
        "Skipping symbol %s on %s for account %s — price lookup failed",
        symbol, tx_date, account_id,
    )
```

```python
# Before: generic message
except Exception:
    logger.exception("Error in compute_portfolio_value_series")

# After:
except Exception:
    logger.exception("Error in compute_portfolio_value_series — returned empty series")
```

### 2.2 `import django` Without `django.setup()` in Standalone Script (Bug)
**File**: `imports/auto_import.py` — line 15

The standalone `auto_import.py` imports Django but may not call `django.setup()` before using models. If this script is run outside of a Django management command context, model access will fail.

**Recommendation**: Add `django.setup()` after the Django import if the script is meant to be run standalone.

### 2.3 `import pandas` Not in `requirements.txt` (Bug) ✅ FIXED
**File**: `requirements.txt`

`pandas` was imported and used in multiple files but was **not listed in `requirements.txt`**.

**Status**: Fixed — `pandas>=2.0` added to `requirements.txt`.

### 2.4 yfinance Dependency Version Too Loose (Risk) ✅ FIXED
**File**: `requirements.txt`

`yfinance>=0.2` was very permissive. yfinance has had breaking API changes between minor versions.

**Status**: Fixed — pinned to `yfinance>=0.2.32,<0.5.0`.

### 2.5 No Unique Constraint on tx_hash (Data Integrity Bug)
**File**: `core/models.py` — `Transaction` model

The `tx_hash` field is used for deduplication in the import logic, but there is **no database-level unique constraint**. This means:
- Duplicate transactions can be inserted directly into the database
- The hash-based deduplication only works at the application level during import
- Concurrent imports could create duplicates

**Recommendation**: Add `unique=True` to the `tx_hash` field:
```python
tx_hash = models.CharField(max_length=64, unique=True, blank=True, null=True)
```
Then run a migration to enforce uniqueness and handle existing duplicates.

### 2.6 `default=timezone.now` on DateField (Bug)
**File**: `core/models.py` — `BrokerStatement.file_date`

If `file_date` is a `DateField` with `default=timezone.now`, this will cause a type mismatch because `timezone.now()` returns a datetime, not a date. Django may auto-convert, but this is fragile.

**Recommendation**: Use `default=date.today` (no parentheses — it's passed as a callable).

### 2.7 Floating-Point Division in Financial Calculations (Risk)
**File**: `core/utils.py`

Several financial calculations use `/` for division. While the code uses `Decimal` for monetary amounts, if any intermediate calculation produces a float, precision will be lost.

**Recommendation**: Audit all division operations in financial calculations to ensure they use `Decimal` division (`Decimal.__truediv__`).

### 2.8 Cache Key Collision Between Symbols (Risk)
**File**: `core/utils.py` — price caching

The price cache uses symbol as the key. If the same symbol appears in different contexts (e.g., different brokers, different currencies), cache entries could collide.

**Recommendation**: Include broker or currency in the cache key: `f"price_{broker}_{symbol}_{currency}"`

---

## 3. Code Quality

### 3.1 `core/utils.py` — 1100+ Lines (Single Responsibility Violation)
**File**: `core/utils.py`

This file contains **1100+ lines** and handles:
- Date series generation
- Price fetching (DB + yfinance)
- Portfolio value computation
- Cost basis calculation
- Realized P&L (FIFO + average cost)
- Income/distribution computation
- Position calculation
- Benchmark data loading
- By-account series computation

**Recommendation**: Split into a module with separate files:
```
core/
  utils/
    __init__.py
    dates.py          # _generate_date_series, date utilities
    prices.py         # _load_prices_from_db, _load_prices_from_yfinance, fetch_last_prices
    portfolio.py      # compute_portfolio_value_series, compute_positions
    pnl.py            # compute_realised_pnl, compute_cost_basis_series
    income.py         # compute_income_series
    benchmarks.py     # _load_benchmark_prices
    by_account.py     # compute_series_by_account
```

### 3.2 `core/views.py` — 1100+ Lines (Single Responsibility Violation)
**File**: `core/views.py`

Similar issue — 1100+ lines mixing:
- Security helpers
- Auth views
- Transaction views
- Account CRUD views
- Chart data views
- Export views
- Summary views

**Recommendation**: Split into view modules or use class-based views for CRUD operations:
```
core/
  views/
    __init__.py
    auth.py           # login, logout
    transactions.py   # list, export
    accounts.py       # CRUD operations
    charts.py         # portfolio_chart, portfolio_chart_data
    exports.py        # CSV/Excel export
    summaries.py      # portfolio_summary, realised_pnl, income_summary
    security.py       # _check_login_attempts, _get_client_ip
```

### 3.3 Inconsistent Exception Handling Patterns
**Files**: `core/utils.py`, `core/views.py`, `imports/parsers.py`

Three patterns observed:
1. **Bare `except Exception:`** — swallows errors silently (parsers.py)
2. **`except Exception as e:` with `logger.warning`** — logs but may lose context (utils.py)
3. **`except Exception as exc:`** — different variable name (utils.py)

**Recommendation**: Standardize on:
```python
logger.exception("Description of what failed: %s", str(e))
# or re-raise with context
raise RuntimeError("Failed to compute X") from e
```

### 3.4 Magic Numbers in Configuration
**Files**: Multiple

```python
# config.ini has poll_interval = 60
# Hardcoded page sizes: 50
# Cache timeouts: 900, 3600
```

While some are in `config.ini`, others may be hardcoded. Ensure all tunable values are configurable.

### 3.5 Duplicate Code in Parser Type Mappings
**File**: `imports/parsers.py`

```python
IB_TYPE_MAP = {'BT': 'BUY', 'ST': 'SELL', ...}
_RBC_TYPE_MAP = {'BUY': 'BUY', 'SELL': 'SELL', ...}
```

Both maps serve the same purpose (normalizing broker-specific type strings). The normalization logic is duplicated.

**Recommendation**: Use a single canonical type mapping that can be extended per broker:
```python
TYPE_MAPS = {
    'IB': {'BT': 'BUY', 'ST': 'SELL', ...},
    'RBC': {'BUY': 'BUY', 'SELL': 'SELL', ...},
}

def normalize_type(broker, raw_type):
    return TYPE_MAPS.get(broker, {}).get(raw_type, raw_type)
```

### 3.6 `date_type` Import Hack
**File**: `core/utils.py:417, 425`

```python
from datetime import date as date_type
```

This is a workaround for a missing type annotation. The proper fix is to use `from __future__ import annotations` and type hints directly.

**Recommendation**: Add `from __future__ import annotations` at the top of utils.py and use `date` directly in type hints.

### 3.7 Hardcoded Color Palette in Template
**File**: `core/templates/portfolio_chart.html`

The color palette is hardcoded in JavaScript:
```javascript
const COLORS = {
    market_value: '#2196F3',
    cost_basis: '#4CAF50',
    // ...
};
```

**Recommendation**: Allow color customization through BrandingConfig model (which already exists for danger_color, etc.).

---

## 4. Performance

### 4.1 N+1 Query in Transaction List
**File**: `core/views.py` — `transaction_list`

If transactions are rendered with their associated statement or account data without `select_related`/`prefetch_related`, each row triggers an additional query.

**Recommendation**: Add `select_related('statement', 'account')` to the queryset:
```python
qs = Transaction.objects.select_related('statement', 'account').all()
```

### 4.2 N+1 Query in Portfolio Summary
**File**: `core/views.py` — `portfolio_summary`

The portfolio summary iterates over accounts and computes values. If each account triggers a separate query for transactions, this is O(n) queries.

**Recommendation**: Use `prefetch_related('transactions')` and compute in memory, or use annotated aggregations.

### 4.3 yfinance Called Without Concurrency
**File**: `core/utils.py` — `_load_prices_from_yfinance`

Prices are fetched sequentially per symbol. For a portfolio with 50+ symbols, this could take minutes.

**Recommendation**: Use `concurrent.futures.ThreadPoolExecutor` to fetch prices in parallel:
```python
with ThreadPoolExecutor(max_workers=5) as executor:
    results = list(executor.map(fetch_price, symbols))
```

### 4.4 No Database Index on `tx_hash`
**File**: `core/models.py`

The `tx_hash` field is used for deduplication lookups (`Transaction.objects.filter(tx_hash=hash)`) but has no database index.

**Recommendation**: Add `db_index=True` to the `tx_hash` field.

### 4.5 Full Table Scan on `compute_realised_pnl`
**File**: `core/utils.py` — `compute_realised_pnl`

The function fetches all transactions for an account and processes them in Python. For accounts with thousands of transactions, this loads everything into memory.

**Recommendation**: Use database-level annotations for initial filtering and only fetch the transactions needed for the current period.

### 4.6 Cache Miss Storm on Price Fetch
**File**: `core/utils.py`

When the cache expires, all requests for the same symbol will simultaneously hit yfinance, potentially hitting rate limits.

**Recommendation**: Implement cache warming or a distributed lock to prevent thundering herd:
```python
from django.core.cache import cache
lock_key = f"lock_price_{symbol}"
if cache.get(lock_key):
    # Wait for existing fetch
    time.sleep(0.5)
    return get_cached_price(symbol)
cache.set(lock_key, True, timeout=30)
try:
    price = fetch_and_cache(symbol)
finally:
    cache.delete(lock_key)
```

---

## 5. Architecture & Design

### 5.1 Mixed ORM Patterns
**Files**: `core/views.py`, `core/utils.py`

Some queries use Django ORM (`Transaction.objects.filter()`), while others fetch raw data and process in Python. The `compute_realised_pnl` function implements FIFO logic entirely in Python rather than using window functions.

**Recommendation**: For simple aggregations, use ORM annotations. For complex FIFO calculations, consider using Django's `RawSQL` or a database view for the heavy lifting.

### 5.2 Background Watcher Thread Management
**File**: `imports/apps.py`

The watcher is started in `Apps.ready()` which runs on every Django process start. In production with multiple gunicorn/uwsgi workers, this could spawn multiple watcher threads.

**Recommendation**: Use a proper task queue (Celery, Django-Q) or a singleton mechanism:
```python
import socket
_hostname = socket.gethostname()
if settings.WATCHER_HOST == _hostname:  # Only run on one worker
    start_background_watcher()
```

### 5.3 No Migration Strategy for Production
**Observation**: Migrations exist in `core/migrations/` but there's no documented migration strategy for production deployments.

**Recommendation**: 
- Document the migration process
- Add `makemigrations` and `migrate` to the deployment script
- Consider using South's `--run-syncdb` for initial setup

### 5.4 Singleton Pattern for BrandingConfig
**File**: `core/models.py` — `BrandingConfig.load()`

The singleton pattern is implemented via a class method `load()` rather than Django's built-in singleton patterns. This works but is non-standard.

**Recommendation**: Consider using a Django signal to ensure only one instance exists, or use a module-level singleton with proper thread safety.

### 5.5 No API Layer
**Observation**: All business logic is in views and utils. There's no separation between the presentation layer and the business logic layer.

**Recommendation**: Introduce a services layer:
```
core/
  services/
    portfolio_service.py    # Business logic for portfolio calculations
    import_service.py       # Business logic for imports
    report_service.py       # Business logic for reports
```

---

## 6. Testing

### 6.1 Test Coverage Gaps
**Observation**: 131 tests pass, but coverage gaps exist:

| Area | Coverage | Notes |
|------|----------|-------|
| `core/utils.py` | Partial | `test_performance_chart.py` covers chart functions, `test_realised_pnl.py` covers P&L |
| `imports/parsers.py` | Partial | `test_import_statements.py` exists but may not cover all edge cases |
| `core/views.py` | Partial | `test_views.py` covers basic view rendering, not all view logic |
| `imports/watcher.py` | **Missing** | No tests for the background watcher daemon |
| `imports/rejection_log.py` | **Missing** | No tests for rejection logging |
| `core/admin.py` | **Missing** | No admin tests |
| `core/forms.py` | **Missing** | No form validation tests |
| `core/context_processors.py` | **Missing** | No context processor tests |

**Recommendation**: Add tests for:
- Watcher thread (mock file system operations)
- Rejection logging (verify CSV output)
- Form validation (invalid data handling)
- Admin operations (singleton enforcement)

### 6.2 No Integration Tests
**Observation**: Tests use Django's TestCase with in-memory database but don't test the full import pipeline (file drop → watcher → parser → database).

**Recommendation**: Add integration tests that simulate the full import workflow:
```python
def test_full_import_pipeline(self):
    # Drop CSV file in inbox
    # Wait for watcher to process
    # Verify transactions in database
```

### 6.3 No Performance Tests
**Observation**: No tests for query performance or calculation correctness with large datasets.

**Recommendation**: Add tests with 10,000+ transactions to verify:
- Query performance
- Memory usage
- Calculation accuracy

### 6.4 Test Settings Separate from Main Settings
**File**: `portfolio_manager/settings_test.py`

Having separate test settings is good practice, but verify they stay in sync. Key settings like `DATABASES` differ, but other settings (middleware, installed apps) should be identical.

**Recommendation**: Use `import settings` and override only the differences:
```python
from .settings import *
DATABASES = {...}  # Override with SQLite for tests
```

---

## 7. Documentation

### 7.1 No README for Developers
**Observation**: `README.md` exists but appears to focus on end-user documentation. There's no developer guide covering:
- Project structure
- How to add a new feature
- How to run tests
- How to deploy
- Database schema documentation

**Recommendation**: Add a `DEVELOPMENT.md` file covering:
- Setup instructions
- Running tests
- Making migrations
- Code style guidelines
- Feature development workflow

### 7.2 No API Documentation
**Observation**: The `core.utils` module has no docstrings on its public functions.

**Recommendation**: Add Google-style or NumPy-style docstrings:
```python
def compute_portfolio_value_series(account_id: str, date_from: date, date_to: date, granularity: str = 'monthly') -> list[dict]:
    """Compute the market value of a portfolio over time.
    
    Args:
        account_id: The account identifier.
        date_from: Start date for the series.
        date_to: End date for the series.
        granularity: One of 'daily', 'weekly', 'monthly', 'annual'.
        
    Returns:
        List of dicts with 'date' and 'market_value_cad' keys.
    """
```

### 7.3 No CHANGELOG
**Observation**: No `CHANGELOG.md` to track changes across versions.

**Recommendation**: Adopt [Keep a Changelog](https://keepachangelog.com/) format.

### 7.4 Spec Documents Are Out of Date
**Observation**: The `specs/` directory contains feature specifications that were implemented in previous sessions. These should be marked as "Implemented" or archived.

**Recommendation**: Add an "Status" column to the spec documents or move implemented specs to `specs/implemented/`.

---

## 8. Dependencies & Configuration

### 8.1 `requirements.txt` Incomplete
**File**: `requirements.txt`

Missing packages that are imported but not listed:
- `pandas` — used in `views.py`, `utils.py`, `fetch_historical_prices.py`
- `openpyxl` — listed but also used for Excel export (good)
- No dev dependencies (pytest, coverage, flake8, etc.)

**Recommendation**: Split into `requirements.txt` and `requirements-dev.txt`:
```
# requirements.txt
django>=5.1
django-mssql-backend>=2.0
python-dotenv>=1.0
openpyxl>=3.1
yfinance>=0.2.32
pandas>=2.0

# requirements-dev.txt
-r requirements.txt
pytest>=7.0
pytest-django>=4.5
coverage>=7.0
flake8>=6.0
```

### 8.2 No `pyproject.toml` or `setup.py`
**Observation**: The project has no Python project metadata. This affects:
- Package installation
- Tool configuration (flake8, black, mypy)
- Entry points for management commands

**Recommendation**: Add a minimal `pyproject.toml`:
```toml
[project]
name = "portfolio-manager"
version = "0.1.0"

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "portfolio_manager.settings_test"

[tool.flake8]
max-line-length = 120
exclude = ["migrations", ".git"]
```

### 8.3 `config.ini` Path Resolution
**File**: `config.ini`

The `inbox_dir` and `processed_dir` are left blank to use defaults. The default path resolution should be documented and tested.

**Recommendation**: Document the default paths and add a validation check at startup:
```python
# In settings.py or management command
if not os.path.exists(INBOX_DIR):
    os.makedirs(INBOX_DIR)
```

### 8.4 Logging Configuration Could Be Improved
**File**: `portfolio_manager/settings.py`

Logging uses a rotating file handler which is good, but:
- No log format specification
- No log level configuration via environment
- Console and file handlers may have different levels

**Recommendation**: Add structured logging with JSON format for production:
```python
LOGGING = {
    'formatters': {
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '%(asctime)s %(name)s %(levelname)s %(message)s',
        },
    },
    'handlers': {
        'file': {
            'level': os.getenv('LOG_LEVEL', 'INFO'),
            'class': 'logging.handlers.RotatingFileHandler',
            'formatter': 'json',
        },
    },
}
```

---

## Summary of Priority Issues

| Priority | Issue | File | Impact |
|----------|-------|------|--------|
| **Critical** | `.env` committed to git | `.env` | Credential exposure |
| ✅ **Fixed** | Missing `pandas` in requirements.txt | `requirements.txt` | Application crash on fresh install |
| ✅ **Fixed** | Bare `except Exception:` swallows errors | `core/utils.py` | Silent failures, hard to debug |
| ✅ **Fixed** | Broker filter not validated | `core/views.py` | Enumeration / unexpected queries |
| ✅ **Fixed** | yfinance version too loose | `requirements.txt` | Breaking API changes |
| **High** | No unique constraint on `tx_hash` | `core/models.py` | Duplicate transactions possible |
| **High** | No authorization on views | `core/views.py` | Any user sees all data |
| **Medium** | N+1 queries in views | `core/views.py` | Performance degradation |
| **Medium** | No rate limiting on state-changing ops | `core/views.py` | Abuse potential |
| **Medium** | No watcher/rejection tests | `imports/` | Undetected regressions |
| **Low** | Large files (utils.py, views.py) | `core/` | Maintainability |
| **Low** | No developer documentation | Project-wide | Onboarding friction |

---

## Notes

- **131 tests pass** — the test suite is healthy and provides good regression protection
- **Security improvements** in previous sessions (DEBUG mode, rate limiting on login, CSRF, path traversal prevention) are solid
- **Chart fixes** from previous sessions (10 bugs resolved) are well-tested
- The codebase is functional and the architecture is sound — most issues are about hardening, testing, and maintainability
