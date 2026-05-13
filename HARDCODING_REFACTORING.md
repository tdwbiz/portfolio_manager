# Hardcoding Refactoring Summary

This document summarizes all the hardcoded values that have been refactored to use environment variables and configuration management.

## Configuration Management Strategy

The application now follows a three-tier configuration approach:

1. **Environment Variables** (Highest Priority - Required for Production)
   - Set via `.env` file or system environment
   - Used for security-sensitive values and deployment-specific settings

2. **config.ini** (Medium Priority - Optional for backward compatibility)
   - Committed to version control
   - Used for non-secret application configuration
   - Acts as fallback for some settings

3. **Hardcoded Defaults** (Lowest Priority - Development fallback only)
   - Built into code
   - Only used when environment variable and config.ini not provided
   - Suitable for development/testing only

## Changes by Category

### 1. Security Settings

| Setting | Before | After | Env Variable |
|---------|--------|-------|--------------|
| DEBUG Mode | `DEBUG = True` (hardcoded) | Environment controlled, defaults to False | `DEBUG` |
| Secret Key | Generated on each restart (broken) | Required environment variable | `DJANGO_SECRET_KEY` |
| Allowed Hosts | Empty list `[]` (dangerous) | Environment required for production | `ALLOWED_HOSTS` |
| DB Username | Hardcoded `'sa'` (admin account) | Optional environment variable | `DB_USER` |
| DB Password | Hardcoded empty `''` (dangerous) | Optional environment variable | `DB_PASSWORD` |
| SSL Validation | Hardcoded `TrustServerCertificate=yes` | Configurable, defaults to secure | `TRUST_SERVER_CERTIFICATE` |

### 2. Authentication & Authorization

| Setting | Before | After | Env Variable |
|---------|--------|-------|--------------|
| Login Rate Limit | Not implemented | Configurable limit | `LOGIN_ATTEMPT_LIMIT` |
| Rate Limit Window | Not implemented | Configurable time window | `LOGIN_ATTEMPT_WINDOW` |

**Details**:
```python
# Before: No rate limiting
def login(request):
    username = request.POST.get('username')
    password = request.POST.get('password')
    # ... no protection against brute-force

# After: Rate limited with caching
def login(request):
    client_ip = _get_client_ip(request)
    if not _check_login_attempts(client_ip):
        return render(..., status=429)  # Too Many Requests
    # ... attempts are tracked and limited
```

### 3. Cookie & Session Security

| Setting | Before | After | Env Variable |
|---------|--------|-------|--------------|
| Session Secure Flag | Not configured | Environment controlled | `SESSION_COOKIE_SECURE` |
| CSRF Secure Flag | Not configured | Environment controlled | `CSRF_COOKIE_SECURE` |
| Session HTTPOnly | Not configured | **Always True** (hardcoded secure) | N/A |
| CSRF HTTPOnly | Not configured | **Always True** (hardcoded secure) | N/A |
| Session SameSite | Not configured | **Strict** (hardcoded secure) | N/A |
| SSL Redirect | Not configured | Environment controlled | `SECURE_SSL_REDIRECT` |
| HSTS Header | Not configured | Environment controlled | `SECURE_HSTS_SECONDS` |

### 4. Cache Configuration

| Setting | Before | After | Env Variable |
|---------|--------|-------|--------------|
| Cache Backend | Not configured (no caching for rate limit) | Environment controlled | `CACHE_BACKEND` |
| Cache Location | Not configured | Environment controlled | `CACHE_LOCATION` |
| Price Cache Timeout | Hardcoded `900` (seconds) | Config.ini fallback with env override | `PRICE_CACHE_TIMEOUT_SECONDS` |
| Historical Cache Timeout | Hardcoded `3600` (seconds) | Config.ini fallback with env override | `HISTORICAL_PRICE_CACHE_TIMEOUT_SECONDS` |

**Code Changes**:
```python
# Before: Hardcoded and static
PRICE_CACHE_TIMEOUT = _cfg.getint('cache', 'price_timeout_seconds', fallback=900)

# After: Environment controlled with fallback
PRICE_CACHE_TIMEOUT = int(os.getenv('PRICE_CACHE_TIMEOUT_SECONDS', 
    _cfg.get('cache', 'price_timeout_seconds', fallback='900')))

# Added caching configuration
CACHES = {
    'default': {
        'BACKEND': os.getenv('CACHE_BACKEND', 'django.core.cache.backends.locmem.LocMemCache'),
        'LOCATION': os.getenv('CACHE_LOCATION', 'unique-snowflake'),
    }
}
```

