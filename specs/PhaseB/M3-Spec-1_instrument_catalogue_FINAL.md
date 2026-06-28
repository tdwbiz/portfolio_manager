# Feature Spec: M3-Spec-1 — Instrument & Strategy Catalogue

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m3-catalogue

Architecture ref: PM_PhaseB_Architecture_Final_v2.0.md
Depends on: M2-Spec-2 fully implemented — rm_urgency_score populated and verified for all entities

---

## Context

This is a pure reference-data spec. The catalogue defines every risk management instrument available to this portfolio and packages them into strategy templates that M4 queries to produce recommendations. No calculations happen here — the crew builds tables, seeds them with the correct data for this specific portfolio, and registers everything in Django admin.

M3 is the bridge between "we know the risk level" (M2) and "we know what to do about it" (M4). M4 is only as good as the catalogue it queries, so the seed data must be complete and correctly tagged.

**Portfolio-specific catalogue requirements (June 2, 2026):**

| Position | Key constraint | Instruments allowed |
|---|---|---|
| AAPL in IB_SUSAN | 1,832% unrealised gain; taxable; options available | Protective put, collar; NOT partial liquidation without tax acknowledgement |
| VFV in TAXABLE_TOM_SUSAN | $331K unrealised gain; taxable; no single-stock options | Trailing stop, partial liquidation with tax model |
| IBIT/ETHA in TFSA_TOM/SUSAN | Crypto ETF; tax-free account; no options on crypto ETFs | Trailing stop, partial liquidation (no tax cost) |
| ADX in LIF_TOM/RRSP_TOM | Closed-end fund; no options available; locked-in (LIF) | Trailing stop only; no partial liquidation for LIF |
| VDY in TAXABLE_TOM_SUSAN | $195K unrealised gain; taxable; no single-stock options | Trailing stop, covered call, partial liquidation with tax model |
| SOXX/SMH/NVDA | Semiconductor concentration; options available on all | SOXS hedge (inverse ETF), protective put, sector rotation |
| QQQ/IGM | Nasdaq concentration; options available on QQQ | PSQ hedge, protective put on QQQ |
| RBF2010/RBF2014/SGOV | Near-cash; Low urgency expected | No hedging needed; cash_raise is applicable in reverse |

**Stack:**
- Python 3.12 / Django 5.1
- MSSQL 2025 (dev: PortfolioDEV)
- Project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. New Database Tables

#### 1.1 rm_instrument_type

One row per instrument type. Reference data — maintained by the developer, not computed.

```python
class InstrumentType(models.Model):
    """
    A category of risk management instrument.
    Defines mechanics, cost structure, and eligibility rules.
    This is reference data — edited via Django admin, not by application logic.
    """
    code = models.CharField(max_length=30, unique=True)
    # Machine-readable identifier used by M4 engine.
    # Values: 'protective_put', 'collar', 'covered_call', 'trailing_stop',
    #         'inverse_etf', 'sector_rotation', 'partial_liquidation', 'cash_raise',
    #         'diversification'

    name = models.CharField(max_length=100)
    # Human-readable name. E.g. "Protective Put Option"

    mechanics_description = models.TextField()
    # Plain English: what this instrument is and how it works.
    # Written for a knowledgeable investor, not a finance textbook.

    use_cases_json = models.TextField()
    # JSON list of strings. When is this instrument appropriate?
    # E.g. ["High urgency single stock", "Pre-earnings risk reduction",
    #        "Taxable position where sale is not desirable"]

    cost_structure_json = models.TextField()
    # JSON object. How does this instrument cost money?
    # E.g. {"type": "premium", "typical_pct_of_notional": 0.02,
    #        "bid_ask_spread_pct": 0.003, "commission_per_contract": 1.50}
    # For trailing_stop: {"type": "friction_only", "bid_ask_spread_pct": 0.001,
    #                     "commission_per_trade": 1.00}

    tax_treatment_json = models.TextField()
    # JSON object keyed by account_category.
    # E.g. {
    #   "TFSA": {"tax_cost_on_exercise": false, "wash_sale_applicable": false, "notes": "No tax consequence"},
    #   "RRSP": {"tax_cost_on_exercise": false, "wash_sale_applicable": false, "notes": "Sheltered"},
    #   "LIF":  {"tax_cost_on_exercise": false, "wash_sale_applicable": false, "notes": "Sheltered; locked-in"},
    #   "RRIF": {"tax_cost_on_exercise": false, "wash_sale_applicable": false, "notes": "Sheltered; mandatory drawdown"},
    #   "Taxable": {"tax_cost_on_exercise": true, "wash_sale_applicable": true,
    #               "notes": "Exercise triggers capital gain; wash sale if repurchase within 30 days"}
    # }

    # Account type eligibility flags
    eligible_tfsa = models.BooleanField(default=True)
    eligible_rrsp = models.BooleanField(default=True)
    eligible_lif = models.BooleanField(default=True)
    eligible_rrif = models.BooleanField(default=True)
    eligible_taxable = models.BooleanField(default=True)
    # False means this instrument CANNOT be used in that account type.
    # E.g. options strategies are generally not available in registered accounts at most brokers.
    # For this portfolio (IB accounts): options ARE available in IB_SUSAN, IB_TOM, IB_JOINT.
    # For registered accounts (RRSP, TFSA, etc.): options are NOT available — set to False.

    requires_options_availability = models.BooleanField(default=False)
    # True for protective_put, collar, covered_call.
    # M4 checks rm_security_universe.options_available before recommending these.

    wash_sale_applicable = models.BooleanField(default=False)
    # True if this instrument can trigger wash sale rules (US tax law).
    # Applies to: partial_liquidation followed by repurchase.
    # Relevant for IB_SUSAN, IB_TOM, IB_JOINT (US-listed securities in taxable accounts).

    sort_order = models.IntegerField(default=0)
    # Display ordering in admin and UI. Lower = shown first.

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rm_instrument_type'
        ordering = ['sort_order', 'name']
```

#### 1.2 rm_strategy_template

One row per named strategy template. Each template is a pre-defined playbook for a specific scenario.

