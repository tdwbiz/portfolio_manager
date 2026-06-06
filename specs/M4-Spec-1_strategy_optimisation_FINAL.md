# Feature Spec: M4-Spec-1 — Strategy Optimisation Engine

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m4-optimisation

Architecture ref: PM_PhaseB_Architecture_Final_v2.0.md
Depends on: M3-Spec-1 fully implemented and seeded — rm_instrument_type, rm_strategy_template, rm_strategy_instrument_link all populated; M2-Spec-2 urgency scores available; M1-Spec-1 security metrics available

---

## Context

M4 answers the question: "Given this position, in this account, at this urgency level, what should I actually do — and what will it cost?"

It reads the instrument catalogue from M3, the urgency scores from M2, the risk metrics from M1, and the position data from core_position_history, then produces a ranked list of actionable strategy recommendations with concrete cost estimates, risk reduction estimates, and explicit tax impact notes.

This is the most financially consequential module in the system. Every output directly informs a potential real-money decision. Two principles dominate the design:

1. Tax-first ranking in taxable accounts. For positions like AAPL in IB_SUSAN ($200K+ unrealised gain) or VFV in TAXABLE_TOM_SUSAN ($331K+ unrealised gain), the tax cost of any strategy that requires selling is so large that it must be modelled explicitly and reflected in the ranking. A collar that costs $2K in premium and crystallises $0 in tax must rank above a trailing stop that costs $0 in premium but crystallises $150K in tax if triggered.

2. No guessing on tax rates. Marginal tax rates are user-supplied inputs stored in rm_owner_config. The system models the tax cost using those rates — it does not attempt to calculate Canadian capital gains tax from first principles.

**Key positions that M4 must handle correctly (June 2, 2026):**

| Position | Account | UGL (USD) | Urgency expected | Primary strategy |
|---|---|---|---|---|
| AAPL | IB_SUSAN | ~$200K | High or Critical | Collar (no sale required) |
| VFV | TAXABLE_TOM_SUSAN | ~$331K | Medium to High | Trailing stop (models tax on trigger) |
| VDY | TAXABLE_TOM_SUSAN | ~$195K | Medium | Trailing stop or covered call |
| IBIT | TFSA_TOM | negative | High | Trailing stop (no tax) |
| ETHA | TFSA_TOM | negative | High | Trailing stop (no tax) |
| ADX | LIF_TOM | ~$7.9K | Medium | Trailing stop only (CEF, LIF) |
| SOXX | TFSA_TOM | ~$9.9K | Medium | Inverse ETF or trailing stop |
| QQQ | IB_TOM | ~$11.8K | Medium | Inverse ETF (PSQ) or trailing stop |

**Stack:**
- Python 3.12 / Django 5.1
- MSSQL 2025 (dev: PortfolioDEV)
- scipy (Black-Scholes for option pricing)
- Project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. New Database Tables

#### 1.1 rm_owner_config

Stores owner-level tax rates and preferences. These are user inputs — not computed by the system.

```python
class OwnerConfig(models.Model):
    """
    Owner-level configuration for tax rates and strategy preferences.
    Populated manually via Django admin — NOT computed by the system.
    """
    owner = models.CharField(max_length=50, unique=True)
    # 'TOM' or 'SUSAN'

    marginal_tax_rate_ca = models.DecimalField(max_digits=5, decimal_places=4)
    # Canadian marginal income tax rate (federal + provincial combined).
    # Used for: capital gains tax = 0.5 * unrealised_gain * marginal_tax_rate_ca
    # (Canada taxes 50% of capital gains as income for gains up to $250K annually;
    #  66.67% inclusion for gains above $250K — store both rates if needed)
    # Example: 0.4326 = 43.26% combined marginal rate

    capital_gains_inclusion_rate = models.DecimalField(max_digits=4, decimal_places=4, default=0.5000)
    # Fraction of capital gain included in income for tax purposes.
    # Canada 2024+: 0.5000 for first $250K annually; 0.6667 above $250K.
    # Default 0.5000. Set to 0.6667 if owner's annual gains exceed $250K threshold.

    cost_tolerance_pct = models.DecimalField(max_digits=5, decimal_places=4, default=0.0200)
    # Maximum acceptable hedging cost as a fraction of position market value.
    # Example: 0.0200 = willing to spend up to 2% of position value on hedging annually.
    # M4 uses this to filter out strategies that are too expensive.

    preferences_json = models.TextField(default='{}')
    # JSON blob for any additional owner preferences.
    # E.g. {"max_option_expiry_months": 6, "preferred_trail_pct": 0.10,
    #        "avoid_inverse_etf": false, "max_annual_gain_realisation_usd": 50000}

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=100, blank=True)
    # Who last updated these rates — for audit trail.

    class Meta:
        db_table = 'rm_owner_config'
```

