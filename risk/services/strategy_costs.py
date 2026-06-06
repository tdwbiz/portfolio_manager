"""
Strategy cost estimation and ranking for M4 optimisation engine.

Handles cost estimation by strategy type (options, trailing stops, inverse ETFs,
sector rotation, partial liquidation, cash raise, covered calls), tax impact
calculation, and cost-per-VaR-reduction ranking.

Dependencies: risk.models, risk.services.option_pricing
"""

from decimal import Decimal

from risk.models import OwnerConfig, StrategyTemplate
from risk.services.option_pricing import estimate_option_cost


def compute_ranking_metric(
    strategy_cost_usd: float,
    tax_cost_usd: float,
    var_reduction_usd: float,
    is_taxable_account: bool,
    tax_penalty_multiplier: float = 1.0,
) -> float:
    """
    Compute the cost-per-VaR-reduction metric used for ranking strategies.

    For taxable accounts:
        effective_cost = strategy_cost_usd + (tax_cost_usd * tax_penalty_multiplier)
    For non-taxable:
        effective_cost = strategy_cost_usd

    cost_per_var_reduction = effective_cost / var_reduction_usd

    Lower is better — more risk reduction per effective dollar.

    Parameters
    ----------
    strategy_cost_usd : float
        Total estimated cost to implement the strategy (premium + friction).
    tax_cost_usd : float
        Estimated capital gains tax if the strategy triggers a taxable sale.
    var_reduction_usd : float
        Estimated reduction in 1-day 95% VaR in USD.
    is_taxable_account : bool
        True if the account is a taxable account (e.g. Investment, Investment Joint).
    tax_penalty_multiplier : float, optional
        Multiplier applied to tax cost in ranking. Default 1.0 (tax cost counted
        at face value). Range: 0.5 (halved) to 2.0 (doubled) — configurable
        via rm_scoring_config key 'tax_penalty_multiplier'.

    Returns
    -------
    float
        Cost per dollar of VaR reduction. float('inf') if var_reduction is zero.
    """
    if var_reduction_usd <= 0:
        return float('inf')

    if is_taxable_account:
        effective_cost = strategy_cost_usd + (tax_cost_usd * tax_penalty_multiplier)
    else:
        effective_cost = strategy_cost_usd

    return effective_cost / var_reduction_usd


def build_tax_impact_note(
    strategy_code: str,
    account_category: str,
    unrealised_gain_usd: float,
    tax_cost_usd: float,
    symbol: str,
    owner: str,
    fx_rate: float,
) -> str:
    """
    Generate plain English tax impact note for a recommendation.

    Parameters
    ----------
    strategy_code : str
        Strategy template code (e.g. 'trailing_stop', 'collar', 'protective_put').
    account_category : str
        Account category (e.g. 'TFSA', 'Investment', 'RRSP', 'LIF').
    unrealised_gain_usd : float
        Unrealised gain in USD for the position.
    tax_cost_usd : float
        Estimated capital gains tax in USD.
    symbol : str
        Security symbol (e.g. 'AAPL', 'VFV').
    owner : str
        Owner name (e.g. 'TOM', 'SUSAN').
    fx_rate : float
        Current USD/CAD exchange rate.

    Returns
    -------
    str
        Plain English tax impact description.

    Examples
    --------
    >>> build_tax_impact_note('trailing_stop', 'TFSA', 5000, 0, 'IBIT', 'TOM', 1.384)
    'No tax consequence — TFSA_TOM account.'
    >>> build_tax_impact_note('collar', 'Investment', 200000, 0, 'AAPL', 'SUSAN', 1.384)
    'Collar: no taxable event unless AAPL is called away at the call strike.'
    """
    # Tax-free accounts
    tax_free_categories = ('TFSA', 'RRSP', 'RRIF', 'LIF')
    if account_category in tax_free_categories:
        account_label = f"{account_category}_{owner}"
        return f"No tax consequence — {account_label} account."

    # Taxable accounts
    cad_gain = unrealised_gain_usd * fx_rate
    cad_tax = tax_cost_usd * fx_rate

    if strategy_code in ('protective_put', 'collar'):
        if strategy_code == 'collar':
            return (
                f"Collar: no taxable event unless {symbol} is called away at the call strike. "
                "Premium cost is not a taxable event."
            )
        return f"Protective put: no taxable event. Premium paid is not deductible."

    if strategy_code == 'covered_call':
        return (
            f"Covered call: call premium received is taxable income in year received. "
            f"If {symbol} is called away at strike, capital gain will be calculated "
            f"using strike price as proceeds."
        )

    if strategy_code == 'trailing_stop':
        if unrealised_gain_usd > 0:
            return (
                f"Trailing stop: if triggered, will crystallise approximately "
                f"${unrealised_gain_usd:,.0f} USD ({cad_gain:,.0f} CAD) in capital gains. "
                f"Estimated tax: ~${tax_cost_usd:,.0f} USD (~${cad_tax:,.0f} CAD)."
            )
        return "Trailing stop: no unrealised gain — no tax consequence on trigger."

    if strategy_code in ('partial_liquidation', 'sector_rotation', 'cash_raise'):
        if unrealised_gain_usd > 0:
            gain_25 = unrealised_gain_usd * 0.25
            tax_25 = tax_cost_usd
            return (
                f"Partial liquidation of 25% of position: estimated capital gain "
                f"${gain_25:,.0f} USD. Estimated tax: ~${tax_25:,.0f} USD."
            )
        return "Partial liquidation: no unrealised gain — no tax consequence."

    if strategy_code == 'inverse_etf':
        return (
            f"Inverse ETF: no sale of underlying required. Gain on inverse ETF itself "
            f"will be taxed when position is closed."
        )

    return f"Strategy cost models tax impact. Review before actioning."


