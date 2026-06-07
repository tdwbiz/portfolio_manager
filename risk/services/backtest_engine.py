"""
Backtesting engine for M5-Spec-1.

Simulates portfolio performance under historical scenarios using four strategy types:
- Trailing stop-loss (ETFs only)
- Protective put (options)
- Collar (long put + short call)
- Inverse ETF (daily rebalanced)

All simulation functions are pure Python — no ORM calls inside them.
Price data comes via DataSource. Results are stored as BacktestResult ORM objects.

Dependencies: numpy, pandas, Django ORM
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd
from django.db import transaction

from risk.models import (
    BacktestResult,
    BacktestRun,
    HistoricalScenario,
    StrategyTemplate,
)
from core.models import PositionHistory
from risk.services.data_source import DataNotAvailableError, DataSource
from risk.services.option_pricing import black_scholes_put, black_scholes_call

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes for simulation outputs
# ---------------------------------------------------------------------------

@dataclass
class TrailingStopOutcome:
    """Outcome of a trailing stop simulation."""
    stop_triggered: bool
    stop_trigger_date: Optional[date]
    stop_trigger_price: float
    high_watermark_series: pd.Series
    final_price_series: pd.Series


@dataclass
class OptionOutcome:
    """Outcome of an options-based simulation (protective put or collar)."""
    put_premium_total_usd: float
    call_premium_total_usd: float
    net_premium_usd: float
    option_payoff_usd: float
    expired_worthless: bool
    daily_hedge_payoff: pd.Series


@dataclass
class InverseETFOutcome:
    """Outcome of an inverse ETF simulation."""
    daily_leverage: pd.Series
    cumulative_leverage: pd.Series
    decay_loss_usd: float


# ---------------------------------------------------------------------------
# Price series loading
# ---------------------------------------------------------------------------

def load_price_series(
    symbol: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """
    Load OHLCV price series from DataSource.

    Parameters
    ----------
    symbol : str
        Ticker symbol (e.g. 'VFV', 'QQQ', 'AAPL').
    start_date : date
        Start of backtest period (inclusive).
    end_date : date
        End of backtest period (inclusive).

    Returns
    -------
    pd.DataFrame
        OHLCV DataFrame with columns: open, high, low, close, adj_close, volume.
        Index: DatetimeIndex.

    Raises
    ------
    DataNotAvailableError
        If no price data exists for the requested range.
    """
    ds = DataSource()
    return ds.get_ohlcv(symbol, start_date, end_date)


# ---------------------------------------------------------------------------
# Position lookup
# ---------------------------------------------------------------------------

def get_position_at_date(
    symbol: str,
    account_id: str,
    as_of_date: date,
) -> Optional[dict]:
    """
    Query PositionHistory for the position as-of a given date.

    Returns the most recent position record where date_from <= as_of_date.

    Parameters
    ----------
    symbol : str
        Ticker symbol.
    account_id : str
        core_account.account_id string.
    as_of_date : date
        Date to look up position as-of.

    Returns
    -------
    dict or None
        {'quantity': Decimal, 'cost_basis_per_unit': Decimal, 'currency': str}
        or None if no position found.
    """
    record = (
        PositionHistory.objects
        .filter(symbol=symbol, account__account_id=account_id, date_from__lte=as_of_date)
        .order_by('-date_from')
        .first()
    )
    if record is None:
        return None
    return {
        'quantity': record.quantity,
        'cost_basis_per_unit': record.cost_basis_per_unit,
        'currency': record.currency,
    }


# ---------------------------------------------------------------------------
# Trailing Stop Simulation
# ---------------------------------------------------------------------------

def simulate_trailing_stop(
    prices: pd.DataFrame,
    trail_pct: float,
) -> TrailingStopOutcome:
    """
    Simulate a trailing stop-loss strategy.

    The stop price trails the highest price reached by `trail_pct`.
    Once price crosses below the stop price, the position is considered sold.
    After the stop triggers, returns are frozen at the stop price.

    Parameters
    ----------
    prices : pd.DataFrame
        OHLCV DataFrame with 'close' column.
    trail_pct : float
        Trail percentage (e.g. 0.10 = 10% trail).

    Returns
    -------
    TrailingStopOutcome
    """
    close = prices['close'].copy()
    high_watermark = close.cummax()
    stop_price = high_watermark * (1 - trail_pct)

    stop_triggered = False
    stop_trigger_date = None
    stop_trigger_price = 0.0

    for idx in close.index:
        if close.loc[idx] < stop_price.loc[idx]:
            if not stop_triggered:
                stop_triggered = True
                stop_trigger_date = idx
                stop_trigger_price = stop_price.loc[idx]
                break

    # Compute hedged price series: after stop triggers, price is frozen
    hedged_close = close.copy()
    if stop_triggered:
        mask = hedged_close.index > stop_trigger_date
        hedged_close[mask] = stop_trigger_price

    return TrailingStopOutcome(
        stop_triggered=stop_triggered,
        stop_trigger_date=stop_trigger_date,
        stop_trigger_price=stop_trigger_price,
        high_watermark_series=high_watermark,
        final_price_series=hedged_close,
    )


# ---------------------------------------------------------------------------
# Protective Put Simulation
# ---------------------------------------------------------------------------

def simulate_protective_put(
    prices: pd.DataFrame,
    quantity: float,
    strike_pct: float,
    expiry_months: int,
    vol_63d: float,
    risk_free_rate: float,
    current_price_usd: float,
) -> OptionOutcome:
    """
    Simulate a protective put strategy.

    Buys ATM put options on the entire position. The put provides downside
    protection — if price falls below the strike, the put payoff offsets losses.

    Parameters
    ----------
    prices : pd.DataFrame
        OHLCV DataFrame.
    quantity : float
        Number of shares/units.
    strike_pct : float
        Strike price as fraction of current price (e.g. 0.85 = 85% of spot).
    expiry_months : int
        Option expiry horizon in months.
    vol_63d : float
        63-day annualised volatility.
    risk_free_rate : float
        Risk-free rate (annual).
    current_price_usd : float
        Current price per unit.

    Returns
    -------
    OptionOutcome
    """
    K = current_price_usd * strike_pct
    T = expiry_months / 12.0

    # Black-Scholes put premium per share
    put_premium_per_share = black_scholes_put(current_price_usd, K, T, risk_free_rate, vol_63d)

    # Contracts = ceil(quantity / 100)
    num_contracts = max(1, int(np.ceil(quantity / 100)))
    put_premium_total = put_premium_per_share * num_contracts * 100

    # Daily hedge payoff: max(K - close, 0) per share, scaled to quantity
    daily_payoff_per_share = np.maximum(K - prices['close'].values, 0.0)
    daily_hedge_payoff = pd.Series(
        daily_payoff_per_share * quantity,
        index=prices.index,
    )

    # Total option payoff at end
    final_price = prices['close'].iloc[-1]
    option_payoff = max(K - final_price, 0.0) * quantity

    expired_worthless = final_price >= K

    return OptionOutcome(
        put_premium_total_usd=put_premium_total,
        call_premium_total_usd=0.0,
        net_premium_usd=put_premium_total,
        option_payoff_usd=option_payoff,
        expired_worthless=expired_worthless,
        daily_hedge_payoff=daily_hedge_payoff,
    )


# ---------------------------------------------------------------------------
# Collar Simulation
# ---------------------------------------------------------------------------

def simulate_collar(
    prices: pd.DataFrame,
    quantity: float,
    put_strike_pct: float,
    call_strike_pct: float,
    expiry_months: int,
    vol_63d: float,
    risk_free_rate: float,
    current_price_usd: float,
) -> OptionOutcome:
    """
    Simulate a collar strategy.

    Buys an OTM put (downside protection) and sells an OTM call (funds the put).
    Upside is capped at the call strike; downside is protected below the put strike.

    Parameters
    ----------
    prices : pd.DataFrame
        OHLCV DataFrame.
    quantity : float
        Number of shares/units.
    put_strike_pct : float
        Put strike as fraction of current price (e.g. 0.85).
    call_strike_pct : float
        Call strike as fraction of current price (e.g. 1.12).
    expiry_months : int
        Option expiry horizon in months.
    vol_63d : float
        63-day annualised volatility.
    risk_free_rate : float
        Risk-free rate (annual).
    current_price_usd : float
        Current price per unit.

    Returns
    -------
    OptionOutcome
    """
    K_put = current_price_usd * put_strike_pct
    K_call = current_price_usd * call_strike_pct
    T = expiry_months / 12.0

    put_premium = black_scholes_put(current_price_usd, K_put, T, risk_free_rate, vol_63d)
    call_premium = black_scholes_call(current_price_usd, K_call, T, risk_free_rate, vol_63d)

    num_contracts = max(1, int(np.ceil(quantity / 100)))
    put_total = put_premium * num_contracts * 100
    call_total = call_premium * num_contracts * 100
    net_premium = put_total - call_total  # net cost

    # Daily payoff: long put payoff + short call payoff (negative when call is in the money)
    put_payoff = np.maximum(K_put - prices['close'].values, 0.0)
    call_payoff = -np.maximum(prices['close'].values - K_call, 0.0)  # short call
    daily_payoff_per_share = put_payoff + call_payoff
    daily_hedge_payoff = pd.Series(
        daily_payoff_per_share * quantity,
        index=prices.index,
    )

    # Final option payoff
    final_price = prices['close'].iloc[-1]
    put_final = max(K_put - final_price, 0.0) * quantity
    call_final = -max(final_price - K_call, 0.0) * quantity
    option_payoff = put_final + call_final

    expired_worthless = final_price >= K_put  # both options worthless if price above put strike

    return OptionOutcome(
        put_premium_total_usd=put_total,
        call_premium_total_usd=call_total,
        net_premium_usd=net_premium,
        option_payoff_usd=option_payoff,
        expired_worthless=expired_worthless,
        daily_hedge_payoff=daily_hedge_payoff,
    )


# ---------------------------------------------------------------------------
# Inverse ETF Simulation
# ---------------------------------------------------------------------------

def simulate_inverse_etf(
    prices: pd.DataFrame,
    leverage: float = -1.0,
) -> InverseETFOutcome:
    """
    Simulate an inverse ETF strategy with daily rebalancing decay.

    The inverse ETF returns `leverage` times the daily return of the underlying.
    Over multiple days, compounding causes decay vs. the simple multiplied return.

    Parameters
    ----------
    prices : pd.DataFrame
        OHLCV DataFrame with 'close' column.
    leverage : float, optional
        Daily leverage factor (default -1.0 for 1x inverse).

    Returns
    -------
    InverseETFOutcome
    """
    daily_returns = prices['close'].pct_change().fillna(0.0)
    inverse_returns = daily_returns * leverage

    # Cumulative return of inverse ETF
    cumulative = (1 + inverse_returns).cumprod()

    # Simple multiplied return (no decay) for comparison
    simple_cumulative = 1 + (leverage * daily_returns.sum())

    # Decay loss = inverse cumulative - simple cumulative at end
    decay_loss = float((cumulative.iloc[-1] - simple_cumulative) * 100)

    return InverseETFOutcome(
        daily_leverage=inverse_returns,
        cumulative_leverage=cumulative,
        decay_loss_usd=decay_loss,
    )


# ---------------------------------------------------------------------------
# Stress Test Mode (Scenario-based)
# ---------------------------------------------------------------------------

def run_stress_test_mode(
    scenario: HistoricalScenario,
    quantity: float,
    start_price_usd: float,
    asset_class: str = 'equity_ca',
) -> dict:
    """
    Run backtest in stress-test mode when no price history exists.

    Applies scenario asset-class returns and benchmark returns to estimate
    portfolio performance. Used for instruments that didn't exist during
    the historical period (e.g., IBIT in 2008).

    Parameters
    ----------
    scenario : HistoricalScenario
        The HistoricalScenario ORM instance.
    quantity : float
        Number of shares/units.
    start_price_usd : float
        Starting price per unit in USD.
    asset_class : str, optional
        Asset class code to look up in scenario returns (default 'equity_ca').

    Returns
    -------
    dict
        {
            'total_return_pct': float,
            'benchmark_spy_return': float or None,
            'benchmark_qqq_return': float or None,
            'benchmark_vdy_return': float or None,
            'is_stress_test_mode': True,
            'end_value_usd': float,
        }
    """
    asset_returns = json.loads(scenario.asset_class_returns_json)
    total_return = asset_returns.get(asset_class, 0.0)

    end_value_usd = start_price_usd * quantity * (1 + total_return)

    return {
        'total_return_pct': total_return,
        'benchmark_spy_return': float(scenario.benchmark_spy_return) if scenario.benchmark_spy_return else None,
        'benchmark_qqq_return': float(scenario.benchmark_qqq_return) if scenario.benchmark_qqq_return else None,
        'benchmark_vdy_return': float(scenario.benchmark_vdy_return) if scenario.benchmark_vdy_return else None,
        'is_stress_test_mode': True,
        'end_value_usd': end_value_usd,
    }
