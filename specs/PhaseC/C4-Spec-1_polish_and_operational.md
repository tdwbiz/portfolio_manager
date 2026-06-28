# Feature Spec: Filesystem Events, Symbol Length, Tests & Operational Improvements

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature_code_review_fixes

---

## Context

Six low-priority enhancements:

1. **Polling-based watcher could use `watchdog`** (`imports/watcher.py`)
2. **Symbol length constraint may be insufficient** (`risk/models.py`)
3. **No automated tests for VL parsers** (`imports/parsers_monthly_vl.py`, `imports/parsers_annual_vl.py`)
4. **Missing `__all__` in module `__init__.py` files** (`core/__init__.py`, `imports/__init__.py`, `risk/__init__.py`)
5. **No health check endpoint** (`core/urls.py`)
6. **Template variable naming inconsistency** (Multiple template files)

**Related issues:** L1 (watchdog), L2 (symbol length), L3 (VL tests), L4 (`__all__`), L5 (health check), L6 (template naming).

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. **Add `watchdog` support for filesystem events (optional)**

- Add `watchdog` to `requirements.txt`
- Create an optional event-based watcher that can be enabled via config
- Keep the existing polling watcher as the default (for compatibility)

**Update `requirements.txt`:**
```
watchdog>=3.0
```

**Create `imports/watcher_event.py`:**
```python
"""Event-based inbox watcher using watchdog."""
import logging
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from django.conf import settings
from django.core.management import call_command

logger = logging.getLogger(__name__)

class InboxEventHandler(FileSystemEventHandler):
    def __init__(self, inbox_dir):
        self.inbox_dir = inbox_dir
    
    def on_created(self, event):
        if event.is_directory:
            return
        filename = os.path.basename(event.src_path)
        if filename.lower().endswith('.csv'):
            logger.info(f"New CSV detected: {filename}")
            call_command('auto_import', verbosity=0)
        elif filename.lower().endswith('.pdf'):
            logger.info(f"New PDF detected: {filename}")
            # Classify and route to appropriate import command
            call_command('manage_pdf_import', inbox_dir=self.inbox_dir, verbosity=0)

def start_event_watcher(inbox_dir):
    """Start the watchdog-based watcher."""
    event_handler = InboxEventHandler(inbox_dir)
    observer = Observer()
    observer.schedule(event_handler, inbox_dir, recursive=False)
    observer.start()
    logger.info(f"Event watcher started for {inbox_dir}")
    return observer
```

### 2. **Increase symbol length constraint**

- Update `risk/models.py` to increase `symbol` max_length from 20 to 50
- This accommodates complex symbols like `BMO.PR.K.TO` or foreign exchanges

```python
# In risk/models.py
class SecurityUniverse(models.Model):
    symbol = models.CharField(max_length=50, unique=True)  # Increased from 20
    # ... rest of fields
```

### 3. **Add unit tests for VL parsers**

- Create `tests/test_vl_parsers.py` with mock-based tests
- Mock the VL client to return sample responses
- Test parsing logic, validation, and error handling

```python
# tests/test_vl_parsers.py
from unittest.mock import patch, MagicMock
from datetime import date
from decimal import Decimal
from imports.parsers_monthly_vl import parse_rbc_monthly_vl, _validate_result

def test_parse_rbc_monthly_vl_mock():
    """Test VL parser with mocked response."""
    mock_response = {
        'statement_date': '2025-01-31',
        'account_number': '12345-67890',
        'sections': [{
            'currency': 'CAD',
            'balances': [],
            'transactions': [],
        }],
    }
    with patch('imports.llm_client.VLClient.extract_json', return_value=mock_response):
        result = parse_rbc_monthly_vl('test.pdf')
        assert result[0]['statement_date'] == date(2025, 1, 31)
        assert result[0]['account_number'] == '12345-67890'

def test_validate_result():
    """Test result validation."""
    valid_result = {
        'statement_date': date(2025, 1, 31),
        'account_number': '12345-67890',
        'sections': [{'currency': 'CAD', 'balances': [], 'transactions': []}],
    }
    assert _validate_result(valid_result) is True
    
    invalid_result = {
        'statement_date': None,  # Missing required field
        'account_number': '',
        'sections': [],
    }
    assert _validate_result(invalid_result) is False
```

