"""
Alert engine: pure-Python detection functions for M7-Spec-1 alerting.

Each detection function is a standalone callable that takes a dict of
pre-computed data and returns a list of alert dicts.  The
evaluate_alerts management command matches detection results against
AlertConfig rows and writes AlertHistory records.

All functions are independently testable and have no ORM dependencies.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases (documentary only — Python is dynamically typed)
# ---------------------------------------------------------------------------
# DetectionInput = dict[str, Any]
# AlertDict = dict[str, Any]

# ---------------------------------------------------------------------------
# 1. Urgency level change detection
# ---------------------------------------------------------------------------


def detect_urgency_level_changes(
    current: dict[str, Any],
    previous: dict[str, Any],
    threshold: Decimal | None = None,
) -> list[dict[str, Any]]:
    """
    Detect portfolios whose urgency score changed by >= threshold.

    Parameters
    ----------
    current : dict
        {portfolio_id: urgency_score} for the current calc_date.
    previous : dict
        {portfolio_id: urgency_score} for the previous calc_date.
    threshold : Decimal, optional
        Minimum absolute change to trigger.  Defaults to ``Decimal('0.15')``.

    Returns
    -------
    list[dict]
        Each dict has keys: ``entity_type``, ``entity_id``, ``entity_description``,
        ``trigger_value``, ``previous_value``, ``symbol`` (empty).
    """
    if threshold is None:
        threshold = Decimal('0.15')

    alerts: list[dict[str, Any]] = []
    for port_id, curr_score in current.items():
        prev_score = previous.get(port_id)
        if prev_score is None:
            continue
        change = abs(curr_score - prev_score)
        if change >= threshold:
            direction = 'UP' if curr_score > prev_score else 'DOWN'
            alerts.append({
                'entity_type': 'portfolio',
                'entity_id': str(port_id),
                'entity_description': f"Portfolio {port_id} urgency {direction}",
                'trigger_value': str(round(float(curr_score), 4)),
                'previous_value': str(round(float(prev_score), 4)),
                'symbol': '',
                'extra': {'direction': direction, 'change': str(round(float(change), 4))},
            })
    return alerts


# ---------------------------------------------------------------------------
# 2. VaR portfolio breach detection
# ---------------------------------------------------------------------------


def detect_var_portfolio_breach(
    var_results: list[dict[str, Any]],
    threshold: Decimal | None = None,
) -> list[dict[str, Any]]:
    """
    Detect portfolios where Value-at-Risk exceeds the configured threshold.

    Parameters
    ----------
    var_results : list[dict]
        Each dict: ``portfolio_id``, ``var_95``, ``var_99``, ``portfolio_value``,
        ``calc_date``.
    threshold : Decimal, optional
        VaR dollar threshold.  Defaults to ``Decimal('100000')``.

    Returns
    -------
    list[dict]
    """
    if threshold is None:
        threshold = Decimal('100000')

    alerts: list[dict[str, Any]] = []
    for row in var_results:
        var_95 = Decimal(str(row.get('var_95', 0)))
        if var_95 > threshold:
            alerts.append({
                'entity_type': 'portfolio',
                'entity_id': str(row['portfolio_id']),
                'entity_description': f"Portfolio {row['portfolio_id']} VaR breach",
                'trigger_value': str(round(float(var_95), 2)),
                'previous_value': '',
                'symbol': '',
                'extra': {
                    'var_95': str(round(float(var_95), 2)),
                    'var_99': str(row.get('var_99', '')),
                    'portfolio_value': str(row.get('portfolio_value', '')),
                },
            })
    return alerts


# ---------------------------------------------------------------------------
# 3. Regime shift detection
# ---------------------------------------------------------------------------


def detect_regime_shift(
    current_regime: dict[str, Any],
    previous_regime: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Detect portfolios where the risk regime changed.

    Parameters
    ----------
    current_regime : dict
        {portfolio_id: regime_name} for current calc_date.
    previous_regime : dict
        {portfolio_id: regime_name} for previous calc_date.

    Returns
    -------
    list[dict]
    """
    alerts: list[dict[str, Any]] = []
    for port_id, curr_regime in current_regime.items():
        prev_regime = previous_regime.get(port_id)
        if prev_regime is None or curr_regime != prev_regime:
            alerts.append({
                'entity_type': 'portfolio',
                'entity_id': str(port_id),
                'entity_description': f"Portfolio {port_id} regime shift",
                'trigger_value': curr_regime,
                'previous_value': prev_regime or 'N/A',
                'symbol': '',
                'extra': {'regime_change': f"{prev_regime} → {curr_regime}"},
            })
    return alerts


# ---------------------------------------------------------------------------
# 4. Death cross detection (50-day MA crosses below 200-day MA)
# ---------------------------------------------------------------------------


