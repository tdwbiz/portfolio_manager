"""
Black-Scholes option pricing for M4 strategy optimisation.

All pricing is for European-style options. Used for directional estimates only,
not trading-grade pricing. Real options are American-style and may be exercised
early, but Black-Scholes provides a reasonable approximation for cost estimation.

Dependencies: numpy, scipy
"""

import math
import numpy as np
from scipy.stats import norm


def black_scholes_put(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
) -> float:
    """
    Black-Scholes price for a European put option.

    Parameters
    ----------
    S : float
        Current stock price (USD).
    K : float
        Strike price (USD) — use S for ATM.
    T : float
        Time to expiry in years (e.g. 0.5 for 6 months).
    r : float
        Risk-free rate (annual; use SGOV yield from rm_economic_indicators).
    sigma : float
        Annualised volatility (from rm_volatility_summary, 63d window).

    Returns
    -------
    float
        Put premium in USD per share.
        Multiply by 100 to get per-contract value (standard US option contract = 100 shares).
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    put_price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return max(put_price, 0.0)


def black_scholes_call(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
) -> float:
    """
    Black-Scholes price for a European call option.

    Parameters
    ----------
    S : float
        Current stock price (USD).
    K : float
        Strike price (USD).
    T : float
        Time to expiry in years.
    r : float
        Risk-free rate (annual).
    sigma : float
        Annualised volatility.

    Returns
    -------
    float
        Call premium in USD per share.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    call_price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return max(call_price, 0.0)


def estimate_option_cost(
    strategy_code: str,
    current_price_usd: float,
    quantity: float,
    vol_63d: float,
    risk_free_rate: float,
    expiry_months: int = 6,
    otm_call_pct: float = 0.12,
) -> dict:
    """
    Estimate total option strategy cost for a given position.

    Parameters
    ----------
    strategy_code : str
        'protective_put', 'collar', or 'covered_call'.
    current_price_usd : float
        Price per unit in USD.
    quantity : float
        Number of shares/units held.
    vol_63d : float
        63-day annualised volatility from rm_volatility_summary.
    risk_free_rate : float
        Current SGOV yield or fallback rate (annual).
    expiry_months : int, optional
        Option expiry horizon in months (default 6).
    otm_call_pct : float, optional
        For collar — call strike is this % above current price (default 0.12 = 12%).

    Returns
    -------
    dict
        {
            'put_premium_per_share': float,
            'call_premium_per_share': float,  # 0 if not applicable
            'net_premium_per_share': float,   # put - call received
            'total_premium_usd': float,       # net_premium_per_share * quantity
            'commission_usd': float,          # $1.50 per contract * number of contracts
            'bid_ask_usd': float,             # 0.4% of notional as bid-ask estimate
            'total_cost_usd': float,
        }

        For trailing_stop or inverse_etf: return friction-only costs (no premium).
    """
    T = expiry_months / 12.0
    K_put = current_price_usd  # ATM put
    K_call = current_price_usd * (1 + otm_call_pct)  # OTM call

    put_premium = black_scholes_put(current_price_usd, K_put, T, risk_free_rate, vol_63d)
    call_premium = 0.0

    if strategy_code == 'collar':
        call_premium = black_scholes_call(
            current_price_usd, K_call, T, risk_free_rate, vol_63d
        )

    net_premium = put_premium - call_premium  # call received offsets put paid
    total_premium = net_premium * quantity

    # Contracts = ceil(quantity / 100); US options are 100 shares each
    num_contracts = max(1, int(math.ceil(quantity / 100)))
    commission = num_contracts * 1.50 * (2 if strategy_code == 'collar' else 1)
    bid_ask = current_price_usd * quantity * 0.004  # 0.4% of notional

    return {
        'put_premium_per_share': put_premium,
        'call_premium_per_share': call_premium,
        'net_premium_per_share': net_premium,
        'total_premium_usd': total_premium,
        'commission_usd': commission,
        'bid_ask_usd': bid_ask,
        'total_cost_usd': max(0, total_premium) + commission + bid_ask,
    }