```python
class StrategyTemplate(models.Model):
    """
    A named, scenario-specific risk management strategy.
    Combines one or more instrument types into an actionable playbook.
    M4 queries this table to find candidates for a given position/urgency/regime.
    """
    code = models.CharField(max_length=50, unique=True)
    # Machine-readable identifier. E.g. 'collar_high_gain_taxable_stock'

    name = models.CharField(max_length=150)
    # Human-readable name shown in reports and dashboard.

    description = models.TextField()
    # Plain English explanation of the strategy: what it does, why it works,
    # and what the investor gives up. Written for an informed investor.

    # --- Tagging — used by M4 for candidate filtering ---

    applicable_urgency_levels = models.CharField(max_length=50)
    # Comma-separated list of urgency levels where this template is relevant.
    # Values: 'Low', 'Medium', 'High', 'Critical'
    # E.g. 'High,Critical' — only recommend this template for High or Critical urgency.

    applicable_regime_quadrants = models.CharField(max_length=20)
    # Comma-separated list of regime quadrants. 'ALL' means regime-agnostic.
    # E.g. 'Q3,Q4' — only recommend in contraction/stagflation regimes.
    # E.g. 'ALL' — recommend regardless of regime.

    applicable_asset_types = models.CharField(max_length=100)
    # Comma-separated list of asset_class values from rm_security_universe.
    # E.g. 'common_stock,etf_equity' — applies to stocks and equity ETFs.
    # E.g. 'common_stock' — stocks only (e.g. options-based strategies).

    applicable_account_categories = models.CharField(max_length=100)
    # Comma-separated list of account categories where this template is valid.
    # Values match core_account.category: 'Investment', 'Investment Joint',
    #   'TFSA', 'RRSP', 'LIF', 'RRIF'
    # E.g. 'Investment,Investment Joint' — taxable accounts only.
    # E.g. 'ALL' — any account type.

    min_unrealised_gain_usd = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Minimum UGL in USD for this template to be applicable.
    # E.g. 50000 — only recommend partial liquidation if gain > $50K USD.
    # 0 = no minimum (applicable at any gain level, including losses).

    requires_options = models.BooleanField(default=False)
    # True if any instrument in this template requires options availability.
    # M4 checks rm_security_universe.options_available and skips if False.

    cef_eligible = models.BooleanField(default=True)
    # False if this template cannot be used for closed-end funds (e.g. ADX).
    # Options-based templates must have cef_eligible=False.

    lif_rrif_eligible = models.BooleanField(default=True)
    # False if withdrawal restrictions make this template impractical for LIF/RRIF.
    # Partial liquidation in a LIF triggers locked-in pension rules — flag it.

    is_tax_triggering = models.BooleanField(default=False)
    # True if applying this strategy will crystallise a taxable gain.
    # Drives M8 tax acknowledgement workflow when recommendation is marked actioned.
    # True for: partial_liquidation in taxable accounts, covered_call (premium is income),
    #           inverse_etf (when closed at a gain).

    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rm_strategy_template'
        ordering = ['sort_order', 'name']
```

#### 1.3 rm_strategy_instrument_link

Many-to-many link: each strategy template uses one or more instrument types.

```python
class StrategyInstrumentLink(models.Model):
    """
    Links a strategy template to the instrument types it uses.
    role describes how the instrument functions within the strategy.
    """
    strategy = models.ForeignKey(
        StrategyTemplate, on_delete=models.CASCADE, related_name='instrument_links'
    )
    instrument = models.ForeignKey(
        InstrumentType, on_delete=models.CASCADE, related_name='strategy_links'
    )
    role = models.CharField(max_length=50)
    # How this instrument is used in the strategy.
    # E.g. 'primary_hedge', 'income_offset', 'exit_trigger'
    sort_order = models.IntegerField(default=0)

    class Meta:
        db_table = 'rm_strategy_instrument_link'
        unique_together = [('strategy', 'instrument')]
        ordering = ['sort_order']
```

---

### 2. Seed Data

Create management command `seed_instrument_catalogue` in `risk/management/commands/seed_instrument_catalogue.py`.

This command uses MSSQL MERGE (upsert by code) so it is safe to run repeatedly without creating duplicates. Run after migrations.

#### 2.1 Instrument Types Seed (8 types)

