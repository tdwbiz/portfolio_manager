"""Tests for M6-Spec-1 Dashboard views."""
import datetime
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from core.models import Account
from risk.models import (
    UrgencyScore,
    PortfolioRiskMetrics,
    RegimeState,
    MarketCondition,
    SecurityRiskMetrics,
    OwnerConfig,
    ScoringConfig,
)
from risk.views.dashboard import dashboard
from risk.views.account import account_summary
from risk.views.owner import owner_summary
from risk.views.heatmap import risk_heatmap


class DashboardViewTest(TestCase):
    """Tests for the portfolio dashboard view."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data shared across all tests in this class."""
        # Create accounts
        cls.account = Account.objects.create(
            owner='TOM',
            category='RRSP')
        
        # Create owner configs
        OwnerConfig.objects.create(
            owner='TOM',
            marginal_tax_rate_ca=Decimal('0.5300'),
            capital_gains_inclusion_rate=Decimal('0.5000'),
            cost_tolerance_pct=Decimal('0.0200'),
        )
        OwnerConfig.objects.create(
            owner='SUSAN',
            marginal_tax_rate_ca=Decimal('0.5300'),
            capital_gains_inclusion_rate=Decimal('0.5000'),
            cost_tolerance_pct=Decimal('0.0200'),
        )
        
        # Create scoring config
        ScoringConfig.objects.create(
            config_key='threshold_low',
            config_value='25',
        )
        ScoringConfig.objects.create(
            config_key='threshold_medium',
            config_value='50',
        )
        ScoringConfig.objects.create(
            config_key='threshold_high',
            config_value='75',
        )
        
        # Create urgency scores
        cls.calc_date = datetime.date(2026, 6, 2)
        
        # Portfolio urgency score
        UrgencyScore.objects.create(
            entity_type='portfolio',
            entity_id='ALL',
            calc_date=cls.calc_date,
            score_raw=Decimal('72.50'),
            urgency_level='High',
            component_m1_score=Decimal('80.00'),
            component_driver_score=Decimal('65.00'),
            component_regime_score=Decimal('70.00'),
            component_market_score=Decimal('60.00'),
            dominant_component='m1_metrics',
            component_scores_json='{"m1": 80, "driver": 65}',
        )
        
        # Account urgency score
        UrgencyScore.objects.create(
            entity_type='account',
            entity_id='RRSP_TOM',
            calc_date=cls.calc_date,
            score_raw=Decimal('45.00'),
            urgency_level='Medium',
            component_m1_score=Decimal('50.00'),
            component_driver_score=Decimal('40.00'),
            component_regime_score=Decimal('45.00'),
            component_market_score=Decimal('40.00'),
            dominant_component='regime',
            component_scores_json='{"m1": 50, "driver": 40}',
        )
        
        # Owner urgency scores
        UrgencyScore.objects.create(
            entity_type='owner',
            entity_id='TOM',
            calc_date=cls.calc_date,
            score_raw=Decimal('55.00'),
            urgency_level='Medium',
            component_m1_score=Decimal('60.00'),
            component_driver_score=Decimal('50.00'),
            component_regime_score=Decimal('55.00'),
            component_market_score=Decimal('50.00'),
            dominant_component='m1_metrics',
            component_scores_json='{}',
        )
        UrgencyScore.objects.create(
            entity_type='owner',
            entity_id='SUSAN',
            calc_date=cls.calc_date,
            score_raw=Decimal('30.00'),
            urgency_level='Low',
            component_m1_score=Decimal('35.00'),
            component_driver_score=Decimal('25.00'),
            component_regime_score=Decimal('30.00'),
            component_market_score=Decimal('25.00'),
            dominant_component='regime',
            component_scores_json='{}',
        )
        
        # Portfolio risk metrics
        PortfolioRiskMetrics.objects.create(
            entity_type='portfolio',
            entity_id='ALL',
            calc_date=cls.calc_date,
            var_portfolio_95_usd=Decimal('15000.00'),
            var_portfolio_95_pct=Decimal('0.0150'),
            portfolio_vol_annualised=Decimal('0.120000'),
            diversification_ratio=Decimal('1.3500'),
            portfolio_beta_spy=Decimal('0.9500'),
            top1_weight=Decimal('0.1500'),
            top1_symbol='VFV',
            hhi_symbol=Decimal('0.0800'),
            effective_sp500_weight=Decimal('0.3500'),
            effective_nasdaq100_weight=Decimal('0.1200'),
            effective_tsx_weight=Decimal('0.1000'),
            effective_semiconductor_weight=Decimal('0.0800'),
            effective_technology_weight=Decimal('0.1500'),
            effective_dividend_weight=Decimal('0.0500'),
            taxable_ugl_usd=Decimal('25000.00'),
            total_market_value_usd=Decimal('1000000.00'),
        )
        
        # Regime state
        RegimeState.objects.create(
            calc_date=cls.calc_date,
            quadrant='Q1',
            confidence_score=Decimal('72.50'),
            transition_flag=False,
            yield_curve_score=Decimal('45.00'),
            vix_score=Decimal('30.00'),
            cpi_score=Decimal('40.00'),
            pmi_score=Decimal('55.00'),
            gdp_score=Decimal('50.00'),
            prob_q1=Decimal('0.4500'),
            prob_q2=Decimal('0.2500'),
            prob_q3=Decimal('0.2000'),
            prob_q4=Decimal('0.1000'),
            indicator_snapshot_json='{"gdp": 50, "cpi": 40}',
        )
        
        # Market conditions
        MarketCondition.objects.create(
            symbol='VFV',
            calc_date=cls.calc_date,
            sma_50=Decimal('180.0000'),
            sma_200=Decimal('175.0000'),
            price_vs_sma50_pct=Decimal('2.5000'),
            price_vs_sma200_pct=Decimal('5.0000'),
            crossover_type='',
            crossover_date=None,
            rsi_14=Decimal('55.00'),
            macd_line=Decimal('1.234567'),
            macd_signal=Decimal('1.123456'),
            macd_histogram=Decimal('0.111111'),
            macd_crossover='',
            bb_upper=Decimal('185.0000'),
            bb_middle=Decimal('180.0000'),
            bb_lower=Decimal('175.0000'),
            bb_bandwidth=Decimal('0.0556'),
            bb_pct_b=Decimal('0.6000'),
            obv=None,
            obv_trend='',
            market_phase='bull',
            high_52w=Decimal('190.0000'),
            low_52w=Decimal('160.0000'),
            pct_from_52w_high=Decimal('-5.0000'),
            price_rows_used=0,
        )
        MarketCondition.objects.create(
            symbol='ZAG',
            calc_date=cls.calc_date,
            sma_50=Decimal('25.0000'),
            sma_200=Decimal('27.0000'),
            price_vs_sma50_pct=Decimal('-5.0000'),
            price_vs_sma200_pct=Decimal('-7.0000'),
            crossover_type='death',
            crossover_date=cls.calc_date - datetime.timedelta(days=5),
            rsi_14=Decimal('25.00'),
            macd_line=Decimal('-0.567890'),
            macd_signal=Decimal('-0.456789'),
            macd_histogram=Decimal('-0.111101'),
            macd_crossover='bearish',
            bb_upper=Decimal('27.0000'),
            bb_middle=Decimal('25.0000'),
            bb_lower=Decimal('23.0000'),
            bb_bandwidth=Decimal('0.1600'),
            bb_pct_b=Decimal('0.1000'),
            obv=None,
            obv_trend='',
            market_phase='bear',
            high_52w=Decimal('30.0000'),
            low_52w=Decimal('20.0000'),
            pct_from_52w_high=Decimal('-16.6700'),
            price_rows_used=0,
        )
    
    def test_dashboard_returns_200(self):
        """Dashboard view should return HTTP 200."""
        client = Client()
        response = client.get('/risk/dashboard/')
        
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'risk/dashboard.html')
    
    def test_dashboard_contains_urgency_level(self):
        """Dashboard should display the portfolio urgency level."""
        client = Client()
        response = client.get('/risk/dashboard/')
        
        self.assertContains(response, 'High')
    
    def test_dashboard_contains_regime_quadrant(self):
        """Dashboard should display the current regime quadrant."""
        client = Client()
        response = client.get('/risk/dashboard/')
        
        self.assertContains(response, 'Q1')
    
    def test_dashboard_contains_var(self):
        """Dashboard should display the portfolio VaR 95%."""
        client = Client()
        response = client.get('/risk/dashboard/')
        
        self.assertContains(response, '15,000')
    
    def test_dashboard_no_data(self):
        """Dashboard should show warning when no calc_date exists."""
        # Delete all urgency scores to clear calc_date
        UrgencyScore.objects.all().delete()
        RegimeState.objects.all().delete()
        
        client = Client()
        response = client.get('/risk/dashboard/')
        
        self.assertContains(response, 'No risk data available')
    
    def test_dashboard_show_excluded_toggle(self):
        """Dashboard should accept show_excluded query parameter."""
        client = Client()
        response = client.get('/risk/dashboard/?show_excluded=true')
        
        self.assertEqual(response.status_code, 200)


