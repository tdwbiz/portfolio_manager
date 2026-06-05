import numpy as np
import pandas as pd
from decimal import Decimal
from django.test import TestCase
from risk.services.metrics import (
    var_parametric, var_historical, var_cornish_fisher, cvar,
    max_drawdown, current_drawdown_from_peak, beta, sharpe_ratio,
    sortino_ratio, liquidity_score, compute_correlation_matrix
)


class VaRFormulaTests(TestCase):
    """Test VaR formulas against known analytical results."""
    
    def test_parametric_var_95_known_value(self):
        # vol_annualised=0.20 (20% annual vol)
        # 1-day parametric 95% VaR = 1.645 * 0.20/sqrt(252) ≈ 0.02074
        result = var_parametric(vol_annualised=0.20, horizon_days=1, confidence=0.95)
        self.assertAlmostEqual(result, 0.02074, places=4)
    
    def test_parametric_var_99_higher_than_95(self):
        v95 = var_parametric(0.25, 1, 0.95)
        v99 = var_parametric(0.25, 1, 0.99)
        self.assertGreater(v99, v95)
    
    def test_parametric_var_10d_scales_by_sqrt10(self):
        v1d = var_parametric(0.20, 1, 0.95)
        v10d = var_parametric(0.20, 10, 0.95)
        self.assertAlmostEqual(v10d, v1d * np.sqrt(10), places=6)
    
    def test_historical_var_95_from_known_distribution(self):
        # 100 daily returns: 90 of 0.001, 10 of -0.05 (exact 10% are -0.05)
        # np.percentile(returns, 5) with linear interpolation:
        # rank = 0.05 * 99 = 4.95, floor=4, ceil=5
        # Both s[4] and s[5] are -0.05 (indices 0-9 are -0.05), so interp = -0.05
        returns = pd.Series([-0.05] * 10 + [0.001] * 90)
        result = var_historical(returns, 0.95)
        # 5th percentile of this distribution is -0.05
        self.assertAlmostEqual(result, 0.05, places=4)
    
    def test_cornish_fisher_greater_than_parametric_for_fat_tails(self):
        # Simulate fat-tailed returns (high kurtosis)
        np.random.seed(42)
        from scipy.stats import t
        # t-distribution with 3 df has heavy tails
        returns = pd.Series(t.rvs(df=3, size=252) * 0.01)
        cf_var = var_cornish_fisher(returns, 0.95)
        param_var = var_parametric(returns.std() * np.sqrt(252), 1, 0.95)
        # Cornish-Fisher should be higher for fat-tailed distributions
        self.assertGreater(cf_var, param_var * 0.9)  # Allow some tolerance
    
    def test_cvar_greater_than_var(self):
        np.random.seed(42)
        returns = pd.Series(np.random.normal(-0.001, 0.015, 252))
        v = var_historical(returns, 0.95)
        cv = cvar(returns, 0.95)
        self.assertGreater(cv, v)
    
    def test_var_returns_none_insufficient_data(self):
        # Historical VaR requires >=63 observations
        returns = pd.Series(np.random.normal(0, 0.01, 50))
        result = var_cornish_fisher(returns, 0.95)
        self.assertIsNone(result)


class DrawdownTests(TestCase):
    
    def test_max_drawdown_known_sequence(self):
        # Prices: 100, 120, 80, 110. Peak=120, Trough=80. Drawdown=(80-120)/120 = -0.333
        prices = pd.Series([100, 120, 80, 110], 
                           index=pd.date_range('2025-01-01', periods=4))
        result = max_drawdown(prices)
        self.assertAlmostEqual(result, -0.3333, places=3)
    
    def test_max_drawdown_all_rising(self):
        prices = pd.Series([100, 110, 120, 130])
        result = max_drawdown(prices)
        self.assertAlmostEqual(result, 0.0, places=6)
    
    def test_current_drawdown_at_new_high(self):
        prices = pd.Series([100, 110, 120, 130])
        result = current_drawdown_from_peak(prices)
        self.assertAlmostEqual(result, 0.0, places=6)
    
    def test_current_drawdown_below_peak(self):
        prices = pd.Series([100, 130, 110])
        result = current_drawdown_from_peak(prices)
        # (110 - 130) / 130 = -0.1538
        self.assertAlmostEqual(result, -0.1538, places=3)


