# Feature Spec: Upload Rate Limiting, Currency Handling & Watcher Classification

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature_rbc_pdf_fixes

---

## Context

Three robustness issues in the import pipeline:

1. **No rate limiting on PDF import endpoints** (`imports/views.py`): Login attempts are rate-limited, but PDF imports have no such protection. An attacker or misconfigured watcher could flood the system.

2. **Incorrect currency assumptions** (`imports/auto_import.py`): Any currency other than 'CAD' is assumed to be 'USD' and converted using a single exchange rate. This fails for EUR, GBP, or other currencies.

3. **Watcher filename-based classification is brittle** (`imports/watcher.py`): Classification relies entirely on filename prefixes. A file named `Annual_Report_2025.pdf` (with underscore) or `rbc_annual.pdf` would be misclassified.

**Related issues:** U3 (upload rate limit), U4 (currency assumptions), U5 (watcher classification).

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. **Add rate limiting to PDF import endpoints**

- Add a `_check_upload_rate_limit()` helper to `imports/views.py`
- Use Django cache to track upload attempts per session/user
- Limit to 10 uploads per hour per user
- Return a 429 status with a helpful message when limit is exceeded

**New helper in `imports/views.py`:**
```python
from django.core.cache import cache
from django.http import JsonResponse

def _check_upload_rate_limit(user_id):
    """Check if user has exceeded upload rate limit (10 per hour)."""
    cache_key = f'upload_attempts:{user_id}'
    attempts = cache.get(cache_key, 0)
    if attempts >= 10:
        return False
    cache.set(cache_key, attempts + 1, 3600)
    return True
```

**Apply to `pdf_import_view` and `annual_report_import_view`:**
```python
if request.method == 'POST':
    user_id = request.user.id if request.user.is_authenticated else request.session.session_key
    if not _check_upload_rate_limit(user_id):
        context['error'] = 'Upload rate limit exceeded. Please try again later.'
        return render(request, 'imports/pdf_import.html', context, status=429)
    # ... rest of existing logic
```

### 2. **Handle unsupported currencies explicitly**

- In `imports/auto_import.py`, explicitly handle currencies other than 'CAD' and 'USD'
- Log a warning for unsupported currencies
- Default to using the exchange rate but mark the transaction with a note

**Updated currency handling in `auto_import.py`:**
```python
currency = trade.get('currency', 'USD')
if currency == 'CAD':
    amount_cad = trade['amount']
elif currency == 'USD':
    rate = trade.get('exchange_rate_cad', exchange_rate_cad)
    amount_cad = (trade['amount'] * rate).quantize(Decimal('0.01'))
else:
    logger.warning(f"Unsupported currency {currency} for {trade['symbol']}, using default rate")
    rate = trade.get('exchange_rate_cad', exchange_rate_cad)
    amount_cad = (trade['amount'] * rate).quantize(Decimal('0.01'))
    # Add a note to flag this transaction for manual review
    notes = trade.get('notes', '')
    if notes:
        notes += ' [UNSUPPORTED CURRENCY: ' + currency + ']'
    else:
        notes = 'UNSUPPORTED CURRENCY: ' + currency
```

### 3. **Improve watcher file classification**

- Add a content-based detection mechanism as a fallback to filename-based classification
- Check the first few pages of the PDF for known headers (e.g., "Annual Investment Performance Report" vs "Monthly Statement")
- If content detection is inconclusive, fall back to filename-based classification with a warning

**Updated `_poll_once()` in `imports/watcher.py`:**
```python
def _classify_pdf(file_path):
    """Classify a PDF as annual report or statement.
    
    Priority:
    1. Content-based detection (check first page for headers)
    2. Filename-based detection (existing logic)
    3. Default to statement
    """
    import fitz
    
    # Try content-based detection first
    try:
        doc = fitz.open(file_path)
        first_page_text = doc[0].get_text().lower()
        doc.close()
        
        if any(phrase in first_page_text for phrase in [
            'annual investment performance report',
            'annual report',
            'performance report',
        ]):
            return 'annual'
        elif any(phrase in first_page_text for phrase in [
            'monthly statement',
            'statement',
            'account statement',
        ]):
            return 'statement'
    except Exception as e:
        logger.debug(f"Content detection failed for {file_path}: {e}")
    
    # Fallback to filename-based detection
    filename = os.path.basename(file_path).lower()
    if filename.startswith('annual report'):
        return 'annual'
    
    # Default to statement
    return 'statement'
```

**Updated `_poll_once()` to use the new classifier:**
```python
if all_pdf_files:
    annual_files = []
    statement_files = []
    
    for f in all_pdf_files:
        file_path = os.path.join(inbox_dir, f)
        classification = _classify_pdf(file_path)
        if classification == 'annual':
            annual_files.append(f)
        else:
            statement_files.append(f)
    
    # ... rest of existing logic
```

---

## Constraints

- Do NOT run migrations — that is a manual step
- Do NOT push to git — list it under Manual Steps Required
- Follow PEP8, add type hints to all new functions
- The rate limit counter should reset after 1 hour (TTL on cache key)
- The currency warning should be visible in the transaction notes for manual review
- All existing tests must continue to pass

---

## Files to Modify

1. `imports/views.py` — Add `_check_upload_rate_limit()` and apply to upload views
2. `imports/auto_import.py` — Update currency handling for unsupported currencies
3. `imports/watcher.py` — Add `_classify_pdf()` and update `_poll_once()`

---

## Tests

- Unit test for upload rate limiting: Simulate 11 rapid uploads, verify 11th is rejected with 429
- Unit test for currency handling: Test with CAD, USD, EUR, GBP currencies
- Unit test for PDF classification: Test with annual report and statement PDFs
- Unit test for filename fallback: Test with various filename patterns

---

## Manual Steps Required

- [ ] `python manage.py makemigrations` (if any model changes)
- [ ] `python manage.py migrate --settings=...`
- [ ] Review and push feature branch: `git push origin feature_rbc_pdf_fixes`
- [ ] Run full test suite: `python run_tests.py`

---

## Human Approval Required

Human approval required before any git push or DB changes.
