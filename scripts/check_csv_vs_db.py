"""Check that every activity line in [PERSON_NAME] exists in core_transaction.

Writes missing lines to docs/reconciliation/missing_transactions.csv.

Usage:
    python scripts/check_csv_vs_db.py
"""

import csv
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
import django
django.setup()

from core.models import Transaction, Account

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())

CSV_PATH = Path('docs/reconciliation/CSV_Activity_detail.csv')
OUTPUT_PATH = Path('docs/reconciliation/missing_transactions.csv')


def parse_date(date_str):
    for fmt in ('%b %d, %Y', '%B %d, %Y', '%d-%b-%y'):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_decimal(val):
    try:
        return Decimal(val.replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return Decimal('0')


def check_transactions():
    if not CSV_PATH.exists():
        print(f"CSV file not found: {CSV_PATH}")
        return

    # Build account_id -> DB PK mapping
    acct_map = {str(a.id): a.account_id for a in Account.objects.all()}
    acct_map_reverse = {v: k for k, v in acct_map.items()}

    # Load transactions with original amount and currency
    transactions = list(Transaction.objects.all().values_list(
        'date', 'account_id', 'symbol', 'amount', 'currency', 'type'
    ))

    # Build matching set: (date, account_number, symbol, abs_amount, currency)
    db_keys = set()
    for date, account_pk, symbol, amount, currency, tx_type in transactions:
        symbol_norm = symbol.upper() if symbol else ''
        amount_abs = abs(amount)
        acct_number = acct_map.get(str(account_pk), str(account_pk))
        db_keys.add((date, acct_number, symbol_norm, amount_abs, currency))
        if symbol_norm:
            db_keys.add((date, acct_number, symbol_norm[:5], amount_abs, currency))

    print(f"Loaded {len(transactions)} transactions from database")

    missing_rows = []
    total_rows = 0
    matched_rows = 0

    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)

        for row in reader:
            total_rows += 1

            date = parse_date(row.get('date', ''))
            account = row.get('account', '').strip()
            symbol = row.get('symbol', '').strip().upper()
            currency = row.get('currency', '').strip().upper()
            value = parse_decimal(row.get('value', '0'))
            value_abs = abs(value)

            if not date or not account:
                continue

            # Skip practice account — intentionally not imported
            if account == 'PRACEF8C2DAE':
                continue

            matched = False

            # Try exact match
            if (date, account, symbol, value_abs, currency) in db_keys:
                matched = True
            elif symbol and (date, account, symbol[:5], value_abs, currency) in db_keys:
                matched = True
            else:
                # Fuzzy match: same date, account, symbol, currency, amount within $1
                for db_date, db_acct, db_sym, db_amt, db_cur in db_keys:
                    if (db_date == date and
                        db_acct == account and
                        db_cur == currency and
                        abs(db_amt - value_abs) < 1.00):
                        matched = True
                        break

            if matched:
                matched_rows += 1
            else:
                missing_rows.append(row)

    if missing_rows:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=missing_rows[0].keys())
            writer.writeheader()
            writer.writerows(missing_rows)

        print(f"\nResults:")
        print(f"Total CSV rows: {total_rows}")
        print(f"Matched: {matched_rows}")
        print(f"Missing: {len(missing_rows)}")
        print(f"Missing transactions written to: {OUTPUT_PATH}")
    else:
        print(f"\nAll {matched_rows}/{total_rows} CSV rows matched database transactions")


if __name__ == '__main__':
    check_transactions()