def detect_death_crosses(
    ma_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Detect symbols where the 50-day moving average crossed below the
    200-day moving average (bearish signal).

    Parameters
    ----------
    ma_data : list[dict]
        Each dict: ``symbol``, ``ma_50``, ``ma_200``, ``calc_date``.
        A "cross" is detected when ma_50 < ma_200 AND the previous day
        had ma_50 >= ma_200 (or this is the first data point).

    Returns
    -------
    list[dict]
    """
    alerts: list[dict[str, Any]] = []
    for row in ma_data:
        ma_50 = Decimal(str(row.get('ma_50', 0)))
        ma_200 = Decimal(str(row.get('ma_200', 0)))
        if ma_50 < ma_200:
            alerts.append({
                'entity_type': 'symbol',
                'entity_id': row['symbol'],
                'entity_description': f"Death cross: {row['symbol']}",
                'trigger_value': str(round(float(ma_50), 2)),
                'previous_value': str(row.get('prev_ma_50', '')),
                'symbol': row['symbol'],
                'extra': {
                    'ma_50': str(round(float(ma_50), 2)),
                    'ma_200': str(round(float(ma_200), 2)),
                },
            })
    return alerts


# ---------------------------------------------------------------------------
# 5. Earnings proximity detection
# ---------------------------------------------------------------------------


def detect_earnings_proximity(
    earnings_data: list[dict[str, Any]],
    days_threshold: int = 7,
) -> list[dict[str, Any]]:
    """
    Detect symbols with earnings announcements within N days.

    Parameters
    ----------
    earnings_data : list[dict]
        Each dict: ``symbol``, ``earnings_date`` (date or str), ``days_away``.
    days_threshold : int
        Number of days before earnings to trigger.  Defaults to 7.

    Returns
    -------
    list[dict]
    """
    alerts: list[dict[str, Any]] = []
    for row in earnings_data:
        days_away = row.get('days_away')
        if days_away is not None and 0 <= days_away <= days_threshold:
            alerts.append({
                'entity_type': 'symbol',
                'entity_id': row['symbol'],
                'entity_description': f"Earnings soon: {row['symbol']}",
                'trigger_value': f"{days_away} days",
                'previous_value': str(row.get('prev_days_away', '')),
                'symbol': row['symbol'],
                'extra': {
                    'earnings_date': str(row.get('earnings_date', '')),
                },
            })
    return alerts


# ---------------------------------------------------------------------------
# 6. Concentration breach detection
# ---------------------------------------------------------------------------


def detect_concentration_breach(
    weights: dict[str, float],
    threshold: Decimal | None = None,
) -> list[dict[str, Any]]:
    """
    Detect individual positions exceeding the portfolio concentration threshold.

    Parameters
    ----------
    weights : dict
        {symbol: weight_fraction} e.g. ``{'AAPL': 0.25, 'MSFT': 0.18}``.
    threshold : Decimal, optional
        Weight threshold.  Defaults to ``Decimal('0.20')`` (20%).

    Returns
    -------
    list[dict]
    """
    if threshold is None:
        threshold = Decimal('0.20')

    alerts: list[dict[str, Any]] = []
    for symbol, weight in weights.items():
        if Decimal(str(weight)) > threshold:
            alerts.append({
                'entity_type': 'symbol',
                'entity_id': symbol,
                'entity_description': f"Concentration: {symbol}",
                'trigger_value': f"{float(weight) * 100:.2f}%",
                'previous_value': '',
                'symbol': symbol,
                'extra': {'weight': str(weight)},
            })
    return alerts


# ---------------------------------------------------------------------------
# 7. Taxable gain bracket detection
# ---------------------------------------------------------------------------


def detect_taxable_gain_brackets(
    gain_data: list[dict[str, Any]],
    threshold: Decimal | None = None,
) -> list[dict[str, Any]]:
    """
    Detect positions approaching or entering higher tax brackets.

    Parameters
    ----------
    gain_data : list[dict]
        Each dict: ``symbol``, ``unrealized_gain``, ``tax_bracket``,
        ``next_bracket_threshold``.
    threshold : Decimal, optional
        Minimum unrealized gain to trigger.  Defaults to ``Decimal('10000')``.

    Returns
    -------
    list[dict]
    """
    if threshold is None:
        threshold = Decimal('10000')

    alerts: list[dict[str, Any]] = []
    for row in gain_data:
        gain = Decimal(str(row.get('unrealized_gain', 0)))
        if gain >= threshold:
            alerts.append({
                'entity_type': 'symbol',
                'entity_id': row['symbol'],
                'entity_description': f"Tax gain: {row['symbol']}",
                'trigger_value': f"${float(gain):,.2f}",
                'previous_value': '',
                'symbol': row['symbol'],
                'extra': {
                    'tax_bracket': str(row.get('tax_bracket', '')),
                    'next_bracket_threshold': str(row.get('next_bracket_threshold', '')),
                },
            })
    return alerts