**Seed data for rm_owner_config (placeholder rates — owner must review and update):**
```python
OWNER_CONFIG_SEED = [
    {
        'owner': 'TOM',
        'marginal_tax_rate_ca': 0.4326,  # Placeholder — must be updated by owner
        'capital_gains_inclusion_rate': 0.5000,
        'cost_tolerance_pct': 0.0200,
        'preferences_json': '{"max_option_expiry_months": 6, "preferred_trail_pct": 0.10}',
    },
    {
        'owner': 'SUSAN',
        'marginal_tax_rate_ca': 0.4326,  # Placeholder — must be updated by owner
        'capital_gains_inclusion_rate': 0.5000,
        'cost_tolerance_pct': 0.0200,
        'preferences_json': '{"max_option_expiry_months": 6, "preferred_trail_pct": 0.10}',
    },
]
```

#### 1.2 rm_strategy_recommendation

One row per recommendation per position per calculation date.

```python
class StrategyRecommendation(models.Model):
    """
    A ranked strategy recommendation for a specific position in a specific account.
    Produced by calc_strategy_recommendations management command.
    All monetary values in USD.
    """
    # --- Entity identification ---
    symbol = models.CharField(max_length=20)
    account_id = models.CharField(max_length=50)
    # core_account.account_id string (e.g. 'IB_SUSAN')
    owner = models.CharField(max_length=50)
    calc_date = models.DateField()

    # --- Linked objects ---
    strategy_template = models.ForeignKey(
        'StrategyTemplate', on_delete=models.PROTECT, related_name='recommendations'
    )
    rank = models.IntegerField()
    # 1 = best recommendation for this position on this date. Lower is better.

    # --- Input context (snapshot at time of calculation) ---
    urgency_level = models.CharField(max_length=10)
    # Urgency level that triggered this recommendation run
    urgency_score = models.DecimalField(max_digits=6, decimal_places=2)
    regime_quadrant = models.CharField(max_length=2)
    position_quantity = models.DecimalField(max_digits=12, decimal_places=4)
    position_market_value_usd = models.DecimalField(max_digits=15, decimal_places=2)
    position_unrealised_gain_usd = models.DecimalField(max_digits=15, decimal_places=2)
    cost_basis_per_unit_usd = models.DecimalField(max_digits=12, decimal_places=4)
    current_price_usd = models.DecimalField(max_digits=12, decimal_places=4)
    vol_63d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # 63-day volatility used for option pricing

    # --- Cost estimates ---
    estimated_strategy_cost_usd = models.DecimalField(max_digits=12, decimal_places=2)
    # Total estimated cost to implement this strategy:
    # For options: Black-Scholes premium + commission + bid-ask
    # For trailing stop: commission only (no premium)
    # For inverse ETF: estimated annual decay cost
    estimated_strategy_cost_pct = models.DecimalField(max_digits=8, decimal_places=4)
    # estimated_strategy_cost_usd / position_market_value_usd

    tax_cost_estimate_usd = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    # Estimated capital gains tax if this strategy triggers a taxable sale.
    # = unrealised_gain_usd_sold * inclusion_rate * marginal_tax_rate
    # 0 for strategies that do not require a sale (protective_put, collar, inverse_etf)
    # 0 for tax-free accounts (TFSA, RRSP, RRIF)

    tax_impact_note = models.CharField(max_length=300, blank=True)
    # Plain English tax impact description.
    # E.g. "Triggering this stop would crystallise ~$331K USD gain. Estimated tax: ~$71K USD."
    # E.g. "No taxable event unless call is exercised (VFV called away at strike price)."
    # E.g. "No tax consequence — TFSA account."

    is_tax_triggering = models.BooleanField(default=False)
    # True if this recommendation will definitely trigger a taxable event on execution.
    # Drives M8 tax acknowledgement workflow.

    # --- Risk reduction estimates ---
    estimated_var_reduction_usd = models.DecimalField(max_digits=12, decimal_places=2)
    # Estimated reduction in 1-day 95% VaR in USD if this strategy is applied.
    estimated_var_reduction_pct = models.DecimalField(max_digits=8, decimal_places=4)
    # As fraction of position_market_value_usd

    estimated_urgency_delta = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    # Estimated change in urgency score if strategy is applied. Negative = score decreases.

    # --- Ranking metric ---
    cost_per_var_reduction_usd = models.DecimalField(max_digits=12, decimal_places=4, null=True)
    # (estimated_strategy_cost_usd + tax_cost_estimate_usd * tax_penalty_multiplier)
    #   / estimated_var_reduction_usd
    # Lower is better — more risk reduction per dollar spent.
    # NULL if estimated_var_reduction_usd is zero.

    # --- Constraint audit ---
    constraints_applied_json = models.TextField()
    # JSON list of constraints that filtered the candidate set.
    # E.g. ["options_not_available: removed protective_put, collar, covered_call",
    #        "cef: removed all options templates",
    #        "lif_restriction: removed partial_liquidation"]
    # Stored for M8 explainability.

    # --- Status ---
    status = models.CharField(max_length=20, default='active')
    # 'active': current recommendation
    # 'actioned': user has marked this as implemented
    # 'superseded': a newer recommendation replaced this one
    # 'acknowledged': user acknowledged but did not act

    actioned_at = models.DateTimeField(null=True, blank=True)
    actioned_by = models.CharField(max_length=100, blank=True)

    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_strategy_recommendation'
        indexes = [
            models.Index(fields=['symbol', 'account_id', 'calc_date']),
            models.Index(fields=['calc_date', 'urgency_level']),
            models.Index(fields=['status', 'calc_date']),
            models.Index(fields=['is_tax_triggering', 'status']),
        ]
```

