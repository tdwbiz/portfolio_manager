"""Tests for M6-Spec-2 Security Detail, Strategy Reports, Alternative Portfolios, Compare, Stress, Export views."""
import datetime
import json
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from core.models import Account
from risk.models import (
    AlternativePortfolio,
    AlternativeHolding,
    StrategyTemplate,
    InstrumentType,
    StrategyRecommendation,
    BacktestResult,
    BacktestRun,
    HistoricalScenario,
    SimulationRun,
    SimulationResult,
    SecurityRiskMetrics,
    MarketCondition,
    RegimeState,
    PortfolioRiskMetrics,
    UrgencyScore,
    OwnerConfig,
    ScoringConfig,
    SecurityUniverse,
)


class SecurityDetailViewTest(TestCase):
    """Tests for the security detail view."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data shared across all tests."""
        cls.account = Account.objects.create(
            account_id='RRSP_TOM',
            owner='TOM',
            category='RRSP',
        )
        cls.user = User.objects.create_user('admin', 'admin@test.com', 'password')
        cls.client = Client()
        cls.client.login(username='admin', password='password')
        cls.calc_date = datetime.date(2026, 6, 2)

        # Create security universe
        cls.security = SecurityUniverse.objects.create(
            symbol='NVDA',
            name='NVIDIA Corp',
            security_type='common_stock',
            asset_class='common_stock',
            primary_currency='USD',
        )

        # Create risk metrics
        SecurityRiskMetrics.objects.create(
            symbol='NVDA',
            calc_date=cls.calc_date,
            vol_63d=Decimal('0.450000'),
            var_parametric_95=Decimal('0.031200'),
            beta_spy=Decimal('1.2500'),
            sharpe_ratio=Decimal('1.5000'),
        )

        # Create market condition
        MarketCondition.objects.create(
            symbol='NVDA',
            calc_date=cls.calc_date,
            rsi_14=Decimal('65.00'),
            market_phase='Bull',
        )

        # Create regime state
        RegimeState.objects.create(
            calc_date=cls.calc_date,
            quadrant='Q1',
            confidence_score=Decimal('15.00'),
            prob_q1=Decimal('0.4000'),
            prob_q2=Decimal('0.2500'),
            prob_q3=Decimal('0.2000'),
            prob_q4=Decimal('0.1500'),
            indicator_snapshot_json='{}',
        )

        # Create urgency score
        UrgencyScore.objects.create(
            entity_type='security_in_account',
            entity_id='RRSP_TOM',
            symbol='NVDA',
            calc_date=cls.calc_date,
            score_raw=Decimal('45.00'),
            urgency_level='Medium',
            component_m1_score=Decimal('50.00'),
            component_driver_score=Decimal('40.00'),
            component_regime_score=Decimal('45.00'),
            component_market_score=Decimal('40.00'),
            dominant_component='m1_metrics',
            component_scores_json='{}',
        )

        # Create owner config
        OwnerConfig.objects.create(
            owner='TOM',
            marginal_tax_rate_ca=Decimal('0.5300'),
            capital_gains_inclusion_rate=Decimal('0.5000'),
            cost_tolerance_pct=Decimal('0.0200'),
        )

    def test_security_detail_view_returns_200(self):
        """Test that the security detail view returns HTTP 200."""
        response = self.client.get('/risk/security/NVDA/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'NVDA')
        self.assertContains(response, 'NVIDIA Corp')

    def test_security_detail_view_shows_risk_metrics(self):
        """Test that the security detail view shows risk metrics."""
        response = self.client.get('/risk/security/NVDA/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '50.0')  # vol_63d formatted as 50.0%
        self.assertContains(response, '1.25')  # beta_spy

    def test_security_detail_view_shows_market_condition(self):
        """Test that the security detail view shows market conditions."""
        response = self.client.get('/risk/security/NVDA/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '65.0')  # RSI (formatted)
        self.assertContains(response, 'Bull')  # market phase


class StrategyReportViewTest(TestCase):
    """Tests for the strategy report view."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data shared across all tests."""
        cls.account = Account.objects.create(
            account_id='RRSP_TOM',
            owner='TOM',
            category='RRSP',
        )
        cls.user = User.objects.create_user('admin', 'admin@test.com', 'password')
        cls.client = Client()
        cls.client.login(username='admin', password='password')
        cls.calc_date = datetime.date(2026, 6, 2)

        # Create instrument type
        cls.instrument = InstrumentType.objects.create(
            code='protective_put',
            name='Protective Put Option',
            mechanics_description='Buy a put option to hedge downside risk.',
            use_cases_json='["High urgency single stock", "Pre-earnings risk reduction"]',
            cost_structure_json='{"type": "premium", "typical_pct_of_notional": 0.02}',
            tax_treatment_json='{"TFSA": {"tax_cost_on_exercise": false}}',
            eligible_tfsa=True,
            eligible_rrsp=True,
            eligible_taxable=True,
        )

        # Create strategy template
        cls.strategy = StrategyTemplate.objects.create(
            code='collar_high_gain_taxable_stock',
            name='Collar for High-Gain Taxable Stock',
            description='Buy put and sell call to create a collar.',
            applicable_urgency_levels='High,Critical',
            applicable_regime_quadrants='Q3,Q4',
            applicable_asset_types='common_stock',
            applicable_account_categories='Investment,Investment Joint',
            is_tax_triggering=False,
        )

        # Create strategy recommendation
        cls.recommendation = StrategyRecommendation.objects.create(
            symbol='NVDA',
            account_id='RRSP_TOM',
            owner='TOM',
            calc_date=cls.calc_date,
            strategy_template=cls.strategy,
            rank=1,
            urgency_level='High',
            urgency_score=Decimal('78.00'),
            regime_quadrant='Q3',
            position_quantity=Decimal('1000.0000'),
            position_market_value_usd=Decimal('500000.00'),
            position_unrealised_gain_usd=Decimal('200000.00'),
            cost_basis_per_unit_usd=Decimal('300.0000'),
            current_price_usd=Decimal('500.0000'),
            vol_63d=Decimal('0.450000'),
            estimated_strategy_cost_usd=Decimal('5000.00'),
            estimated_strategy_cost_pct=Decimal('0.0100'),
            tax_cost_estimate_usd=Decimal('0.00'),
            is_tax_triggering=False,
            estimated_var_reduction_usd=Decimal('25000.00'),
            estimated_var_reduction_pct=Decimal('0.0500'),
            cost_per_var_reduction_usd=Decimal('0.2000'),
            constraints_applied_json='[]',
            status='active',
        )

        # Create backtest result
        cls.scenario = HistoricalScenario.objects.create(
            name='2020_covid_crash',
            display_name='2020 COVID Crash',
            start_date=datetime.date(2020, 1, 1),
            end_date=datetime.date(2020, 3, 23),
            asset_class_returns_json='{"equity_us": -0.30}',
            benchmark_spy_return=Decimal('-0.335000'),
        )

        cls.backtest_run = BacktestRun.objects.create(
            symbol='NVDA',
            account_id='RRSP_TOM',
            owner='TOM',
            strategy_template=cls.strategy,
            scenario=cls.scenario,
            start_date=datetime.date(2020, 1, 1),
            end_date=datetime.date(2020, 3, 23),
            quantity=Decimal('1000.0000'),
            cost_basis_per_unit_usd=Decimal('300.0000'),
            start_price_usd=Decimal('400.0000'),
            currency='USD',
            strategy_params_json='{}',
        )

        BacktestResult.objects.create(
            run=cls.backtest_run,
            total_return_unhedged_pct=Decimal('-0.200000'),
            total_return_hedged_pct=Decimal('-0.100000'),
            return_improvement_pct=Decimal('0.100000'),
            max_drawdown_unhedged=Decimal('-0.500000'),
            max_drawdown_hedged=Decimal('-0.300000'),
            drawdown_reduction_pct=Decimal('0.200000'),
            hedge_cost_total_usd=Decimal('5000.00'),
            hedge_cost_pct_of_position=Decimal('0.012500'),
            hedge_effectiveness_ratio=Decimal('16.0000'),
            position_value_start_usd=Decimal('400000.00'),
            position_value_end_unhedged_usd=Decimal('320000.00'),
            position_value_end_hedged_usd=Decimal('360000.00'),
            regime_split_json='{}',
        )

    def test_strategy_report_view_returns_200(self):
        """Test that the strategy report view returns HTTP 200."""
        response = self.client.get(
            f'/risk/strategy/{self.recommendation.pk}/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Collar for High-Gain Taxable Stock')

    def test_strategy_report_view_shows_backtest(self):
        """Test that the strategy report view shows backtest results."""
        response = self.client.get(
            f'/risk/strategy/{self.recommendation.pk}/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Related Backtest')
        self.assertContains(response, '-0.2')  # unhedged return -0.200000 formatted as -0.2%

    def test_strategy_report_view_shows_risk_reduction(self):
        """Test that the strategy report view shows risk reduction metrics."""
        response = self.client.get(
            f'/risk/strategy/{self.recommendation.pk}/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '$25,000')  # var reduction

    def test_strategy_acknowledge_view(self):
        """Test that the strategy acknowledge view works."""
        # Create a tax-triggering recommendation for acknowledge test
        tax_triggering_rec = StrategyRecommendation.objects.create(
            symbol='NVDA',
            account_id='RRSP_TOM',
            owner='TOM',
            calc_date=self.calc_date,
            strategy_template=self.strategy,
            rank=1,
            urgency_level='High',
            urgency_score=Decimal('80.00'),
            regime_quadrant='Q3',
            position_quantity=Decimal('1000.0000'),
            position_market_value_usd=Decimal('500000.00'),
            position_unrealised_gain_usd=Decimal('200000.00'),
            cost_basis_per_unit_usd=Decimal('300.0000'),
            current_price_usd=Decimal('500.0000'),
            vol_63d=Decimal('0.450000'),
            estimated_strategy_cost_usd=Decimal('1000.00'),
            estimated_strategy_cost_pct=Decimal('0.0020'),
            tax_cost_estimate_usd=Decimal('53000.00'),
            is_tax_triggering=True,
            estimated_var_reduction_usd=Decimal('50000.00'),
            estimated_var_reduction_pct=Decimal('0.1000'),
            cost_per_var_reduction_usd=Decimal('1.0600'),
            constraints_applied_json='[]',
            status='active',
        )
        response = self.client.post(
            f'/risk/strategy/{tax_triggering_rec.pk}/acknowledge/',
            {
                'presented_to_user': 'TOM',
                'user_notes': 'Acknowledged for review',
            },
        )
        # Should redirect after successful acknowledgement
        self.assertEqual(response.status_code, 302)

    def test_strategy_mark_actioned_view(self):
        """Test that the strategy mark-as-actioned view works."""
        response = self.client.post(
            f'/risk/strategy/{self.recommendation.pk}/mark_actioned/',
            {
                'actioned_by': 'admin',
            },
        )
        self.assertEqual(response.status_code, 302)
        # Verify status was updated
        self.recommendation.refresh_from_db()
        self.assertEqual(self.recommendation.status, 'actioned')


class AlternativePortfolioViewsTest(TestCase):
    """Tests for Alternative Portfolio views."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data shared across all tests."""
        cls.account = Account.objects.create(
            account_id='RRSP_TOM',
            owner='TOM',
            category='RRSP',
        )
        cls.user = User.objects.create_user('admin', 'admin@test.com', 'password')
        cls.client = Client()
        cls.client.login(username='admin', password='password')

        # Create alternative portfolio
        cls.alt_portfolio = AlternativePortfolio.objects.create(
            owner='TOM',
            account=cls.account,
            name='Conservative Alternative',
            description='A lower-risk alternative portfolio.',
            is_active=True,
        )

        # Create holdings
        AlternativeHolding.objects.create(
            portfolio=cls.alt_portfolio,
            symbol='VFV',
            target_weight=Decimal('0.5000'),
        )
        AlternativeHolding.objects.create(
            portfolio=cls.alt_portfolio,
            symbol='XAW',
            target_weight=Decimal('0.3000'),
        )
        AlternativeHolding.objects.create(
            portfolio=cls.alt_portfolio,
            symbol='XIC',
            target_weight=Decimal('0.2000'),
        )

    def test_alternatives_list_view(self):
        """Test that the alternatives list view returns HTTP 200."""
        response = self.client.get('/risk/alternatives/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Conservative Alternative')

    def test_alternatives_account_view(self):
        """Test that the alternatives account view returns HTTP 200."""
        response = self.client.get(
            f'/risk/alternatives/{self.account.account_id}/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Conservative Alternative')

    def test_alternatives_create_view(self):
        """Test that the alternatives create view returns HTTP 200."""
        response = self.client.get('/risk/alternatives/create/')
        self.assertEqual(response.status_code, 200)

    def test_alternatives_edit_view(self):
        """Test that the alternatives edit view returns HTTP 200."""
        response = self.client.get(
            f'/risk/alternatives/{self.alt_portfolio.id}/edit/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Conservative Alternative')


class PortfolioCompareViewTest(TestCase):
    """Tests for the portfolio compare view."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data shared across all tests."""
        cls.account = Account.objects.create(
            account_id='RRSP_TOM',
            owner='TOM',
            category='RRSP',
        )
        cls.user = User.objects.create_user('admin', 'admin@test.com', 'password')
        cls.client = Client()
        cls.client.login(username='admin', password='password')
        cls.calc_date = datetime.date(2026, 6, 2)

        # Create alternative portfolio
        cls.alt_portfolio = AlternativePortfolio.objects.create(
            owner='TOM',
            account=cls.account,
            name='Conservative Alternative',
            is_active=True,
        )

        AlternativeHolding.objects.create(
            portfolio=cls.alt_portfolio,
            symbol='VFV',
            target_weight=Decimal('0.5000'),
        )

        # Create portfolio risk metrics for current (entity_type='portfolio' matches view query)
        PortfolioRiskMetrics.objects.create(
            entity_type='portfolio',
            entity_id='ALL',
            calc_date=cls.calc_date,
            var_portfolio_95_usd=Decimal('25000.00'),
            var_portfolio_95_pct=Decimal('0.0500'),
            portfolio_vol_annualised=Decimal('0.150000'),
            portfolio_beta_spy=Decimal('0.9500'),
            portfolio_sharpe=Decimal('1.2000'),
            total_market_value_usd=Decimal('500000.00'),
            position_count=15,
        )

        # Create portfolio risk metrics for alternative (use different entity_id)
        PortfolioRiskMetrics.objects.create(
            entity_type='account',
            entity_id='TFSA_TOM',
            calc_date=cls.calc_date,
            var_portfolio_95_usd=Decimal('18000.00'),
            var_portfolio_95_pct=Decimal('0.0360'),
            portfolio_vol_annualised=Decimal('0.120000'),
            portfolio_beta_spy=Decimal('0.8000'),
            portfolio_sharpe=Decimal('1.1000'),
            total_market_value_usd=Decimal('300000.00'),
            position_count=8,
        )

        # Create UrgencyScore so get_latest_calc_date() returns the test calc_date
        UrgencyScore.objects.create(
            entity_type='portfolio',
            entity_id='ALL',
            calc_date=cls.calc_date,
            score_raw=Decimal('50.00'),
            urgency_level='Medium',
        )

    def test_portfolio_compare_view_returns_200(self):
        """Test that the portfolio compare view returns HTTP 200."""
        response = self.client.get('/risk/compare/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Conservative Alternative')

    def test_portfolio_compare_shows_risk_metrics(self):
        """Test that the portfolio compare view shows risk metrics."""
        response = self.client.get('/risk/compare/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '5.00%')  # current var_95_pct formatted as percentage
        self.assertContains(response, 'Conservative Alternative')  # alt portfolio shown


class StressTestViewTest(TestCase):
    """Tests for the stress test results view."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data shared across all tests."""
        cls.account = Account.objects.create(
            account_id='RRSP_TOM',
            owner='TOM',
            category='RRSP',
        )
        cls.user = User.objects.create_user('admin', 'admin@test.com', 'password')
        cls.client = Client()
        cls.client.login(username='admin', password='password')
        cls.calc_date = datetime.date(2026, 6, 2)

        # Create simulation run
        cls.simulation_run = SimulationRun.objects.create(
            simulation_type='stress_test',
            entity_type='account',
            entity_id='RRSP_TOM',
            scenario_name='2020_covid_crash',
            calc_date=cls.calc_date,
            run_by='system',
        )

        # Create simulation result
        SimulationResult.objects.create(
            run=cls.simulation_run,
            portfolio_value_start_usd=Decimal('500000.00'),
            stress_loss_unhedged_usd=Decimal('-150000.00'),
            stress_loss_unhedged_pct=Decimal('-0.3000'),
            stress_loss_hedged_usd=Decimal('-80000.00'),
            stress_loss_hedged_pct=Decimal('-0.1600'),
            stress_per_position_json=json.dumps([
                {'symbol': 'NVDA', 'account': 'RRSP_TOM', 'loss_usd': -50000, 'loss_pct': -0.30},
                {'symbol': 'VFV', 'account': 'RRSP_TOM', 'loss_usd': -30000, 'loss_pct': -0.25},
            ]),
        )

    def test_stress_results_view_returns_200(self):
        """Test that the stress results view returns HTTP 200."""
        response = self.client.get(
            f'/risk/stress/{self.simulation_run.pk}/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '2020_covid_crash')  # scenario_name as stored

    def test_stress_results_shows_loss(self):
        """Test that the stress results view shows loss metrics."""
        response = self.client.get(
            f'/risk/stress/{self.simulation_run.pk}/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '-150,000')  # unhedged loss (negative)
        self.assertContains(response, '-80,000')  # hedged loss (negative)


class TaxAcknowledgementTest(TestCase):
    """Tests for the TaxDecisionAcknowledgement model."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data shared across all tests."""
        cls.user = User.objects.create_user('admin', 'admin@test.com', 'password')
        cls.client = Client()
        cls.client.login(username='admin', password='password')
        cls.calc_date = datetime.date(2026, 6, 2)

        # Create strategy template
        cls.strategy = StrategyTemplate.objects.create(
            code='partial_liquidation',
            name='Partial Liquidation',
            description='Sell a portion of the position to reduce risk.',
            applicable_urgency_levels='High,Critical',
            applicable_regime_quadrants='ALL',
            applicable_asset_types='common_stock',
            applicable_account_categories='Investment',
            is_tax_triggering=True,
        )

        cls.recommendation = StrategyRecommendation.objects.create(
            symbol='NVDA',
            account_id='RRSP_TOM',
            owner='TOM',
            calc_date=cls.calc_date,
            strategy_template=cls.strategy,
            rank=1,
            urgency_level='High',
            urgency_score=Decimal('80.00'),
            regime_quadrant='Q3',
            position_quantity=Decimal('1000.0000'),
            position_market_value_usd=Decimal('500000.00'),
            position_unrealised_gain_usd=Decimal('200000.00'),
            cost_basis_per_unit_usd=Decimal('300.0000'),
            current_price_usd=Decimal('500.0000'),
            vol_63d=Decimal('0.450000'),
            estimated_strategy_cost_usd=Decimal('1000.00'),
            estimated_strategy_cost_pct=Decimal('0.0020'),
            tax_cost_estimate_usd=Decimal('53000.00'),
            is_tax_triggering=True,
            estimated_var_reduction_usd=Decimal('50000.00'),
            estimated_var_reduction_pct=Decimal('0.1000'),
            cost_per_var_reduction_usd=Decimal('1.0600'),
            constraints_applied_json='[]',
            status='active',
        )

    def test_tax_acknowledgement_model_creation(self):
        """Test that TaxDecisionAcknowledgement can be created."""
        from risk.models import TaxDecisionAcknowledgement
        ack = TaxDecisionAcknowledgement.objects.create(
            recommendation=self.recommendation,
            presented_to_user='TOM',
            acknowledged_at=timezone.now(),
            acknowledged_by='admin',
            tax_cost_estimate_presented=Decimal('53000.00'),
            user_notes='Acknowledged',
        )
        self.assertEqual(ack.recommendation, self.recommendation)
        self.assertEqual(ack.tax_cost_estimate_presented, Decimal('53000.00'))

    def test_tax_acknowledgement_cascade_delete(self):
        """Test that deleting a recommendation cascades to acknowledgements."""
        from risk.models import TaxDecisionAcknowledgement
        TaxDecisionAcknowledgement.objects.create(
            recommendation=self.recommendation,
            acknowledged_by='admin',
        )
        self.assertEqual(self.recommendation.tax_acknowledgements.count(), 1)
        self.recommendation.delete()
        self.assertEqual(TaxDecisionAcknowledgement.objects.count(), 0)


class ExportServiceTest(TestCase):
    """Tests for the export service."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data shared across all tests."""
        cls.user = User.objects.create_user('admin', 'admin@test.com', 'password')
        cls.client = Client()
        cls.client.login(username='admin', password='password')
        cls.calc_date = datetime.date(2026, 6, 2)

        # Create security universe
        cls.security = SecurityUniverse.objects.create(
            symbol='NVDA',
            name='NVIDIA Corp',
            security_type='common_stock',
            asset_class='common_stock',
            primary_currency='USD',
        )

        # Create risk metrics
        SecurityRiskMetrics.objects.create(
            symbol='NVDA',
            calc_date=cls.calc_date,
            vol_63d=Decimal('0.450000'),
            var_parametric_95=Decimal('0.031200'),
            beta_spy=Decimal('1.2500'),
            sharpe_ratio=Decimal('1.5000'),
        )

        # Create market condition
        MarketCondition.objects.create(
            symbol='NVDA',
            calc_date=cls.calc_date,
            rsi_14=Decimal('65.00'),
            market_phase='Bull',
        )

        # Create regime state
        RegimeState.objects.create(
            calc_date=cls.calc_date,
            quadrant='Q1',
            confidence_score=Decimal('15.00'),
            prob_q1=Decimal('0.4000'),
            prob_q2=Decimal('0.2500'),
            prob_q3=Decimal('0.2000'),
            prob_q4=Decimal('0.1500'),
            indicator_snapshot_json='{}',
        )

        # Create urgency score
        UrgencyScore.objects.create(
            entity_type='security_in_account',
            entity_id='RRSP_TOM',
            symbol='NVDA',
            calc_date=cls.calc_date,
            score_raw=Decimal('45.00'),
            urgency_level='Medium',
            component_m1_score=Decimal('50.00'),
            component_driver_score=Decimal('40.00'),
            component_regime_score=Decimal('45.00'),
            component_market_score=Decimal('40.00'),
            dominant_component='m1_metrics',
            component_scores_json='{}',
        )

        # Create owner config
        OwnerConfig.objects.create(
            owner='TOM',
            marginal_tax_rate_ca=Decimal('0.5300'),
            capital_gains_inclusion_rate=Decimal('0.5000'),
            cost_tolerance_pct=Decimal('0.0200'),
        )

    def test_export_security_csv(self):
        """Test that the security CSV export returns CSV content."""
        response = self.client.get(
            f'/risk/security/NVDA/export/csv/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('NVDA', response.content.decode('utf-8'))

    @staticmethod
    def _weasyprint_available():
        """Check if WeasyPrint is available (catches all import errors)."""
        try:
            from weasyprint import HTML  # noqa: F401
            return True
        except Exception:
            return False

    def test_export_security_pdf(self):
        """Test that the security PDF export returns PDF content."""
        if not self._weasyprint_available():
            self.skipTest("WeasyPrint not installed")
        response = self.client.get(
            f'/risk/security/NVDA/export/pdf/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/pdf', response['Content-Type'])
