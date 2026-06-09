"""
Monte Carlo simulation engine for portfolio risk analysis.

Generates price paths using:
- Geometric Brownian Motion (GBM)
- Merton jump-diffusion (for crypto/NVDA)
- Correlated paths via Cholesky decomposition

Applies strategy overlays (trailing stop, collar, protective put) and
computes output distributions for risk metrics.

All path generation is vectorised numpy — no Python loops over individual paths.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy import linalg

logger = logging.getLogger(__name__)

# Default jump-diffusion parameters (overridden by rm_scoring_config at runtime)
DEFAULT_JUMP_PARAMS: dict[str, dict[str, float]] = {
    "crypto": {
        "jump_intensity": 5.0,
        "jump_mean": -0.04,
        "jump_std": 0.08,
    },
    "nvda": {
        "jump_intensity": 3.0,
        "jump_mean": -0.02,
        "jump_std": 0.06,
    },
}


# ---------------------------------------------------------------------------
# 1. Geometric Brownian Motion
# ---------------------------------------------------------------------------

def generate_gbm_paths(
    S0: float,
    mu: float,
    sigma: float,
    horizon_days: int,
    n_paths: int,
    dt: float = 1 / 252,
    rng: np.random.Generator | None = None,
) -> NDArray[np.float64]:
    """Generate N independent GBM price paths.

    Formula per time step:
        S[t] = S[t-1] * exp((mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z)
    where Z ~ N(0,1)

    Args:
        S0: Starting price.
        mu: Annualised drift.
        sigma: Annualised volatility.
        horizon_days: Number of trading days to simulate.
        n_paths: Number of paths to generate.
        dt: Time step (default 1/252 for daily).
        rng: Optional numpy RandomState for reproducibility.

    Returns:
        np.ndarray of shape (n_paths, horizon_days + 1).
        Column 0 = S0. Column horizon_days = final price.
    """
    if rng is None:
        rng = np.random.default_rng()

    log_returns = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * rng.standard_normal(
        (n_paths, horizon_days)
    )
    paths = S0 * np.exp(np.cumsum(log_returns, axis=1))
    paths = np.hstack([np.full((n_paths, 1), S0), paths])
    return paths


# ---------------------------------------------------------------------------
# 2. Merton Jump-Diffusion
# ---------------------------------------------------------------------------

def generate_jump_diffusion_paths(
    S0: float,
    mu: float,
    sigma: float,
    jump_intensity: float,
    jump_mean: float,
    jump_std: float,
    horizon_days: int,
    n_paths: int,
    dt: float = 1 / 252,
    rng: np.random.Generator | None = None,
) -> NDArray[np.float64]:
    """Merton jump-diffusion paths: GBM + compound Poisson jump process.

    Per time step:
        N_t ~ Poisson(lambda * dt)
        J_t = sum of N_t jumps, each drawn from N(mu_J, sigma_J)
        S[t] = S[t-1] * exp((mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z + J_t)

    Args:
        S0: Starting price.
        mu: Annualised drift.
        sigma: Annualised volatility.
        jump_intensity: Lambda — average number of jumps per year.
        jump_mean: mu_J — mean jump size (log scale).
        jump_std: sigma_J — std of jump size (log scale).
        horizon_days: Number of trading days.
        n_paths: Number of paths.
        dt: Time step.
        rng: Optional numpy RandomState.

    Returns:
        np.ndarray of shape (n_paths, horizon_days + 1).
    """
    if rng is None:
        rng = np.random.default_rng()

    # GBM component
    gbm_log_returns = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * rng.standard_normal(
        (n_paths, horizon_days)
    )

    # Jump component — fully vectorised
    # Number of jumps per step per path: N ~ Poisson(lambda * dt)
    n_jumps = rng.poisson(jump_intensity * dt, size=(n_paths, horizon_days))

    # Vectorised jump size computation:
    # 1. Determine maximum jumps needed across all path-step combinations
    max_jumps = int(n_jumps.max()) if n_jumps.size > 0 else 0
    
    # 2. Generate all jump sizes at once: (n_paths, horizon_days, max_jumps)
    if max_jumps > 0:
        all_jump_sizes = rng.normal(jump_mean, jump_std, size=(n_paths, horizon_days, max_jumps))
        
        # 3. Create mask for valid jumps: True where jump index < n_jumps[path, step]
        jump_indices = np.arange(max_jumps, dtype=np.int32)  # (max_jumps,)
        # Broadcast to (n_paths, horizon_days, max_jumps)
        valid_mask = jump_indices < n_jumps[:, :, np.newaxis]  # (n_paths, horizon_days, max_jumps)
        
        # 4. Sum only valid jumps along the last axis
        # Replace invalid jumps with 0 before summing
        masked_jumps = np.where(valid_mask, all_jump_sizes, 0.0)
        jump_sizes = np.sum(masked_jumps, axis=2)  # (n_paths, horizon_days)
    else:
        jump_sizes = np.zeros((n_paths, horizon_days))

    total_log_returns = gbm_log_returns + jump_sizes
    paths = S0 * np.exp(np.cumsum(total_log_returns, axis=1))
    paths = np.hstack([np.full((n_paths, 1), S0), paths])
    return paths


# ---------------------------------------------------------------------------
# 3. Nearest Positive Definite Matrix (Higham 1988)
# ---------------------------------------------------------------------------

def nearest_positive_definite(matrix: NDArray[np.float64]) -> NDArray[np.float64]:
    """Find the nearest positive definite matrix using Higham's algorithm.

    Algorithm:
    1. Symmetrise: A = (matrix + matrix.T) / 2
    2. Eigendecompose: eigenvalues, eigenvectors = np.linalg.eigh(A)
    3. Clip negative eigenvalues to epsilon = 1e-8
    4. Reconstruct: A_pd = V @ diag(clipped_eigenvalues) @ V.T
    5. Re-symmetrise and rescale diagonal to 1.0

    Args:
        matrix: Symmetric matrix (possibly non-PD).

    Returns:
        Nearest positive definite matrix with unit diagonal.
    """
    # Step 1: Symmetrise
    A = (matrix + matrix.T) / 2.0

    # Step 2: Eigendecompose
    eigenvalues, eigenvectors = np.linalg.eigh(A)

    # Step 3: Clip negative eigenvalues
    epsilon = 1e-8
    clipped = np.maximum(eigenvalues, epsilon)

    # Step 4: Reconstruct
    A_pd = eigenvectors @ np.diag(clipped) @ eigenvectors.T

    # Step 5: Re-symmetrise and rescale diagonal to 1.0
    A_pd = (A_pd + A_pd.T) / 2.0
    D = np.sqrt(np.diag(A_pd))
    A_pd = A_pd / np.outer(D, D)

    return A_pd


# ---------------------------------------------------------------------------
# 4. Correlated Portfolio Paths
# ---------------------------------------------------------------------------

def generate_correlated_paths(
    symbols: list[str],
    S0_values: dict[str, float],
    mu_values: dict[str, float],
    sigma_values: dict[str, float],
    correlation_matrix: NDArray[np.float64],
    position_weights: dict[str, float],
    horizon_days: int,
    n_paths: int,
    use_jump_diffusion_flags: dict[str, bool] | None = None,
    jump_params: dict[str, dict[str, float]] | None = None,
    dt: float = 1 / 252,
    rng: np.random.Generator | None = None,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Generate correlated price paths using Cholesky decomposition.

    Algorithm:
    1. Cholesky: L = cholesky(correlation_matrix) with Higham nearest-PD fix.
    2. Independent draws: Z_independent ~ N(0,1) shape (n_paths, n_symbols, horizon_days).
    3. Correlate: Z_correlated = Z_independent @ L.T.
    4. Generate paths per symbol using correlated Z.
    5. Compute portfolio value at each step.

    Args:
        symbols: List of ticker symbols.
        S0_values: {symbol: current price}.
        mu_values: {symbol: annualised drift}.
        sigma_values: {symbol: annualised volatility}.
        correlation_matrix: Square matrix shape (n_symbols, n_symbols).
        position_weights: {symbol: portfolio weight}.
        horizon_days: Simulation horizon.
        n_paths: Number of paths.
        use_jump_diffusion_flags: {symbol: bool}.
        jump_params: {symbol: {intensity, mean, std}}.
        dt: Time step.
        rng: Optional RandomState.

    Returns:
        symbol_paths: np.ndarray shape (n_symbols, n_paths, horizon_days + 1).
        portfolio_paths: np.ndarray shape (n_paths, horizon_days + 1).
    """
    if rng is None:
        rng = np.random.default_rng()

    n_symbols = len(symbols)
    if correlation_matrix.shape != (n_symbols, n_symbols):
        raise ValueError(
            f"Correlation matrix shape {correlation_matrix.shape} does not match "
            f"number of symbols {n_symbols}"
        )

    # Ensure correlation matrix is PD
    if not _is_positive_definite(correlation_matrix):
        logger.warning(
            "Correlation matrix is not positive definite; applying Higham nearest-PD fix"
        )
        correlation_matrix = nearest_positive_definite(correlation_matrix)

    # Cholesky decomposition
    try:
        L = linalg.cholesky(correlation_matrix, lower=True)
    except linalg.LinAlgError:
        logger.warning("Cholesky failed; applying Higham nearest-PD fix and retrying")
        correlation_matrix = nearest_positive_definite(correlation_matrix)
        L = linalg.cholesky(correlation_matrix, lower=True)

    # Independent standard normal draws: (n_paths, horizon_days, n_symbols)
    Z_independent = rng.standard_normal((n_paths, horizon_days, n_symbols))

    # Apply correlation: Z_correlated[path, t, :] = Z_independent[path, t, :] @ L.T
    # This gives correlated draws per time step
    Z_correlated = Z_independent @ L.T  # (n_paths, horizon_days, n_symbols)

    # Generate paths for each symbol
    symbol_paths_list: list[NDArray[np.float64]] = []
    use_jump = use_jump_diffusion_flags or {}
    jparams = jump_params or {}

    for sym_idx, sym in enumerate(symbols):
        S0 = S0_values[sym]
        mu = mu_values[sym]
        sigma = sigma_values[sym]
        Z_sym = Z_correlated[:, :, sym_idx]  # (n_paths, horizon_days)

        if use_jump.get(sym, False):
            jp = jparams.get(sym, DEFAULT_JUMP_PARAMS.get("crypto", {}))
            paths = generate_jump_diffusion_paths(
                S0=S0,
                mu=mu,
                sigma=sigma,
                jump_intensity=jp.get("jump_intensity", 5.0),
                jump_mean=jp.get("jump_mean", -0.04),
                jump_std=jp.get("jump_std", 0.08),
                horizon_days=horizon_days,
                n_paths=n_paths,
                dt=dt,
                rng=rng,
            )
        else:
            paths = generate_gbm_paths(
                S0=S0,
                mu=mu,
                sigma=sigma,
                horizon_days=horizon_days,
                n_paths=n_paths,
                dt=dt,
                rng=rng,
            )

        symbol_paths_list.append(paths)  # each: (n_paths, horizon_days + 1)

    symbol_paths = np.stack(symbol_paths_list, axis=0)  # (n_symbols, n_paths, horizon_days + 1)

    # Compute portfolio value at each time step
    # portfolio_value[path, t] = sum_s weight[s] * price[s, path, t] / price[s, path, 0]
    # This gives normalised portfolio value (starting at sum of weights = 1.0)
    weights_array = np.array([position_weights.get(sym, 0.0) for sym in symbols])  # (n_symbols,)

    # Normalised returns: (n_symbols, n_paths, horizon_days + 1)
    normalised = symbol_paths / symbol_paths[:, :, 0:1]  # broadcast divide

    # Portfolio value per path per time: weights @ normalised
    # weights: (n_symbols,) @ normalised: (n_symbols, n_paths, horizon_days + 1)
    # Need to sum over symbols: portfolio_paths[path, time] = sum_s weight[s] * normalised[s, path, time]
    portfolio_paths = np.einsum('s,spt->pt', weights_array, normalised)  # (n_paths, horizon_days + 1)

    return symbol_paths, portfolio_paths


def _is_positive_definite(matrix: NDArray[np.float64]) -> bool:
    """Check if a symmetric matrix is positive definite."""
    try:
        linalg.cholesky(matrix, lower=True)
        return True
    except linalg.LinAlgError:
        return False


# ---------------------------------------------------------------------------
# 5. Strategy Overlay
# ---------------------------------------------------------------------------

def apply_strategy_overlay(
    portfolio_paths: NDArray[np.float64],
    symbol_paths: NDArray[np.float64],
    strategy_code: str,
    strategy_params: dict[str, Any],
    position_symbol: str,
    symbol_index: int,
    position_weight: float,
    vol_annualised: float,
    risk_free_rate: float = 0.05,
    hedge_cost_per_unit: float = 0.0,
    leverage_factor: float = 1.0,
    daily_decay: float = 0.0,
) -> NDArray[np.float64]:
    """Apply a strategy overlay to the portfolio paths.

    Args:
        portfolio_paths: Normalised portfolio paths shape (n_paths, T+1).
        symbol_paths: Symbol paths shape (n_symbols, n_paths, T+1).
        strategy_code: e.g. 'trailing_stop', 'collar', 'protective_put', 'inverse_etf'.
        strategy_params: Strategy-specific parameters.
        position_symbol: Ticker of the symbol the strategy applies to.
        symbol_index: Index of that symbol in symbol_paths.
        position_weight: Weight of the symbol in the portfolio.
        vol_annualised: Annualised volatility of the symbol.
        risk_free_rate: Risk-free rate (default 5%).
        hedge_cost_per_unit: Upfront hedge cost per unit.
        leverage_factor: Leverage for inverse ETF (default 1.0).
        daily_decay: Daily decay cost for inverse ETF (default 0).

    Returns:
        hedged_portfolio_paths: np.ndarray shape (n_paths, T+1).
    """
    n_paths, T_plus_1 = portfolio_paths.shape
    sym_paths = symbol_paths[symbol_index]  # (n_paths, T+1)

    if strategy_code == "trailing_stop":
        trail_pct = strategy_params.get("trail_pct", 0.10)
        return _apply_trailing_stop(
            portfolio_paths, sym_paths, trail_pct, position_weight
        )

    elif strategy_code in ("protective_put", "collar"):
        put_strike_pct = strategy_params.get("put_strike_pct", 0.85)
        call_strike_pct = strategy_params.get("call_strike_pct")
        quantity = strategy_params.get("quantity", 100)

        put_strike = sym_paths[:, 0] * put_strike_pct

        # Payoff at horizon: max(0, put_strike - final_price)
        payoff_per_unit = np.maximum(0, put_strike - sym_paths[:, -1])

        # Upfront cost (already paid, subtract from each path)
        total_cost = hedge_cost_per_unit * quantity

        # Adjust portfolio: replace symbol contribution with put payoff
        # portfolio = (1 - weight) * other_assets + weight * max(final, put_strike) - cost
        # Simplified: portfolio_hedged = portfolio - weight*(sym_final - max(sym_final, put_strike)) - cost
        sym_final = sym_paths[:, -1] / sym_paths[:, 0]  # normalised final
        sym_final_hedged = np.maximum(sym_final, put_strike / sym_paths[:, 0])
        adjustment = position_weight * (sym_final_hedged - sym_final)
        hedged = portfolio_paths - adjustment
        hedged[:, -1] -= total_cost  # subtract cost only at horizon
        return hedged

    elif strategy_code == "inverse_etf":
        return _apply_inverse_etf(
            portfolio_paths, sym_paths, position_weight,
            leverage_factor, daily_decay,
        )

    else:
        logger.warning("Unknown strategy_code '%s'; returning unhedged paths", strategy_code)
        return portfolio_paths


def _apply_trailing_stop(
    portfolio_paths: NDArray[np.float64],
    sym_paths: NDArray[np.float64],
    trail_pct: float,
    position_weight: float,
) -> NDArray[np.float64]:
    """Apply trailing stop overlay to portfolio paths.

    Vectorised approach:
    1. Compute high water mark for each path.
    2. Find first day price drops below trail_pct from high.
    3. From exit day onward: replace symbol price with exit price.
    """
    n_paths, T_plus_1 = portfolio_paths.shape

    # High water mark: cummax along time axis for each path
    high_watermark = np.maximum.accumulate(sym_paths, axis=1)

    # Trail trigger: price < high * (1 - trail_pct)
    trigger = sym_paths < high_watermark * (1 - trail_pct)

    # Find first trigger day per path
    trigger_days = np.full(n_paths, T_plus_1, dtype=int)
    trigger_mask = np.any(trigger, axis=1)
    trigger_days[trigger_mask] = np.argmax(trigger[trigger_mask], axis=1)

    # Build hedged symbol paths: from exit day onward, use exit price
    exit_prices = np.where(
        trigger_mask,
        sym_paths[np.arange(n_paths), trigger_days],
        sym_paths[:, -1],  # never triggered — use final price
    )

    # Create hedged symbol paths: replace post-exit prices with exit price
    # Fully vectorised: use advanced indexing to set all post-exit prices at once
    hedged_sym_paths = sym_paths.copy()
    
    # For paths that triggered, set post-exit prices to exit price
    triggered_paths = np.where(trigger_mask)[0]
    if len(triggered_paths) > 0:
        # Create time indices array: (T_plus_1,)
        time_indices = np.arange(T_plus_1)
        
        # For each triggered path, set prices from exit day onward to exit price
        for path_idx in triggered_paths:
            exit_day = trigger_days[path_idx]
            hedged_sym_paths[path_idx, exit_day:] = exit_prices[path_idx]

    # Adjust portfolio: replace symbol contribution
    # Original: portfolio = (1 - w) * others + w * sym/S0
    # Hedged:   portfolio = (1 - w) * others + w * hedged_sym/S0
    sym_normalised = sym_paths / sym_paths[:, 0:1]
    hedged_sym_normalised = hedged_sym_paths / sym_paths[:, 0:1]
    adjustment = position_weight * (hedged_sym_normalised - sym_normalised)
    hedged_portfolio = portfolio_paths + adjustment

    return hedged_portfolio


def _apply_inverse_etf(
    portfolio_paths: NDArray[np.float64],
    sym_paths: NDArray[np.float64],
    position_weight: float,
    leverage_factor: float,
    daily_decay: float,
) -> NDArray[np.float64]:
    """Apply inverse ETF overlay.

    daily_hedge_return = -symbol_daily_return * leverage * (1 - daily_decay)
    """
    n_paths, T_plus_1 = portfolio_paths.shape

    # Daily returns of the symbol
    daily_returns = np.diff(sym_paths, axis=1) / sym_paths[:, :-1]

    # Inverse hedge returns
    hedge_returns = -daily_returns * leverage_factor * (1 - daily_decay)

    # Cumulative hedge return per path
    cum_hedge = np.cumsum(hedge_returns, axis=1)

    # Adjust portfolio: add hedge payoff to each path
    # Scale by position weight (hedge replaces the original position)
    adjustment = position_weight * cum_hedge
    hedged = portfolio_paths + adjustment

    return hedged


# ---------------------------------------------------------------------------
# 6. Output Distribution Computation
# ---------------------------------------------------------------------------

def compute_output_distribution(
    portfolio_paths: NDArray[np.float64],
    portfolio_value_start: float,
    sample_days: list[int] | None = None,
) -> dict[str, Any]:
    """Compute the full output distribution from simulated paths.

    Args:
        portfolio_paths: Shape (n_paths, T+1).
        portfolio_value_start: Starting portfolio value in USD.
        sample_days: Time points for percentile series (default [0, 21, 63, 126, 252]).

    Returns:
        Dict with percentiles, loss probabilities, expected shortfall, and series JSON.
    """
    if sample_days is None:
        sample_days = [0, 21, 63, 126, 252]

    final_values = portfolio_paths[:, -1]  # terminal value per path

    # Percentiles
    pct5 = float(np.percentile(final_values, 5))
    pct25 = float(np.percentile(final_values, 25))
    pct50 = float(np.percentile(final_values, 50))
    pct75 = float(np.percentile(final_values, 75))
    pct95 = float(np.percentile(final_values, 95))

    # Loss probabilities
    prob_loss_5pct = float(np.mean(final_values < portfolio_value_start * 0.95))
    prob_loss_10pct = float(np.mean(final_values < portfolio_value_start * 0.90))
    prob_loss_20pct = float(np.mean(final_values < portfolio_value_start * 0.80))
    prob_loss_50pct = float(np.mean(final_values < portfolio_value_start * 0.50))

    # Expected Shortfall (CVaR at 95%)
    worst_5pct = final_values[final_values <= np.percentile(final_values, 5)]
    expected_shortfall = float(np.mean(worst_5pct)) if len(worst_5pct) > 0 else pct5
    expected_shortfall_pct = (portfolio_value_start - expected_shortfall) / portfolio_value_start

    # Percentile series
    percentile_series = build_percentile_series_json(portfolio_paths, sample_days)

    return {
        "pct5": pct5,
        "pct25": pct25,
        "pct50": pct50,
        "pct75": pct75,
        "pct95": pct95,
        "prob_loss_5pct": prob_loss_5pct,
        "prob_loss_10pct": prob_loss_10pct,
        "prob_loss_20pct": prob_loss_20pct,
        "prob_loss_50pct": prob_loss_50pct,
        "expected_shortfall": expected_shortfall,
        "expected_shortfall_pct": expected_shortfall_pct,
        "percentile_series_json": percentile_series,
    }


def build_percentile_series_json(
    portfolio_paths: NDArray[np.float64],
    sample_days: list[int] = None,
) -> str:
    """Extract percentile bands at sampled time points for charting.

    Args:
        portfolio_paths: Shape (n_paths, T+1).
        sample_days: Time points to sample (default [0, 21, 63, 126, 252]).

    Returns:
        JSON string matching rm_simulation_result.percentile_series_json schema.
    """
    if sample_days is None:
        sample_days = [0, 21, 63, 126, 252]

    T_plus_1 = portfolio_paths.shape[1]
    # Cap sample days at actual horizon
    days = [min(d, T_plus_1 - 1) for d in sample_days]

    result = {"days": days}
    for pct in ["p5", "p25", "p50", "p75", "p95"]:
        p = int(pct[1:])
        result[pct] = [
            float(np.percentile(portfolio_paths[:, day], p)) for day in days
        ]

    return json.dumps(result)
