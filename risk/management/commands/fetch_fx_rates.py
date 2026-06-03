"""
Management command: fetch_fx_rates

Fetch USD/CAD exchange rates from yFinance.

Usage:
    python manage.py fetch_fx_rates [--start YYYY-MM-DD] [--force-full]
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand

from risk.models import FxRate
from risk.services.yfinance_client import fetch_symbol

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_YEARS = getattr(settings, 'YFINANCE_DEFAULT_HISTORY_YEARS', 10)


class Command(BaseCommand):
    help = 'Fetch USD/CAD exchange rates from yFinance.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--start',
            type=str,
            default=None,
            help='Start date in YYYY-MM-DD format.',
        )
        parser.add_argument(
            '--force-full',
            action='store_true',
            help='Ignore existing data; fetch full history (up to 10 years).',
        )

    def handle(self, *args, **options):
        start_override = options['start']
        force_full = options['force_full']

        # Determine fetch range
        if start_override:
            start_date = date.fromisoformat(start_override)
            end_date = date.today() + timedelta(days=1)
        elif force_full:
            start_date = date.today() - timedelta(days=DEFAULT_HISTORY_YEARS * 365)
            end_date = date.today() + timedelta(days=1)
        else:
            # Incremental
            last_row = FxRate.objects.order_by('-date').first()
            if last_row:
                start_date = last_row.date + timedelta(days=1)
                end_date = date.today() + timedelta(days=1)
            else:
                start_date = date.today() - timedelta(days=DEFAULT_HISTORY_YEARS * 365)
                end_date = date.today() + timedelta(days=1)

        if start_date > end_date:
            self.stdout.write(self.style.WARNING('No new data to fetch.'))
            return

        # Fetch CAD=X from yFinance
        df = fetch_symbol('CAD=X', start_date, end_date)
        if df.empty:
            self.stdout.write(self.style.WARNING('No FX data returned from yFinance.'))
            return

        inserted = 0
        for dt, row in df.iterrows():
            close_val = float(row['Close']) if 'Close' in df.columns else 0.0
            # CAD=X Close is USD per 1 CAD; we store CAD per 1 USD
            usd_cad_rate = Decimal(str(round(1.0 / close_val, 6))) if close_val else Decimal('0')

            # Skip if already exists
            if FxRate.objects.filter(date=dt.date()).exists():
                continue

            FxRate.objects.create(
                date=dt.date(),
                usd_cad_rate=usd_cad_rate,
                data_source='yfinance',
            )
            inserted += 1

        # Verify most recent rate is within 3 business days of today
        latest = FxRate.objects.order_by('-date').first()
        if latest:
            days_diff = (date.today() - latest.date).days
            if days_diff > 5:
                self.stdout.write(
                    self.style.WARNING(
                        f'Most recent FX rate is {days_diff} days old '
                        f'(latest: {latest.date}). Expected within 3 business days.'
                    )
                )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(self.style.SUCCESS('FX Rate Fetch Summary'))
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(f'  Rows inserted: {inserted}')
        if latest:
            self.stdout.write(f'  Latest rate: {latest.usd_cad_rate} on {latest.date}')
        self.stdout.write(self.style.SUCCESS('=' * 50))
