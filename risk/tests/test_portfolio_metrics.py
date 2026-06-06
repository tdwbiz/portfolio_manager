import numpy as np
import pandas as pd
from django.test import TestCase
from risk.services.portfolio_metrics import (
    build_covariance_matrix, portfolio_var_markowitz,
    portfolio_var_undiversified, diversification_ratio,
    hhi, hhi_by_group, effective_index_weights,
    get_owner_positions,
)


class CorrelatedVaRTests(TestCase):

    def test_correlated_var_less_than_undiversified(self):
        # Two assets, partial correlation (rho=0.5)
        # Correlated VaR must be less than sum of individual VaRs
        weights = np.array([0.6, 0.4])
        vols = np.array([0.20, 0.30])
        corr = np.array([[1.0, 0.5], [0.5, 1.0]])
        cov = np.outer(vols, vols) * corr
        total_mv = 1_000_000
        var_corr, var_usd = portfolio_var_markowitz(weights, cov, total_mv)
        var_univ = portfolio_var_undiversified(weights, vols / np.sqrt(252) * 1.645, total_mv)
        self.assertLess(var_usd, var_univ)

    def test_perfectly_correlated_assets_equal_undiversified(self):
        # rho=1.0: no diversification benefit
        weights = np.array([0.5, 0.5])
        vols = np.array([0.20, 0.20])
        corr = np.array([[1.0, 1.0], [1.0, 1.0]])
        cov = np.outer(vols, vols) * corr
        total_mv = 1_000_000
        var_corr_frac, var_usd = portfolio_var_markowitz(weights, cov, total_mv)
        var_univ = portfolio_var_undiversified(weights, vols / np.sqrt(252) * 1.645, total_mv)
        self.assertAlmostEqual(var_usd, var_univ, delta=10)

    def test_single_asset_var_matches_security_var(self):
        # Portfolio with one asset: portfolio VaR should equal individual VaR * MV
        weights = np.array([1.0])
        vol = 0.25
        cov = np.array([[vol**2]])
        total_mv = 500_000
        from scipy.stats import norm
        expected_var = total_mv * (vol / np.sqrt(252)) * norm.ppf(0.95)
        _, var_usd = portfolio_var_markowitz(weights, cov, total_mv)
        self.assertAlmostEqual(var_usd, expected_var, delta=1)

    def test_missing_correlation_uses_conservative_default(self):
        # Symbol pair with no correlation data defaults to 0.5 — not zero
        symbols = ['AAPL', 'NVDA']
        metrics = {
            'AAPL': {'vol_252d': 0.30},
            'NVDA': {'vol_252d': 0.55},
        }
        corr_data = {}  # No correlation data available
        cov = build_covariance_matrix(symbols, metrics, corr_data)
        # Off-diagonal should be 0.5 * 0.30 * 0.55 = 0.0825
        self.assertAlmostEqual(cov[0, 1], 0.5 * 0.30 * 0.55, places=4)


class DiversificationRatioTests(TestCase):

    def test_ratio_greater_than_one_for_imperfect_correlation(self):
        weights = np.array([0.5, 0.5])
        vols = np.array([0.20, 0.30])
        # Portfolio vol with rho=0.3 is less than weighted avg vol
        corr = 0.3
        port_var = (0.5**2 * 0.20**2 + 0.5**2 * 0.30**2 + 2 * 0.5 * 0.5 * corr * 0.20 * 0.30)
        port_vol = np.sqrt(port_var)
        weighted_avg = 0.5 * 0.20 + 0.5 * 0.30
        ratio = diversification_ratio(weights, vols, port_vol)
        self.assertGreater(ratio, 1.0)
        self.assertAlmostEqual(ratio, weighted_avg / port_vol, places=4)

    def test_ratio_equals_one_for_perfect_correlation(self):
        weights = np.array([0.5, 0.5])
        vols = np.array([0.25, 0.25])
        # rho=1.0: portfolio vol = weighted avg vol
        port_vol = 0.25
        ratio = diversification_ratio(weights, vols, port_vol)
        self.assertAlmostEqual(ratio, 1.0, places=4)


class HHITests(TestCase):

    def test_single_position_hhi_is_one(self):
        weights = np.array([1.0])
        self.assertAlmostEqual(hhi(weights), 1.0, places=6)

    def test_equal_weight_25_positions(self):
        weights = np.full(25, 1/25)
        result = hhi(weights)
        self.assertAlmostEqual(result, 1/25, places=4)

    def test_vdy_dominance_raises_hhi(self):
        # VDY at 20.7% of portfolio contributes 0.0428 to HHI alone
        # Remaining 79.3% spread evenly across 27 symbols
        vdy_weight = 0.207
        rest = (1 - vdy_weight) / 27
        weights = np.array([vdy_weight] + [rest] * 27)
        result = hhi(weights)
        self.assertGreater(result, vdy_weight**2)

    def test_hhi_by_sector_groups_correctly(self):
        symbols = ['AAPL', 'NVDA', 'VDY', 'CGDV']
        weights = np.array([0.25, 0.25, 0.25, 0.25])
        group_map = {
            'AAPL': 'Technology',
            'NVDA': 'Technology',
            'VDY': 'Financial',
            'CGDV': 'Financial',
        }
        result = hhi_by_group(symbols, weights, group_map)
        # Two groups each with weight 0.5: HHI = 0.5^2 + 0.5^2 = 0.5
        self.assertAlmostEqual(result, 0.5, places=4)