---

### 2. Management Command

Create `risk/management/commands/calc_strategy_recommendations.py`.

```
python manage.py calc_strategy_recommendations [--date YYYY-MM-DD] [--account ACC] [--symbol SYM] [--min-urgency Medium]
```

- --date: calculation date (default: today)
- --account: run for one account only (default: all accounts where exclude_from_risk=False)
- --symbol: run for one symbol only within the specified account
- --min-urgency: only process positions at this urgency level or above (default: Medium)

**Execution order:**
```
1. Load calc_date, current regime from rm_regime_state
2. Load urgency scores from rm_urgency_score for calc_date
3. Load security metrics from rm_security_risk_metrics for calc_date
4. Load FX rates from rm_fx_rates for calc_date
5. Load owner configs from rm_owner_config
6. Load scoring config from rm_scoring_config
7. For each position in vw_current_portfolio (respects exclude_from_risk):
   a. Filter to positions where urgency_level >= --min-urgency
   b. Load position details from core_position_history
   c. Run get_eligible_templates() from M3 catalogue
   d. For each eligible template: compute cost estimate (see §3)
   e. For each eligible template: compute VaR reduction estimate (see §4)
   f. Compute cost_per_var_reduction ranking metric (see §5)
   g. Apply tax penalty multiplier for taxable accounts (see §6)
   h. Sort by cost_per_var_reduction ascending (best first)
   i. Mark previous recommendations for this position as 'superseded'
   j. INSERT new recommendations with rank 1, 2, 3... (store all candidates, not just top 3)
8. Print summary: positions processed, total recommendations, any positions skipped
```

---

### 3. Option Premium Estimation (Black-Scholes)

Create `risk/services/option_pricing.py`.

