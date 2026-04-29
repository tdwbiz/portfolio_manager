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


def write_rejection_log(rejections: List[Dict[str, Any]], dest_dir: str,
                        source_filename: str) -> str | None:
    """Write *rejections* to a timestamped CSV file in *dest_dir*.

    Returns the path to the written file, or None if rejections is empty.
    """
    if not rejections:
        return None

    os.makedirs(dest_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(source_filename))[0]
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(dest_dir, f"{name}_{timestamp}_rejected.csv")

    with open(log_path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rejections)

    return log_path