```python
INSTRUMENT_TYPES = [
    {
        'code': 'protective_put',
        'name': 'Protective Put Option',
        'sort_order': 10,
        'requires_options_availability': True,
        'wash_sale_applicable': False,
        'is_tax_triggering_on_exercise': False,
        'eligible_tfsa': False,
        'eligible_rrsp': False,
        'eligible_lif': False,
        'eligible_rrif': False,
        'eligible_taxable': True,
        # Options not available in Canadian registered accounts for this portfolio.
        # IB_SUSAN, IB_TOM, IB_JOINT are taxable USD accounts where options are available.
        'mechanics_description': (
            'Purchase a put option giving the right to sell a held security at the strike '
            'price before expiry. Limits downside below the strike price while preserving '
            'upside participation. Cost is the option premium paid upfront.'
        ),
        'use_cases_json': '[\"High unrealised gain in single stock\", \"Pre-earnings protection\", '
                          '\"High urgency without wanting to sell\"]',
        'cost_structure_json': '{\"type\": \"premium\", \"typical_pct_of_notional_annual\": 0.025, '
                               '\"bid_ask_spread_pct\": 0.004, \"commission_per_contract_usd\": 1.50}',
        'tax_treatment_json': '{\"Taxable\": {\"tax_cost_on_exercise\": false, '
                              '\"wash_sale_applicable\": false, '
                              '\"notes\": \"Put premium is a capital cost; exercise does not itself trigger sale of underlying\"}, '
                              '\"TFSA\": {\"tax_cost_on_exercise\": false, \"notes\": \"Not available\"}}',
    },
    {
        'code': 'collar',
        'name': 'Collar (Protective Put + Covered Call)',
        'sort_order': 20,
        'requires_options_availability': True,
        'wash_sale_applicable': False,
        'eligible_tfsa': False,
        'eligible_rrsp': False,
        'eligible_lif': False,
        'eligible_rrif': False,
        'eligible_taxable': True,
        'mechanics_description': (
            'Simultaneously buy a put option (downside protection) and sell a call option '
            '(upside cap) on the same held security. The call premium offsets some or all of '
            'the put premium, reducing net cost. Trade-off: upside is capped at the call strike. '
            'Ideal for high-gain taxable positions where the goal is protection without triggering a sale.'
        ),
        'use_cases_json': '[\"Very high unrealised gain taxable position\", '
                          '\"Investor comfortable capping upside\", '
                          '\"Reduce net hedging cost vs standalone put\"]',
        'cost_structure_json': '{\"type\": \"net_premium\", '
                               '\"typical_net_cost_pct_of_notional\": 0.005, '
                               '\"note\": \"Call premium received offsets put premium paid; may be near zero-cost\"}',
        'tax_treatment_json': '{\"Taxable\": {\"tax_cost_on_exercise\": false, '
                              '\"notes\": \"No sale triggered; call premium received is taxable income in year received\"}}',
    },
    {
        'code': 'covered_call',
        'name': 'Covered Call',
        'sort_order': 30,
        'requires_options_availability': True,
        'wash_sale_applicable': False,
        'eligible_tfsa': False,
        'eligible_rrsp': False,
        'eligible_lif': False,
        'eligible_rrif': False,
        'eligible_taxable': True,
        'mechanics_description': (
            'Sell a call option against a held position. Receive premium income immediately. '
            'If the price exceeds the strike at expiry, shares are called away at the strike '
            'price — effectively a sale at a predetermined price. Appropriate when the investor '
            'is willing to sell at the strike level and wants income in the meantime.'
        ),
        'use_cases_json': '[\"Generate income on stagnant position\", '
                          '\"VDY income enhancement\", '
                          '\"Position where upside is acceptable to cap at strike\"]',
        'cost_structure_json': '{\"type\": \"income\", \"typical_premium_pct_annual\": 0.04, '
                               '\"commission_per_contract_usd\": 1.50}',
        'tax_treatment_json': '{\"Taxable\": {\"premium_taxable\": true, '
                              '\"exercise_triggers_sale\": true, '
                              '\"notes\": \"Premium income taxable in year received; exercise is a taxable disposition\"}}',
    },
    {
        'code': 'trailing_stop',
        'name': 'Trailing Stop-Loss Order',
        'sort_order': 40,
        'requires_options_availability': False,
        'wash_sale_applicable': True,
        'eligible_tfsa': True,
        'eligible_rrsp': True,
        'eligible_lif': False,
        'eligible_rrif': True,
        'eligible_taxable': True,
        # LIF: not eligible — locked-in pension funds have restrictions on withdrawal triggering events.
        'mechanics_description': (
            'A stop-loss order that trails the price upward but never moves downward. '
            'Set as a % or dollar amount below the current market price. If price drops '
            'by that amount from any subsequent high, position is sold at market. '
            'No cost until triggered (other than bid-ask on execution). Simple and effective '
            'for ETF positions where options are unnecessary or unavailable.'
        ),
        'use_cases_json': '[\"Crypto ETF (IBIT, ETHA) in TFSA\", '
                          '\"CEF position (ADX) with no options\", '
                          '\"Any ETF at High or Critical urgency\", '
                          '\"Low-cost protection in registered accounts\"]',
        'cost_structure_json': '{\"type\": \"friction_only\", \"bid_ask_spread_pct\": 0.001, '
                               '\"commission_per_trade_usd\": 1.00, '
                               '\"note\": \"No premium; cost is only on execution\"}',
        'tax_treatment_json': '{\"Taxable\": {\"triggers_sale\": true, \"wash_sale_applicable\": true, '
                              '\"notes\": \"Sale triggers capital gain/loss; repurchase within 30d is wash sale\"}, '
                              '\"TFSA\": {\"triggers_sale\": true, \"tax_cost\": false}, '
                              '\"RRSP\": {\"triggers_sale\": true, \"tax_cost\": false}}',
    },
    {
        'code': 'inverse_etf',
        'name': 'Inverse ETF Hedge',
        'sort_order': 50,
        'requires_options_availability': False,
        'wash_sale_applicable': False,
        'eligible_tfsa': True,
        'eligible_rrsp': True,
        'eligible_lif': False,
        'eligible_rrif': True,
        'eligible_taxable': True,
        'mechanics_description': (
            'Purchase an inverse or short ETF that moves opposite to a held index or sector. '
            'E.g. PSQ (inverse QQQ) hedges Nasdaq concentration; SOXS (3x inverse semiconductors) '
            'hedges SOXX/SMH/NVDA. The hedge ratio is position_size / inverse_etf_notional. '
            'Note: leveraged inverse ETFs decay over time due to daily rebalancing — intended '
            'for short-term hedging (days to weeks), not long-term holds.'
        ),
        'use_cases_json': '[\"Nasdaq concentration hedge via PSQ\", '
                          '\"Semiconductor concentration hedge via SOXS\", '
                          '\"S&P 500 hedge via SPXS or SH\", '
                          '\"Bear market or Q3/Q4 regime protection\"]',
        'cost_structure_json': '{\"type\": \"management_fee_plus_decay\", '
                               '\"typical_annual_decay_pct\": 0.05, '
                               '\"bid_ask_spread_pct\": 0.002, '
                               '\"note\": \"Leveraged inverse ETFs have significant decay; hold period should be short\"}',
        'tax_treatment_json': '{\"Taxable\": {\"gain_on_close_taxable\": true, '
                              '\"notes\": \"Closing a profitable inverse ETF position triggers capital gain\"}, '
                              '\"TFSA\": {\"gain_on_close_taxable\": false}, '
                              '\"RRSP\": {\"gain_on_close_taxable\": false}}',
    },
    {
        'code': 'sector_rotation',
        'name': 'Sector Rotation',
        'sort_order': 60,
        'requires_options_availability': False,
        'wash_sale_applicable': True,
        'eligible_tfsa': True,
        'eligible_rrsp': True,
        'eligible_lif': True,
        'eligible_rrif': True,
        'eligible_taxable': True,
        'mechanics_description': (
            'Reduce weighting in a cyclical or at-risk sector and increase allocation to '
            'a defensive or uncorrelated sector. E.g. reduce SOXX/SMH (semiconductor), '
            'increase CGDV or SGOV (dividend/cash). Does not require selling everything — '
            'only trim the overweight position and redeploy into the defensive target. '
            'Particularly effective in Q3/Q4 regime where cyclical sectors underperform.'
        ),
        'use_cases_json': '[\"Q3 or Q4 regime — reduce cyclical, add defensive\", '
                          '\"Semiconductor concentration above threshold\", '
                          '\"Tech concentration with high urgency across multiple positions\"]',
        'cost_structure_json': '{\"type\": \"two_trade_friction\", '
                               '\"bid_ask_spread_pct_sell\": 0.001, '
                               '\"bid_ask_spread_pct_buy\": 0.001, '
                               '\"commission_per_trade_usd\": 1.00}',
        'tax_treatment_json': '{\"Taxable\": {\"sale_triggers_gain\": true, '
                              '\"notes\": \"Reducing position triggers capital gain on unrealised amount sold\"}, '
                              '\"TFSA\": {\"sale_triggers_gain\": false}, '
                              '\"RRSP\": {\"sale_triggers_gain\": false}, '
                              '\"LIF\": {\"sale_triggers_gain\": false, \"notes\": \"Locked-in; reinvestment within plan\"}}',
    },
    {
        'code': 'partial_liquidation',
        'name': 'Partial Position Reduction',
        'sort_order': 70,
        'requires_options_availability': False,
        'wash_sale_applicable': True,
        'eligible_tfsa': True,
        'eligible_rrsp': True,
        'eligible_lif': False,
        'eligible_rrif': True,
        'eligible_taxable': True,
        # LIF: not eligible — partial withdrawal from locked-in pension has regulatory constraints.
        'mechanics_description': (
            'Sell a portion of a held position to reduce exposure. The most direct risk reduction '
            'available, but triggers a taxable event for any unrealised gain in taxable accounts. '
            'M4 models the tax cost explicitly and compares it to the risk reduction achieved. '
            'For TAXABLE accounts with large unrealised gains (AAPL, VFV), this should rank '
            'BELOW collar and trailing stop unless the urgency level is Critical.'
        ),
        'use_cases_json': '[\"Critical urgency where other strategies are unavailable\", '
                          '\"Tax-free accounts (TFSA) with high urgency\", '
                          '\"Portfolio rebalancing to reduce concentration\", '
                          '\"Raise cash for defensive positioning\"]',
        'cost_structure_json': '{\"type\": \"friction_only\", '
                               '\"bid_ask_spread_pct\": 0.001, '
                               '\"commission_per_trade_usd\": 1.00}',
        'tax_treatment_json': '{\"Taxable\": {\"triggers_capital_gain\": true, '
                              '\"wash_sale_applicable\": true, '
                              '\"notes\": \"(proceeds - adjusted_cost_base) is taxable capital gain in Canada\"}, '
                              '\"TFSA\": {\"triggers_capital_gain\": false, \"notes\": \"No tax consequence\"}, '
                              '\"RRSP\": {\"triggers_capital_gain\": false, \"notes\": \"No tax consequence within plan\"}, '
                              '\"RRIF\": {\"triggers_capital_gain\": false, \"notes\": \"No tax on internal sale; withdrawals are taxed as income\"}}',
    },
    {
        'code': 'cash_raise',
        'name': 'Cash Raise / Defensive Positioning',
        'sort_order': 80,
        'requires_options_availability': False,
        'wash_sale_applicable': False,
        'eligible_tfsa': True,
        'eligible_rrsp': True,
        'eligible_lif': False,
        'eligible_rrif': True,
        'eligible_taxable': True,
        'mechanics_description': (
            'Increase allocation to cash-equivalent instruments (SGOV, RBF2010, RBF2014, ICSH) '
            'by selling risk assets. Reduces portfolio beta and VaR directly. In taxable accounts, '
            'any sale of appreciated positions triggers capital gain. In tax-free accounts, '
            'this is the cleanest and most flexible defensive action available. '
            'Appropriate in Q3/Q4 regimes or when portfolio urgency is Critical.'
        ),
        'use_cases_json': '[\"Q4 stagflation regime — maximum defensiveness\", '
                          '\"Portfolio urgency Critical — reduce overall risk\", '
                          '\"TFSA/RRSP accounts during bear market\", '
                          '\"Raise liquidity ahead of anticipated volatility event\"]',
        'cost_structure_json': '{\"type\": \"friction_only\", '
                               '\"bid_ask_spread_pct\": 0.001, '
                               '\"commission_per_trade_usd\": 1.00, '
                               '\"opportunity_cost\": \"forgone equity returns\"}',
        'tax_treatment_json': '{\"Taxable\": {\"triggers_capital_gain\": true, '
                              '\"notes\": \"Selling appreciated positions to raise cash triggers capital gain\"}, '
                              '\"TFSA\": {\"triggers_capital_gain\": false}, '
                              '\"RRSP\": {\"triggers_capital_gain\": false}}',
    },
]
```

