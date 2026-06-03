"""
Volatility summary computation service.

Called by both the Amibroker importer and the yFinance fetcher after new
price data is loaded.
"""

from __future__ import annotations

import logging
import math
from datetime import date

import numpy as np
from django.db import transaction
from django.db.models import F
from decimal import Decimal

from risk.models import MarketPriceHistory, SecurityUniverse, VolatilitySummary

logger = logging.getLogger(__name__)

LOOKBACK_WINDOWS = [10, 21, 63, 252]


def compute_volatility_summaries(
    symbol: str | None = None,
    calc_date: date | None = None,
) -> dict[str, int]:
    """
    Compute volatility summaries for one or all symbols.

    For each symbol and each lookback window:
      1. Load the last (lookback_window + 1) closing prices.
      2. Compute daily log returns.
      3. Compute std deviation of log returns over the window.
      4. Annualise: vol = std * sqrt(252).
      5. UPSERT into rm_volatility_summary.

    Returns a dict mapping symbol → number of windows computed.
    """
    if calc_date is None:
        calc_date = date.today()

    if symbol:
        symbols = [symbol]
    else:
        symbols = list(SecurityUniverse.objects.filter(is_active=True).values_list('symbol', flat=True))

    results: dict[str, int] = {}

    for sym in symbols:
        windows_computed = 0
        for window in LOOKBACK_WINDOWS:
            try:
                _compute_and_upsert(sym, window, calc_date)
                windows_computed += 1
            except Exception as exc:
                logger.warning(
                    "Failed to compute volatility for %s (window=%d): %s",
                    sym, window, exc,
                )
        results[sym] = windows_computed
        logger.info(
            "Volatility computed for %s: %d/%d windows",
            sym, windows_computed, len(LOOKBACK_WINDOWS),
        )

    return results


def _compute_and_upsert(symbol: str, lookback_days: int, calc_date: date) -> None:
    """Compute volatility for a single symbol/window and upsert into DB."""
    # Need at least (lookback_window + 2) price rows to compute log returns
    required = lookback_days + 2

    prices = (
        MarketPriceHistory.objects.filter(symbol=symbol)
        .order_by('-date')[:required]
    )

    if prices.count() < required:
        logger.warning(
            "Insufficient history for %s (window=%d): have %d, need %d",
            symbol, lookback_days, prices.count(), required,
        )
        return

    # Convert to numpy array (most recent first)
    close_values = np.array([float(p.close) for p in prices])
    close_values = close_values[::-1]  # oldest first

    # Compute daily log returns
    log_returns = np.diff(np.log(close_values))

    if len(log_returns) < lookback_days:
        logger.warning(
            "Not enough log returns for %s (window=%d): have %d",
            symbol, lookback_days, len(log_returns),
        )
        return

    # Use the last `lookback_days` log returns
    recent_returns = log_returns[-lookback_days:]

    # Annualised volatility
    std_dev = np.std(recent_returns, ddof=1)
    annualised_vol = float(std_dev * math.sqrt(252))

    # UPSERT using Django's update_or_create (works with SQLite and MSSQL)
    VolatilitySummary.objects.update_or_create(
        symbol=symbol,
        calc_date=calc_date,
        lookback_days=lookback_days,
        defaults={
            'volatility_annualised': Decimal(str(round(annualised_vol, 6))),
            'data_source': 'computed',
        },
    )
