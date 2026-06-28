# Feature Spec: Database Indexes, Input Validation & Frontend Assets

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature_rbc_pdf_fixes

---

## Context

Four quality and maintainability issues:

1. **Missing database indexes** (`core/models.py`): Fields like `Transaction.type`, `Transaction.symbol`, and `Account.owner` are frequently filtered but lack explicit indexes.
2. **No input sanitization on symbol field** (`core/views_portfolio.py`): While length is limited, there's no validation that the symbol contains only valid characters.
3. **Hardcoded CDN URLs for Bootstrap/Chart.js** (`core/templates/core/base.html`): External CDN dependencies break when offline and introduce supply chain risk.
4. **Risk dashboard returns empty placeholder** (`risk/views/dashboard.py`): The comment explicitly states this is a placeholder.

**Related issues:** M3 (missing indexes), M4 (symbol sanitization), M5 (hardcoded CDN), M6 (placeholder dashboard).

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. **Add missing database indexes**

- Add explicit indexes to frequently filtered fields in `core/models.py`
- Use Django's `Meta.indexes` for all new indexes

```python
class Transaction(models.Model):
    # ... existing fields ...
    
    class Meta:
        indexes = [
            models.Index(fields=['type'], name='tx_type_idx'),
            models.Index(fields=['symbol'], name='tx_symbol_idx'),
            models.Index(fields=['date'], name='tx_date_idx'),
            models.Index(fields=['account', 'date'], name='tx_account_date_idx'),
            models.Index(fields=['currency'], name='tx_currency_idx'),
        ]

class Account(models.Model):
    # ... existing fields ...
    
    class Meta:
        indexes = [
            models.Index(fields=['owner'], name='acc_owner_idx'),
            models.Index(fields=['category'], name='acc_category_idx'),
            models.Index(fields=['broker'], name='acc_broker_idx'),
        ]
```

### 2. **Add input sanitization on symbol field**

- In `core/views_portfolio.py`, add validation that the symbol contains only valid characters
- Use regex to allow alphanumeric, dots, and hyphens (common in ticker symbols)

```python
import re

symbol = request.GET.get('symbol', '').strip().upper()
if symbol and not re.match(r'^[A-Z0-9.\-]+$', symbol):
    symbol = ''  # Reject invalid symbols
```

### 3. **Bundle frontend assets locally**

- Replace CDN URLs with local static file references
- Use `django-bootstrap5` package or bundle assets manually

**Option A: Use django-bootstrap5 (recommended)**
```bash
pip install django-bootstrap5
```

**Update `settings.py`:**
```python
INSTALLED_APPS = [
    # ... existing apps ...
    'bootstrap5',
]
```

**Update `base.html`:**
```html
{% load bootstrap5 %}
{% bootstrap_css %}
{% bootstrap_javascript %}
```

**Option B: Manual bundling**
- Download Bootstrap 5.3.2 CSS/JS to `static/core/vendor/bootstrap/`
- Download Chart.js 4.4.0 to `static/core/vendor/chartjs/`
- Update `base.html` to reference local files

```html
<link rel="stylesheet" href="{% static 'core/vendor/bootstrap/css/bootstrap.min.css' %}">
<script src="{% static 'core/vendor/bootstrap/js/bootstrap.bundle.min.js' %}"></script>
<script src="{% static 'core/vendor/chartjs/chart.umd.min.js' %}"></script>
```

### 4. **Add "Coming Soon" message to risk dashboard**

- In `risk/views/dashboard.py`, add a clear message when the risk dashboard is incomplete
- Update the template to show the message

```python
def dashboard(request):
    # ... existing logic ...
    
    # Check if risk features are fully implemented
    if not top_ugl_positions and calc_date:
        context['risk_incomplete'] = True
    
    return render(request, 'risk/dashboard.html', context)
```

**Update `risk/templates/risk/dashboard.html`:**
```html
{% if risk_incomplete %}
<div class="alert alert-info">
    <i class="bi bi-info-circle"></i> Risk analysis features are coming soon. 
    Current positions are displayed for reference.
</div>
{% endif %}
```

---

## Constraints

- Do NOT run migrations — that is a manual step
- Do NOT push to git — list it under Manual Steps Required
- Follow PEP8, add type hints to all new functions
- The `django-bootstrap5` package is preferred over manual bundling
- All existing tests must continue to pass

---

## Files to Modify

1. `core/models.py` — Add indexes to `Transaction` and `Account` Meta classes
2. `core/views_portfolio.py` — Add symbol validation
3. `core/templates/core/base.html` — Replace CDN URLs with local references
4. `risk/views/dashboard.py` — Add "Coming Soon" message
5. `risk/templates/risk/dashboard.html` — Add "Coming Soon" alert

---

## Tests

- Unit test for symbol validation: Test with valid symbols (should pass), invalid symbols (should reject)
- Unit test for indexes: Verify indexes exist after migration (manual test)
- Unit test for "Coming Soon" message: Verify message appears when risk features are incomplete

---

## Manual Steps Required

- [ ] `python manage.py makemigrations` (for index changes)
- [ ] `python manage.py migrate --settings=...`
- [ ] Review and push feature branch: `git push origin feature_rbc_pdf_fixes`
- [ ] Run full test suite: `python run_tests.py`

---

## Human Approval Required

Human approval required before any git push or DB changes.
