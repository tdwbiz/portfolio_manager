"""Import CSV files from the inbox directory.

Usage:
    python manage.py import_csv [--dry-run]
    python manage.py import_csv [--holdings]
    python manage.py import_csv [--holdings --dry-run]

Options:
    --holdings   Import Holdings CSVs into PositionHistory (default: Activity CSVs)
    --dry-run    Parse files without writing to the database
"""

import glob
import logging
import os
import sys
import shutil
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import BrokerStatement, Transaction, PositionHistory
from imports.auto_import import auto_import
from imports.parsers_holdings import import_holdings_snapshot

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Import CSV files from inbox directory"

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

        inbox = getattr(settings, 'IMPORT_INBOX_DIR', 'inbox')

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
        
        # Setup processed directory
        processed_dir = os.path.join(inbox, 'processed')
        os.makedirs(processed_dir, exist_ok=True)

        for file_path in csv_files:
            filename = os.path.basename(file_path)
            self.stdout.write(f"Importing: {filename}")
            try:
                auto_import(file_path, 'RBC', dry_run=dry_run)
                imported_total += 1
                
                # Move file to processed directory with timestamp
                if not dry_run:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    new_filename = f"{timestamp}_{filename}"
                    dest_path = os.path.join(processed_dir, new_filename)
                    shutil.move(file_path, dest_path)
                    self.stdout.write(f"  Moved to: {new_filename}")
            except Exception as e:
                self.stderr.write(f"  ERROR importing {filename}: {e}")
                errors += 1
                import traceback
                self.stderr.write(traceback.format_exc())

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
        
        # Setup processed directory
        processed_dir = os.path.join(inbox, 'processed')
        os.makedirs(processed_dir, exist_ok=True)

        for file_path in csv_files:
            filename = os.path.basename(file_path)
            self.stdout.write(f"Importing holdings: {filename}")
            try:
                created, closed = import_holdings_snapshot(
                    file_path, dry_run=dry_run
                )
                created_total += created
                closed_total += closed
                
                # Move file to processed directory with timestamp
                if not dry_run:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    new_filename = f"{timestamp}_{filename}"
                    dest_path = os.path.join(processed_dir, new_filename)
                    shutil.move(file_path, dest_path)
                    self.stdout.write(f"  Moved to: {new_filename}")
            except Exception as e:
                self.stderr.write(
                    f"  ERROR importing holdings {filename}: {e}"
                )
                errors += 1

        self.stdout.write(
            f"Total: {created_total} created, {closed_total} closed "
            f"({errors} errors)"
        )