#### 2.2 Strategy Templates Seed (11 templates)

```python
STRATEGY_TEMPLATES = [
    {
        'code': 'collar_high_gain_taxable_stock',
        'name': 'Collar — High-Gain Taxable Single Stock',
        'sort_order': 10,
        'applicable_urgency_levels': 'High,Critical',
        'applicable_regime_quadrants': 'ALL',
        'applicable_asset_types': 'common_stock',
        'applicable_account_categories': 'Investment,Investment Joint',
        'min_unrealised_gain_usd': 50000,
        'requires_options': True,
        'cef_eligible': False,
        'lif_rrif_eligible': False,
        'is_tax_triggering': False,
        # Tax not triggered unless the position is called away at expiry.
        'description': (
            'Buy a protective put and simultaneously sell a covered call on the same high-gain '
            'taxable single stock position. The call premium offsets the put cost, typically '
            'achieving near-zero-cost protection. Downside is floored at the put strike; '
            'upside is capped at the call strike. No taxable event unless the call is exercised. '
            'Primary use case: AAPL in IB_SUSAN ($196K+ unrealised gain) and MA in IB_JOINT. '
            'The system should model this as: buy 6-month ATM put, sell OTM call 10–15% above '
            'current price. Net cost estimate comes from Black-Scholes in M4.'
        ),
        'instruments': [
            ('protective_put', 'primary_hedge'),
            ('covered_call', 'income_offset'),
        ],
    },
    {
        'code': 'protective_put_pretax_stock',
        'name': 'Protective Put — Single Stock Pre-Tax Protection',
        'sort_order': 20,
        'applicable_urgency_levels': 'High,Critical',
        'applicable_regime_quadrants': 'ALL',
        'applicable_asset_types': 'common_stock',
        'applicable_account_categories': 'Investment,Investment Joint',
        'min_unrealised_gain_usd': 0,
        'requires_options': True,
        'cef_eligible': False,
        'lif_rrif_eligible': False,
        'is_tax_triggering': False,
        'description': (
            'Purchase a standalone protective put on a taxable single-stock position. '
            'Full downside protection below the strike price with unlimited upside participation. '
            'Higher cost than a collar due to no call premium offset. '
            'Use when the investor wants to preserve all upside and can absorb the premium cost. '
            'Applicable to: AAPL, NVDA, GOOG, MA, V in taxable accounts.'
        ),
        'instruments': [
            ('protective_put', 'primary_hedge'),
        ],
    },
    {
        'code': 'trailing_stop_etf_taxable',
        'name': 'Trailing Stop — ETF in Taxable Account',
        'sort_order': 30,
        'applicable_urgency_levels': 'Medium,High,Critical',
        'applicable_regime_quadrants': 'ALL',
        'applicable_asset_types': 'etf_equity',
        'applicable_account_categories': 'Investment,Investment Joint',
        'min_unrealised_gain_usd': 0,
        'requires_options': False,
        'cef_eligible': True,
        'lif_rrif_eligible': False,
        'is_tax_triggering': True,
        # Will trigger a taxable gain if the stop is hit and the position has unrealised gain.
        'description': (
            'Set a trailing stop-loss order on an ETF position in a taxable account. '
            'Automatically exits the position if price drops by the trail amount from any '
            'subsequent high. Low cost; no premium; protection activates automatically. '
            'Important: triggering the stop crystallises a capital gain. M4 must model the '
            'tax cost and surface it prominently for positions with large unrealised gains '
            '(VFV $331K gain, VDY $195K gain in TAXABLE_TOM_SUSAN). '
            'Recommended trail: 10–15% for volatile ETFs, 8–10% for stable dividend ETFs.'
        ),
        'instruments': [
            ('trailing_stop', 'primary_hedge'),
        ],
    },
    {
        'code': 'trailing_stop_registered',
        'name': 'Trailing Stop — Registered Account (No Tax)',
        'sort_order': 35,
        'applicable_urgency_levels': 'Medium,High,Critical',
        'applicable_regime_quadrants': 'ALL',
        'applicable_asset_types': 'etf_equity,crypto_etf,common_stock',
        'applicable_account_categories': 'TFSA,RRSP,RRIF',
        'min_unrealised_gain_usd': 0,
        'requires_options': False,
        'cef_eligible': True,
        'lif_rrif_eligible': False,
        'is_tax_triggering': False,
        'description': (
            'Set a trailing stop-loss in a registered account (TFSA, RRSP, RRIF). '
            'Identical mechanics to the taxable version but no capital gains consequence '
            'on execution. The cleanest and lowest-cost risk reduction available in tax-sheltered '
            'accounts. Highly appropriate for crypto ETF positions (IBIT, ETHA in TFSA) '
            'given their high volatility. Recommended trail: 15–20% for IBIT/ETHA; '
            '8–12% for equity ETFs.'
        ),
        'instruments': [
            ('trailing_stop', 'primary_hedge'),
        ],
    },
    {
        'code': 'trailing_stop_cef',
        'name': 'Trailing Stop — Closed-End Fund (ADX)',
        'sort_order': 40,
        'applicable_urgency_levels': 'Medium,High,Critical',
        'applicable_regime_quadrants': 'ALL',
        'applicable_asset_types': 'cef',
        'applicable_account_categories': 'ALL',
        'min_unrealised_gain_usd': 0,
        'requires_options': False,
        'cef_eligible': True,
        'lif_rrif_eligible': False,
        'is_tax_triggering': True,
        'description': (
            'Trailing stop-loss on a closed-end fund position. '
            'This is the ONLY hedging instrument available for ADX, as options are not '
            'listed on closed-end funds. CEFs can trade at significant discounts or premiums '
            'to NAV; the stop should account for normal premium/discount volatility. '
            'Recommended trail: 12–15% to allow for NAV discount widening without premature exit. '
            'ADX is held in LIF_TOM (locked-in) and RRSP_TOM — LIF rules prevent full '
            'withdrawal so the stop must be set conservatively.'
        ),
        'instruments': [
            ('trailing_stop', 'primary_hedge'),
        ],
    },
    {
        'code': 'partial_liquidation_taxfree',
        'name': 'Partial Liquidation — Tax-Free Account',
        'sort_order': 50,
        'applicable_urgency_levels': 'High,Critical',
        'applicable_regime_quadrants': 'ALL',
        'applicable_asset_types': 'etf_equity,crypto_etf,common_stock,cef',
        'applicable_account_categories': 'TFSA,RRSP,RRIF',
        'min_unrealised_gain_usd': 0,
        'requires_options': False,
        'cef_eligible': True,
        'lif_rrif_eligible': False,
        'is_tax_triggering': False,
        'description': (
            'Reduce position size in a tax-sheltered account (TFSA, RRSP, RRIF). '
            'No capital gains consequence. The most direct risk reduction available in '
            'registered accounts. Proceed sold can be redeployed to SGOV or RBF for '
            'temporary defensive positioning. For RRIF accounts, be aware of minimum '
            'annual withdrawal requirements — selling a position is fine as long as '
            'the proceeds remain within the plan.'
        ),
        'instruments': [
            ('partial_liquidation', 'primary_action'),
        ],
    },
    {
        'code': 'partial_liquidation_taxable',
        'name': 'Partial Liquidation — Taxable Account (Last Resort)',
        'sort_order': 55,
        'applicable_urgency_levels': 'Critical',
        # Only recommend in taxable accounts when urgency is Critical — tax cost is too high otherwise.
        'applicable_regime_quadrants': 'ALL',
        'applicable_asset_types': 'etf_equity,crypto_etf,common_stock,cef',
        'applicable_account_categories': 'Investment,Investment Joint',
        'min_unrealised_gain_usd': 0,
        'requires_options': False,
        'cef_eligible': True,
        'lif_rrif_eligible': False,
        'is_tax_triggering': True,
        'description': (
            'Reduce position size in a taxable account. Triggers capital gains tax on the '
            'sold portion. M4 models the full tax cost using the owner-provided marginal tax rate. '
            'This template only appears as a recommendation in taxable accounts when urgency is '
            'Critical AND other strategies (collar, protective put, trailing stop) are either '
            'unavailable or have already been applied. '
            'For positions with massive unrealised gains (AAPL: $200K+, VFV: $331K+), the '
            'tax cost typically exceeds the risk reduction value unless the urgency is extreme.'
        ),
        'instruments': [
            ('partial_liquidation', 'primary_action'),
        ],
    },
    {
        'code': 'inverse_etf_semiconductor',
        'name': 'Inverse ETF Hedge — Semiconductor Concentration',
        'sort_order': 60,
        'applicable_urgency_levels': 'High,Critical',
        'applicable_regime_quadrants': 'Q3,Q4',
        'applicable_asset_types': 'etf_equity,common_stock',
        'applicable_account_categories': 'ALL',
        'min_unrealised_gain_usd': 0,
        'requires_options': False,
        'cef_eligible': False,
        'lif_rrif_eligible': False,
        'is_tax_triggering': False,
        'description': (
            'Purchase SOXS (Direxion 3x Inverse Semiconductor) to hedge concentrated '
            'semiconductor exposure across SOXX (TFSA_TOM, IB_TOM), SMH (TFSA_TOM), '
            'and NVDA (IB_TOM). The hedge ratio is: '
            'SOXS_notional = (total_semi_exposure_usd / 3) for a full delta hedge, '
            'or (total_semi_exposure_usd / 6) for a half hedge. '
            'IMPORTANT: SOXS is a 3x leveraged product with significant daily decay. '
            'Set a maximum hold period of 30 days. This is a tactical, not structural, hedge. '
            'Available in all account types. No tax event until SOXS is sold.'
        ),
        'instruments': [
            ('inverse_etf', 'primary_hedge'),
        ],
    },
    {
        'code': 'inverse_etf_nasdaq',
        'name': 'Inverse ETF Hedge — Nasdaq Concentration',
        'sort_order': 70,
        'applicable_urgency_levels': 'High,Critical',
        'applicable_regime_quadrants': 'Q3,Q4',
        'applicable_asset_types': 'etf_equity,common_stock',
        'applicable_account_categories': 'ALL',
        'min_unrealised_gain_usd': 0,
        'requires_options': False,
        'cef_eligible': False,
        'lif_rrif_eligible': False,
        'is_tax_triggering': False,
        'description': (
            'Purchase PSQ (ProShares Short QQQ, 1x inverse) to hedge Nasdaq-100 concentration '
            'across QQQ (IB_TOM, TFSA_TOM, TFSA_SUSAN) and IGM (TFSA_TOM, TFSA_SUSAN, IB_SUSAN). '
            'PSQ is 1x inverse (not leveraged) — lower decay than SOXS, suitable for longer '
            'hold periods (up to 90 days). Hedge ratio: PSQ_notional = total_nasdaq_exposure_usd '
            'for a full hedge, or 50% for a partial hedge. '
            'This effectively reduces Nasdaq beta toward zero across the QQQ/IGM positions.'
        ),
        'instruments': [
            ('inverse_etf', 'primary_hedge'),
        ],
    },
    {
        'code': 'sector_rotation_defensive',
        'name': 'Sector Rotation — Cyclical to Defensive',
        'sort_order': 80,
        'applicable_urgency_levels': 'Medium,High,Critical',
        'applicable_regime_quadrants': 'Q3,Q4',
        'applicable_asset_types': 'etf_equity,common_stock',
        'applicable_account_categories': 'ALL',
        'min_unrealised_gain_usd': 0,
        'requires_options': False,
        'cef_eligible': False,
        'lif_rrif_eligible': True,
        'is_tax_triggering': True,
        # Selling a cyclical position to buy defensive triggers a gain in taxable accounts.
        'description': (
            'Trim cyclical or growth sector positions and rotate into dividend or defensive '
            'instruments. Sell targets: SOXX, SMH, NVDA, QQQ, IGM (high-beta tech/semi). '
            'Buy targets: CGDV, VDY, SGOV (dividend/cash — lower beta, more defensive). '
            'Most effective in Q3 (recession) and Q4 (stagflation) regimes. '
            'In taxable accounts, any sold position with unrealised gain triggers capital gains — '
            'M4 models this. In registered accounts, rotation is clean. '
            'For LIF_TOM: eligible but proceeds must stay within the LIF (cannot withdraw).'
        ),
        'instruments': [
            ('sector_rotation', 'primary_action'),
        ],
    },
    {
        'code': 'covered_call_income',
        'name': 'Covered Call — Income Enhancement on Stable ETF',
        'sort_order': 90,
        'applicable_urgency_levels': 'Low,Medium',
        # Covered calls are an income strategy, not a crisis strategy.
        # Appropriate at Low/Medium urgency when upside can be capped.
        'applicable_regime_quadrants': 'Q1,Q2',
        'applicable_asset_types': 'etf_equity',
        'applicable_account_categories': 'Investment,Investment Joint',
        'min_unrealised_gain_usd': 0,
        'requires_options': True,
        'cef_eligible': False,
        'lif_rrif_eligible': False,
        'is_tax_triggering': True,
        # Premium received is taxable income.
        'description': (
            'Sell covered calls against a stable ETF position to generate additional income. '
            'Primary target: VDY in taxable accounts — a dividend-focused ETF where the '
            'investor is collecting dividends and would accept selling at a higher price. '
            'Set strike 10–15% above current price; collect 2–4% annual premium. '
            'Risk: if VDY rallies above the call strike, shares are called away — triggering '
            'a taxable disposition at the strike price. Not appropriate at High/Critical urgency '
            'because it caps the upside precisely when a recovery rally would be most valuable.'
        ),
        'instruments': [
            ('covered_call', 'income_generator'),
        ],
    },
]
```

