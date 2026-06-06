"""
Management command: seed_instrument_catalogue

Seeds the instrument catalogue reference data:
  - 8 instrument types (rm_instrument_type)
  - 11 strategy templates (rm_strategy_template)
  - Instrument-to-template links (rm_strategy_instrument_link)

Safe to run repeatedly — upserts by code using Django's update_or_create.
Run after migrations.

Usage:
    python manage.py seed_instrument_catalogue --settings=portfolio_manager.settings_dev
"""

import json
from django.core.management.base import BaseCommand
from django.db import transaction
from risk.models import InstrumentType, StrategyTemplate, StrategyInstrumentLink


INSTRUMENT_TYPES = [
    {
        'code': 'protective_put',
        'name': 'Protective Put Option',
        'sort_order': 10,
        'requires_options_availability': True,
        'wash_sale_applicable': False,
        'eligible_tfsa': False,
        'eligible_rrsp': False,
        'eligible_lif': False,
        'eligible_rrif': False,
        'eligible_taxable': True,
        'mechanics_description': (
            'Purchase a put option giving the right to sell a held security at the strike '
            'price before expiry. Limits downside below the strike price while preserving '
            'upside participation. Cost is the option premium paid upfront.'
        ),
        'use_cases_json': json.dumps([
            'High unrealised gain in single stock',
            'Pre-earnings protection',
            'High urgency without wanting to sell',
        ]),
        'cost_structure_json': json.dumps({
            'type': 'premium',
            'typical_pct_of_notional_annual': 0.025,
            'bid_ask_spread_pct': 0.004,
            'commission_per_contract_usd': 1.50,
        }),
        'tax_treatment_json': json.dumps({
            'Taxable': {
                'tax_cost_on_exercise': False,
                'wash_sale_applicable': False,
                'notes': (
                    'Put premium is a capital cost; exercise does not itself '
                    'trigger sale of underlying'
                ),
            },
            'TFSA': {
                'tax_cost_on_exercise': False,
                'notes': 'Not available',
            },
        }),
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
            'Ideal for high-gain taxable positions where the goal is protection without '
            'triggering a sale.'
        ),
        'use_cases_json': json.dumps([
            'Very high unrealised gain taxable position',
            'Investor comfortable capping upside',
            'Reduce net hedging cost vs standalone put',
        ]),
        'cost_structure_json': json.dumps({
            'type': 'net_premium',
            'typical_net_cost_pct_of_notional': 0.005,
            'note': (
                'Call premium received offsets put premium paid; may be near zero-cost'
            ),
        }),
        'tax_treatment_json': json.dumps({
            'Taxable': {
                'tax_cost_on_exercise': False,
                'notes': (
                    'No sale triggered; call premium received is taxable income '
                    'in year received'
                ),
            },
        }),
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
            'price — effectively a sale at a predetermined price. Appropriate when the '
            'investor is willing to sell at the strike level and wants income in the meantime.'
        ),
        'use_cases_json': json.dumps([
            'Generate income on stagnant position',
            'VDY income enhancement',
            'Position where upside is acceptable to cap at strike',
        ]),
        'cost_structure_json': json.dumps({
            'type': 'income',
            'typical_premium_pct_annual': 0.04,
            'commission_per_contract_usd': 1.50,
        }),
        'tax_treatment_json': json.dumps({
            'Taxable': {
                'premium_taxable': True,
                'exercise_triggers_sale': True,
                'notes': (
                    'Premium income taxable in year received; exercise is a taxable '
                    'disposition'
                ),
            },
        }),
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
        'mechanics_description': (
            'A stop-loss order that trails the price upward but never moves downward. '
            'Set as a % or dollar amount below the current market price. If price drops '
            'by that amount from any subsequent high, position is sold at market. '
            'No cost until triggered (other than bid-ask on execution). Simple and effective '
            'for ETF positions where options are unnecessary or unavailable.'
        ),
        'use_cases_json': json.dumps([
            'Crypto ETF (IBIT, ETHA) in TFSA',
            'CEF position (ADX) with no options',
            'Any ETF at High or Critical urgency',
            'Low-cost protection in registered accounts',
        ]),
        'cost_structure_json': json.dumps({
            'type': 'friction_only',
            'bid_ask_spread_pct': 0.001,
            'commission_per_trade_usd': 1.00,
            'note': 'No premium; cost is only on execution',
        }),
        'tax_treatment_json': json.dumps({
            'Taxable': {
                'triggers_sale': True,
                'wash_sale_applicable': True,
                'notes': (
                    'Sale triggers capital gain/loss; repurchase within 30d is wash sale'
                ),
            },
            'TFSA': {
                'triggers_sale': True,
                'tax_cost': False,
            },
            'RRSP': {
                'triggers_sale': True,
                'tax_cost': False,
            },
        }),
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
            'E.g. PSQ (inverse QQQ) hedges Nasdaq concentration; SOXS (3x inverse '
            'semiconductors) hedges SOXX/SMH/NVDA. The hedge ratio is '
            'position_size / inverse_etf_notional. Note: leveraged inverse ETFs decay over '
            'time due to daily rebalancing — intended for short-term hedging (days to weeks), '
            'not long-term holds.'
        ),
        'use_cases_json': json.dumps([
            'Nasdaq concentration hedge via PSQ',
            'Semiconductor concentration hedge via SOXS',
            'S&P 500 hedge via SPXS or SH',
            'Bear market or Q3/Q4 regime protection',
        ]),
        'cost_structure_json': json.dumps({
            'type': 'management_fee_plus_decay',
            'typical_annual_decay_pct': 0.05,
            'bid_ask_spread_pct': 0.002,
            'note': (
                'Leveraged inverse ETFs have significant decay; hold period should be short'
            ),
        }),
        'tax_treatment_json': json.dumps({
            'Taxable': {
                'gain_on_close_taxable': True,
                'notes': (
                    'Closing a profitable inverse ETF position triggers capital gain'
                ),
            },
            'TFSA': {
                'gain_on_close_taxable': False,
            },
            'RRSP': {
                'gain_on_close_taxable': False,
            },
        }),
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
        'use_cases_json': json.dumps([
            'Q3 or Q4 regime — reduce cyclical, add defensive',
            'Semiconductor concentration above threshold',
            'Tech concentration with high urgency across multiple positions',
        ]),
        'cost_structure_json': json.dumps({
            'type': 'two_trade_friction',
            'bid_ask_spread_pct_sell': 0.001,
            'bid_ask_spread_pct_buy': 0.001,
            'commission_per_trade_usd': 1.00,
        }),
        'tax_treatment_json': json.dumps({
            'Taxable': {
                'sale_triggers_gain': True,
                'notes': (
                    'Reducing position triggers capital gain on unrealised amount sold'
                ),
            },
            'TFSA': {
                'sale_triggers_gain': False,
            },
            'RRSP': {
                'sale_triggers_gain': False,
            },
            'LIF': {
                'sale_triggers_gain': False,
                'notes': 'Locked-in; reinvestment within plan',
            },
        }),
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
        'mechanics_description': (
            'Sell a portion of a held position to reduce exposure. The most direct risk '
            'reduction available, but triggers a taxable event for any unrealised gain in '
            'taxable accounts. M4 models the tax cost explicitly and compares it to the '
            'risk reduction achieved. For TAXABLE accounts with large unrealised gains '
            '(AAPL, VFV), this should rank BELOW collar and trailing stop unless the '
            'urgency level is Critical.'
        ),
        'use_cases_json': json.dumps([
            'Critical urgency where other strategies are unavailable',
            'Tax-free accounts (TFSA) with high urgency',
            'Portfolio rebalancing to reduce concentration',
            'Raise cash for defensive positioning',
        ]),
        'cost_structure_json': json.dumps({
            'type': 'friction_only',
            'bid_ask_spread_pct': 0.001,
            'commission_per_trade_usd': 1.00,
        }),
        'tax_treatment_json': json.dumps({
            'Taxable': {
                'triggers_capital_gain': True,
                'wash_sale_applicable': True,
                'notes': (
                    '(proceeds - adjusted_cost_base) is taxable capital gain in Canada'
                ),
            },
            'TFSA': {
                'triggers_capital_gain': False,
                'notes': 'No tax consequence',
            },
            'RRSP': {
                'triggers_capital_gain': False,
                'notes': 'No tax consequence within plan',
            },
            'RRIF': {
                'triggers_capital_gain': False,
                'notes': (
                    'No tax on internal sale; withdrawals are taxed as income'
                ),
            },
        }),
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
            'Increase allocation to cash-equivalent instruments (SGOV, RBF2010, RBF2014, '
            'ICSH) by selling risk assets. Reduces portfolio beta and VaR directly. In '
            'taxable accounts, any sale of appreciated positions triggers capital gain. In '
            'tax-free accounts, this is the cleanest and most flexible defensive action '
            'available. Appropriate in Q3/Q4 regimes or when portfolio urgency is Critical.'
        ),
        'use_cases_json': json.dumps([
            'Q4 stagflation regime — maximum defensiveness',
            'Portfolio urgency Critical — reduce overall risk',
            'TFSA/RRSP accounts during bear market',
            'Raise liquidity ahead of anticipated volatility event',
        ]),
        'cost_structure_json': json.dumps({
            'type': 'friction_only',
            'bid_ask_spread_pct': 0.001,
            'commission_per_trade_usd': 1.00,
            'opportunity_cost': 'forgone equity returns',
        }),
        'tax_treatment_json': json.dumps({
            'Taxable': {
                'triggers_capital_gain': True,
                'notes': (
                    'Selling appreciated positions to raise cash triggers capital gain'
                ),
            },
            'TFSA': {
                'triggers_capital_gain': False,
            },
            'RRSP': {
                'triggers_capital_gain': False,
            },
        }),
    },
]


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
        'description': (
            'Buy a protective put and simultaneously sell a covered call on the same '
            'high-gain taxable single stock position. The call premium offsets the put cost, '
            'typically achieving near-zero-cost protection. Downside is floored at the put '
            'strike; upside is capped at the call strike. No taxable event unless the call '
            'is exercised. Primary use case: AAPL in IB_SUSAN ($196K+ unrealised gain) and '
            'MA in IB_JOINT. The system should model this as: buy 6-month ATM put, sell '
            'OTM call 10-15% above current price. Net cost estimate comes from Black-Scholes '
            'in M4.'
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
            'Full downside protection below the strike price with unlimited upside '
            'participation. Higher cost than a collar due to no call premium offset. '
            'Use when the investor wants to preserve all upside and can absorb the premium '
            'cost. Applicable to: AAPL, NVDA, GOOG, MA, V in taxable accounts.'
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
        'description': (
            'Set a trailing stop-loss order on an ETF position in a taxable account. '
            'Automatically exits the position if price drops by the trail amount from any '
            'subsequent high. Low cost; no premium; protection activates automatically. '
            'Important: triggering the stop crystallises a capital gain. M4 must model the '
            'tax cost and surface it prominently for positions with large unrealised gains '
            '(VFV $331K gain, VDY $195K gain in TAXABLE_TOM_SUSAN). Recommended trail: '
            '10-15% for volatile ETFs, 8-10% for stable dividend ETFs.'
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
            'on execution. The cleanest and lowest-cost risk reduction available in '
            'tax-sheltered accounts. Highly appropriate for crypto ETF positions (IBIT, '
            'ETHA in TFSA) given their high volatility. Recommended trail: 15-20% for '
            'IBIT/ETHA; 8-12% for equity ETFs.'
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
        'lif_rrif_eligible': True,
        'is_tax_triggering': True,
        'description': (
            'Trailing stop-loss on a closed-end fund position. This is the ONLY hedging '
            'instrument available for ADX, as options are not listed on closed-end funds. '
            'CEFs can trade at significant discounts or premiums to NAV; the stop should '
            'account for normal premium/discount volatility. Recommended trail: 12-15% to '
            'allow for NAV discount widening without premature exit. ADX is held in '
            'LIF_TOM (locked-in) and RRSP_TOM — LIF rules prevent full withdrawal so the '
            'stop must be set conservatively.'
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
            'Reduce position size in a tax-sheltered account (TFSA, RRSP, RRIF). No '
            'capital gains consequence. The most direct risk reduction available in '
            'registered accounts. Proceeds sold can be redeployed to SGOV or RBF for '
            'temporary defensive positioning. For RRIF accounts, be aware of minimum '
            'annual withdrawal requirements — selling a position is fine as long as the '
            'proceeds remain within the plan.'
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
            'sold portion. M4 models the full tax cost using the owner-provided marginal '
            'tax rate. This template only appears as a recommendation in taxable accounts '
            'when urgency is Critical AND other strategies (collar, protective put, trailing '
            'stop) are either unavailable or have already been applied. For positions with '
            'massive unrealised gains (AAPL: $200K+, VFV: $331K+), the tax cost typically '
            'exceeds the risk reduction value unless the urgency is extreme.'
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
            'semiconductor exposure across SOXX (TFSA_TOM, IB_TOM), SMH (TFSA_TOM), and '
            'NVDA (IB_TOM). The hedge ratio is: SOXS_notional = (total_semi_exposure_usd / '
            '3) for a full delta hedge, or (total_semi_exposure_usd / 6) for a half hedge. '
            'IMPORTANT: SOXS is a 3x leveraged product with significant daily decay. Set a '
            'maximum hold period of 30 days. This is a tactical, not structural, hedge. '
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
            'Purchase PSQ (ProShares Short QQQ, 1x inverse) to hedge Nasdaq-100 '
            'concentration across QQQ (IB_TOM, TFSA_TOM, TFSA_SUSAN) and IGM '
            '(TFSA_TOM, TFSA_SUSAN, IB_SUSAN). PSQ is 1x inverse (not leveraged) — lower '
            'decay than SOXS, suitable for longer hold periods (up to 90 days). Hedge ratio: '
            'PSQ_notional = total_nasdaq_exposure_usd for a full hedge, or 50% for a partial '
            'hedge. This effectively reduces Nasdaq beta toward zero across the QQQ/IGM '
            'positions.'
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
        'description': (
            'Trim cyclical or growth sector positions and rotate into dividend or defensive '
            'instruments. Sell targets: SOXX, SMH, NVDA, QQQ, IGM (high-beta tech/semi). '
            'Buy targets: CGDV, VDY, SGOV (dividend/cash — lower beta, more defensive). '
            'Most effective in Q3 (recession) and Q4 (stagflation) regimes. In taxable '
            'accounts, any sold position with unrealised gain triggers capital gains — M4 '
            'models this. In registered accounts, rotation is clean. For LIF_TOM: eligible '
            'but proceeds must stay within the LIF (cannot withdraw).'
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
        'applicable_regime_quadrants': 'Q1,Q2',
        'applicable_asset_types': 'etf_equity',
        'applicable_account_categories': 'Investment,Investment Joint',
        'min_unrealised_gain_usd': 0,
        'requires_options': True,
        'cef_eligible': False,
        'lif_rrif_eligible': False,
        'is_tax_triggering': True,
        'description': (
            'Sell covered calls against a stable ETF position to generate additional income. '
            'Primary target: VDY in taxable accounts — a dividend-focused ETF where the '
            'investor is collecting dividends and would accept selling at a higher price. '
            'Set strike 10-15% above current price; collect 2-4% annual premium. Risk: if '
            'VDY rallies above the call strike, shares are called away — triggering a '
            'taxable disposition at the strike price. Not appropriate at High/Critical '
            'urgency because it caps the upside precisely when a recovery rally would be '
            'most valuable.'
        ),
        'instruments': [
            ('covered_call', 'income_generator'),
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed instrument catalogue reference data (8 instrument types, 11 strategy templates)'

    def handle(self, *args, **options):
        verbosity = options.get('verbosity', 1)

        with transaction.atomic():
            # Seed instrument types
            created_instruments = 0
            for data in INSTRUMENT_TYPES:
                _, created = InstrumentType.objects.update_or_create(
                    code=data['code'],
                    defaults=data,
                )
                if created:
                    created_instruments += 1
                if verbosity > 0:
                    self.stdout.write(
                        f"  InstrumentType: {data['code']} "
                        f"{'created' if created else 'updated'}"
                    )

            # Seed strategy templates and links
            created_templates = 0
            for data in STRATEGY_TEMPLATES:
                instruments_data = data.get('instruments', [])
                template_defaults = {k: v for k, v in data.items() if k != 'instruments'}
                _, created = StrategyTemplate.objects.update_or_create(
                    code=data['code'],
                    defaults=template_defaults,
                )
                if created:
                    created_templates += 1

                # Clear existing links and recreate
                StrategyInstrumentLink.objects.filter(
                    strategy__code=data['code']
                ).delete()

                for idx, (instrument_code, role) in enumerate(instruments_data, start=1):
                    try:
                        instrument = InstrumentType.objects.get(code=instrument_code)
                    except InstrumentType.DoesNotExist:
                        if verbosity > 0:
                            self.stderr.write(
                                f"  WARNING: InstrumentType '{instrument_code}' "
                                f"not found for template '{data['code']}'"
                            )
                        continue
                    template = StrategyTemplate.objects.get(code=data['code'])
                    StrategyInstrumentLink.objects.create(
                        strategy=template,
                        instrument=instrument,
                        role=role,
                        sort_order=idx,
                    )

                if verbosity > 0:
                    self.stdout.write(
                        f"  StrategyTemplate: {data['code']} "
                        f"{'created' if created else 'updated'} "
                        f"({len(instruments_data)} instruments)"
                    )

        if verbosity > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nSeeded {created_instruments} instrument type(s), '
                    f'{created_templates} strategy template(s).'
                )
            )
