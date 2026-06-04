"""
Gap detection service for price data.

Detects runs of missing business days in recent price history and logs
them to DataQualityLog.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from django.db.models import Max
from dateutil.relativedelta import relativedelta

from risk.models import DataQualityLog, MarketPriceHistory, SecurityUniverse

logger = logging.getLogger(__name__)

PRICE_GAP_WARNING_DAYS = 5
PRICE_GAP_CRITICAL_DAYS = 10


class GapReport:
    """Represents a detected gap in price data."""

    def __init__(
        self,
        symbol: str,
        gap_start: date,
        gap_end: date,
        gap_days: int,
        severity: str,
    ):
        self.symbol = symbol
        self.gap_start = gap_start
        self.gap_end = gap_end
        self.gap_days = gap_days
        self.severity = severity

    def __repr__(self):
        return (
            f"GapReport(symbol={self.symbol}, "
            f"gap_start={self.gap_start}, gap_end={self.gap_end}, "
            f"gap_days={self.gap_days}, severity={self.severity})"
        )


def detect_price_gaps(symbols: list[str] | None = None) -> list[GapReport]:
    """
    For each symbol, detect runs of missing business days in recent price
    history (last 252 trading days).

    Returns list of GapReport(symbol, gap_start, gap_end, gap_days, severity).
    severity: 'warning' (5 days) or 'critical' (>10 days).
    """
    reports: list[GapReport] = []

    if symbols:
        symbol_list = symbols
    else:
        symbol_list = list(
            SecurityUniverse.objects.filter(is_active=True).values_list(
                'symbol', flat=True
            )
        )

    today = date.today()
    lookback = today - timedelta(days=365)  # ~1 year to cover 252 trading days

    for symbol in symbol_list:
        symbol_reports = _detect_gaps_for_symbol(symbol, lookback, today)
        reports.extend(symbol_reports)

    return reports


def _detect_gaps_for_symbol(
    symbol: str, start_date: date, end_date: date
) -> list[GapReport]:
    """Detect gaps for a single symbol."""
    reports: list[GapReport] = []

    # Get all dates with price data for this symbol
    dates_with_data = set(
        MarketPriceHistory.objects.filter(
            symbol=symbol,
            date__gte=start_date,
            date__lte=end_date,
        ).values_list('date', flat=True)
    )

    if not dates_with_data:
        logger.warning(
            "No price data found for %s in [%s, %s]",
            symbol, start_date, end_date,
        )
        return reports

    # Get all trading days (Mon-Fri) in the range
    trading_days = _get_trading_days(start_date, end_date)

    # Find missing trading days
    missing_days = sorted(set(trading_days) - dates_with_data)

    if not missing_days:
        return reports

    # Group consecutive missing days into gaps
    gap_start = missing_days[0]
    gap_end = missing_days[0]

    for i in range(1, len(missing_days)):
        if (missing_days[i] - missing_days[i - 1]).days == 1:
            gap_end = missing_days[i]
        else:
            # End current gap — count business days in the gap
            gap_days = _count_business_days(gap_start, gap_end)
            severity = _classify_severity(gap_days)
            report = GapReport(symbol, gap_start, gap_end, gap_days, severity)
            reports.append(report)
            _log_gap_to_quality_log(report)
            # Start new gap
            gap_start = missing_days[i]
            gap_end = missing_days[i]

    # Handle last gap
    gap_days = _count_business_days(gap_start, gap_end)
    severity = _classify_severity(gap_days)
    report = GapReport(symbol, gap_start, gap_end, gap_days, severity)
    reports.append(report)
    _log_gap_to_quality_log(report)

    return reports


def _count_business_days(start: date, end: date) -> int:
    """Count the number of business days (Mon-Fri) in [start, end] inclusive."""
    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def _get_trading_days(start: date, end: date) -> list[date]:
    """Generate all Mon-Fri dates in the range [start, end]."""
    trading_days = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # Monday=0 .. Friday=4
            trading_days.append(current)
        current += timedelta(days=1)
    return trading_days


def _classify_severity(gap_days: int) -> str:
    """Classify gap severity based on number of calendar days missing."""
    if gap_days > PRICE_GAP_CRITICAL_DAYS:
        return 'critical'
    elif gap_days >= PRICE_GAP_WARNING_DAYS:
        return 'warning'
    return 'info'


def _log_gap_to_quality_log(report: GapReport) -> None:
    """Log a gap report to the DataQualityLog table."""
    DataQualityLog.objects.create(
        symbol=report.symbol,
        event_type='gap_detected',
        severity=report.severity,
        description=(
            f"Price data gap detected for {report.symbol}: "
            f"{report.gap_start} to {report.gap_end} "
            f"({report.gap_days} calendar days)"
        ),
        start_date=report.gap_start,
        end_date=report.gap_end,
        gap_days=report.gap_days,
    )

    if report.severity == 'warning':
        logger.warning(
            "Gap detected for %s: %s to %s (%d days) — severity: %s",
            report.symbol, report.gap_start, report.gap_end,
            report.gap_days, report.severity,
        )
    elif report.severity == 'critical':
        logger.critical(
            "CRITICAL gap for %s: %s to %s (%d days) — severity: %s",
            report.symbol, report.gap_start, report.gap_end,
            report.gap_days, report.severity,
        )