#### 2.3 Seed Command Algorithm

```
For each instrument type in INSTRUMENT_TYPES:
    MERGE rm_instrument_type ON code = :code
    WHEN MATCHED: UPDATE all fields
    WHEN NOT MATCHED: INSERT

For each strategy template in STRATEGY_TEMPLATES:
    MERGE rm_strategy_template ON code = :code
    WHEN MATCHED: UPDATE all fields
    WHEN NOT MATCHED: INSERT
    
    Delete existing rm_strategy_instrument_link rows for this strategy
    For each (instrument_code, role) in template['instruments']:
        Lookup rm_instrument_type.id by code
        INSERT INTO rm_strategy_instrument_link

Print: X instrument types seeded, Y strategy templates seeded
```

---

### 3. Admin Registration

```python
from django.contrib import admin
from risk.models import InstrumentType, StrategyTemplate, StrategyInstrumentLink


class StrategyInstrumentLinkInline(admin.TabularInline):
    model = StrategyInstrumentLink
    extra = 1
    fields = ['instrument', 'role', 'sort_order']


@admin.register(InstrumentType)
class InstrumentTypeAdmin(admin.ModelAdmin):
    list_display = [
        'code', 'name',
        'eligible_tfsa', 'eligible_rrsp', 'eligible_lif', 'eligible_rrif', 'eligible_taxable',
        'requires_options_availability', 'wash_sale_applicable', 'is_active'
    ]
    list_filter = [
        'eligible_tfsa', 'eligible_taxable',
        'requires_options_availability', 'wash_sale_applicable', 'is_active'
    ]
    search_fields = ['code', 'name']
    ordering = ['sort_order', 'name']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = [
        ('Identity', {'fields': ['code', 'name', 'sort_order', 'is_active']}),
        ('Description', {'fields': ['mechanics_description', 'use_cases_json']}),
        ('Cost & Tax', {'fields': ['cost_structure_json', 'tax_treatment_json']}),
        ('Eligibility', {'fields': [
            'eligible_tfsa', 'eligible_rrsp', 'eligible_lif', 'eligible_rrif', 'eligible_taxable',
            'requires_options_availability', 'wash_sale_applicable'
        ]}),
        ('Metadata', {'fields': ['created_at', 'updated_at'], 'classes': ['collapse']}),
    ]


@admin.register(StrategyTemplate)
class StrategyTemplateAdmin(admin.ModelAdmin):
    list_display = [
        'code', 'name',
        'applicable_urgency_levels', 'applicable_regime_quadrants',
        'requires_options', 'cef_eligible', 'lif_rrif_eligible',
        'is_tax_triggering', 'is_active'
    ]
    list_filter = [
        'applicable_urgency_levels', 'applicable_regime_quadrants',
        'requires_options', 'cef_eligible', 'lif_rrif_eligible',
        'is_tax_triggering', 'is_active'
    ]
    search_fields = ['code', 'name', 'description']
    ordering = ['sort_order', 'name']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [StrategyInstrumentLinkInline]
    fieldsets = [
        ('Identity', {'fields': ['code', 'name', 'sort_order', 'is_active']}),
        ('Description', {'fields': ['description']}),
        ('Applicability Tags', {'fields': [
            'applicable_urgency_levels', 'applicable_regime_quadrants',
            'applicable_asset_types', 'applicable_account_categories',
            'min_unrealised_gain_usd'
        ]}),
        ('Constraints', {'fields': [
            'requires_options', 'cef_eligible', 'lif_rrif_eligible', 'is_tax_triggering'
        ]}),
        ('Metadata', {'fields': ['created_at', 'updated_at'], 'classes': ['collapse']}),
    ]
```

