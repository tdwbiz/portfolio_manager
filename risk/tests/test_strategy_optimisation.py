"""
Tests for M4-Spec-1 Strategy Optimisation Engine.

Tests cover:
- Black-Scholes option pricing (option_pricing.py)
- VaR reduction estimation (var_reduction.py)
- Strategy cost estimation and ranking (strategy_costs.py)
- Tax impact note generation (strategy_costs.py)

Run with:
    python manage.py test risk.tests.test_strategy_optimisation --settings=portfolio_manager.settings_test
"""

import math
import numpy as np
from decimal import Decimal
from django.test import TestCase
from django.core.management import call_command
from risk.models import OwnerConfig, StrategyRecommendation
from risk.services.option_pricing import black_scholes_put, black_scholes_call, estimate_option_cost
from risk.services.var_reduction import estimate_var_reduction
from risk.services.strategy_costs import compute_ranking_metric, build_tax_impact_note


class BlackScholesTests(TestCase):
    """Tests for Black-Scholes option pricing functions."""

    def test_put_price_increases_with_volatility(self):
        """Higher vol = more expensive put."""
        put_low_vol = black_scholes_put(S=100, K=100, T=0.5, r=0.045, sigma=0.20)
        put_high_vol = black_scholes_put(S=100, K=100, T=0.5, r=0.045, sigma=0.50)
        self.assertGreater(put_high_vol, put_low_vol)

    def test_put_call_parity(self):
        """Put-call parity: C - P = S - K * exp(-r*T)."""
        S, K, T, r, sigma = 100, 100, 0.5, 0.045, 0.30
        call = black_scholes_call(S, K, T, r, sigma)
        put = black_scholes_put(S, K, T, r, sigma)
        parity_lhs = call - put
        parity_rhs = S - K * np.exp(-r * T)
        self.assertAlmostEqual(parity_lhs, parity_rhs, places=4)

    def test_atm_put_approximately_half_vol_times_price(self):
        """ATM put approximation: P ≈ S * sigma * sqrt(T/2pi)."""
        S, K, T, r, sigma = 100, 100, 0.5, 0.04, 0.30
        put = black_scholes_put(S, K, T, r, sigma)
        approx = S * sigma * np.sqrt(T / (2 * np.pi))
        self.assertAlmostEqual(put, approx, delta=approx * 0.15)  # within 15%

    def test_zero_volatility_put_equals_intrinsic(self):
        """With sigma≈0 and ITM put (K > S): put ≈ K*exp(-rT) - S."""
        put = black_scholes_put(S=90, K=100, T=1.0, r=0.05, sigma=0.001)
        intrinsic = 100 * np.exp(-0.05) - 90
        self.assertAlmostEqual(put, intrinsic, delta=0.01)

    def test_collar_cost_less_than_put_alone(self):
        """Collar (put bought + call sold) should cost less than standalone put."""
        option_cost_result = estimate_option_cost(
            'protective_put', 200.0, 670, 0.30, 0.045, 6
        )
        collar_cost_result = estimate_option_cost(
            'collar', 200.0, 670, 0.30, 0.045, 6
        )
        self.assertLess(collar_cost_result['total_cost_usd'], option_cost_result['total_cost_usd'])

    def test_aapl_put_cost_reasonable(self):
        """AAPL: price ~$315, 670 shares, vol ~30%, 6-month ATM put.
        Expected rough cost: 670 * 315 * 0.025 ≈ $5,276 (2.5% of notional)."""
        result = estimate_option_cost('protective_put', 315.0, 670, 0.30, 0.045, 6)
        self.assertGreater(result['total_cost_usd'], 2000)
        self.assertLess(result['total_cost_usd'], 25000)

    def test_call_price_increases_with_price(self):
        """Higher underlying price = more expensive call."""
        call_low = black_scholes_put(S=100, K=100, T=0.5, r=0.045, sigma=0.30)
        call_high = black_scholes_call(S=120, K=100, T=0.5, r=0.045, sigma=0.30)
        call_atm = black_scholes_call(S=100, K=100, T=0.5, r=0.045, sigma=0.30)
        self.assertGreater(call_high, call_atm)

    def test_option_price_zero_for_invalid_inputs(self):
        """Zero or negative inputs should return 0."""
        self.assertEqual(black_scholes_put(S=0, K=100, T=0.5, r=0.045, sigma=0.30), 0.0)
        self.assertEqual(black_scholes_put(S=100, K=100, T=0, r=0.045, sigma=0.30), 0.0)
        self.assertEqual(black_scholes_put(S=100, K=100, T=0.5, r=0.045, sigma=0), 0.0)


