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
import json
import logging
import os
import shutil
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import FileImportLog, Transaction
from core.utils.import_checks import (
    content_hash_of,
    is_content_imported,
    statement_date_problems,
)
from core.utils.safe_names import is_safe_file_name
from imports.auto_import import auto_import
from imports.parsers_holdings import (
    holdings_import_report,
    import_holdings_snapshot,
)

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
                report = auto_import(file_path, 'RBC', dry_run=dry_run)

                # Row accounting per file (plan step 3):
                # data rows = imported + duplicates + practice + rejected.
                rejected_total = sum(report.rejected.values())
                if not report.content_duplicate:
                    self.stdout.write(
                        f"  Rows: {report.data_rows} data = "
                        f"{report.imported} imported + {report.duplicates} duplicates"
                        f" + {report.practice} practice + {rejected_total} rejected"
                    )

                # A row-count mismatch fails the whole file atomically —
                # nothing stored, FileImportLog status error (decision 4).
                if report.failed:
                    self.stderr.write(self.style.ERROR(
                        f"  ROW COUNT MISMATCH — file failed atomically "
                        f"(nothing stored): {report.fail_reason}"
                    ))
                    errors += 1
                    if not dry_run:
                        dest = errors_dir / filename
                        _move_file(Path(file_path), dest)
                        self.stdout.write(self.style.WARNING(f"  Moved to errors/: {filename}"))
                    continue

                # Identical content already imported — nothing to do.
                if report.content_duplicate:
                    if not dry_run:
                        dest = processed_dir / filename
                        _move_file(Path(file_path), dest)
                        self.stdout.write(self.style.SUCCESS(f"  Moved to processed/: {filename}"))
                    continue

                # Only flag as error when the parser found no data rows
                if report.data_rows == 0 and not dry_run:
                    self.stderr.write(f"  WARNING: No data rows parsed from {filename}")
                    errors += 1
                    dest = errors_dir / filename
                    _move_file(Path(file_path), dest)
                    self.stdout.write(self.style.WARNING(f"  Moved to errors/: {filename}"))
                    continue

                if report.dry_run:
                    self.stdout.write(self.style.SUCCESS(
                        f"  [DRY-RUN] would import {report.imported}, "
                        f"{report.duplicates} duplicates, "
                        f"{rejected_total} rejected"
                    ))
                    continue

                # Nothing imported and nothing matched (e.g. every row was
                # rejected) — the file is an error.
                if report.imported == 0 and report.duplicates == 0:
                    errors += 1
                    dest = errors_dir / filename
                    _move_file(Path(file_path), dest)
                    self.stdout.write(self.style.WARNING(f"  Moved to errors/: {filename}"))
                    continue

                # Track success (even if all trades were duplicates — still a valid import)
                imported_total += 1
                if report.imported == 0 and report.duplicates > 0:
                    self.stdout.write(self.style.SUCCESS(
                        f"  All {report.data_rows} rows already imported "
                        f"({report.duplicates} duplicates, no new transactions)"
                    ))
                # Move to processed/ on success
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
        """Import Holdings CSVs into PositionHistory.

        Step 13b protections: content-hash duplicate check (skip before
        parsing), a FileImportLog entry per file, snapshot-date sanity and a
        JSON log per file with parser/sections/counts/verdict — names and
        counts only, never quantities, prices or account digits.
        """
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
        json_dir = Path(
            getattr(settings, 'IMPORT_JSON_DIR', settings.BASE_DIR / 'processed' / 'json_logs')
        )
        for d in (processed_dir, errors_dir, json_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Don't clear PositionHistory - snapshots are cumulative.
        # Each new import closes old open positions and creates new ones.

        created_total = 0
        closed_total = 0
        duplicates = 0
        errors = 0

        for file_path in csv_files:
            # The original file name can carry the account number: it is used
            # only for the on-disk moves; FileImportLog and every output line
            # use a safe name instead (step 13b review).
            filename = os.path.basename(file_path)
            full_hash = content_hash_of(file_path)
            self.stdout.write(f"Importing holdings: {_holdings_safe_name(None, full_hash)}")

            # Content-hash duplicate check (step 13b): the same content
            # already imported successfully → skip before parsing.
            if not dry_run and is_content_imported(full_hash):
                prior = FileImportLog.objects.filter(
                    content_hash=full_hash, status__in=('success', 'ok')
                ).order_by('-imported_at').first()
                safe_name = (
                    prior.filename
                    if prior and is_safe_file_name(prior.filename)
                    else _holdings_safe_name(None, full_hash)
                )
                duplicates += 1
                self.stdout.write(self.style.SUCCESS(
                    "  Duplicate content — already imported; skipping (no parse)"
                ))
                FileImportLog.objects.create(
                    filename=safe_name, broker='RBC',
                    records_imported=0, records_skipped=0,
                    status='duplicate', error_message='Content already imported',
                    content_hash=full_hash,
                )
                _holdings_json_log(json_dir, full_hash, 'duplicate', [], 0)
                dest = _unique_destination(processed_dir, filename)
                _move_file(Path(file_path), dest)
                self.stdout.write(self.style.SUCCESS(f"  Moved to processed/: {safe_name}"))
                continue

            try:
                report = holdings_import_report(file_path)
            except Exception as e:
                safe_name = _holdings_safe_name(None, full_hash)
                self.stderr.write(f"  ERROR importing holdings {safe_name}: {e}")
                errors += 1
                if not dry_run:
                    FileImportLog.objects.create(
                        filename=safe_name, broker='RBC',
                        records_imported=0, records_skipped=0,
                        status='error', error_message=str(e),
                        content_hash=full_hash,
                    )
                    _holdings_json_log(json_dir, full_hash, 'error', [], 0)
                    dest = errors_dir / filename
                    _move_file(Path(file_path), dest)
                    self.stdout.write(self.style.WARNING(f"  Moved to errors/: {safe_name}"))
                continue

            positions = report['positions']
            snapshot_date = report['snapshot_date']
            sections = report['sections']
            holdings_count = len(positions)
            safe_name = _holdings_safe_name(snapshot_date, full_hash)

            if not positions:
                # No parseable data
                errors += 1
                self.stderr.write(self.style.ERROR("  No positions parsed"))
                if not dry_run:
                    FileImportLog.objects.create(
                        filename=safe_name, broker='RBC',
                        records_imported=0, records_skipped=0,
                        status='error', error_message='No positions parsed',
                        content_hash=full_hash,
                    )
                    _holdings_json_log(json_dir, full_hash, 'error', [], 0)
                    dest = errors_dir / filename
                    _move_file(Path(file_path), dest)
                    self.stdout.write(self.style.WARNING(f"  Moved to errors/: {safe_name}"))
                continue

            # Snapshot-date sanity (step 13b): not in the future, not before
            # 2000; no 1-100-day rule for holdings snapshots.
            problems = statement_date_problems(
                snapshot_date, today=timezone.now().date(), last_known=None
            )
            if problems:
                reason = 'Snapshot date: ' + '; '.join(problems)
                errors += 1
                self.stderr.write(self.style.ERROR(f"  {reason}"))
                if not dry_run:
                    FileImportLog.objects.create(
                        filename=safe_name, broker='RBC',
                        records_imported=0, records_skipped=0,
                        status='error', error_message=reason,
                        content_hash=full_hash,
                    )
                    _holdings_json_log(json_dir, full_hash, 'date_error',
                                       sections, holdings_count)
                    dest = errors_dir / filename
                    _move_file(Path(file_path), dest)
                    self.stdout.write(self.style.WARNING(f"  Moved to errors/: {safe_name}"))
                continue

            brief = _sections_brief(sections)
            created, closed = import_holdings_snapshot(file_path, dry_run=dry_run)
            created_total += created
            closed_total += closed

            if dry_run:
                # Nothing is written in dry-run (no log, no verdict, no move).
                self.stdout.write(
                    f"  [DRY-RUN] parser=holdings_csv snapshot={snapshot_date} "
                    f"holdings={holdings_count} sections={brief}"
                )
                continue

            if created + closed > 0:
                verdict = 'imported'
            else:
                # All positions skipped (e.g. unknown accounts) — nothing stored
                verdict = 'error'

            if verdict == 'imported':
                FileImportLog.objects.create(
                    filename=safe_name, broker='RBC',
                    records_imported=created, records_skipped=closed,
                    status='success', error_message='',
                    content_hash=full_hash,
                )
                _holdings_json_log(json_dir, full_hash, verdict, sections, holdings_count)
                self.stdout.write(
                    f"  Imported: parser=holdings_csv snapshot={snapshot_date} "
                    f"holdings={holdings_count} created={created} closed={closed} "
                    f"sections={brief} verdict={verdict}"
                )
                dest = processed_dir / filename
            else:
                FileImportLog.objects.create(
                    filename=safe_name, broker='RBC',
                    records_imported=0, records_skipped=holdings_count,
                    status='error',
                    error_message='No positions imported (all rows skipped)',
                    content_hash=full_hash,
                )
                _holdings_json_log(json_dir, full_hash, verdict, sections, holdings_count)
                self.stdout.write(
                    f"  No positions imported: parser=holdings_csv "
                    f"snapshot={snapshot_date} holdings={holdings_count} "
                    f"sections={brief} verdict={verdict}"
                )
                dest = errors_dir / filename
                errors += 1

            _move_file(Path(file_path), dest)
            label = 'processed/' if verdict == 'imported' else 'errors/'
            self.stdout.write(self.style.SUCCESS(f"  Moved to {label}: {safe_name}"))

        self.stdout.write(
            f"Total: {created_total} created, {closed_total} closed "
            f"({duplicates} duplicates, {errors} errors)"
        )


def _holdings_safe_name(snapshot_date, full_hash: str) -> str:
    """Safe name for holdings artifacts — never the original file name, which
    can carry the account number (step 13b review).

    ``<snapshot date>_holdings_<hash8>.csv`` when the snapshot date is known,
    else ``<hash8>_holdings.csv``.
    """
    h8 = full_hash[:8]
    if snapshot_date:
        return f'{snapshot_date}_holdings_{h8}.csv'
    return f'{h8}_holdings.csv'


def _sections_brief(sections: list[dict]) -> str:
    """One-line summary of holdings sections — names and counts only."""
    if not sections:
        return 'none'
    return '+'.join(
        f"{s['account']}/{s['currency']}:{s['holdings']}" for s in sections
    )


def _holdings_json_log(json_dir: Path, full_hash: str, verdict: str,
                       sections: list[dict], holdings_count: int) -> None:
    """Write the per-file Holdings JSON log (step 13b).

    Names and counts only — never quantities, prices or account digits; the
    original file name carries the account number and is therefore not in
    the payload or the log file name (which is content-hash based). A
    re-run gets a ``_dup`` suffix so the original log is never overwritten.
    """
    payload = {
        'type': 'holdings_csv',
        'parser': 'holdings_csv',
        'processed_at': timezone.now().isoformat(),
        'verdict': verdict,
        'holdings': holdings_count,
        'sections': sections,
    }
    dest = _unique_destination(json_dir, f'{full_hash[:8]}_holdings.json')
    try:
        with open(dest, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps(payload, indent=2, default=str))
    except OSError as exc:
        logger.warning("Could not write holdings JSON log for %s: %s", dest.name, exc)


def _unique_destination(directory: Path, name: str) -> Path:
    """``directory/name``, uniquified with a ``_dup`` suffix if it exists.

    Re-imported duplicate content must never overwrite the file (or its JSON
    log) from the original import.
    """
    dest = directory / name
    if not dest.exists():
        return dest
    stem, dot, ext = name.rpartition('.')
    base = stem if dot else name
    suffix = f'.{ext}' if dot else ''
    n = 1
    while True:
        candidate = directory / f'{base}_dup{n}{suffix}'
        if not candidate.exists():
            return candidate
        n += 1


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
