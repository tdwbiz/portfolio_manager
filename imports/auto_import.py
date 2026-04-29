"""Automated import script for broker statements."""

import hashlib
import logging
import os
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone
from django.db import transaction

# Setup Django environment
import django
if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': ':memory:',
            }
        },
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
            'core',
            'imports',
        ],
        ROOT_URLCONF='',
        USE_TZ=True,
    )
django.setup()

from core.models import BrokerStatement, Transaction, Account, FileImportLog
from imports.parsers import parse_ib_csv, parse_rbc_csv

logger = logging.getLogger(__name__)


def auto_import(file_path: str, broker: str, dry_run: bool = False) -> None:
    """
    Import a broker statement file.
    
    Args:
        file_path: Path to the CSV file.
        broker: 'IB' or 'RBC'.
        dry_run: If True, only parse and report.
    """
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return

    # Read file bytes for hashing
    with open(file_path, 'rb') as f:
        file_bytes = f.read()
    
    content_hash = hashlib.sha256(file_bytes).hexdigest()

    # Check for duplicate file content (skip if found, unless dry-run)
    if not dry_run:
        prior_log = FileImportLog.objects.filter(content_hash=content_hash).first()
        if prior_log:
            logger.warning(f"Skipping {os.path.basename(file_path)}: identical content already imported as {prior_log.filename}")
            return

    # Parse CSV
    logger.info(f"Parsing {broker} CSV file: {file_path}")
    exchange_rate_cad: Decimal = Decimal('1.000000')
    if broker == 'IB':
        trades = parse_ib_csv(file_path)
    elif broker == 'RBC':
        trades, exchange_rate_cad = parse_rbc_csv(file_path)
    else:
        logger.error(f"Unsupported broker type: {broker}")
        return

    if not trades:
        logger.warning("No trades found in the file.")
        return

    logger.info(f"Found {len(trades)} trades to import.")

    if dry_run:
        logger.info("Dry run complete. No changes made to the database.")
        for i, trade in enumerate(trades, 1):
            logger.info(f"  {i}. {trade}")
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
            logger.info(f"Created BrokerStatement: {statement}")

            imported_count = 0
            skipped_count = 0

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

                # Check for duplicates (including account)
                try:
                    existing = Transaction.objects.get(
                        statement__source=broker,
                        date=trade['date'],
                        symbol=trade['symbol'],
                        type=trade['type'],
                        quantity=trade['quantity'],
                        price=trade['price'],
                        account__account_id=account_id if account_id else '',
                    )
                    logger.debug(f"Duplicate transaction found: {existing}")
                    skipped_count += 1
                    continue
                except Transaction.DoesNotExist:
                    pass

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
                    )
                    imported_count += 1
                except Exception as e:
                    logger.error(f"Error creating transaction for {trade}: {e}")
                    raise

            # Save file import log
            FileImportLog.objects.create(
                filename=os.path.basename(file_path),
                broker=broker,
                records_imported=imported_count,
                records_skipped=skipped_count,
                status='success' if imported_count > 0 else 'partial',
                content_hash=content_hash,
            )

            logger.info(f"Successfully imported {imported_count} transactions ({skipped_count} duplicates skipped).")

            # Roll back the BrokerStatement if nothing was actually imported
            if imported_count == 0:
                statement.delete()

    except Exception as e:
        logger.error(f"Import failed: {e}")


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print("Usage: python auto_import.py <file_path> <broker>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    broker = sys.argv[2]
    dry_run = '--dry-run' in sys.argv
    
    auto_import(file_path, broker, dry_run)
