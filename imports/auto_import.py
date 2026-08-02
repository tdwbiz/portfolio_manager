"""Automated import script for broker statements.

REQUIRES Django to be configured (e.g., via python manage.py auto_import).
This file does NOT configure Django itself — it expects the project's
settings to already be loaded.
"""

import csv
import hashlib
import logging
import os
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.db import transaction

# Verify Django is configured
if not settings.configured:
    raise ImproperlyConfigured(
        "Django is not configured. Run this script via: python manage.py auto_import"
    )

from core.models import BrokerStatement, Transaction, Account, FileImportLog, compute_tx_hash
from imports.parsers import parse_ib_csv, parse_rbc_csv
import re
from datetime import date

logger = logging.getLogger(__name__)


def extract_statement_date(content: str) -> date | None:
    """Extract statement date from CSV content using regex.

    Tries multiple common date patterns found in broker CSV headers.

    Args:
        content: Raw CSV file content as string.

    Returns:
        Extracted date or None if not found.
    """
    # Pattern 1: "Statement Date: MM/DD/YYYY" or "Statement Date: MM-DD-YYYY"
    patterns = [
        r'Statement\s+Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
        r'Statement\s+Period[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
        r'Period\s+End[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
        r'Report\s+Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
        r'(\d{4}[/-]\d{1,2}[/-]\d{1,2})',  # YYYY-MM-DD or YYYY/MM/DD
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            date_str = match.group(1).replace('/', '-')
            try:
                parts = date_str.split('-')
                if len(parts) == 3:
                    # Handle YYYY-MM-DD vs MM-DD-YYYY
                    if len(parts[0]) == 4:
                        return date(int(parts[0]), int(parts[1]), int(parts[2]))
                    else:
                        return date(int(parts[2]), int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                continue
    return None


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

    # Setup import tracking
    import_log = Path('docs/reconciliation/CSV_Activity_Imported.csv')
    reject_log = Path('docs/reconciliation/CSV_Activity_Rejected.csv')
    import_log.parent.mkdir(parents=True, exist_ok=True)
    reject_log.parent.mkdir(parents=True, exist_ok=True)
    imported_trades = []
    failed_trades = []
    rejections = []

    # Parse CSV
    logger.info(f"Parsing {broker} CSV file: {file_path}")
    exchange_rate_cad: Decimal = Decimal('1.000000')
    if broker == 'IB':
        trades = parse_ib_csv(file_path, rejections)
    elif broker == 'RBC':
        trades, exchange_rate_cad = parse_rbc_csv(file_path, rejections)
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

                # Compute tx_hash for deduplication
                tx_hash = compute_tx_hash(
                    broker=broker,
                    account_id=account_id,
                    date=trade['date'],
                    symbol=trade['symbol'],
                    tx_type=trade['type'],
                    quantity=trade['quantity'],
                    price=trade['price'],
                    currency=trade.get('currency', 'USD'),
                    amount=trade['amount'],
                )

                # Check for duplicate by tx_hash
                if Transaction.objects.filter(tx_hash=tx_hash).exists():
                    logger.debug(f"Duplicate transaction (hash match): {tx_hash}")
                    skipped_count += 1
                    failed_trades.append({
                        'Source_File': os.path.basename(file_path),
                        'account': account_id,
                        'date': str(trade['date']),
                        'symbol': trade['symbol'],
                        'type': trade['type'],
                        'amount': trade['amount'],
                        'currency': trade.get('currency', 'USD'),
                        'quantity': trade['quantity'],
                        'error': 'Duplicate (hash match)',
                    })
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
                        settlement_date=trade.get('settlement_date'),
                        symbol=trade['symbol'],
                        type=trade['type'],
                        quantity=trade['quantity'],
                        price=trade['price'],
                        commission=trade.get('commission', Decimal('0.00')),
                        amount=trade['amount'],
                        currency=trade.get('currency', 'USD'),
                        exchange_rate_cad=rate,
                        amount_cad=amount_cad,
                        tx_hash=tx_hash,
                    )
                    imported_count += 1
                    imported_trades.append({
                        'Source_File': os.path.basename(file_path),
                        'account': account_id,
                        'date': str(trade['date']),
                        'symbol': trade['symbol'],
                        'type': trade['type'],
                        'amount': trade['amount'],
                        'currency': trade.get('currency', 'USD'),
                        'quantity': trade['quantity'],
                    })
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

    # Write imported transactions to tracking file (after commit)
    if imported_trades:
        with open(import_log, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'Source_File', 'account', 'date', 'symbol', 'type', 'amount', 'currency', 'quantity'
            ])
            if import_log.stat().st_size == 0:
                writer.writeheader()
            writer.writerows(imported_trades)

    # Write failed/rejected transactions
    all_rejections = failed_trades + [
        {
            'Source_File': r.get('Filename', ''),
            'account': r.get('Account ID', ''),
            'date': r.get('Date', ''),
            'symbol': r.get('Symbol', ''),
            'type': r.get('Type', ''),
            'amount': r.get('Amount', ''),
            'currency': r.get('Currency', ''),
            'quantity': r.get('Quantity', ''),
            'error': r.get('Reason Rejected', ''),
        }
        for r in rejections
    ]
    if all_rejections:
        with open(reject_log, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'Source_File', 'account', 'date', 'symbol', 'type', 'amount', 'currency', 'quantity', 'error'
            ])
            if reject_log.stat().st_size == 0:
                writer.writeheader()
            writer.writerows(all_rejections)


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print("Usage: python auto_import.py <file_path> <broker>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    broker = sys.argv[2]
    dry_run = '--dry-run' in sys.argv
    
    auto_import(file_path, broker, dry_run)
