"""
Management command: refresh_market_data

Master refresh command that orchestrates all data fetch commands in the
correct order. Called daily by cron or Windows Task Scheduler.

Usage:
    python manage.py refresh_market_data [--full] [--dry-run] [--symbol SYM]
"""

from __future__ import annotations

import logging
from datetime import date

from django.core.management import call_command
from django.core.management.base import BaseCommand

from risk.services.gap_detection import detect_price_gaps

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Master refresh command: orchestrates all data fetch commands '
        'in the correct order.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--full',
            action='store_true',
            help='Force full re-fetch of all data sources.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without writing to database.',
        )
        parser.add_argument(
            '--symbol',
            type=str,
            default=None,
            help='Process only this symbol.',
        )

    def handle(self, *args, **options):
        full = options['full']
        dry_run = options['dry_run']
        symbol = options['symbol']

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS('Market Data Refresh — Starting'))
        self.stdout.write(self.style.SUCCESS('=' * 60))

        start_time = date.today()

        # 1. Fetch FX rates (needed first)
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Step 1/6: Fetching FX rates...'))
        try:
            call_command(
                'fetch_fx_rates',
                force_full=full,
                start=start_time.isoformat() if full else None,
            )
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f'FX rate fetch failed: {exc}')
            )

        # 2. Import Amibroker prices
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Step 2/6: Importing Amibroker prices...'))
        try:
            call_command(
                'import_amibroker_prices',
                symbol=symbol,
                dry_run=dry_run,
            )
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f'Amibroker import failed: {exc}')
            )

        # 3. Fetch yFinance prices
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Step 3/6: Fetching yFinance prices...'))
        try:
            call_command(
                'fetch_yfinance_prices',
                symbol=symbol,
                force_full=full,
                start=start_time.isoformat() if full else None,
            )
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f'yFinance fetch failed: {exc}')
            )

        # 4. Fetch economic indicators
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Step 4/6: Fetching economic indicators...'))
        try:
            call_command(
                'fetch_economic_indicators',
                force_full=full,
                start=start_time.isoformat() if full else None,
            )
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f'Economic indicators fetch failed: {exc}')
            )

        # 5. Compute volatility
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Step 5/6: Computing volatility...'))
        try:
            call_command(
                'compute_volatility',
                symbol=symbol,
            )
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f'Volatility computation failed: {exc}')
            )

        # 6. Detect price gaps
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Step 6/6: Detecting price gaps...'))
        try:
            gaps = detect_price_gaps(symbols=[symbol] if symbol else None)
            if gaps:
                for gap in gaps:
                    self.stdout.write(
                        self.style.WARNING(
                            f'  Gap: {gap.symbol} {gap.gap_start} → {gap.gap_end} '
                            f'({gap.gap_days} days, {gap.severity})'
                        )
                    )
                self.stdout.write(
                    self.style.WARNING(f'  Total gaps detected: {len(gaps)}')
                )
            else:
                self.stdout.write(self.style.SUCCESS('  No gaps detected.'))
        except Exception as exc:
            self.stdout.write(
                self.style.ERROR(f'Gap detection failed: {exc}')
            )

        # Final summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS('Market Data Refresh — Complete'))
        self.stdout.write(self.style.SUCCESS('=' * 60))
