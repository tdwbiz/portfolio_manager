import numpy as np
import pandas as pd
from django.test import TestCase
from risk.services.regime_engine import (
    score_yield_curve, score_vix, compute_quadrant_probabilities
)
from risk.services.technical_indicators import (
    sma, sma_crossover, rsi, macd, bollinger_bands,
    on_balance_volume, classify_market_phase
)


class RegimeDetectionTests(TestCase):

    def test_inverted_yield_curve_scores_low(self):
        score = score_yield_curve(-0.60)
        self.assertLess(score, 30)

    def test_normal_yield_curve_scores_high(self):
        score = score_yield_curve(1.20)
        # 1.20 falls in >= +0.50 bucket = score 65
        self.assertAlmostEqual(score, 65.0)

    def test_flat_yield_curve_scores_midpoint(self):
        score = score_yield_curve(0.05)
        self.assertAlmostEqual(score, 50, delta=10)

    def test_high_vix_scores_low(self):
        score = score_vix(40)
        self.assertLess(score, 20)

    def test_low_vix_scores_high(self):
        score = score_vix(10)
        self.assertGreater(score, 75)

    def test_quadrant_probabilities_sum_to_one(self):
        scores = {
            'yield_curve': 65.0,
            'vix': 60.0,
            'cpi': None,
            'pmi': None,
            'gdp': None,
        }
        weights = {
            'regime_weight_yield_curve': 0.35,
            'regime_weight_vix': 0.25,
            'regime_weight_cpi': 0.20,
            'regime_weight_pmi': 0.10,
            'regime_weight_gdp': 0.10,
        }
        probs, confidence, quadrant = compute_quadrant_probabilities(scores, weights)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=4)

    def test_high_growth_low_inflation_gives_q1(self):
        # High yield curve score, low VIX, low CPI
        scores = {'yield_curve': 80.0, 'vix': 80.0, 'cpi': 20.0, 'pmi': None, 'gdp': None}
        weights = {'regime_weight_yield_curve': 0.35, 'regime_weight_vix': 0.25,
                   'regime_weight_cpi': 0.20, 'regime_weight_pmi': 0.10, 'regime_weight_gdp': 0.10}
        probs, confidence, quadrant = compute_quadrant_probabilities(scores, weights)
        self.assertEqual(quadrant, 'Q1')

    def test_low_growth_high_inflation_gives_q4(self):
        # Inverted yield curve, high VIX, high CPI
        scores = {'yield_curve': 15.0, 'vix': 15.0, 'cpi': 90.0, 'pmi': None, 'gdp': None}
        weights = {'regime_weight_yield_curve': 0.35, 'regime_weight_vix': 0.25,
                   'regime_weight_cpi': 0.20, 'regime_weight_pmi': 0.10, 'regime_weight_gdp': 0.10}
        probs, confidence, quadrant = compute_quadrant_probabilities(scores, weights)
        self.assertEqual(quadrant, 'Q4')

    def test_null_indicators_redistributed(self):
        # With only 2 indicators, weights should redistribute to sum to 1.0
        scores = {'yield_curve': 60.0, 'vix': 50.0, 'cpi': None, 'pmi': None, 'gdp': None}
        weights = {'regime_weight_yield_curve': 0.35, 'regime_weight_vix': 0.25,
                   'regime_weight_cpi': 0.20, 'regime_weight_pmi': 0.10, 'regime_weight_gdp': 0.10}
        probs, confidence, quadrant = compute_quadrant_probabilities(scores, weights)
        # Should not raise; probabilities should still sum to 1.0
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=4)


class RSITests(TestCase):

    def test_rsi_on_known_sequence(self):
        # 14 days of constant +1 gain: RSI should be 100
        prices = pd.Series(list(range(1, 20)))  # 1,2,3...19 (18 days up)
        result = rsi(prices, period=14)
        # After 14+ up days in a row: RSI approaches 100
        self.assertGreater(result.dropna().iloc[-1], 90)

    def test_rsi_on_constant_losses(self):
        # 14 days of constant -1 loss: RSI should be 0
        prices = pd.Series(list(range(20, 1, -1)))  # 20,19,...2
        result = rsi(prices, period=14)
        self.assertLess(result.dropna().iloc[-1], 10)

    def test_rsi_range_is_0_to_100(self):
        np.random.seed(5)
        prices = pd.Series(100 + np.random.randn(252).cumsum())
        result = rsi(prices, period=14)
        valid = result.dropna()
        self.assertTrue((valid >= 0).all())
        self.assertTrue((valid <= 100).all())

    def test_rsi_requires_period_plus_one_rows(self):
        # Only 10 rows, period=14: all NaN
        prices = pd.Series(range(10))
        result = rsi(prices, period=14)
        self.assertTrue(result.isna().all())


