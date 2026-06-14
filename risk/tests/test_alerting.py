"""
Tests for M7-Spec-1: Alerting & Continuous Monitoring.

Covers:
- alert_engine.py detection functions (unit tests)
- AlertConfig & AlertHistory models
- seed_alert_configs command
- evaluate_alerts command
- alert_email service
- alerts helpers (badge count, acknowledge)
- context processor
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch, MagicMock

from risk.models import AlertConfig, AlertHistory
from risk.services import alert_engine, alerts
from risk.services.alert_email import send_alert_email, send_alert_digest


# =============================================================================
# Detection function tests
# =============================================================================


class UrgencyLevelChangeTests(TestCase):
    """Test detect_urgency_level_changes."""

    def test_detects_change_above_threshold(self):
        current = {'P1': 0.8, 'P2': 0.3}
        previous = {'P1': 0.6, 'P2': 0.3}
        results = alert_engine.detect_urgency_level_changes(current, previous)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['entity_id'], 'P1')
        self.assertIn('UP', results[0]['entity_description'])

    def test_ignores_small_changes(self):
        current = {'P1': 0.61, 'P2': 0.5}
        previous = {'P1': 0.6, 'P2': 0.4}
        results = alert_engine.detect_urgency_level_changes(current, previous, threshold=Decimal('0.15'))
        self.assertEqual(len(results), 0)

    def test_detects_downward_change(self):
        current = {'P1': 0.3}
        previous = {'P1': 0.6}
        results = alert_engine.detect_urgency_level_changes(current, previous)
        self.assertEqual(len(results), 1)
        self.assertIn('DOWN', results[0]['entity_description'])

    def test_ignores_missing_previous(self):
        current = {'P1': 0.8}
        previous = {}
        results = alert_engine.detect_urgency_level_changes(current, previous)
        self.assertEqual(len(results), 0)


class VarPortfolioBreachTests(TestCase):
    """Test detect_var_portfolio_breach."""

    def test_detects_breach(self):
        var_results = [
            {'portfolio_id': 'P1', 'var_95': 150000, 'var_99': 200000, 'portfolio_value': 1000000},
            {'portfolio_id': 'P2', 'var_95': 50000, 'var_99': 75000, 'portfolio_value': 500000},
        ]
        results = alert_engine.detect_var_portfolio_breach(var_results, threshold=Decimal('100000'))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['entity_id'], 'P1')

    def test_ignores_below_threshold(self):
        var_results = [
            {'portfolio_id': 'P1', 'var_95': 50000, 'var_99': 75000, 'portfolio_value': 500000},
        ]
        results = alert_engine.detect_var_portfolio_breach(var_results, threshold=Decimal('100000'))
        self.assertEqual(len(results), 0)


class RegimeShiftTests(TestCase):
    """Test detect_regime_shift."""

    def test_detects_regime_change(self):
        current = {'P1': 'HIGH', 'P2': 'MEDIUM'}
        previous = {'P1': 'LOW', 'P2': 'MEDIUM'}
        results = alert_engine.detect_regime_shift(current, previous)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['entity_id'], 'P1')

    def test_detects_new_portfolio(self):
        current = {'P1': 'HIGH'}
        previous = {}
        results = alert_engine.detect_regime_shift(current, previous)
        self.assertEqual(len(results), 1)

    def test_ignores_same_regime(self):
        current = {'P1': 'LOW', 'P2': 'MEDIUM'}
        previous = {'P1': 'LOW', 'P2': 'MEDIUM'}
        results = alert_engine.detect_regime_shift(current, previous)
        self.assertEqual(len(results), 0)


class DeathCrossTests(TestCase):
    """Test detect_death_crosses."""

    def test_detects_death_cross(self):
        ma_data = [
            {'symbol': 'AAPL', 'ma_50': 140, 'ma_200': 150, 'calc_date': date(2026, 6, 10)},
            {'symbol': 'MSFT', 'ma_50': 380, 'ma_200': 350, 'calc_date': date(2026, 6, 10)},
        ]
        results = alert_engine.detect_death_crosses(ma_data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['symbol'], 'AAPL')

    def test_ignores_golden_cross(self):
        ma_data = [
            {'symbol': 'AAPL', 'ma_50': 160, 'ma_200': 150, 'calc_date': date(2026, 6, 10)},
        ]
        results = alert_engine.detect_death_crosses(ma_data)
        self.assertEqual(len(results), 0)


class EarningsProximityTests(TestCase):
    """Test detect_earnings_proximity."""

    def test_detects_earnings_within_threshold(self):
        earnings_data = [
            {'symbol': 'AAPL', 'earnings_date': date(2026, 6, 15), 'days_away': 5},
            {'symbol': 'MSFT', 'earnings_date': date(2026, 7, 1), 'days_away': 21},
        ]
        results = alert_engine.detect_earnings_proximity(earnings_data, days_threshold=7)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['symbol'], 'AAPL')

    def test_ignores_far_earnings(self):
        earnings_data = [
            {'symbol': 'AAPL', 'earnings_date': date(2026, 7, 1), 'days_away': 21},
        ]
        results = alert_engine.detect_earnings_proximity(earnings_data, days_threshold=7)
        self.assertEqual(len(results), 0)


class ConcentrationBreachTests(TestCase):
    """Test detect_concentration_breach."""

    def test_detects_concentration_breach(self):
        weights = {'AAPL': 0.25, 'MSFT': 0.18, 'GOOGL': 0.10}
        results = alert_engine.detect_concentration_breach(weights, threshold=Decimal('0.20'))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['symbol'], 'AAPL')

    def test_ignores_below_threshold(self):
        weights = {'AAPL': 0.15, 'MSFT': 0.15, 'GOOGL': 0.15}
        results = alert_engine.detect_concentration_breach(weights, threshold=Decimal('0.20'))
        self.assertEqual(len(results), 0)


class TaxableGainBracketTests(TestCase):
    """Test detect_taxable_gain_brackets."""

    def test_detects_high_gain(self):
        gain_data = [
            {'symbol': 'AAPL', 'unrealized_gain': 15000, 'tax_bracket': '22%', 'next_bracket_threshold': 40000},
            {'symbol': 'MSFT', 'unrealized_gain': 5000, 'tax_bracket': '22%', 'next_bracket_threshold': 40000},
        ]
        results = alert_engine.detect_taxable_gain_brackets(gain_data, threshold=Decimal('10000'))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['symbol'], 'AAPL')

    def test_ignores_low_gain(self):
        gain_data = [
            {'symbol': 'AAPL', 'unrealized_gain': 5000, 'tax_bracket': '22%', 'next_bracket_threshold': 40000},
        ]
        results = alert_engine.detect_taxable_gain_brackets(gain_data, threshold=Decimal('10000'))
        self.assertEqual(len(results), 0)


# =============================================================================
# Model tests
# =============================================================================


class AlertConfigModelTests(TestCase):
    """Test AlertConfig model."""

    def test_creates_with_required_fields(self):
        config = AlertConfig.objects.create(
            alert_type='death_cross',
            is_active=True,
        )
        self.assertEqual(str(config), 'death_cross — ALL / ALL / ALL')
        self.assertIsNotNone(config.created_at)
        self.assertIsNotNone(config.updated_at)

    def test_scope_filters(self):
        config = AlertConfig.objects.create(
            alert_type='concentration_breach',
            owner='TOM',
            account_id='TAXABLE_TOM',
            symbol='AAPL',
            threshold_value=0.15,
            threshold_operator='>',
            notify_email='tom@example.com',
            description='Test concentration alert',
        )
        self.assertEqual(config.owner, 'TOM')
        self.assertEqual(float(config.threshold_value), 0.15)

    def test_str_representation(self):
        config = AlertConfig.objects.create(
            alert_type='regime_shift',
            owner='TOM',
        )
        self.assertEqual(str(config), 'regime_shift — TOM / ALL / ALL')


class AlertHistoryModelTests(TestCase):
    """Test AlertHistory model."""

    def setUp(self):
        self.config = AlertConfig.objects.create(
            alert_type='death_cross',
            is_active=True,
        )

    def test_creates_with_required_fields(self):
        history = AlertHistory.objects.create(
            alert_config=self.config,
            alert_type='death_cross',
            calc_date=date(2026, 6, 10),
            entity_description='Death cross: AAPL',
            entity_type='symbol',
            entity_id='AAPL',
            symbol='AAPL',
            trigger_value='140.00',
        )
        self.assertIsNotNone(history.triggered_at)
        self.assertEqual(str(history), 'death_cross — Death cross: AAPL (2026-06-10)')

    def test_acknowledge(self):
        history = AlertHistory.objects.create(
            alert_config=self.config,
            alert_type='death_cross',
            calc_date=date(2026, 6, 10),
            entity_description='Death cross: AAPL',
            entity_type='symbol',
            entity_id='AAPL',
            trigger_value='140.00',
        )
        history.acknowledged_at = timezone.make_aware(timezone.datetime(2026, 6, 11))
        history.acknowledged_by = 'tom'
        history.save()
        self.assertIsNotNone(history.acknowledged_at)
        self.assertEqual(history.acknowledged_by, 'tom')


# =============================================================================
# Command tests
# =============================================================================


class SeedAlertConfigsCommandTests(TestCase):
    """Test seed_alert_configs management command."""

    def test_seeds_default_configs(self):
        call_command('seed_alert_configs')
        self.assertEqual(AlertConfig.objects.count(), 7)

    def test_idempotent(self):
        call_command('seed_alert_configs')
        count_after_first = AlertConfig.objects.count()
        call_command('seed_alert_configs')
        count_after_second = AlertConfig.objects.count()
        self.assertEqual(count_after_first, count_after_second)

    def test_creces_all_seven_types(self):
        call_command('seed_alert_configs')
        types = set(AlertConfig.objects.values_list('alert_type', flat=True))
        expected = {
            'urgency_level_change', 'var_portfolio_breach', 'regime_shift',
            'death_cross', 'earnings_proximity', 'concentration_breach',
            'taxable_gain_bracket',
        }
        self.assertEqual(types, expected)


class EvaluateAlertsCommandTests(TestCase):
    """Test evaluate_alerts management command."""

    def setUp(self):
        self.config = AlertConfig.objects.create(
            alert_type='death_cross',
            is_active=True,
            notify_email='',
        )

    @patch('risk.services.alert_engine.detect_death_crosses')
    def test_dry_run(self, mock_detect):
        mock_detect.return_value = [
            {
                'entity_type': 'symbol',
                'entity_id': 'AAPL',
                'entity_description': 'Death cross: AAPL',
                'trigger_value': '140.00',
                'previous_value': '',
                'symbol': 'AAPL',
            }
        ]
        call_command('evaluate_alerts', calc_date=date(2026, 6, 10), dry_run=True)
        self.assertEqual(AlertHistory.objects.count(), 0)

    @patch('risk.services.alert_engine.detect_death_crosses')
    def test_writes_alert_history(self, mock_detect):
        mock_detect.return_value = [
            {
                'entity_type': 'symbol',
                'entity_id': 'AAPL',
                'entity_description': 'Death cross: AAPL',
                'trigger_value': '140.00',
                'previous_value': '',
                'symbol': 'AAPL',
            }
        ]
        call_command('evaluate_alerts', calc_date=date(2026, 6, 10))
        self.assertEqual(AlertHistory.objects.count(), 1)
        history = AlertHistory.objects.first()
        self.assertEqual(history.alert_type, 'death_cross')
        self.assertEqual(history.entity_id, 'AAPL')

    @patch('risk.services.alert_engine.detect_death_crosses')
    def test_idempotent_no_duplicates(self, mock_detect):
        mock_detect.return_value = [
            {
                'entity_type': 'symbol',
                'entity_id': 'AAPL',
                'entity_description': 'Death cross: AAPL',
                'trigger_value': '140.00',
                'previous_value': '',
                'symbol': 'AAPL',
            }
        ]
        call_command('evaluate_alerts', calc_date=date(2026, 6, 10))
        call_command('evaluate_alerts', calc_date=date(2026, 6, 10))
        self.assertEqual(AlertHistory.objects.count(), 1)

    @patch('risk.services.alert_engine.detect_death_crosses')
    def test_no_active_configs(self, mock_detect):
        self.config.is_active = False
        self.config.save()
        call_command('evaluate_alerts', calc_date=date(2026, 6, 10))
        self.assertEqual(AlertHistory.objects.count(), 0)


# =============================================================================
# Email service tests
# =============================================================================


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='test@example.com',
)
class AlertEmailTests(TestCase):
    """Test alert_email service."""

    def test_send_alert_email_success(self):
        result = send_alert_email(
            recipient='tom@example.com',
            alert_type='death_cross',
            entity_description='Death cross: AAPL',
            trigger_value='140.00',
            previous_value='',
            calc_date=date(2026, 6, 10),
            symbol='AAPL',
        )
        self.assertTrue(result)

    @patch('risk.services.alert_email.send_mail')
    def test_send_alert_email_failure(self, mock_send_mail):
        mock_send_mail.side_effect = Exception('SMTP error')
        result = send_alert_email(
            recipient='tom@example.com',
            alert_type='death_cross',
            entity_description='Death cross: AAPL',
            trigger_value='140.00',
            previous_value='',
            calc_date=date(2026, 6, 10),
            symbol='AAPL',
        )
        self.assertFalse(result)

    def test_send_alert_digest(self):
        alerts_list = [
            {'alert_type': 'death_cross', 'entity_description': 'Death cross: AAPL',
             'trigger_value': '140.00', 'symbol': 'AAPL'},
            {'alert_type': 'regime_shift', 'entity_description': 'Regime: P1 → HIGH',
             'trigger_value': 'HIGH', 'symbol': ''},
        ]
        sent = send_alert_digest(
            recipients=['tom@example.com', 'susan@example.com'],
            alerts=alerts_list,
            calc_date=date(2026, 6, 10),
        )
        self.assertEqual(sent, 2)


# =============================================================================
# Alert helpers tests
# =============================================================================


class AlertHelpersTests(TestCase):
    """Test alerts service helpers."""

    def setUp(self):
        self.config = AlertConfig.objects.create(
            alert_type='death_cross',
            is_active=True,
        )
        # Create alerts with different ages
        self.recent = AlertHistory.objects.create(
            alert_config=self.config,
            alert_type='death_cross',
            calc_date=date(2026, 6, 10),
            entity_description='Death cross: AAPL',
            entity_type='symbol',
            entity_id='AAPL',
            trigger_value='140.00',
            triggered_at=timezone.make_aware(timezone.datetime(2026, 6, 10, 12, 0, 0)),
        )
        self.acknowledged = AlertHistory.objects.create(
            alert_config=self.config,
            alert_type='death_cross',
            calc_date=date(2026, 6, 9),
            entity_description='Death cross: MSFT',
            entity_type='symbol',
            entity_id='MSFT',
            trigger_value='380.00',
            triggered_at=timezone.make_aware(timezone.datetime(2026, 6, 9, 12, 0, 0)),
            acknowledged_at=timezone.make_aware(timezone.datetime(2026, 6, 9, 14, 0, 0)),
            acknowledged_by='tom',
        )

    def test_get_unacknowledged_count(self):
        count = alerts.get_unacknowledged_alert_count()
        self.assertEqual(count, 1)  # Only recent, not acknowledged

    def test_acknowledge_single(self):
        result = alerts.acknowledge_alert(self.recent.pk, user='tom')
        self.assertTrue(result)
        self.recent.refresh_from_db()
        self.assertIsNotNone(self.recent.acknowledged_at)
        self.assertEqual(self.recent.acknowledged_by, 'tom')

    def test_acknowledge_nonexistent(self):
        result = alerts.acknowledge_alert(99999, user='tom')
        self.assertFalse(result)

    def test_acknowledge_already_acknowledged(self):
        result = alerts.acknowledge_alert(self.acknowledged.pk, user='susan')
        self.assertFalse(result)

    def test_acknowledge_multiple(self):
        # Create more alerts
        a2 = AlertHistory.objects.create(
            alert_config=self.config,
            alert_type='death_cross',
            calc_date=date(2026, 6, 10),
            entity_description='Death cross: GOOGL',
            entity_type='symbol',
            entity_id='GOOGL',
            trigger_value='200.00',
            triggered_at=timezone.make_aware(timezone.datetime(2026, 6, 10, 13, 0, 0)),
        )
        count = alerts.acknowledge_alerts([self.recent.pk, a2.pk], user='tom')
        self.assertEqual(count, 2)


# =============================================================================
# Context processor test
# =============================================================================


class ContextProcessorTests(TestCase):
    """Test alert_badge context processor."""

    @patch('risk.services.alerts.get_unacknowledged_alert_count', return_value=5)
    def test_provides_count(self, mock_count):
        """Context processor should always provide the key, even if 0."""
        from core.context_processors import alert_badge
        result = alert_badge(None)
        self.assertIn('unacknowledged_alert_count', result)
        self.assertEqual(result['unacknowledged_alert_count'], 5)

    @patch('risk.services.alerts.get_unacknowledged_alert_count', side_effect=Exception('DB error'))
    def test_handles_exceptions_gracefully(self, mock_count):
        """Context processor should return 0 on any exception."""
        from core.context_processors import alert_badge
        result = alert_badge(None)
        self.assertEqual(result['unacknowledged_alert_count'], 0)
