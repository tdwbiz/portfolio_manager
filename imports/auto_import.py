"""Automated import script for broker statements.

REQUIRES Django to be configured (e.g., via python manage.py auto_import).
This file does NOT configure Django itself — it expects the project's
settings to already be loaded.
"""

import csv
import hashlib
import logging
import os
import re
from decimal import Decimal
from pathlib import Path

from datetime import date

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from core.log_filter import ScrubFilter
from core.models import BrokerStatement, Transaction, Account, FileImportLog, compute_tx_hash
from core.utils.account_fingerprint import (
    build_fingerprint_name_map,
    lookup_account,
    scrub_text,
    wrap_tx_hash,
)
from imports.parsers import parse_ib_csv, parse_rbc_csv

logger = logging.getLogger(__name__)


def _setup_duplicate_logger() -> None:
    """Set up a one-shot file handler for duplicate logging."""
    dup_logger = logging.getLogger('imports.duplicates')
    dup_logger.setLevel(logging.INFO)

    for handler in dup_logger.handlers[:]:
        dup_logger.removeHandler(handler)

    dup_log_path = Path('docs/reconciliation/CSV_Duplicates.csv')
    dup_log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        dup_handler = logging.FileHandler(dup_log_path, encoding='utf-8')
        dup_handler.setFormatter(logging.Formatter('%(message)s'))
        dup_handler.addFilter(ScrubFilter())  # scrub at the source: this logger writes straight to CSV_Duplicates.csv
        dup_logger.addHandler(dup_handler)

        # Write header if file doesn't exist or is empty
        if not dup_log_path.exists() or dup_log_path.stat().st_size == 0:
            with open(dup_log_path, 'w', encoding='utf-8', newline='') as f:
                f.write('Source_File,CSV_Line,Account,Date,Symbol,Type,Quantity,Price,Amount,Currency,Notes\n')
    except PermissionError:
        # File is locked (e.g. open in Excel) — fall back to console logger
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(message)s'))
        console_handler.addFilter(ScrubFilter())  # scrub at the source for the locked-file fallback
        dup_logger.addHandler(console_handler)


def auto_import(file_path: str, broker: str, dry_run: bool = False) -> tuple[int, int, int]:
    """
    Import a broker statement file.
    
    Args:
        file_path: Path to the CSV file.
        broker: 'IB' or 'RBC'.
        dry_run: If True, only parse and report.
    
    Returns:
        Tuple of (imported_count, skipped_count, parsed_count).
        - dry_run: parsed_count = number of trades found (imported_count, skipped_count always 0)
        - actual: imported_count = transactions written to DB,
                  skipped_count = duplicates already in DB,
                  parsed_count = total trades found by parser
    """
    # One query for the fingerprint->name map; scrub_text must never query
    # per candidate (SQL Server without MARS).
    name_map = build_fingerprint_name_map()

    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return (0, 0, 0)

    # Read file bytes for hashing
    with open(file_path, 'rb') as f:
        file_bytes = f.read()
    
    content_hash = hashlib.sha256(file_bytes).hexdigest()

    # Check for duplicate file content (skip if found, unless dry-run)
    if not dry_run:
        prior_log = FileImportLog.objects.filter(content_hash=content_hash).first()
        if prior_log:
            logger.warning(f"Skipping {os.path.basename(file_path)}: identical content already imported as {prior_log.filename}")
            return (0, 0, 1)

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
        return (0, 0, 0)

    if not trades:
        logger.warning("No trades found in the file.")
        return (0, 0, 0)

    logger.info(f"Found {len(trades)} trades to import.")

    if dry_run:
        logger.info("Dry run complete. No changes made to the database.")
        for i, trade in enumerate(trades, 1):
            logger.info(f"  {i}. {trade}")
        return (0, 0, len(trades))

    # Set up duplicate logger — writes CSV with line numbers
    _setup_duplicate_logger()
    dup_logger = logging.getLogger('imports.duplicates')

    # Import to DB
    imported_count = 0
    skipped_count = 0
    try:
        with transaction.atomic():
            # Create BrokerStatement
            statement = BrokerStatement.objects.create(
                source=broker,
                file_date=timezone.now().date(),
                raw_filename=os.path.basename(file_path),
            )
            logger.info(f"Created BrokerStatement: {statement}")

            for trade in trades:
                # Get account_id from trade
                raw_account_id = trade.get('account_id', '').strip().upper()
                
                # Skip PRACEF practice accounts
                if raw_account_id.startswith('PRACEF'):
                    skipped_count += 1
                    continue
                
                # Normalize account ID to 8-digit canonical form
                if raw_account_id:
                    digits = re.sub(r'\D', '', raw_account_id)
                    normalized_id = digits[:8] if len(digits) >= 8 else digits
                else:
                    normalized_id = None
                
                if raw_account_id:
                    account = lookup_account(raw_account_id)
                    if account is None:
                        logger.warning(
                            "Unknown account fingerprint — skipping transaction "
                            "(do not auto-create accounts from CSV)"
                        )
                        skipped_count += 1
                        continue
                else:
                    account = None
                    skipped_count += 1
                    continue

                # Compute tx_hash for deduplication, then wrap it: the stored
                # value is HMAC-SHA256(fingerprint key, raw hash) — never the
                # raw SHA-256 itself.
                tx_hash = wrap_tx_hash(compute_tx_hash(
                    broker=broker,
                    account_id=normalized_id,
                    date=trade['date'],
                    symbol=trade['symbol'],
                    tx_type=trade['type'],
                    quantity=trade['quantity'],
                    price=trade['price'],
                    currency=trade.get('currency', 'USD'),
                    amount=trade['amount'],
                    row_number=trade.get('row_number'),
                ))

                # Check for duplicate by tx_hash
                if Transaction.objects.filter(tx_hash=tx_hash).exists():
                    skipped_count += 1
                    row_num = trade.get('row_number', '')
                    # Write duplicate detail to dedicated logfile
                    dup_logger.info(
                        f'{os.path.basename(file_path)},Line {row_num},{account.name},'
                        f'{trade["date"]},{trade["symbol"]},{trade["type"]},'  
                        f'{trade["quantity"]},{trade["price"]},{trade["amount"]},'  
                        f'{trade.get("currency", "USD")},{trade.get("notes", "")}'
                    )
                    failed_trades.append({
                        'Source_File': os.path.basename(file_path),
                        'account': account.name,
                        'date': str(trade['date']),
                        'symbol': trade['symbol'],
                        'type': trade['type'],
                        'amount': trade['amount'],
                        'currency': trade.get('currency', 'USD'),
                        'quantity': trade['quantity'],
                        'error': 'Duplicate (hash match)',
                        'CSV_Line': row_num,
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
                        notes=scrub_text(trade.get('notes', '') or '', name_map)[0],
                        tx_hash=tx_hash,
                    )
                    imported_count += 1
                    imported_trades.append({
                        'Source_File': os.path.basename(file_path),
                        'account': account.name,
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
        # Ensure import_log file exists before checking size
        if not import_log.exists():
            import_log.touch()
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
                'Source_File', 'account', 'date', 'symbol', 'type', 'amount', 'currency', 'quantity', 'error', 'CSV_Line'
            ])
            if reject_log.exists() and reject_log.stat().st_size == 0:
                writer.writeheader()
            writer.writerows(all_rejections)

    return (imported_count, skipped_count, len(trades))


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print("Usage: python auto_import.py <file_path> <broker>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    broker = sys.argv[2]
    dry_run = '--dry-run' in sys.argv
    
    auto_import(file_path, broker, dry_run)
