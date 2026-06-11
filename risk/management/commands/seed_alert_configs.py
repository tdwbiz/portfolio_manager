"""
Seed command for M7-Spec-1 alerting.

Creates default AlertConfig rows on first run.  Idempotent — does not
duplicate existing rows.
"""

from django.core.management.base import BaseCommand
from risk.models import AlertConfig


DEFAULT_ALERTS = [
    {
        'alert_type': 'urgency_level_change',
        'owner': None,
        'account_id': None,
        'symbol': None,
        'threshold_value': 0.15,
        'threshold_operator': '>=',
        'is_active': True,
        'notify_email': '',
        'description': 'Urgency score changed by >= 0.15',
    },
    {
        'alert_type': 'var_portfolio_breach',
        'owner': None,
        'account_id': None,
        'symbol': None,
        'threshold_value': 100000,
        'threshold_operator': '>',
        'is_active': True,
        'notify_email': '',
        'description': '95% VaR exceeds $100,000',
    },
    {
        'alert_type': 'regime_shift',
        'owner': None,
        'account_id': None,
        'symbol': None,
        'threshold_value': None,
        'threshold_operator': '==',
        'is_active': True,
        'notify_email': '',
        'description': 'Risk regime changed for any portfolio',
    },
    {
        'alert_type': 'death_cross',
        'owner': None,
        'account_id': None,
        'symbol': None,
        'threshold_value': None,
        'threshold_operator': '==',
        'is_active': True,
        'notify_email': '',
        'description': '50-day MA crosses below 200-day MA',
    },
    {
        'alert_type': 'earnings_proximity',
        'owner': None,
        'account_id': None,
        'symbol': None,
        'threshold_value': 7,
        'threshold_operator': '<=',
        'is_active': True,
        'notify_email': '',
        'description': 'Earnings announcement within 7 days',
    },
    {
        'alert_type': 'concentration_breach',
        'owner': None,
        'account_id': None,
        'symbol': None,
        'threshold_value': 0.20,
        'threshold_operator': '>',
        'is_active': True,
        'notify_email': '',
        'description': 'Single position exceeds 20% of portfolio',
    },
    {
        'alert_type': 'taxable_gain_bracket',
        'owner': None,
        'account_id': None,
        'symbol': None,
        'threshold_value': 10000,
        'threshold_operator': '>=',
        'is_active': True,
        'notify_email': '',
        'description': 'Unrealized gain >= $10,000',
    },
]


class Command(BaseCommand):
    help = 'Seed default alert configurations (idempotent).'

    def handle(self, *args, **options):
        seeded = 0
        for defaults in DEFAULT_ALERTS:
            obj, created = AlertConfig.objects.get_or_create(
                alert_type=defaults['alert_type'],
                owner=defaults['owner'],
                account_id=defaults['account_id'],
                symbol=defaults['symbol'],
                defaults=defaults,
            )
            if created:
                seeded += 1
                self.stdout.write(
                    self.style.SUCCESS(f'Created: {obj}')
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f'Skipped (exists): {obj}')
                )

        self.stdout.write(
            self.style.SUCCESS(f'Seeded {seeded}/{len(DEFAULT_ALERTS)} alert configs.')
        )
