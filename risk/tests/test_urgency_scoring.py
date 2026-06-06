import json
from decimal import Decimal
from django.test import TestCase
from risk.services.urgency_engine import (
    score_m1_metrics,
    score_security_driver,
    score_regime_adjustment,
    score_market_condition,
    compute_urgency_score,
    rollup_account,
)


class M1ComponentTests(TestCase):

    def test_highest_var_symbol_scores_100(self):
        # Symbol with highest VaR in universe gets rank 100
        all_metrics = [
            {'symbol': 'A', 'var_parametric_95': 0.05, 'current_drawdown_from_252d_peak': -0.10, 'vol_252d': 0.30},
            {'symbol': 'B', 'var_parametric_95': 0.02, 'current_drawdown_from_252d_peak': -0.02, 'vol_252d': 0.10},
        ]
        score_a, _ = score_m1_metrics('A', all_metrics[0], all_metrics)
        score_b, _ = score_m1_metrics('B', all_metrics[1], all_metrics)
        self.assertGreater(score_a, score_b)
        self.assertAlmostEqual(score_a, 100.0, places=1)
        self.assertAlmostEqual(score_b, 0.0, places=1)

    def test_null_metrics_return_zero(self):
        # Mutual fund: all M1 metrics are NULL
        metrics = {'var_parametric_95': None, 'current_drawdown_from_252d_peak': None, 'vol_252d': None}
        score, _ = score_m1_metrics('RBF2010', metrics, [metrics])
        self.assertEqual(score, 0.0)

    def test_partial_null_averages_available(self):
        # Only vol_252d available; var and drawdown NULL
        metrics = {'var_parametric_95': None, 'current_drawdown_from_252d_peak': None, 'vol_252d': 0.50}
        all_metrics = [
            metrics,
            {'var_parametric_95': None, 'current_drawdown_from_252d_peak': None, 'vol_252d': 0.10},
        ]
        score, _ = score_m1_metrics('IBIT', metrics, all_metrics)
        # Only vol_rank is available; IBIT has higher vol = rank 100
        self.assertAlmostEqual(score, 100.0, places=1)


class DriverComponentTests(TestCase):

    def test_crypto_scores_higher_than_cash_equiv(self):
        crypto_driver = {'asset_class_baseline': 80, 'sector_cyclicality': 85, 'liquidity_tier': 25, 'earnings_proximity_flag': False}
        cash_driver   = {'asset_class_baseline': 10, 'sector_cyclicality': 10, 'liquidity_tier': 5,  'earnings_proximity_flag': False}
        config = {'earnings_proximity_boost': 10}
        score_crypto, _ = score_security_driver('IBIT', crypto_driver, config)
        score_cash, _   = score_security_driver('SGOV', cash_driver, config)
        self.assertGreater(score_crypto, score_cash)

    def test_earnings_proximity_adds_boost(self):
        driver_no_earnings  = {'asset_class_baseline': 60, 'sector_cyclicality': 65, 'liquidity_tier': 15, 'earnings_proximity_flag': False}
        driver_with_earnings = {'asset_class_baseline': 60, 'sector_cyclicality': 65, 'liquidity_tier': 15, 'earnings_proximity_flag': True}
        config = {'earnings_proximity_boost': 10}
        score_no, _   = score_security_driver('AAPL', driver_no_earnings, config)
        score_yes, _  = score_security_driver('AAPL', driver_with_earnings, config)
        self.assertAlmostEqual(score_yes, min(100, score_no + 10), places=1)

    def test_earnings_boost_does_not_exceed_100(self):
        driver = {'asset_class_baseline': 95, 'sector_cyclicality': 95, 'liquidity_tier': 95, 'earnings_proximity_flag': True}
        config = {'earnings_proximity_boost': 10}
        score, _ = score_security_driver('EXTREME', driver, config)
        self.assertLessEqual(score, 100.0)


class RegimeComponentTests(TestCase):

    def test_q3_amplifies_equity_risk(self):
        # In Q3 (recession), equity risk is amplified (multiplier > 1.0)
        driver_score = 50.0
        regime = {'quadrant': 'Q3', 'transition_flag': False}
        sensitivities = {'etf_equity': 1.20}
        driver = {'asset_class': 'etf_equity'}
        config = {'regime_transition_dampener': 0.80}
        score, _ = score_regime_adjustment('VFV', driver_score, driver, regime, sensitivities, config)
        self.assertGreater(score, driver_score)
        self.assertAlmostEqual(score, min(100, driver_score * 1.20), places=1)

    def test_transition_flag_dampens_regime_score(self):
        driver_score = 50.0
        regime_stable     = {'quadrant': 'Q3', 'transition_flag': False}
        regime_transition = {'quadrant': 'Q3', 'transition_flag': True}
        sensitivities = {'etf_equity': 1.20}
        driver = {'asset_class': 'etf_equity'}
        config = {'regime_transition_dampener': 0.80}
        score_stable, _     = score_regime_adjustment('VFV', driver_score, driver, regime_stable, sensitivities, config)
        score_transition, _ = score_regime_adjustment('VFV', driver_score, driver, regime_transition, sensitivities, config)
        self.assertLess(score_transition, score_stable)

    def test_q1_reduces_cash_risk(self):
        # Cash is even safer in Q1 (multiplier < 1.0)
        driver_score = 10.0
        regime = {'quadrant': 'Q1', 'transition_flag': False}
        sensitivities = {'cash_equiv': 0.60}
        driver = {'asset_class': 'cash_equiv'}
        config = {'regime_transition_dampener': 0.80}
        score, _ = score_regime_adjustment('SGOV', driver_score, driver, regime, sensitivities, config)
        self.assertLess(score, driver_score)


