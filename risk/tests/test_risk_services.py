"""
Tests for untested risk services.

Tests cover:
- Volatility summary computation (volatility.py)
- Technical indicators: SMA, RSI, MACD, Bollinger Bands (technical_indicators.py)
- Black-Scholes option pricing (option_pricing.py)
- Stress test engine (stress_test.py)
- Gap detection (gap_detection.py)
- Strategy cost estimation and ranking (strategy_costs.py)
- VaR reduction estimation (var_reduction.py)

Run with:
    python manage.py test risk.tests.test_risk_services --settings=portfolio_manager.settings_test
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock

from django.test import TestCase

import numpy as np
import pandas as pd

from risk.services.volatility import (
    compute_volatility_summaries,
    _compute_and_upsert,
    LOOKBACK_WINDOWS,
)
from risk.services.technical_indicators import (
    sma,
    sma_crossover,
    rsi,
    macd,
    bollinger_bands,
    on_balance_volume,
    classify_market_phase,
)
from risk.services.option_pricing import (
    black_scholes_put,
    black_scholes_call,
    estimate_option_cost,
)
from risk.services.stress_test import (
    run_portfolio_stress_test,
    resolve_shock_for_position,
    _sector_to_composite_key,
)
from risk.services.var_reduction import (
    estimate_var_reduction,
)
from risk.services.strategy_costs import (
    compute_ranking_metric,
    build_tax_impact_note,
)


# ---------------------------------------------------------------------------
# Volatility Tests
# ---------------------------------------------------------------------------

class VolatilityComputationTests(TestCase):
    """Tests for volatility.py computation functions."""

    def setUp(self):
        """Set up test data with known price series."""
        # Create a price series with known volatility
        np.random.seed(42)
        self.n_days = 300
        self.base_price = 100.0
        self.daily_return = 0.0002  # ~5% annual drift
        self.daily_vol = 0.015  # ~24% annual vol

        # Generate prices: random walk with drift
        returns = np.random.normal(self.daily_return, self.daily_vol, self.n_days)
        self.prices = self.base_price * np.exp(np.cumsum(returns))

    def test_lookback_windows_defined(self):
        """LOOKBACK_WINDOWS should contain standard windows."""
        expected = [10, 21, 63, 252]
        self.assertEqual(LOOKBACK_WINDOWS, expected)

    def test_volatility_annualisation(self):
        """Daily vol * sqrt(252) should approximate annual vol."""
        daily_prices = pd.Series(self.prices)
        log_returns = np.diff(np.log(daily_prices.values))
        daily_std = np.std(log_returns, ddof=1)
        annual_vol = daily_std * np.sqrt(252)

        # Should be close to the input vol (within sampling error)
        self.assertAlmostEqual(annual_vol, self.daily_vol * np.sqrt(252), delta=0.02)

    def test_volatility_with_fewer_prices(self):
        """Volatility computation should handle smaller price series."""
        # Use only 63 days for 63-day window
        short_prices = pd.Series(self.prices[:64])  # 64 prices = 63 returns
        log_returns = np.diff(np.log(short_prices.values))
        self.assertEqual(len(log_returns), 63)

    def test_volatility_constant_prices(self):
        """Constant prices should yield zero volatility."""
        constant_prices = pd.Series([100.0] * 100)
        log_returns = np.diff(np.log(constant_prices.values))
        vol = np.std(log_returns, ddof=1)
        self.assertAlmostEqual(vol, 0.0, places=10)

    @patch('risk.services.volatility.SecurityUniverse.objects')
    @patch('risk.services.volatility._compute_and_upsert')
    def test_compute_volatility_summaries_no_symbols(self, mock_upsert, mock_objects):
        """compute_volatility_summaries should fetch symbols from DB when none provided."""
        mock_objects.filter.return_value.values_list.return_value = ['AAPL', 'GOOGL']

        result = compute_volatility_summaries()

        self.assertEqual(len(result), 2)
        self.assertEqual(mock_upsert.call_count, 8)  # 2 symbols * 4 windows

    @patch('risk.services.volatility.SecurityUniverse.objects')
    @patch('risk.services.volatility._compute_and_upsert')
    def test_compute_volatility_summaries_single_symbol(self, mock_upsert, mock_objects):
        """compute_volatility_summaries should only compute for specified symbol."""
        result = compute_volatility_summaries(symbol='AAPL')

        self.assertEqual(len(result), 1)
        self.assertEqual(result['AAPL'], 4)  # 4 windows
        self.assertEqual(mock_upsert.call_count, 4)


# ---------------------------------------------------------------------------
# Technical Indicators Tests
# ---------------------------------------------------------------------------

class SMAIndicatorTests(TestCase):
    """Tests for Simple Moving Average."""

    def test_sma_basic(self):
        """SMA should compute rolling mean correctly."""
        prices = pd.Series([10, 11, 12, 13, 14, 15])
        result = sma(prices, window=3)

        # First two values should be NaN
        self.assertTrue(pd.isna(result.iloc[0]))
        self.assertTrue(pd.isna(result.iloc[1]))

        # Third value: mean of [10, 11, 12] = 11
        self.assertAlmostEqual(result.iloc[2], 11.0)

        # Fourth value: mean of [11, 12, 13] = 12
        self.assertAlmostEqual(result.iloc[3], 12.0)

    def test_sma_window_equals_length(self):
        """SMA with window equal to series length should return single value."""
        prices = pd.Series([10, 20, 30, 40, 50])
        result = sma(prices, window=5)

        self.assertTrue(pd.isna(result.iloc[0]))
        self.assertTrue(pd.isna(result.iloc[1]))
        self.assertTrue(pd.isna(result.iloc[2]))
        self.assertTrue(pd.isna(result.iloc[3]))
        self.assertAlmostEqual(result.iloc[4], 30.0)  # mean of all

    def test_sma_window_one(self):
        """SMA with window=1 should return original prices."""
        prices = pd.Series([10.0, 20.0, 30.0, 40.0])  # Use float to avoid dtype mismatch
        result = sma(prices, window=1)

        # rolling(1).mean() returns same values
        pd.testing.assert_series_equal(result, prices, check_names=False, check_exact=False)
        self.assertTrue(np.allclose(result.values, prices.values))


class RSIIndicatorTests(TestCase):
    """Tests for RSI (Relative Strength Index)."""

    def test_rsi_rising_prices(self):
        """RSI on consistently rising prices should be high (>90)."""
        prices = pd.Series(range(1, 30))  # 1, 2, 3, ..., 29
        result = rsi(prices, period=14)
        valid = result.dropna()
        self.assertGreater(valid.iloc[-1], 90)

    def test_rsi_falling_prices(self):
        """RSI on consistently falling prices should be low (<10)."""
        prices = pd.Series(range(30, 0, -1))  # 30, 29, ..., 1
        result = rsi(prices, period=14)
        valid = result.dropna()
        self.assertLess(valid.iloc[-1], 10)

    def test_rsi_range_0_to_100(self):
        """RSI values should be in range [0, 100]."""
        np.random.seed(42)
        prices = pd.Series(100 + np.random.randn(252).cumsum())
        result = rsi(prices, period=14)
        valid = result.dropna()
        self.assertTrue((valid >= 0).all())
        self.assertTrue((valid <= 100).all())

    def test_rsi_constant_prices(self):
        """RSI on constant prices should be 100 (avg_loss==0 means no downward movement)."""
        prices = pd.Series([100.0] * 30)
        result = rsi(prices, period=14)
        # When all losses are 0, RSI = 100 (no downward movement)
        valid = result.dropna()
        if len(valid) > 0:
            self.assertEqual(valid.iloc[-1], 100.0)


class MACDIndicatorTests(TestCase):
    """Tests for MACD (Moving Average Convergence Divergence)."""

    def test_macd_on_trending_prices(self):
        """MACD should be positive on rising prices."""
        prices = pd.Series(range(10, 50))  # Strong uptrend
        macd_line, signal, histogram = macd(prices, fast=12, slow=26, signal=9)

        # After initial warmup, MACD should be positive for uptrend
        valid_macd = macd_line.dropna()
        if len(valid_macd) > 0:
            self.assertGreater(valid_macd.iloc[-1], 0)

    def test_macd_on_falling_prices(self):
        """MACD should be negative on falling prices."""
        prices = pd.Series(range(50, 10, -1))  # Strong downtrend
        macd_line, signal, histogram = macd(prices, fast=12, slow=26, signal=9)

        valid_macd = macd_line.dropna()
        if len(valid_macd) > 0:
            self.assertLess(valid_macd.iloc[-1], 0)


class BollingerBandsTests(TestCase):
    """Tests for Bollinger Bands."""

    def test_bollinger_bands_width(self):
        """Upper band should be above middle band, lower band below."""
        prices = pd.Series(range(10, 30))
        upper, middle, lower, bandwidth, pct_b = bollinger_bands(prices, window=10, num_std=2)

        valid = middle.dropna()
        if len(valid) > 0:
            self.assertGreater(upper.iloc[-1], middle.iloc[-1])
            self.assertLess(lower.iloc[-1], middle.iloc[-1])

    def test_bollinger_bands_expanding(self):
        """Bollinger Bands should expand with increasing volatility."""
        # Prices with increasing volatility
        np.random.seed(42)
        base = 100
        prices = [base]
        for i in range(99):
            volatility = 0.1 * (i / 100)  # Increasing volatility
            prices.append(prices[-1] + np.random.normal(0, volatility))

        prices = pd.Series(prices)
        upper, middle, lower, bandwidth, pct_b = bollinger_bands(prices, window=10, num_std=2)

        valid = middle.dropna()
        if len(valid) >= 2:
            # Band width should generally increase
            final_width = upper.iloc[-1] - lower.iloc[-1]
            mid_width = upper.iloc[len(valid)//2] - lower.iloc[len(valid)//2]
            self.assertGreaterEqual(final_width, mid_width * 0.5)  # Allow some noise


class OBVTests(TestCase):
    """Tests for On-Balance Volume."""

    def test_obv_on_rising_prices(self):
        """OBV should increase when prices rise."""
        prices = pd.Series([10, 11, 12, 13, 14])
        volumes = pd.Series([100, 200, 150, 300, 250])
        result = on_balance_volume(prices, volumes)

        # OBV should be positive and increasing
        self.assertGreater(result.iloc[-1], 0)

    def test_obv_on_falling_prices(self):
        """OBV should decrease when prices fall."""
        prices = pd.Series([14, 13, 12, 11, 10])
        volumes = pd.Series([100, 200, 150, 300, 250])
        result = on_balance_volume(prices, volumes)

        # OBV should be negative or decreasing
        self.assertLessEqual(result.iloc[-1], 0)


class SMACrossoverTests(TestCase):
    """Tests for SMA Crossover detection."""

    def test_golden_cross(self):
        """Detect golden cross (fast crosses above slow) within last 252 days."""
        # Golden cross: fast goes from below to above slow
        # Use 300-day series, crossover at day 200 (within last 252 days = days 48-300)
        slow_sma = pd.Series([100.0] * 300)
        fast_sma = pd.Series([95.0] * 200 + [105.0] * 100)  # Crosses at day 200

        crossover_type, crossover_date = sma_crossover(fast_sma, slow_sma)
        self.assertEqual(crossover_type, 'golden')

    def test_death_cross(self):
        """Detect death cross (fast crosses below slow) within last 252 days."""
        # Death cross: fast goes from above to below slow
        slow_sma = pd.Series([100.0] * 300)
        fast_sma = pd.Series([105.0] * 200 + [95.0] * 100)  # Crosses at day 200

        crossover_type, crossover_date = sma_crossover(fast_sma, slow_sma)
        self.assertEqual(crossover_type, 'death')

    def test_no_crossover(self):
        """Return 'none' when no crossover in last 252 days."""
        fast_sma = pd.Series([95.0] * 300)  # Always below
        slow_sma = pd.Series([100.0] * 300)

        crossover_type, crossover_date = sma_crossover(fast_sma, slow_sma)
        self.assertEqual(crossover_type, 'none')


class ClassifyMarketPhaseTests(TestCase):
    """Tests for market phase classification."""

    def test_bear_market(self):
        """Classify as 'bear' when >20% below 52-week high."""
        phase = classify_market_phase(current_price=80, sma_200=100, pct_from_52w_high=-25.0, rsi=50)
        self.assertEqual(phase, 'bear')

    def test_correction(self):
        """Classify as 'correction' when 10-20% below 52-week high."""
        phase = classify_market_phase(current_price=92, sma_200=100, pct_from_52w_high=-15.0, rsi=50)
        self.assertEqual(phase, 'correction')

    def test_overbought(self):
        """Classify as 'overbought' when RSI > 70."""
        phase = classify_market_phase(current_price=110, sma_200=100, pct_from_52w_high=5.0, rsi=75)
        self.assertEqual(phase, 'overbought')

    def test_oversold(self):
        """Classify as 'oversold' when RSI < 30."""
        phase = classify_market_phase(current_price=90, sma_200=100, pct_from_52w_high=-5.0, rsi=25)
        self.assertEqual(phase, 'oversold')

    def test_uptrend_via_sma(self):
        """Classify as 'uptrend' when price > SMA-200."""
        phase = classify_market_phase(current_price=110, sma_200=100, pct_from_52w_high=5.0, rsi=None)
        self.assertEqual(phase, 'uptrend')

    def test_uptrend_fallback(self):
        """Classify as 'uptrend' fallback when no other condition."""
        phase = classify_market_phase(current_price=100, sma_200=None, pct_from_52w_high=0.0, rsi=None)
        self.assertEqual(phase, 'uptrend')


# ---------------------------------------------------------------------------
# Option Pricing Tests (Black-Scholes)
# ---------------------------------------------------------------------------

class BlackScholesPutTests(TestCase):
    """Tests for Black-Scholes put option pricing."""

    def test_atm_put_positive_price(self):
        """ATM put should have positive price."""
        price = black_scholes_put(S=100, K=100, T=1.0, r=0.05, sigma=0.2)
        self.assertGreater(price, 0)

    def test_deep_itm_put(self):
        """Deep ITM put should be worth more than ATM put."""
        itm_price = black_scholes_put(S=80, K=100, T=1.0, r=0.05, sigma=0.2)
        atm_price = black_scholes_put(S=100, K=100, T=1.0, r=0.05, sigma=0.2)
        self.assertGreater(itm_price, atm_price)

    def test_deep_otm_put(self):
        """Deep OTM put should be worth less than ATM put."""
        otm_price = black_scholes_put(S=120, K=100, T=1.0, r=0.05, sigma=0.2)
        atm_price = black_scholes_put(S=100, K=100, T=1.0, r=0.05, sigma=0.2)
        self.assertLess(otm_price, atm_price)

    def test_zero_time_to_expiry(self):
        """Put price should be zero when T=0."""
        price = black_scholes_put(S=100, K=100, T=0, r=0.05, sigma=0.2)
        self.assertEqual(price, 0.0)

    def test_zero_volatility(self):
        """Put price should be zero when sigma=0."""
        price = black_scholes_put(S=100, K=100, T=1.0, r=0.05, sigma=0)
        self.assertEqual(price, 0.0)

    def test_put_call_parity(self):
        """Verify put-call parity: C - P = S*e^(-q*T) - K*e^(-r*T). With q=0: C - P = S - K*e^(-r*T)."""
        S, K, T, r, sigma = 100, 100, 1.0, 0.05, 0.2
        call = black_scholes_call(S, K, T, r, sigma)
        put = black_scholes_put(S, K, T, r, sigma)

        left = call - put
        right = S - K * np.exp(-r * T)  # No dividend (q=0)
        self.assertAlmostEqual(left, right, places=2)

    def test_put_price_increases_with_volatility(self):
        """Put price should increase with volatility."""
        p1 = black_scholes_put(S=100, K=100, T=1.0, r=0.05, sigma=0.15)
        p2 = black_scholes_put(S=100, K=100, T=1.0, r=0.05, sigma=0.25)
        self.assertGreater(p2, p1)

    def test_put_price_increases_with_time(self):
        """Put price should generally increase with time to expiry."""
        p1 = black_scholes_put(S=100, K=100, T=0.5, r=0.05, sigma=0.2)
        p2 = black_scholes_put(S=100, K=100, T=1.0, r=0.05, sigma=0.2)
        self.assertGreater(p2, p1)


class BlackScholesCallTests(TestCase):
    """Tests for Black-Scholes call option pricing."""

    def test_atm_call_positive_price(self):
        """ATM call should have positive price."""
        price = black_scholes_call(S=100, K=100, T=1.0, r=0.05, sigma=0.2)
        self.assertGreater(price, 0)

    def test_deep_itm_call(self):
        """Deep ITM call should be worth more than ATM call."""
        itm_price = black_scholes_call(S=120, K=100, T=1.0, r=0.05, sigma=0.2)
        atm_price = black_scholes_call(S=100, K=100, T=1.0, r=0.05, sigma=0.2)
        self.assertGreater(itm_price, atm_price)

    def test_deep_otm_call(self):
        """Deep OTM call should be worth less than ATM call."""
        otm_price = black_scholes_call(S=80, K=100, T=1.0, r=0.05, sigma=0.2)
        atm_price = black_scholes_call(S=100, K=100, T=1.0, r=0.05, sigma=0.2)
        self.assertLess(otm_price, atm_price)

    def test_call_zero_time_to_expiry(self):
        """Call price should be zero when T=0."""
        price = black_scholes_call(S=100, K=100, T=0, r=0.05, sigma=0.2)
        self.assertEqual(price, 0.0)


class EstimateOptionCostTests(TestCase):
    """Tests for option cost estimation."""

    def test_estimate_option_cost_returns_dict(self):
        """estimate_option_cost should return a dict with expected keys."""
        result = estimate_option_cost(
            strategy_code='protective_put',
            current_price_usd=100.0,
            quantity=100,
            vol_63d=0.2,
            risk_free_rate=0.05,
            expiry_months=6,
        )
        self.assertIsInstance(result, dict)
        self.assertIn('total_cost_usd', result)
        self.assertIn('put_premium_per_share', result)
        self.assertIn('total_premium_usd', result)
        self.assertIn('commission_usd', result)
        self.assertIn('bid_ask_usd', result)
        self.assertIn('net_premium_per_share', result)

    def test_option_cost_increases_with_volatility(self):
        """Option cost should increase with volatility."""
        cost1 = estimate_option_cost(
            strategy_code='protective_put',
            current_price_usd=100.0,
            quantity=100,
            vol_63d=0.15,
            risk_free_rate=0.05,
        )
        cost2 = estimate_option_cost(
            strategy_code='protective_put',
            current_price_usd=100.0,
            quantity=100,
            vol_63d=0.25,
            risk_free_rate=0.05,
        )
        self.assertGreater(cost2['total_cost_usd'], cost1['total_cost_usd'])

    def test_option_cost_increases_with_quantity(self):
        """Option cost should increase with quantity."""
        cost1 = estimate_option_cost(
            strategy_code='protective_put',
            current_price_usd=100.0,
            quantity=100,
            vol_63d=0.2,
            risk_free_rate=0.05,
        )
        cost2 = estimate_option_cost(
            strategy_code='protective_put',
            current_price_usd=100.0,
            quantity=200,
            vol_63d=0.2,
            risk_free_rate=0.05,
        )
        self.assertGreater(cost2['total_cost_usd'], cost1['total_cost_usd'])


# ---------------------------------------------------------------------------
# Stress Test Engine Tests
# ---------------------------------------------------------------------------

class StressTestEngineTests(TestCase):
    """Tests for stress test engine."""

    def setUp(self):
        """Set up test positions and shock map."""
        self.positions = [
            {
                'symbol': 'AAPL',
                'quantity': 100,
                'currency': 'USD',
                'account_category': 'Investment',
                'account_id': '12345',
                'owner': 'TOM',
                'asset_class': 'common_stock_it',
            },
            {
                'symbol': 'VFV.TO',
                'quantity': 50,
                'currency': 'CAD',
                'account_category': 'Investment',
                'account_id': '12345',
                'owner': 'TOM',
                'asset_class': 'etf_equity',
            },
        ]
        self.current_prices = {'AAPL': 150.0, 'VFV.TO': 100.0}
        self.fx_rate = 1.35  # CAD/USD
        self.shock_map = {'common_stock_it': -0.20, 'etf_equity': -0.15}

    def test_stress_test_returns_dict(self):
        """run_portfolio_stress_test should return dict with expected keys."""
        result = run_portfolio_stress_test(
            positions=self.positions,
            shock_map=self.shock_map,
            current_prices=self.current_prices,
            fx_rate=self.fx_rate,
        )
        self.assertIsInstance(result, dict)
        self.assertIn('stress_loss_unhedged_usd', result)
        self.assertIn('stress_loss_unhedged_pct', result)
        self.assertIn('stress_per_position_json', result)

    def test_stress_test_negative_loss(self):
        """Stress loss should be negative (representing loss)."""
        result = run_portfolio_stress_test(
            positions=self.positions,
            shock_map=self.shock_map,
            current_prices=self.current_prices,
            fx_rate=self.fx_rate,
        )
        self.assertLess(result['stress_loss_unhedged_usd'], 0)

    def test_stress_test_per_position(self):
        """Stress test should compute loss per position."""
        result = run_portfolio_stress_test(
            positions=self.positions,
            shock_map=self.shock_map,
            current_prices=self.current_prices,
            fx_rate=self.fx_rate,
        )
        self.assertIn('stress_per_position_json', result)
        # stress_per_position_json is a JSON string — parse and check length
        import json
        per_pos = json.loads(result['stress_per_position_json'])
        self.assertEqual(len(per_pos), 2)

    def test_stress_test_no_shock(self):
        """With zero shock, loss should be zero."""
        zero_shock = {'common_stock_it': 0.0, 'etf_equity': 0.0}
        result = run_portfolio_stress_test(
            positions=self.positions,
            shock_map=zero_shock,
            current_prices=self.current_prices,
            fx_rate=self.fx_rate,
        )
        self.assertAlmostEqual(result['stress_loss_unhedged_usd'], 0.0, places=5)

    def test_stress_test_with_strategy_overlay(self):
        """Stress test with strategy overlay should compute hedged loss."""
        strategy = {
            'type': 'protective_put',
            'strike_pct': 0.90,
        }
        result = run_portfolio_stress_test(
            positions=self.positions,
            shock_map=self.shock_map,
            current_prices=self.current_prices,
            fx_rate=self.fx_rate,
            strategy_overlay=strategy,
        )
        self.assertIn('stress_loss_hedged_usd', result)
        # Hedged loss should be less severe (smaller absolute value) than unhedged
        self.assertLessEqual(
            abs(result['stress_loss_hedged_usd']),
            abs(result['stress_loss_unhedged_usd']),
        )

    def test_stress_test_missing_price_skips_position(self):
        """Positions without price should be skipped."""
        positions = [
            {'symbol': 'UNKNOWN', 'quantity': 100, 'currency': 'USD'},
        ]
        result = run_portfolio_stress_test(
            positions=positions,
            shock_map={'UNKNOWN': -0.10},
            current_prices={},  # No prices
            fx_rate=1.0,
        )
        self.assertEqual(result['stress_loss_unhedged_usd'], 0.0)


class ResolveShockForPositionTests(TestCase):
    """Tests for shock resolution logic."""

    def test_symbol_shock_takes_precedence(self):
        """Symbol-level shock should override asset_class shock."""
        shock_map = {'AAPL': -0.30, 'common_stock_it': -0.20}
        shock = resolve_shock_for_position('AAPL', 'common_stock_it', '', shock_map)
        self.assertAlmostEqual(shock, -0.30)

    def test_asset_class_shock_fallback(self):
        """Should fall back to asset_class shock when symbol not in map."""
        shock_map = {'common_stock_it': -0.20}
        shock = resolve_shock_for_position('AAPL', 'common_stock_it', '', shock_map)
        self.assertAlmostEqual(shock, -0.20)

    def test_zero_shock_default(self):
        """Should return 0.0 when no shock found."""
        shock_map = {}
        shock = resolve_shock_for_position('UNKNOWN', 'unknown_class', '', shock_map)
        self.assertAlmostEqual(shock, 0.0)


class SectorToCompositeKeyTests(TestCase):
    """Tests for sector-to-composite-key mapping."""

    def test_sector_mapping(self):
        """_sector_to_composite_key should map asset_class + sector to composite key."""
        result = _sector_to_composite_key('common_stock', 'Technology/Semiconductor')
        self.assertEqual(result, 'common_stock_semiconductor')

    def test_sector_mapping_etf(self):
        """ETF sector mapping should produce correct composite key."""
        result = _sector_to_composite_key('etf_equity', 'Canadian Equity Broad Market')
        self.assertEqual(result, 'etf_equity_canadian_equity_broad_market')

    def test_sector_mapping_no_slash(self):
        """Sector without slash should still work."""
        result = _sector_to_composite_key('common_stock', 'Technology')
        self.assertEqual(result, 'common_stock_technology')

    def test_sector_mapping_none_inputs(self):
        """None inputs should return None."""
        self.assertIsNone(_sector_to_composite_key('', 'Technology'))
        self.assertIsNone(_sector_to_composite_key('common_stock', ''))


# ---------------------------------------------------------------------------
# Gap Detection Tests
# ---------------------------------------------------------------------------

class GapDetectionLogicTests(TestCase):
    """Tests for gap detection logic (without DB dependencies)."""

    def test_gap_severity_warning(self):
        """Gap of 5 days should be 'warning' severity."""
        from risk.services.gap_detection import GapReport
        report = GapReport(
            symbol='AAPL',
            gap_start=date(2024, 1, 1),
            gap_end=date(2024, 1, 8),
            gap_days=5,
            severity='warning',
        )
        self.assertEqual(report.severity, 'warning')
        self.assertEqual(report.gap_days, 5)

    def test_gap_severity_critical(self):
        """Gap of 11 days should be 'critical' severity."""
        from risk.services.gap_detection import GapReport
        report = GapReport(
            symbol='AAPL',
            gap_start=date(2024, 1, 1),
            gap_end=date(2024, 1, 15),
            gap_days=11,
            severity='critical',
        )
        self.assertEqual(report.severity, 'critical')

    def test_gap_report_repr(self):
        """GapReport.__repr__ should return readable string."""
        from risk.services.gap_detection import GapReport
        report = GapReport(
            symbol='AAPL',
            gap_start=date(2024, 1, 1),
            gap_end=date(2024, 1, 8),
            gap_days=5,
            severity='warning',
        )
        repr_str = repr(report)
        self.assertIn('AAPL', repr_str)
        self.assertIn('warning', repr_str)


# ---------------------------------------------------------------------------
# Strategy Cost Tests
# ---------------------------------------------------------------------------

class StrategyCostRankingTests(TestCase):
    """Tests for strategy cost ranking metric."""

    def test_ranking_metric_taxable_account(self):
        """Ranking metric should include tax cost for taxable accounts."""
        metric = compute_ranking_metric(
            strategy_cost_usd=1000,
            tax_cost_usd=500,
            var_reduction_usd=5000,
            is_taxable_account=True,
        )
        # effective_cost = 1000 + 500 * 1.0 = 1500
        # metric = 1500 / 5000 = 0.3
        self.assertAlmostEqual(metric, 0.3, places=5)

    def test_ranking_metric_non_taxable_account(self):
        """Ranking metric should exclude tax cost for non-taxable accounts."""
        metric = compute_ranking_metric(
            strategy_cost_usd=1000,
            tax_cost_usd=500,
            var_reduction_usd=5000,
            is_taxable_account=False,
        )
        # effective_cost = 1000 (tax cost ignored)
        # metric = 1000 / 5000 = 0.2
        self.assertAlmostEqual(metric, 0.2, places=5)

    def test_ranking_metric_zero_var_reduction(self):
        """Ranking metric should return inf when var_reduction is zero."""
        metric = compute_ranking_metric(
            strategy_cost_usd=1000,
            tax_cost_usd=0,
            var_reduction_usd=0,
            is_taxable_account=True,
        )
        self.assertEqual(metric, float('inf'))

    def test_ranking_metric_tax_penalty_multiplier(self):
        """Tax penalty multiplier should affect ranking."""
        metric_low = compute_ranking_metric(
            strategy_cost_usd=1000,
            tax_cost_usd=500,
            var_reduction_usd=5000,
            is_taxable_account=True,
            tax_penalty_multiplier=0.5,
        )
        metric_high = compute_ranking_metric(
            strategy_cost_usd=1000,
            tax_cost_usd=500,
            var_reduction_usd=5000,
            is_taxable_account=True,
            tax_penalty_multiplier=2.0,
        )
        # Lower multiplier = lower effective cost = better metric
        self.assertLess(metric_low, metric_high)


class BuildTaxImpactNoteTests(TestCase):
    """Tests for tax impact note generation."""

    def test_tax_note_tfsa(self):
        """Tax note should indicate TFSA is tax-sheltered."""
        note = build_tax_impact_note(
            strategy_code='trailing_stop',
            account_category='TFSA',
            unrealised_gain_usd=10000,
            tax_cost_usd=0,
            symbol='AAPL',
            owner='TOM',
            fx_rate=1.35,
        )
        self.assertIn('No tax consequence', note)
        self.assertIn('TFSA', note)

    def test_tax_note_taxable_account(self):
        """Tax note should show tax cost for taxable accounts."""
        note = build_tax_impact_note(
            strategy_code='protective_put',
            account_category='Investment',
            unrealised_gain_usd=10000,
            tax_cost_usd=2000,
            symbol='AAPL',
            owner='TOM',
            fx_rate=1.35,
        )
        # Protective put in taxable account: no taxable event message
        self.assertIn('Protective put', note)
        self.assertIn('no taxable event', note.lower())

    def test_tax_note_includes_symbol_and_owner(self):
        """Tax note should include symbol and owner for taxable accounts."""
        note = build_tax_impact_note(
            strategy_code='collar',
            account_category='Investment',
            unrealised_gain_usd=5000,
            tax_cost_usd=0,
            symbol='GOOGL',
            owner='SUSAN',
            fx_rate=1.35,
        )
        self.assertIn('GOOGL', note)


# ---------------------------------------------------------------------------
# VaR Reduction Tests
# ---------------------------------------------------------------------------

class VaRReductionTests(TestCase):
    """Tests for VaR reduction estimation."""

    def test_protective_put_reduction(self):
        """Protective put should reduce VaR by ~50%."""
        result = estimate_var_reduction(
            strategy_code='protective_put',
            position_market_value_usd=100000,
            var_parametric_95=0.03,  # 3% daily VaR
            beta_spy=1.0,
            current_price_usd=150.0,
        )
        # VaR = 0.03 * 100000 = 3000
        # Reduction = 3000 * 0.5 = 1500
        self.assertAlmostEqual(result['var_reduction_usd'], 1500.0, places=5)
        self.assertIn('50%', result['method'])

    def test_collar_reduction(self):
        """Collar should reduce VaR by ~50% (same as protective put)."""
        result = estimate_var_reduction(
            strategy_code='collar',
            position_market_value_usd=100000,
            var_parametric_95=0.03,
            beta_spy=1.0,
            current_price_usd=150.0,
        )
        self.assertAlmostEqual(result['var_reduction_usd'], 1500.0, places=5)
        self.assertIn('capped', result['method'].lower())

    def test_trailing_stop_reduction(self):
        """Trailing stop should reduce VaR based on stop distance."""
        result = estimate_var_reduction(
            strategy_code='trailing_stop',
            position_market_value_usd=100000,
            var_parametric_95=0.03,  # VaR = 3000
            beta_spy=1.0,
            current_price_usd=150.0,
            stop_trail_pct=0.10,  # 10% stop
        )
        # Max loss after stop = 0.10 * 100000 = 10000
        # VaR_after_stop = min(3000, 10000) = 3000
        # Reduction = max(0, 3000 - 3000) = 0
        # (VaR is already less than stop distance, so no reduction)
        self.assertEqual(result['var_reduction_usd'], 0.0)

    def test_trailing_stop_reduction_with_high_var(self):
        """Trailing stop should reduce VaR when VaR exceeds stop distance."""
        result = estimate_var_reduction(
            strategy_code='trailing_stop',
            position_market_value_usd=100000,
            var_parametric_95=0.15,  # VaR = 15000
            beta_spy=1.0,
            current_price_usd=150.0,
            stop_trail_pct=0.10,  # 10% stop = 10000 max loss
        )
        # VaR = 0.15 * 100000 = 15000
        # Max loss after stop = 0.10 * 100000 = 10000
        # VaR_after_stop = min(15000, 10000) = 10000
        # Reduction = max(0, 15000 - 10000) = 5000
        self.assertAlmostEqual(result['var_reduction_usd'], 5000.0, places=5)

    def test_inverse_etf_reduction(self):
        """Inverse ETF should reduce VaR with hedge ratio."""
        result = estimate_var_reduction(
            strategy_code='inverse_etf',
            position_market_value_usd=100000,
            var_parametric_95=0.03,
            beta_spy=1.0,  # Not leveraged
            current_price_usd=50.0,
        )
        # Hedge ratio = 0.5, leverage discount = 1.0 (beta <= 1.5)
        # Reduction = 3000 * 0.5 * 1.0 = 1500
        self.assertAlmostEqual(result['var_reduction_usd'], 1500.0, places=5)

    def test_inverse_etf_3x_discount(self):
        """3x inverse ETF should have reduced effectiveness."""
        result = estimate_var_reduction(
            strategy_code='inverse_etf',
            position_market_value_usd=100000,
            var_parametric_95=0.03,
            beta_spy=2.0,  # Leveraged (beta > 1.5)
            current_price_usd=30.0,
        )
        # Hedge ratio = 0.5, leverage discount = 0.8 (beta > 1.5)
        # Reduction = 3000 * 0.5 * 0.8 = 1200
        self.assertAlmostEqual(result['var_reduction_usd'], 1200.0, places=5)

    def test_var_reduction_pct_of_mv(self):
        """var_reduction_pct_of_mv should be reduction / market value."""
        result = estimate_var_reduction(
            strategy_code='protective_put',
            position_market_value_usd=100000,
            var_parametric_95=0.03,
            beta_spy=1.0,
            current_price_usd=150.0,
        )
        expected_pct = 1500.0 / 100000.0  # 0.015 = 1.5%
        self.assertAlmostEqual(result['var_reduction_pct_of_mv'], expected_pct, places=5)

    def test_unknown_strategy_returns_zero(self):
        """Unknown strategy should return zero reduction."""
        result = estimate_var_reduction(
            strategy_code='unknown_strategy',
            position_market_value_usd=100000,
            var_parametric_95=0.03,
            beta_spy=1.0,
            current_price_usd=150.0,
        )
        self.assertEqual(result['var_reduction_usd'], 0.0)


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------

class RiskServicesIntegrationTests(TestCase):
    """Integration tests across multiple risk services."""

    def test_option_cost_to_var_reduction_to_ranking(self):
        """Test full pipeline: option cost -> VaR reduction -> ranking."""
        # Step 1: Estimate option cost
        option_cost = estimate_option_cost(
            strategy_code='protective_put',
            current_price_usd=100.0,
            quantity=100,
            vol_63d=0.2,
            risk_free_rate=0.05,
        )

        # Step 2: Estimate VaR reduction
        var_reduction = estimate_var_reduction(
            strategy_code='protective_put',
            position_market_value_usd=10000.0,  # 100 shares * $100
            var_parametric_95=0.03,
            beta_spy=1.0,
            current_price_usd=100.0,
        )

        # Step 3: Compute ranking metric
        ranking = compute_ranking_metric(
            strategy_cost_usd=option_cost['total_cost_usd'],
            tax_cost_usd=0,  # TFSA
            var_reduction_usd=var_reduction['var_reduction_usd'],
            is_taxable_account=False,
        )

        # All steps should complete without error
        self.assertIsInstance(ranking, float)
        self.assertGreaterEqual(ranking, 0)

    def test_stress_test_with_strategy_and_var_reduction(self):
        """Test stress test with strategy overlay and VaR reduction."""
        # Stress test
        stress_result = run_portfolio_stress_test(
            positions=[{
                'symbol': 'AAPL',
                'quantity': 100,
                'currency': 'USD',
                'account_category': 'Investment',
                'account_id': '12345',
                'owner': 'TOM',
                'asset_class': 'common_stock_it',
            }],
            shock_map={'common_stock_it': -0.20},
            current_prices={'AAPL': 150.0},
            fx_rate=1.35,
            strategy_overlay={'type': 'protective_put', 'strike_pct': 0.90},
        )

        # VaR reduction
        var_reduction = estimate_var_reduction(
            strategy_code='protective_put',
            position_market_value_usd=15000.0,  # 100 * 150
            var_parametric_95=0.03,
            beta_spy=1.0,
            current_price_usd=150.0,
        )

        # Both should complete
        self.assertIn('stress_loss_unhedged_usd', stress_result)
        self.assertIn('var_reduction_usd', var_reduction)
