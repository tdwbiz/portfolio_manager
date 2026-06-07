"""
Stress test engine for portfolio risk analysis.

Applies instantaneous shocks to current positions and computes
per-position and portfolio-level losses.

Supports:
- Named scenarios from rm_historical_scenario (asset_class returns)
- Custom scenarios from rm_custom_stress_scenario (symbol-level shocks)
- Optional strategy overlay (trailing stop, protective put, collar)
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_portfolio_stress_test(
    positions: list[dict[str, Any]],
    shock_map: dict[str, float],
    current_prices: dict[str, float],
    fx_rate: float,
    strategy_overlay: dict[str, Any] | None = None,
    security_universe: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply instantaneous shocks to current positions.

    Args:
        positions: List of position dicts from vw_current_portfolio.
            Each dict should have: symbol, quantity, currency, account_category,
            account_id, owner, asset_class (optional, from security_universe).
        shock_map: {asset_class_or_symbol: shock_fraction}.
            E.g. {"SOXX": -0.30, "common_stock_semi": -0.28, "etf_equity": -0.15}
        current_prices: {symbol: current_price_usd}.
        fx_rate: CAD/USD exchange rate (e.g. 1.384).
        strategy_overlay: Optional dict with strategy params for hedged comparison.
        security_universe: Optional {symbol: {asset_class, sector}} for shock resolution.

    Returns:
        Dict matching rm_simulation_result stress fields:
            stress_loss_unhedged_usd, stress_loss_unhedged_pct,
            stress_loss_hedged_usd, stress_loss_hedged_pct,
            stress_per_position_json
    """
    total_mv_usd = 0.0
    total_loss_usd = 0.0
    total_loss_hedged_usd = 0.0
    per_position: list[dict[str, Any]] = []

    for pos in positions:
        symbol = pos["symbol"]
        quantity = float(pos["quantity"])
        currency = pos.get("currency", "USD")
        account_id = pos.get("account_id", "")
        owner = pos.get("owner", "")

        # Get current price in USD
        price_usd = current_prices.get(symbol, 0.0)
        if price_usd <= 0:
            logger.warning("No price found for %s; skipping", symbol)
            continue

        # Convert to USD if CAD
        if currency.upper() == "CAD":
            mv_usd = quantity * price_usd * fx_rate
        else:
            mv_usd = quantity * price_usd

        total_mv_usd += mv_usd

        # Resolve shock for this position
        asset_class = pos.get("asset_class", "")
        sector = pos.get("sector", "")
        if security_universe and symbol in security_universe:
            asset_class = security_universe[symbol].get("asset_class", asset_class)
            sector = security_universe[symbol].get("sector", sector)

        shock_pct = resolve_shock_for_position(symbol, asset_class, sector, shock_map)

        # Compute loss
        shocked_mv = mv_usd * (1 + shock_pct)
        loss_usd = shocked_mv - mv_usd  # negative = loss

        # Compute hedged loss if strategy overlay provided
        if strategy_overlay:
            loss_hedged_usd = _apply_hedge_to_position(
                symbol, quantity, price_usd, shock_pct,
                strategy_overlay,
            )
        else:
            loss_hedged_usd = loss_usd  # same as unhedged by default

        total_loss_usd += loss_usd
        total_loss_hedged_usd += loss_hedged_usd

        per_position.append({
            "symbol": symbol,
            "account": account_id,
            "quantity": quantity,
            "price_usd": round(price_usd, 4),
            "mv_usd": round(mv_usd, 2),
            "shock_pct": round(shock_pct, 4),
            "loss_usd": round(loss_usd, 2),
            "loss_pct": round(shock_pct, 4),
            "loss_hedged_usd": round(loss_hedged_usd, 2),
        })

    # Compute portfolio-level percentages
    stress_loss_unhedged_pct = total_loss_usd / total_mv_usd if total_mv_usd > 0 else 0.0
    stress_loss_hedged_pct = total_loss_hedged_usd / total_mv_usd if total_mv_usd > 0 else 0.0

    return {
        "stress_loss_unhedged_usd": round(total_loss_usd, 2),
        "stress_loss_unhedged_pct": round(stress_loss_unhedged_pct, 4),
        "stress_loss_hedged_usd": round(total_loss_hedged_usd, 2),
        "stress_loss_hedged_pct": round(stress_loss_hedged_pct, 4),
        "stress_per_position_json": json.dumps(per_position, indent=2),
        "total_mv_usd": round(total_mv_usd, 2),
        "n_positions": len(per_position),
    }


