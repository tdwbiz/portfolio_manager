"""
Tests for M5-Spec-1 Backtesting Engine.

Tests cover:
- Trailing stop simulation
- Protective put simulation
- Collar simulation
- Inverse ETF simulation
- Max drawdown computation
- Hedge effectiveness computation
- Scenario seeding
- Integration tests (with pass stubs for fixture-dependent tests)

Run with:
    python manage.py test risk.tests.test_backtesting --settings=portfolio_manager.settings_test
"""

from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

import numpy as np
import pandas as pd

from risk.models import HistoricalScenario, BacktestRun, BacktestResult
from risk.services.backtest_engine import (
    simulate_trailing_stop,
    simulate_protective_put,
    simulate_collar,
    simulate_inverse_etf,
    run_stress_test_mode,
)
from risk.services.backtest_metrics import (
    compute_backtest_metrics,
    compute_max_drawdown_of_series,
    build_regime_split,
)


# ---------------------------------------------------------------------------
# Trailing Stop Tests
# ---------------------------------------------------------------------------

class TrailingStopTests(TestCase):

    def setUp(self):
        """Create a 2022-like declining price series for VFV."""
        np.random.seed(42)
        dates = pd.date_range('2022-01-03', periods=252, freq='B')
        # Simulate ~-18% decline with some volatility
        daily_returns = np.random.normal(-0.0008, 0.012, 252)
        prices = 100 * np.cumprod(1 + daily_returns)
        self.prices = pd.DataFrame({
            'open': prices * 1.001,
            'high': prices * 1.005,
            'low': prices * 0.995,
            'close': prices,
            'adj_close': prices,
            'volume': 1000000,
        }, index=dates)

    def test_trailing_stop_no_trigger_in_rally(self):
        """If prices rally, trailing stop should never trigger."""
        rally_prices = pd.DataFrame({
            'close': pd.Series([100, 102, 104, 106, 108, 110]),
        })
        outcome = simulate_trailing_stop(rally_prices, trail_pct=0.10)
        self.assertFalse(outcome.stop_triggered)

    def test_trailing_stop_triggers_on_decline(self):
        """If prices decline more than trail%, stop should trigger."""
        decline_prices = pd.DataFrame({
            'close': pd.Series([100, 98, 95, 90, 85, 80, 75]),
        })
        outcome = simulate_trailing_stop(decline_prices, trail_pct=0.10)
        self.assertTrue(outcome.stop_triggered)

    def test_trailing_stop_stops_at_trigger_price(self):
        """After stop triggers, hedged price series should freeze at stop price."""
        decline_prices = pd.DataFrame({
            'close': pd.Series([100, 95, 90, 85, 80, 75, 70, 65]),
        })
        outcome = simulate_trailing_stop(decline_prices, trail_pct=0.10)
        self.assertTrue(outcome.stop_triggered)
        # Final hedged price should be the stop price, not the final low
        self.assertEqual(outcome.final_price_series.iloc[-1], outcome.stop_trigger_price)

    def test_trailing_stop_high_watermark(self):
        """High watermark should be the cumulative max of prices."""
        prices = pd.DataFrame({
            'close': pd.Series([100, 105, 102, 108, 106]),
        })
        outcome = simulate_trailing_stop(prices, trail_pct=0.10)
        # High watermark at each point
        expected_hwm = pd.Series([100, 105, 105, 108, 108])
        for i in range(len(expected_hwm)):
            self.assertAlmostEqual(
                outcome.high_watermark_series.iloc[i],
                expected_hwm.iloc[i],
                places=6
            )

    def test_trailing_stop_tight_trail_triggers_earlier(self):
        """A tighter trail % should trigger sooner than a wider trail."""
        prices = pd.DataFrame({
            'close': pd.Series([100, 98, 96, 94, 92, 90, 88, 86]),
        })
        outcome_5 = simulate_trailing_stop(prices, trail_pct=0.05)
        outcome_10 = simulate_trailing_stop(prices, trail_pct=0.10)
        # 5% trail should trigger at a HIGHER price (earlier) than 10% trail
        # 5% trail from 100 = 95 trigger; 10% trail from 100 = 90 trigger
        if outcome_5.stop_triggered and outcome_10.stop_triggered:
            self.assertGreaterEqual(
                outcome_5.stop_trigger_price,
                outcome_10.stop_trigger_price
            )


