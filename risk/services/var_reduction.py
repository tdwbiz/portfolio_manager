"""
VaR (Value at Risk) reduction estimation for M4 strategy optimisation engine.

Estimates how much each strategy type reduces the 1-day 95% VaR of a position.
These are directional estimates only — not trading-grade risk calculations.

Dependencies: None (pure math, no Django ORM)
"""


def estimate_var_reduction(
    strategy_code: str,
    position_market_value_usd: float,
    var_parametric_95: float,
    beta_spy: float,
    current_price_usd: float,
    stop_trail_pct: float = 0.10,
) -> dict:
    """
    Estimate the reduction in 1-day 95% VaR if this strategy is applied.

    Parameters
    ----------
    strategy_code : str
        Strategy template code (e.g. 'protective_put', 'collar', 'trailing_stop').
    position_market_value_usd : float
        Current market value of the position in USD.
    var_parametric_95 : float
        1-day 95% parametric VaR as a fraction (e.g. 0.03 = 3% daily VaR).
    beta_spy : float
        Beta of the position relative to S&P 500.
    current_price_usd : float
        Current price per unit in USD.
    stop_trail_pct : float, optional
        Trailing stop percentage (default 0.10 = 10%).

    Returns
    -------
    dict
        {
            'var_reduction_usd': float,
            'var_reduction_pct_of_mv': float,
            'method': str,  # Description of estimation method used.
        }
    """
    position_var = var_parametric_95 * position_market_value_usd

    # --- Protective put ---
    # ATM put has delta ≈ -0.5. The put gains ~$0.50 for every $1.00 the stock falls.
    # VaR reduction ≈ 50% of position VaR (half of the 1-day tail loss is offset)
    if strategy_code == 'protective_put':
        reduction = position_var * 0.5
        return {
            'var_reduction_usd': reduction,
            'var_reduction_pct_of_mv': reduction / position_market_value_usd,
            'method': 'ATM put delta ≈ -0.5; VaR reduction = 50% of position VaR',
        }

    # --- Collar ---
    # Same as protective_put: put provides the downside protection.
    # Upside is capped at the call strike — not a VaR concern.
    if strategy_code == 'collar':
        reduction = position_var * 0.5
        return {
            'var_reduction_usd': reduction,
            'var_reduction_pct_of_mv': reduction / position_market_value_usd,
            'method': 'Collar put delta ≈ -0.5; VaR reduction = 50% of position VaR. '
                     'Upside capped at call strike.',
        }

    # --- Trailing stop ---
    # If stop is at (1 - stop_trail_pct) * current_price:
    # Maximum loss after stop = stop_trail_pct * position_market_value
    # VaR_after_stop = min(var_parametric_95 * position_mv, stop_trail_pct * position_mv)
    # VaR_reduction = max(0, var_parametric_95 * position_mv - VaR_after_stop)
    # Note: stop does not prevent gap-down losses; documented as limitation.
    if strategy_code in ('trailing_stop', 'trailing_stop_cef', 'trailing_stop_registered'):
        max_loss_after_stop = stop_trail_pct * position_market_value_usd
        var_after_stop = min(position_var, max_loss_after_stop)
        reduction = max(0, position_var - var_after_stop)
        return {
            'var_reduction_usd': reduction,
            'var_reduction_pct_of_mv': reduction / position_market_value_usd,
            'method': (
                f'Trailing stop at {stop_trail_pct * 100:.0f}%; '
                f'max loss = {stop_trail_pct * 100:.0f}% of MV. '
                f'Note: stop does not prevent gap-down losses.'
            ),
        }

    # --- Inverse ETF ---
    # hedge_ratio = 0.5 (assume 50% hedge)
    # For 1x inverse ETF (PSQ): VaR reduction = var * position_mv * hedge_ratio
    # For 3x inverse ETF (SOXS): discount to 0.8x due to basis risk and tracking error
    if strategy_code == 'inverse_etf':
        hedge_ratio = 0.5
        # Check if this is likely a 3x ETF (high beta suggests leveraged)
        # We use a simple heuristic: if beta > 1.5, apply 0.8 discount
        leverage_discount = 0.8 if beta_spy > 1.5 else 1.0
        reduction = position_var * hedge_ratio * leverage_discount
        return {
            'var_reduction_usd': reduction,
            'var_reduction_pct_of_mv': reduction / position_market_value_usd,
            'method': (
                f'50% hedge with inverse ETF. '
                f'{"3x leverage — 0.8x basis risk discount applied." if beta_spy > 1.5 else "1x inverse ETF."}'
            ),
        }

    # --- Sector rotation / partial liquidation / cash raise ---
    # Assume 25% of position is reduced.
    # Linear: removing 25% of position removes 25% of its VaR contribution.
    if strategy_code in ('sector_rotation', 'partial_liquidation', 'cash_raise'):
        reduction = position_var * 0.25
        return {
            'var_reduction_usd': reduction,
            'var_reduction_pct_of_mv': reduction / position_market_value_usd,
            'method': '25% position reduction; linear VaR reduction = 25% of position VaR',
        }

    # --- Covered call ---
    # No VaR reduction (call premium does not protect downside).
    # This is an income strategy; rank it separately.
    if strategy_code == 'covered_call':
        return {
            'var_reduction_usd': 0.0,
            'var_reduction_pct_of_mv': 0.0,
            'method': 'Covered call provides income only; no downside protection.',
        }

    # --- Catch-all ---
    return {
        'var_reduction_usd': 0.0,
        'var_reduction_pct_of_mv': 0.0,
        'method': f'Unknown strategy code: {strategy_code}. No VaR reduction estimated.',
    }
