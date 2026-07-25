"""
Django settings for portfolio_manager project.
"""

import configparser
import logging.handlers  # noqa: F401
import os
from pathlib import Path

from dotenv import load_dotenv

# Load secrets from .env (never committed to git)
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load non-secret app config from config.ini (committed to git)
_cfg = configparser.ConfigParser()
_cfg.read(BASE_DIR / "config.ini")

# SECURITY: SECRET_KEY is REQUIRED and must not fall back to a generated key.
# A generated key invalidates all sessions on every restart.
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError(
        "DJANGO_SECRET_KEY environment variable must be set. "
        'Generate a secure key with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"'
    )

# SECURITY: DEBUG must be explicitly enabled via environment variable.
# Default to False for security.
DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")

# SECURITY: ALLOWED_HOSTS must be explicitly set via environment variable.
# Empty list is insecure and will raise an error if DEBUG is False.
ALLOWED_HOSTS_STR = os.getenv("ALLOWED_HOSTS", "")
if not ALLOWED_HOSTS_STR and not DEBUG:
    raise ValueError(
        "ALLOWED_HOSTS environment variable must be set for production. "
        'Provide as comma-separated list (e.g., "example.com,www.example.com")'
    )
ALLOWED_HOSTS = (
    [h.strip() for h in ALLOWED_HOSTS_STR.split(",") if h.strip()]
    if ALLOWED_HOSTS_STR
    else ["localhost", "127.0.0.1"]
)

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core",
    "imports",
    "risk",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "portfolio_manager.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.branding",
                "core.context_processors.alert_badge",
            ],
        },
    },
]

WSGI_APPLICATION = "portfolio_manager.wsgi.application"

# Database
# https://docs.djangoproject.com/en/5.1/ref/settings/#databases

DB_NAME = os.getenv("DB_NAME", "PortfolioDev")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "1433")

if not DB_USER and not DEBUG:
    raise ValueError("DB_USER environment variable must be set for production.")
if not DB_PASSWORD and not DEBUG:
    raise ValueError("DB_PASSWORD environment variable must be set for production.")

# SECURITY: TrustServerCertificate=yes disables certificate validation.
# In production, use proper certificate validation.
TRUST_SERVER_CERTIFICATE = os.getenv("TRUST_SERVER_CERTIFICATE", "False").lower() in (
    "true",
    "1",
    "yes",
)

DATABASES = {
    "default": {
        "ENGINE": "mssql",
        "NAME": DB_NAME,
        "USER": DB_USER or "sa",  # Use fallback only in development
        "PASSWORD": DB_PASSWORD or "",  # Use fallback only in development
        "HOST": DB_HOST,
        "PORT": DB_PORT,
        "OPTIONS": {
            "driver": "ODBC Driver 18 for SQL Server",
            "extra_params": f"Encrypt=yes;TrustServerCertificate={'yes' if TRUST_SERVER_CERTIFICATE else 'no'};",
        },
    }
}

# Use SQLite for testing when running `manage.py test`
# This avoids MSSQL permission issues with test database creation
if os.getenv("DJANGO_TESTING"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"  # Eastern Standard Time (EST/EDT)
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Authentication settings
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

# SECURITY: Secure cookie settings for production
# Set to True in production (enforce via environment variable)
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "False").lower() in (
    "true",
    "1",
    "yes",
)
CSRF_COOKIE_SECURE = os.getenv("CSRF_COOKIE_SECURE", "False").lower() in (
    "true",
    "1",
    "yes",
)
SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to session cookie
CSRF_COOKIE_HTTPONLY = True  # Prevent JavaScript access to CSRF cookie
SESSION_COOKIE_SAMESITE = "Strict"  # Prevent CSRF attacks

# SECURITY: Enforce HTTPS redirects in production
SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "False").lower() in (
    "true",
    "1",
    "yes",
)
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv(
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", "False"
).lower() in ("true", "1", "yes")
SECURE_HSTS_PRELOAD = os.getenv("SECURE_HSTS_PRELOAD", "False").lower() in (
    "true",
    "1",
    "yes",
)

# Logging Configuration
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Auto-import settings — prefer environment variables, fall back to config.ini, then hardcoded defaults
_inbox_raw = (
    os.getenv("IMPORT_INBOX_DIR", "").strip()
    or _cfg.get("import", "inbox_dir", fallback="").strip()
)
IMPORT_INBOX_DIR = Path(_inbox_raw) if _inbox_raw else BASE_DIR / "inbox"

_processed_raw = (
    os.getenv("IMPORT_PROCESSED_DIR", "").strip()
    or _cfg.get("import", "processed_dir", fallback="").strip()
)
IMPORT_PROCESSED_DIR = (
    Path(_processed_raw) if _processed_raw else BASE_DIR / "processed"
)

_logs_raw = (
    os.getenv("IMPORT_LOGS_DIR", "").strip()
    or _cfg.get("import", "logs_dir", fallback="").strip()
)
IMPORT_LOGS_DIR = Path(_logs_raw) if _logs_raw else BASE_DIR / "logs"