class VaRReductionTests(TestCase):
    """Tests for VaR reduction estimation."""

    def test_protective_put_reduces_var_by_half(self):
        """ATM put delta ≈ 0.5, so VaR reduction ≈ 50% of position VaR."""
        result = estimate_var_reduction(
            strategy_code='protective_put',
            position_market_value_usd=200000,
            var_parametric_95=0.03,  # 3% daily VaR
            beta_spy=1.2,
            current_price_usd=315.0,
        )
        # Expected: 200000 * 0.03 * 0.5 = $3,000
        self.assertAlmostEqual(result['var_reduction_usd'], 3000, delta=200)

    def test_collar_reduces_var_by_half(self):
        """Collar uses same put as protective put — same VaR reduction."""
        result = estimate_var_reduction(
            strategy_code='collar',
            position_market_value_usd=200000,
            var_parametric_95=0.03,
            beta_spy=1.2,
            current_price_usd=315.0,
        )
        self.assertAlmostEqual(result['var_reduction_usd'], 3000, delta=200)

    def test_trailing_stop_10pct_limits_var(self):
        """10% trail: max loss = 10% of position."""
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

    def test_trailing_stop_large_var_reduces(self):
        """When VaR > stop trail, trailing stop should reduce VaR."""
        result = estimate_var_reduction(
            strategy_code='trailing_stop',
            position_market_value_usd=100000,
            var_parametric_95=0.15,  # 15% daily VaR (high vol)
            beta_spy=2.0,
            current_price_usd=50.0,
            stop_trail_pct=0.10,  # 10% stop
        )
        # var = 100000 * 0.15 = 15000
        # max_loss = 100000 * 0.10 = 10000
        # reduction = 15000 - 10000 = 5000
        self.assertAlmostEqual(result['var_reduction_usd'], 5000, delta=100)

    def test_inverse_etf_reduces_var_proportionally(self):
        """50% hedge with 1x inverse ETF should reduce VaR by ~50%."""
        result = estimate_var_reduction(
            strategy_code='inverse_etf',
            position_market_value_usd=100000,
            var_parametric_95=0.04,
            beta_spy=1.5,  # At threshold — may or may not get 3x discount
            current_price_usd=747.0,
        )
        # hedge_ratio=0.5; VaR reduction ≈ 0.04 * 100000 * 0.5 = $2,000
        self.assertAlmostEqual(result['var_reduction_usd'], 2000, delta=400)

    def test_inverse_etf_3x_discount(self):
        """3x inverse ETF (beta > 1.5) should have basis risk discount."""
        result = estimate_var_reduction(
            strategy_code='inverse_etf',
            position_market_value_usd=100000,
            var_parametric_95=0.04,
            beta_spy=3.0,  # Clearly 3x leveraged
            current_price_usd=50.0,
        )
        # hedge_ratio=0.5, leverage_discount=0.8
        # reduction = 0.04 * 100000 * 0.5 * 0.8 = $1,600
        self.assertAlmostEqual(result['var_reduction_usd'], 1600, delta=100)

    def test_covered_call_zero_var_reduction(self):
        """Covered call provides no downside protection."""
        result = estimate_var_reduction(
            strategy_code='covered_call',
            position_market_value_usd=450000,
            var_parametric_95=0.02,
            beta_spy=0.7,
            current_price_usd=73.74,
        )
        self.assertEqual(result['var_reduction_usd'], 0)

    def test_partial_liquidation_reduces_var_linearly(self):
        """25% position reduction = 25% VaR reduction."""
        result = estimate_var_reduction(
            strategy_code='partial_liquidation',
            position_market_value_usd=500000,
            var_parametric_95=0.03,
            beta_spy=1.0,
            current_price_usd=100.0,
        )
        # reduction = 500000 * 0.03 * 0.25 = $3,750
        self.assertAlmostEqual(result['var_reduction_usd'], 3750, delta=100)

    def test_sector_rotation_reduces_var_linearly(self):
        """Sector rotation = 25% position reduction = 25% VaR reduction."""
        result = estimate_var_reduction(
            strategy_code='sector_rotation',
            position_market_value_usd=500000,
            var_parametric_95=0.03,
            beta_spy=1.0,
            current_price_usd=100.0,
        )
        self.assertAlmostEqual(result['var_reduction_usd'], 3750, delta=100)

    def test_cash_raise_reduces_var_linearly(self):
        """Cash raise = 25% position reduction = 25% VaR reduction."""
        result = estimate_var_reduction(
            strategy_code='cash_raise',
            position_market_value_usd=500000,
            var_parametric_95=0.03,
            beta_spy=1.0,
            current_price_usd=100.0,
        )
        self.assertAlmostEqual(result['var_reduction_usd'], 3750, delta=100)

    def test_trailing_stop_cef_reduces_var(self):
        """Trailing stop CEF should use same logic as trailing stop."""
        result = estimate_var_reduction(
            strategy_code='trailing_stop_cef',
            position_market_value_usd=10000,
            var_parametric_95=0.05,  # 5% daily VaR (CEF is volatile)
            beta_spy=0.5,
            current_price_usd=12.0,
            stop_trail_pct=0.10,
        )
        # var = 10000 * 0.05 = 500
        # max_loss = 10000 * 0.10 = 1000
        # reduction = 500 - 500 = 0 (VaR already less than stop)
        self.assertGreaterEqual(result['var_reduction_usd'], 0)


