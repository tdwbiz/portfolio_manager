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
from dataclasses import dataclass, field
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


@dataclass
class ImportReport:
    """Per-file import report (plan step 3/6).

    Row accounting: ``data_rows = imported + duplicates + practice +
    sum(rejected.values())``. In a dry run, ``imported`` is the number of
    rows that would be imported.
    """
    filename: str = ''
    broker: str = ''
    dry_run: bool = False
    footer: int = 0            # RBC disclaimer footer rows (outside data_rows)
    data_rows: int = 0          # data rows seen in the file
    imported: int = 0           # created (or, in a dry run, would be created)
    duplicates: int = 0         # skipped because the tx_hash is already stored
    practice: int = 0           # PRAC* rows
    rejected: dict = field(default_factory=dict)   # reason -> count
    failed: bool = False        # row-count mismatch — file failed atomically
    fail_reason: str = ''
    # Dry-run only (plan step 6):
    breakdown: dict = field(default_factory=dict)
    #   (account name, type, 'YYYY-MM') -> {'import': n, 'duplicate': n}
    rows: list = field(default_factory=list)
    #   parsed rows (names/dates/counts only) for cross-file near-duplicate
    #   detection in the import_csv dry-run report
    neg_qty_by_type: dict = field(default_factory=dict)
    #   Dry-run only (review fix 5): transaction type -> number of rows whose
    #   quantity is negative in the CSV itself (stored quantity stays abs()).

    def accounted(self) -> int:
        return (self.imported + self.duplicates + self.practice
                + sum(self.rejected.values()))


