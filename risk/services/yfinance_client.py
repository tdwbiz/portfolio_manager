"""
yFinance client for fetching market data.

Provides a thin wrapper around yfinance.download with retry logic,
ticker mapping, and mutual fund detection.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from django.conf import settings

from risk.models import SecurityUniverse

logger = logging.getLogger(__name__)

# Known mutual funds not available via yFinance
MUTUAL_FUNDS = {'RBF2010', 'RBF2014'}


def resolve_yfinance_ticker(symbol: str) -> Optional[str]:
    """
    Resolve a database symbol to a yFinance ticker.

    Rules:
    1. If rm_security_universe.amibroker_symbol is populated, use that.
    2. Else if primary_currency == 'CAD', append '.TO'.
    3. Else use symbol as-is.

    Returns None if the symbol is a known mutual fund.
    """
    if symbol in MUTUAL_FUNDS:
        logger.info(
            "Symbol %s: mutual fund not available via yFinance — skipping", symbol
        )
        return None

    try:
        sec = SecurityUniverse.objects.get(symbol=symbol)
    except SecurityUniverse.DoesNotExist:
        logger.warning("Symbol %s not found in rm_security_universe", symbol)
        return None

    # Rule 1: use amibroker_symbol if populated
    if sec.amibroker_symbol:
        logger.info(
            "Ticker mapping applied: %s → %s (from amibroker_symbol)",
            symbol, sec.amibroker_symbol,
        )
        return sec.amibroker_symbol

    # Rule 2: CAD symbols get .TO suffix
    if sec.primary_currency == 'CAD':
        ticker = f"{symbol}.TO"
        logger.info(
            "Ticker mapping applied: %s → %s (CAD symbol)", symbol, ticker
        )
        return ticker

    # Rule 3: use as-is
    return symbol


def fetch_symbol(
    ticker: str,
    start: date,
    end: date,
    retry_attempts: Optional[int] = None,
    retry_delay: Optional[int] = None,
    timeout: Optional[int] = None,
) -> pd.DataFrame:
    """
    Fetch price data for a yFinance ticker.

    Returns DataFrame with columns: Open, High, Low, Close, Volume.
    Index: DatetimeIndex. May return empty DataFrame if no data available.
    Retries on failure.
    """
    import yfinance as yf

    retry_attempts = retry_attempts or getattr(settings, 'YFINANCE_RETRY_ATTEMPTS', 3)
    retry_delay = retry_delay or getattr(settings, 'YFINANCE_RETRY_DELAY_SECONDS', 5)
    timeout = timeout or getattr(settings, 'YFINANCE_REQUEST_TIMEOUT', 30)

    last_error = None
    for attempt in range(1, retry_attempts + 1):
        try:
            logger.info(
                "Fetching %s from %s to %s (attempt %d/%d)",
                ticker, start, end, attempt, retry_attempts,
            )
            df = yf.download(
                ticker,
                start=start.isoformat(),
                end=end.isoformat(),
                auto_adjust=True,
                progress=False,
                timeout=timeout,
            )
            if df.empty:
                logger.warning("No data returned for %s", ticker)
                return pd.DataFrame()
            return df
        except Exception as exc:
            last_error = exc
            logger.warning(
                "yFinance fetch failed for %s (attempt %d/%d): %s",
                ticker, attempt, retry_attempts, exc,
            )
            if attempt < retry_attempts:
                time.sleep(retry_delay)

    logger.error(
        "All %d attempts failed for %s: %s", retry_attempts, ticker, last_error,
    )
    return pd.DataFrame()
