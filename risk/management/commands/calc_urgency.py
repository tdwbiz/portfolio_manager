"""
Management command to calculate urgency scores.

Usage:
    python manage.py calc_urgency [--date YYYY-MM-DD] [--entity-type security_in_account|account|owner|portfolio] [--account ACC] [--symbol SYM]
"""

import argparse
from datetime import date

from django.core.management.base import BaseCommand
from django.utils import timezone

from risk.services.urgency_engine import calculate_urgency_for_date


class Command(BaseCommand):
    help = 'Calculate urgency scores for all risk entities.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Calculation date in YYYY-MM-DD format. Defaults to today.',
        )
        parser.add_argument(
            '--entity-type',
            type=str,
            choices=['security_in_account', 'account', 'owner', 'portfolio'],
            help='Filter by entity type (for future use).',
        )
        parser.add_argument(
            '--account',
            type=str,
            help='Filter by account ID.',
        )
        parser.add_argument(
            '--symbol',
            type=str,
            help='Filter by symbol.',
        )

    def handle(self, *args, **options):
        calc_date_str = options.get('date')
        if calc_date_str:
            try:
                calc_date = date.fromisoformat(calc_date_str)
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Invalid date format: {calc_date_str}. Use YYYY-MM-DD."))
                return
        else:
            calc_date = timezone.now().date()

        account_filter = options.get('account')
        symbol_filter = options.get('symbol')

        self.stdout.write(f"Calculating urgency scores for date: {calc_date}")

        try:
            result = calculate_urgency_for_date(
                calc_date=calc_date,
                entity_type_filter=options.get('entity_type'),
                account_id_filter=account_filter,
                symbol_filter=symbol_filter,
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error calculating urgency scores: {e}"))
            raise

        self.stdout.write(self.style.SUCCESS(f"Urgency score calculation complete for {calc_date}"))
        self.stdout.write(f"  Security-in-account rows: {result['security_in_account_count']}")
        self.stdout.write(f"  Account rows: {result['account_count']}")
        self.stdout.write(f"  Owner rows: {result['owner_count']}")
        self.stdout.write(f"  Portfolio rows: {result['portfolio_count']}")
        self.stdout.write(f"  Skipped symbols: {', '.join(result['skipped_symbols']) if result['skipped_symbols'] else 'None'}")
        self.stdout.write(f"  Distribution: {result['distribution']}")