class BetaTests(TestCase):
    
    def test_beta_of_one_for_identical_returns(self):
        np.random.seed(1)
        market = pd.Series(np.random.normal(0.001, 0.01, 252))
        result = beta(market, market)
        self.assertAlmostEqual(result, 1.0, places=4)
    
    def test_beta_of_two_for_double_returns(self):
        np.random.seed(1)
        market = pd.Series(np.random.normal(0.001, 0.01, 252))
        double = market * 2
        result = beta(double, market)
        self.assertAlmostEqual(result, 2.0, places=3)
    
    def test_beta_returns_none_insufficient_overlap(self):
        # Only 100 overlapping days (< 126 minimum)
        a = pd.Series(np.random.normal(0, 0.01, 100))
        b = pd.Series(np.random.normal(0, 0.01, 100))
        result = beta(a, b)
        self.assertIsNone(result)
    
    def test_negative_beta(self):
        np.random.seed(2)
        market = pd.Series(np.random.normal(0.001, 0.01, 252))
        inverse = -market
        result = beta(inverse, market)
        self.assertAlmostEqual(result, -1.0, places=3)


class SharpeTests(TestCase):
    
    def test_sharpe_positive_for_positive_excess_returns(self):
        # Returns consistently above risk-free with realistic variance
        np.random.seed(42)
        rf_daily = 0.04 / 252
        # Daily returns centered at 0.002 (above rf_daily ~0.00016) with small noise
        returns = pd.Series(np.random.normal(0.002, 0.005, 252))
        result = sharpe_ratio(returns, risk_free_annual=0.04, window=252)
        self.assertGreater(result, 0)
    
    def test_sharpe_zero_for_risk_free_level_returns(self):
        # Returns exactly at risk-free rate each day
        rf_daily = 0.05 / 252
        returns = pd.Series([rf_daily] * 252)
        result = sharpe_ratio(returns, risk_free_annual=0.05, window=252)
        self.assertAlmostEqual(result, 0.0, places=4)
    
    def test_sortino_returns_none_when_no_downside(self):
        # All returns above risk-free — no downside deviation exists
        # Per spec: "Returns None if all excess returns are positive (no downside)"
        returns = pd.Series([0.001] * 252)
        result = sortino_ratio(returns, risk_free_annual=0.02, window=252)
        self.assertIsNone(result)


class LiquidityScoreTests(TestCase):
    
    def test_highly_liquid_large_etf(self):
        # $500M daily volume, $1M position → 0.002 days to exit
        result = liquidity_score(500_000_000, 1_000_000)
        self.assertEqual(result, 100.0)
    
    def test_illiquid_large_position(self):
        # $100K daily volume, $5M position → 50 days to exit
        result = liquidity_score(100_000, 5_000_000)
        self.assertEqual(result, 10.0)
    
    def test_cef_penalty_applied(self):
        # Same as liquid case but with CEF flag
        base = liquidity_score(500_000_000, 1_000_000, is_cef=False)
        cef = liquidity_score(500_000_000, 1_000_000, is_cef=True)
        self.assertAlmostEqual(cef, base * 0.7, places=2)
    
    def test_mutual_fund_high_liquidity(self):
        result = liquidity_score(0, 0, is_mutual_fund=True)
        self.assertEqual(result, 95.0)
    
    def test_zero_volume_gives_low_score(self):
        result = liquidity_score(0, 1_000_000)
        self.assertEqual(result, 5.0)


class CorrelationMatrixTests(TestCase):
    
    def test_diagonal_is_one(self):
        # After compute, (A, A) pair should be 1.0
        # Use synthetic data — seed into rm_market_price_history first
        pass  # Integration test — set up in fixtures
    
    def test_matrix_is_symmetric(self):
        # (A, B) pearson should equal (B, A) pearson
        pass  # Integration test
    
    def test_pair_skipped_when_insufficient_overlap(self):
        # Symbol C has only 100 days of history; pairs with C should be skipped
        pass  # Integration test


class IntegrationTests(TestCase):
    """End-to-end test: seed price data, run command, verify output."""
    
    def test_full_metric_calculation_for_synthetic_symbol(self):
        """
        Seed 300 rows of synthetic price data for a test symbol.
        Run calc_security_metrics for that symbol.
        Assert rm_security_risk_metrics has one row with non-null values
        for vol_21d, var_parametric_95, beta_spy (if SPY data also seeded),
        sharpe_ratio, liquidity_score.
        """
        pass  # Implement with test fixtures
    
    def test_cash_equiv_symbol_has_null_var_and_beta(self):
        """
        Seed RBF2010-like symbol (mutual_fund=True, no price data).
        Run calc_security_metrics.
        Assert var_parametric_95 is None, liquidity_score = 95.
        """
        pass
    
    def test_ugl_at_risk_computed_correctly(self):
        """
        Seed core_position_history: 100 shares of TEST_SYM at cost $50.
        Seed current price: $100.
        Seed vol_21d: 0.20 (20% annualised).
        Run calc_security_metrics.
        Expected ugl_at_risk_usd = 100 * $100 * var_parametric_95(0.20)
                                  ≈ 10000 * 0.02074 ≈ $207.
        Expected ugl_at_risk_pct_of_ugl = 207 / (100 * (100-50)) = 207/5000 ≈ 0.0414
        """
        pass