```python
import numpy as np
from scipy.stats import norm


def black_scholes_put(
    S: float,   # Current stock price (USD)
    K: float,   # Strike price (USD) — use S for ATM
    T: float,   # Time to expiry in years (e.g. 0.5 for 6 months)
    r: float,   # Risk-free rate (annual; use SGOV yield from rm_economic_indicators)
    sigma: float  # Annualised volatility (from rm_volatility_summary, 63d window)
) -> float:
    """
    Black-Scholes price for a European put option.

    d1 = (ln(S/K) + (r + sigma^2/2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    put = K * exp(-r*T) * N(-d2) - S * N(-d1)

    Returns put premium in USD per share.
    Multiply by 100 to get per-contract value (standard US option contract = 100 shares).
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    put_price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return max(put_price, 0.0)


def black_scholes_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Black-Scholes price for a European call option.

    d1 = (ln(S/K) + (r + sigma^2/2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    call = S * N(d1) - K * exp(-r*T) * N(d2)
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    call_price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return max(call_price, 0.0)


def estimate_option_cost(
    strategy_code: str,
    current_price_usd: float,
    quantity: float,
    vol_63d: float,
    risk_free_rate: float,
    expiry_months: int = 6,
    otm_call_pct: float = 0.12
) -> dict:
    """
    Estimate total option strategy cost for a given position.

    strategy_code: 'protective_put', 'collar', 'covered_call'
    current_price_usd: price per unit in USD
    quantity: number of shares/units held
    vol_63d: 63-day annualised volatility from rm_volatility_summary
    risk_free_rate: current SGOV yield or fallback rate (annual)
    expiry_months: option expiry horizon (default 6 months)
    otm_call_pct: for collar — call strike is this % above current price (default 12%)

    Returns dict:
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
        call_premium = black_scholes_call(current_price_usd, K_call, T, risk_free_rate, vol_63d)

    net_premium = put_premium - call_premium  # call received offsets put paid
    total_premium = net_premium * quantity

    # Contracts = ceil(quantity / 100); US options are 100 shares each
    num_contracts = max(1, int(quantity / 100))
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
```

---

### 4. Cost Estimation by Strategy Type

Create `risk/services/strategy_costs.py`.

```python
def estimate_strategy_cost(
    strategy_template: StrategyTemplate,
    position: dict,
    security_metrics: dict,
    risk_free_rate: float,
    fx_rate: float,
    owner_config: OwnerConfig,
    scoring_config: dict
) -> dict:
    """
    Estimate the full cost of implementing a strategy template on a position.

    position keys: symbol, quantity, current_price_usd, cost_basis_per_unit_usd,
                   unrealised_gain_usd, account_category, owner, currency

    Returns dict:
    {
        'strategy_cost_usd': float,     # Premium + friction
        'tax_cost_usd': float,          # Capital gains tax if sale required
        'tax_impact_note': str,
        'is_tax_triggering': bool,
        'total_effective_cost_usd': float,  # strategy_cost + tax_cost * penalty_multiplier
    }

    Strategy-specific cost logic:

    protective_put / collar:
        Use estimate_option_cost() with vol_63d from security_metrics.
        tax_cost_usd = 0 (no sale required).
        is_tax_triggering = False.

    trailing_stop:
        strategy_cost_usd = commission ($1.00) + bid_ask (0.1% of notional).
        If account_category is taxable AND unrealised_gain_usd > 0:
            tax_cost_usd = unrealised_gain_usd * inclusion_rate * marginal_tax_rate_ca
            (models the tax if the stop is triggered — conservative; stop may not trigger)
            tax_impact_note = "If stop is triggered: estimated tax on full position ~$X USD"
        is_tax_triggering = True for taxable accounts (conditionally).

    inverse_etf:
        strategy_cost_usd = position_market_value_usd * hedge_ratio * annual_decay_rate / 12
        (monthly decay cost for short-term hold)
        where annual_decay_rate = 0.05 (5% annual decay for 3x leveraged ETFs)
        tax_cost_usd = 0 (no sale of underlying; gain on ETF itself taxed when closed)
        is_tax_triggering = False at purchase; True at future close.

    sector_rotation / partial_liquidation / cash_raise:
        strategy_cost_usd = commission + bid_ask (friction only).
        tax_cost_usd:
            if account_category in ('Investment', 'Investment Joint'):
                fraction_sold = 0.25  # assume 25% of position is rotated/liquidated
                gain_realised = unrealised_gain_usd * fraction_sold
                tax_cost_usd = gain_realised * inclusion_rate * marginal_tax_rate_ca
            else:
                tax_cost_usd = 0
        is_tax_triggering = True for taxable accounts.

    covered_call:
        strategy_cost_usd = 0 (premium received; net income, not cost)
        Actually: strategy_cost_usd = -call_premium * quantity (negative = income)
        For ranking purposes, use max(0, strategy_cost_usd) = 0.
        tax_impact_note = "Call premium received is taxable income in year received."
        is_tax_triggering = True (premium income); is NOT sale-triggering unless called away.
    """
```

---

### 5. VaR Reduction Estimation

Create `risk/services/var_reduction.py`.

