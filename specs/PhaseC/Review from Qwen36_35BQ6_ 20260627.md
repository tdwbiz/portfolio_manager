# Comprehensive Code Review Report — Portfolio Manager

**Date:** 2026-06-27  
**Reviewer:** Qwen3.5 35B Q6  
**Scope:** Full codebase review — `core/`, `imports/`, `risk/`, templates, configuration  
**Branch:** `feature_rbc_pdf_fixes` (default: `main`)

---

## 1. Executive Summary

Portfolio Manager is a well-architected Django 5.1 financial portfolio management application with:

- **Strong Foundation**: Proper use of `Decimal` for all financial calculations, atomic transactions, SHA-256 hashing for idempotency, and comprehensive data models.
- **Advanced Ingestion Pipeline**: Hybrid CSV + Vision Language (VL) model parsing for RBC statements, with sophisticated text cleaning for wide-character spacing in RBC PDFs.
- **Risk Analytics**: Professional-grade risk modeling with security universes, market price history, volatility summaries, and alternative portfolio management.
- **Observability**: Excellent logging, rejection logs, and data quality tracking.

**Overall Assessment:** The codebase is mature and production-ready. However, several architectural inconsistencies, security gaps, and robustness issues need attention before further scaling.

---

## 2. Severe Issues 🔴

*Critical bugs or security vulnerabilities that risk data corruption, loss, or unauthorized access.*

### S1: Duplicate Detection Ignores `tx_hash`

**File:** `imports/auto_import.py` (lines ~110-125)  
**Impact:** Data integrity risk — duplicate transactions may be imported if minor field variations exist.

```python
# Current: Manual field-by-field comparison
existing = Transaction.objects.get(
    statement__source=broker,
    date=trade['date'],
    symbol=trade['symbol'],
    type=trade['type'],
    quantity=trade['quantity'],
    price=trade['price'],
    account__account_id=account_id if account_id else '',
)
```

**Problem:** The `Transaction` model defines a `tx_hash` field (SHA-256) in `core/models.py`, but `auto_import.py` performs manual field comparison instead. This is less robust and misses the canonical deduplication mechanism.

**Recommendation:** Use `tx_hash` for duplicate detection:
```python
from core.models import compute_tx_hash
tx_hash = compute_tx_hash(broker, account_id, trade['date'], ...)
existing = Transaction.objects.filter(tx_hash=tx_hash).first()
```

---

### S2: Hardcoded SQLite in `auto_import.py` Standalone Mode

**File:** `imports/auto_import.py` (lines ~18-33)  
**Impact:** Standalone script execution will fail or use wrong database.

```python
if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': ':memory:',
            }
        },
        ...
    )
```

**Problem:** When run as `python auto_import.py`, the script configures an in-memory SQLite database, ignoring the actual MSSQL configuration from `config.ini`/`.env`.

**Recommendation:** Remove the fallback `settings.configure()` and require Django management command invocation:
```bash
python manage.py auto_import <file_path> <broker>
```

---

### S3: Open Redirect Vulnerability (Partial Fix)

**File:** `core/views_auth.py` (line ~47)  
**Impact:** Potential phishing attacks via malicious redirect URLs.

```python
next_url = request.POST.get('next', '/')
if next_url.startswith('/'):
    return redirect(next_url)
return redirect('/')
```

**Problem:** While the code checks for `/` prefix, it doesn't validate against absolute URLs like `//evil.com` or `http://evil.com`. An attacker could craft a login URL with `next=//evil.com` to redirect users to a phishing site.

**Recommendation:** Use Django's `urlparse` to validate:
```python
from urllib.parse import urlparse, urljoin

def is_safe_url(target):
    ref_url = urlparse(request.build_absolute_uri())
    test_url = urlparse(urljoin(ref_url.scheme + "://" + ref_url.netloc, target))
    return test_url.scheme in ('http', 'https') and test_url.netloc == ref_url.netloc
```

---

### S4: File Upload Missing CSRF Protection on Import Views

**File:** `imports/views.py`  
**Impact:** CSRF attacks could trigger unauthorized file uploads.

**Problem:** The `pdf_import_view` and `annual_report_import_view` handle file uploads but the CSRF decorator status is unclear. If these views are accessed via POST without proper CSRF tokens, attackers could upload malicious files.

**Recommendation:** Ensure `@csrf_exempt` is NOT used, and verify CSRF middleware is enabled. Add file size limits:
```python
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
```

---

## 3. Urgent Issues 🟠

*High-priority issues that should be fixed to prevent future failures or data inconsistencies.*

### U1: Statement Date Uses Import Time, Not Actual Date

