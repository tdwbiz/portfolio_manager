"""Test settings — uses SQLite so tests run without an MSSQL server."""

import tempfile

from .settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "db_test.sqlite3",
    }
}

# Fake RBC accounts for tests — no .env needed.
# (Labels ACCOUNT_A ... ACCOUNT_H correspond to the fake IDs 90000001-90000008.)
RBC_ACCOUNTS = {
    "90000001": "ACCOUNT_A",
    "90000002": "ACCOUNT_B",
    "90000003": "ACCOUNT_C",
    "90000004": "ACCOUNT_D",
    "90000005": "ACCOUNT_E",
    "90000006": "ACCOUNT_F",
    "90000007": "ACCOUNT_G",
    "90000008": "ACCOUNT_H",
}
RBC_DEFAULT_ACCOUNT = "90000001"

# Amibroker export watch folder (temp dir for tests)
AMIBROKER_WATCH_FOLDER = tempfile.mkdtemp(prefix="amibroker_")

# VL model settings for tests (same defaults production uses — Halogen)
VL_MODEL_BASE_URL = "http://127.0.0.1:11435/v1"
VL_MODEL_NAME = "halogen-qwen3.8-flash-next"

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