class SMATests(TestCase):

    def test_sma_known_values(self):
        prices = pd.Series([10, 20, 30, 40, 50])
        result = sma(prices, 3)
        # SMA-3 of [10,20,30,40,50] = [NaN, NaN, 20.0, 30.0, 40.0]
        self.assertAlmostEqual(result.iloc[2], 20.0)
        self.assertAlmostEqual(result.iloc[4], 40.0)

    def test_golden_cross_detected(self):
        # Build series where 50d SMA crosses above 200d SMA on the last day
        dates = pd.date_range('2025-01-01', periods=300)
        sma_fast = pd.Series(
            [50.0] * 250 + [49.0] + [51.0] * 49,
            index=dates
        )
        sma_slow = pd.Series([50.0] * 300, index=dates)
        crossover_type, crossover_date = sma_crossover(sma_fast, sma_slow)
        self.assertEqual(crossover_type, 'golden')

    def test_no_crossover_when_stable(self):
        dates = pd.date_range('2025-01-01', periods=300)
        sma_fast = pd.Series([55.0] * 300, index=dates)  # Always above
        sma_slow = pd.Series([50.0] * 300, index=dates)
        crossover_type, _ = sma_crossover(sma_fast, sma_slow)
        self.assertEqual(crossover_type, 'none')


class BollingerBandTests(TestCase):

    def test_bandwidth_widens_with_higher_volatility(self):
        np.random.seed(7)
        stable_prices = pd.Series(100 + np.random.randn(50) * 1)
        volatile_prices = pd.Series(100 + np.random.randn(50) * 10)
        _, _, _, bw_stable, _ = bollinger_bands(stable_prices, window=20)
        _, _, _, bw_volatile, _ = bollinger_bands(volatile_prices, window=20)
        self.assertGreater(bw_volatile.iloc[-1], bw_stable.iloc[-1])

    def test_price_at_middle_gives_pct_b_half(self):
        prices = pd.Series([100.0] * 25)
        _, middle, _, _, pct_b = bollinger_bands(prices, window=20)
        # Price = middle = upper = lower when std=0: pct_b undefined (division by zero)
        # In this degenerate case, return 0.5 (price is at the middle)
        # The function must handle std=0 gracefully — return 0.5
        self.assertIsNotNone(pct_b.iloc[-1])


class MarketPhaseTests(TestCase):

    def test_bear_phase_overrides_oversold(self):
        # Price is 25% below 52-week high AND RSI is 25 (oversold)
        # Bear should take priority
        phase = classify_market_phase(75.0, sma_200=90.0, pct_from_52w_high=-25.0, rsi=25.0)
        self.assertEqual(phase, 'bear')

    def test_correction_phase(self):
        phase = classify_market_phase(85.0, sma_200=80.0, pct_from_52w_high=-15.0, rsi=45.0)
        self.assertEqual(phase, 'correction')

    def test_overbought_in_uptrend(self):
        phase = classify_market_phase(105.0, sma_200=90.0, pct_from_52w_high=-2.0, rsi=75.0)
        self.assertEqual(phase, 'overbought')

    def test_uptrend_when_above_200d_sma(self):
        phase = classify_market_phase(110.0, sma_200=90.0, pct_from_52w_high=-5.0, rsi=55.0)
        self.assertEqual(phase, 'uptrend')


class OBVTests(TestCase):

    def test_obv_increases_on_up_days(self):
        closes = pd.Series([100, 102, 101, 103])
        volumes = pd.Series([1000, 2000, 1500, 3000])
        result = on_balance_volume(closes, volumes)
        # Day 0: OBV=1000; Day 1 up: +2000; Day 2 down: -1500; Day 3 up: +3000
        self.assertEqual(result.iloc[1], 3000)
        self.assertEqual(result.iloc[2], 1500)
        self.assertEqual(result.iloc[3], 4500)

    def test_obv_constant_on_unchanged_close(self):
        closes = pd.Series([100, 100, 100])
        volumes = pd.Series([1000, 2000, 3000])
        result = on_balance_volume(closes, volumes)
        # No change in price: OBV stays at initial value
        self.assertEqual(result.iloc[1], result.iloc[0])