**File:** `imports/auto_import.py` (line ~105)  
**Impact:** Historical reporting accuracy compromised.

```python
statement = BrokerStatement.objects.create(
    source=broker,
    file_date=timezone.now().date(),  # ← WRONG: uses import date
    raw_filename=os.path.basename(file_path),
)
```

**Problem:** `file_date` should reflect the statement's actual date (extracted from the file), not when it was imported. This causes incorrect time-series analysis.

**Recommendation:** Extract the date from the CSV/PDF content or filename:
```python
# For CSV: parse from first transaction date or file metadata
# For PDF: extract from statement_date field
statement_date = extract_statement_date(trades, broker)  # Implement this
statement = BrokerStatement.objects.create(
    source=broker,
    file_date=statement_date,
    raw_filename=os.path.basename(file_path),
)
```

---

### U2: LLM Failure Halts Entire Import

**File:** `imports/parsers_monthly_vl.py` (lines ~240-250)  
**Impact:** Single malformed PDF blocks all imports in a batch.

```python
if not statement_date_str:
    raise ValueError(
        f"VL model did not return statement_date for {file_path}: {raw}"
    )
```

**Problem:** If the VL model fails to extract `statement_date`, the entire import fails with a hard error. No partial success, no retry mechanism, no graceful degradation.

**Recommendation:** Implement a retry with fallback:
```python
if not statement_date_str:
    logger.warning(f"VL model missing statement_date, attempting regex fallback for {file_path}")
    statement_date_str = extract_date_from_filename(file_path) or extract_date_from_text(raw)
    if not statement_date_str:
        raise ValueError(...)  # Only fail if all methods fail
```

---

### U3: No Rate Limiting on PDF Import Endpoints

**File:** `imports/views.py`  
**Impact:** Resource exhaustion via rapid file uploads.

**Problem:** Login attempts are rate-limited (S1), but PDF imports have no such protection. An attacker or misconfigured watcher could flood the system with PDF uploads, consuming disk space and LLM API calls.

**Recommendation:** Add rate limiting:
```python
from django.views.decorators.csrf import csrf_protect
from django.core.cache import cache

def _check_upload_rate_limit(user_id):
    cache_key = f'upload_attempts:{user_id}'
    attempts = cache.get(cache_key, 0)
    if attempts >= 10:  # 10 uploads per hour
        return False
    cache.set(cache_key, attempts + 1, 3600)
    return True
```

---

### U4: Inconsistent Currency Assumptions

**File:** `imports/auto_import.py` (line ~130)  
**Impact:** Incorrect CAD conversions for non-CAD/USD currencies.

```python
if trade.get('currency', 'USD') == 'CAD':
    amount_cad = trade['amount']
else:
    amount_cad = (trade['amount'] * rate).quantize(Decimal('0.01'))
```

**Problem:** Any currency other than 'CAD' is assumed to be 'USD' and converted using a single exchange rate. This fails for EUR, GBP, or other currencies.

**Recommendation:** Explicitly handle unsupported currencies:
```python
currency = trade.get('currency', 'USD')
if currency == 'CAD':
    amount_cad = trade['amount']
elif currency == 'USD':
    amount_cad = (trade['amount'] * rate).quantize(Decimal('0.01'))
else:
    logger.warning(f"Unsupported currency {currency} for {trade['symbol']}, defaulting to USD conversion")
    amount_cad = (trade['amount'] * rate).quantize(Decimal('0.01'))
```

---

### U5: Watcher Filename-Based Classification is Brittle

**File:** `imports/watcher.py` (lines ~24-30)  
**Impact:** Misclassification of PDFs leads to parsing failures.

```python
annual_files = [f for f in all_pdf_files if f.lower().startswith('annual report')]
statement_files = [f for f in all_pdf_files if not f.lower().startswith('annual report')]
```

**Problem:** Classification relies entirely on filename prefixes. A file named `Annual_Report_2025.pdf` (with underscore) or `rbc_annual.pdf` would be misclassified.

**Recommendation:** Use content-based detection or a more robust naming convention:
```python
# Option 1: Check file content for headers
# Option 2: Require specific naming convention documented in USER_MANUAL.md
# Option 3: Add a manual classification step in the UI
```

---

## 4. Moderate Issues 🟡

*Best practice violations, code quality issues, and minor robustness improvements.*

### M1: Transaction Type Normalization Lacks Canonical Mapping

**File:** `imports/parsers.py` (lines ~20-60)  
**Impact:** Reporting fragmentation — same transaction type stored differently.

