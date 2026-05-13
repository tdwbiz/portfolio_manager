# Security Hardening Guide

This document outlines the security improvements implemented in the Portfolio Manager application and provides deployment recommendations.

## Summary of Security Fixes

### 1. Critical Settings Hardening

#### DEBUG Mode
- **Issue**: DEBUG=True exposes sensitive information in production
- **Fix**: DEBUG is now controlled via `DEBUG` environment variable, defaults to `False`
- **Action Required**: Ensure `DEBUG=false` in production `.env` file

#### SECRET_KEY
- **Issue**: Fallback to `get_random_secret_key()` breaks sessions on every restart
- **Fix**: SECRET_KEY now **requires** `DJANGO_SECRET_KEY` environment variable; raises error if not set
- **Action Required**: Generate a secure key and set it in production:
  ```bash
  python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
  ```

#### ALLOWED_HOSTS
- **Issue**: Empty ALLOWED_HOSTS is dangerous and vulnerable to Host header attacks
- **Fix**: Must be explicitly set via `ALLOWED_HOSTS` environment variable for production
- **Action Required**: Set your domain names:
  ```
  ALLOWED_HOSTS=example.com,www.example.com,api.example.com
  ```

### 2. Database Security

#### Credentials
- **Issue**: Database username defaulted to 'sa' (SQL Server admin) with empty password
- **Fix**: DB_USER and DB_PASSWORD now require environment variables in production
- **Action Required**: Set secure credentials:
  ```
  DB_USER=database_user_account
  DB_PASSWORD=strong_password_here
  ```

#### Certificate Validation
- **Issue**: TrustServerCertificate=yes disables certificate validation, vulnerable to MITM attacks
- **Fix**: Defaults to proper certificate validation; can be disabled via `TRUST_SERVER_CERTIFICATE` only if necessary
- **Action Required**: Keep `TRUST_SERVER_CERTIFICATE=false` (default) unless you have specific reasons to disable it

### 3. Cookie & Session Security

#### Secure Cookies
- **New Settings**:
  - `SESSION_COOKIE_SECURE`: Requires HTTPS (set via environment)
  - `CSRF_COOKIE_SECURE`: Requires HTTPS (set via environment)
  - `SESSION_COOKIE_HTTPONLY`: JavaScript cannot access session cookie ✓
  - `CSRF_COOKIE_HTTPONLY`: JavaScript cannot access CSRF cookie ✓
  - `SESSION_COOKIE_SAMESITE=Strict`: Prevents CSRF attacks ✓

- **Action Required**: In production, enable:
  ```
  SESSION_COOKIE_SECURE=true
  CSRF_COOKIE_SECURE=true
  ```

#### HTTPS Enforcement
- **New Settings**:
  - `SECURE_SSL_REDIRECT`: Redirect HTTP → HTTPS
  - `SECURE_HSTS_SECONDS`: HTTP Strict-Transport-Security header
  - `SECURE_HSTS_INCLUDE_SUBDOMAINS`: Apply to all subdomains
  - `SECURE_HSTS_PRELOAD`: Register for HSTS preload list

- **Action Required**: In production:
  ```
  SECURE_SSL_REDIRECT=true
  SECURE_HSTS_SECONDS=31536000
  SECURE_HSTS_INCLUDE_SUBDOMAINS=true
  SECURE_HSTS_PRELOAD=false  # Set to true after careful consideration
  ```

### 4. Authentication Security

#### Login Rate Limiting
- **Issue**: No rate limiting on login attempts, vulnerable to brute-force attacks
- **Fix**: Added rate limiting with configurable limits and time windows
- **Settings**:
  - `LOGIN_ATTEMPT_LIMIT=5` (max failed attempts)
  - `LOGIN_ATTEMPT_WINDOW=900` (15 minutes)
  - Uses configurable cache backend (in-memory or Redis)

- **Action Required**: Configure cache backend for production:
  ```
  # For production, use Redis instead of in-memory:
  CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
  CACHE_LOCATION=redis://your-redis-host:6379/1
  ```

#### Login Input Validation
- Added validation for username/password presence
- Added open redirect prevention
- IP address logging for failed attempts

### 5. Authorization & Data Access

#### Account Deletion
- **Issue**: No authorization check; any logged-in user could delete any account
- **Fix**: Added authorization check with logging
- **TODO**: Implement ownership validation for multi-tenant deployment:
  ```python
  if account.owner_id != request.user.id and not request.user.is_staff:
      return HttpResponse('Unauthorized', status=403)
  ```

#### Data Exports
- **Issue**: Users could export all company data without validation
- **Fix**: 
  - Added input validation to filters
  - Date parameters validated with `parse_date()`
  - Broker/account/symbol filters validated against database
  - Length limits on string parameters to prevent abuse
  - Activity logged for audit trail

### 6. File Access Security

#### Rejection Log Downloads
- **Issue**: Insufficient path traversal validation
- **Fix**:
  - Added `_is_path_safe()` function to prevent directory traversal
  - Added login requirement
  - Enhanced validation for month parameter
  - Proper error handling and logging
  - Context manager for file operations

### 7. Input Validation

#### Transaction Filters
- Added string length limits (prevent memory exhaustion)
- Date validation using Django's `parse_date()`
- Database existence checks for foreign keys
- Case normalization for symbol/type fields

