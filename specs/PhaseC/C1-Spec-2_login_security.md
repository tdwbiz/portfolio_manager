# Feature Spec: Login Security & Import Endpoint Protection

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature_code_review_fixes

---

## Context

Phase A added basic login rate limiting via `_check_login_attempts()` in `core/views.py`. However, two security gaps remain:

1. **Open redirect vulnerability** in `core/views_auth.py`: The `next` parameter only checks `startswith('/')`, which allows `//evil.com` (protocol-relative URLs) to redirect users to phishing sites.
2. **Missing CSRF/rate-limiting on PDF import endpoints** in `imports/views.py`: File upload views have no rate limiting, allowing resource exhaustion via rapid uploads.

**Related issues:** S3 (open redirect), S4 (CSRF on uploads).

---

## Stack

- Language / Framework: Python 3.12 / Django 5.1
- Database: MSSQL — existing DB "PortfolioDEV" via .env
- Existing project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. **Fix open redirect vulnerability in login view**

- Add a `is_safe_url()` helper function to `core/views.py`
- Use `urlparse` and `urljoin` to validate that the `next` parameter points to the same origin
- Replace the current `next_url.startswith('/')` check in `core/views_auth.py`

**New helper in `core/views.py`:**
```python
from urllib.parse import urlparse, urljoin

def is_safe_url(target):
    """Validate that target URL is safe for redirection (same origin only)."""
    ref_url = urlparse('http://' + settings.ALLOWED_HOSTS[0] if settings.ALLOWED_HOSTS else 'http://localhost')
    test_url = urlparse(urljoin(ref_url.scheme + "://" + ref_url.netloc, target))
    return test_url.scheme in ('http', 'https') and test_url.netloc == ref_url.netloc
```

**Updated login view in `core/views_auth.py`:**
```python
from core.views import is_safe_url

next_url = request.POST.get('next', '/')
if next_url == '/' or is_safe_url(next_url):
    return redirect(next_url)
return redirect('/')
```

### 2. **Add rate limiting to PDF import endpoints**

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

### 3. **Verify CSRF protection on import views**

- Ensure `@csrf_exempt` is NOT used on any import view
- Verify CSRF middleware is enabled in Django settings
- Add a comment in `imports/views.py` documenting the CSRF protection status

---

## Constraints

- Do NOT run migrations — that is a manual step
- Do NOT push to git — list it under Manual Steps Required
- Follow PEP8, add type hints to all new functions
- The `is_safe_url()` helper must be added to `core/views.py` (shared utilities)
- The rate limit helper must be added to `imports/views.py`
- All existing tests must continue to pass

---

## Files to Modify

1. `core/views.py` — Add `is_safe_url()` helper
2. `core/views_auth.py` — Use `is_safe_url()` for next parameter validation
3. `imports/views.py` — Add `_check_upload_rate_limit()` and apply to upload views

---

## Tests

- Unit test for `is_safe_url()`: Test with same-origin URLs (should pass), cross-origin URLs (should fail), protocol-relative URLs (should fail)
- Unit test for upload rate limiting: Simulate 11 rapid uploads, verify 11th is rejected with 429
- Unit test for CSRF: Verify POST to import views without CSRF token returns 403

---

## Manual Steps Required

- [ ] `python manage.py makemigrations` (if any model changes)
- [ ] `python manage.py migrate --settings=...`
- [ ] Review and push feature branch: `git push origin feature_rbc_pdf_fixes`
- [ ] Run full test suite: `python run_tests.py`

---

## Human Approval Required

Human approval required before any git push or DB changes.