### 4. **Add `__all__` to module `__init__.py` files**

- Add `__all__` to `core/__init__.py`, `imports/__init__.py`, `risk/__init__.py`
- List all public functions and classes

**Update `core/__init__.py`:**
```python
__all__ = [
    'compute_tx_hash',
    'TransactionType',
    'Account',
    'Transaction',
    'BrokerStatement',
    'FileImportLog',
]
```

**Update `imports/__init__.py`:**
```python
__all__ = [
    'parse_ib_csv',
    'parse_rbc_csv',
    'parse_rbc_monthly_vl',
    'parse_rbc_annual_report',
    'parse_rbc_annual_report_vl',
    'auto_import',
    'VLClient',
]
```

**Update `risk/__init__.py`:**
```python
__all__ = [
    'SecurityUniverse',
    'MarketPriceHistory',
    'AlternativePortfolio',
    'AlternativeHolding',
]
```

### 5. **Add health check endpoint**

- Add a simple health check view in `core/views.py`
- Add a URL route in `core/urls.py`
- Returns JSON with status and timestamp

**Add to `core/views.py`:**
```python
from django.http import JsonResponse
from django.utils import timezone

def health_check(request):
    """Health check endpoint for deployment orchestration."""
    return JsonResponse({
        'status': 'ok',
        'timestamp': timezone.now().isoformat(),
    })
```

**Add to `core/urls.py`:**
```python
from core.views import health_check

urlpatterns = [
    # ... existing URLs ...
    path('health/', health_check, name='health_check'),
]
```

### 6. **Standardize template variable naming**

- Review all paginated views and ensure they use `page_obj` consistently
- Update any views that use non-standard names like `transactions` for pagination

**Update `core/views_accounts.py`:**
```python
# Before: page_obj = Paginator(transactions, 20).page(page_number)
# After: Ensure all views use 'page_obj' as the template variable name
context = {
    'page_obj': page_obj,  # Consistent naming
    'account': account,
    # ... other context
}
```

---

## Constraints

- Do NOT run migrations — that is a manual step
- Do NOT push to git — list it under Manual Steps Required
- Follow PEP8, add type hints to all new functions
- The `watchdog` watcher is optional — keep the polling watcher as default
- All existing tests must continue to pass

---

## Files to Modify

1. `requirements.txt` — Add `watchdog`
2. `imports/watcher_event.py` — NEW FILE, event-based watcher
3. `risk/models.py` — Increase symbol max_length to 50
4. `tests/test_vl_parsers.py` — NEW FILE, VL parser tests
5. `core/__init__.py` — Add `__all__`
6. `imports/__init__.py` — Add `__all__`
7. `risk/__init__.py` — Add `__all__`
8. `core/views.py` — Add `health_check()` view
9. `core/urls.py` — Add health check URL route
10. `core/views_accounts.py` — Standardize template variable naming

---

## Tests

- Unit test for health check: Verify JSON response with status 'ok'
- Unit test for VL parsers: Mock VL response, verify parsing logic
- Unit test for symbol length: Verify 50-character symbols are accepted
- Manual test for watchdog: Verify event-based watcher detects new files

---

## Manual Steps Required

- [ ] `python manage.py makemigrations` (for symbol length change)
- [ ] `python manage.py migrate --settings=...`
- [ ] Review and push feature branch: `git push origin feature_rbc_pdf_fixes`
- [ ] Run full test suite: `python run_tests.py`

---

## Human Approval Required

Human approval required before any git push or DB changes.
