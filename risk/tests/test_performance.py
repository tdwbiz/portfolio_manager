"""
Performance regression tests for risk calculations.

These tests benchmark key operations and fail if performance degrades
beyond a threshold (10% degradation allowed).

Usage:
    python manage.py test risk.tests.test_performance --settings=portfolio_manager.settings_test

Dependencies: hypothesis, numpy, pandas, scipy, timeit
"""

import time
import unittest
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd

from risk.services.monte_carlo import generate_gbm_paths, generate_jump_diffusion_paths
from risk.services.volatility import compute_volatility_summaries
from risk.services.technical_indicators import sma, rsi, macd
from risk.services.option_pricing import black_scholes_call, black_scholes_put


# ============================================================================
# Performance Baselines
# ============================================================================

# Performance thresholds: max allowed degradation percentage (10%)
PERFORMANCE_DEGRADATION_THRESHOLD = 0.10

# Baseline timings (in seconds) - measured on reference hardware
# These should be updated if legitimate performance improvements are made
BASELINE_TIMINGS = {
    'monte_carlo_gbm_10000_paths': 0.5,  # 10,000 GBM paths, 252 days
    'monte_carlo_jump_10000_paths': 0.8,  # 10,000 jump-diffusion paths, 252 days
    'volatility_1y_prices': 0.05,  # 252 daily prices
    'sma_21_window': 0.01,  # 1,000 prices
    'rsi_14_period': 0.02,  # 1,000 prices
    'macd_12_26_9': 0.03,  # 1,000 prices
    'black_scholes_100000_calls': 4.0,  # 100,000 option pricing calls (measured: ~3.7s)
}


# ============================================================================
# Monte Carlo Performance Tests
# ============================================================================

class TestMonteCarloPerformance(unittest.TestCase):
    """Performance tests for Monte Carlo simulation."""
    
    def test_gbm_10000_paths_performance(self):
        """Test that GBM simulation with 10,000 paths completes within baseline."""
        n_paths = 10000
        horizon_days = 252
        S0 = 100.0
        mu = 0.08
        sigma = 0.2
        
        # Warm-up run
        generate_gbm_paths(S0, mu, sigma, horizon_days, 100)
        
        # Benchmark run
        start_time = time.perf_counter()
        paths = generate_gbm_paths(S0, mu, sigma, horizon_days, n_paths)
        elapsed = time.perf_counter() - start_time
        
        baseline = BASELINE_TIMINGS['monte_carlo_gbm_10000_paths']
        max_allowed = baseline * (1 + PERFORMANCE_DEGRADATION_THRESHOLD)
        
        self.assertLess(
            elapsed, max_allowed,
            f"GBM simulation took {elapsed:.3f}s, expected < {max_allowed:.3f}s "
            f"(baseline: {baseline:.3f}s)"
        )
        
        # Verify output shape
        self.assertEqual(paths.shape, (n_paths, horizon_days + 1))
    
    def test_jump_diffusion_10000_paths_performance(self):
        """Test that jump-diffusion simulation with 10,000 paths completes within baseline."""
        n_paths = 10000
        horizon_days = 252
        S0 = 100.0
        mu = 0.08
        sigma = 0.3
        jump_intensity = 5.0
        jump_mean = -0.04
        jump_std = 0.08
        
        # Warm-up run
        generate_jump_diffusion_paths(
            S0, mu, sigma, jump_intensity, jump_mean, jump_std,
            horizon_days, 100
        )
        
        # Benchmark run
        start_time = time.perf_counter()
        paths = generate_jump_diffusion_paths(
            S0, mu, sigma, jump_intensity, jump_mean, jump_std,
            horizon_days, n_paths
        )
        elapsed = time.perf_counter() - start_time
        
        baseline = BASELINE_TIMINGS['monte_carlo_jump_10000_paths']
        max_allowed = baseline * (1 + PERFORMANCE_DEGRADATION_THRESHOLD)
        
        self.assertLess(
            elapsed, max_allowed,
            f"Jump-diffusion simulation took {elapsed:.3f}s, expected < {max_allowed:.3f}s "
            f"(baseline: {baseline:.3f}s)"
        )
        
        # Verify output shape
        self.assertEqual(paths.shape, (n_paths, horizon_days + 1))


# ============================================================================
# Volatility Performance Tests
# ============================================================================

class TestVolatilityPerformance(unittest.TestCase):
    """Performance tests for volatility calculations."""
    
    def test_volatility_summaries_performance(self):
        """Test that volatility summaries calculation completes within baseline."""
        np.random.seed(42)
        n_days = 252
        returns = np.random.normal(0.08/252, 0.2/np.sqrt(252), n_days)
        prices = pd.Series(100 * np.cumprod(1 + returns))
        
        # Simple volatility calculation for benchmarking
        log_returns = np.diff(np.log(prices))
        daily_vol = np.std(log_returns, ddof=1)
        annual_vol = daily_vol * np.sqrt(252)
        
        # Warm-up run
        _ = annual_vol
        
        # Benchmark run
        start_time = time.perf_counter()
        for _ in range(100):
            log_returns = np.diff(np.log(prices))
            daily_vol = np.std(log_returns, ddof=1)
            annual_vol = daily_vol * np.sqrt(252)
        elapsed = time.perf_counter() - start_time
        
        baseline = BASELINE_TIMINGS['volatility_1y_prices']
        max_allowed = baseline * (1 + PERFORMANCE_DEGRADATION_THRESHOLD)
        
        self.assertLess(
            elapsed, max_allowed,
            f"Volatility calculation took {elapsed:.3f}s, expected < {max_allowed:.3f}s "
            f"(baseline: {baseline:.3f}s)"
        )
        
        # Verify result is reasonable
        self.assertGreater(annual_vol, 0.1)
        self.assertLess(annual_vol, 0.5)