**Problem:** While `_IB_TYPE_MAP` and `_RBC_TYPE_MAP` normalize types to canonical codes (BUY, SELL, DIV, etc.), there's no mechanism to handle future broker-specific codes. The fallback `re.sub(r'[^A-Z0-9]', '_', raw.upper())[:20]` creates inconsistent codes like `FOREIGN_TAX_WITHOLDING` vs `WITHTAX`.

**Recommendation:** Maintain a centralized type registry with automatic learning:
```python
# Add to core/models.py TransactionType
@classmethod
def auto_create_or_get(cls, code, label):
    obj, created = cls.objects.get_or_create(
        code=code,
        defaults={'label': label}
    )
    return obj
```

---

### M2: Portfolio View Loads All Transactions Into Memory

**File:** `core/views_accounts.py` (lines ~30-50)  
**Impact:** Performance degradation with large transaction counts.

```python
buy_sell_txs = transactions.filter(type__in=POSITION_TRANSACTION_TYPES).order_by('date')
holdings = {}
for tx in buy_sell_txs:
    ...
```

**Problem:** The `account_detail` view loads all transactions for an account into Python memory to compute holdings. With 10,000+ transactions, this becomes slow and memory-intensive.

**Recommendation:** Use database aggregation:
```python
holdings = Transaction.objects.filter(
    account=account,
    type__in=POSITION_TRANSACTION_TYPES
).values('symbol').annotate(
    shares=models.Sum(models.Case(
        models.When(type='BUY', then='quantity'),
        models.When(type='SELL', then=-F('quantity')),
        default=0,
        output_field=DecimalField()
    ))
).filter(shares__gt=0)
```

---

### M3: Missing Database Indexes on Frequently Filtered Fields

**File:** `core/models.py`  
**Impact:** Slow queries on large datasets.

**Problem:** Fields like `Transaction.type`, `Transaction.symbol`, and `Account.owner` are frequently filtered but lack explicit indexes.

**Recommendation:** Add indexes:
```python
class Transaction(models.Model):
    # ...
    class Meta:
        indexes = [
            models.Index(fields=['type']),
            models.Index(fields=['symbol']),
            models.Index(fields=['date']),
            models.Index(fields=['account', 'date']),
        ]
```

---

### M4: No Input Sanitization on Symbol Field

**File:** `core/views_portfolio.py` (line ~140)  
**Impact:** Potential SQL injection or XSS via symbol names.

```python
symbol = request.GET.get('symbol', '').strip().upper()[:20]
```

**Problem:** While length is limited, there's no validation that the symbol contains only valid characters (alphanumeric, dots, hyphens). Malicious symbols could contain HTML/JavaScript.

**Recommendation:** Add validation:
```python
import re
if symbol and not re.match(r'^[A-Z0-9.\-]+$', symbol):
    symbol = ''  # Reject invalid symbols
```

---

### M5: Hardcoded CDN URLs for Bootstrap/Chart.js

**File:** `core/templates/core/base.html` (lines ~12-15)  
**Impact:** Offline usage broken, potential supply chain attack.

```html
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" ...>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
```

**Problem:** External CDN dependencies break when offline and introduce supply chain risk (CDN compromise).

**Recommendation:** Bundle assets locally:
```bash
pip install django-bootstrap5
# Or use webpack/whitenoise to serve static files
```

---

### M6: Risk Dashboard Returns Empty Placeholder

**File:** `risk/views/dashboard.py` (lines ~15-35)  
**Impact:** Risk features appear broken to users.

```python
def get_top_ugl_positions(calc_date, limit=5, include_excluded=False):
    # This is a simplified implementation - the full version would use raw SQL
    # or DataSource to query vw_current_portfolio joined with price data
    # For now, return empty list - the view handles None gracefully
    return []
```

**Problem:** The comment explicitly states this is a placeholder. Users see empty risk dashboards without clear indication that the feature is incomplete.

**Recommendation:** Either implement the full query or add a clear "Coming Soon" message:
```python
return []  # TODO: Implement full UGL query using vw_current_portfolio
# Add to template: {% if not top_ugl_positions and calc_date %}<p>Risk analysis coming soon.</p>{% endif %}
```

---

## 5. Low Priority / Enhancements 🔵

*Style improvements, documentation, and nice-to-have features.*

### L1: Polling-Based Watcher Could Use `watchdog`

**File:** `imports/watcher.py`  
**Impact:** Inefficient resource usage.

**Current:** 60-second polling interval (configurable).  
**Recommendation:** Use `watchdog` library for filesystem events:
```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
```

---

### L2: Symbol Length Constraint May Be Insufficient

**File:** `risk/models.py` (line ~13)  
**Impact:** International symbols truncated.