---

### 4. Helper Functions for M4

Create `risk/services/catalogue.py` — query helpers that M4 will call.

```python
def get_eligible_templates(
    asset_class: str,
    account_category: str,
    urgency_level: str,
    regime_quadrant: str,
    options_available: bool,
    is_cef: bool,
    is_lif_or_rrif: bool,
    unrealised_gain_usd: float
) -> list[StrategyTemplate]:
    """
    Return all active strategy templates eligible for this position.

    Filtering rules (all must pass):
    1. applicable_asset_types contains asset_class (or is a superset)
    2. applicable_account_categories contains account_category (or == 'ALL')
    3. applicable_urgency_levels contains urgency_level
    4. applicable_regime_quadrants contains regime_quadrant (or == 'ALL')
    5. If requires_options=True: options_available must be True
    6. If is_cef=True: cef_eligible must be True
    7. If is_lif_or_rrif=True: lif_rrif_eligible must be True
    8. unrealised_gain_usd >= template.min_unrealised_gain_usd
    9. is_active=True

    Returns ordered by sort_order.
    """

def get_instruments_for_template(template: StrategyTemplate) -> list[dict]:
    """
    Return the instrument types for a strategy template, including role.
    Returns list of {instrument: InstrumentType, role: str} dicts.
    """
```

