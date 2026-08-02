"""Test settings — uses SQLite so tests run without an MSSQL server."""

import tempfile

from .settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "db_test.sqlite3",
    }
}

# Amibroker export watch folder (temp dir for tests)
AMIBROKER_WATCH_FOLDER = tempfile.mkdtemp(prefix="amibroker_")

# VL model settings for tests
VL_MODEL_BASE_URL = "http://127.0.0.1:11440/v1"

# yFinance fetch settings
YFINANCE_DEFAULT_HISTORY_YEARS = 10
YFINANCE_RETRY_ATTEMPTS = 3
YFINANCE_RETRY_DELAY_SECONDS = 5
YFINANCE_REQUEST_TIMEOUT = 30

# Gap detection threshold
PRICE_GAP_WARNING_DAYS = 5
PRICE_GAP_CRITICAL_DAYS = 10

# Suppress naive datetime warnings for auto_now_add fields during tests
import warnings

warnings.filterwarnings(
    "ignore",
    message=".*DateTimeField.*received a naive datetime.*",
    category=RuntimeWarning,
)