class AccountSummaryViewTest(TestCase):
    """Tests for the account summary view."""
    
    @classmethod
    def setUpTestData(cls):
        """Set up test data shared across all tests."""
        # Create accounts
        cls.account = Account.objects.create(
            name='RRSP_TOM',
            owner='TOM',
            category='RRSP')
        cls.taxable = Account.objects.create(
            name='TAXABLE_TOM',
            owner='TOM',
            category='TAXABLE')
        
        # Create owner configs
        OwnerConfig.objects.create(
            owner='TOM',
            marginal_tax_rate_ca=Decimal('0.5300'),
            capital_gains_inclusion_rate=Decimal('0.5000'),
            cost_tolerance_pct=Decimal('0.0200'),
        )
        
        # Create scoring config
        ScoringConfig.objects.create(
            config_key='threshold_low',
            config_value='25',
        )
        ScoringConfig.objects.create(
            config_key='threshold_medium',
            config_value='50',
        )
        ScoringConfig.objects.create(
            config_key='threshold_high',
            config_value='75',
        )
        
        cls.calc_date = datetime.date(2026, 6, 2)
        
        # Account urgency score
        UrgencyScore.objects.create(
            entity_type='account',
            entity_id='RRSP_TOM',
            calc_date=cls.calc_date,
            score_raw=Decimal('45.00'),
            urgency_level='Medium',
            component_m1_score=Decimal('50.00'),
            component_driver_score=Decimal('40.00'),
            component_regime_score=Decimal('45.00'),
            component_market_score=Decimal('40.00'),
            dominant_component='regime',
            component_scores_json='{}',
        )
        
        # Portfolio risk metrics for account
        PortfolioRiskMetrics.objects.create(
            entity_type='account',
            entity_id='RRSP_TOM',
            calc_date=cls.calc_date,
            var_portfolio_95_usd=Decimal('5000.00'),
            var_portfolio_95_pct=Decimal('0.0200'),
            total_market_value_usd=Decimal('250000.00'),
        )
        
        # Regime state
        RegimeState.objects.create(
            calc_date=cls.calc_date,
            quadrant='Q1',
            confidence_score=Decimal('72.50'),
            transition_flag=False,
            yield_curve_score=Decimal('45.00'),
            vix_score=Decimal('30.00'),
            cpi_score=Decimal('40.00'),
            pmi_score=Decimal('55.00'),
            gdp_score=Decimal('50.00'),
            prob_q1=Decimal('0.4500'),
            prob_q2=Decimal('0.2500'),
            prob_q3=Decimal('0.2000'),
            prob_q4=Decimal('0.1000'),
            indicator_snapshot_json='{}',
        )
    
    def test_account_summary_returns_200(self):
        """Account summary should return HTTP 200 for valid account."""
        client = Client()
        response = client.get('/risk/account/RRSP_TOM/')
        
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'risk/account_summary.html')
    
    def test_account_summary_contains_account_id(self):
        """Account summary should display the account ID."""
        client = Client()
        response = client.get('/risk/account/RRSP_TOM/')
        
        self.assertContains(response, 'RRSP_TOM')
    
    def test_account_summary_nonexistent_account(self):
        """Account summary should return 404 for non-existent account."""
        client = Client()
        response = client.get('/risk/account/NONEXISTENT/')
        
        self.assertEqual(response.status_code, 404)


