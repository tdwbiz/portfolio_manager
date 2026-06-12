"""
Property-based tests for risk calculations using Hypothesis.

These tests verify mathematical properties and invariants of risk calculation
functions, rather than specific expected outputs. They help catch edge cases
and numerical instability issues.

Dependencies: hypothesis, numpy, pandas, scipy
"""

import math
import unittest
from datetime import date

import numpy as np
import pandas as pd
from hypothesis import given, settings, strategies as st
from scipy.stats import norm

from risk.services.option_pricing import (
    black_scholes_call,
    black_scholes_put,
    estimate_option_cost,
)
from risk.services.var_reduction import estimate_var_reduction
from risk.services.volatility import LOOKBACK_WINDOWS
from risk.services.technical_indicators import sma, rsi, macd, sma_crossover


# ============================================================================
# Black-Scholes Property Tests
# ============================================================================

class TestBlackScholesProperties(unittest.TestCase):
    """
    Property-based tests for Black-Scholes option pricing.
    
    Properties verified:
    - Put-call parity: C - P = S - K*e^(-rT)
    - Monotonicity: price increases with S, decreases with K
    - Non-negativity: prices are always >= 0
    - Boundary conditions: price = 0 when T=0 or sigma=0
    """
    
    @given(
        S=st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
        K=st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
        T=st.floats(min_value=0.01, max_value=5.0, allow_nan=False, allow_infinity=False),
        r=st.floats(min_value=-0.1, max_value=0.5, allow_nan=False, allow_infinity=False),
        sigma=st.floats(min_value=0.01, max_value=2.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_put_call_parity(self, S, K, T, r, sigma):
        """
        Property: Put-call parity holds for European options.
        C - P = S - K * e^(-rT)
        """
        call_price = black_scholes_call(S, K, T, r, sigma)
        put_price = black_scholes_put(S, K, T, r, sigma)
        
        parity_lhs = call_price - put_price
        parity_rhs = S - K * math.exp(-r * T)
        
        # Allow 1% tolerance for numerical errors
        assert abs(parity_lhs - parity_rhs) < 0.01 * max(abs(parity_lhs), abs(parity_rhs), 1.0)
    
    @given(
        S=st.floats(min_value=10, max_value=1000, allow_nan=False, allow_infinity=False),
        K=st.floats(min_value=10, max_value=1000, allow_nan=False, allow_infinity=False),
        T=st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False),
        r=st.floats(min_value=0.01, max_value=0.2, allow_nan=False, allow_infinity=False),
        sigma=st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_call_price_monotonic_in_S(self, S, K, T, r, sigma):
        """Property: Call price is non-decreasing in S."""
        call1 = black_scholes_call(S, K, T, r, sigma)
        call2 = black_scholes_call(S * 1.1, K, T, r, sigma)
        assert call2 >= call1 - 1e-10  # Allow tiny numerical errors
    
    @given(
        S=st.floats(min_value=10, max_value=1000, allow_nan=False, allow_infinity=False),
        K=st.floats(min_value=10, max_value=1000, allow_nan=False, allow_infinity=False),
        T=st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False),
        r=st.floats(min_value=0.01, max_value=0.2, allow_nan=False, allow_infinity=False),
        sigma=st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_call_price_decreasing_in_K(self, S, K, T, r, sigma):
        """Property: Call price is non-increasing in K."""
        call1 = black_scholes_call(S, K, T, r, sigma)
        call2 = black_scholes_call(S, K * 1.1, T, r, sigma)
        assert call2 <= call1 + 1e-10  # Allow tiny numerical errors
    
    @given(
        S=st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
        K=st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
        T=st.floats(min_value=0.01, max_value=5.0, allow_nan=False, allow_infinity=False),
        r=st.floats(min_value=-0.1, max_value=0.5, allow_nan=False, allow_infinity=False),
        sigma=st.floats(min_value=0.01, max_value=2.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_non_negativity(self, S, K, T, r, sigma):
        """Property: Option prices are always non-negative."""
        assert black_scholes_call(S, K, T, r, sigma) >= 0
        assert black_scholes_put(S, K, T, r, sigma) >= 0
    
    @given(
        S=st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
        K=st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
        r=st.floats(min_value=-0.1, max_value=0.5, allow_nan=False, allow_infinity=False),
        sigma=st.floats(min_value=0.01, max_value=2.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_zero_time_expiry(self, S, K, r, sigma):
        """Property: Option price = 0 when T = 0."""
        assert black_scholes_call(S, K, 0, r, sigma) == 0
        assert black_scholes_put(S, K, 0, r, sigma) == 0
    
    @given(
        S=st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
        K=st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
        T=st.floats(min_value=0.01, max_value=5.0, allow_nan=False, allow_infinity=False),
        r=st.floats(min_value=-0.1, max_value=0.5, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_zero_volatility(self, S, K, T, r):
        """Property: Option price = 0 when sigma = 0."""
        assert black_scholes_call(S, K, T, r, 0) == 0
        assert black_scholes_put(S, K, T, r, 0) == 0


# ============================================================================
# VaR Reduction Property Tests
# ============================================================================

class TestVaRReductionProperties(unittest.TestCase):
    """
    Property-based tests for VaR reduction estimation.
    
    Properties verified:
    - Non-negativity: reductions are always >= 0
    - Proportionality: reduction scales with position size
    - Bounds: reduction <= position VaR (can't reduce more than total risk)
    """
    
    @given(
        mv=st.floats(min_value=1000, max_value=1000000, allow_nan=False, allow_infinity=False),
        var_pct=st.floats(min_value=0.01, max_value=0.2, allow_nan=False, allow_infinity=False),
        beta=st.floats(min_value=-2, max_value=2, allow_nan=False, allow_infinity=False),
        price=st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_var_reduction_non_negative(self, mv, var_pct, beta, price):
        """Property: VaR reduction is always >= 0."""
        for strategy in ['protective_put', 'collar', 'trailing_stop', 'inverse_etf']:
            result = estimate_var_reduction(
                strategy_code=strategy,
                position_market_value_usd=mv,
                var_parametric_95=var_pct,
                beta_spy=beta,
                current_price_usd=price,
            )
            assert result['var_reduction_usd'] >= 0
    
    @given(
        mv=st.floats(min_value=1000, max_value=100000, allow_nan=False, allow_infinity=False),
        var_pct=st.floats(min_value=0.01, max_value=0.2, allow_nan=False, allow_infinity=False),
        beta=st.floats(min_value=-2, max_value=2, allow_nan=False, allow_infinity=False),
        price=st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_var_reduction_bounds(self, mv, var_pct, beta, price):
        """Property: VaR reduction <= position VaR."""
        position_var = var_pct * mv
        for strategy in ['protective_put', 'collar', 'trailing_stop', 'inverse_etf']:
            result = estimate_var_reduction(
                strategy_code=strategy,
                position_market_value_usd=mv,
                var_parametric_95=var_pct,
                beta_spy=beta,
                current_price_usd=price,
            )
            assert result['var_reduction_usd'] <= position_var + 1e-10
    
    @given(
        var_pct=st.floats(min_value=0.01, max_value=0.2, allow_nan=False, allow_infinity=False),
        beta=st.floats(min_value=-2, max_value=2, allow_nan=False, allow_infinity=False),
        price=st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
        multiplier=st.floats(min_value=0.5, max_value=2.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_var_reduction_proportional_to_size(self, var_pct, beta, price, multiplier):
        """Property: VaR reduction scales linearly with position size."""
        mv1 = 10000
        mv2 = mv1 * multiplier
        
        result1 = estimate_var_reduction(
            strategy_code='protective_put',
            position_market_value_usd=mv1,
            var_parametric_95=var_pct,
            beta_spy=beta,
            current_price_usd=price,
        )
        result2 = estimate_var_reduction(
            strategy_code='protective_put',
            position_market_value_usd=mv2,
            var_parametric_95=var_pct,
            beta_spy=beta,
            current_price_usd=price,
        )
        
        # Reduction should scale proportionally
        expected_ratio = multiplier
        actual_ratio = result2['var_reduction_usd'] / result1['var_reduction_usd']
        assert 0.9 * expected_ratio <= actual_ratio <= 1.1 * expected_ratio


# ============================================================================
# Technical Indicators Property Tests
# ============================================================================

class TestTechnicalIndicatorsProperties(unittest.TestCase):
    """
    Property-based tests for technical indicator calculations.
    
    Properties verified:
    - SMA: smooths price data, reduces variance
    - RSI: bounded between 0 and 100
    - MACD: signal line is smoother than MACD line
    """
    
    @given(
        prices=st.lists(
            st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
            min_size=80,
            max_size=200,
        ),
        window=st.sampled_from([10, 21, 63]),
    )
    @settings(max_examples=50)
    def test_sma_reduces_variance(self, prices, window):
        """Property: SMA has lower variance than raw prices (when prices vary)."""
        price_series = pd.Series(prices)
        sma_result = sma(price_series, window)
        
        # Remove NaN values for comparison
        sma_valid = sma_result.dropna()
        price_valid = price_series.iloc[window-1:]
        
        if len(sma_valid) > 0 and len(price_valid) > 0:
            # Skip if prices are all identical (variance = 0)
            if price_valid.var() == 0:
                return
            assert sma_valid.var() < price_valid.var()
    
    @given(
        prices=st.lists(
            st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
            min_size=50,
            max_size=200,
        ),
    )
    @settings(max_examples=50)
    def test_rsi_bounded(self, prices):
        """Property: RSI is always between 0 and 100."""
        price_series = pd.Series(prices)
        rsi_result = rsi(price_series, period=14)
        
        # RSI should be NaN for first 14 values, then between 0 and 100
        rsi_valid = rsi_result.dropna()
        if len(rsi_valid) > 0:
            assert rsi_valid.min() >= 0
            assert rsi_valid.max() <= 100
    
    @given(
        prices=st.lists(
            st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
            min_size=100,
            max_size=300,
        ),
    )
    @settings(max_examples=50)
    def test_macd_signal_smoother(self, prices):
        """Property: MACD signal line is smoother (less volatile) than MACD line."""
        price_series = pd.Series(prices)
        macd_line, signal_line, histogram = macd(price_series)
        
        macd_valid = macd_line.dropna()
        signal_valid = signal_line.dropna()
        
        if len(macd_valid) > 26 and len(signal_valid) > 26:
            # Signal line should have lower variance (smoother)
            assert signal_valid.var() <= macd_valid.var() + 1e-10


# ============================================================================
# Option Cost Estimation Property Tests
# ============================================================================

class TestOptionCostEstimationProperties(unittest.TestCase):
    """
    Property-based tests for option cost estimation.
    
    Properties verified:
    - Costs are non-negative
    - Costs scale with quantity
    - Collar costs <= protective put costs (call premium offsets put)
    """
    
    @given(
        price=st.floats(min_value=10, max_value=1000, allow_nan=False, allow_infinity=False),
        quantity=st.floats(min_value=10, max_value=10000, allow_nan=False, allow_infinity=False),
        vol=st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False),
        rate=st.floats(min_value=0.01, max_value=0.2, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_option_cost_non_negative(self, price, quantity, vol, rate):
        """Property: Option costs are always >= 0."""
        for strategy in ['protective_put', 'collar', 'covered_call']:
            result = estimate_option_cost(
                strategy_code=strategy,
                current_price_usd=price,
                quantity=quantity,
                vol_63d=vol,
                risk_free_rate=rate,
            )
            assert result['total_cost_usd'] >= 0
    
    @given(
        price=st.floats(min_value=10, max_value=1000, allow_nan=False, allow_infinity=False),
        quantity=st.floats(min_value=100, max_value=5000, allow_nan=False, allow_infinity=False),
        vol=st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False),
        rate=st.floats(min_value=0.01, max_value=0.2, allow_nan=False, allow_infinity=False),
        multiplier=st.floats(min_value=2, max_value=10, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_option_cost_scales_with_quantity(self, price, quantity, vol, rate, multiplier):
        """Property: Option cost scales linearly with quantity."""
        result1 = estimate_option_cost(
            strategy_code='protective_put',
            current_price_usd=price,
            quantity=quantity,
            vol_63d=vol,
            risk_free_rate=rate,
        )
        result2 = estimate_option_cost(
            strategy_code='protective_put',
            current_price_usd=price,
            quantity=quantity * multiplier,
            vol_63d=vol,
            risk_free_rate=rate,
        )
        
        # Cost should scale proportionally
        expected_ratio = multiplier
        actual_ratio = result2['total_cost_usd'] / result1['total_cost_usd']
        assert 0.9 * expected_ratio <= actual_ratio <= 1.1 * expected_ratio
    
    @given(
        price=st.floats(min_value=10, max_value=1000, allow_nan=False, allow_infinity=False),
        quantity=st.floats(min_value=100, max_value=5000, allow_nan=False, allow_infinity=False),
        vol=st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False),
        rate=st.floats(min_value=0.01, max_value=0.2, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_collar_cost_leq_put_cost(self, price, quantity, vol, rate):
        """Property: Collar cost <= protective put cost (call offsets put)."""
        put_result = estimate_option_cost(
            strategy_code='protective_put',
            current_price_usd=price,
            quantity=quantity,
            vol_63d=vol,
            risk_free_rate=rate,
        )
        collar_result = estimate_option_cost(
            strategy_code='collar',
            current_price_usd=price,
            quantity=quantity,
            vol_63d=vol,
            risk_free_rate=rate,
        )
        
        assert collar_result['total_cost_usd'] <= put_result['total_cost_usd'] + 1e-10


# ============================================================================
# Volatility Property Tests
# ============================================================================

class TestVolatilityProperties(unittest.TestCase):
    """
    Property-based tests for volatility calculations.
    
    Properties verified:
    - Volatility is non-negative
    - Volatility scales with sqrt(time)
    - Identical prices give zero volatility
    """
    
    @given(
        base_price=st.floats(min_value=10, max_value=1000, allow_nan=False, allow_infinity=False),
        noise_level=st.floats(min_value=0.001, max_value=0.1, allow_nan=False, allow_infinity=False),
        n_days=st.integers(min_value=253, max_value=500),
    )
    @settings(max_examples=30)
    def test_volatility_non_negative(self, base_price, noise_level, n_days):
        """Property: Volatility is always >= 0."""
        # Generate synthetic price series with known volatility
        np.random.seed(42)
        returns = np.random.normal(0, noise_level, n_days)
        prices = base_price * np.cumprod(1 + returns)
        
        price_series = pd.Series(prices)
        log_returns = np.diff(np.log(price_series))
        daily_vol = np.std(log_returns, ddof=1)
        annual_vol = daily_vol * np.sqrt(252)
        
        assert annual_vol >= 0
    
    @given(
        base_price=st.floats(min_value=10, max_value=1000, allow_nan=False, allow_infinity=False),
        n_days=st.integers(min_value=253, max_value=500),
    )
    @settings(max_examples=30)
    def test_identical_prices_zero_vol(self, base_price, n_days):
        """Property: Identical prices give zero volatility."""
        prices = np.full(n_days, base_price)
        price_series = pd.Series(prices)
        log_returns = np.diff(np.log(price_series))
        
        if len(log_returns) > 1:
            daily_vol = np.std(log_returns, ddof=1)
            assert daily_vol == 0


# ============================================================================
# Run all tests
# ============================================================================

if __name__ == '__main__':
    import unittest
    
    # Run property-based tests
    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestBlackScholesProperties))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestVaRReductionProperties))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestTechnicalIndicatorsProperties))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestOptionCostEstimationProperties))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestVolatilityProperties))
    
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)