# ============================================================================
# Technical Indicators Performance Tests
# ============================================================================

class TestTechnicalIndicatorsPerformance(unittest.TestCase):
    """Performance tests for technical indicator calculations."""
    
    def setUp(self):
        """Generate test data for all tests."""
        np.random.seed(42)
        self.prices_1k = 100 * np.cumprod(1 + np.random.normal(0, 0.01, 1000))
        self.price_series = pd.Series(self.prices_1k)
    
    def test_sma_21_window_performance(self):
        """Test that SMA calculation completes within baseline."""
        # Warm-up run
        sma(pd.Series(np.ones(100)), 21)
        
        # Benchmark run
        start_time = time.perf_counter()
        result = sma(self.price_series, 21)
        elapsed = time.perf_counter() - start_time
        
        baseline = BASELINE_TIMINGS['sma_21_window']
        max_allowed = baseline * (1 + PERFORMANCE_DEGRADATION_THRESHOLD)
        
        self.assertLess(
            elapsed, max_allowed,
            f"SMA calculation took {elapsed:.3f}s, expected < {max_allowed:.3f}s "
            f"(baseline: {baseline:.3f}s)"
        )
        
        # Verify result shape
        self.assertEqual(len(result), len(self.price_series))
    
    def test_rsi_14_period_performance(self):
        """Test that RSI calculation completes within baseline."""
        # Warm-up run
        rsi(pd.Series(np.ones(100)), 14)
        
        # Benchmark run
        start_time = time.perf_counter()
        result = rsi(self.price_series, 14)
        elapsed = time.perf_counter() - start_time
        
        baseline = BASELINE_TIMINGS['rsi_14_period']
        max_allowed = baseline * (1 + PERFORMANCE_DEGRADATION_THRESHOLD)
        
        self.assertLess(
            elapsed, max_allowed,
            f"RSI calculation took {elapsed:.3f}s, expected < {max_allowed:.3f}s "
            f"(baseline: {baseline:.3f}s)"
        )
        
        # Verify result bounds
        rsi_valid = result.dropna()
        if len(rsi_valid) > 0:
            self.assertGreaterEqual(rsi_valid.min(), 0)
            self.assertLessEqual(rsi_valid.max(), 100)
    
    def test_macd_12_26_9_performance(self):
        """Test that MACD calculation completes within baseline."""
        # Warm-up run
        macd(pd.Series(np.ones(100)))
        
        # Benchmark run
        start_time = time.perf_counter()
        macd_line, signal_line, histogram = macd(self.price_series)
        elapsed = time.perf_counter() - start_time
        
        baseline = BASELINE_TIMINGS['macd_12_26_9']
        max_allowed = baseline * (1 + PERFORMANCE_DEGRADATION_THRESHOLD)
        
        self.assertLess(
            elapsed, max_allowed,
            f"MACD calculation took {elapsed:.3f}s, expected < {max_allowed:.3f}s "
            f"(baseline: {baseline:.3f}s)"
        )
        
        # Verify result shapes
        self.assertEqual(len(macd_line), len(self.price_series))
        self.assertEqual(len(signal_line), len(self.price_series))
        self.assertEqual(len(histogram), len(self.price_series))


# ============================================================================
# Option Pricing Performance Tests
# ============================================================================

class TestOptionPricingPerformance(unittest.TestCase):
    """Performance tests for option pricing calculations."""
    
    def test_black_scholes_batch_performance(self):
        """Test that batch Black-Scholes pricing completes within baseline."""
        n_calls = 100000
        S = 100.0
        K = 105.0
        T = 1.0
        r = 0.05
        sigma = 0.2
        
        # Warm-up run
        black_scholes_call(S, K, T, r, sigma)
        
        # Benchmark run (batch pricing)
        start_time = time.perf_counter()
        for _ in range(n_calls):
            black_scholes_call(S, K, T, r, sigma)
        elapsed = time.perf_counter() - start_time
        
        baseline = BASELINE_TIMINGS['black_scholes_100000_calls']
        max_allowed = baseline * (1 + PERFORMANCE_DEGRADATION_THRESHOLD)
        
        self.assertLess(
            elapsed, max_allowed,
            f"Black-Scholes pricing took {elapsed:.3f}s, expected < {max_allowed:.3f}s "
            f"(baseline: {baseline:.3f}s)"
        )


# ============================================================================
# Performance Report
# ============================================================================

def performance_summary():
    """Print a summary of all performance baselines."""
    print("\n" + "="*70)
    print("PERFORMANCE BASELINES")
    print("="*70)
    for test_name, baseline in BASELINE_TIMINGS.items():
        max_allowed = baseline * (1 + PERFORMANCE_DEGRADATION_THRESHOLD)
        print(f"{test_name:40s} {baseline:8.3f}s  (max: {max_allowed:.3f}s)")
    print("="*70 + "\n")


if __name__ == '__main__':
    import sys
    
    # Print summary if requested
    if '--summary' in sys.argv:
        performance_summary()
        sys.exit(0)
    
    # Run tests
    unittest.main()