class OwnerSummaryViewTest(TestCase):
    """Tests for the owner summary view."""
    
    @classmethod
    def setUpTestData(cls):
        """Set up test data shared across all tests."""
        # Create accounts
        Account.objects.create(
            owner='TOM',
            category='RRSP')
        Account.objects.create(
            owner='SUSAN',
            category='RRSP')
        
        # Create owner configs
        OwnerConfig.objects.create(
            owner='TOM',
            marginal_tax_rate_ca=Decimal('0.5300'),
            capital_gains_inclusion_rate=Decimal('0.5000'),
            cost_tolerance_pct=Decimal('0.0200'),
        )
        OwnerConfig.objects.create(
            owner='SUSAN',
            marginal_tax_rate_ca=Decimal('0.5300'),
            capital_gains_inclusion_rate=Decimal('0.5000'),
            cost_tolerance_pct=Decimal('0.0200'),
        )
        
        # Create scoring config
        ScoringConfig.objects.create(
            config_key='threshold_low',
            config_value='25',
        )
        ScoringConfig.objects.create(
            config_key='threshold_medium',
            config_value='50',
        )
        ScoringConfig.objects.create(
            config_key='threshold_high',
            config_value='75',
        )
        
        cls.calc_date = datetime.date(2026, 6, 2)
        
        # Owner urgency scores
        UrgencyScore.objects.create(
            entity_type='owner',
            entity_id='TOM',
            calc_date=cls.calc_date,
            score_raw=Decimal('55.00'),
            urgency_level='Medium',
            component_m1_score=Decimal('60.00'),
            component_driver_score=Decimal('50.00'),
            component_regime_score=Decimal('55.00'),
            component_market_score=Decimal('50.00'),
            dominant_component='m1_metrics',
            component_scores_json='{}',
        )
        UrgencyScore.objects.create(
            entity_type='owner',
            entity_id='SUSAN',
            calc_date=cls.calc_date,
            score_raw=Decimal('30.00'),
            urgency_level='Low',
            component_m1_score=Decimal('35.00'),
            component_driver_score=Decimal('25.00'),
            component_regime_score=Decimal('30.00'),
            component_market_score=Decimal('25.00'),
            dominant_component='regime',
            component_scores_json='{}',
        )
        
        # Account urgency scores
        UrgencyScore.objects.create(
            entity_type='account',
            entity_id='RRSP_TOM',
            calc_date=cls.calc_date,
            score_raw=Decimal('45.00'),
            urgency_level='Medium',
            component_m1_score=Decimal('50.00'),
            component_driver_score=Decimal('40.00'),
            component_regime_score=Decimal('45.00'),
            component_market_score=Decimal('40.00'),
            dominant_component='regime',
            component_scores_json='{}',
        )
        UrgencyScore.objects.create(
            entity_type='account',
            entity_id='RRSP_SUSAN',
            calc_date=cls.calc_date,
            score_raw=Decimal('25.00'),
            urgency_level='Low',
            component_m1_score=Decimal('30.00'),
            component_driver_score=Decimal('20.00'),
            component_regime_score=Decimal('25.00'),
            component_market_score=Decimal('20.00'),
            dominant_component='regime',
            component_scores_json='{}',
        )
        
        # Regime state
        RegimeState.objects.create(
            calc_date=cls.calc_date,
            quadrant='Q1',
            confidence_score=Decimal('72.50'),
            transition_flag=False,
            yield_curve_score=Decimal('45.00'),
            vix_score=Decimal('30.00'),
            cpi_score=Decimal('40.00'),
            pmi_score=Decimal('55.00'),
            gdp_score=Decimal('50.00'),
            prob_q1=Decimal('0.4500'),
            prob_q2=Decimal('0.2500'),
            prob_q3=Decimal('0.2000'),
            prob_q4=Decimal('0.1000'),
            indicator_snapshot_json='{}',
        )
    
    def test_owner_summary_returns_200(self):
        """Owner summary should return HTTP 200 for valid owner."""
        client = Client()
        response = client.get('/risk/owner/TOM/')
        
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'risk/owner_summary.html')
    
    def test_owner_summary_contains_owner_name(self):
        """Owner summary should display the owner name."""
        client = Client()
        response = client.get('/risk/owner/TOM/')
        
        self.assertContains(response, 'TOM')
    
    def test_owner_summary_404_invalid(self):
        """Owner summary should return 404 for invalid owner."""
        client = Client()
        response = client.get('/risk/owner/INVALID/')
        
        self.assertEqual(response.status_code, 404)