```python
def estimate_var_reduction(
    strategy_template: StrategyTemplate,
    position_market_value_usd: float,
    var_parametric_95: float,
    beta_spy: float,
    current_price_usd: float,
    stop_trail_pct: float = 0.10
) -> dict:
    """
    Estimate the reduction in 1-day 95% VaR if this strategy is applied.

    Returns dict:
    {
        'var_reduction_usd': float,
        'var_reduction_pct_of_mv': float,
        'method': str,  # description of estimation method used
    }

    Strategy-specific VaR reduction logic:

    protective_put:
        An ATM put has delta ≈ -0.5.
        The put gains ~$0.50 for every $1.00 the stock falls.
        VaR reduction = position_market_value_usd * var_parametric_95 * 0.5
        (approximate: half of the 1-day tail loss is offset by the put)

    collar:
        Same as protective_put: put provides the downside protection.
        Delta of put ≈ -0.5. VaR reduction same as standalone put.
        Note: upside is capped at the call strike — this is not a VaR concern.

    trailing_stop:
        If stop is at (1 - stop_trail_pct) * current_price:
        Maximum loss after stop is set = stop_trail_pct * position_market_value_usd
        (theoretical max; actual execution may be worse in fast markets)
        VaR_after_stop = min(var_parametric_95 * position_mv, stop_trail_pct * position_mv)
        VaR_reduction = max(0, var_parametric_95 * position_mv - VaR_after_stop)
        Note: stop does not prevent gap-down losses; document this limitation.

    inverse_etf:
        hedge_ratio = 0.5  # assume 50% hedge (half position notional in inverse ETF)
        For 1x inverse ETF (PSQ): VaR reduction = var_parametric_95 * position_mv * hedge_ratio
        For 3x inverse ETF (SOXS): VaR reduction = var_parametric_95 * position_mv * hedge_ratio * 0.8
        (discount to 0.8 because 3x leverage creates basis risk and tracking error)

    sector_rotation / partial_liquidation / cash_raise:
        Assume 25% of position is reduced.
        VaR reduction = var_parametric_95 * position_mv * 0.25
        (linear: removing 25% of position removes 25% of its VaR contribution)

    covered_call:
        No VaR reduction (call premium does not protect downside).
        var_reduction_usd = 0
        (This is an income strategy; rank it separately)
    """
```

---

### 6. Ranking with Tax Penalty

```python
def compute_ranking_metric(
    strategy_cost_usd: float,
    tax_cost_usd: float,
    var_reduction_usd: float,
    is_taxable_account: bool,
    tax_penalty_multiplier: float
) -> float:
    """
    Compute the cost-per-VaR-reduction metric used for ranking.

    For taxable accounts:
        effective_cost = strategy_cost_usd + (tax_cost_usd * tax_penalty_multiplier)
    For non-taxable:
        effective_cost = strategy_cost_usd

    cost_per_var_reduction = effective_cost / var_reduction_usd

    Lower is better — more risk reduction per effective dollar.

    tax_penalty_multiplier from rm_scoring_config key 'tax_penalty_multiplier'.
    Default value: 1.0 (tax cost counted at face value in ranking).
    Range: 0.5 (halved) to 2.0 (doubled) — configurable by the user.

    If var_reduction_usd is zero or NULL: return float('inf')
    (strategy provides no VaR reduction — rank last)
    """
```

---

### 7. Tax Cost and Notes

```python
def build_tax_impact_note(
    strategy_code: str,
    account_category: str,
    unrealised_gain_usd: float,
    tax_cost_usd: float,
    symbol: str,
    owner: str,
    fx_rate: float
) -> str:
    """
    Generate plain English tax impact note for a recommendation.

    Examples:
    "No tax consequence — TFSA_TOM account."
    "Collar: no taxable event unless AAPL is called away at the call strike. Premium cost is not a taxable event."
    "Trailing stop: if triggered, will crystallise approximately $200,273 USD ($277,114 CAD) in capital gains. Estimated tax: ~$43,219 USD (~$59,788 CAD) at 43.26% marginal rate with 50% inclusion."
    "Partial liquidation of 25% of position: estimated capital gain ~$82,750 USD. Estimated tax: ~$17,873 USD."
    """
```

---

### 8. Admin Registration