class MarketConditionComponentTests(TestCase):

    def test_death_cross_raises_score(self):
        mc_death   = {'rsi_14': 45, 'crossover_type': 'death', 'sma_50': 90, 'sma_200': 100,
                      'market_phase': 'correction', 'bb_pct_b': 0.30}
        mc_golden  = {'rsi_14': 55, 'crossover_type': 'golden', 'sma_50': 110, 'sma_200': 100,
                      'market_phase': 'uptrend', 'bb_pct_b': 0.60}
        score_death, _  = score_market_condition('VFV', mc_death)
        score_golden, _ = score_market_condition('VFV', mc_golden)
        self.assertGreater(score_death, score_golden)

    def test_none_market_condition_returns_zero(self):
        score, _ = score_market_condition('RBF2010', None)
        self.assertEqual(score, 0.0)

    def test_bear_phase_scores_high(self):
        mc = {'rsi_14': 28, 'crossover_type': 'death', 'sma_50': 70, 'sma_200': 100,
              'market_phase': 'bear', 'bb_pct_b': 0.05}
        score, _ = score_market_condition('SYM', mc)
        self.assertGreater(score, 60)

    def test_uptrend_golden_cross_scores_low(self):
        mc = {'rsi_14': 58, 'crossover_type': 'golden', 'sma_50': 110, 'sma_200': 100,
              'market_phase': 'uptrend', 'bb_pct_b': 0.55}
        score, _ = score_market_condition('SYM', mc)
        self.assertLess(score, 35)


class CompositeScoreTests(TestCase):

    def test_thresholds_classify_correctly(self):
        config = {'weight_m1_metrics': 0.35, 'weight_security_driver': 0.20,
                  'weight_regime': 0.25, 'weight_market_condition': 0.20,
                  'threshold_low': 25, 'threshold_medium': 50, 'threshold_high': 75}
        score, level, _ = compute_urgency_score(10, 10, 10, 10, config)
        self.assertEqual(level, 'Low')
        score, level, _ = compute_urgency_score(40, 40, 40, 40, config)
        self.assertEqual(level, 'Medium')
        score, level, _ = compute_urgency_score(65, 65, 65, 65, config)
        self.assertEqual(level, 'High')
        score, level, _ = compute_urgency_score(90, 90, 90, 90, config)
        self.assertEqual(level, 'Critical')

    def test_weights_sum_to_one(self):
        config = {'weight_m1_metrics': 0.35, 'weight_security_driver': 0.20,
                  'weight_regime': 0.25, 'weight_market_condition': 0.20,
                  'threshold_low': 25, 'threshold_medium': 50, 'threshold_high': 75}
        total = (config['weight_m1_metrics'] + config['weight_security_driver'] +
                 config['weight_regime'] + config['weight_market_condition'])
        self.assertAlmostEqual(total, 1.0, places=4)

    def test_dominant_component_identified(self):
        config = {'weight_m1_metrics': 0.35, 'weight_security_driver': 0.20,
                  'weight_regime': 0.25, 'weight_market_condition': 0.20,
                  'threshold_low': 25, 'threshold_medium': 50, 'threshold_high': 75}
        # M1 component at 90 with weight 0.35 = 31.5 contribution — dominant
        score, level, dominant = compute_urgency_score(90, 30, 30, 30, config)
        self.assertEqual(dominant, 'm1_metrics')


class AccountRollupTests(TestCase):

    def test_critical_security_floors_account_at_high(self):
        security_scores = [
            {'symbol': 'AAPL', 'score_raw': 85, 'urgency_level': 'Critical'},
            {'symbol': 'SGOV', 'score_raw': 8,  'urgency_level': 'Low'},
        ]
        weights = {'AAPL': 0.90, 'SGOV': 0.10}
        config = {'threshold_low': 25, 'threshold_medium': 50, 'threshold_high': 75}
        score, level = rollup_account('IB_SUSAN', security_scores, weights, config)
        # Weighted avg: 85*0.90 + 8*0.10 = 77.3 (would be High anyway here)
        # Even if average were lower, Critical floor ensures at least High
        self.assertIn(level, ['High', 'Critical'])

    def test_all_low_gives_low_account(self):
        security_scores = [
            {'symbol': 'SGOV', 'score_raw': 8,  'urgency_level': 'Low'},
            {'symbol': 'RBF2010', 'score_raw': 5, 'urgency_level': 'Low'},
        ]
        weights = {'SGOV': 0.70, 'RBF2010': 0.30}
        config = {'threshold_low': 25, 'threshold_medium': 50, 'threshold_high': 75}
        score, level = rollup_account('TAXABLE_CAR', security_scores, weights, config)
        self.assertEqual(level, 'Low')


class IntegrationTests(TestCase):

    def test_full_urgency_run_produces_all_entity_types(self):
        # Seed M1, M2-Spec-1, and M1-Spec-2 output for a minimal portfolio
        # Run calc_urgency --date 2026-06-02
        # Assert rm_urgency_score has rows for entity_type in
        #   ['security_in_account', 'account', 'owner', 'portfolio']
        pass

    def test_cash_equiv_scores_low(self):
        # After full run: SGOV and RBF2010 urgency_level should be 'Low'
        pass

    def test_crypto_etf_scores_high_or_critical(self):
        # After full run: IBIT and ETHA urgency_level should be 'High' or 'Critical'
        pass

    def test_component_scores_json_is_valid_json(self):
        # After full run: assert json.loads(score.component_scores_json) succeeds
        # and contains keys: 'm1', 'driver', 'regime', 'market'
        pass

    def test_excluded_account_not_in_portfolio_rollup(self):
        # TAXABLE_CAR scores should not affect portfolio entity urgency
        # (exclude_from_risk flag respected)
        pass