class HeatMapViewTest(TestCase):
    """Tests for the risk heat map view."""
    
    def setUp(self):
        """Set up test data for each test."""
        self.calc_date = datetime.date(2026, 6, 2)
        
        # Clear any existing data to avoid duplicate key errors
        UrgencyScore.objects.filter(calc_date=self.calc_date).delete()
        RegimeState.objects.filter(calc_date=self.calc_date).delete()
        MarketCondition.objects.filter(calc_date=self.calc_date).delete()
        PortfolioRiskMetrics.objects.filter(calc_date=self.calc_date).delete()
        
        # Create accounts (ignore errors if they already exist)
        Account.objects.get_or_create(
            name='RRSP_TOM',
            defaults={'owner': 'TOM', 'category': 'RRSP'}
        )
        Account.objects.get_or_create(
            name='RRSP_SUSAN',
            defaults={'owner': 'SUSAN', 'category': 'RRSP'}
        )
        
        # Create owner configs (ignore errors if they already exist)
        from django.db import IntegrityError
        try:
            OwnerConfig.objects.get(owner='TOM')
        except OwnerConfig.DoesNotExist:
            OwnerConfig.objects.create(
                owner='TOM',
                marginal_tax_rate_ca=Decimal('0.5300'),
                capital_gains_inclusion_rate=Decimal('0.5000'),
                cost_tolerance_pct=Decimal('0.0200'),
            )
        try:
            OwnerConfig.objects.get(owner='SUSAN')
        except OwnerConfig.DoesNotExist:
            OwnerConfig.objects.create(
                owner='SUSAN',
                marginal_tax_rate_ca=Decimal('0.5300'),
                capital_gains_inclusion_rate=Decimal('0.5000'),
                cost_tolerance_pct=Decimal('0.0200'),
            )
        
        # Create scoring config (ignore errors if they already exist)
        for key, value in [('threshold_low', '25'), ('threshold_medium', '50'), ('threshold_high', '75')]:
            try:
                ScoringConfig.objects.get(config_key=key)
            except ScoringConfig.DoesNotExist:
                ScoringConfig.objects.create(
                    config_key=key,
                    config_value=value,
                )
        
        self.calc_date = datetime.date(2026, 6, 2)
        
        # Security urgency scores (security_in_account)
        UrgencyScore.objects.create(
            entity_type='security_in_account',
            entity_id='RRSP_TOM',
            symbol='VFV',
            account_id='RRSP_TOM',
            calc_date=self.calc_date,
            score_raw=Decimal('80.00'),
            urgency_level='Critical',
            component_m1_score=Decimal('85.00'),
            component_driver_score=Decimal('75.00'),
            component_regime_score=Decimal('80.00'),
            component_market_score=Decimal('75.00'),
            dominant_component='m1_metrics',
            component_scores_json='{}',
        )
        UrgencyScore.objects.create(
            entity_type='security_in_account',
            entity_id='RRSP_TOM',
            symbol='ZAG',
            account_id='RRSP_TOM',
            calc_date=self.calc_date,
            score_raw=Decimal('35.00'),
            urgency_level='Low',
            component_m1_score=Decimal('40.00'),
            component_driver_score=Decimal('30.00'),
            component_regime_score=Decimal('35.00'),
            component_market_score=Decimal('30.00'),
            dominant_component='regime',
            component_scores_json='{}',
        )
        UrgencyScore.objects.create(
            entity_type='security_in_account',
            entity_id='RRSP_SUSAN',
            symbol='VFV',
            account_id='RRSP_SUSAN',
            calc_date=self.calc_date,
            score_raw=Decimal('60.00'),
            urgency_level='High',
            component_m1_score=Decimal('65.00'),
            component_driver_score=Decimal('55.00'),
            component_regime_score=Decimal('60.00'),
            component_market_score=Decimal('55.00'),
            dominant_component='m1_metrics',
            component_scores_json='{}',
        )
        
        # Regime state
        RegimeState.objects.create(
            calc_date=self.calc_date,
            quadrant='Q1',
            confidence_score=Decimal('72.50'),
            transition_flag=False,
            yield_curve_score=Decimal('45.00'),
            vix_score=Decimal('30.00'),
            cpi_score=Decimal('40.00'),
            pmi_score=Decimal('55.00'),
            gdp_score=Decimal('50.00'),
            prob_q1=Decimal('0.4500'),
            prob_q2=Decimal('0.2500'),
            prob_q3=Decimal('0.2000'),
            prob_q4=Decimal('0.1000'),
            indicator_snapshot_json='{}',
        )
    
    def test_heatmap_returns_200(self):
        """Heat map should return HTTP 200."""
        client = Client()
        response = client.get('/risk/heatmap/')
        
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'risk/heatmap.html')
    
    def test_heatmap_contains_symbol(self):
        """Heat map should display symbols."""
        client = Client()
        response = client.get('/risk/heatmap/')
        
        self.assertContains(response, 'VFV')
        self.assertContains(response, 'ZAG')
    
    def test_heatmap_contains_urgency_badge(self):
        """Heat map should display urgency level badges."""
        client = Client()
        response = client.get('/risk/heatmap/')
        
        self.assertContains(response, 'Critical')
        self.assertContains(response, 'High')
        self.assertContains(response, 'Low')
    
    def test_heatmap_contains_urgency_css_class(self):
        """Heat map should render urgency CSS classes."""
        client = Client()
        response = client.get('/risk/heatmap/')
        
        self.assertContains(response, 'urgency-critical')
        self.assertContains(response, 'urgency-high')
        self.assertContains(response, 'urgency-low')
