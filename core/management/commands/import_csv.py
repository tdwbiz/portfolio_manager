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
import shutil
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import Transaction
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
        """Import Activity CSVs incrementally.

        Only new files are processed. Duplicate transactions (same tx_hash)
        are skipped automatically. Previously imported files in processed/
        are ignored - only CSVs remaining in inbox/ are parsed.
        """
        # Find all Activity CSV files (exclude Holdings and Dividends files)
        csv_files = []
        for pattern in ['*.csv', '**/*.csv']:
            csv_files.extend(glob.glob(os.path.join(inbox, pattern), recursive=True))
        csv_files = sorted(set(csv_files))
        # Filter out Holdings and Dividends files
        csv_files = [f for f in csv_files if not (
            os.path.basename(f).lower().startswith('holdings') or
            'dividend' in os.path.basename(f).lower()
        )]

        if not csv_files:
            self.stdout.write(f"No CSV files found in {inbox}")
            return

        self.stdout.write(f"Found {len(csv_files)} CSV files")

        # Setup processed directories (same as PDF import)
        processed_dir = Path(
            getattr(settings, 'IMPORT_PROCESSED_DIR', settings.BASE_DIR / 'processed')
        )
        errors_dir = processed_dir / 'errors'
        for d in (processed_dir, errors_dir):
            d.mkdir(parents=True, exist_ok=True)

        imported_total = 0
        errors = 0

        for file_path in csv_files:
            filename = os.path.basename(file_path)
            self.stdout.write(f"Importing: {filename}")
            try:
                imported_count, skipped_count, parsed_count = auto_import(file_path, 'RBC', dry_run=dry_run)
                # Only flag as error when parser found zero trades
                if parsed_count == 0 and not dry_run:
                    self.stderr.write(f"  WARNING: No trades parsed from {filename}")
                    errors += 1
                    dest = errors_dir / filename
                    _move_file(Path(file_path), dest)
                    self.stdout.write(self.style.WARNING(f"  Moved to errors/: {filename}"))
                    continue
                # Track success (even if all trades were duplicates — still a valid import)
                imported_total += 1
                if parsed_count > 0 and imported_count == 0:
                    if skipped_count > 0:
                        self.stdout.write(self.style.SUCCESS(
                            f"  All {parsed_count} trades already imported ({skipped_count} duplicates, no new transactions)"
                        ))
                    else:
                        self.stdout.write(self.style.SUCCESS(
                            f"  All {parsed_count} trades already imported (no new transactions)"
                        ))
                # Move to processed/ on success
                if not dry_run:
                    dest = processed_dir / filename
                    _move_file(Path(file_path), dest)
                    self.stdout.write(self.style.SUCCESS(f"  Moved to processed/: {filename}"))
            except Exception as e:
                self.stderr.write(f"  ERROR importing {filename}: {e}")
                errors += 1
                # Move to processed/errors/ on failure
                if not dry_run:
                    dest = errors_dir / filename
                    _move_file(Path(file_path), dest)
                    self.stdout.write(self.style.WARNING(f"  Moved to errors/: {filename}"))

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

        # Setup processed directories (same as PDF import)
        processed_dir = Path(
            getattr(settings, 'IMPORT_PROCESSED_DIR', settings.BASE_DIR / 'processed')
        )
        errors_dir = processed_dir / 'errors'
        for d in (processed_dir, errors_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Don't clear PositionHistory - snapshots are cumulative.
        # Each new import closes old open positions and creates new ones.

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
                if created_total + closed_total > 0:
                    # Move to processed/ on success (positions were created or closed)
                    if not dry_run:
                        dest = processed_dir / filename
                        _move_file(Path(file_path), dest)
                        self.stdout.write(self.style.SUCCESS(f"  Moved to processed/: {filename}"))
                else:
                    # 0 positions created/closed - file has no parseable data
                    if not dry_run:
                        dest = errors_dir / filename
                        _move_file(Path(file_path), dest)
                        self.stdout.write(self.style.WARNING(f"  Moved to errors/: {filename}"))
            except Exception as e:
                self.stderr.write(
                    f"  ERROR importing holdings {filename}: {e}"
                )
                errors += 1
                # Move to processed/errors/ on failure
                if not dry_run:
                    dest = errors_dir / filename
                    _move_file(Path(file_path), dest)
                    self.stdout.write(self.style.WARNING(f"  Moved to errors/: {filename}"))

        self.stdout.write(
            f"Total: {created_total} created, {closed_total} closed "
            f"({errors} errors)"
        )


def _move_file(src: Path, dest: Path) -> bool:
    """Move src to dest, retrying up to 5 times on PermissionError.

    Returns True if the file was moved, False otherwise.
    If the source file is already gone (moved by a concurrent process),
    logs a warning and returns False without raising.
    """
    if not src.exists():
        logger.warning("_move_file: %s not found", src.name)
        return False

    for attempt in range(5):
        try:
            shutil.move(str(src), str(dest))
            logger.info("Moved %s -> %s", src.name, dest)
            return True
        except PermissionError:
            if attempt == 4:
                logger.error("Failed to move %s after 5 attempts", src.name)
                return False
            delay = 2 * (attempt + 1)
            logger.warning("File locked, retrying move in %ds...", delay)
            time.sleep(delay)
        except FileNotFoundError:
            logger.warning("_move_file: %s disappeared during move", src.name)
            return False

    return False