---

### 5. Tests

File: `risk/tests/test_instrument_catalogue.py`

```python
from django.test import TestCase
from django.core.management import call_command
from risk.models import InstrumentType, StrategyTemplate, StrategyInstrumentLink
from risk.services.catalogue import get_eligible_templates


class SeedDataIntegrityTests(TestCase):

    def setUp(self):
        call_command('seed_instrument_catalogue', verbosity=0)

    def test_all_eight_instrument_types_seeded(self):
        expected_codes = {
            'protective_put', 'collar', 'covered_call', 'trailing_stop',
            'inverse_etf', 'sector_rotation', 'partial_liquidation', 'cash_raise'
        }
        actual_codes = set(InstrumentType.objects.values_list('code', flat=True))
        self.assertEqual(actual_codes, expected_codes)

    def test_all_eleven_strategy_templates_seeded(self):
        count = StrategyTemplate.objects.filter(is_active=True).count()
        self.assertGreaterEqual(count, 11)

    def test_every_template_has_at_least_one_instrument(self):
        for template in StrategyTemplate.objects.filter(is_active=True):
            link_count = StrategyInstrumentLink.objects.filter(strategy=template).count()
            self.assertGreater(link_count, 0,
                msg=f"Template '{template.code}' has no instrument links")

    def test_collar_has_two_instruments(self):
        collar = StrategyTemplate.objects.get(code='collar_high_gain_taxable_stock')
        links = StrategyInstrumentLink.objects.filter(strategy=collar)
        self.assertEqual(links.count(), 2)
        codes = set(links.values_list('instrument__code', flat=True))
        self.assertIn('protective_put', codes)
        self.assertIn('covered_call', codes)

    def test_cef_template_has_cef_eligible_true(self):
        cef_template = StrategyTemplate.objects.get(code='trailing_stop_cef')
        self.assertTrue(cef_template.cef_eligible)

    def test_options_instruments_not_eligible_in_registered_accounts(self):
        protective_put = InstrumentType.objects.get(code='protective_put')
        self.assertFalse(protective_put.eligible_tfsa)
        self.assertFalse(protective_put.eligible_rrsp)
        self.assertFalse(protective_put.eligible_lif)
        self.assertFalse(protective_put.eligible_rrif)
        self.assertTrue(protective_put.eligible_taxable)

    def test_partial_liquidation_not_eligible_in_lif(self):
        partial_liq = InstrumentType.objects.get(code='partial_liquidation')
        self.assertFalse(partial_liq.eligible_lif)

    def test_trailing_stop_not_eligible_in_lif(self):
        trailing = InstrumentType.objects.get(code='trailing_stop')
        self.assertFalse(trailing.eligible_lif)


class CatalogueQueryTests(TestCase):

    def setUp(self):
        call_command('seed_instrument_catalogue', verbosity=0)

    def test_aapl_ibsusan_high_urgency_returns_collar(self):
        # AAPL in IB_SUSAN: common_stock, taxable, High urgency, options available
        templates = get_eligible_templates(
            asset_class='common_stock',
            account_category='Investment',
            urgency_level='High',
            regime_quadrant='Q2',
            options_available=True,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=196000,
        )
        codes = [t.code for t in templates]
        self.assertIn('collar_high_gain_taxable_stock', codes)
        self.assertIn('protective_put_pretax_stock', codes)

    def test_adx_liftom_returns_only_trailing_stop(self):
        # ADX in LIF_TOM: CEF, LIF (locked-in), no options
        templates = get_eligible_templates(
            asset_class='cef',
            account_category='LIF',
            urgency_level='High',
            regime_quadrant='Q3',
            options_available=False,
            is_cef=True,
            is_lif_or_rrif=True,
            unrealised_gain_usd=7890,
        )
        codes = [t.code for t in templates]
        # Only trailing_stop_cef should appear — it's the only CEF+LIF-eligible template
        self.assertIn('trailing_stop_cef', codes)
        # Collar and partial_liquidation must NOT appear
        self.assertNotIn('collar_high_gain_taxable_stock', codes)
        self.assertNotIn('partial_liquidation_taxable', codes)

    def test_ibit_tfsa_critical_urgency_returns_trailing_stop(self):
        # IBIT in TFSA: crypto_etf, TFSA, Critical urgency, no options
        templates = get_eligible_templates(
            asset_class='crypto_etf',
            account_category='TFSA',
            urgency_level='Critical',
            regime_quadrant='Q3',
            options_available=False,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=0,
        )
        codes = [t.code for t in templates]
        self.assertIn('trailing_stop_registered', codes)
        # Options-based strategies must NOT appear (TFSA not eligible)
        self.assertNotIn('collar_high_gain_taxable_stock', codes)
        self.assertNotIn('protective_put_pretax_stock', codes)

    def test_taxable_partial_liquidation_only_at_critical(self):
        # The taxable partial_liquidation template requires Critical urgency
        medium_templates = get_eligible_templates(
            asset_class='etf_equity',
            account_category='Investment',
            urgency_level='Medium',
            regime_quadrant='Q1',
            options_available=False,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=100000,
        )
        critical_templates = get_eligible_templates(
            asset_class='etf_equity',
            account_category='Investment',
            urgency_level='Critical',
            regime_quadrant='Q1',
            options_available=False,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=100000,
        )
        medium_codes = [t.code for t in medium_templates]
        critical_codes = [t.code for t in critical_templates]
        self.assertNotIn('partial_liquidation_taxable', medium_codes)
        self.assertIn('partial_liquidation_taxable', critical_codes)

    def test_inverse_etf_templates_only_in_q3_q4(self):
        q1_templates = get_eligible_templates(
            asset_class='etf_equity',
            account_category='TFSA',
            urgency_level='High',
            regime_quadrant='Q1',
            options_available=False,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=0,
        )
        q3_templates = get_eligible_templates(
            asset_class='etf_equity',
            account_category='TFSA',
            urgency_level='High',
            regime_quadrant='Q3',
            options_available=False,
            is_cef=False,
            is_lif_or_rrif=False,
            unrealised_gain_usd=0,
        )
        q1_codes = [t.code for t in q1_templates]
        q3_codes = [t.code for t in q3_templates]
        self.assertNotIn('inverse_etf_semiconductor', q1_codes)
        self.assertNotIn('inverse_etf_nasdaq', q1_codes)
        self.assertIn('inverse_etf_semiconductor', q3_codes)
        self.assertIn('inverse_etf_nasdaq', q3_codes)

    def test_seed_is_idempotent(self):
        # Running seed twice should not create duplicates
        call_command('seed_instrument_catalogue', verbosity=0)
        call_command('seed_instrument_catalogue', verbosity=0)
        self.assertEqual(InstrumentType.objects.count(), 8)
        self.assertGreaterEqual(StrategyTemplate.objects.count(), 11)
```