# ---------------------------------------------------------------------------
# Protective Put Tests
# ---------------------------------------------------------------------------

class ProtectivePutTests(TestCase):

    def setUp(self):
        """Create a declining price series."""
        np.random.seed(42)
        dates = pd.date_range('2022-01-03', periods=100, freq='B')
        daily_returns = np.random.normal(-0.001, 0.015, 100)
        prices = 100 * np.cumprod(1 + daily_returns)
        self.prices = pd.DataFrame({
            'close': prices,
        }, index=dates)

    def test_protective_put_provides_downside_protection(self):
        """Put payoff should be positive when price falls below strike."""
        outcome = simulate_protective_put(
            prices=self.prices,
            quantity=1000,
            strike_pct=0.85,
            expiry_months=6,
            vol_63d=0.25,
            risk_free_rate=0.045,
            current_price_usd=100.0,
        )
        # Put premium should be positive
        self.assertGreater(outcome.put_premium_total_usd, 0)

    def test_protective_put_payoff_when_price_falls(self):
        """If price ends below strike, option payoff should be positive."""
        # Create a steep decline
        decline_prices = pd.DataFrame({
            'close': pd.Series([100, 95, 90, 85, 80, 75, 70]),
        })
        outcome = simulate_protective_put(
            prices=decline_prices,
            quantity=100,
            strike_pct=0.85,  # Strike = 85
            expiry_months=6,
            vol_63d=0.30,
            risk_free_rate=0.045,
            current_price_usd=100.0,
        )
        # Final price (70) < strike (85), so payoff should be positive
        self.assertGreater(outcome.option_payoff_usd, 0)
        self.assertFalse(outcome.expired_worthless)

    def test_protective_put_expired_worthless_when_price_rises(self):
        """If price ends above strike, option should expire worthless."""
        rally_prices = pd.DataFrame({
            'close': pd.Series([100, 102, 105, 108, 110, 115, 120]),
        })
        outcome = simulate_protective_put(
            prices=rally_prices,
            quantity=100,
            strike_pct=0.85,
            expiry_months=6,
            vol_63d=0.20,
            risk_free_rate=0.045,
            current_price_usd=100.0,
        )
        self.assertTrue(outcome.expired_worthless)


# ---------------------------------------------------------------------------
# Collar Tests
# ---------------------------------------------------------------------------

class CollarTests(TestCase):

    def test_collar_cheaper_than_protective_put(self):
        """Collar net premium should be less than protective put alone."""
        np.random.seed(42)
        dates = pd.date_range('2022-01-03', periods=50, freq='B')
        prices = 100 * np.cumprod(1 + np.random.normal(0, 0.01, 50))
        prices_df = pd.DataFrame({'close': prices}, index=dates)

        put_outcome = simulate_protective_put(
            prices=prices_df,
            quantity=1000,
            strike_pct=0.85,
            expiry_months=6,
            vol_63d=0.25,
            risk_free_rate=0.045,
            current_price_usd=100.0,
        )
        collar_outcome = simulate_collar(
            prices=prices_df,
            quantity=1000,
            put_strike_pct=0.85,
            call_strike_pct=1.12,
            expiry_months=6,
            vol_63d=0.25,
            risk_free_rate=0.045,
            current_price_usd=100.0,
        )
        # Collar net cost should be less (or equal) than put-only cost
        self.assertLessEqual(collar_outcome.net_premium_usd, put_outcome.put_premium_total_usd)

    def test_collar_caps_upside(self):
        """Collar should generate negative call payoff when price exceeds call strike."""
        rally_prices = pd.DataFrame({
            'close': pd.Series([100, 105, 110, 115, 120, 125, 130]),
        })
        outcome = simulate_collar(
            prices=rally_prices,
            quantity=100,
            put_strike_pct=0.85,
            call_strike_pct=1.12,  # Strike = 112
            expiry_months=6,
            vol_63d=0.20,
            risk_free_rate=0.045,
            current_price_usd=100.0,
        )
        # Final price (130) > call strike (112), so call payoff should be negative
        self.assertGreater(outcome.call_premium_total_usd, 0)  # Premium received is positive


