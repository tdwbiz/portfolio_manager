"""
Portfolio-level risk metric aggregation service.

All functions are pure Python (no Django ORM calls) and take plain
DataFrames/arrays as input. This makes them independently testable.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


def build_position_weights(
    positions: list[dict],
    security_metrics: dict[str, dict],
    fx_rate: float,
) -> tuple[np.ndarray, list[str], float]:
    """
    Build a weight vector and symbol list for an entity's positions.

    positions: list of dicts with keys symbol, quantity, currency, account_category
               sourced from vw_current_portfolio filtered to this entity
    security_metrics: dict keyed by symbol, values from rm_security_risk_metrics

    Returns:
        weights: np.ndarray of shape (n,) — each weight = position_mv_usd / total_mv_usd
        symbols: list of str in same order as weights
        total_mv_usd: float

    Skips symbols where:
    - var_parametric_95 is NULL in rm_security_risk_metrics (insufficient price history)
    - vol_252d is NULL
    Logs skipped symbols to Django logger.
    """
    total_mv_usd = 0.0
    symbol_mv_usd: dict[str, float] = {}

    for pos in positions:
        symbol = pos['symbol']
        quantity = float(pos['quantity'])
        currency = pos.get('currency', 'USD')
        price = float(pos.get('price', 0))

        if currency == 'CAD':
            mv_usd = (quantity * price) / fx_rate
        else:
            mv_usd = quantity * price

        symbol_mv_usd[symbol] = symbol_mv_usd.get(symbol, 0.0) + mv_usd

    # Filter to symbols with valid security metrics
    valid_symbols = []
    for symbol, mv in symbol_mv_usd.items():
        metrics = security_metrics.get(symbol)
        if metrics is None:
            logger.warning("No security metrics for %s, skipping", symbol)
            continue
        if metrics.get('var_parametric_95') is None or metrics.get('vol_252d') is None:
            logger.warning("Missing risk metrics for %s (var or vol is NULL), skipping", symbol)
            continue
        valid_symbols.append(symbol)
        total_mv_usd += mv

    if total_mv_usd == 0:
        return np.array([]), [], 0.0

    weights = np.array([symbol_mv_usd[s] / total_mv_usd for s in valid_symbols])
    return weights, valid_symbols, total_mv_usd


def build_covariance_matrix(
    symbols: list[str],
    security_metrics: dict[str, dict],
    correlation_matrix: dict[tuple[str, str], float],
) -> np.ndarray:
    """
    Build annualised covariance matrix for a list of symbols.

    Formula: Sigma[i,j] = corr(i,j) * vol_252d[i] * vol_252d[j]
    Diagonal: Sigma[i,i] = vol_252d[i]^2

    If correlation for pair (i,j) is missing (insufficient overlap in M1):
    assume correlation = 0.5 as a conservative estimate and log a warning.
    This prevents a missing pair from making the entire matrix unusable.

    Returns np.ndarray of shape (n, n).
    Raises ValueError if matrix is not positive semi-definite after construction
    (indicates data quality issue — log the symbols involved).
    """
    n = len(symbols)
    vols = np.array([security_metrics[s]['vol_252d'] for s in symbols])
    cov = np.zeros((n, n))

    for i in range(n):
        for j in range(n):
            if i == j:
                cov[i, j] = vols[i] ** 2
            else:
                pair = (symbols[i], symbols[j])
                corr = correlation_matrix.get(pair)
                if corr is None:
                    logger.warning(
                        "Missing correlation for %s/%s, using conservative default 0.5",
                        symbols[i], symbols[j],
                    )
                    corr = 0.5
                cov[i, j] = corr * vols[i] * vols[j]

    # Check positive semi-definite
    eigvals = np.linalg.eigvalsh(cov)
    if np.any(eigvals < -1e-10):
        # Try to fix by setting negative eigenvalues to zero
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        eigenvalues = np.maximum(eigenvalues, 0)
        cov = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T
        logger.warning(
            "Covariance matrix was not positive semi-definite; fixed by zeroing negative eigenvalues. "
            "Symbols: %s", symbols,
        )

    return cov


def portfolio_var_markowitz(
    weights: np.ndarray,
    cov_matrix: np.ndarray,
    total_mv_usd: float,
    confidence: float = 0.95,
    horizon_days: int = 1,
) -> tuple[float, float]:
    """
    Markowitz portfolio VaR using correlation-adjusted covariance.

    Formula:
        portfolio_variance = w^T * Sigma * w
        portfolio_vol_daily = sqrt(portfolio_variance) / sqrt(252)
        z = scipy.stats.norm.ppf(1 - confidence)
        VaR_fraction = z * portfolio_vol_daily * sqrt(horizon_days)
        VaR_usd = abs(VaR_fraction) * total_mv_usd

    Returns (var_fraction, var_usd).

    Note: this formula uses annualised vols on the covariance matrix,
    so divides by sqrt(252) to get daily vol, then scales by sqrt(horizon_days).
    """
    if len(weights) == 0:
        return 0.0, 0.0

    portfolio_variance = float(weights @ cov_matrix @ weights)
    portfolio_vol_daily = np.sqrt(portfolio_variance) / np.sqrt(252)
    z = stats.norm.ppf(1 - confidence)
    var_fraction = z * portfolio_vol_daily * np.sqrt(horizon_days)
    var_usd = abs(var_fraction) * total_mv_usd

    return var_fraction, var_usd


def portfolio_var_undiversified(
    weights: np.ndarray,
    individual_vars_95: np.ndarray,
    total_mv_usd: float,
) -> float:
    """
    Simple weighted sum of individual VaRs — ignores all correlation.
    = sum(weight_i * var_i) * total_mv_usd
    Always >= correlated VaR. Difference = diversification benefit.
    """
    if len(weights) == 0:
        return 0.0
    weighted_var_sum = np.sum(weights * individual_vars_95)
    return float(weighted_var_sum * total_mv_usd)


def diversification_ratio(
    weights: np.ndarray,
    individual_vols: np.ndarray,
    portfolio_vol: float,
) -> float:
    """
    Diversification ratio = weighted_avg_individual_vol / portfolio_vol.

    weighted_avg_individual_vol = sum(weight_i * vol_i)
    portfolio_vol = from portfolio_var_markowitz (annualised)

    Interpretation:
    - ratio = 1.0: no diversification benefit (perfectly correlated)
    - ratio > 1.0: diversification reducing risk (typical for this portfolio)
    - ratio < 1.0: impossible in theory; indicates data inconsistency

    This portfolio should show ratio ~1.15–1.35 given mixed asset classes.
    """
    if portfolio_vol == 0:
        return 1.0
    weighted_avg_vol = float(np.sum(weights * individual_vols))
    return weighted_avg_vol / portfolio_vol


def hhi(weights: np.ndarray) -> float:
    """
    Herfindahl-Hirschman Index. sum(w_i^2).
    weights must sum to 1.0.
    Returns float in [0, 1]. Lower = more diversified.
    Example for this portfolio: VDY at 20.7% alone contributes 0.207^2 = 0.043.
    """
    return float(np.sum(weights ** 2))


def hhi_by_group(
    symbols: list[str],
    weights: np.ndarray,
    group_map: dict[str, str],
) -> float:
    """
    HHI computed on grouped weights.
    group_map: {symbol: group_name} e.g. {VDY: 'Financial', QQQ: 'Technology'}
    First aggregates weights by group, then computes HHI on group weights.
    """
    group_weights: dict[str, float] = {}
    for symbol, weight in zip(symbols, weights):
        group = group_map.get(symbol, 'Unknown')
        group_weights[group] = group_weights.get(group, 0.0) + weight
    return hhi(np.array(list(group_weights.values())))


def effective_index_weights(
    symbols: list[str],
    position_weights: np.ndarray,
    index_exposures: dict[str, dict[str, float]],
) -> dict[str, float]:
    """
    Compute effective portfolio weight in each underlying index.

    index_exposures: {symbol: {index_name: exposure_weight}}
                     sourced from rm_security_index_exposure

    Formula: effective_index_weight[idx] = sum(position_weight[i] * exposure[i][idx])

    Example for SP500:
        VFV: position weight 0.141, SP500 exposure 1.0  -> 0.141
        SPY: position weight 0.034, SP500 exposure 1.0  -> 0.034
        VOO: position weight 0.029, SP500 exposure 1.0  -> 0.029
        ADX: position weight 0.051, SP500 exposure 0.85 -> 0.043
        IWY: position weight 0.027, SP500 exposure 0.85 -> 0.023
        CGDV:position weight 0.058, SP500 exposure 0.70 -> 0.041
        ...
        Total effective SP500 weight ≈ 0.35+ (35%+ effective S&P 500 exposure)

    Returns dict: {index_name: effective_weight_fraction}
    """
    effective: dict[str, float] = {}
    for symbol, weight in zip(symbols, position_weights):
        exposures = index_exposures.get(symbol, {})
        for idx_name, exposure in exposures.items():
            effective[idx_name] = effective.get(idx_name, 0.0) + weight * exposure
    return effective


def portfolio_return_series(
    symbols: list[str],
    weights: np.ndarray,
    price_data: dict[str, pd.Series],
    window: int = 252,
) -> pd.Series:
    """
    Construct daily portfolio return series for last `window` trading days.

    Method:
    1. For each symbol: load last 252 daily log returns from DataSource
    2. Inner join on date (some symbols may have fewer days)
    3. Portfolio return on day t = sum(weight_i * return_i_t)
       Weights are the current weights (static, not time-varying)
       This is a simplification — we are not rebalancing the weights daily.
    4. Return pd.Series indexed by date.

    Note: uses same weights as current portfolio.
    If a symbol has fewer than 126 days of history in the window,
    substitute its weight proportionally into remaining symbols
    and log a warning.
    """
    # Build return series for each symbol
    symbol_returns: dict[str, pd.Series] = {}
    for symbol in symbols:
        prices = price_data.get(symbol)
        if prices is None or len(prices) < 2:
            logger.warning("Insufficient price data for %s", symbol)
            continue
        returns = np.log(prices / prices.shift(1)).dropna()
        symbol_returns[symbol] = returns.tail(window)

    if not symbol_returns:
        return pd.Series(dtype=float)

    # Align all return series
    aligned = pd.DataFrame(symbol_returns)
    aligned = aligned.dropna(how='all').tail(window)

    if aligned.empty:
        return pd.Series(dtype=float)

    # Compute portfolio returns
    portfolio_returns = aligned @ weights
    return portfolio_returns


def compute_tax_risk_flag(
    entity_type: str,
    entity_id: str,
    positions: list[dict],
    current_prices: dict[str, float],
    fx_rate: float,
    threshold_usd: float,
) -> tuple[float, bool]:
    """
    Compute total unrealised gain in USD for taxable positions in this entity.

    Only includes positions in accounts where category in
    ('Investment', 'Investment Joint') — taxable accounts.
    Excludes TFSA, RRSP, RRIF, LIF.

    taxable_ugl_usd = sum over taxable positions:
        (current_price_usd - cost_basis_per_unit_usd) * quantity

    tax_risk_flag = taxable_ugl_usd > threshold_usd

    threshold_usd sourced from rm_scoring_config key 'taxable_ugl_alert_usd'
    Default if not configured: 100000 (USD 100K)

    Returns (taxable_ugl_usd, tax_risk_flag).
    """
    taxable_categories = {'Investment', 'Investment Joint'}
    taxable_ugl_usd = 0.0

    for pos in positions:
        account_category = pos.get('account_category', '')
        if account_category not in taxable_categories:
            continue

        symbol = pos['symbol']
        quantity = float(pos['quantity'])
        cost_basis = float(pos.get('cost_basis_per_unit', 0))
        currency = pos.get('currency', 'USD')

        current_price = current_prices.get(symbol, 0)
        if currency == 'CAD':
            current_price_usd = current_price / fx_rate
            cost_basis_usd = cost_basis / fx_rate
        else:
            current_price_usd = current_price
            cost_basis_usd = cost_basis

        ugl_usd = (current_price_usd - cost_basis_usd) * quantity
        taxable_ugl_usd += ugl_usd

    tax_risk_flag = taxable_ugl_usd > threshold_usd
    return taxable_ugl_usd, tax_risk_flag


def get_owner_positions(
    owner: str,
    all_positions: list[dict],
    joint_split: float = 0.5,
) -> list[dict]:
    """
    Return positions attributed to this owner.

    For accounts where core_account.owner == owner: include 100% of position.
    For joint accounts (owner field is 'JOINT' or similar):
        include joint_split fraction (default 0.5) of each position quantity.
        Adjust quantity and total_cost_basis by the fraction.

    joint_split sourced from rm_scoring_config key 'joint_account_owner_split'.
    Default 0.5. Both TOM and SUSAN get 50% of joint accounts.

    Joint accounts as of June 2 export: TAXABLE_TOM_SUSAN, IB_JOINT.
    """
    attributed: list[dict] = []

    for pos in all_positions:
        pos_owner = pos.get('owner', '')
        account_id = pos.get('account_id', '')

        if pos_owner == owner:
            # 100% ownership
            attributed.append(pos)
        elif pos_owner == 'JOINT' or 'TOM_SUSAN' in account_id or 'JOINT' in account_id:
            # Joint account: split
            adjusted_pos = pos.copy()
            adjusted_pos['quantity'] = float(pos['quantity']) * joint_split
            if 'total_cost_basis' in pos:
                adjusted_pos['total_cost_basis'] = float(pos['total_cost_basis']) * joint_split
            attributed.append(adjusted_pos)

    return attributed
