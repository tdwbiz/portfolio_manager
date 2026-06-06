"""
Management command to seed rm_security_driver with reference data for all held symbols.

Usage:
    python manage.py seed_security_drivers
"""

from django.core.management.base import BaseCommand

from risk.models import SecurityDriver

SECURITY_DRIVERS_SEED = [
    ('AAPL', 60, 65, 15, 'Large-cap tech stock'),
    ('ADX', 55, 40, 45, 'CEF; liquidity risk'),
    ('AIS', 45, 40, 25, 'Dividend ETF'),
    ('BANK', 45, 65, 25, 'Canadian banks ETF'),
    ('CAGE', 45, 50, 25, 'Multi-asset ETF; verify if crypto'),
    ('CGDV', 45, 40, 15, 'Dividend ETF; large, liquid'),
    ('DRAM', 45, 75, 30, 'Semiconductor memory ETF'),
    ('ETHA', 80, 85, 25, 'Crypto ETF; highest risk tier'),
    ('GOOG', 60, 65, 15, 'Large-cap tech stock'),
    ('IBIT', 80, 85, 25, 'Crypto ETF; highest risk tier'),
    ('ICSH', 10, 10, 5, 'Ultra-short bond; near-cash'),
    ('IGM', 45, 65, 15, 'Tech growth ETF'),
    ('IWY', 45, 60, 20, 'Large-cap growth ETF'),
    ('MA', 60, 55, 15, 'Financial services stock'),
    ('NVDA', 60, 75, 15, 'High-vol semiconductor stock'),
    ('QQQ', 45, 65, 5, 'Nasdaq-100; most liquid ETF'),
    ('RBF2010', 12, 10, 5, 'CAD money market fund'),
    ('RBF2014', 12, 10, 5, 'USD money market fund'),
    ('SGOV', 10, 10, 5, 'T-bill ETF; near risk-free'),
    ('SMH', 45, 75, 20, 'Semiconductor ETF'),
    ('SOXX', 45, 75, 20, 'Semiconductor ETF'),
    ('SPY', 45, 50, 5, 'S&P 500; most liquid ETF'),
    ('V', 60, 55, 15, 'Financial services stock'),
    ('VDY', 45, 40, 15, 'Canadian dividend ETF'),
    ('VFV', 45, 50, 15, 'CAD S&P 500 ETF'),
    ('VOO', 45, 50, 5, 'USD S&P 500 ETF'),
    ('XDIV', 45, 40, 20, 'Canadian dividend ETF'),
    ('XEQT', 45, 50, 20, 'Canadian equity ETF'),
]


class Command(BaseCommand):
    help = 'Seed rm_security_driver with reference data for all held symbols.'

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0

        for symbol, asset_class_baseline, sector_cyclicality, liquidity_tier, notes in SECURITY_DRIVERS_SEED:
            obj, created = SecurityDriver.objects.update_or_create(
                symbol=symbol,
                defaults={
                    'asset_class_baseline': asset_class_baseline,
                    'sector_cyclicality': sector_cyclicality,
                    'liquidity_tier': liquidity_tier,
                    'notes': notes,
                },
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"Created: {symbol}"))
            else:
                updated_count += 1
                self.stdout.write(f"Updated: {symbol}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeding complete: {created_count} created, {updated_count} updated."
            )
        )