#### Security Helpers Added
- `_check_login_attempts()`: Rate limit enforcement
- `_record_login_attempt()`: Track failed attempts
- `_get_client_ip()`: Get IP accounting for proxies
- `_is_path_safe()`: Prevent path traversal
- `_validate_date_param()`: Validate date inputs

### 8. Configuration Management

#### Environment Variables
All configuration now supports environment variables for production:

**Database**:
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`

**Security**:
- `DJANGO_SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`
- `TRUST_SERVER_CERTIFICATE`

**HTTPS**:
- `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`
- `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`

**UI**:
- `TRANSACTIONS_PAGE_SIZE`, `ACCOUNT_DETAIL_PAGE_SIZE`, `DASHBOARD_RECENT_LIMIT`

**Cache**:
- `CACHE_BACKEND`, `CACHE_LOCATION`
- `PRICE_CACHE_TIMEOUT_SECONDS`, `HISTORICAL_PRICE_CACHE_TIMEOUT_SECONDS`

**Logging**:
- `LOG_FILE_MAX_BYTES`, `LOG_FILE_BACKUP_COUNT`

**Login Security**:
- `LOGIN_ATTEMPT_LIMIT`, `LOGIN_ATTEMPT_WINDOW`

## Deployment Checklist

### Before Production Deployment

- [ ] Generate secure `DJANGO_SECRET_KEY`
- [ ] Set strong `DB_USER` and `DB_PASSWORD`
- [ ] Configure `ALLOWED_HOSTS` with actual domain names
- [ ] Set `DEBUG=false`
- [ ] Enable HTTPS and configure:
  - [ ] `SECURE_SSL_REDIRECT=true`
  - [ ] `SESSION_COOKIE_SECURE=true`
  - [ ] `CSRF_COOKIE_SECURE=true`
  - [ ] `SECURE_HSTS_SECONDS=31536000`
- [ ] Configure cache backend (preferably Redis for production)
- [ ] Set up proper logging and monitoring
- [ ] Review and test login rate limiting
- [ ] Implement authorization checks for multi-tenant deployments
- [ ] Configure web server (Nginx/Apache) to enforce HTTPS
- [ ] Use a proper WSGI server (Gunicorn, uWSGI, not development server)
- [ ] Keep dependencies up to date: `pip install --upgrade -r requirements.txt`

### Database Security

1. **Don't use 'sa' account**:
   ```sql
   CREATE USER portfolio_user WITH PASSWORD 'strong_password'
   GRANT CONNECT, SELECT, INSERT, UPDATE, DELETE ON [DATABASE] TO portfolio_user
   ```

2. **Validate SSL Certificates**: Remove `TrustServerCertificate=yes`

3. **Use Encrypted Connections**: Ensure `Encrypt=yes` is set

### Web Server Configuration

#### Nginx Example
```nginx
# Force HTTPS
server {
    listen 80;
    server_name example.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name example.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    
    # Add HSTS header
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Redis Cache Setup (Recommended for Production)

```bash
# Install redis-py
pip install redis

# In Docker or on server, run Redis
docker run -d -p 6379:6379 redis:latest

# Set in .env:
CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
CACHE_LOCATION=redis://127.0.0.1:6379/1
```

## Monitoring & Logging

### Enable Security Logging
The application logs:
- Failed login attempts (IP address, username)
- Account modifications
- Data exports (user, filters, record count)
- Authorization failures
- File access and downloads
- Invalid parameter attempts

### Monitor These Events
```python
# View security logs
tail -f logs/portfolio_manager.log | grep "WARNING\|ERROR"

# Count failed logins by IP
grep "Failed login attempt" logs/portfolio_manager.log | cut -d' ' -f10 | sort | uniq -c
```

## Multi-Tenant Deployment

If deploying as a multi-tenant system:

1. **Add User Ownership Checks**: Uncomment the authorization checks in:
   - `account_delete()` - Verify account ownership
   - Add similar checks to all data access views

2. **Implement Organization/Tenant Isolation**:
   - Add tenant/organization field to Account model
   - Filter all queries by current user's organization
   - Implement permission system (Django Guardian recommended)

3. **Review Code**:
   - Search for TODO comments in `core/views.py` marked with "SECURITY"
   - Implement multi-tenant authorization checks

## Security Best Practices

### Regular Updates
```bash
# Check for security updates
pip list --outdated

# Update all packages
pip install --upgrade -r requirements.txt

# Pin versions in production
pip freeze > requirements.lock
```

### Secret Management
- Never commit `.env` files
- Use environment variable management (AWS Secrets Manager, HashiCorp Vault, etc.)
- Rotate secrets periodically
- Use different secrets for different environments

### Database Backups
- Regular automated backups
- Test backup restoration
- Secure backup storage
- Encrypt backups at rest and in transit

### Access Control
- Limit database access to application server
- Use firewalls to restrict network access
- Implement VPN/SSH access for administration
- Use strong SSH keys (no password auth)

## Reporting Security Issues

Please report security vulnerabilities through responsible disclosure. Do not open public issues for security problems.

---

**Last Updated**: May 13, 2026  
**Django Version**: 5.1  
**Python Version**: 3.10+