### 5. UI & Pagination Settings

| Setting | Before | After | Env Variable |
|---------|--------|-------|--------------|
| Transactions Page Size | Hardcoded in config.ini: `50` | Config.ini fallback with env override | `TRANSACTIONS_PAGE_SIZE` |
| Account Detail Page Size | Hardcoded in config.ini: `50` | Config.ini fallback with env override | `ACCOUNT_DETAIL_PAGE_SIZE` |
| Dashboard Recent Items | Hardcoded in code: `10` | Environment controlled, defaults to 10 | `DASHBOARD_RECENT_LIMIT` |

**Code Changes**:
```python
# Before: Hardcoded constants
recent_transactions = Transaction.objects.all()[:10]
recent_imports = FileImportLog.objects.all()[:10]

# After: Uses configurable setting
recent_transactions = Transaction.objects.all()[:settings.DASHBOARD_RECENT_LIMIT]
recent_imports = FileImportLog.objects.all()[:settings.DASHBOARD_RECENT_LIMIT]
```

### 6. Logging Configuration

| Setting | Before | After | Env Variable |
|---------|--------|-------|--------------|
| Log File Max Bytes | Hardcoded `5242880` (5MB) | Config.ini fallback with env override | `LOG_FILE_MAX_BYTES` |
| Log Backup Count | Hardcoded `5` | Config.ini fallback with env override | `LOG_FILE_BACKUP_COUNT` |

**Code Changes**:
```python
# Before: Hardcoded fallbacks
LOG_FILE_MAX_BYTES = _cfg.getint('logging', 'file_max_bytes', fallback=5 * 1024 * 1024)

# After: Environment controlled
LOG_FILE_MAX_BYTES = int(os.getenv('LOG_FILE_MAX_BYTES', 
    _cfg.get('logging', 'file_max_bytes', fallback=str(5 * 1024 * 1024))))
```

### 7. Import Directories

| Setting | Before | After | Env Variable |
|---------|--------|-------|--------------|
| Inbox Directory | Only config.ini | Environment controlled with fallback | `IMPORT_INBOX_DIR` |
| Processed Directory | Only config.ini | Environment controlled with fallback | `IMPORT_PROCESSED_DIR` |
| Logs Directory | Only config.ini | Environment controlled with fallback | `IMPORT_LOGS_DIR` |
| Poll Interval | Only config.ini | Environment controlled with fallback | `IMPORT_INBOX_POLL_INTERVAL` |

**Code Changes**:
```python
# Before: Only config.ini support
IMPORT_INBOX_DIR = Path(_cfg.get('import', 'inbox_dir', fallback='')) or BASE_DIR / 'inbox'

# After: Environment first, config.ini fallback
IMPORT_INBOX_DIR = Path(
    os.getenv('IMPORT_INBOX_DIR', '') or 
    _cfg.get('import', 'inbox_dir', fallback='')
) or BASE_DIR / 'inbox'
```

### 8. Business Logic Hardcoding

The following hardcoded constants in `core/views.py` remain hardcoded but are now better documented:

```python
# Transaction type labels - could be moved to database
TRANSACTION_TYPE_LABELS = {
    'BUY': 'Buy',
    'SELL': 'Sell',
    'DIV': 'Dividend',
    # ... etc
}

# Position transaction types - could be configurable
POSITION_TRANSACTION_TYPES = ('BUY', 'SELL')
CASH_ACTIVITY_TRANSACTION_TYPES = ('BUY', 'SELL', 'DIV', 'DIST', 'INT', 'WITHTAX', 'TAX')

# Export headers - could be configurable
TRANSACTION_EXPORT_HEADERS = [
    'Date', 'Symbol', 'Type', 'Quantity', 'Price', ...
]
```

**Future Improvements**:
- Move transaction types to database (TransactionType model)
- Make export headers configurable via admin interface
- Allow custom report definitions

## Configuration Priority Order

The application now uses this configuration resolution order:

```python
# Example for any setting
setting_value = (
    os.getenv('SETTING_NAME') or                    # 1. Environment Variable (highest)
    _cfg.get('section', 'key', fallback='') or      # 2. config.ini
    'hardcoded_default'                              # 3. Hardcoded Default (lowest)
)
```

## Benefits of Changes

### Security
- ✅ Different secrets for different environments
- ✅ No hardcoded production credentials in code
- ✅ Secrets never committed to version control
- ✅ Easier rotation of credentials

### Operations
- ✅ Configuration without code changes
- ✅ Easy deployment to multiple environments
- ✅ Docker-friendly (environment variables)
- ✅ CI/CD pipeline friendly

### Maintainability
- ✅ Clear audit trail of configuration
- ✅ Environment parity easier to achieve
- ✅ Configuration centralized in `.env`
- ✅ Backward compatibility with config.ini

### Flexibility
- ✅ Can adjust settings per deployment
- ✅ Different cache backends for dev/prod
- ✅ Configurable rate limiting
- ✅ Per-environment logging levels

## Migration Path

If upgrading from an older version:

1. **Copy .env.example to .env**:
   ```bash
   cp .env.example .env
   ```

2. **Update .env with production values**:
   ```bash
   nano .env
   # Set DJANGO_SECRET_KEY, DB credentials, etc.
   ```

3. **Verify config.ini still works** (backward compatible):
   ```bash
   cat config.ini
   # Environment variables will override these settings
   ```

4. **Test with environment variables**:
   ```bash
   DEBUG=false ALLOWED_HOSTS=example.com python manage.py check --deploy
   ```

5. **Gradually move config.ini values to .env**:
   - Test one environment variable at a time
   - Verify application behavior hasn't changed
   - Remove from config.ini once confident

## Example: Development vs Production

### Development (.env)
```bash
DEBUG=true
DJANGO_SECRET_KEY=dev-key-not-secret
ALLOWED_HOSTS=localhost,127.0.0.1
DB_USER=dev_user
DB_PASSWORD=dev_password
SESSION_COOKIE_SECURE=false
CACHE_BACKEND=django.core.cache.backends.locmem.LocMemCache
LOGIN_ATTEMPT_LIMIT=100  # Relaxed for testing
```

### Production (.env)
```bash
DEBUG=false
DJANGO_SECRET_KEY=<generated-secure-key>
ALLOWED_HOSTS=example.com,www.example.com
DB_USER=<secure-username>
DB_PASSWORD=<secure-password>
SESSION_COOKIE_SECURE=true
SECURE_SSL_REDIRECT=true
CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
LOGIN_ATTEMPT_LIMIT=5  # Strict for security
```

## Docker Support

The refactored configuration is Docker-friendly:

```dockerfile
FROM python:3.10

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Environment variables set at runtime
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "portfolio_manager.wsgi"]
```

```bash
# Run with environment variables
docker run -e DJANGO_SECRET_KEY=xyz -e DB_USER=user -e ALLOWED_HOSTS=example.com portfolio-manager
```

## Kubernetes Support

Using ConfigMap and Secrets:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: portfolio-config
data:
  DEBUG: "false"
  ALLOWED_HOSTS: "example.com,www.example.com"
  CACHE_BACKEND: "django.core.cache.backends.redis.RedisCache"
---
apiVersion: v1
kind: Secret
metadata:
  name: portfolio-secrets
type: Opaque
stringData:
  DJANGO_SECRET_KEY: "..."
  DB_USER: "..."
  DB_PASSWORD: "..."
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: portfolio-manager
spec:
  containers:
  - name: app
    image: portfolio-manager:latest
    envFrom:
    - configMapRef:
        name: portfolio-config
    - secretRef:
        name: portfolio-secrets
```

## Verification Checklist

After deploying configuration changes:

- [ ] `python manage.py check --deploy` passes all checks
- [ ] Django loads SECRET_KEY from environment variable
- [ ] ALLOWED_HOSTS is set correctly
- [ ] Debug mode is off in production
- [ ] HTTPS is enforced
- [ ] Rate limiting is working (test with wrong credentials)
- [ ] Cache backend is configured correctly
- [ ] Logs are being written with correct rotation
- [ ] Database connection is secure (Encrypt=yes, TrustServerCertificate=no)

---

**Last Updated**: May 13, 2026  
**Configuration Version**: 1.0