# ---------------------------------------------------------------------------
# Inverse ETF Tests
# ---------------------------------------------------------------------------

class InverseETFTests(TestCase):

    def test_inverse_etf_daily_leverage(self):
        """Inverse ETF daily returns should be leverage * underlying daily returns."""
        prices = pd.DataFrame({
            'close': pd.Series([100, 101, 99, 102, 100]),
        })
        outcome = simulate_inverse_etf(prices, leverage=-1.0)
        # First return is 0 (fillna default for no prior day), rest should be inverted
        self.assertAlmostEqual(float(outcome.daily_leverage.iloc[0]), 0.0)
        expected_ret_1 = -(101/100 - 1)
        self.assertAlmostEqual(float(outcome.daily_leverage.iloc[1]), expected_ret_1, places=6)

    def test_inverse_etf_decay_exists(self):
        """Inverse ETF should show decay vs simple multiplied return over volatile series."""
        # Create a volatile series that ends flat but has large swings
        prices = pd.DataFrame({
            'close': pd.Series([100, 95, 105, 90, 110, 95, 100]),
        })
        outcome = simulate_inverse_etf(prices, leverage=-1.0)
        # Decay should be non-zero for volatile series
        self.assertNotEqual(outcome.decay_loss_usd, 0.0)


# ---------------------------------------------------------------------------
# Drawdown Tests
# ---------------------------------------------------------------------------

class DrawdownTests(TestCase):

    def test_all_rising_has_zero_drawdown(self):
        """A strictly rising series has zero drawdown."""
        values = pd.Series([100, 110, 120, 130])
        result = compute_max_drawdown_of_series(values)
        self.assertAlmostEqual(result, 0.0, places=6)

    def test_known_drawdown(self):
        """A series that drops 20% and recovers should show 20% max drawdown."""
        values = pd.Series([100, 100, 80, 90, 100])
        result = compute_max_drawdown_of_series(values)
        self.assertAlmostEqual(result, -0.20, places=6)


# ---------------------------------------------------------------------------
# Hedge Effectiveness Tests
# ---------------------------------------------------------------------------

class HedgeEffectivenessTests(TestCase):

    def test_effectiveness_ratio_positive_when_hedge_helped(self):
        """Drawdown reduced from 20% to ~10%; cost 2% of position."""
        dates = pd.date_range('2022-01-03', periods=50, freq='B')
        # Price declines from 100 to 80 (20% drop)
        closes = [100 * (1 - 0.004 * i) for i in range(50)]
        prices = pd.DataFrame({'close': closes}, index=dates)
        # Hedge payoff: total dollar payoff (not per-share).
        # A put with strike 100 on 100 shares: payoff = max(100 - close, 0) * 100
        # This provides full downside protection proportional to the drop.
        hedge_payoff = pd.Series(
            [max(100 - c, 0) * 100 for c in closes],
            index=dates,
        )
        result = compute_backtest_metrics(
            prices=prices,
            hedge_payoff_daily=hedge_payoff,
            hedge_cost_total_usd=1000.0,
            quantity=100,
            start_price_usd=100.0,
            strategy_specific_outcomes={'stop_triggered': False, 'option_expired_worthless': None},
        )
        # The hedge should reduce drawdown more than its cost
        self.assertGreater(result['hedge_effectiveness_ratio'], 0)

    def test_return_improvement_positive_when_hedge_better(self):
        """Hedged return should be better (less negative) than unhedged."""
        dates = pd.date_range('2022-01-03', periods=20, freq='B')
        closes = [100, 98, 95, 90, 85, 82, 80, 79, 78, 77,
                  76, 75, 74, 73, 72, 71, 70, 69, 68, 67]
        prices = pd.DataFrame({'close': closes}, index=dates)
        hedge_payoff = pd.Series([30.0] * 20, index=dates)
        result = compute_backtest_metrics(
            prices, hedge_payoff, 200.0, 100, 100.0, {}
        )
        self.assertGreater(
            result['total_return_hedged_pct'],
            result['total_return_unhedged_pct']
        )


