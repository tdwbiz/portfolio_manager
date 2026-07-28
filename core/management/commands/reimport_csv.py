"""Re-import all CSV files from the inbox, clearing old data first.

Usage:
    python manage.py reimport_csv [--dry-run]
    python manage.py reimport_csv [--holdings]
    python manage.py reimport_csv [--holdings --dry-run]

Options:
    --holdings   Import Holdings CSVs into PositionHistory (default: Activity CSVs)
    --dry-run    Parse files without writing to the database
"""

import glob
import logging
import os
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import BrokerStatement, Transaction, PositionHistory
from imports.auto_import import auto_import
from imports.parsers_holdings import import_holdings_snapshot

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Re-import CSV files from inbox directories"

    def add_arguments(self, parser):
        parser.add_argument(
            '--holdings',
            action='store_true',
            help='Import Holdings CSVs instead of Activity CSVs',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse files without writing to the database',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        holdings = options['holdings']

        inbox = getattr(settings, 'IMPORT_INBOX', 'inbox')

        if holdings:
            return self._import_holdings(inbox, dry_run)
        else:
            return self._import_activity(inbox, dry_run)

    def _import_activity(self, inbox: str, dry_run: bool) -> None:
        """Import Activity CSVs, clearing old CSV data first."""
        # Find all CSV files
        csv_files = []
        for pattern in ['*.csv', '**/*.csv']:
            csv_files.extend(glob.glob(os.path.join(inbox, pattern), recursive=True))
        csv_files = sorted(set(csv_files))

        if not csv_files:
            self.stdout.write(f"No CSV files found in {inbox}")
            return

        self.stdout.write(f"Found {len(csv_files)} CSV files")

        if not dry_run:
            # Clear existing CSV-imported data
            self.stdout.write("Clearing existing CSV-imported data...")
            tx_count = Transaction.objects.count()
            stmt_count = BrokerStatement.objects.count()
            Transaction.objects.all().delete()
            BrokerStatement.objects.all().delete()
            self.stdout.write(
                f"Cleared {tx_count} transactions and {stmt_count} statements"
            )

        imported_total = 0
        skipped_total = 0
        errors = 0

        for file_path in csv_files:
            filename = os.path.basename(file_path)
            self.stdout.write(f"Importing: {filename}")
            try:
                auto_import(file_path, 'RBC', dry_run=dry_run)
                imported_total += 1
            except Exception as e:
                self.stderr.write(f"  ERROR importing {filename}: {e}")
                errors += 1

        # Report final counts
        if not dry_run:
            total = Transaction.objects.count()
            unique_hashes = Transaction.objects.values_list(
                'tx_hash', flat=True
            ).distinct().count()
            self.stdout.write(f"Total transactions: {total}")
            self.stdout.write(f"Unique hashes: {unique_hashes}")
            if total != unique_hashes:
                self.stderr.write(
                    f"WARNING: {total - unique_hashes} duplicate transactions detected"
                )

        self.stdout.write(
            f"Imported {imported_total}/{len(csv_files)} files "
            f"({errors} errors)"
        )

    def _import_holdings(self, inbox: str, dry_run: bool) -> None:
        """Import Holdings CSVs into PositionHistory."""
        # Find Holdings CSVs
        csv_files = []
        for pattern in ['Holdings*.csv', '**/Holdings*.csv']:
            csv_files.extend(glob.glob(os.path.join(inbox, pattern), recursive=True))
        csv_files = sorted(set(csv_files))

        if not csv_files:
            self.stdout.write(f"No Holdings CSV files found in {inbox}")
            return

        self.stdout.write(f"Found {len(csv_files)} Holdings CSV files")

        if not dry_run:
            self.stdout.write("Clearing existing PositionHistory data...")
            count = PositionHistory.objects.count()
            PositionHistory.objects.all().delete()
            self.stdout.write(f"Cleared {count} position history records")

        created_total = 0
        closed_total = 0
        errors = 0

        for file_path in csv_files:
            filename = os.path.basename(file_path)
            self.stdout.write(f"Importing holdings: {filename}")
            try:
                created, closed = import_holdings_snapshot(
                    file_path, dry_run=dry_run
                )
                created_total += created
                closed_total += closed
            except Exception as e:
                self.stderr.write(
                    f"  ERROR importing holdings {filename}: {e}"
                )
                errors += 1

        self.stdout.write(
            f"Total: {created_total} created, {closed_total} closed "
            f"({errors} errors)"
        )