_json_raw = (
    os.getenv("IMPORT_JSON_DIR", "").strip()
    or _cfg.get("import", "json_dir", fallback="").strip()
)
IMPORT_JSON_DIR = Path(_json_raw) if _json_raw else BASE_DIR / "processed" / "json_logs"

# Vision-language (VL) model settings — qwen2.5-vl-72b on port 11440 via llama.cpp
VL_MODEL_BASE_URL = os.getenv("VL_MODEL_BASE_URL", "http://127.0.0.1:11440/v1")
VL_MODEL_NAME = os.getenv("VL_MODEL_NAME", "qwen2.5-vl")
VL_PAGE_DPI = int(os.getenv("VL_PAGE_DPI", "150"))
VL_MAX_RETRIES = int(os.getenv("VL_MAX_RETRIES", "2"))
VL_TIMEOUT_SECONDS = int(os.getenv("VL_TIMEOUT_SECONDS", "600"))  # 10 minutes for large PDFs
VL_MAX_PAGES = int(os.getenv("VL_MAX_PAGES", "3"))
VL_MONTHLY_MAX_PAGES = int(
    os.getenv("VL_MONTHLY_MAX_PAGES", "0")
)  # 0 = unlimited — render all pages so holdings on later pages are captured

# UI configuration via environment variables
IMPORT_INBOX_POLL_INTERVAL = int(
    os.getenv(
        "IMPORT_INBOX_POLL_INTERVAL",
        _cfg.get("import", "poll_interval", fallback="300"),
    )
)
TRANSACTIONS_PAGE_SIZE = int(
    os.getenv(
        "TRANSACTIONS_PAGE_SIZE",
        _cfg.get("ui", "transactions_page_size", fallback="50"),
    )
)
ACCOUNT_DETAIL_PAGE_SIZE = int(
    os.getenv(
        "ACCOUNT_DETAIL_PAGE_SIZE",
        _cfg.get("ui", "account_detail_page_size", fallback="50"),
    )
)
DASHBOARD_RECENT_LIMIT = int(os.getenv("DASHBOARD_RECENT_LIMIT", "10"))

# Cache configuration via environment variables
PRICE_CACHE_TIMEOUT = int(
    os.getenv(
        "PRICE_CACHE_TIMEOUT_SECONDS",
        _cfg.get("cache", "price_timeout_seconds", fallback="900"),
    )
)
HISTORICAL_PRICE_CACHE_TIMEOUT = int(
    os.getenv(
        "HISTORICAL_PRICE_CACHE_TIMEOUT_SECONDS",
        _cfg.get("cache", "historical_price_timeout_seconds", fallback="3600"),
    )
)
PRICE_CACHE_TIMEOUT_MINUTES = max(1, (PRICE_CACHE_TIMEOUT + 59) // 60)

# Logging configuration via environment variables
LOG_FILE_MAX_BYTES = int(
    os.getenv(
        "LOG_FILE_MAX_BYTES",
        _cfg.get("logging", "file_max_bytes", fallback=str(5 * 1024 * 1024)),
    )
)
LOG_FILE_BACKUP_COUNT = int(
    os.getenv(
        "LOG_FILE_BACKUP_COUNT", _cfg.get("logging", "file_backup_count", fallback="5")
    )
)

# Login security configuration
LOGIN_ATTEMPT_LIMIT = int(
    os.getenv("LOGIN_ATTEMPT_LIMIT", "5")
)  # Max attempts before lockout
LOGIN_ATTEMPT_WINDOW = int(
    os.getenv("LOGIN_ATTEMPT_WINDOW", "900")
)  # Time window in seconds (15 minutes)

# Caching configuration for rate limiting and session data
# Uses in-memory cache by default; switch to Redis for production
CACHES = {
    "default": {
        "BACKEND": os.getenv(
            "CACHE_BACKEND", "django.core.cache.backends.locmem.LocMemCache"
        ),
        "LOCATION": os.getenv("CACHE_LOCATION", "unique-snowflake"),
    }
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": BASE_DIR / "logs" / "portfolio_manager.log",
            "maxBytes": LOG_FILE_MAX_BYTES,
            "backupCount": LOG_FILE_BACKUP_COUNT,
            "formatter": "verbose",
            "encoding": "utf-8",
            "mode": "a",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "INFO",
    },
    # Suppress noisy third-party debug logs
    "loggers": {
        "pdfminer": {"level": "WARNING"},
        "pdfplumber": {"level": "WARNING"},
        "mssql": {"level": "WARNING"},
        "django": {"level": "WARNING"},
    },
}

# Amibroker export watch folder (configurable via environment variable)
AMIBROKER_WATCH_FOLDER = os.getenv(
    "AMIBROKER_WATCH_FOLDER",
    os.path.join(BASE_DIR, "amibroker_exports"),
)

# yFinance fetch settings
YFINANCE_DEFAULT_HISTORY_YEARS = 10  # Full history on first fetch
YFINANCE_RETRY_ATTEMPTS = 3
YFINANCE_RETRY_DELAY_SECONDS = 5
YFINANCE_REQUEST_TIMEOUT = 30

# Gap detection threshold
PRICE_GAP_WARNING_DAYS = 5  # Warn if gap > 5 business days
PRICE_GAP_CRITICAL_DAYS = 10  # Critical if gap > 10 business days
