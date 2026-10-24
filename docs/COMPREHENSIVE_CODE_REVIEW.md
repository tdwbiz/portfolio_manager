# Portfolio Manager — Comprehensive Code Review

**Project**: Portfolio Manager (Django 5.1.15 / Python 3.11–3.12)
**Database**: MSSQL 2025 (production), SQLite in-memory (tests)
**Date**: 2025-01-24

---

## 1. Executive Summary

The Portfolio Manager project is a mature, well-structured Django application for investment portfolio management, risk analytics, and automated document import. It features:

- **Core financial operations**: Transaction management, account CRUD, portfolio valuation, PnL tracking
- **Risk analytics**: Monte Carlo simulation (GBM + Merton jump-diffusion), backtesting, VaR/CVaR, regime detection, urgency scoring, stress testing, option pricing
- **Document import**: IBKR CSV parsers, RBC PDF parsers, LLM-assisted PDF classification (Qwen2.5-VL), annual report parsing
- **Data pipeline**: yFinance integration, Amibroker price import, caching, seeding commands

**Overall assessment**: The codebase is **well-architected** with strong test coverage (415 tests passing), clear documentation, and thoughtful separation of concerns. The main areas for improvement are: hardcoded paths, file size management in `core/views.py` and `core/utils.py`, performance optimisation in `monte_carlo.py`, and removing a few duplicate code patterns.

---

## 2. Project Statistics

| Metric | Value |
|---|---|
| **Total Python files** | 180 |
| **Total lines of code** | ~34,112 |
| **Test files** | 29 |
| **Passing tests** | 415 |
| **Skipped tests** | 3 |
| **Failed tests** | 0 |
| **Management commands** | 30 (core: 7, risk: 22, imports: 4) |
| **Apps** | 3 (core, imports, risk) |

### Top 20 Largest Files (by LOC)

| File | LOC | Notes |
|---|---|---|
| `risk/models.py` | 1,333 | Large model file — could benefit from splitting |
| `core/views.py` | 1,222 | **Too large** — needs splitting |
| `imports/parsers_pdf.py` | 1,039 | Acceptable for a parser module |
| `core/utils.py` | 1,017 | **Too large** — mix of concerns |
| `risk/services/urgency_engine.py` | 829 | Acceptable for a single service |
| `risk/tests/test_data_pipeline.py` | 819 | Good test coverage |
| `risk/management/commands/seed_instrument_catalogue.py` | 748 | Large seed command |
| `risk/tests/test_monte_carlo.py` | 722 | Comprehensive tests |
| `imports/parsers_annual.py` | 689 | Acceptable |
| `risk/tests/test_dashboard_views.py` | 675 | Good test coverage |
| `core/management/commands/manage_inbox_import.py` | 594 | Complex command |
| `risk/services/monte_carlo.py` | 582 | Performance concern (nested loops) |
| `imports/parsers.py` | 564 | Acceptable |
| `risk/services/metrics.py` | 530 | Acceptable |
| `imports/management/commands/auto_import.py` | 508 | Complex command |
| `risk/management/commands/run_montecarlo.py` | 486 | Acceptable |
| `risk/admin.py` | 485 | Acceptable |
| `risk/tests/test_portfolio_metrics.py` | 477 | Good test coverage |
| `risk/services/backtest_engine.py` | 463 | Acceptable |
| `risk/management/commands/calc_portfolio_metrics.py` | 450 | Acceptable |

---

## 3. Project Structure Analysis

### 3.1 App Organisation

The project uses a standard Django multi-app structure:

| App | Responsibility | Quality |
|---|---|---|
| **core** | Accounts, transactions, branding, portfolio valuation | Good — well-documented models, proper admin |
| **imports** | CSV/PDF parsing, LLM-assisted classification, auto-import | Good — but duplicate code in views |
| **risk** | Risk analytics, backtesting, Monte Carlo, regime detection | Excellent — clean service layer, comprehensive tests |

### 3.2 Risk Services Layer

The `risk/services/` directory is well-organised with clear separation:

| Service | Purpose | Quality |
|---|---|---|
| `backtest_engine.py` | Trailing stop, protective put, collar simulations | Good — pure Python functions |
| `monte_carlo.py` | GBM, Merton jump-diffusion, Cholesky | Good — but performance concern |
| `urgency_engine.py` | Security scoring, urgency metrics | Good — config-driven |
| `regime_engine.py` | Market regime detection | Good — indicator-based |
| `metrics.py` | VaR, CVaR, beta, drawdown | Excellent — pure functions |
| `data_source.py` | Abstract price provider | Good — source-agnostic |
| `yfinance_client.py` | yFinance wrapper with retry | Good |
| `portfolio_metrics.py` | Portfolio aggregation | Good |
| `volatility.py` | Volatility computations | Good |
| `option_pricing.py` | Option pricing models | Good |
| `stress_test.py` | Stress testing scenarios | Good |

### 3.3 Risk Views (Modular)

The risk views are split into focused modules:

- `dashboard.py` (~200 lines) — Dashboard with urgency scores, regime state
- `account.py` — Account-level risk views
- `heatmap.py` — Heatmap visualisations
- `helpers.py` — Shared view helpers
- `owner.py` — Owner configuration views

This modular approach is **good practice** and contrasts with the monolithic `core/views.py`.

---

## 4. Code Quality Assessment

### 4.1 Strengths

1. **Excellent test coverage** — 415 passing tests across 29 test files, including comprehensive tests for Monte Carlo, backtesting, urgency scoring, and data pipeline.
2. **Well-documented models** — `core/models.py` has thorough help_text, db_table specifications, indexes, and docstrings.
3. **Clean service layer** — `risk/services/` uses pure Python functions with clear inputs/outputs, making them easy to test.
4. **Good use of caching** — `core/utils.py` implements Redis/cache-based price caching with configurable timeouts.
5. **Logging throughout** — Consistent use of `logger.info/warning/error` patterns.
6. **Django best practices** — Proper use of `get_object_or_404`, class-based admin, management commands.
7. **Security awareness** — Login rate limiting, path validation, CSRF protection, secure cookie settings.
8. **Config-driven** — Many risk parameters configurable via settings/env vars.

### 4.2 Areas for Improvement

#### 4.2.1 File Size — `core/views.py` (1,222 LOC)

**Severity**: Medium

The file is too large and handles too many responsibilities:
- Authentication (login with rate limiting)
- Dashboard rendering
- Transaction listing with filtering
- Account CRUD operations
- Portfolio summary
- Realised PnL
- Tax summary
- Income summary
- Export functionality

**Recommendation**: Split into focused view modules similar to `risk/views/`:
- `core/views_auth.py` — Login, rate limiting
- `core/views_portfolio.py` — Dashboard, portfolio summary, realised PnL
- `core/views_transactions.py` — Transaction list, filtering
- `core/views_accounts.py` — Account CRUD
- `core/views_reports.py` — Tax summary, income summary, export

#### 4.2.2 File Size — `core/utils.py` (1,017 LOC)

**Severity**: Low

This file mixes several concerns:
- Date series generation (`_generate_date_series`)
- Price loading from DB and yFinance (`_load_prices_from_db`, `_load_prices_from_yfinance`)
- Portfolio value computation (`compute_portfolio_value_series`)
- Position history management
- Currency conversion
- Price fetching utilities

**Recommendation**: Split into:
- `core/utils_dates.py` — Date series generation
- `core/utils_prices.py` — Price loading, caching, fetching
- `core/utils_portfolio.py` — Portfolio valuation, position management
- `core/utils_currencies.py` — Currency conversion

#### 4.2.3 File Size — `risk/models.py` (1,333 LOC)

**Severity**: Low

This file contains many model classes (SecurityUniverse, MarketPriceHistory, VolatilitySummary, FxRate, EconomicIndicator, AlternativePortfolio, AlternativeHolding, ScoringConfig, SecurityIndexExposure, DataQualityLog, SecurityRiskMetrics, CorrelationMatrix). While large, it's acceptable for a single app's models if they're all related.

**Recommendation**: Consider splitting into `risk/models_market.py`, `risk/models_risk.py`, `risk/models_alternative.py` if it continues to grow.

#### 4.2.4 Duplicate Code — PDF Upload in `imports/views.py`

**Severity**: Medium

