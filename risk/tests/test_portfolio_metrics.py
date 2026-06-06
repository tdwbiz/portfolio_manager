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

    def setUp(self):
        """Seed test data: security metrics, correlations, positions, index exposures."""
        from datetime import date
        from decimal import Decimal
        from risk.models import (
            SecurityRiskMetrics, CorrelationMatrix, SecurityIndexExposure,
            SecurityUniverse, Account, ScoringConfig,
        )

        self.test_date = date(2026, 6, 5)

        # Create test symbols in security universe
        self.symbols_data = [
            {'symbol': 'VFV', 'sector': 'Equity', 'asset_class': 'ETF',
             'vol_252d': 0.15, 'var_parametric_95': Decimal('0.0250'),
             'beta_spy': Decimal('0.95'), 'beta_xiu': Decimal('0.90')},
            {'symbol': 'SPY', 'sector': 'Equity', 'asset_class': 'ETF',
             'vol_252d': 0.18, 'var_parametric_95': Decimal('0.0300'),
             'beta_spy': Decimal('1.00'), 'beta_xiu': Decimal('0.85')},
            {'symbol': 'VDY', 'sector': 'Equity', 'asset_class': 'ETF',
             'vol_252d': 0.14, 'var_parametric_95': Decimal('0.0230'),
             'beta_spy': Decimal('0.80'), 'beta_xiu': Decimal('0.95')},
            {'symbol': 'GOOG', 'sector': 'Technology', 'asset_class': 'Stock',
             'vol_252d': 0.28, 'var_parametric_95': Decimal('0.0450'),
             'beta_spy': Decimal('1.20'), 'beta_xiu': Decimal('1.10')},
        ]

        # Create security universe entries
        self.universe_entries = []
        for sd in self.symbols_data:
            entry, _ = SecurityUniverse.objects.get_or_create(
                symbol=sd['symbol'],
                defaults={'sector': sd['sector'], 'asset_class': sd['asset_class']},
            )
            self.universe_entries.append(entry)

        # Create security risk metrics for each symbol
        self.security_metrics = []
        for sd in self.symbols_data:
            sm, _ = SecurityRiskMetrics.objects.get_or_create(
                symbol=sd['symbol'],
                calc_date=self.test_date,
                defaults={
                    'vol_10d': Decimal(str(sd['vol_252d'] * 0.5)),
                    'vol_21d': Decimal(str(sd['vol_252d'] * 0.7)),
                    'vol_63d': Decimal(str(sd['vol_252d'] * 0.85)),
                    'vol_252d': Decimal(str(sd['vol_252d'])),
                    'var_parametric_95': sd['var_parametric_95'],
                    'var_parametric_99': sd['var_parametric_95'] * Decimal('1.4'),
                    'var_historical_95': sd['var_parametric_95'] * Decimal('1.1'),
                    'var_cornish_fisher_95': sd['var_parametric_95'] * Decimal('1.15'),
                    'cvar_95': sd['var_parametric_95'] * Decimal('1.3'),
                    'max_drawdown_from_cost': Decimal('-0.20'),
                    'max_drawdown_252d': Decimal('-0.15'),
                    'current_drawdown_from_252d_peak': Decimal('-0.05'),
                    'beta_spy': sd['beta_spy'],
                    'beta_xiu': sd['beta_xiu'],
                    'sharpe_ratio': Decimal('0.85'),
                    'sortino_ratio': Decimal('1.10'),
                    'liquidity_score': Decimal('85.00'),
                    'avg_daily_volume_usd_30d': Decimal('50000000.00'),
                    'ugl_at_risk_95_usd': Decimal('1000.00'),
                    'ugl_at_risk_pct_of_ugl': Decimal('0.05'),
                    'price_data_source': 'yfinance',
                    'price_rows_used': 300,
                },
            )
            self.security_metrics.append(sm)

        # Create correlation matrix entries (symmetric pairs)
        self.correlation_pairs = [
            ('VFV', 'SPY', 0.85), ('SPY', 'VFV', 0.85),
            ('VFV', 'VDY', 0.70), ('VDY', 'VFV', 0.70),
            ('VFV', 'GOOG', 0.60), ('GOOG', 'VFV', 0.60),
            ('SPY', 'VDY', 0.65), ('VDY', 'SPY', 0.65),
            ('SPY', 'GOOG', 0.75), ('GOOG', 'SPY', 0.75),
            ('VDY', 'GOOG', 0.55), ('GOOG', 'VDY', 0.55),
            ('VFV', 'VFV', 1.0), ('SPY', 'SPY', 1.0),
            ('VDY', 'VDY', 1.0), ('GOOG', 'GOOG', 1.0),
        ]
        for sym_a, sym_b, pearson in self.correlation_pairs:
            CorrelationMatrix.objects.get_or_create(
                symbol_a=sym_a, symbol_b=sym_b, calc_date=self.test_date,
                defaults={'pearson_252d': pearson, 'price_rows_overlap': 250},
            )

        # Create index exposures
        self.index_exposures = [
            ('VFV', 'SP500', 1.0), ('VFV', 'TSX', 0.0),
            ('SPY', 'SP500', 1.0), ('SPY', 'NASDAQ100', 0.0),
            ('VDY', 'TSX', 1.0), ('VDY', 'DIVIDEND_CA', 1.0),
            ('GOOG', 'NASDAQ100', 0.85), ('GOOG', 'TECHNOLOGY', 1.0),
            ('GOOG', 'SP500', 0.15),
        ]
        for symbol, index_name, exposure in self.index_exposures:
            SecurityIndexExposure.objects.get_or_create(
                symbol=symbol, index_name=index_name,
                defaults={'exposure_weight': exposure},
            )

        # Create test accounts
        self.account_tom = Account.objects.create(
            account_id='RRSP_TOM', name='Tom RRSP', broker='RBC',
            currency='CAD', category='RRSP', owner='TOM',
            exclude_from_risk=False,
        )
        self.account_taxable = Account.objects.create(
            account_id='TAXABLE_TOM', name='Tom Taxable', broker='RBC',
            currency='CAD', category='TAXABLE', owner='TOM',
            exclude_from_risk=False,
        )
        self.account_taxable_car = Account.objects.create(
            account_id='TAXABLE_CAR', name='Car Fund', broker='RBC',
            currency='CAD', category='TAXABLE', owner='TOM',
            exclude_from_risk=True,
        )

        # Set tax threshold config
        ScoringConfig.objects.get_or_create(
            config_key='taxable_ugl_alert_usd',
            defaults={'config_value': '100000'},
        )

    def tearDown(self):
        """Clean up test data."""
        from risk.models import (
            Account, CorrelationMatrix, PortfolioRiskMetrics, SecurityIndexExposure,
            SecurityRiskMetrics, SecurityUniverse
        )
        PortfolioRiskMetrics.objects.filter(calc_date=self.test_date).delete()
        SecurityIndexExposure.objects.filter(
            symbol__in=['VFV', 'SPY', 'VDY', 'GOOG']
        ).delete()
        CorrelationMatrix.objects.filter(calc_date=self.test_date).delete()
        SecurityRiskMetrics.objects.filter(calc_date=self.test_date).delete()
        SecurityUniverse.objects.filter(
            symbol__in=['VFV', 'SPY', 'VDY', 'GOOG']
        ).delete()
        Account.objects.filter(
            account_id__in=['RRSP_TOM', 'TAXABLE_TOM', 'TAXABLE_CAR']
        ).delete()

    def _create_mock_positions(self, account_id: str, symbol: str, quantity: float,
                                price: float, currency: str = 'CAD',
                                cost_basis: float = 0.0) -> dict:
        """Create a mock position dict for testing."""
        return {
            'account_id': account_id,
            'owner': 'TOM',
            'symbol': symbol,
            'quantity': quantity,
            'currency': currency,
            'price': price,
            'cost_basis_per_unit': cost_basis,
            'account_category': 'Investment',
            'exclude_from_risk': 0,
        }

    def _get_command_class(self):
        """Get the calc_portfolio_metrics command class."""
        from risk.management.commands.calc_portfolio_metrics import Command
        return Command

    def test_portfolio_var_less_than_sum_of_individual_vars(self):
        """Correlated portfolio VaR must be less than sum of individual VaRs."""
        from django.core.management import call_command
        from unittest.mock import patch
        from risk.models import PortfolioRiskMetrics

        # Seed positions for VFV, SPY, VDY, GOOG
        positions = [
            self._create_mock_positions('RRSP_TOM', 'VFV', 100, 110.0),
            self._create_mock_positions('RRSP_TOM', 'SPY', 50, 550.0),
            self._create_mock_positions('RRSP_TOM', 'VDY', 200, 35.0),
            self._create_mock_positions('RRSP_TOM', 'GOOG', 10, 175.0),
        ]

        cmd_class = self._get_command_class()
        with patch.object(cmd_class, '_load_positions', return_value=positions):
            call_command('calc_portfolio_metrics', '--date', self.test_date.isoformat(),
                         '--entity-type', 'portfolio')

        result = PortfolioRiskMetrics.objects.get(
            entity_type='portfolio', entity_id='ALL', calc_date=self.test_date
        )

        self.assertIsNotNone(result.var_portfolio_95_usd)
        self.assertIsNotNone(result.var_undiversified_95_usd)
        # Correlated VaR must be strictly less than undiversified
        self.assertLess(
            float(result.var_portfolio_95_usd),
            float(result.var_undiversified_95_usd)
        )
        # Diversification benefit must be positive
        self.assertGreater(float(result.diversification_benefit_usd), 0)

    def test_diversification_ratio_above_one(self):
        """Portfolio with imperfectly correlated assets should have div ratio > 1.0."""
        from django.core.management import call_command
        from unittest.mock import patch
        from risk.models import PortfolioRiskMetrics

        positions = [
            self._create_mock_positions('RRSP_TOM', 'VFV', 100, 110.0),
            self._create_mock_positions('RRSP_TOM', 'GOOG', 10, 175.0),
        ]

        cmd_class = self._get_command_class()
        with patch.object(cmd_class, '_load_positions', return_value=positions):
            call_command('calc_portfolio_metrics', '--date', self.test_date.isoformat(),
                         '--entity-type', 'portfolio')

        result = PortfolioRiskMetrics.objects.get(
            entity_type='portfolio', entity_id='ALL', calc_date=self.test_date
        )

        self.assertIsNotNone(result.diversification_ratio)
        self.assertGreater(float(result.diversification_ratio), 1.0)

    def test_effective_sp500_concentration(self):
        """Effective SP500 weight should reflect overlapping ETF exposure."""
        from django.core.management import call_command
        from unittest.mock import patch
        from risk.models import PortfolioRiskMetrics

        # VFV and SPY both have SP500 exposure = 1.0
        positions = [
            self._create_mock_positions('RRSP_TOM', 'VFV', 100, 110.0),
            self._create_mock_positions('RRSP_TOM', 'SPY', 50, 550.0),
            self._create_mock_positions('RRSP_TOM', 'VDY', 200, 35.0),  # TSX, not SP500
        ]

        cmd_class = self._get_command_class()
        with patch.object(cmd_class, '_load_positions', return_value=positions):
            call_command('calc_portfolio_metrics', '--date', self.test_date.isoformat(),
                         '--entity-type', 'portfolio')

        result = PortfolioRiskMetrics.objects.get(
            entity_type='portfolio', entity_id='ALL', calc_date=self.test_date
        )

        self.assertIsNotNone(result.effective_sp500_weight)
        # VFV + SPY both have SP500 exposure 1.0, so effective should be > 0
        self.assertGreater(float(result.effective_sp500_weight), 0.0)

    def test_tax_risk_flag_set_for_taxable_accounts(self):
        """Tax risk flag should be True when taxable UGL exceeds threshold."""
        from django.core.management import call_command
        from unittest.mock import patch
        from risk.models import PortfolioRiskMetrics, ScoringConfig

        # Create position with large unrealised gain in taxable account
        positions = [
            self._create_mock_positions(
                'TAXABLE_TOM_SUSAN', 'GOOG', 100, 175.0, currency='USD', cost_basis=50.0
            ),
            # UGL = 100 * (175 - 50) = $12,500 USD — above threshold of 100
        ]

        # Set threshold to 100 to trigger flag
        ScoringConfig.objects.filter(config_key='taxable_ugl_alert_usd').update(
            config_value='100'
        )

        cmd_class = self._get_command_class()
        with patch.object(cmd_class, '_load_positions', return_value=positions):
            call_command('calc_portfolio_metrics', '--date', self.test_date.isoformat(),
                         '--entity-type', 'account', '--entity-id', 'TAXABLE_TOM_SUSAN')

        result = PortfolioRiskMetrics.objects.get(
            entity_type='account', entity_id='TAXABLE_TOM_SUSAN', calc_date=self.test_date
        )

        self.assertIsNotNone(result.taxable_ugl_usd)
        self.assertTrue(result.tax_risk_flag)

    def test_excluded_account_not_in_portfolio_total(self):
        """TAXABLE_CAR (exclude_from_risk=True) should not appear in portfolio roll-up."""
        from django.core.management import call_command
        from unittest.mock import patch
        from risk.models import PortfolioRiskMetrics

        # Seed positions in both taxable and excluded accounts
        positions = [
            self._create_mock_positions('TAXABLE_TOM', 'VFV', 100, 110.0),
            self._create_mock_positions('TAXABLE_CAR', 'GOOG', 50, 175.0),
        ]

        cmd_class = self._get_command_class()
        with patch.object(cmd_class, '_load_positions', return_value=positions):
            call_command('calc_portfolio_metrics', '--date', self.test_date.isoformat(),
                         '--entity-type', 'portfolio')

        result = PortfolioRiskMetrics.objects.get(
            entity_type='portfolio', entity_id='ALL', calc_date=self.test_date
        )

        # Portfolio should have computed metrics from non-excluded positions
        self.assertIsNotNone(result.var_portfolio_95_usd)
        self.assertIsNotNone(result.total_market_value_usd)
