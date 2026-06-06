"""
Management command: seed_regime_sensitivity

Populates rm_regime_sensitivity with default sensitivity multipliers
for each (asset_class, regime_quadrant) combination.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from risk.models import RegimeSensitivity

SEED_DATA = [
    # (asset_class, quadrant, multiplier, rationale)
    ('common_stock', 'Q1', 1.00, 'Growth stocks supported in growth/low inflation'),
    ('common_stock', 'Q2', 1.10, 'Growth stocks face inflation pressure'),
    ('common_stock', 'Q3', 1.30, 'Growth stocks worst in deflation/recession'),
    ('common_stock', 'Q4', 1.40, 'Growth stocks worst in stagflation'),
    ('etf_equity', 'Q1', 1.00, 'Broad equity ETFs normal in growth/low inflation'),
    ('etf_equity', 'Q2', 1.05, 'Broad equity ETFs slightly pressured by inflation'),
    ('etf_equity', 'Q3', 1.20, 'Broad equity ETFs decline in recession'),
    ('etf_equity', 'Q4', 1.30, 'Broad equity ETFs worst in stagflation'),
    ('etf_equity (dividend)', 'Q1', 0.90, 'Dividend ETFs more defensive in growth'),
    ('etf_equity (dividend)', 'Q2', 1.00, 'Dividend ETFs neutral in reflation'),
    ('etf_equity (dividend)', 'Q3', 1.10, 'Dividend ETFs defensive in recession'),
    ('etf_equity (dividend)', 'Q4', 1.20, 'Dividend ETFs moderate in stagflation'),
    ('cef', 'Q1', 1.00, 'CEFs normal in growth/low inflation'),
    ('cef', 'Q2', 1.05, 'CEFs slightly pressured by inflation'),
    ('cef', 'Q3', 1.20, 'CEFs add liquidity risk in recession'),
    ('cef', 'Q4', 1.25, 'CEFs stressed in stagflation'),
    ('crypto_etf', 'Q1', 1.20, 'Crypto volatile even in growth'),
    ('crypto_etf', 'Q2', 1.30, 'Crypto very volatile in inflation'),
    ('crypto_etf', 'Q3', 1.50, 'Crypto crashes in recession'),
    ('crypto_etf', 'Q4', 1.60, 'Crypto worst in stagflation'),
    ('cash_equiv', 'Q1', 0.60, 'Cash is a haven; lower risk score'),
    ('cash_equiv', 'Q2', 0.70, 'Cash loses to inflation but safe'),
    ('cash_equiv', 'Q3', 0.70, 'Cash safe in recession'),
    ('cash_equiv', 'Q4', 0.80, 'Cash safe but inflation erodes value'),
    ('mutual_fund', 'Q1', 0.60, 'Money market funds very stable'),
    ('mutual_fund', 'Q2', 0.65, 'Money market funds stable in inflation'),
    ('mutual_fund', 'Q3', 0.70, 'Money market funds stable in recession'),
    ('mutual_fund', 'Q4', 0.75, 'Money market funds stable in stagflation'),
]


class Command(BaseCommand):
    help = 'Seed rm_regime_sensitivity with default sensitivity multipliers'

    def handle(self, *args, **options):
        created = 0
        for asset_class, quadrant, multiplier, rationale in SEED_DATA:
            _, c = RegimeSensitivity.objects.update_or_create(
                asset_class=asset_class,
                regime_quadrant=quadrant,
                defaults={
                    'sensitivity_multiplier': multiplier,
                    'rationale': rationale,
                },
            )
            if c:
                created += 1

        self.stdout.write(
            self.style.SUCCESS(f"Seeded {created} new regime sensitivity records")
        )