The `upload_dir`, `temp_path`, file saving, and cleanup logic is duplicated between `pdf_import_view` and `annual_report_import_view`:

```python
# Lines 36-39 (first occurrence)
upload_dir = Path(settings.MEDIA_ROOT) / 'pdf_uploads'
upload_dir.mkdir(parents=True, exist_ok=True)
temp_path = upload_dir / uploaded_file.name
with open(temp_path, 'wb+') as destination:

# Lines 154-157 (second occurrence)
upload_dir = Path(settings.MEDIA_ROOT) / 'pdf_uploads'
upload_dir.mkdir(parents=True, exist_ok=True)
temp_path = upload_dir / uploaded_file.name
with open(temp_path, 'wb+') as destination:
```

**Recommendation**: Extract to a shared helper function:
```python
def save_uploaded_pdf(uploaded_file, upload_dir_name='pdf_uploads'):
    upload_dir = Path(settings.MEDIA_ROOT) / upload_dir_name
    upload_dir.mkdir(parents=True, exist_ok=True)
    temp_path = upload_dir / uploaded_file.name
    with open(temp_path, 'wb+') as destination:
        for chunk in uploaded_file.chunks():
            destination.write(chunk)
    return temp_path
```

#### 4.2.5 Bare `except Exception:` Clauses

**Severity**: Low

There are ~30 `except Exception` clauses across the codebase. While most are logged appropriately, some swallow errors silently:

```python
# core/utils.py:757
except Exception:  # No logging
    pass
```

**Recommendation**: Ensure every `except Exception` either:
- Logs the error with `logger.exception()`
- Re-raises with `raise`
- Returns a meaningful default

---

## 5. Architecture Concerns

### 5.1 Hardcoded Paths

| Location | Hardcoded Value | Severity |
|---|---|---|
| `portfolio_manager/settings.py:261` | `AMIBROKER_WATCH_FOLDER = r'C:\AI-Workspace\amibroker_exports'` | **High** |
| `portfolio_manager/settings_test.py:18` | `AMIBROKER_WATCH_FOLDER = r'C:\AI-Workspace\amibroker_exports'` | **High** |
| `portfolio_manager/settings.py:187` | `VL_MODEL_BASE_URL = os.getenv('VL_MODEL_BASE_URL', 'http://localhost:11440/v1')` | **Medium** |
| `imports/classify_pdf_vl.py:70` | `'http://localhost:11440/v1'` (fallback) | **Medium** |
| `imports/parsers_annual_vl.py:127` | `'http://localhost:11440/v1'` (fallback) | **Medium** |
| `imports/parsers_monthly_vl.py:154` | `'http://localhost:11440/v1'` (fallback) | **Medium** |

**Recommendations**:
1. **AMIBROKER_WATCH_FOLDER**: Move to environment variable with a cross-platform default:
   ```python
   AMIBROKER_WATCH_FOLDER = os.getenv('AMIBROKER_WATCH_FOLDER', os.path.join(BASE_DIR, 'amibroker_exports'))
   ```
2. **VL_MODEL_BASE_URL**: The fallback to `localhost:11440` is acceptable for development but should be **required** in production. Add a validation check:
   ```python
   if not os.getenv('VL_MODEL_BASE_URL'):
       raise ImproperlyConfigured('VL_MODEL_BASE_URL must be set in production')
   ```

### 5.2 Empty `imports/models.py`

**Severity**: Low

The file exists but is essentially empty (2 lines). If no models are defined here, consider whether the `imports` app needs its own models or if they belong in `core` or another app.

### 5.3 Management Command Proliferation

**Severity**: Low

There are 30 management commands across 3 apps. While Django encourages this pattern, the sheer number makes it hard to navigate. Consider:
- Grouping related commands into subdirectories
- Adding `help_text` that clearly distinguishes overlapping commands (e.g., `manage_inbox_import.py` vs `manage_pdf_import.py` vs `manage_annual_report_import.py`)

### 5.4 Inline Django Setup in `auto_import.py`

**Severity**: Medium

The `imports/management/commands/auto_import.py` (508 LOC) and `imports/auto_import.py` contain Django setup inline:
```python
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
django.setup()
```

