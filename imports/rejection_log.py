"""Shared rejection-log writer for broker import commands.

Each rejected / skipped row is recorded in a CSV file alongside the
processed statement so imports can be audited without opening a DB client.

CSV columns (always in this order):
    Filename, Row Number, Reason Rejected,
    Date, Type, Symbol, Quantity, Price, Amount, Currency, Account ID, Notes
"""

import csv
import os
from datetime import datetime
from typing import Any, Dict, List

FIELDNAMES = [
    'Filename',
    'Row Number',
    'Reason Rejected',
    'Date',
    'Type',
    'Symbol',
    'Quantity',
    'Price',
    'Amount',
    'Currency',
    'Account ID',
    'Notes',
]


def make_rejection(filename: str, row_number: int, reason: str,
                   date='', tx_type='', symbol='', quantity='',
                   price='', amount='', currency='', account_id='',
                   notes='') -> Dict[str, Any]:
    """Build a rejection-log dict from either parsed or raw fields."""
    return {
        'Filename':         filename,
        'Row Number':       row_number,
        'Reason Rejected':  reason,
        'Date':             date,
        'Type':             tx_type,
        'Symbol':           symbol,
        'Quantity':         quantity,
        'Price':            price,
        'Amount':           amount,
        'Currency':         currency,
        'Account ID':       account_id,
        'Notes':            notes,
    }


def rejection_from_trade(filename: str, row_number: int, reason: str,
                         trade: Dict[str, Any]) -> Dict[str, Any]:
    """Build a rejection-log dict from a fully-parsed trade dict."""
    return make_rejection(
        filename=filename,
        row_number=row_number,
        reason=reason,
        date=str(trade.get('date', '')),
        tx_type=trade.get('type', ''),
        symbol=trade.get('symbol', ''),
        quantity=str(trade.get('quantity', '')),
        price=str(trade.get('price', '')),
        amount=str(trade.get('amount', '')),
        currency=trade.get('currency', ''),
        account_id=trade.get('account_id', ''),
        notes=trade.get('notes', ''),
    )


def write_rejection_log(rejections: List[Dict[str, Any]], logs_dir: str,
                        source_filename: str) -> str | None:
    """Append *rejections* to the monthly rejection-log CSV in *logs_dir*.

    The file is named ``rejected_YYYY_MM.csv`` so one file covers each
    calendar month and files rotate automatically.  A header row is written
    only when the file is created for the first time.

    Returns the path to the log file, or None if rejections is empty.
    """
    if not rejections:
        return None

    os.makedirs(logs_dir, exist_ok=True)
    month_stamp = datetime.now().strftime('%Y_%m')
    log_path = os.path.join(logs_dir, f"rejected_{month_stamp}.csv")

    is_new = not os.path.exists(log_path) or os.path.getsize(log_path) == 0

    with open(log_path, 'a', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction='ignore')
        if is_new:
            writer.writeheader()
        writer.writerows(rejections)

    return log_path