def estimate_strategy_cost(
    strategy_template: StrategyTemplate,
    position: dict,
    security_metrics: dict,
    risk_free_rate: float,
    fx_rate: float,
    owner_config: OwnerConfig,
    scoring_config: dict,
) -> dict:
    """
    Estimate the full cost of implementing a strategy template on a position.

    Parameters
    ----------
    strategy_template : StrategyTemplate
        The strategy template to estimate.
    position : dict
        Position details with keys: symbol, quantity, current_price_usd,
        cost_basis_per_unit_usd, unrealised_gain_usd, account_category,
        owner, currency.
    security_metrics : dict
        Security metrics with keys: vol_63d, var_parametric_95, beta_spy.
    risk_free_rate : float
        Current risk-free rate (annual).
    fx_rate : float
        Current USD/CAD exchange rate.
    owner_config : OwnerConfig
        Owner configuration with tax rates and preferences.
    scoring_config : dict
        Scoring configuration with key 'tax_penalty_multiplier'.

    Returns
    -------
    dict
        {
            'strategy_cost_usd': float,
            'tax_cost_usd': float,
            'tax_impact_note': str,
            'is_tax_triggering': bool,
            'total_effective_cost_usd': float,
        }
    """
    symbol = position['symbol']
    quantity = position['quantity']
    current_price = position['current_price_usd']
    unrealised_gain = position['unrealised_gain_usd']
    account_category = position['account_category']
    owner = position['owner']
    vol_63d = security_metrics.get('vol_63d', 0.30) or 0.30
    is_taxable = account_category in ('Investment', 'Investment Joint')

    marginal_tax_rate = float(owner_config.marginal_tax_rate_ca)
    inclusion_rate = float(owner_config.capital_gains_inclusion_rate)
    tax_penalty_multiplier = float(scoring_config.get('tax_penalty_multiplier', 1.0))

    strategy_code = strategy_template.code
    position_market_value = current_price * quantity

    # --- Options-based strategies ---
    if strategy_code in ('protective_put', 'collar', 'covered_call'):
        option_result = estimate_option_cost(
            strategy_code=strategy_code,
            current_price_usd=current_price,
            quantity=quantity,
            vol_63d=vol_63d,
            risk_free_rate=risk_free_rate,
            expiry_months=int(
                owner_config.preferences_json.get('max_option_expiry_months', 6)
                if isinstance(owner_config.preferences_json, str)
                else 6
            ),
        )
        strategy_cost = option_result['total_cost_usd']
        tax_cost = 0.0  # No sale required

        if strategy_code == 'covered_call':
            is_tax_triggering = True  # Premium is taxable income
            tax_note = build_tax_impact_note(
                strategy_code, account_category, unrealised_gain,
                tax_cost, symbol, owner, fx_rate,
            )
        else:
            is_tax_triggering = False
            tax_note = build_tax_impact_note(
                strategy_code, account_category, unrealised_gain,
                tax_cost, symbol, owner, fx_rate,
            )

        return {
            'strategy_cost_usd': strategy_cost,
            'tax_cost_usd': tax_cost,
            'tax_impact_note': tax_note,
            'is_tax_triggering': is_tax_triggering,
        }

    # --- Trailing stop ---
    if strategy_code in ('trailing_stop', 'trailing_stop_cef', 'trailing_stop_registered'):
        # Commission + bid-ask friction only (no premium)
        commission = 1.00  # $1.00 per trade
        bid_ask = position_market_value * 0.001  # 0.1% of notional
        strategy_cost = commission + bid_ask

        if is_taxable and unrealised_gain > 0:
            tax_cost = unrealised_gain * inclusion_rate * marginal_tax_rate
            is_tax_triggering = True  # Conditionally triggering
            tax_note = build_tax_impact_note(
                strategy_code, account_category, unrealised_gain,
                tax_cost, symbol, owner, fx_rate,
            )
        else:
            tax_cost = 0.0
            is_tax_triggering = False
            tax_note = build_tax_impact_note(
                strategy_code, account_category, unrealised_gain,
                tax_cost, symbol, owner, fx_rate,
            )

        return {
            'strategy_cost_usd': strategy_cost,
            'tax_cost_usd': tax_cost,
            'tax_impact_note': tax_note,
            'is_tax_triggering': is_tax_triggering,
        }

    # --- Inverse ETF ---
    if strategy_code == 'inverse_etf':
        # Annual decay cost for leveraged ETFs (5% annual decay)
        annual_decay_rate = 0.05
        hedge_ratio = 0.5  # assume 50% hedge
        monthly_decay = position_market_value * annual_decay_rate * hedge_ratio / 12
        strategy_cost = monthly_decay
        tax_cost = 0.0  # No sale of underlying
        is_tax_triggering = False  # True at future close, not at purchase
        tax_note = build_tax_impact_note(
            strategy_code, account_category, unrealised_gain,
            tax_cost, symbol, owner, fx_rate,
        )

        return {
            'strategy_cost_usd': strategy_cost,
            'tax_cost_usd': tax_cost,
            'tax_impact_note': tax_note,
            'is_tax_triggering': is_tax_triggering,
        }

    # --- Sector rotation / partial liquidation / cash raise ---
    if strategy_code in ('sector_rotation', 'partial_liquidation', 'cash_raise'):
        # Commission + bid-ask friction only
        commission = 1.00
        bid_ask = position_market_value * 0.001
        strategy_cost = commission + bid_ask

        if is_taxable and unrealised_gain > 0:
            # Assume 25% of position is rotated/liquidated
            tax_cost = unrealised_gain * 0.25 * inclusion_rate * marginal_tax_rate
            is_tax_triggering = True
            tax_note = build_tax_impact_note(
                strategy_code, account_category, unrealised_gain,
                tax_cost, symbol, owner, fx_rate,
            )
        else:
            tax_cost = 0.0
            is_tax_triggering = False
            tax_note = build_tax_impact_note(
                strategy_code, account_category, unrealised_gain,
                tax_cost, symbol, owner, fx_rate,
            )

        return {
            'strategy_cost_usd': strategy_cost,
            'tax_cost_usd': tax_cost,
            'tax_impact_note': tax_note,
            'is_tax_triggering': is_tax_triggering,
        }

    # --- Diversification (catch-all) ---
    strategy_cost = 0.0
    tax_cost = 0.0
    is_tax_triggering = False
    tax_note = "Diversification strategy — review manually for tax implications."

    return {
        'strategy_cost_usd': strategy_cost,
        'tax_cost_usd': tax_cost,
        'tax_impact_note': tax_note,
        'is_tax_triggering': is_tax_triggering,
    }