**Recommendation**: Move Django setup to a shared utility module or use Django's management command infrastructure properly.

---

## 6. Security Review

### 6.1 Strengths

| Area | Status | Details |
|---|---|---|
| **SECRET_KEY** | ✅ Secure | Loaded from environment variable |
| **DB_PASSWORD** | ✅ Secure | Loaded from environment variable |
| **CSRF Protection** | ✅ Enabled | CsrfViewMiddleware, CSRF_COOKIE_HTTPONLY=True |
| **SESSION_COOKIE_SAMESITE** | ✅ Strict | Prevents CSRF via cross-site requests |
| **LOGIN_RATE_LIMITING** | ✅ Implemented | `_check_login_attempts`, `_record_login_attempt` |
| **PATH_VALIDATION** | ✅ Implemented | `_is_path_safe` prevents directory traversal |
| **DATE_VALIDATION** | ✅ Implemented | `_validate_date_param` prevents injection |
| **SQL Parameterisation** | ✅ Used | Django ORM prevents SQL injection |

### 6.2 Areas to Review

| Area | Concern | Severity |
|---|---|---|
| **CSRF_COOKIE_SECURE** | Defaults to `False` — acceptable for dev but must be `True` in production | Low |
| **File Upload** | PDF files saved to `MEDIA_ROOT/pdf_uploads` without content validation | Medium |
| **VL Model URL** | Fallback to `localhost:11440` could be exploited in misconfigured environments | Low |
| **yFinance** | External API calls without timeout limits in some code paths | Low |

### 6.3 File Upload Security

The PDF upload views (`imports/views.py`) save files without validating content type beyond the file extension. Consider:
- Checking the actual file magic bytes (PDF header `%PDF-`)
- Validating file size limits
- Sanitising filenames

---

## 7. Performance Concerns

### 7.1 Monte Carlo Nested Python Loops

**Severity**: High

`risk/services/monte_carlo.py` (582 LOC) contains nested Python loops for simulation:

```python
# Lines 137-140: Nested loops for path simulation
# Lines 449: Another nested loop
```

**Recommendation**: Vectorise using NumPy/pandas:
```python
# Instead of:
for i in range(n_paths):
    for t in range(n_steps):
        path[i, t] = ...

# Use:
increments = np.random.normal(0, 1, (n_paths, n_steps))
paths = np.exp(np.cumsum(drift + volatility * increments, axis=1))
```

### 7.2 yFinance API Calls

**Severity**: Medium

`core/utils.py` has caching for historical prices but `fetch_last_prices` may make repeated yFinance calls without adequate caching in some code paths.

**Recommendation**: Ensure all yFinance calls go through the cached `_load_prices_from_yfinance` function.

### 7.3 Database Queries

**Severity**: Low

The `compute_portfolio_value_series` function loads all BUY/SELL transactions without pagination:
```python
transactions = Transaction.objects.filter(type__in=POSITION_TRANSACTION_TYPES).order_by('date', 'id')
```

For portfolios with thousands of transactions, consider:
- Using `.only()` to select only needed fields
- Adding database indexes on `type` and `date`

---

## 8. Test Coverage Analysis

### 8.1 Test File Distribution

| App | Test Files | Tests | Coverage Assessment |
|---|---|---|---|
| **core** | 12 | ~150 | Good — covers auth, accounts, portfolio summary, PnL, tax, income, export |
| **risk** | 12 | ~200 | Excellent — covers backtesting, Monte Carlo, dashboard, data pipeline, urgency, strategies |
| **imports** | 5 | ~65 | Good — covers parsers, PDF import, auto-import, FX audit |

### 8.2 Test Coverage Gaps

| Area | Gap | Priority |
|---|---|---|
| **Risk views** | No dedicated tests for `risk/views/account.py`, `risk/views/heatmap.py`, `risk/views/owner.py` | Medium |
| **Risk services** | `volatility.py`, `technical_indicators.py`, `option_pricing.py`, `stress_test.py`, `gap_detection.py`, `strategy_costs.py`, `var_reduction.py` — no dedicated tests | High |
| **Core utils** | `core/utils.py` functions not individually tested (only tested indirectly through views) | Medium |
| **Management commands** | Many commands lack test coverage (e.g., `fetch_historical_prices.py`, `merge_duplicate_accounts.py`) | Medium |
| **LLM integration** | `classify_pdf_vl.py`, `parsers_annual_vl.py`, `parsers_monthly_vl.py` — tests mock the LLM but don't test error handling paths | Low |

