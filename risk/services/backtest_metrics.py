"""
Backtest metrics computation for M5-Spec-1.

Computes all metrics stored in BacktestResult from simulation outputs:
- Total returns (hedged vs unhedged)
- Maximum drawdown
- Hedge cost and effectiveness ratio
- Regime split analysis

All functions are pure Python — no ORM calls.

Dependencies: numpy, pandas, Django ORM (for regime data only)
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Maximum Drawdown
# ---------------------------------------------------------------------------

def compute_max_drawdown_of_series(
    values: pd.Series,
) -> float:
    """
    Compute maximum peak-to-trough drawdown of a price/value series.

    Parameters
    ----------
    values : pd.Series
        Time series of values (e.g., daily portfolio NAV).

    Returns
    -------
    float
        Maximum drawdown as a negative fraction (e.g. -0.20 = 20% drawdown).
        Returns 0.0 if no drawdown occurs.
    """
    if values.empty:
        return 0.0

    rolling_max = values.cummax()
    drawdowns = (values - rolling_max) / rolling_max
    return float(drawdowns.min())


# ---------------------------------------------------------------------------
# Main Metrics Computation
# ---------------------------------------------------------------------------

def compute_backtest_metrics(
    prices: pd.DataFrame,
    hedge_payoff_daily: pd.Series,
    hedge_cost_total_usd: float,
    quantity: float,
    start_price_usd: float,
    strategy_specific_outcomes: dict,
) -> dict:
    """
    Compute all backtest metrics from simulation outputs.

    Parameters
    ----------
    prices : pd.DataFrame
        OHLCV DataFrame with 'close' column (unhedged prices).
    hedge_payoff_daily : pd.Series
        Daily P&L from the hedge (options payoff, stop-loss freeze, etc.).
    hedge_cost_total_usd : float
        Total cost of the hedge (premium + commission + bid-ask).
    quantity : float
        Number of shares/units in the position.
    start_price_usd : float
        Starting price per unit in USD.
    strategy_specific_outcomes : dict
        Strategy-specific result flags, e.g.:
        {'stop_triggered': bool, 'option_expired_worthless': bool}

    Returns
    -------
    dict
        All metrics matching BacktestResult fields:
        {
            'total_return_unhedged_pct': float,
            'total_return_hedged_pct': float,
            'return_improvement_pct': float,
            'max_drawdown_unhedged': float,
            'max_drawdown_hedged': float,
            'drawdown_reduction_pct': float,
            'hedge_cost_total_usd': float,
            'hedge_cost_pct_of_position': float,
            'hedge_effectiveness_ratio': float or None,
            'breakeven_annual_cost_pct': float or None,
            'stop_triggered': bool or None,
            'stop_trigger_date': date or None,
            'stop_trigger_price_usd': float or None,
            'option_expired_worthless': bool or None,
            'option_payoff_usd': float,
            'position_value_start_usd': float,
            'position_value_end_unhedged_usd': float,
            'position_value_end_hedged_usd': float,
        }
    """
    end_price = prices['close'].iloc[-1]
    start_value = start_price_usd * quantity
    end_value_unhedged = end_price * quantity

    # Total return unhedged
    total_return_unhedged = (end_price - start_price_usd) / start_price_usd

    # Hedged return: unhedged P&L + cumulative hedge payoff - hedge cost
    cumulative_hedge_payoff = float(hedge_payoff_daily.sum())
    net_hedge_pnl = cumulative_hedge_payoff - hedge_cost_total_usd
    end_value_hedged = end_value_unhedged + net_hedge_pnl
    total_return_hedged = (end_value_hedged - start_value) / start_value
    return_improvement = total_return_hedged - total_return_unhedged

    # Drawdowns
    max_dd_unhedged = compute_max_drawdown_of_series(prices['close'])
    hedged_values = prices['close'] + (hedge_payoff_daily / quantity)
    max_dd_hedged = compute_max_drawdown_of_series(hedged_values)
    # max_dd values are negative (e.g. -0.20 = 20% drawdown).
    # Positive drawdown_reduction means the hedge reduced the drawdown magnitude.
    drawdown_reduction = max_dd_hedged - max_dd_unhedged

    # Hedge cost as % of position
    hedge_cost_pct = hedge_cost_total_usd / start_value if start_value > 0 else 0.0

    # Hedge effectiveness ratio
    hedge_effectiveness = None
    if hedge_cost_pct > 0:
        # Effectiveness = drawdown reduction / hedge cost %
        # Positive means the hedge reduced drawdown more than its cost
        hedge_effectiveness = drawdown_reduction / hedge_cost_pct if hedge_cost_pct != 0 else None

    # Breakeven annual cost
    # How much annual hedge cost would make the strategy break even?
    # breakeven = drawdown_reduction / days * 365
    breakeven_annual = None
    days = len(prices)
    if days > 0:
        annualized_reduction = drawdown_reduction / days * 365
        breakeven_annual = annualized_reduction

    # Strategy-specific outcomes
    stop_triggered = strategy_specific_outcomes.get('stop_triggered', None)
    stop_trigger_date = strategy_specific_outcomes.get('stop_trigger_date', None)
    stop_trigger_price = strategy_specific_outcomes.get('stop_trigger_price', None)
    option_expired_worthless = strategy_specific_outcomes.get('option_expired_worthless', None)

    # Option payoff: cumulative hedge payoff at end (for options)
    option_payoff = cumulative_hedge_payoff

    return {
        'total_return_unhedged_pct': total_return_unhedged,
        'total_return_hedged_pct': total_return_hedged,
        'return_improvement_pct': return_improvement,
        'max_drawdown_unhedged': max_dd_unhedged,
        'max_drawdown_hedged': max_dd_hedged,
        'drawdown_reduction_pct': drawdown_reduction,
        'hedge_cost_total_usd': hedge_cost_total_usd,
        'hedge_cost_pct_of_position': hedge_cost_pct,
        'hedge_effectiveness_ratio': hedge_effectiveness,
        'breakeven_annual_cost_pct': breakeven_annual,
        'stop_triggered': stop_triggered,
        'stop_trigger_date': stop_trigger_date,
        'stop_trigger_price_usd': stop_trigger_price,
        'option_expired_worthless': option_expired_worthless,
        'option_payoff_usd': option_payoff,
        'position_value_start_usd': start_value,
        'position_value_end_unhedged_usd': end_value_unhedged,
        'position_value_end_hedged_usd': end_value_hedged,
    }


# ---------------------------------------------------------------------------
# Regime Split
# ---------------------------------------------------------------------------

def build_regime_split(
    prices: pd.DataFrame,
    regime_data: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Split backtest results by risk regime periods.

    Parameters
    ----------
    prices : pd.DataFrame
        OHLCV DataFrame with 'close' column and DatetimeIndex.
    regime_data : pd.DataFrame or None
        DataFrame with 'rm_regime_state' column indexed by date.
        If None, returns {'unhedged': {'days': len(prices)}}.

    Returns
    -------
    dict
        JSON-serializable regime split:
        {
            'regime_name': {
                'unhedged_return': float,
                'hedged_return': float,
                'days': int,
            },
            ...
        }
    """
    if regime_data is None or regime_data.empty:
        return {
            'unhedged': {
                'unhedged_return': float((prices['close'].iloc[-1] / prices['close'].iloc[0]) - 1),
                'hedged_return': float((prices['close'].iloc[-1] / prices['close'].iloc[0]) - 1),
                'days': len(prices),
            }
        }

    # Ensure regime_data index is date-compatible
    regime_dates = regime_data.index
    if hasattr(regime_dates, 'date'):
        regime_dates = regime_dates.date

    result = {}
    for regime in regime_data['rm_regime_state'].unique():
        mask = regime_data['rm_regime_state'] == regime
        regime_prices = prices.loc[mask]
        if regime_prices.empty:
            continue

        unhedged_ret = float(
            (regime_prices['close'].iloc[-1] / regime_prices['close'].iloc[0]) - 1
        )
        result[str(regime)] = {
            'unhedged_return': unhedged_ret,
            'hedged_return': unhedged_ret,  # hedged return filled in by caller
            'days': len(regime_prices),
        }

    return result