Run with: python manage.py test risk.tests.test_instrument_catalogue --settings=portfolio_manager.settings_test

---

## Constraints

- DO NOT implement any calculation logic — that is M4
- DO NOT implement any UI views — that is M6
- DO NOT push to git (manual step)
- Seed command must be idempotent — safe to run multiple times (use MSSQL MERGE)
- All text fields in seed data must be valid JSON where noted — validate on seed
- PEP 8 throughout; full type hints on all helper functions

---

## Manual Steps Required (in order)

- [ ] Verify gate: SELECT COUNT(*) FROM rm_urgency_score WHERE calc_date='2026-06-02' — expect rows for all entity types
- [ ] python manage.py makemigrations risk
- [ ] python manage.py migrate --settings=portfolio_manager.settings_dev
- [ ] python manage.py seed_instrument_catalogue --settings=portfolio_manager.settings_dev
- [ ] Verify in SSMS: SELECT code, name, eligible_taxable, eligible_tfsa, eligible_lif FROM rm_instrument_type ORDER BY sort_order
- [ ] Verify in SSMS: SELECT code, name, applicable_urgency_levels, requires_options, cef_eligible, lif_rrif_eligible FROM rm_strategy_template ORDER BY sort_order
- [ ] Verify in Django admin: open Strategy Templates, confirm collar has two instrument links
- [ ] python manage.py test risk.tests.test_instrument_catalogue --settings=portfolio_manager.settings_test
- [ ] git add -A && git commit -m "feat: M3-Spec-1 instrument catalogue + strategy templates"
- [ ] git push origin feature/risk-m3-catalogue

---

## Human Approval Required

Review the catalogue seed data before proceeding to M4. Confirm all instrument eligibility flags match your broker capabilities — particularly which instruments are actually available in the registered accounts (RRSP, TFSA, etc.) through your IB setup.

---

M3-Spec-1 | Portfolio Manager Phase B | June 2026 | tdwdev
