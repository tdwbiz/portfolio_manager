"""
Management command: compute_volatility

Compute volatility summaries for all symbols or a specific symbol.

Usage:
    python manage.py compute_volatility [--symbol SYM] [--date YYYY-MM-DD]
"""

from __future__ import annotations

import logging
from datetime import date

from django.core.management.base import BaseCommand

from risk.services.volatility import compute_volatility_summaries

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Compute volatility summaries for price data.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--symbol',
            type=str,
            default=None,
            help='Compute volatility for this symbol only.',
        )
        parser.add_argument(
            '--date',
            type=str,
            default=None,
            help='Calculation date in YYYY-MM-DD format (default: today).',
        )

    def handle(self, *args, **options):
        symbol = options['symbol']
        calc_date = date.fromisoformat(options['date']) if options['date'] else None

        results = compute_volatility_summaries(symbol=symbol, calc_date=calc_date)

        total_windows = sum(results.values())
        total_symbols = len(results)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(self.style.SUCCESS('Volatility Computation Summary'))
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(f'  Symbols processed: {total_symbols}')
        self.stdout.write(f'  Total windows computed: {total_windows}')
        for sym, windows in sorted(results.items()):
            self.stdout.write(f'  {sym}: {windows}/4 windows')
        self.stdout.write(self.style.SUCCESS('=' * 50))
