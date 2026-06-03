"""
Management command: import_amibroker_prices

Import OHLCV data from Amibroker/AmiQuote CSV exports.

Usage:
    python manage.py import_amibroker_prices [--watch-folder PATH] [--symbol SYM] [--dry-run]
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand

from risk.models import DataQualityLog, MarketPriceHistory, SecurityUniverse
from risk.services.volatility import compute_volatility_summaries

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Import OHLCV data from Amibroker/AmiQuote CSV exports.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--watch-folder',
            type=str,
            default=None,
            help='Path to Amibroker export watch folder.',
        )
        parser.add_argument(
            '--symbol',
            type=str,
            default=None,
            help='Process only this symbol\'s CSV file.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse files but do not write to database.',
        )

    def handle(self, *args, **options):
        watch_folder = options['watch_folder'] or getattr(
            settings, 'AMIBROKER_WATCH_FOLDER', None
        )
        if not watch_folder:
            self.stdout.write(
                self.style.ERROR(
                    'No watch folder configured. '
                    'Set AMIBROKER_WATCH_FOLDER in settings or use --watch-folder.'
                )
            )
            return

        if not os.path.isdir(watch_folder):
            self.stdout.write(
                self.style.ERROR(f'Watch folder does not exist: {watch_folder}')
            )
            return

        symbol_filter = options['symbol']
        dry_run = options['dry_run']

        # Collect CSV files
        csv_files = []
        for fname in sorted(os.listdir(watch_folder)):
            if not fname.lower().endswith('.csv'):
                continue
            if symbol_filter:
                # Match symbol: VDY.csv or VDY.TO.csv
                base = fname[:-4].upper().replace('.TO', '')
                if base == symbol_filter.upper():
                    csv_files.append(os.path.join(watch_folder, fname))
            else:
                csv_files.append(os.path.join(watch_folder, fname))

        if not csv_files:
            self.stdout.write(self.style.WARNING('No CSV files found to process.'))
            return

        total_inserted = 0
        total_skipped = 0
        total_files = 0
        symbols_not_in_universe = []

        for csv_path in csv_files:
            fname = os.path.basename(csv_path)
            # Derive symbol from filename
            base = fname[:-4]  # strip .csv
            symbol = base.upper().replace('.TO', '')
            if base.upper().endswith('.TO'):
                self.stdout.write(
                    self.style.WARNING(
                        f'Stripped .TO suffix from {fname}: stored as {symbol}'
                    )
                )

            # Check if symbol exists in universe
            try:
                sec = SecurityUniverse.objects.get(symbol=symbol)
            except SecurityUniverse.DoesNotExist:
                logger.warning(
                    "Symbol %s not found in rm_security_universe — skipping %s",
                    symbol, fname,
                )
                DataQualityLog.objects.create(
                    symbol=symbol,
                    event_type='symbol_not_in_universe',
                    severity='warning',
                    description=f'Symbol {symbol} not in universe; skipping {fname}',
                )
                symbols_not_in_universe.append(symbol)
                continue

            # Parse CSV
            inserted, skipped = self._process_csv(
                csv_path, symbol, sec.primary_currency, dry_run
            )
            total_inserted += inserted
            total_skipped += skipped
            total_files += 1

            # Compute volatility for this symbol
            if not dry_run and inserted > 0:
                compute_volatility_summaries(symbol=symbol)

            self.stdout.write(
                f'  {fname}: {inserted} inserted, {skipped} skipped'
            )

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(self.style.SUCCESS('Import Summary'))
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(f'  Files processed:  {total_files}')
        self.stdout.write(f'  Rows inserted:    {total_inserted}')
        self.stdout.write(f'  Rows skipped:     {total_skipped}')
        if symbols_not_in_universe:
            self.stdout.write(
                self.style.WARNING(
                    f'  Symbols not in universe: {", ".join(symbols_not_in_universe)}'
                )
            )
        if dry_run:
            self.stdout.write(self.style.WARNING('  (DRY RUN — no data written)'))
        self.stdout.write(self.style.SUCCESS('=' * 50))

    def _process_csv(
        self,
        csv_path: str,
        symbol: str,
        currency: str,
        dry_run: bool,
    ) -> tuple[int, int]:
        """Parse a single CSV and insert rows."""
        inserted = 0
        skipped = 0

        with open(csv_path, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)

            # Detect format by column count
            has_adj_close = len(header) >= 7
            # Format A: Date,Open,High,Low,Close,Volume (6 cols)
            # Format B: Date,Open,High,Low,Close,Adj Close,Volume (7 cols)

            # Map column indices
            col_map = {col.strip().lower().replace(' ', '_'): i for i, col in enumerate(header)}
            date_idx = col_map.get('date')
            open_idx = col_map.get('open')
            high_idx = col_map.get('high')
            low_idx = col_map.get('low')
            close_idx = col_map.get('close')
            adj_close_idx = col_map.get('adj_close')
            volume_idx = col_map.get('volume')

            if date_idx is None or close_idx is None:
                logger.error(
                    "Cannot parse header %s for %s — missing date or close column",
                    header, csv_path,
                )
                return 0, 0

            for row in reader:
                if len(row) <= max(date_idx, close_idx):
                    continue

                try:
                    date_str = row[date_idx].strip()
                    # Parse YYYYMMDD
                    row_date = date(
                        int(date_str[0:4]),
                        int(date_str[4:6]),
                        int(date_str[6:8]),
                    )
                except (ValueError, IndexError):
                    continue

                # Skip if already exists with data_source='amibroker'
                if MarketPriceHistory.objects.filter(
                    symbol=symbol, date=row_date, data_source='amibroker'
                ).exists():
                    skipped += 1
                    continue

                open_val = float(row[open_idx]) if open_idx is not None and open_idx < len(row) else 0.0
                high_val = float(row[high_idx]) if high_idx is not None and high_idx < len(row) else 0.0
                low_val = float(row[low_idx]) if low_idx is not None and low_idx < len(row) else 0.0
                close_val = float(row[close_idx])
                volume_val = int(float(row[volume_idx])) if volume_idx is not None and volume_idx < len(row) else 0

                if has_adj_close and adj_close_idx is not None and adj_close_idx < len(row):
                    adj_close_val = float(row[adj_close_idx])
                else:
                    adj_close_val = close_val  # Format A: no adj close data

                if not dry_run:
                    MarketPriceHistory.objects.create(
                        symbol=symbol,
                        date=row_date,
                        open=open_val,
                        high=high_val,
                        low=low_val,
                        close=close_val,
                        adj_close=adj_close_val,
                        volume=volume_val,
                        currency=currency,
                        data_source='amibroker',
                    )
                inserted += 1

        return inserted, skipped
