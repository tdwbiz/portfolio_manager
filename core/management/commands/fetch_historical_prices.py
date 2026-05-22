"""
Management command: fetch_historical_prices

Fetches daily closing prices from yfinance for all portfolio symbols
and the USDCAD=X FX rate, then stores them in the HistoricalPrice table.

Usage:
    python manage.py fetch_historical_prices
    python manage.py fetch_historical_prices --start 2012-01-01
    python manage.py fetch_historical_prices --extra-symbols SPY,^GSPC,QQQ
    python manage.py fetch_historical_prices --incremental

Modes:
    Default  : fetch from --start (2012-01-01) for all symbols, overwriting existing data.
    Incremental (--incremental): for each symbol fetch only from its last stored date + 1 day.
"""

import logging
from datetime import date, timedelta, datetime
from decimal import Decimal

import yfinance as yf
import pandas as pd

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction

from core.models import Transaction, HistoricalPrice

logger = logging.getLogger(__name__)

DEFAULT_START = '2012-01-01'
ALWAYS_FETCH = ['USDCAD=X']


class Command(BaseCommand):
    help = 'Fetch historical daily closing prices from yfinance and store in HistoricalPrice table.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--start',
            default=DEFAULT_START,
            help=f'Fetch data from this date (default: {DEFAULT_START}). Ignored with --incremental.',
        )
        parser.add_argument(
            '--extra-symbols',
            default='SPY,^GSPC',
            help='Comma-separated extra symbols to fetch (e.g. benchmark tickers). Default: SPY,^GSPC',
        )
        parser.add_argument(
            '--incremental',
            action='store_true',
            default=False,
            help='Only fetch dates after the last stored date for each symbol.',
        )

    def handle(self, *args, **options):
        start_str = options['start']
        incremental = options['incremental']
        extra_symbols_raw = options['extra_symbols']

        try:
            global_start = datetime.strptime(start_str, '%Y-%m-%d').date()
        except ValueError:
            raise CommandError(f'Invalid --start date: {start_str!r}. Use YYYY-MM-DD.')

        # Collect all unique symbols from Transaction table
        portfolio_symbols = list(
            Transaction.objects.values_list('symbol', flat=True).distinct()
        )

        # Add FX rate + any extra symbols (benchmarks etc.)
        extra_symbols = [s.strip() for s in extra_symbols_raw.split(',') if s.strip()]
        raw_symbols = list(set(portfolio_symbols + ALWAYS_FETCH + extra_symbols))

        # Filter out symbols that yfinance cannot handle:
        #  - empty / whitespace-only strings
        #  - OCC option contract codes (contain spaces, e.g. "NVDA  241011C00132000")
        #  - literal '-' or other single-char junk
        #  - 'USD.CAD' style FX codes (use 'USDCAD=X' instead)
        all_symbols = [
            s for s in raw_symbols
            if s and s.strip() and ' ' not in s and s != '-' and '.' not in s
        ] + [s for s in raw_symbols if '=' in s]  # keep yfinance FX codes like USDCAD=X
        all_symbols = list(set(all_symbols))  # deduplicate

        self.stdout.write(
            self.style.NOTICE(
                f'Symbols to fetch ({len(all_symbols)}): {", ".join(sorted(all_symbols))}'
            )
        )

        today = date.today()

        if incremental:
            self._run_incremental(all_symbols, global_start, today)
        else:
            self._run_full(all_symbols, global_start, today)

        self.stdout.write(self.style.SUCCESS('Done.'))

    # ------------------------------------------------------------------ #

    def _run_full(self, symbols: list[str], start: date, end: date):
        """Download full history for all symbols in a single yfinance call."""
        self.stdout.write(f'Full fetch: {start} to {end}')
        df = self._download_batch(symbols, start, end)
        if df is None or df.empty:
            self.stdout.write(self.style.WARNING('No data returned from yfinance.'))
            return
        # Clear existing records for fetched symbols before re-inserting
        fetched_symbols = list(df.columns)
        deleted, _ = HistoricalPrice.objects.filter(symbol__in=fetched_symbols).delete()
        if deleted:
            self.stdout.write(f'  Cleared {deleted} existing records for {len(fetched_symbols)} symbols.')
        self._store_dataframe(df)

    def _run_incremental(self, symbols: list[str], global_start: date, end: date):
        """For each symbol, fetch only data newer than what is already stored."""
        # Find the last stored date per symbol
        from django.db.models import Max
        last_dates = dict(
            HistoricalPrice.objects.filter(symbol__in=symbols)
            .values('symbol')
            .annotate(last=Max('date'))
            .values_list('symbol', 'last')
        )

        # Group symbols by their effective start date (to batch-download efficiently)
        from collections import defaultdict
        groups: dict[date, list[str]] = defaultdict(list)
        for sym in symbols:
            last = last_dates.get(sym)
            if last is not None:
                fetch_from = last + timedelta(days=1)
            else:
                fetch_from = global_start
            if fetch_from <= end:
                groups[fetch_from].append(sym)

        if not groups:
            self.stdout.write('All symbols are already up to date.')
            return

        for fetch_from, syms in sorted(groups.items()):
            self.stdout.write(f'Incremental fetch: {fetch_from} to {end} for {len(syms)} symbols')
            df = self._download_batch(syms, fetch_from, end)
            if df is not None and not df.empty:
                self._store_dataframe(df)

    # ------------------------------------------------------------------ #

    def _download_batch(self, symbols: list[str], start: date, end: date):
        """Download closing prices for a list of symbols. Returns a wide DataFrame
        with DatetimeIndex and symbols as columns, or None on failure."""
        try:
            self.stdout.write(f'  Downloading {len(symbols)} symbols via yfinance…')
            raw = yf.download(
                symbols,
                start=start.strftime('%Y-%m-%d'),
                end=(end + timedelta(days=1)).strftime('%Y-%m-%d'),
                auto_adjust=True,
                progress=False,
            )
            if raw is None or raw.empty:
                self.stdout.write(self.style.WARNING('  yfinance returned empty DataFrame.'))
                return None

            # Normalise MultiIndex → plain DataFrame with tickers as columns
            if isinstance(raw.columns, pd.MultiIndex):
                lvl0 = raw.columns.get_level_values(0)
                if 'Close' in lvl0:
                    df = raw['Close']
                else:
                    df = raw.xs('Close', axis=1, level=1)
            elif len(symbols) == 1:
                df = raw[['Close']].rename(columns={'Close': symbols[0]})
            else:
                df = raw.get('Close', raw)

            # Drop rows that are all-NaN
            df = df.dropna(how='all')
            self.stdout.write(f'  Got {len(df)} rows × {len(df.columns)} symbols.')
            return df
        except Exception as exc:
            logger.error(f'yfinance download failed: {exc}')
            self.stdout.write(self.style.ERROR(f'  Download failed: {exc}'))
            return None

    def _store_dataframe(self, df: pd.DataFrame):
        """Bulk-upsert a wide DataFrame (DatetimeIndex, tickers as columns) into HistoricalPrice."""
        objs = []
        for dt_idx, row in df.iterrows():
            price_date = dt_idx.date() if hasattr(dt_idx, 'date') else dt_idx
            for symbol in df.columns:
                price = row[symbol]
                if pd.isna(price) or price <= 0:
                    continue
                objs.append(HistoricalPrice(
                    symbol=str(symbol),
                    date=price_date,
                    close=Decimal(str(round(float(price), 6))),
                ))

        if not objs:
            self.stdout.write(self.style.WARNING('  No valid price rows to store.'))
            return

        self.stdout.write(f'  Storing {len(objs)} price records...')
        batch_size = 2000
        stored = 0
        with db_transaction.atomic():
            for i in range(0, len(objs), batch_size):
                chunk = objs[i:i + batch_size]
                HistoricalPrice.objects.bulk_create(chunk)
                stored += len(chunk)
        self.stdout.write(self.style.SUCCESS(f'  Stored {stored} records OK.'))