### 8.3 Test Quality Observations

**Strengths**:
- `test_monte_carlo.py` (722 lines) — Comprehensive parameterised tests
- `test_data_pipeline.py` (819 lines) — Thorough end-to-end testing
- `test_dashboard_views.py` (675 lines) — Good view testing

**Areas for improvement**:
- Consider adding property-based tests (Hypothesis) for risk calculations
- Add integration tests that exercise the full import pipeline
- Consider adding performance regression tests for Monte Carlo

---

## 9. Documentation Quality

### 9.1 Strengths

- **README.md**: Comprehensive project overview with features, tech stack, quick start
- **DEPLOYMENT.md**: Clear deployment instructions
- **history/SECURITY.md**: Security practices documented
- **LOGGING_GUIDE.md**: Logging conventions documented
- **history/CODE_REVIEW.md**: Review process documented
- **history/NEXT_FEATURES.md**: Roadmap documented
- **specs/**: now holds only the parked Phase B specifications (earlier sets archived in docs/history/specs/)

### 9.2 Areas for Improvement

| Area | Issue |
|---|---|
| **Inline documentation** | Some functions lack docstrings (e.g., `regime_engine.py` indicator functions) |
| **API documentation** | No generated API docs (Sphinx, MkDocs) |
| **Architecture documentation** | No system architecture diagram or data flow documentation |
| **Management commands** | Many commands lack `help_text` in their `handle()` method |

---

## 10. Recommendations (Prioritised)

### 🔴 Critical (Fix Immediately)

| # | Recommendation | Impact | Effort |
|---|---|---|---|
| 1 | **Vectorise Monte Carlo nested loops** | 10–100x performance improvement | Medium |
| 2 | **Make VL_MODEL_BASE_URL required in production** | Prevents silent failures | Low |

### 🟡 High (Fix Soon)

| # | Recommendation | Impact | Effort |
|---|---|---|---|
| 3 | **Split `core/views.py` into modular views** | Maintainability, testability | High |
| 4 | **Split `core/utils.py` into focused modules** | Cohesion, reusability | Medium |
| 5 | **Extract duplicate PDF upload code** | DRY, maintainability | Low |
| 6 | **Make AMIBROKER_WATCH_FOLDER configurable** | Cross-platform support | Low |
| 7 | **Add tests for untested risk services** | Test coverage | Medium |

### 🟢 Medium (Plan for Next Sprint)

| # | Recommendation | Impact | Effort |
|---|---|---|---|
| 8 | **Add API documentation** | Onboarding, maintainability | Medium |
| 9 | **Add architecture documentation** | Understanding, onboarding | Low |
| 10 | **Add `help_text` to management commands** | Usability | Low |
| 11 | **Validate PDF file content on upload** | Security | Low |
| 12 | **Add logging to bare `except Exception` clauses** | Debuggability | Low |

### 🔵 Low (Nice to Have)

| # | Recommendation | Impact | Effort |
|---|---|---|---|
| 13 | **Add property-based tests for risk calculations** | Test quality | Medium |
| 14 | **Add performance regression tests** | Regression detection | Low |
| 15 | **Group management commands in subdirectories** | Navigation | Low |

---

## 11. Summary Scorecard

| Category | Score | Notes |
|---|---|---|
| **Code Quality** | 8/10 | Well-written, documented, but some files too large |
| **Test Coverage** | 9/10 | 415 tests, but gaps in risk services |
| **Security** | 8/10 | Good practices, but file upload validation needed |
| **Architecture** | 8/10 | Clean service layer, but views need splitting |
| **Performance** | 6/10 | Monte Carlo loops need vectorisation |
| **Documentation** | 8/10 | Good external docs, could add API docs |
| **Maintainability** | 7/10 | Large files and duplicate code are concerns |

**Overall: 7.7/10** — A mature, well-tested project with clear areas for improvement.