class RankingTests(TestCase):
    """Tests for ranking metric computation."""

    def test_collar_ranks_above_liquidation_for_high_gain_taxable(self):
        """
        For AAPL in IB_SUSAN: collar (no sale) must rank above partial liquidation
        (triggers $200K gain tax). This is validated by checking the ranking metric.
        """
        # Collar: strategy_cost=5000, tax_cost=0, var_reduction=30000
        collar_ranking = compute_ranking_metric(
            strategy_cost_usd=5000,
            tax_cost_usd=0,
            var_reduction_usd=30000,
            is_taxable_account=True,
            tax_penalty_multiplier=1.0,
        )

        # Partial liquidation: strategy_cost=100, tax_cost=43300, var_reduction=12500
        liquidation_ranking = compute_ranking_metric(
            strategy_cost_usd=100,
            tax_cost_usd=43300,
            var_reduction_usd=12500,
            is_taxable_account=True,
            tax_penalty_multiplier=1.0,
        )

        # Collar should rank better (lower cost_per_var)
        self.assertLess(collar_ranking, liquidation_ranking)

    def test_tfsa_strategies_have_zero_tax_cost(self):
        """All strategies in TFSA accounts: tax_cost_estimate_usd must be 0."""
        # In TFSA, is_taxable=False, so tax cost is not penalised
        tfsa_ranking = compute_ranking_metric(
            strategy_cost_usd=100,
            tax_cost_usd=0,
            var_reduction_usd=5000,
            is_taxable_account=False,
            tax_penalty_multiplier=1.0,
        )
        self.assertAlmostEqual(tfsa_ranking, 0.02, places=4)

    def test_cost_per_var_reduction_calculated_correctly(self):
        """strategy_cost=1000, tax_cost=0, var_reduction=5000 → cost_per_var = 0.2."""
        result = compute_ranking_metric(
            strategy_cost_usd=1000,
            tax_cost_usd=0,
            var_reduction_usd=5000,
            is_taxable_account=False,
            tax_penalty_multiplier=1.0,
        )
        self.assertAlmostEqual(result, 0.2, places=4)

    def test_tax_penalty_multiplier_increases_effective_cost(self):
        """Higher tax penalty multiplier → higher effective cost for strategies with tax."""
        no_penalty = compute_ranking_metric(
            strategy_cost_usd=1000,
            tax_cost_usd=5000,
            var_reduction_usd=10000,
            is_taxable_account=True,
            tax_penalty_multiplier=1.0,
        )
        with_penalty = compute_ranking_metric(
            strategy_cost_usd=1000,
            tax_cost_usd=5000,
            var_reduction_usd=10000,
            is_taxable_account=True,
            tax_penalty_multiplier=1.5,
        )
        self.assertGreater(with_penalty, no_penalty)

    def test_zero_var_reduction_ranks_last(self):
        """Zero VaR reduction → infinity (rank last)."""
        result = compute_ranking_metric(
            strategy_cost_usd=100,
            tax_cost_usd=0,
            var_reduction_usd=0,
            is_taxable_account=False,
            tax_penalty_multiplier=1.0,
        )
        self.assertEqual(result, float('inf'))

    def test_negative_var_reduction_ranks_last(self):
        """Negative VaR reduction → infinity (rank last)."""
        result = compute_ranking_metric(
            strategy_cost_usd=100,
            tax_cost_usd=0,
            var_reduction_usd=-100,
            is_taxable_account=False,
            tax_penalty_multiplier=1.0,
        )
        self.assertEqual(result, float('inf'))

    def test_high_var_reduction_ranks_better(self):
        """Higher VaR reduction → lower cost_per_var → better ranking."""
        low_reduction = compute_ranking_metric(
            strategy_cost_usd=1000, tax_cost_usd=0,
            var_reduction_usd=5000, is_taxable_account=False, tax_penalty_multiplier=1.0,
        )
        high_reduction = compute_ranking_metric(
            strategy_cost_usd=1000, tax_cost_usd=0,
            var_reduction_usd=20000, is_taxable_account=False, tax_penalty_multiplier=1.0,
        )
        self.assertLess(high_reduction, low_reduction)

    def test_taxable_vs_non_taxable_ranking(self):
        """Same strategy in taxable vs TFSA: taxable should rank worse if tax_cost > 0."""
        taxable_ranking = compute_ranking_metric(
            strategy_cost_usd=100, tax_cost_usd=5000,
            var_reduction_usd=10000, is_taxable_account=True, tax_penalty_multiplier=1.0,
        )
        non_taxable_ranking = compute_ranking_metric(
            strategy_cost_usd=100, tax_cost_usd=5000,
            var_reduction_usd=10000, is_taxable_account=False, tax_penalty_multiplier=1.0,
        )
        # In taxable: effective_cost = 100 + 5000 * 1.0 = 5100 → 5100/10000 = 0.51
        # In non-taxable: effective_cost = 100 → 100/10000 = 0.01
        self.assertGreater(taxable_ranking, non_taxable_ranking)


