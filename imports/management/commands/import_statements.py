"""Django management command to import broker statements."""

import hashlib
import logging
import os
from typing import Any

from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import BrokerStatement, Transaction, Account, FileImportLog, TransactionType, compute_tx_hash
from imports.parsers import parse_ib_csv, parse_rbc_csv
from imports.rejection_log import rejection_from_trade, write_rejection_log

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Import broker statements (IB or RBC) from CSV files.'

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            '--broker',
            type=str,
            required=True,
            choices=['IB', 'RBC'],
            help='Broker type (IB or RBC)',
        )
        parser.add_argument(
            '--file',
            type=str,
            required=True,
            help='Path to the CSV file',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and report, do not save to DB',
        )

    def handle(self, *args: Any, **options: Any) -> None:
        broker: str = options['broker']
        file_path: str = options['file']
        dry_run: bool = options['dry_run']

        # Validate file exists
        if not os.path.exists(file_path):
            raise CommandError(f"File not found: {file_path}")

        # Read file bytes for hashing
        with open(file_path, 'rb') as f:
            file_bytes = f.read()
        
        content_hash = hashlib.sha256(file_bytes).hexdigest()

        # Check for duplicate file content (skip if found, unless dry-run)
        if not dry_run:
            prior_log = FileImportLog.objects.filter(content_hash=content_hash).first()
            if prior_log:
                self.stdout.write(self.style.WARNING(
                    f"Skipping {os.path.basename(file_path)}: identical content already imported as {prior_log.filename}"
                ))
                return

        # Parse CSV
        self.stdout.write(f"Parsing {broker} CSV file: {file_path}")
        exchange_rate_cad: Decimal = Decimal('1.000000')
        rejections: List[Dict[str, Any]] = []
        if broker == 'IB':
            trades: List[Dict[str, Any]] = parse_ib_csv(file_path, rejections)
        elif broker == 'RBC':
            trades, exchange_rate_cad = parse_rbc_csv(file_path, rejections)
        else:
            raise CommandError(f"Unsupported broker type: {broker}")

        if not trades:
            self.stdout.write(self.style.WARNING("No trades found in the file."))
            return

        self.stdout.write(f"Found {len(trades)} trades to import.")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry run complete. No changes made to the database."))
            for i, trade in enumerate(trades, 1):
                self.stdout.write(f"  {i}. {trade}")
            return

        # Import to DB
        try:
            with transaction.atomic():
                # Create BrokerStatement
                statement = BrokerStatement.objects.create(
                    source=broker,
                    file_date=timezone.now().date(),
                    raw_filename=os.path.basename(file_path),
                )
                self.stdout.write(f"Created BrokerStatement: {statement}")

                imported_count: int = 0
                skipped_count: int = 0

                for trade in trades:
                    # Get account_id from trade
                    account_id = trade.get('account_id', '').strip().upper()
                    if account_id:
                        account, _ = Account.objects.get_or_create(
                            account_id=account_id,
                            defaults={'name': account_id, 'broker': broker}
                        )
                    else:
                        account = None

                    # Ensure TransactionType record exists
                    TransactionType.objects.get_or_create(
                        code=trade['type'],
                        defaults={'label': trade.get('type_label', trade['type'])},
                    )

                    # Dedup via content hash
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
                        logger.debug(f"Duplicate transaction skipped: {trade}")
                        skipped_count += 1
                        rejections.append(rejection_from_trade(
                            os.path.basename(file_path), 0, 'Duplicate (already imported)', trade,
                        ))
                        continue

                    # Create transaction
                    try:
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
                    except Exception as e:
                        logger.error(f"Error creating transaction for {trade}: {e}")
                        raise CommandError(f"Failed to import transaction: {e}")

                # Save file import log
                FileImportLog.objects.create(
                    filename=os.path.basename(file_path),
                    broker=broker,
                    records_imported=imported_count,
                    records_skipped=skipped_count,
                    status='success' if imported_count > 0 else 'partial',
                    content_hash=content_hash,
                )

                self.stdout.write(self.style.SUCCESS(
                    f"Successfully imported {imported_count} transactions "
                    f"({skipped_count} duplicates skipped)."
                ))

                # Write rejection log to the configured logs directory
                log_path = write_rejection_log(rejections, str(settings.IMPORT_LOGS_DIR), os.path.basename(file_path))
                if log_path:
                    self.stdout.write(f"Rejection log written: {log_path}")

                # Roll back the BrokerStatement if nothing was actually imported
                if imported_count == 0:
                    statement.delete()

        except CommandError:
            raise
        except Exception as e:
            raise CommandError(f"Import failed: {e}")