class EffectiveIndexConcentrationTests(TestCase):

    def test_sp500_concentration_from_overlapping_etfs(self):
        # VFV 14% of portfolio (SP500 exposure 1.0) + SPY 3.4% (1.0) + VOO 2.9% (1.0)
        # + ADX 5.1% (0.85) + IWY 2.7% (0.85) + CGDV 5.8% (0.70)
        # Expected effective SP500: 0.14 + 0.034 + 0.029 + 0.0434 + 0.023 + 0.041 = 0.310
        symbols = ['VFV', 'SPY', 'VOO', 'ADX', 'IWY', 'CGDV']
        weights = np.array([0.14, 0.034, 0.029, 0.051, 0.027, 0.058])
        exposures = {
            'VFV':  {'SP500': 1.00},
            'SPY':  {'SP500': 1.00},
            'VOO':  {'SP500': 1.00},
            'ADX':  {'SP500': 0.85},
            'IWY':  {'SP500': 0.85},
            'CGDV': {'SP500': 0.70},
        }
        result = effective_index_weights(symbols, weights, exposures)
        expected = 0.14 + 0.034 + 0.029 + 0.051*0.85 + 0.027*0.85 + 0.058*0.70
        self.assertAlmostEqual(result['SP500'], expected, places=4)

    def test_symbol_without_exposure_contributes_zero(self):
        # AAPL has no SP500 exposure entry
        symbols = ['AAPL', 'VFV']
        weights = np.array([0.50, 0.50])
        exposures = {'VFV': {'SP500': 1.0}}
        result = effective_index_weights(symbols, weights, exposures)
        self.assertAlmostEqual(result['SP500'], 0.50, places=4)


class OwnerAttributionTests(TestCase):

    def test_joint_account_split_50_50(self):
        positions = [
            {'symbol': 'VDY', 'quantity': 100, 'owner': 'TOM', 'account_id': 'RRSP_TOM'},
            {'symbol': 'VFV', 'quantity': 200, 'owner': 'JOINT', 'account_id': 'TAXABLE_TOM_SUSAN'},
        ]
        tom_positions = get_owner_positions('TOM', positions, joint_split=0.5)
        # TOM gets 100% of RRSP_TOM VDY and 50% of TAXABLE VFV
        vdy_pos = next(p for p in tom_positions if p['symbol'] == 'VDY')
        vfv_pos = next(p for p in tom_positions if p['symbol'] == 'VFV')
        self.assertEqual(vdy_pos['quantity'], 100)
        self.assertAlmostEqual(vfv_pos['quantity'], 100.0, places=2)

    def test_owner_does_not_get_other_owners_accounts(self):
        positions = [
            {'symbol': 'GOOG', 'quantity': 100, 'owner': 'SUSAN', 'account_id': 'TFSA_SUSAN'},
            {'symbol': 'VDY', 'quantity': 100, 'owner': 'TOM',   'account_id': 'RRSP_TOM'},
        ]
        tom_positions = get_owner_positions('TOM', positions)
        symbols = [p['symbol'] for p in tom_positions]
        self.assertIn('VDY', symbols)
        self.assertNotIn('GOOG', symbols)


class IntegrationTests(TestCase):
    """
    End-to-end: seed rm_security_risk_metrics + rm_correlation_matrix,
    run calc_portfolio_metrics, verify rm_portfolio_risk_metrics output.
    """

    def test_portfolio_var_less_than_sum_of_individual_vars(self):
        # Seed metrics for 3 symbols with known vols and correlations
        # Run calc_portfolio_metrics --entity-type portfolio
        # Assert portfolio VaR < undiversified VaR
        pass  # Implement with fixtures

    def test_diversification_ratio_above_one(self):
        # Seed partially correlated portfolio
        # Assert diversification_ratio > 1.0
        pass

    def test_effective_sp500_above_20_pct(self):
        # Seed full VFV + SPY + VOO positions
        # Assert effective_sp500_weight > 0.20 for portfolio entity
        pass

    def test_tax_risk_flag_set_for_taxable_accounts(self):
        # Seed position with $150K USD unrealised gain in taxable account
        # Set threshold to 100000
        # Assert tax_risk_flag = True
        pass

    def test_excluded_account_not_in_portfolio_total(self):
        # TAXABLE_CAR (exclude_from_risk=True) positions should not appear
        # in entity_type='portfolio' roll-up
        pass
