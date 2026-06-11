"""
Security-level risk metric computation functions.

All functions are pure Python (no Django ORM calls) and take plain
DataFrames/arrays as input. This makes them independently testable.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


def prepare_return_series(prices: pd.Series) -> pd.Series:
    """
    Input: daily closing price series (DatetimeIndex, float values).
    Output: daily log return series. ln(P_t / P_{t-1}).
    First element is NaN (dropped by callers).
    """
    return np.log(prices / prices.shift(1))


def align_return_series(series_a: pd.Series, series_b: pd.Series) -> tuple[pd.Series, pd.Series]:
    """
    Inner join on DatetimeIndex. Returns two series with identical index.
    Used for beta and correlation calculations.
    """
    common_index = series_a.index.intersection(series_b.index)
    return series_a.loc[common_index], series_b.loc[common_index]


def load_volatility(
    symbol: str,
    calc_date: date,
    windows: list[int],
) -> dict[int, Optional[float]]:
    """
    Load volatility from rm_volatility_summary for given symbol, date, and windows.
    Returns {10: 0.0312, 21: 0.0298, 63: 0.0276, 252: 0.0251}.
    Returns None for windows where data is unavailable.
    """
    from risk.models import VolatilitySummary

    result = {}
    for window in windows:
        entry = VolatilitySummary.objects.filter(
            symbol=symbol,
            calc_date=calc_date,
            lookback_days=window,
        ).first()
        if entry:
            result[window] = float(entry.volatility_annualised)
        else:
            result[window] = None
    return result


def var_parametric(
    vol_annualised: float,
    horizon_days: int = 1,
    confidence: float = 0.95,
) -> float:
    """
    Parametric (normal) VaR.
    Formula: z_score * vol_daily * sqrt(horizon_days)
    where vol_daily = vol_annualised / sqrt(252)
    and z_score = scipy.stats.norm.ppf(1 - confidence)
    Returns positive fraction representing expected loss.
    Example: vol_annualised=0.30, horizon=1, confidence=0.95 → ~0.0311 (3.11% daily loss)
    Uses vol_21d as input (21-day volatility is standard for 1-day VaR).
    """
    vol_daily = vol_annualised / np.sqrt(252)
    z_score = stats.norm.ppf(1 - confidence)
    return abs(z_score * vol_daily * np.sqrt(horizon_days))


def var_historical(returns: pd.Series, confidence: float = 0.95) -> float:
    """
    Historical simulation VaR.
    Formula: abs(np.percentile(returns, (1 - confidence) * 100))
    Input: daily log return series, minimum 252 values.
    Returns positive fraction.
    """
    return abs(np.percentile(returns, (1 - confidence) * 100))


def var_cornish_fisher(returns: pd.Series, confidence: float = 0.95) -> Optional[float]:
    """
    Cornish-Fisher expansion VaR. Adjusts for skewness and excess kurtosis.
    
    Algorithm:
    1. Compute: mu = returns.mean(), sigma = returns.std(), 
                S = skewness(returns), K = excess_kurtosis(returns)
    2. z = scipy.stats.norm.ppf(1 - confidence)
    3. z_cf = z + (z**2 - 1)*S/6 + (z**3 - 3*z)*K/24 - (2*z**3 - 5*z)*S**2/36
    4. VaR_cf = abs(mu + z_cf * sigma)
    
    This is the preferred VaR for IBIT, ETHA, NVDA where fat tails matter.
    For normal-ish securities: Cornish-Fisher ≈ Parametric.
    Returns positive fraction.
    Requires minimum 63 return observations; return None if fewer.
    """
    if len(returns) < 63:
        return None
    
    mu = returns.mean()
    sigma = returns.std()
    if sigma == 0:
        return 0.0
    
    S = stats.skew(returns)
    K = stats.kurtosis(returns)  # excess kurtosis
    
    z = stats.norm.ppf(1 - confidence)
    
    z_cf = (
        z
        + (z**2 - 1) * S / 6
        + (z**3 - 3 * z) * K / 24
        - (2 * z**3 - 5 * z) * S**2 / 36
    )
    
    return abs(mu + z_cf * sigma)


def cvar(returns: pd.Series, confidence: float = 0.95) -> float:
    """
    Conditional VaR (Expected Shortfall).
    Formula: mean of returns below the (1-confidence) percentile.
    Example: for 95% CVaR, take the mean of the worst 5% of daily returns.
    Returns positive fraction (expected loss in tail).
    """
    threshold = np.percentile(returns, (1 - confidence) * 100)
    tail_returns = returns[returns <= threshold]
    if len(tail_returns) == 0:
        return 0.0
    return abs(tail_returns.mean())


def max_drawdown(prices: pd.Series) -> float:
    """
    Maximum peak-to-trough drawdown over the full price series.
    Formula: min((prices - prices.cummax()) / prices.cummax())
    Returns negative fraction. E.g. -0.34 = 34% drawdown.
    """
    cummax = prices.cummax()
    drawdowns = (prices - cummax) / cummax
    return drawdowns.min()


def current_drawdown_from_peak(prices: pd.Series, window: int = 252) -> float:
    """
    Current price relative to the maximum price in the last `window` trading days.
    Formula: (current_price - max(prices[-window:])) / max(prices[-window:])
    Returns negative fraction (or 0.0 if at a new high).
    """
    if len(prices) == 0:
        return 0.0
    recent_prices = prices.tail(window)
    peak = recent_prices.max()
    current = prices.iloc[-1]
    if peak == 0:
        return 0.0
    return (current - peak) / peak


def beta(
    symbol_returns: pd.Series,
    benchmark_returns: pd.Series,
) -> Optional[float]:
    """
    OLS regression beta of symbol returns vs benchmark returns.
    Formula: cov(symbol, benchmark) / var(benchmark)
    Uses aligned 252-day return series.
    Returns float. Typical range -2.0 to 3.0 for this portfolio.
    Returns None if fewer than 126 overlapping observations.
    """
    symbol_returns, benchmark_returns = align_return_series(symbol_returns, benchmark_returns)
    
    if len(symbol_returns) < 126:
        return None
    
    cov_matrix = np.cov(symbol_returns, benchmark_returns)
    return cov_matrix[0, 1] / cov_matrix[1, 1]


def fx_adjusted_returns(usd_returns: pd.Series, fx_returns: pd.Series) -> pd.Series:
    """
    Approximate CAD return = USD return + FX return (ln(FX_t / FX_{t-1})).
    Where FX is USD/CAD rate (number of CAD per USD).
    A CAD investor holding SPY earns SPY's USD return PLUS any USD/CAD appreciation.
    """
    usd_returns, fx_returns = align_return_series(usd_returns, fx_returns)
    return usd_returns + fx_returns


def sharpe_ratio(
    symbol_returns: pd.Series,
    risk_free_annual: float,
    window: int = 252,
) -> float:
    """
    Sharpe ratio using last `window` daily returns.
    daily_rf = risk_free_annual / 252
    excess_returns = symbol_returns - daily_rf
    Sharpe = (excess_returns.mean() / excess_returns.std()) * sqrt(252)
    
    Risk-free rate source: SGOV current yield approximated as 
    (last SGOV close - SGOV price 252 days ago) / SGOV price 252 days ago.
    If SGOV data unavailable: use 4.5% annualised as fallback (document this).
    """
    daily_rf = risk_free_annual / 252
    excess_returns = symbol_returns.tail(window) - daily_rf
    if excess_returns.std() == 0:
        return 0.0
    return (excess_returns.mean() / excess_returns.std()) * np.sqrt(252)


def sortino_ratio(
    symbol_returns: pd.Series,
    risk_free_annual: float,
    window: int = 252,
) -> Optional[float]:
    """
    Sortino ratio — same as Sharpe but uses downside deviation as denominator.
    downside_dev = std(min(excess_returns, 0)) * sqrt(252)
    Sortino = (mean(excess_returns) * 252) / downside_dev
    Returns None if all excess returns are positive (no downside).
    """
    daily_rf = risk_free_annual / 252
    excess_returns = symbol_returns.tail(window) - daily_rf
    
    downside_returns = excess_returns[excess_returns < 0]
    if len(downside_returns) == 0:
        return None
    
    downside_dev = downside_returns.std() * np.sqrt(252)
    if downside_dev == 0:
        return None
    
    return (excess_returns.mean() * 252) / downside_dev


def liquidity_score(
    avg_daily_dollar_volume_usd: float,
    position_market_value_usd: float,
    is_cef: bool = False,
    is_mutual_fund: bool = False,
    is_cash_equivalent: bool = False,
) -> float:
    """
    Liquidity score 0–100.
    
    Mutual funds (RBF2010, RBF2014): return 95.0 (high liquidity, daily NAV redemption)
    Cash equivalents (SGOV, ICSH): return 98.0
    
    For all others:
    days_to_exit = position_market_value_usd / avg_daily_dollar_volume_usd
    
    Score:
    - days_to_exit < 0.1:  100.0  (large, liquid ETF)
    - days_to_exit < 1.0:   90.0
    - days_to_exit < 5.0:   70.0
    - days_to_exit < 10.0:  50.0
    - days_to_exit < 30.0:  30.0
    - days_to_exit >= 30.0: 10.0
    
    CEF adjustment (ADX): multiply final score by 0.7 (CEFs trade at discount/premium,
    actual liquidity worse than volume implies).
    
    If avg_daily_dollar_volume_usd is 0 or NULL: return 5.0
    """
    if is_mutual_fund:
        return 95.0
    if is_cash_equivalent:
        return 98.0
    
    if avg_daily_dollar_volume_usd is None or avg_daily_dollar_volume_usd == 0:
        return 5.0
    
    days_to_exit = position_market_value_usd / avg_daily_dollar_volume_usd
    
    if days_to_exit < 0.1:
        score = 100.0
    elif days_to_exit < 1.0:
        score = 90.0
    elif days_to_exit < 5.0:
        score = 70.0
    elif days_to_exit < 10.0:
        score = 50.0
    elif days_to_exit < 30.0:
        score = 30.0
    else:
        score = 10.0
    
    if is_cef:
        score *= 0.7
    
    return score


def avg_daily_dollar_volume(
    symbol: str,
    window_days: int = 30,
    fx_rate: Optional[float] = None,
) -> float:
    """
    From rm_market_price_history: mean of (volume * close) over last window_days rows.
    For USD symbols: result is already USD.
    For CAD symbols: multiply by current FX rate (usd_cad_rate).
    Returns 0.0 if no volume data available (mutual funds, some ETFs).
    """
    from risk.models import MarketPriceHistory
    
    rows = MarketPriceHistory.objects.filter(symbol=symbol).order_by('-date')[:window_days]
    if not rows.exists():
        return 0.0
    
    total_volume_usd = 0.0
    count = 0
    for row in rows:
        if row.volume > 0:
            daily_volume_usd = float(row.volume) * float(row.close)
            if row.currency == 'CAD' and fx_rate:
                daily_volume_usd /= fx_rate
            total_volume_usd += daily_volume_usd
            count += 1
    
    if count == 0:
        return 0.0
    
    return total_volume_usd / count


def compute_correlation_matrix(
    symbols: list[str],
    calc_date: date,
    window: int = 252,
) -> pd.DataFrame:
    """
    Pearson correlation matrix for all symbol pairs.
    
    1. For each symbol: load last 252 daily log returns from DataSource
    2. Inner-join all return series on date (some symbols may have fewer days)
    3. Compute pandas correlation matrix: df.corr()
    4. For each (symbol_a, symbol_b) pair:
       - Only store if overlap >= 126 days
       - Store both (A, B) and (B, A) directions
       - Store diagonal (A, A) as 1.0
    5. UPSERT into rm_correlation_matrix
    
    Minimum 126 days chosen: half of 252 ensures the correlation is based on 
    at least 6 months of co-movement data.
    """
    from risk.services.data_source import DataSource, DataNotAvailableError
    
    ds = DataSource()
    return_frames = {}
    
    for symbol in symbols:
        try:
            prices = ds.get_price_series(symbol, calc_date - timedelta(days=365), calc_date)
            returns = prepare_return_series(prices)
            return_frames[symbol] = returns.tail(window)
        except DataNotAvailableError:
            logger.warning("No price data for %s, skipping correlation", symbol)
    
    if not return_frames:
        return pd.DataFrame()
    
    # Align all return series
    aligned_returns = pd.DataFrame(return_frames)
    aligned_returns = aligned_returns.dropna(how='all')
    
    if aligned_returns.shape[0] < 126:
        logger.warning("Insufficient overlapping data for correlation matrix")
        return pd.DataFrame()
    
    corr_matrix = aligned_returns.corr()
    
    # Store results
    from risk.models import CorrelationMatrix
    
    for symbol_a in symbols:
        for symbol_b in symbols:
            if symbol_a not in corr_matrix.columns or symbol_b not in corr_matrix.columns:
                continue
            
            pearson = corr_matrix.loc[symbol_a, symbol_b]
            overlap = aligned_returns[[symbol_a, symbol_b]].dropna().shape[0]
            
            if overlap >= 126:
                CorrelationMatrix.objects.update_or_create(
                    symbol_a=symbol_a,
                    symbol_b=symbol_b,
                    calc_date=calc_date,
                    defaults={
                        'pearson_252d': pearson,
                        'price_rows_overlap': overlap,
                    },
                )
    
    return corr_matrix


def get_position_data(symbol: str, calc_date: date) -> list:
    """
    Load all open positions for symbol as of calc_date.
    Query: WHERE symbol = :symbol AND date_from <= :calc_date AND (date_to IS NULL OR date_to > :calc_date)
    
    Returns list of PositionRecord(account_id, owner, symbol, quantity, cost_basis_per_unit, 
                                    total_cost_basis, currency)
    """
    from core.models import PositionHistory
    
    positions = PositionHistory.objects.filter(
        models.Q(date_to__gt=calc_date) | models.Q(date_to__isnull=True),
        symbol=symbol,
        date_from__lte=calc_date,
    )
    
    PositionRecord = type('PositionRecord', (), {})
    
    records = []
    for pos in positions:
        record = PositionRecord()
        record.account_id = pos.account.account_id
        record.owner = pos.owner
        record.symbol = pos.symbol
        record.quantity = float(pos.quantity)
        record.cost_basis_per_unit = float(pos.cost_basis_per_unit)
        record.total_cost_basis = float(pos.total_cost_basis)
        record.currency = pos.currency
        records.append(record)
    
    return records


def ugl_at_risk(
    symbol: str,
    calc_date: date,
    var_parametric_95: float,
) -> tuple[Optional[float], Optional[float]]:
    """
    Returns (ugl_at_risk_usd, ugl_at_risk_pct_of_ugl).
    
    1. Query core_position_history for all open positions (date_to IS NULL) for this symbol
    2. For each position: get quantity and cost_basis_per_unit
    3. Get latest price from rm_market_price_history
    4. Convert to USD if CAD (use rm_fx_rates for calc_date)
    5. Total market value USD = sum(quantity * price_usd) across all accounts
    6. Total UGL USD = sum(quantity * (price_usd - cost_basis_per_unit_usd))
    7. ugl_at_risk_usd = total_market_value_usd * var_parametric_95
    8. ugl_at_risk_pct = ugl_at_risk_usd / total_ugl_usd (None if total_ugl <= 0)
    
    Note: uses ALL accounts for this symbol (including excluded ones) for accurate UGL.
    The exclude_from_risk flag is for dashboard display only, not metric calculation.
    """
    from risk.models import MarketPriceHistory, FxRate
    from risk.services.data_source import DataNotAvailableError
    
    positions = get_position_data(symbol, calc_date)
    if not positions:
        return None, None
    
    # Get FX rate
    try:
        fx_rate = float(FxRate.objects.get(date=calc_date).usd_cad_rate)
    except FxRate.DoesNotExist:
        try:
            prior = FxRate.objects.filter(date__lt=calc_date).order_by('-date').first()
            fx_rate = float(prior.usd_cad_rate)
        except FxRate.DoesNotExist:
            fx_rate = 1.0  # Assume USD if no FX rate
    
    # Get latest price
    try:
        latest_price = MarketPriceHistory.objects.filter(symbol=symbol).order_by('-date').first()
        if latest_price is None:
            return None, None
        current_price = float(latest_price.close)
        currency = latest_price.currency
    except Exception:
        logger.warning("Could not fetch latest price for %s — returning None", symbol)
        return None, None
    
    # Calculate totals
    total_market_value_usd = 0.0
    total_ugl_usd = 0.0
    
    for pos in positions:
        price_usd = current_price
        cost_basis_usd = pos.cost_basis_per_unit
        
        if currency == 'CAD':
            price_usd /= fx_rate
            cost_basis_usd /= fx_rate
        
        market_value_usd = pos.quantity * price_usd
        ugl_usd = pos.quantity * (price_usd - cost_basis_usd)
        
        total_market_value_usd += market_value_usd
        total_ugl_usd += ugl_usd
    
    ugl_at_risk_usd = total_market_value_usd * var_parametric_95
    ugl_at_risk_pct = ugl_at_risk_usd / total_ugl_usd if total_ugl_usd > 0 else None
    
    return ugl_at_risk_usd, ugl_at_risk_pct


def to_usd(value: float, currency: str, fx_rate: float) -> float:
    """
    Convert value to USD.
    If currency == 'USD': return value unchanged.
    If currency == 'CAD': return value / fx_rate  (fx_rate = CAD per 1 USD)
    """
    if currency == 'USD':
        return value
    elif currency == 'CAD':
        return value / fx_rate
    else:
        # For other currencies, assume 1:1 or handle as needed
        logger.warning("Unknown currency %s, assuming 1:1 with USD", currency)
        return value