class _AccountingMismatch(Exception):
    """A file's row accounting does not add up (parser bug — plan decision 4)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _hash_group_key(broker: str, trade: dict) -> tuple:
    """Key identifying rows with identical ``compute_tx_hash`` inputs.

    Mirrors the hash fields exactly (except seq itself) so that two rows
    which would hash identically in one file share a group and receive
    distinct seq values (plan step 5).
    """
    raw_account_id = trade.get('account_id', '').strip().upper()
    if raw_account_id:
        digits = re.sub(r'\D', '', raw_account_id)
        normalized_id = digits[:8] if len(digits) >= 8 else digits
    else:
        normalized_id = None
    tx_type = trade['type']
    amount = trade.get('amount')
    key = (
        broker,
        normalized_id,
        str(trade.get('date')),
        trade.get('symbol', ''),
        tx_type,
        f"{Decimal(str(trade.get('quantity', 0))):.4f}",
        f"{Decimal(str(trade.get('price', 0))):.4f}",
        trade.get('currency', 'USD'),
        str(trade.get('settlement_date')),
    )
    if tx_type not in ('BUY', 'SELL') and amount is not None:
        key = key + (f"{Decimal(str(amount)).quantize(Decimal('0.01')):.2f}",)
    return key


def _assign_seq(trades: list[dict], broker: str) -> None:
    """Assign a within-file ``seq`` to rows with identical hash inputs.

    Rows are grouped by their hash inputs in file order; the n-th row of a
    group gets seq n. Two identical rows in one file → seq 1 and 2, both
    imported with different hashes. The same row in two overlapping exports
    → seq 1 in each file, same hash, second skipped as duplicate.
    """
    counts: dict[tuple, int] = {}
    for trade in trades:
        key = _hash_group_key(broker, trade)
        counts[key] = counts.get(key, 0) + 1
        trade['seq'] = counts[key]


def _accounting_mismatch_message(report: ImportReport, filename: str) -> str:
    """Message describing a row-accounting mismatch, or '' when it adds up.

    The same check runs in the real import (plan decision 4 — fails the
    file atomically) and in ``--dry-run`` (review fix 3 — reported as a
    failed file).
    """
    accounted = report.accounted()
    if accounted == report.data_rows:
        return ''
    return (f"Row count mismatch in {filename}: data rows={report.data_rows}, "
            f"accounted={accounted} (imported={report.imported}, "
            f"duplicates={report.duplicates}, practice={report.practice}, "
            f"rejected={sum(report.rejected.values())})")


def _dry_run_report(report: ImportReport, trades: list[dict],
                    broker: str,
                    seen_hashes: set | None = None) -> ImportReport:
    """Fill in a dry-run report without writing anything (plan step 6).

    Duplicates are checked against the DB read-only, and — since nothing is
    written during the run — also against the hashes already seen in an
    earlier file of the same run (``seen_hashes``, review fix 3). Per-
    account/type/month counts go into ``report.breakdown`` and the parsed
    rows (account name, date, symbol, value — never the Account column)
    into ``report.rows`` for cross-file near-duplicate detection.
    """
    if seen_hashes is None:
        seen_hashes = set()
    for trade in trades:
        raw_account_id = trade.get('account_id', '').strip().upper()

        # Defensive: the RBC parser already rejects PRAC* rows.
        if raw_account_id.startswith('PRACEF'):
            report.practice += 1
            continue

        # CSV sign of the quantity, per transaction type (review fix 5):
        # counted for every parsed row, duplicates included.
        if Decimal(str(trade.get('quantity_csv', 0))) < 0:
            tx_type = trade['type']
            report.neg_qty_by_type[tx_type] = (
                report.neg_qty_by_type.get(tx_type, 0) + 1
            )

        if raw_account_id:
            digits = re.sub(r'\D', '', raw_account_id)
            normalized_id = digits[:8] if len(digits) >= 8 else digits
            account = lookup_account(raw_account_id)
            if account is None:
                report.rejected['Unknown account'] = (
                    report.rejected.get('Unknown account', 0) + 1
                )
                continue
        else:
            normalized_id = None
            report.rejected['Missing account'] = (
                report.rejected.get('Missing account', 0) + 1
            )
            continue

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
            settlement_date=trade.get('settlement_date'),
            seq=trade.get('seq', 1),
        ))

        month = f"{trade['date'].year:04d}-{trade['date'].month:02d}"
        bucket = report.breakdown.setdefault(
            (account.name, trade['type'], month),
            {'import': 0, 'duplicate': 0},
        )
        if (Transaction.objects.filter(tx_hash=tx_hash).exists()
                or tx_hash in seen_hashes):
            bucket['duplicate'] += 1
            report.duplicates += 1
        else:
            seen_hashes.add(tx_hash)
            bucket['import'] += 1
            report.imported += 1

        report.rows.append({
            'filename': report.filename,
            'row_number': trade.get('row_number', ''),
            'account': account.name,
            'date': str(trade['date']),
            'symbol': trade['symbol'],
            'type': trade['type'],
            'quantity': str(trade['quantity']),
            'price': str(trade['price']),
            'currency': trade.get('currency', 'USD'),
            'value': str(trade['amount']),
            'settlement_date': str(trade.get('settlement_date')),
        })
    return report


def auto_import(file_path: str, broker: str, dry_run: bool = False,
                seen_hashes: set | None = None) -> ImportReport:
    """
    Import a broker statement file.

    Args:
        file_path: Path to the CSV file.
        broker: 'IB' or 'RBC'.
        dry_run: If True, only parse and report (no DB writes).

    Returns:
        An :class:`ImportReport` with per-file row accounting. A row-count
        mismatch — or any other exception during the import (review fix 2)
        — fails the whole file atomically (nothing stored), records a
        ``FileImportLog`` with status ``error`` carrying a scrubbed reason,
        and sets ``report.failed`` so the caller can continue with the next
        file.
    """
    filename = os.path.basename(file_path)
    report = ImportReport(filename=filename, broker=broker, dry_run=dry_run)

    # One query for the fingerprint->name map; scrub_text must never query
    # per candidate (SQL Server without MARS).
    name_map = build_fingerprint_name_map()

    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return report

    # Read file bytes for hashing
    with open(file_path, 'rb') as f:
        file_bytes = f.read()

    # Content hash is still written to the FileImportLog, but it never skips
    # a file: after core_transaction is cleared a re-import must run, and a
    # file whose earlier log was an error must not be skipped forever.
    # Row-level tx_hash already decides duplicates — a re-imported identical
    # file just reports all rows as duplicates (review fix 1).
    content_hash = hashlib.sha256(file_bytes).hexdigest()

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
        # The IB parser returns no accounting — derive it from its output so
        # the row-accounting invariant still holds for this path.
        practice = sum(
            1 for r in rejections
            if r['Reason Rejected'].startswith('Practice account')
        )
        rejected_counts: dict[str, int] = {}
        for r in rejections:
            if r['Reason Rejected'].startswith('Practice account'):
                continue
            reason = r['Reason Rejected']
            rejected_counts[reason] = rejected_counts.get(reason, 0) + 1
        accounting = {
            'data_rows': len(trades) + len(rejections),
            'imported': len(trades),
            'practice': practice,
            'rejected': rejected_counts,
        }
    elif broker == 'RBC':
        trades, exchange_rate_cad, accounting = parse_rbc_csv(file_path, rejections)
    else:
        logger.error(f"Unsupported broker type: {broker}")
        return report

    report.footer = accounting.get('footer', 0)
    report.data_rows = accounting['data_rows']
    report.practice = accounting['practice']
    report.rejected = dict(accounting['rejected'])

    # Within-file sequence for rows with identical hash inputs (plan step 5).
    _assign_seq(trades, broker)

    if report.data_rows == 0:
        logger.warning("No data rows found in the file.")
        return report

    logger.info(f"Found {len(trades)} trades to import.")

    if dry_run:
        # Nothing is written (no BrokerStatement, no FileImportLog, no file
        # moves); the duplicate check against the DB is read-only, and a
        # hash already seen in an earlier file of this same run also counts
        # as a duplicate (review fix 3).
        _dry_run_report(report, trades, broker, seen_hashes)
        # Same row-accounting check as the real run (review fix 3): a
        # mismatch is reported as a failed file.
        mismatch = _accounting_mismatch_message(report, filename)
        if mismatch:
            report.failed = True
            report.fail_reason = mismatch
        logger.info("Dry run complete. No changes made to the database.")
        return report

    # Set up duplicate logger — writes CSV with line numbers
    _setup_duplicate_logger()
    dup_logger = logging.getLogger('imports.duplicates')

    # Import to DB
    imported_count = 0
    duplicate_count = 0
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

                # Defensive: the RBC parser already rejects PRAC* rows.
                if raw_account_id.startswith('PRACEF'):
                    report.practice += 1
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
                        report.rejected['Unknown account'] = (
                            report.rejected.get('Unknown account', 0) + 1
                        )
                        continue
                else:
                    account = None
                    report.rejected['Missing account'] = (
                        report.rejected.get('Missing account', 0) + 1
                    )
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
                    settlement_date=trade.get('settlement_date'),
                    seq=trade.get('seq', 1),
                ))

                # Check for duplicate by tx_hash
                if Transaction.objects.filter(tx_hash=tx_hash).exists():
                    duplicate_count += 1
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

            # Fill the statement period from the file's transaction dates
            # (architecture §6; import-checks plan step 13b).
            trade_dates = [t['date'] for t in trades if t.get('date')]
            if trade_dates:
                statement.period_start = min(trade_dates)
                statement.period_end = max(trade_dates)
                statement.save(update_fields=['period_start', 'period_end'])

            # Row accounting must add up (plan decision 4): a mismatch is a
            # parser bug — fail the whole file atomically, store nothing.
            report.imported = imported_count
            report.duplicates = duplicate_count
            mismatch = _accounting_mismatch_message(report, filename)
            if mismatch:
                raise _AccountingMismatch(mismatch)

            # Save file import log
            FileImportLog.objects.create(
                filename=os.path.basename(file_path),
                broker=broker,
                records_imported=imported_count,
                records_skipped=duplicate_count,
                status='success' if imported_count > 0 else 'partial',
                content_hash=content_hash,
            )

            logger.info(f"Successfully imported {imported_count} transactions ({duplicate_count} duplicates skipped).")

            # Roll back the BrokerStatement if nothing was actually imported
            if imported_count == 0:
                statement.delete()

    except _AccountingMismatch as e:
        # The transaction above rolled back — nothing is stored for this
        # file. Record the failure and let the run continue with the next.
        FileImportLog.objects.create(
            filename=filename,
            broker=broker,
            records_imported=0,
            records_skipped=0,
            status='error',
            error_message=e.message,
            content_hash=content_hash,
        )
        report.failed = True
        report.fail_reason = e.message
    except Exception as e:
        # Any other exception also fails the file (review fix 2): the
        # transaction above rolled back, so nothing is stored. The reason is
        # scrubbed before it is reported or logged (the trade dict can carry
        # the Account column value), a FileImportLog error row is written,
        # and the run continues with the next file.
        try:
            fail_reason = scrub_text(str(e), name_map)[0]
        except Exception:
            fail_reason = 'Import failed (see server log)'
        logger.error("Import failed: %s", fail_reason)
        FileImportLog.objects.create(
            filename=filename,
            broker=broker,
            records_imported=0,
            records_skipped=0,
            status='error',
            error_message=fail_reason,
            content_hash=content_hash,
        )
        report.failed = True
        report.fail_reason = fail_reason
        # The report shows 0 imported / 0 duplicates and nothing goes to the
        # CSV_Activity_Imported tracking file.
        imported_count = 0
        duplicate_count = 0
        imported_trades.clear()

    report.imported = imported_count
    report.duplicates = duplicate_count

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

    return report


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print("Usage: python auto_import.py <file_path> <broker>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    broker = sys.argv[2]
    dry_run = '--dry-run' in sys.argv
    
    auto_import(file_path, broker, dry_run)
