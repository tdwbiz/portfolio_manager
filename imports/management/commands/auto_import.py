"""Django management command to auto-import broker CSVs from the inbox folder."""

import logging
import os
import shutil
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Dict

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import BrokerStatement, FileImportLog, Transaction
from imports.parsers import parse_ib_csv, parse_rbc_csv

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Scan inbox/ for CSV files and auto-import broker transactions.'

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            '--broker',
            type=str,
            choices=['IB', 'RBC'],
            default=None,
            help='Override broker detection (IB or RBC)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and report without writing to DB or moving files',
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _detect_broker(self, file_path: str) -> str:
        """Detect broker type from file content (not filename)."""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read(4096)  # read first 4KB — enough for headers

        if 'Activity Export' in content or '"Activity"' in content:
            return 'RBC'
        if 'Transaction History' in content or 'Statement' in content:
            return 'IB'
        return 'IB'  # default

    def _move_file(self, src: str, dest_dir: str) -> str:
        """Atomic copy-then-delete to dest_dir, appending a timestamp."""
        os.makedirs(dest_dir, exist_ok=True)
        name, ext = os.path.splitext(os.path.basename(src))
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        dest_path = os.path.join(dest_dir, f"{name}_{timestamp}{ext}")
        shutil.copy2(src, dest_path)
        os.remove(src)
        return dest_path

    # ------------------------------------------------------------------
    # Main handler
    # ------------------------------------------------------------------

    def handle(self, *args: Any, **options: Any) -> None:
        broker_override: str | None = options['broker']
        dry_run: bool = options['dry_run']

        inbox_dir = str(getattr(settings, 'IMPORT_INBOX_DIR', settings.BASE_DIR / 'inbox'))
        processed_dir = str(getattr(settings, 'IMPORT_PROCESSED_DIR', settings.BASE_DIR / 'processed'))
        error_dir = os.path.join(processed_dir, 'errors')

        if not os.path.exists(inbox_dir):
            self.stdout.write(self.style.ERROR(f"Inbox directory not found: {inbox_dir}"))
            return

        csv_files = sorted(f for f in os.listdir(inbox_dir) if f.lower().endswith('.csv'))
        if not csv_files:
            self.stdout.write("No CSV files found in inbox.")
            return

        total_imported = total_skipped = total_files = 0

        for filename in csv_files:
            file_path = os.path.join(inbox_dir, filename)
            total_files += 1
            broker = broker_override or 'UNK'

            try:
                # Detect broker
                broker = broker_override or self._detect_broker(file_path)

                # Parse
                exchange_rate_cad: Decimal = Decimal('1.000000')
                if broker == 'RBC':
                    trades, exchange_rate_cad = parse_rbc_csv(file_path)
                else:
                    trades = parse_ib_csv(file_path)

                if dry_run:
                    self.stdout.write(
                        f"[DRY RUN] {filename} ({broker}): {len(trades)} trades detected"
                    )
                    continue

                # Import
                imported_count = skipped_count = 0

                with transaction.atomic():
                    statement = BrokerStatement.objects.create(
                        source=broker,
                        file_date=datetime.now().date(),
                        raw_filename=filename,
                    )

                    for trade in trades:
                        exists = Transaction.objects.filter(
                            statement__source=broker,
                            date=trade['date'],
                            symbol=trade['symbol'],
                            type=trade['type'],
                            quantity=trade['quantity'],
                            price=trade['price'],
                        ).exists()

                        if exists:
                            skipped_count += 1
                            continue

                        rate = trade.get('exchange_rate_cad', exchange_rate_cad)
                        if trade.get('currency', 'USD') == 'CAD':
                            amount_cad = trade['amount']
                        else:
                            amount_cad = (trade['amount'] * rate).quantize(Decimal('0.01'))

                        Transaction.objects.create(
                            statement=statement,
                            date=trade['date'],
                            symbol=trade['symbol'],
                            type=trade['type'],
                            quantity=trade['quantity'],
                            price=trade['price'],
                            commission=trade.get('commission', Decimal('0.00')),
                            amount=trade['amount'],
                            currency=trade.get('currency', 'USD'),
                            exchange_rate_cad=rate,
                            amount_cad=amount_cad,
                        )
                        imported_count += 1

                    if imported_count == 0:
                        statement.delete()

                status = 'success' if imported_count > 0 else 'partial'
                FileImportLog.objects.create(
                    filename=filename,
                    broker=broker,
                    records_imported=imported_count,
                    records_skipped=skipped_count,
                    status=status,
                )

                dest = self._move_file(file_path, processed_dir)
                total_imported += imported_count
                total_skipped += skipped_count

                logger.info(
                    f"auto_import: {filename} ({broker}) - "
                    f"{imported_count} imported, {skipped_count} skipped -> {dest}"
                )
                self.stdout.write(
                    f"  {filename} ({broker}): {imported_count} imported, {skipped_count} skipped"
                )

            except Exception as exc:
                FileImportLog.objects.create(
                    filename=filename,
                    broker=(broker_override or 'UNK')[:3],
                    status='error',
                    error_message=str(exc),
                )
                try:
                    self._move_file(file_path, error_dir)
                except Exception as move_err:
                    logger.error(f"auto_import: failed to move error file {filename}: {move_err}")

                logger.error(f"auto_import: error processing {filename}: {exc}")
                self.stdout.write(self.style.ERROR(f"  {filename}: ERROR — {exc}"))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone: {total_files} file(s) processed, "
                f"{total_imported} imported, {total_skipped} skipped."
            ))