def resolve_shock_for_position(
    symbol: str,
    asset_class: str,
    sector: str,
    shock_map: dict[str, float],
) -> float:
    """Resolve the applicable shock fraction for a position.

    Priority:
    1. Exact symbol match in shock_map
    2. Asset class + sector composite key (e.g. 'common_stock_semi')
    3. Asset class alone (e.g. 'etf_equity')
    4. Default: 0.0 (no shock)

    Args:
        symbol: Ticker symbol.
        asset_class: Asset class from rm_security_universe.
        sector: Sector from rm_security_universe.
        shock_map: {key: shock_fraction}.

    Returns:
        Shock fraction (e.g. -0.30 for -30%).
    """
    # 1. Exact symbol match
    if symbol in shock_map:
        return shock_map[symbol]

    # 2. Asset class + sector composite key
    if sector:
        # Normalize sector to match common patterns
        # e.g. "Technology/Semiconductor" -> "common_stock_semi"
        sector_key = _sector_to_composite_key(asset_class, sector)
        if sector_key and sector_key in shock_map:
            return shock_map[sector_key]

    # 3. Asset class alone
    if asset_class in shock_map:
        return shock_map[asset_class]

    # 4. Default: no shock
    return 0.0


def _sector_to_composite_key(asset_class: str, sector: str) -> str | None:
    """Convert asset_class + sector to a composite key for shock lookup.

    Examples:
        ('common_stock', 'Technology/Semiconductor') -> 'common_stock_semi'
        ('etf_equity', 'Canadian Equity Broad Market') -> 'etf_equity_ca'
        ('common_stock', 'Technology') -> 'common_stock_tech'

    Args:
        asset_class: e.g. 'common_stock', 'etf_equity'.
        sector: e.g. 'Technology/Semiconductor'.

    Returns:
        Composite key or None if no mapping found.
    """
    if not asset_class or not sector:
        return None

    # Normalise
    ac = asset_class.lower().replace(" ", "_")
    # Extract key sector word (last part after /)
    sector_parts = sector.lower().split("/")
    sector_word = sector_parts[-1].replace(" ", "_") if sector_parts else ""

    # Build composite
    if sector_word:
        return f"{ac}_{sector_word}"

    return ac


def _apply_hedge_to_position(
    symbol: str,
    quantity: float,
    price_usd: float,
    shock_pct: float,
    strategy_overlay: dict[str, Any],
) -> float:
    """Apply simplified hedge payoff at shock magnitude.

    Args:
        symbol: Ticker.
        quantity: Number of units.
        price_usd: Current price.
        shock_pct: Shock fraction applied to position.
        strategy_overlay: Dict with 'strategy_code' and strategy params.

    Returns:
        Hedged loss in USD.
    """
    strategy_code = strategy_overlay.get("strategy_code", "")
    put_strike_pct = strategy_overlay.get("put_strike_pct", 0.85)
    trail_pct = strategy_overlay.get("trail_pct", 0.10)

    if strategy_code == "trailing_stop":
        # If shock exceeds trail, stop triggers: loss = trail_pct * mv
        mv_usd = quantity * price_usd
        if abs(shock_pct) > trail_pct and shock_pct < 0:
            return -trail_pct * mv_usd  # capped at trail loss
        else:
            return shock_pct * mv_usd  # no trigger

    elif strategy_code in ("protective_put", "collar"):
        mv_usd = quantity * price_usd
        put_strike = price_usd * put_strike_pct
        shocked_price = price_usd * (1 + shock_pct)

        # Put payoff: max(0, put_strike - shocked_price)
        put_payoff_per_unit = max(0, put_strike - shocked_price)
        put_payoff_total = put_payoff_per_unit * quantity

        # Hedged loss = original loss + put payoff
        original_loss = shock_pct * mv_usd
        return original_loss + put_payoff_total

    elif strategy_code == "inverse_etf":
        # Inverse ETF: gain = -shock * leverage * mv
        leverage = strategy_overlay.get("leverage_factor", 1.0)
        mv_usd = quantity * price_usd
        hedge_gain = -shock_pct * leverage * mv_usd
        return shock_pct * mv_usd + hedge_gain  # net = original + hedge

    # Unknown strategy: return unhedged loss
    return shock_pct * quantity * price_usd