```python
symbol = models.CharField(max_length=20, unique=True)
```

**Recommendation:** Increase to 30 or use `max_length=50` to accommodate complex symbols like `BMO.PR.K.TO` or foreign exchanges.

---

### L3: No Automated Tests for VL Parsers

**File:** `imports/parsers_monthly_vl.py`, `imports/parsers_annual_vl.py`  
**Impact:** Regression risk when LLM prompts change.

**Recommendation:** Add unit tests with mock VL responses:
```python
def test_parse_rbc_monthly_vl_mock():
    mock_response = {
        'statement_date': '2025-01-31',
        'account_number': '12345-67890',
        'sections': [...]
    }
    with patch('imports.llm_client.VLClient.extract_json', return_value=mock_response):
        result = parse_rbc_monthly_vl('test.pdf')
        assert result[0]['statement_date'] == date(2025, 1, 31)
```

---

### L4: Missing `__all__` in Module `__init__.py` Files

**File:** `core/__init__.py`, `imports/__init__.py`, `risk/__init__.py`  
**Impact:** Unclear public API.

**Recommendation:** Add `__all__` to modules with public functions:
```python
# core/utils/__init__.py
__all__ = [
    'compute_portfolio_value_series',
    'compute_realised_pnl',
    'compute_positions',
    ...
]
```

---

### L5: No Health Check Endpoint

**File:** `core/urls.py`  
**Impact:** Deployment orchestration (Kubernetes, Docker) lacks readiness probes.

**Recommendation:** Add a simple health check:
```python
from django.http import JsonResponse

def health_check(request):
    return JsonResponse({'status': 'ok', 'timestamp': timezone.now().isoformat()})

# urls.py
path('health/', health_check, name='health_check')
```

---

### L6: Template Variables Inconsistent Naming

**File:** Multiple template files  
**Impact:** Developer confusion when navigating templates.

**Problem:** Some templates use `page_obj`, others use `transactions`. Inconsistent naming across views.

**Recommendation:** Standardize on `page_obj` for all paginated views.

---

## 6. Architecture Observations

### Strengths
1. **Decimal Precision**: All financial calculations use `Decimal`, avoiding floating-point errors.
2. **Atomic Transactions**: Import operations use `transaction.atomic()` for rollback safety.
3. **VL Model Integration**: Sophisticated use of Qwen2.5-VL for PDF parsing with post-processing heuristics.
4. **Data Quality Logging**: `DataQualityLog` model provides excellent audit trail.
5. **Modular Views**: Domain-specific view files (`views_accounts.py`, `views_portfolio.py`, etc.) improve maintainability.

### Areas for Improvement
1. **Caching Strategy**: Cache timeouts are configurable but no cache invalidation strategy exists.
2. **API Layer**: No REST API — all access is via Django templates. Consider adding DRF for mobile/integration.
3. **Multi-Tenancy**: Comment in `views_accounts.py` acknowledges multi-tenant support is planned but not implemented.
4. **Migration History**: No visible migration strategy for schema changes in production.

---

## 7. Recommended Action Plan

### Immediate (This Sprint)
- [ ] Fix S1: Use `tx_hash` for duplicate detection
- [ ] Fix S3: Improve open redirect validation
- [ ] Fix U1: Extract actual statement date from files
- [ ] Fix U4: Handle unsupported currencies explicitly

### Short-Term (Next Sprint)
- [ ] Fix U2: Add LLM failure fallback mechanism
- [ ] Fix U3: Add rate limiting to upload endpoints
- [ ] Fix M2: Optimize portfolio view with database aggregation
- [ ] Fix M3: Add missing database indexes

### Medium-Term (Next Quarter)
- [ ] Fix S2: Remove hardcoded SQLite fallback
- [ ] Fix M5: Bundle frontend assets locally
- [ ] Fix M6: Implement or deprecate risk dashboard placeholders
- [ ] Add L3: Unit tests for VL parsers
- [ ] Add L5: Health check endpoint

---

## 8. Conclusion

Portfolio Manager is a mature, well-architected application with a strong foundation. The most critical issues are around **data integrity** (duplicate detection, statement dates) and **security** (open redirect, rate limiting). Addressing the Severe and Urgent issues will significantly improve the system's reliability and security posture.

The codebase demonstrates excellent engineering practices in financial data handling, LLM integration, and risk analytics. With the recommended improvements, it will be production-ready for multi-tenant deployment.

---

**Report Generated:** 2026-06-27  
**Reviewer:** Qwen3.5 35B Q6  
**Next Review:** After implementing Severe and Urgent fixes