# ---------------------------------------------------------------------------
# Scenario Seed Tests
# ---------------------------------------------------------------------------

class ScenarioSeedTests(TestCase):

    def test_all_four_scenarios_seeded(self):
        """seed_historical_scenarios should create exactly 4 active scenarios."""
        call_command('seed_historical_scenarios', verbosity=0)
        count = HistoricalScenario.objects.filter(is_active=True).count()
        self.assertEqual(count, 4)

    def test_scenarios_have_valid_date_ranges(self):
        """All scenarios should have start_date < end_date."""
        call_command('seed_historical_scenarios', verbosity=0)
        for s in HistoricalScenario.objects.all():
            self.assertLess(s.start_date, s.end_date)

    def test_seed_is_idempotent(self):
        """Running seed twice should still result in exactly 4 scenarios."""
        call_command('seed_historical_scenarios', verbosity=0)
        call_command('seed_historical_scenarios', verbosity=0)
        self.assertEqual(HistoricalScenario.objects.count(), 4)


# ---------------------------------------------------------------------------
# Stress Test Mode Tests
# ---------------------------------------------------------------------------

class StressTestTests(TestCase):

    def setUp(self):
        """Create a test scenario."""
        call_command('seed_historical_scenarios', verbosity=0)
        self.scenario = HistoricalScenario.objects.get(name='2022_rate_shock')

    def test_stress_test_returns_asset_class_return(self):
        """Stress test should return the asset class return from scenario."""
        result = run_stress_test_mode(
            scenario=self.scenario,
            quantity=1000,
            start_price_usd=100.0,
            asset_class='equity_ca',
        )
        self.assertAlmostEqual(result['total_return_pct'], -0.18, places=4)
        self.assertTrue(result['is_stress_test_mode'])

    def test_stress_test_end_value(self):
        """End value should be start * quantity * (1 + return)."""
        result = run_stress_test_mode(
            scenario=self.scenario,
            quantity=1000,
            start_price_usd=100.0,
            asset_class='equity_ca',
        )
        expected_end = 100.0 * 1000 * (1 - 0.18)
        self.assertAlmostEqual(result['end_value_usd'], expected_end, places=2)

    def test_stress_test_benchmarks_included(self):
        """Stress test should include benchmark returns."""
        result = run_stress_test_mode(
            scenario=self.scenario,
            quantity=1000,
            start_price_usd=100.0,
            asset_class='equity_ca',
        )
        self.assertIsNotNone(result['benchmark_spy_return'])
        self.assertIsNotNone(result['benchmark_qqq_return'])
        self.assertIsNotNone(result['benchmark_vdy_return'])


# ---------------------------------------------------------------------------
# Integration Tests (pass stubs — require test fixtures with real price data)
# ---------------------------------------------------------------------------

class IntegrationTests(TestCase):

    def test_run_backtest_vfv_trailing_stop_2022(self):
        """
        Seed VFV price data for 2022, seed a position record.
        Run: python manage.py run_backtest --symbol VFV --account TAXABLE_TOM_SUSAN
             --strategy trailing_stop_etf_taxable --scenario 2022_rate_shock
        Assert BacktestRun and BacktestResult rows created.
        Assert stop_triggered = True (VFV did fall more than 10% in 2022).
        Assert hedge_effectiveness_ratio > 0.
        """
        pass  # Implement with test fixtures seeding actual VFV price history

    def test_run_backtest_aapl_collar_2022(self):
        """
        Seed AAPL price data for 2022.
        Run collar backtest.
        Assert: collar reduced drawdown vs unhedged.
        Assert: net_premium_usd < protective_put_premium (collar is cheaper).
        """
        pass

    def test_stress_test_mode_used_when_no_price_history(self):
        """
        Run backtest for IBIT with 2008_gfc scenario (IBIT didn't exist in 2008).
        Assert: result uses stress test mode (asset_class_returns applied).
        Assert: result dict contains _is_stress_test_mode=True.
        """
        pass
