"""
Management command: seed_owner_config

Seeds OwnerConfig records for each owner (TOM, SUSAN) with placeholder tax rates.
These rates MUST be reviewed and updated by the owner before running recommendations.

Safe to run repeatedly — upserts by owner using update_or_create.

Usage:
    python manage.py seed_owner_config --settings=portfolio_manager.settings_dev
"""

from django.core.management.base import BaseCommand
from risk.models import OwnerConfig


OWNER_CONFIG_SEED = [
    {
        'owner': 'TOM',
        'marginal_tax_rate_ca': 0.4326,  # Placeholder — must be updated by owner
        'capital_gains_inclusion_rate': 0.5000,
        'cost_tolerance_pct': 0.0200,
        'preferences_json': '{"max_option_expiry_months": 6, "preferred_trail_pct": 0.10}',
    },
    {
        'owner': 'SUSAN',
        'marginal_tax_rate_ca': 0.4326,  # Placeholder — must be updated by owner
        'capital_gains_inclusion_rate': 0.5000,
        'cost_tolerance_pct': 0.0200,
        'preferences_json': '{"max_option_expiry_months": 6, "preferred_trail_pct": 0.10}',
    },
]


class Command(BaseCommand):
    help = 'Seed OwnerConfig records with placeholder tax rates.'

    def handle(self, *args, **options):
        created = 0
        updated = 0

        for config_data in OWNER_CONFIG_SEED:
            oc, is_created = OwnerConfig.objects.update_or_create(
                owner=config_data['owner'],
                defaults={
                    'marginal_tax_rate_ca': config_data['marginal_tax_rate_ca'],
                    'capital_gains_inclusion_rate': config_data['capital_gains_inclusion_rate'],
                    'cost_tolerance_pct': config_data['cost_tolerance_pct'],
                    'preferences_json': config_data['preferences_json'],
                    'updated_by': 'seed_owner_config',
                },
            )
            if is_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created OwnerConfig for {oc.owner}"))
            else:
                updated += 1
                self.stdout.write(self.style.WARNING(f"Updated OwnerConfig for {oc.owner}"))

        self.stdout.write(
            self.style.WARNING(
                "\n*** IMPORTANT: Review and update marginal tax rates in Django admin ***\n"
                "Current placeholder rates are 43.26% for both TOM and SUSAN.\n"
                "Run: python manage.py shell\n"
                "  >>> from risk.models import OwnerConfig\n"
                "  >>> for oc in OwnerConfig.objects.all():\n"
                "  ...     print(f'{oc.owner}: {oc.marginal_tax_rate_ca * 100:.2f}% marginal, "
                f"{oc.capital_gains_inclusion_rate * 100:.2f}% CG inclusion')\n"
            )
        )
        self.stdout.write(f"Done. Created: {created}, Updated: {updated}")
