"""Django management command to auto-import broker files (CSVs and PDFs) from the inbox folder."""

import hashlib
import logging
import os
import shutil
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Dict

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Account, BrokerStatement, FileImportLog, Transaction, TransactionType, compute_tx_hash
from imports.parsers import parse_ib_csv, parse_rbc_csv
from imports.parsers_pdf import parse_rbc_pdf
from imports.rejection_log import rejection_from_trade, write_rejection_log

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
        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
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
        pdf_files = sorted(f for f in os.listdir(inbox_dir) if f.lower().endswith('.pdf'))
        if not csv_files and not pdf_files:
            self.stdout.write("No CSV or PDF files found in inbox.")
            return

        total_imported = total_skipped = total_files = 0

        for filename in csv_files:
            file_path = os.path.join(inbox_dir, filename)
            total_files += 1
            broker = broker_override or 'UNK'

            try:
                # Hash check — skip if identical content was imported before
                with open(file_path, 'rb') as _fh:
                    content_hash = hashlib.sha256(_fh.read()).hexdigest()
                prior = FileImportLog.objects.filter(content_hash=content_hash).first()
                if prior and not dry_run:
                    self.stdout.write(
                        f"  Skipping {filename}: identical content already imported as {prior.filename}"
                    )
                    logger.info(
                        f"auto_import: skipping {filename} — duplicate of {prior.filename}"
                    )
                    # Move the duplicate out of inbox without creating a new log
                    self._move_file(file_path, processed_dir)
                    total_files -= 1  # don't count it as processed
                    continue

                # Detect broker
                broker = broker_override or self._detect_broker(file_path)

                # Parse
                exchange_rate_cad: Decimal = Decimal('1.000000')
                rejections: List[Dict[str, Any]] = []
                if broker == 'RBC':
                    trades, exchange_rate_cad = parse_rbc_csv(file_path, rejections)
                else:
                    trades = parse_ib_csv(file_path, rejections)

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
                        file_date=timezone.now().date(),
                        raw_filename=filename,
                    )

                    for trade in trades:
                        account_id = trade.get('account_id', '').strip().upper()
                        if account_id:
                            account, _ = Account.objects.get_or_create(
                                account_id=account_id,
                                defaults={'name': account_id, 'broker': broker},
                            )
                        else:
                            account = None

                        # Ensure TransactionType record exists
                        TransactionType.objects.get_or_create(
                            code=trade['type'],
                            defaults={'label': trade.get('type_label', trade['type'])},
                        )

                        # Dedup via content hash (broker+account+date+symbol+type+qty+price+amount+currency)
                        tx_hash = compute_tx_hash(
                            broker,
                            account.account_id if account else '',
                            trade['date'],
                            trade['symbol'],
                            trade['type'],
                            trade['quantity'],
                            trade['price'],
                            trade['amount'],
                            trade.get('currency', 'USD'),
                        )
                        if Transaction.objects.filter(tx_hash=tx_hash).exists():
                            skipped_count += 1
                            rejections.append(rejection_from_trade(
                                filename, 0, 'Duplicate (already imported)', trade,
                            ))
                            continue

                        rate = trade.get('exchange_rate_cad', exchange_rate_cad)
                        if trade.get('currency', 'USD') == 'CAD':
                            amount_cad = trade['amount']
                        else:
                            amount_cad = (trade['amount'] * rate).quantize(Decimal('0.01'))

                        Transaction.objects.create(
                            statement=statement,
                            account=account,
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
                            notes=trade.get('notes', ''),
                            tx_hash=tx_hash,
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
                    content_hash=content_hash,
                )

                dest = self._move_file(file_path, processed_dir)
                total_imported += imported_count
                total_skipped += skipped_count

                # Write rejection log to the configured logs directory
                log_path = write_rejection_log(rejections, str(settings.IMPORT_LOGS_DIR), filename)
                if log_path:
                    self.stdout.write(f"  Rejection log: {log_path}")

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

        # --- PDF Processing ---
        if pdf_files:
            self.stdout.write(f"\nProcessing {len(pdf_files)} PDF file(s)...")
            for filename in pdf_files:
                file_path = os.path.join(inbox_dir, filename)
                total_files += 1
                try:
                    # Hash check
                    with open(file_path, 'rb') as _fh:
                        content_hash = hashlib.sha256(_fh.read()).hexdigest()
                    prior = FileImportLog.objects.filter(content_hash=content_hash).first()
                    if prior and not dry_run:
                        self.stdout.write(
                            f"  Skipping {filename}: identical content already imported as {prior.filename}"
                        )
                        logger.info(f"auto_import: skipping {filename} — duplicate of {prior.filename}")
                        if not dry_run:
                            self._move_file(file_path, processed_dir)
                        continue

                    # Parse PDF
                    self.stdout.write(f"  Parsing: {filename}")
                    parsed = parse_rbc_pdf(file_path)

                    if dry_run:
                        self.stdout.write(
                            f"  [DRY RUN] {filename}: {parsed['statement_count']} statement(s), "
                            f"{parsed['transaction_count']} transactions, "
                            f"{parsed['balance_count']} balances"
                        )
                        continue

                    # Import PDF data
                    statements_created = 0
                    transactions_imported = 0
                    balances_recorded = 0
                    duplicates_skipped = 0

                    for stmt_data in parsed['statements']:
                        with transaction.atomic():
                            # Create account
                            account_id = stmt_data.get('account_number', '').replace('-', '').upper()
                            if account_id:
                                account, _ = Account.objects.get_or_create(
                                    account_id=account_id,
                                    defaults={'name': account_id, 'broker': 'RBC'},
                                )
                            else:
                                account = None

                            # Create statement
                            statement = BrokerStatement.objects.create(
                                source='RBC',
                                file_date=timezone.now().date(),
                                raw_filename=filename,
                            )

                            # Import transactions
                            for tx_data in stmt_data.get('transactions', []):
                                tx_hash = compute_tx_hash(
                                    'RBC',
                                    account.account_id if account else '',
                                    tx_data['date'],
                                    tx_data['symbol'],
                                    tx_data['type'],
                                    tx_data['quantity'],
                                    tx_data['price'],
                                    tx_data['amount'],
                                    tx_data.get('currency', 'CAD'),
                                )
                                if Transaction.objects.filter(tx_hash=tx_hash).exists():
                                    duplicates_skipped += 1
                                    continue

                                Transaction.objects.create(
                                    statement=statement,
                                    account=account,
                                    date=tx_data['date'],
                                    symbol=tx_data['symbol'],
                                    type=tx_data['type'],
                                    quantity=tx_data['quantity'],
                                    price=tx_data['price'],
                                    commission=tx_data.get('commission', Decimal('0.00')),
                                    amount=tx_data['amount'],
                                    currency=tx_data.get('currency', 'CAD'),
                                    exchange_rate_cad=tx_data.get('exchange_rate_cad', Decimal('1.000000')),
                                    amount_cad=tx_data.get('amount_cad', tx_data['amount']),
                                    notes=tx_data.get('notes', ''),
                                    tx_hash=tx_hash,
                                )
                                transactions_imported += 1

                            if transactions_imported > 0:
                                statements_created += 1

                    # Record balances
                    for bal_data in parsed.get('balances', []):
                        with transaction.atomic():
                            balance_hash = hashlib.sha256(
                                f"{filename}:{bal_data['date']}:{bal_data['asset_class']}:{bal_data['amount']}".encode()
                            ).hexdigest()
                            if not FileImportLog.objects.filter(content_hash=balance_hash).exists():
                                FileImportLog.objects.create(
                                    filename=f"{filename}_balance_{bal_data['date']}",
                                    broker='RBC',
                                    records_imported=1,
                                    records_skipped=0,
                                    status='success',
                                    content_hash=balance_hash,
                                )
                                balances_recorded += 1

                    status = 'success' if statements_created > 0 else 'partial'
                    FileImportLog.objects.create(
                        filename=filename,
                        broker='RBC',
                        records_imported=transactions_imported,
                        records_skipped=duplicates_skipped,
                        status=status,
                        content_hash=content_hash,
                    )

                    if not dry_run:
                        self._move_file(file_path, processed_dir)

                    total_imported += transactions_imported
                    total_skipped += duplicates_skipped

                    self.stdout.write(
                        f"  {filename}: {statements_created} statements, "
                        f"{transactions_imported} transactions, "
                        f"{balances_recorded} balances, {duplicates_skipped} skipped"
                    )
                    logger.info(
                        f"auto_import: {filename} (PDF) - "
                        f"{transactions_imported} imported, {duplicates_skipped} skipped"
                    )

                except Exception as exc:
                    FileImportLog.objects.create(
                        filename=filename,
                        broker='RBC',
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
