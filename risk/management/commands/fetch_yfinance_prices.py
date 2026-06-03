"""
Management command: fetch_yfinance_prices

Fetch OHLCV data from yFinance for all active symbols.

Usage:
    python manage.py fetch_yfinance_prices [--symbol SYM] [--start YYYY-MM-DD] [--force-full]
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand

from risk.models import MarketPriceHistory, SecurityUniverse
from risk.services.volatility import compute_volatility_summaries
from risk.services.yfinance_client import fetch_symbol, resolve_yfinance_ticker

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_YEARS = getattr(settings, 'YFINANCE_DEFAULT_HISTORY_YEARS', 10)


class Command(BaseCommand):
    help = 'Fetch OHLCV data from yFinance for all active symbols.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--symbol',
            type=str,
            default=None,
            help='Fetch only this symbol.',
        )
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
        symbol_filter = options['symbol']
        force_full = options['force_full']
        start_override = options['start']

        # Build fetch list
        if symbol_filter:
            symbols = [symbol_filter]
        else:
            symbols = list(
                SecurityUniverse.objects.filter(is_active=True).values_list(
                    'symbol', flat=True
                )
            )

        total_inserted = 0
        total_errors = 0
        fetched_symbols = []

        for symbol in symbols:
            # Resolve yFinance ticker
            ticker = resolve_yfinance_ticker(symbol)
            if ticker is None:
                # Mutual fund or not in universe — already logged
                continue

            # Determine fetch range
            if start_override:
                start_date = date.fromisoformat(start_override)
                end_date = date.today() + timedelta(days=1)
            elif force_full:
                start_date = date.today() - timedelta(days=DEFAULT_HISTORY_YEARS * 365)
                end_date = date.today() + timedelta(days=1)
            else:
                # Incremental: detect last fetched date
                last_row = MarketPriceHistory.objects.filter(
                    symbol=symbol
                ).order_by('-date').first()
                if last_row:
                    start_date = last_row.date + timedelta(days=1)
                    end_date = date.today() + timedelta(days=1)
                else:
                    # No data exists — full history
                    start_date = date.today() - timedelta(days=DEFAULT_HISTORY_YEARS * 365)
                    end_date = date.today() + timedelta(days=1)

            if start_date > end_date:
                logger.info("No new data to fetch for %s", symbol)
                continue

            # Fetch from yFinance
            df = fetch_symbol(ticker, start_date, end_date)
            if df.empty:
                logger.info("No data returned for %s (%s)", symbol, ticker)
                continue

            # Insert rows
            inserted = self._insert_rows(symbol, df)
            total_inserted += inserted
            fetched_symbols.append((symbol, start_date, end_date, inserted))

            if inserted == 0:
                logger.info("No new rows to insert for %s", symbol)

        # Compute volatility for all symbols that had new data
        if total_inserted > 0:
            symbols_updated = {s[0] for s in fetched_symbols if s[3] > 0}
            for sym in symbols_updated:
                try:
                    compute_volatility_summaries(symbol=sym)
                except Exception as exc:
                    logger.warning("Volatility computation failed for %s: %s", sym, exc)

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(self.style.SUCCESS('yFinance Fetch Summary'))
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(f'  Total rows inserted: {total_inserted}')
        for sym, s, e, cnt in fetched_symbols:
            self.stdout.write(f'  {sym}: {s} → {e} ({cnt} rows)')
        if total_errors > 0:
            self.stdout.write(
                self.style.ERROR(f'  Symbols with errors: {total_errors}')
            )
        self.stdout.write(self.style.SUCCESS('=' * 50))

    def _insert_rows(self, symbol: str, df) -> int:
        """Insert price rows from a yFinance DataFrame."""
        inserted = 0

        # Determine currency for this symbol
        try:
            sec = SecurityUniverse.objects.get(symbol=symbol)
            currency = sec.primary_currency
        except SecurityUniverse.DoesNotExist:
            currency = 'USD'

        for dt, row in df.iterrows():
            # Skip if amibroker already has this (symbol, date)
            amibroker_exists = MarketPriceHistory.objects.filter(
                symbol=symbol,
                date=dt.date(),
                data_source='amibroker',
            ).exists()
            if amibroker_exists:
                continue

            # Skip if yfinance already has this (symbol, date)
            yf_exists = MarketPriceHistory.objects.filter(
                symbol=symbol,
                date=dt.date(),
                data_source='yfinance',
            ).exists()
            if yf_exists:
                continue

            close_val = float(row['Close']) if 'Close' in df.columns else 0.0
            adj_close_val = close_val  # auto_adjust=True means Close = adj close
            high_val = float(row.get('High', close_val))
            low_val = float(row.get('Low', close_val))
            open_val = float(row.get('Open', close_val))
            volume_val = int(row.get('Volume', 0))

            MarketPriceHistory.objects.create(
                symbol=symbol,
                date=dt.date(),
                open=open_val,
                high=high_val,
                low=low_val,
                close=close_val,
                adj_close=adj_close_val,
                volume=volume_val,
                currency=currency,
                data_source='yfinance',
            )
            inserted += 1

        return inserted