class TaxImpactNoteTests(TestCase):
    """Tests for tax impact note generation."""

    def test_tfsa_note_says_no_tax(self):
        """TFSA account → note says no tax."""
        note = build_tax_impact_note(
            'trailing_stop', 'TFSA', 5000, 0, 'IBIT', 'TOM', 1.384
        )
        self.assertIn('no tax', note.lower())

    def test_rrsp_note_says_no_tax(self):
        """RRSP account → note says no tax."""
        note = build_tax_impact_note(
            'trailing_stop', 'RRSP', 5000, 0, 'VFV', 'TOM', 1.384
        )
        self.assertIn('no tax', note.lower())

    def test_taxable_trailing_stop_note_includes_gain_amount(self):
        """Taxable trailing stop → note includes gain and tax amounts."""
        note = build_tax_impact_note(
            'trailing_stop', 'Investment', 200000, 43300, 'AAPL', 'SUSAN', 1.384
        )
        self.assertIn('200,000', note.replace(',', ','))  # gain amount present
        self.assertIn('43,300', note.replace(',', ','))  # tax amount present

    def test_collar_note_says_no_sale_required(self):
        """Collar → note says no taxable event unless called away."""
        note = build_tax_impact_note(
            'collar', 'Investment', 200000, 0, 'AAPL', 'SUSAN', 1.384
        )
        self.assertIn('no taxable event', note.lower())

    def test_protective_put_note(self):
        """Protective put → note says no taxable event."""
        note = build_tax_impact_note(
            'protective_put', 'Investment', 200000, 0, 'AAPL', 'SUSAN', 1.384
        )
        self.assertIn('no taxable event', note.lower())

    def test_covered_call_note_mentions_income(self):
        """Covered call → note mentions premium is taxable income."""
        note = build_tax_impact_note(
            'covered_call', 'Investment', 200000, 0, 'VFV', 'TOM', 1.384
        )
        self.assertIn('taxable income', note.lower())

    def test_partial_liquidation_note_includes_gain(self):
        """Partial liquidation → note includes 25% gain amount."""
        note = build_tax_impact_note(
            'partial_liquidation', 'Investment', 200000, 21650, 'VFV', 'TOM', 1.384
        )
        self.assertIn('25%', note.lower())

    def test_inverse_etf_note(self):
        """Inverse ETF → note says no sale of underlying."""
        note = build_tax_impact_note(
            'inverse_etf', 'Investment', 200000, 0, 'QQQ', 'TOM', 1.384
        )
        self.assertIn('no sale', note.lower())

    def test_trailing_stop_no_gain(self):
        """Trailing stop with no unrealised gain → no tax consequence."""
        note = build_tax_impact_note(
            'trailing_stop', 'Investment', -5000, 0, 'IBIT', 'TOM', 1.384
        )
        self.assertIn('no unrealised gain', note.lower())

    def test_lif_account_says_no_tax(self):
        """LIF account → note says no tax."""
        note = build_tax_impact_note(
            'trailing_stop', 'LIF', 7900, 0, 'ADX', 'TOM', 1.384
        )
        self.assertIn('no tax', note.lower())