```python
@admin.register(OwnerConfig)
class OwnerConfigAdmin(admin.ModelAdmin):
    list_display = ['owner', 'marginal_tax_rate_ca', 'capital_gains_inclusion_rate',
                    'cost_tolerance_pct', 'updated_at', 'updated_by']
    fields = ['owner', 'marginal_tax_rate_ca', 'capital_gains_inclusion_rate',
              'cost_tolerance_pct', 'preferences_json', 'updated_by']
    # NOTE: marginal_tax_rate_ca and capital_gains_inclusion_rate are sensitive.
    # These are user-supplied; the system only displays and uses them.


@admin.register(StrategyRecommendation)
class StrategyRecommendationAdmin(admin.ModelAdmin):
    list_display = [
        'symbol', 'account_id', 'calc_date', 'rank',
        'strategy_template', 'urgency_level',
        'estimated_strategy_cost_usd', 'tax_cost_estimate_usd',
        'estimated_var_reduction_usd', 'cost_per_var_reduction_usd',
        'is_tax_triggering', 'status'
    ]
    list_filter = ['calc_date', 'urgency_level', 'status', 'is_tax_triggering', 'account_id']
    search_fields = ['symbol', 'account_id', 'tax_impact_note']
    ordering = ['-calc_date', 'symbol', 'rank']
    readonly_fields = [
        'symbol', 'account_id', 'owner', 'calc_date', 'strategy_template', 'rank',
        'urgency_level', 'urgency_score', 'regime_quadrant',
        'position_quantity', 'position_market_value_usd', 'position_unrealised_gain_usd',
        'cost_basis_per_unit_usd', 'current_price_usd', 'vol_63d',
        'estimated_strategy_cost_usd', 'estimated_strategy_cost_pct',
        'tax_cost_estimate_usd', 'tax_impact_note', 'is_tax_triggering',
        'estimated_var_reduction_usd', 'estimated_var_reduction_pct',
        'estimated_urgency_delta', 'cost_per_var_reduction_usd',
        'constraints_applied_json', 'calc_timestamp'
    ]
    # status, actioned_at, actioned_by are editable (user can mark as actioned)
```

---

### 9. Tests

File: `risk/tests/test_strategy_optimisation.py`

