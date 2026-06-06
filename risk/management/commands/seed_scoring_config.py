"""
Management command to seed rm_scoring_config with operational values.

Usage:
    python manage.py seed_scoring_config
"""

from django.core.management.base import BaseCommand

from risk.models import ScoringConfig

SCORING_CONFIG_SEED = [
    # Component weights
    ('weight_m1_metrics',       '0.35', 'Weight of M1 metric severity component'),
    ('weight_security_driver',  '0.20', 'Weight of security driver tier component'),
    ('weight_regime',           '0.25', 'Weight of regime adjustment component'),
    ('weight_market_condition', '0.20', 'Weight of market condition component'),

    # Urgency thresholds
    ('threshold_low',    '25', 'Score below this = Low urgency'),
    ('threshold_medium', '50', 'Score 25-50 = Medium urgency'),
    ('threshold_high',   '75', 'Score 50-75 = High; above = Critical'),

    # Adjustments
    ('earnings_proximity_boost',    '10',   'Score boost when earnings within 14 days'),
    ('regime_transition_dampener',  '0.80', 'Multiplier on regime score during transitions'),

    # Portfolio alerts (used by M7)
    ('var_portfolio_alert_pct',    '5.0',    'Alert if portfolio VaR > this % of portfolio value'),
    ('concentration_alert_pct',    '20.0',   'Alert if any symbol > this % of portfolio'),
    ('taxable_ugl_alert_usd',      '100000', 'Tax risk flag if taxable UGL exceeds this (USD)'),

    # Owner attribution
    ('joint_account_owner_split',  '0.50', 'Each owner gets this fraction of joint account positions'),
]


class Command(BaseCommand):
    help = 'Seed rm_scoring_config with operational values.'

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0

        for config_key, config_value, description in SCORING_CONFIG_SEED:
            obj, created = ScoringConfig.objects.update_or_create(
                config_key=config_key,
                defaults={
                    'config_value': config_value,
                    'description': description,
                },
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"Created: {config_key} = {config_value}"))
            else:
                updated_count += 1
                self.stdout.write(f"Updated: {config_key} = {config_value}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeding complete: {created_count} created, {updated_count} updated."
            )
        )
