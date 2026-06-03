"""
Management command: fetch_economic_indicators

Fetch economic indicators from yFinance and store in rm_economic_indicators.

Usage:
    python manage.py fetch_economic_indicators [--start YYYY-MM-DD] [--force-full]
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand

from risk.models import EconomicIndicator
from risk.services.yfinance_client import fetch_symbol

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_YEARS = getattr(settings, 'YFINANCE_DEFAULT_HISTORY_YEARS', 10)

# Fetchable indicators
FETCHABLE_INDICATORS = {
    'vix_close': '^VIX',
    'yield_10y': '^TNX',
    'yield_2y': '^IRX',  # Note: ^IRX is 13-week T-bill, not 2-year
}

# Future upgrades (not available via free yFinance)
# cpi_yoy: FRED CPIAUCSL — requires API key
# pmi_composite: ISM/Markit — no free API
# gdp_growth_qoq: FRED A191RL1Q225SBEA — requires API key
# unemployment_rate: FRED UNRATE — requires API key


class Command(BaseCommand):
    help = 'Fetch economic indicators from yFinance.'

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
            # Incremental: find the latest date across all indicators
            latest = EconomicIndicator.objects.order_by('-date').first()
            if latest:
                start_date = latest.date + timedelta(days=1)
                end_date = date.today() + timedelta(days=1)
            else:
                start_date = date.today() - timedelta(days=DEFAULT_HISTORY_YEARS * 365)
                end_date = date.today() + timedelta(days=1)

        if start_date > end_date:
            self.stdout.write(self.style.WARNING('No new data to fetch.'))
            return

        total_inserted = 0

        # Fetch each indicator
        for indicator_name, ticker in FETCHABLE_INDICATORS.items():
            inserted = self._fetch_indicator(indicator_name, ticker, start_date, end_date)
            total_inserted += inserted
            self.stdout.write(f'  {indicator_name}: {inserted} rows inserted')

        # Compute yield_curve_slope
        slope_inserted = self._compute_yield_curve_slope(start_date, end_date)
        total_inserted += slope_inserted
        self.stdout.write(f'  yield_curve_slope: {slope_inserted} rows inserted')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(self.style.SUCCESS('Economic Indicators Summary'))
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(f'  Total rows inserted: {total_inserted}')
        self.stdout.write(self.style.SUCCESS('=' * 50))

    def _fetch_indicator(
        self,
        indicator_name: str,
        ticker: str,
        start_date: date,
        end_date: date,
    ) -> int:
        """Fetch a single indicator and insert rows."""
        df = fetch_symbol(ticker, start_date, end_date)
        if df.empty:
            logger.warning("No data for %s (%s)", indicator_name, ticker)
            return 0

        inserted = 0
        for dt, row in df.iterrows():
            value = float(row['Close']) if 'Close' in df.columns else 0.0

            # Skip if already exists
            if EconomicIndicator.objects.filter(
                date=dt.date(), indicator_name=indicator_name
            ).exists():
                continue

            EconomicIndicator.objects.create(
                date=dt.date(),
                indicator_name=indicator_name,
                value=Decimal(str(round(value, 4))),
                data_source='yfinance',
            )
            inserted += 1

        return inserted

    def _compute_yield_curve_slope(
        self, start_date: date, end_date: date
    ) -> int:
        """Compute yield_curve_slope from yield_10y and yield_2y."""
        # Get all dates where both indicators exist
        dates_10y = set(
            EconomicIndicator.objects.filter(
                indicator_name='yield_10y',
                date__gte=start_date,
                date__lte=end_date,
            ).values_list('date', flat=True)
        )
        dates_2y = set(
            EconomicIndicator.objects.filter(
                indicator_name='yield_2y',
                date__gte=start_date,
                date__lte=end_date,
            ).values_list('date', flat=True)
        )
        common_dates = sorted(dates_10y & dates_2y)

        inserted = 0
        for d in common_dates:
            # Skip if already exists
            if EconomicIndicator.objects.filter(
                date=d, indicator_name='yield_curve_slope'
            ).exists():
                continue

            val_10y = EconomicIndicator.objects.get(
                date=d, indicator_name='yield_10y'
            ).value
            val_2y = EconomicIndicator.objects.get(
                date=d, indicator_name='yield_2y'
            ).value
            slope = val_10y - val_2y

            EconomicIndicator.objects.create(
                date=d,
                indicator_name='yield_curve_slope',
                value=Decimal(str(round(slope, 4))),
                data_source='computed',
            )
            inserted += 1

        return inserted
