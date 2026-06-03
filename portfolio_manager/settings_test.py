"""Test settings — uses SQLite so tests run without an MSSQL server."""

from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Amibroker export watch folder
AMIBROKER_WATCH_FOLDER = r'C:\AI-Workspace\amibroker_exports'

# yFinance fetch settings
YFINANCE_DEFAULT_HISTORY_YEARS = 10
YFINANCE_RETRY_ATTEMPTS = 3
YFINANCE_RETRY_DELAY_SECONDS = 5
YFINANCE_REQUEST_TIMEOUT = 30

# Gap detection threshold
PRICE_GAP_WARNING_DAYS = 5
PRICE_GAP_CRITICAL_DAYS = 10
