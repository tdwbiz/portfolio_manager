"""
Technical indicator computation functions.

Pure Python functions operating on pandas Series/DataFrames.
No Django ORM calls inside computation functions.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def sma(prices: pd.Series, window: int) -> pd.Series:
    """
    Simple moving average. prices.rolling(window).mean().
    Returns series of same length with NaN for first (window-1) values.
    """
    return prices.rolling(window=window).mean()


def sma_crossover(sma_fast: pd.Series, sma_slow: pd.Series) -> tuple[str, date | None]:
    """
    Detect most recent SMA crossover in the last 252 days.

    Returns (crossover_type, crossover_date).
    crossover_type: 'golden' | 'death' | 'none'
    crossover_date: date of the crossover event, or None.

    Algorithm:
    1. Compute sign(sma_fast - sma_slow) each day
    2. Find most recent sign change in last 252 days
    3. If sign went negative to positive: 'golden'
    4. If sign went positive to negative: 'death'
    5. If no sign change in last 252 days: 'none'
    """
    diff = sma_fast - sma_slow
    sign = np.sign(diff)

    # Get last 252 days
    recent_sign = sign.iloc[-252:] if len(sign) >= 252 else sign

    # Find sign changes
    sign_changes = np.diff(recent_sign.values)
    if len(sign_changes) == 0:
        return 'none', None

    # Find indices where sign changed
    change_indices = np.where(sign_changes != 0)[0]
    if len(change_indices) == 0:
        return 'none', None

    # Most recent change
    last_change_idx = change_indices[-1]
    crossover_type_idx = last_change_idx + 1  # Position in recent_sign

    # Determine type: if sign went from negative to positive = golden
    old_sign = recent_sign.iloc[last_change_idx]
    new_sign = recent_sign.iloc[crossover_type_idx]

    if new_sign > 0 and old_sign <= 0:
        crossover_type = 'golden'
    elif new_sign < 0 and old_sign >= 0:
        crossover_type = 'death'
    else:
        crossover_type = 'none'

    # Get the date of the crossover
    if hasattr(recent_sign.index, 'date'):
        crossover_date = recent_sign.index[crossover_type_idx].date()
    else:
        crossover_date = recent_sign.index[crossover_type_idx]

    return crossover_type, crossover_date


def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder's RSI. Uses exponential smoothing with alpha = 1/period.
    Standard formula — NOT simple moving average of gains/losses.

    Algorithm:
    1. Compute daily price changes: delta = prices.diff()
    2. Separate gains (delta > 0) and losses (abs(delta) where delta < 0)
    3. Initial avg_gain = mean(gains[:period]), initial avg_loss = mean(losses[:period])
    4. For each subsequent day:
       avg_gain = (avg_gain * (period-1) + gain) / period
       avg_loss = (avg_loss * (period-1) + loss) / period
    5. RS = avg_gain / avg_loss
    6. RSI = 100 - (100 / (1 + RS))

    Requires minimum period + 1 data points.
    Returns pd.Series. First (period) values are NaN.
    """
    delta = prices.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    result = pd.Series(np.nan, index=prices.index)

    if len(prices) < period + 1:
        return result

    # Initial average
    avg_gain = gains.iloc[:period].mean()
    avg_loss = losses.iloc[:period].mean()

    if avg_loss == 0:
        result.iloc[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result.iloc[period] = 100 - (100 / (1 + rs))

    # Smoothed averages
    for i in range(period + 1, len(prices)):
        avg_gain = (avg_gain * (period - 1) + gains.iloc[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses.iloc[i]) / period

        if avg_loss == 0:
            result.iloc[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result.iloc[i] = 100 - (100 / (1 + rs))

    return result


def macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD indicator using exponential moving averages.

    ema_fast   = prices.ewm(span=fast, adjust=False).mean()
    ema_slow   = prices.ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    macd_signal = macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - macd_signal

    Returns (macd_line, macd_signal, histogram) — all pd.Series.
    """
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    macd_signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - macd_signal_line

    return macd_line, macd_signal_line, histogram


def macd_crossover_today(histogram: pd.Series) -> str:
    """
    Detect if a MACD crossover occurred on the most recent day.

    'bullish': histogram[-2] < 0 and histogram[-1] >= 0 (crossed above zero)
    'bearish': histogram[-2] > 0 and histogram[-1] <= 0 (crossed below zero)
    'none': no crossover today
    """
    if len(histogram) < 2:
        return 'none'

    prev_val = histogram.iloc[-2]
    curr_val = histogram.iloc[-1]

    if prev_val < 0 and curr_val >= 0:
        return 'bullish'
    elif prev_val > 0 and curr_val <= 0:
        return 'bearish'
    else:
        return 'none'


def bollinger_bands(prices: pd.Series, window: int = 20, num_std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    Bollinger Bands with standard deviation bands.

    middle = prices.rolling(window).mean()  (20-day SMA)
    std    = prices.rolling(window).std()
    upper  = middle + num_std * std
    lower  = middle - num_std * std
    bandwidth = (upper - lower) / middle
    pct_b  = (prices - lower) / (upper - lower)

    Returns (upper, middle, lower, bandwidth, pct_b) — all pd.Series.
    """
    middle = prices.rolling(window=window).mean()
    std = prices.rolling(window=window).std()
    upper = middle + num_std * std
    lower = middle - num_std * std

    # Handle division by zero for bandwidth
    bandwidth = pd.Series(np.nan, index=prices.index)
    valid_middle = middle != 0
    bandwidth[valid_middle] = (upper[valid_middle] - lower[valid_middle]) / middle[valid_middle]

    # Handle division by zero for pct_b
    pct_b = pd.Series(np.nan, index=prices.index)
    valid_range = (upper - lower) != 0
    pct_b[valid_range] = (prices[valid_range] - lower[valid_range]) / (upper[valid_range] - lower[valid_range])

    # Degenerate case: std=0 (all prices same), pct_b = 0.5
    zero_std = (upper == lower) & (upper != 0)
    pct_b[zero_std] = 0.5

    return upper, middle, lower, bandwidth, pct_b


def on_balance_volume(closes: pd.Series, volumes: pd.Series) -> pd.Series:
    """
    OBV = cumulative sum of signed volume.

    If close[t] > close[t-1]: OBV[t] = OBV[t-1] + volume[t]
    If close[t] < close[t-1]: OBV[t] = OBV[t-1] - volume[t]
    If close[t] == close[t-1]: OBV[t] = OBV[t-1]

    Returns pd.Series. Starts at volume[0] on day 0.
    """
    obv = pd.Series(0, index=closes.index, dtype=float)
    obv.iloc[0] = float(volumes.iloc[0])

    for i in range(1, len(closes)):
        if closes.iloc[i] > closes.iloc[i - 1]:
            obv.iloc[i] = obv.iloc[i - 1] + float(volumes.iloc[i])
        elif closes.iloc[i] < closes.iloc[i - 1]:
            obv.iloc[i] = obv.iloc[i - 1] - float(volumes.iloc[i])
        else:
            obv.iloc[i] = obv.iloc[i - 1]

    return obv.astype('Int64')


def obv_trend(obv: pd.Series, fast_window: int = 20, slow_window: int = 50) -> str:
    """
    Classify OBV trend using two SMA crossover.

    obv_sma_fast = obv.rolling(fast_window).mean()
    obv_sma_slow = obv.rolling(slow_window).mean()

    'rising':  obv_sma_fast[-1] > obv_sma_slow[-1]
    'falling': obv_sma_fast[-1] < obv_sma_slow[-1]
    'neutral': abs difference < 0.5% of abs(obv_sma_slow[-1])
    """
    if len(obv) < slow_window:
        return 'neutral'

    obv_sma_fast = obv.rolling(window=fast_window).mean()
    obv_sma_slow = obv.rolling(window=slow_window).mean()

    fast_val = obv_sma_fast.iloc[-1]
    slow_val = obv_sma_slow.iloc[-1]

    if pd.isna(fast_val) or pd.isna(slow_val):
        return 'neutral'

    if slow_val == 0:
        if fast_val > slow_val:
            return 'rising'
        elif fast_val < slow_val:
            return 'falling'
        else:
            return 'neutral'

    diff_pct = abs(fast_val - slow_val) / abs(slow_val)

    if diff_pct < 0.005:
        return 'neutral'
    elif fast_val > slow_val:
        return 'rising'
    else:
        return 'falling'


def fifty_two_week_range(prices: pd.Series) -> tuple[float, float, float]:
    """
    Compute 52-week high, low, and current distance from high.

    window = last 252 prices
    high_52w = max(window)
    low_52w  = min(window)
    pct_from_high = (prices[-1] - high_52w) / high_52w * 100  (negative if below high)

    Returns (high_52w, low_52w, pct_from_high).
    """
    recent = prices.tail(252) if len(prices) >= 252 else prices
    high_52w = float(recent.max())
    low_52w = float(recent.min())
    current = float(prices.iloc[-1])

    if high_52w == 0:
        return high_52w, low_52w, 0.0

    pct_from_high = (current - high_52w) / high_52w * 100

    return high_52w, low_52w, pct_from_high


def classify_market_phase(
    current_price: float,
    sma_200: float | None,
    pct_from_52w_high: float,
    rsi: float | None,
) -> str:
    """
    Classify current market phase. Priority order matters.

    Priority (highest to lowest):
    1. 'bear':       pct_from_52w_high < -20.0  (>20% below 52-week high)
    2. 'correction': pct_from_52w_high < -10.0  (10–20% below 52-week high)
    3. 'overbought': rsi is not None and rsi > 70
    4. 'oversold':   rsi is not None and rsi < 30
    5. 'uptrend':    sma_200 is not None and current_price > sma_200
    6. 'uptrend':    fallback if no other condition (no SMA-200 data)

    Note: overbought/oversold can coexist with uptrend — use highest priority.
    A bear market security can also be oversold — 'bear' takes priority.
    """
    if pct_from_52w_high < -20.0:
        return 'bear'
    elif pct_from_52w_high < -10.0:
        return 'correction'
    elif rsi is not None and rsi > 70:
        return 'overbought'
    elif rsi is not None and rsi < 30:
        return 'oversold'
    elif sma_200 is not None and current_price > sma_200:
        return 'uptrend'
    else:
        return 'uptrend'