```python
import numpy as np
from decimal import Decimal
from django.test import TestCase
from django.core.management import call_command
from risk.services.option_pricing import black_scholes_put, black_scholes_call, estimate_option_cost
from risk.services.var_reduction import estimate_var_reduction
from risk.services.strategy_costs import estimate_strategy_cost
from risk.models import StrategyRecommendation, OwnerConfig


class BlackScholesTests(TestCase):

    def test_put_price_increases_with_volatility(self):
        # Higher vol = more expensive put
        put_low_vol = black_scholes_put(S=100, K=100, T=0.5, r=0.045, sigma=0.20)
        put_high_vol = black_scholes_put(S=100, K=100, T=0.5, r=0.045, sigma=0.50)
        self.assertGreater(put_high_vol, put_low_vol)

    def test_put_call_parity(self):
        # Put-call parity: C - P = S - K * exp(-r*T)
        S, K, T, r, sigma = 100, 100, 0.5, 0.045, 0.30
        call = black_scholes_call(S, K, T, r, sigma)
        put = black_scholes_put(S, K, T, r, sigma)
        parity_lhs = call - put
        parity_rhs = S - K * np.exp(-r * T)
        self.assertAlmostEqual(parity_lhs, parity_rhs, places=4)

    def test_atm_put_approximately_half_vol_times_price(self):
        # ATM put approximation: P ≈ S * sigma * sqrt(T/2pi)
        S, K, T, r, sigma = 100, 100, 0.5, 0.04, 0.30
        put = black_scholes_put(S, K, T, r, sigma)
        approx = S * sigma * np.sqrt(T / (2 * np.pi))
        self.assertAlmostEqual(put, approx, delta=approx * 0.15)  # within 15%

    def test_zero_volatility_put_equals_intrinsic(self):
        # With sigma=0 and ITM put (K > S): put = K*exp(-rT) - S
        put = black_scholes_put(S=90, K=100, T=1.0, r=0.05, sigma=0.001)
        intrinsic = 100 * np.exp(-0.05) - 90
        self.assertAlmostEqual(put, intrinsic, delta=0.01)

    def test_collar_cost_less_than_put_alone(self):
        # Collar (put bought + call sold) should cost less than standalone put
        option_cost = estimate_option_cost('protective_put', 200.0, 670, 0.30, 0.045, 6)
        collar_cost = estimate_option_cost('collar', 200.0, 670, 0.30, 0.045, 6)
        self.assertLess(collar_cost['total_cost_usd'], option_cost['total_cost_usd'])

    def test_aapl_put_cost_reasonable(self):
        # AAPL: price ~$315, 670 shares, vol ~30%, 6-month ATM put
        # Expected rough cost: 670 * 315 * 0.025 ≈ $5,276 (2.5% of notional)
        result = estimate_option_cost('protective_put', 315.0, 670, 0.30, 0.045, 6)
        self.assertGreater(result['total_cost_usd'], 2000)
        self.assertLess(result['total_cost_usd'], 15000)


class VaRReductionTests(TestCase):

    def test_protective_put_reduces_var_by_half(self):
        # ATM put delta ≈ 0.5, so VaR reduction ≈ 50% of position VaR
        result = estimate_var_reduction(
            strategy_code='protective_put',
            position_market_value_usd=200000,
            var_parametric_95=0.03,  # 3% daily VaR
            beta_spy=1.2,
            current_price_usd=315.0,
        )
        # Expected: 200000 * 0.03 * 0.5 = $3,000
        self.assertAlmostEqual(result['var_reduction_usd'], 3000, delta=200)

    def test_trailing_stop_10pct_limits_var(self):
        # 10% trail: max loss = 10% of position
        result = estimate_var_reduction(
            strategy_code='trailing_stop',
            position_market_value_usd=450000,
            var_parametric_95=0.025,
            beta_spy=0.8,
            current_price_usd=73.74,
            stop_trail_pct=0.10,
        )
        # var_after_stop = min(0.025*450000, 0.10*450000) = min(11250, 45000) = 11250
        # var_reduction = 11250 - 11250 = 0 (VaR is already less than the stop trail)
        # This is correct — for a 2.5% daily VaR, a 10% stop doesn't add protection
        self.assertGreaterEqual(result['var_reduction_usd'], 0)

    def test_inverse_etf_reduces_var_proportionally(self):
        # 50% hedge with 1x inverse ETF should reduce VaR by ~50%
        result = estimate_var_reduction(
            strategy_code='inverse_etf',
            position_market_value_usd=100000,
            var_parametric_95=0.04,
            beta_spy=1.5,
            current_price_usd=747.0,
        )
        # hedge_ratio=0.5; VaR reduction = 0.04 * 100000 * 0.5 = $2,000
        self.assertAlmostEqual(result['var_reduction_usd'], 2000, delta=300)

    def test_covered_call_zero_var_reduction(self):
        result = estimate_var_reduction(
            strategy_code='covered_call',
            position_market_value_usd=450000,
            var_parametric_95=0.02,
            beta_spy=0.7,
            current_price_usd=73.74,
        )
        self.assertEqual(result['var_reduction_usd'], 0)


class RankingTests(TestCase):

    def setUp(self):
        call_command('seed_instrument_catalogue', verbosity=0)
        # Seed owner config
        OwnerConfig.objects.create(
            owner='SUSAN',
            marginal_tax_rate_ca=Decimal('0.4326'),
            capital_gains_inclusion_rate=Decimal('0.5000'),
            cost_tolerance_pct=Decimal('0.0200'),
        )

    def test_collar_ranks_above_liquidation_for_high_gain_taxable(self):
        # For AAPL in IB_SUSAN: collar (no sale) must rank above partial liquidation (triggers $200K gain tax)
        # This is validated by checking that the recommendation table has collar at rank 1
        # Full integration test — run the command with seeded data
        pass  # Implement with full fixture setup

    def test_tfsa_strategies_have_zero_tax_cost(self):
        # All strategies in TFSA accounts: tax_cost_estimate_usd must be 0
        # Seed a TFSA position and run command; assert tax_cost = 0 for all results
        pass

    def test_cef_adx_only_gets_trailing_stop(self):
        # ADX (CEF, LIF_TOM): only trailing_stop_cef should appear in recommendations
        pass

    def test_cost_per_var_reduction_calculated_correctly(self):
        # strategy_cost=1000, tax_cost=0, var_reduction=5000
        # cost_per_var_reduction should be 1000/5000 = 0.2
        from risk.services.strategy_costs import compute_ranking_metric
        result = compute_ranking_metric(
            strategy_cost_usd=1000,
            tax_cost_usd=0,
            var_reduction_usd=5000,
            is_taxable_account=False,
            tax_penalty_multiplier=1.0
        )
        self.assertAlmostEqual(result, 0.2, places=4)

    def test_tax_penalty_multiplier_increases_effective_cost(self):
        from risk.services.strategy_costs import compute_ranking_metric
        no_penalty = compute_ranking_metric(1000, 5000, 10000, True, 1.0)
        with_penalty = compute_ranking_metric(1000, 5000, 10000, True, 1.5)
        self.assertGreater(with_penalty, no_penalty)

    def test_zero_var_reduction_ranks_last(self):
        from risk.services.strategy_costs import compute_ranking_metric
        result = compute_ranking_metric(100, 0, 0, False, 1.0)
        self.assertEqual(result, float('inf'))


class TaxImpactNoteTests(TestCase):

    def test_tfsa_note_says_no_tax(self):
        from risk.services.strategy_costs import build_tax_impact_note
        note = build_tax_impact_note('trailing_stop', 'TFSA', 5000, 0, 'IBIT', 'TOM', 1.384)
        self.assertIn('no tax', note.lower())

    def test_taxable_trailing_stop_note_includes_gain_amount(self):
        from risk.services.strategy_costs import build_tax_impact_note
        note = build_tax_impact_note('trailing_stop', 'Investment', 200000, 43300, 'AAPL', 'SUSAN', 1.384)
        self.assertIn('200,000', note.replace(',', ','))  # gain amount present
        self.assertIn('43,300', note.replace(',', ','))  # tax amount present

    def test_collar_note_says_no_sale_required(self):
        from risk.services.strategy_costs import build_tax_impact_note
        note = build_tax_impact_note('collar', 'Investment', 200000, 0, 'AAPL', 'SUSAN', 1.384)
        self.assertIn('no taxable event', note.lower())
```

Run with: python manage.py test risk.tests.test_strategy_optimisation --settings=portfolio_manager.settings_test

---

## Constraints

- DO NOT implement any UI views — that is M6
- DO NOT implement backtesting — that is M5
- DO NOT push to git (manual step)
- Tax rates in rm_owner_config are user-supplied — the system NEVER calculates or infers them
- Black-Scholes is used only for directional estimates, not trading-grade pricing — document this in code
- All monetary outputs in USD; display CAD equivalent using current FX rate
- The tax_penalty_multiplier in rm_scoring_config must be configurable (default 1.0) — never hard-coded
- PEP 8 throughout; full type hints; docstrings on all public functions

---

## Manual Steps Required (in order)

- [ ] Verify gate: SELECT COUNT(*) FROM rm_strategy_template WHERE is_active=1 — expect 11+ rows
- [ ] Verify gate: SELECT COUNT(*) FROM rm_urgency_score WHERE calc_date='2026-06-02' AND urgency_level IN ('High','Critical')
- [ ] python manage.py makemigrations risk
- [ ] python manage.py migrate --settings=portfolio_manager.settings_dev
- [ ] IMPORTANT: Update rm_owner_config with REAL marginal tax rates for TOM and SUSAN via Django admin before running recommendations
- [ ] python manage.py calc_strategy_recommendations --date 2026-06-02 --settings=portfolio_manager.settings_dev
- [ ] Verify in SSMS: SELECT symbol, account_id, rank, strategy_template_id, estimated_strategy_cost_usd, tax_cost_estimate_usd, estimated_var_reduction_usd, cost_per_var_reduction_usd, is_tax_triggering FROM rm_strategy_recommendation WHERE calc_date='2026-06-02' ORDER BY symbol, account_id, rank
- [ ] Manual review — confirm these specific results:
  - AAPL in IB_SUSAN: rank 1 should be collar or protective_put (not partial_liquidation)
  - VFV in TAXABLE_TOM_SUSAN: trailing stop should show ~$331K gain crystallisation warning
  - IBIT in TFSA_TOM: trailing_stop_registered should appear; tax_cost=0
  - ADX in LIF_TOM: only trailing_stop_cef should appear
- [ ] python manage.py test risk.tests.test_strategy_optimisation --settings=portfolio_manager.settings_test
- [ ] git add -A && git commit -m "feat: M4-Spec-1 strategy optimisation engine + option pricing"
- [ ] git push origin feature/risk-m4-optimisation

---

## Human Approval Required

Review tax rates in rm_owner_config with TOM and SUSAN before running recommendations — the tax cost estimates are meaningless without correct marginal rates. Also review the rank-1 recommendation for AAPL in IB_SUSAN: if partial_liquidation appears above collar, the tax_penalty_multiplier needs adjustment.

---

M4-Spec-1 | Portfolio Manager Phase B | June 2026 | tdwdev
