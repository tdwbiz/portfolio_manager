"""CSV parsers for IB and RBC broker statement imports."""

import csv
import logging
import os
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type normalisation helpers
# ---------------------------------------------------------------------------

# Canonical codes used across both brokers:
#   BUY, SELL, DIV, DIST, INT, TRANSFER, DEP, WDRAW, TAX, WITHTAX, FEE, OTHER

# Maps raw broker type strings → (canonical_code, display_label)
_IB_TYPE_MAP: Dict[str, Tuple[str, str]] = {
    # Trades
    'Buy':                              ('BUY',      'Buy'),
    'Sell':                             ('SELL',     'Sell'),
    # Income
    'Dividend':                         ('DIV',      'Dividend'),
    'Dividends':                        ('DIV',      'Dividend'),
    'Distribution':                     ('DIST',     'Distribution'),
    'Interest':                         ('INT',      'Interest'),
    # Cash movements
    'Deposit':                          ('DEP',      'Deposit'),
    'Deposits':                         ('DEP',      'Deposit'),
    'Deposits & Withdrawals':           ('DEP',      'Deposit'),
    'Withdrawal':                       ('WDRAW',    'Withdrawal'),
    'Withdrawals':                      ('WDRAW',    'Withdrawal'),
    'Wire Transfer':                    ('WDRAW',    'Withdrawal'),
    # Transfers
    'Transfer':                         ('TRANSFER', 'Transfer'),
    'Transfers':                        ('TRANSFER', 'Transfer'),
    # Fees & tax
    'Fee':                              ('FEE',      'Fee'),
    'Commission':                       ('FEE',      'Fee'),
    'Sales Tax':                        ('TAX',      'Tax'),
    'Tax':                              ('TAX',      'Tax'),
    'Foreign Tax Withholding':          ('WITHTAX',  'Foreign Tax Withholding'),
    'W/H Tax':                          ('WITHTAX',  'Foreign Tax Withholding'),
    'Withholding Tax':                  ('WITHTAX',  'Foreign Tax Withholding'),
    # Catch-all
    'Other':                            ('OTHER',    'Other'),
}

_RBC_TYPE_MAP: Dict[str, Tuple[str, str]] = {
    # Trades
    'Buy':                              ('BUY',      'Buy'),
    'Sell':                             ('SELL',     'Sell'),
    # Income
    'Dividends':                        ('DIV',      'Dividend'),
    'Distribution':                     ('DIST',     'Distribution'),
    'Interest':                         ('INT',      'Interest'),
    # Cash movements
    'Deposits & Contributions':         ('DEP',      'Deposit'),
    'Withdrawals & De-registrations':   ('WDRAW',    'Withdrawal'),
    'Withdrawal':                       ('WDRAW',    'Withdrawal'),
    'WDRAW':                            ('WDRAW',    'Withdrawal'),
    # Transfers
    'Transfers':                        ('TRANSFER', 'Transfer'),
    'Transfer':                         ('TRANSFER', 'Transfer'),
    # Fees & tax
    'Fees':                             ('FEE',      'Fee'),
    'Commission':                       ('FEE',      'Fee'),
    'Taxes':                            ('TAX',      'Tax'),
    'Tax':                              ('TAX',      'Tax'),
    'Foreign Tax Withholding':          ('WITHTAX',  'Foreign Tax Withholding'),
    # Catch-all
    'Others':                           ('OTHER',    'Other'),
    'Other':                            ('OTHER',    'Other'),
}


def _normalize_type(raw: str, type_map: Dict[str, Tuple[str, str]]) -> Tuple[str, str]:
    """Return (code, label) for a raw broker type string.

    Falls back to a sanitised uppercase code for any unknown type so that
    future broker inventions are stored without requiring a code change.
    """
    if raw in type_map:
        return type_map[raw]
    code = re.sub(r'[^A-Z0-9]', '_', raw.upper())[:20].strip('_') or 'OTHER'
    return (code, raw)


def parse_ib_csv(file_path: str, rejections: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Parse Interactive Brokers CSV — supports two formats:

    1. Activity Statement (multi-section): first column is the section name
       e.g. "Trades", "Dividends", with a header row then data rows.
    2. Transaction History (flat export): columns are
       Section, Row-type, Date, Account, Description, Type, Symbol,
       Quantity, Price, Currency, Gross Amount, Commission, Net Amount.

    All row types are imported (Buy, Sell, Dividend, Fee, Tax, Transfer, etc.).

    Args:
        file_path:   Path to the IB CSV file.
        rejections:  Optional list; skipped rows are appended as dicts.

    Returns:
        List of dictionaries with parsed trade data.
    """
    if rejections is None:
        rejections = []  # caller didn't ask for them — discard silently
    trades: List[Dict[str, Any]] = []

    try:
        with open(file_path, 'r', encoding='utf-8-sig') as csvfile:
            reader = csv.reader(csvfile)
            rows = list(reader)

        if not rows:
            logger.info("Parsed 0 trades from IB CSV")
            return trades

        # --- Detect format ---
        # Transaction History format: first cell of first non-empty row is a
        # section label (e.g. "Statement") and second cell is "Header" or "Data".
        first_row = next((r for r in rows if r and any(c.strip() for c in r)), None)
        is_transaction_history = (
            first_row is not None
            and len(first_row) >= 2
            and first_row[1].strip().lower() in ('header', 'data')
        )

        if is_transaction_history:
            trades = _parse_ib_transaction_history(rows, rejections, file_path)
        else:
            trades = _parse_ib_activity_statement(rows, rejections, file_path)

    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")
    except PermissionError:
        raise PermissionError(f"Permission denied: {file_path}")

    logger.info(f"Parsed {len(trades)} trades from IB CSV")
    return trades


def _parse_ib_transaction_history(rows: List[List[str]],
                                   rejections: List[Dict[str, Any]],
                                   filename: str = '') -> List[Dict[str, Any]]:
    """Parse IB Transaction History CSV format.

    Columns (0-based):
      0  Section ("Transaction History")
      1  Row type ("Header" / "Data")
      2  Date (YYYY-MM-DD)
      3  Account
      4  Description / notes
      5  Transaction Type (Buy / Sell / Dividend / Fee / Tax / Transfer / …)
      6  Symbol
      7  Quantity
      8  Price
      9  Price Currency
      10 Gross Amount
      11 Commission
      12 Net Amount

    All row types are imported.
    """
    trades: List[Dict[str, Any]] = []

    for row_num, row in enumerate(rows, start=1):
        if len(row) < 13:
            continue
        if row[0].strip() != 'Transaction History' or row[1].strip() != 'Data':
            continue

        tx_type_raw = row[5].strip()
        if not tx_type_raw:
            rejections.append({
                'Filename': filename, 'Row Number': row_num,
                'Reason Rejected': 'Missing transaction type',
                'Date': row[2].strip(), 'Type': '', 'Symbol': row[6].strip() if len(row) > 6 else '',
                'Quantity': '', 'Price': '', 'Amount': '',
                'Currency': row[9].strip() if len(row) > 9 else '', 'Account ID': row[3].strip(), 'Notes': row[4].strip() if len(row) > 4 else '',
            })
            continue

        try:
            date_str = row[2].strip()
            trade_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            logger.warning(f"Row {row_num}: Could not parse date '{row[2].strip()}'")
            rejections.append({
                'Filename': filename, 'Row Number': row_num,
                'Reason Rejected': f"Invalid date: {row[2].strip()!r}",
                'Date': row[2].strip(), 'Type': row[5].strip(), 'Symbol': row[6].strip() if len(row) > 6 else '',
                'Quantity': row[7].strip() if len(row) > 7 else '', 'Price': row[8].strip() if len(row) > 8 else '',
                'Amount': row[10].strip() if len(row) > 10 else '',
                'Currency': row[9].strip() if len(row) > 9 else '', 'Account ID': row[3].strip(), 'Notes': row[4].strip() if len(row) > 4 else '',
            })
            continue

        account_id_raw = row[3].strip().upper()
        if account_id_raw.startswith('PRAC'):
            logger.debug(f"Row {row_num}: Skipping practice account '{account_id_raw}'")
            rejections.append({
                'Filename': filename, 'Row Number': row_num,
                'Reason Rejected': f"Practice account: {account_id_raw}",
                'Date': str(trade_date), 'Type': row[5].strip(), 'Symbol': row[6].strip() if len(row) > 6 else '',
                'Quantity': row[7].strip() if len(row) > 7 else '', 'Price': row[8].strip() if len(row) > 8 else '',
                'Amount': row[10].strip() if len(row) > 10 else '',
                'Currency': row[9].strip() if len(row) > 9 else '', 'Account ID': account_id_raw, 'Notes': row[4].strip() if len(row) > 4 else '',
            })
            continue

        type_code, type_label = _normalize_type(tx_type_raw, _IB_TYPE_MAP)

        symbol = row[6].strip()

        def _safe_decimal(val: str, default: Decimal = Decimal('0')) -> Decimal:
            v = val.strip().replace(',', '')
            if not v or v in ('-', '--', 'N/A'):
                return default
            try:
                return Decimal(v)
            except Exception:
                return default

        quantity = abs(_safe_decimal(row[7]))
        price    = _safe_decimal(row[8])
        currency = row[9].strip() if row[9].strip() not in ('', '-') else 'USD'
        amount   = abs(_safe_decimal(row[10]))
        commission = abs(_safe_decimal(row[11]))
        notes    = row[4].strip()

        trades.append({
            'date':              trade_date,
            'symbol':            symbol,
            'type':              type_code,
            'type_label':        type_label,
            'quantity':          quantity,
            'price':             price,
            'commission':        commission,
            'amount':            amount,
            'currency':          currency,
            'exchange_rate_cad': Decimal('1.000000'),
            'account_id':        account_id_raw,
            'notes':             notes,
        })

    return trades


def _parse_ib_activity_statement(rows: List[List[str]],
                                  rejections: List[Dict[str, Any]],
                                  filename: str = '') -> List[Dict[str, Any]]:
    """Parse IB Activity Statement CSV format (multi-section with "Trades" header)."""
    trades: List[Dict[str, Any]] = []
    in_trades_section: bool = False
    header: List[str] = []
    account_idx: int = None

    for row_num, row in enumerate(rows, start=1):
        if not row or all(cell.strip() == '' for cell in row):
            continue

        row_text = row[0].strip().lower()

        if row_text == 'trades':
            in_trades_section = True
            continue

        if in_trades_section:
            if len(row) > 0 and row[0].strip():
                first_cell = row[0].strip().lower()
                if first_cell in ['dividends', 'fx', 'interest', 'journal',
                                   'funds', 'misc', 'closed positions',
                                   'activity summary', 'account']:
                    break
                if any(col in first_cell for col in ['date/time', 'symbol',
                                                      'buy/sell', 'quantity',
                                                      't. price']):
                    header = [cell.strip().lower().replace('/', '_') for cell in row]
                    # Check for Account column
                    if 'account' in header:
                        account_idx = header.index('account')
                    continue

            if header and len(row) >= len(header):
                try:
                    if len(row) >= 8:
                        date_str = row[0].strip()
                        try:
                            dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                            trade_date = dt.date()
                        except ValueError:
                            try:
                                dt = datetime.strptime(date_str, '%m/%d/%Y %H:%M:%S')
                                trade_date = dt.date()
                            except ValueError:
                                logger.warning(f"Row {row_num}: Could not parse date '{date_str}'")
                                rejections.append({
                                    'Filename': filename, 'Row Number': row_num,
                                    'Reason Rejected': f'Invalid date: {date_str!r}',
                                    'Date': date_str, 'Type': row[2].strip() if len(row) > 2 else '',
                                    'Symbol': row[1].strip() if len(row) > 1 else '',
                                    'Quantity': row[3].strip() if len(row) > 3 else '',
                                    'Price': row[4].strip() if len(row) > 4 else '',
                                    'Amount': row[6].strip() if len(row) > 6 else '',
                                    'Currency': row[7].strip() if len(row) > 7 else '',
                                    'Account ID': '', 'Notes': '',
                                })
                                continue

                        symbol = row[1].strip()
                        buy_sell = row[2].strip().upper()
                        type_code, type_label = _normalize_type(buy_sell, _IB_TYPE_MAP)

                        try:
                            quantity = Decimal(row[3].strip())
                        except Exception:
                            logger.warning(f"Row {row_num}: Could not parse quantity")
                            rejections.append({
                                'Filename': filename, 'Row Number': row_num,
                                'Reason Rejected': f'Invalid quantity: {row[3].strip()!r}',
                                'Date': str(date_str), 'Type': buy_sell, 'Symbol': row[1].strip(),
                                'Quantity': row[3].strip(), 'Price': row[4].strip() if len(row) > 4 else '',
                                'Amount': row[6].strip() if len(row) > 6 else '',
                                'Currency': row[7].strip() if len(row) > 7 else '',
                                'Account ID': '', 'Notes': '',
                            })
                            continue

                        try:
                            price = Decimal(row[4].strip())
                        except Exception:
                            logger.warning(f"Row {row_num}: Could not parse price")
                            rejections.append({
                                'Filename': filename, 'Row Number': row_num,
                                'Reason Rejected': f'Invalid price: {row[4].strip()!r}',
                                'Date': str(date_str), 'Type': buy_sell, 'Symbol': row[1].strip(),
                                'Quantity': str(quantity), 'Price': row[4].strip(),
                                'Amount': row[6].strip() if len(row) > 6 else '',
                                'Currency': row[7].strip() if len(row) > 7 else '',
                                'Account ID': '', 'Notes': '',
                            })
                            continue

                        try:
                            commission = Decimal(row[5].strip())
                        except Exception:
                            commission = Decimal('0.00')

                        try:
                            amount = Decimal(row[6].strip())
                        except Exception:
                            logger.warning(f"Row {row_num}: Could not parse proceeds")
                            rejections.append({
                                'Filename': filename, 'Row Number': row_num,
                                'Reason Rejected': f'Invalid amount: {row[6].strip()!r}',
                                'Date': str(date_str), 'Type': buy_sell, 'Symbol': row[1].strip(),
                                'Quantity': str(quantity), 'Price': str(price),
                                'Amount': row[6].strip(),
                                'Currency': row[7].strip() if len(row) > 7 else '',
                                'Account ID': '', 'Notes': '',
                            })
                            continue

                        currency = row[7].strip() if len(row) > 7 else 'USD'
                        
                        account_id = ''
                        if account_idx is not None and len(row) > account_idx:
                            account_id = row[account_idx].strip().upper()

                        trades.append({
                            'date':              trade_date,
                            'symbol':            symbol,
                            'type':              type_code,
                            'type_label':        type_label,
                            'quantity':          quantity,
                            'price':             price,
                            'commission':        commission,
                            'amount':            amount,
                            'currency':          currency,
                            'exchange_rate_cad': Decimal('1.000000'),
                            'account_id':        account_id,
                            'notes':             '',
                        })
                except Exception as e:
                    logger.error(f"Row {row_num}: Error parsing trade data: {e}")
                    rejections.append({
                        'Filename': filename, 'Row Number': row_num,
                        'Reason Rejected': f'Parse error: {e}',
                        'Date': row[0].strip() if row else '', 'Type': '', 'Symbol': '',
                        'Quantity': '', 'Price': '', 'Amount': '', 'Currency': '',
                        'Account ID': '', 'Notes': '',
                    })
                    continue

    return trades


def parse_rbc_csv(file_path: str,
                  rejections: List[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Decimal]:
    """Parse RBC Activity Export CSV.

    Handles the real RBC format: quoted CSV with a metadata header block,
    an exchange rate line ("Exchange Rate: 1 USD = X.XXXX CAD"), a data
    header row, then trade data rows.

    All activity types are imported (Buy, Sell, Dividends, Distribution,
    Interest, Transfers, Deposits & Contributions, Taxes, etc.).

    Args:
        file_path: Path to the RBC CSV file.

    Returns:
        Tuple of (trades_list, usd_to_cad_rate).
        Rate is Decimal('1.0') if not found in file.
    """

    if rejections is None:
        rejections = []
    trades: List[Dict[str, Any]] = []
    usd_to_cad_rate: Decimal = Decimal('1.0')
    found_header: bool = False
    filename = os.path.basename(file_path)

    try:
        with open(file_path, 'r', encoding='utf-8-sig') as csvfile:
            reader = csv.reader(csvfile)

            for row_num, row in enumerate(reader, start=1):
                if not row or all(cell.strip() == '' for cell in row):
                    continue

                # Extract exchange rate before anything else
                exchange_rate_match = re.search(
                    r'Exchange Rate:\s*1\s*USD\s*=\s*([\d.]+)\s*CAD',
                    ' '.join(row),
                )
                if exchange_rate_match:
                    usd_to_cad_rate = Decimal(exchange_rate_match.group(1))
                    continue

                if not found_header:
                    # Detect data header row by first column value
                    if row[0].strip().strip('"') == 'Date':
                        found_header = True
                    continue

                # Parse data rows — require at least 9 columns (up to Value)
                if len(row) < 9:
                    rejections.append({
                        'Filename': filename, 'Row Number': row_num,
                        'Reason Rejected': f'Too few columns ({len(row)})',
                        'Date': cleaned[0] if cleaned else '', 'Type': '', 'Symbol': '',
                        'Quantity': '', 'Price': '', 'Amount': '', 'Currency': '',
                        'Account ID': '', 'Notes': '',
                    })
                    continue

                cleaned = [cell.strip().strip('"') for cell in row]

                activity = cleaned[1]
                if not activity:
                    rejections.append({
                        'Filename': filename, 'Row Number': row_num,
                        'Reason Rejected': 'Missing activity type',
                        'Date': cleaned[0], 'Type': '', 'Symbol': cleaned[2] if len(cleaned) > 2 else '',
                        'Quantity': '', 'Price': '', 'Amount': cleaned[8] if len(cleaned) > 8 else '',
                        'Currency': cleaned[9] if len(cleaned) > 9 else '', 'Account ID': cleaned[7] if len(cleaned) > 7 else '', 'Notes': '',
                    })
                    continue

                type_code, type_label = _normalize_type(activity, _RBC_TYPE_MAP)

                try:
                    trade_date = datetime.strptime(cleaned[0], '%B %d, %Y').date()
                except ValueError:
                    logger.warning(f"Row {row_num}: Could not parse date '{cleaned[0]}'")
                    rejections.append({
                        'Filename': filename, 'Row Number': row_num,
                        'Reason Rejected': f"Invalid date: {cleaned[0]!r}",
                        'Date': cleaned[0], 'Type': activity, 'Symbol': cleaned[2] if len(cleaned) > 2 else '',
                        'Quantity': cleaned[4] if len(cleaned) > 4 else '', 'Price': cleaned[5] if len(cleaned) > 5 else '',
                        'Amount': cleaned[8] if len(cleaned) > 8 else '',
                        'Currency': cleaned[9] if len(cleaned) > 9 else '', 'Account ID': cleaned[7] if len(cleaned) > 7 else '', 'Notes': '',
                    })
                    continue

                def _to_decimal(val: str) -> Decimal:
                    """Strip commas and treat dash/empty as zero."""
                    v = val.replace(',', '').strip()
                    if v in ('', '-', '--', 'N/A'):
                        return Decimal('0')
                    return Decimal(v)

                try:
                    quantity = abs(_to_decimal(cleaned[4]))
                    price    = _to_decimal(cleaned[5])
                    amount   = _to_decimal(cleaned[8])  # signed — negative = outflow
                except Exception:
                    logger.warning(
                        f"Row {row_num}: Could not parse numeric fields "
                        f"(qty={cleaned[4]!r}, price={cleaned[5]!r}, value={cleaned[8]!r}), skipping"
                    )
                    rejections.append({
                        'Filename': filename, 'Row Number': row_num,
                        'Reason Rejected': f'Invalid numeric fields (qty={cleaned[4]!r}, price={cleaned[5]!r}, amount={cleaned[8]!r})',
                        'Date': str(trade_date), 'Type': activity, 'Symbol': cleaned[2] if len(cleaned) > 2 else '',
                        'Quantity': cleaned[4], 'Price': cleaned[5], 'Amount': cleaned[8],
                        'Currency': cleaned[9] if len(cleaned) > 9 else '', 'Account ID': cleaned[7] if len(cleaned) > 7 else '', 'Notes': cleaned[10] if len(cleaned) > 10 else '',
                    })
                    continue

                # Commission only meaningful for trade types
                if type_code in ('BUY', 'SELL') and quantity > 0 and price > 0:
                    gross = quantity * price
                    commission = abs(gross - abs(amount)).quantize(Decimal('0.01'))
                    if commission > 50:   # sanity cap — avg price discrepancy can look large
                        commission = Decimal('0.00')
                else:
                    commission = Decimal('0.00')

                currency   = cleaned[9] if len(cleaned) > 9 else 'USD'
                symbol     = cleaned[2]
                notes      = cleaned[10] if len(cleaned) > 10 else ''

                # Capture Account column (index 7) — skip practice accounts
                account_id = cleaned[7].strip().upper() if len(cleaned) > 7 else ''
                if account_id.startswith('PRAC'):
                    logger.debug(f"Row {row_num}: Skipping practice account '{account_id}'")
                    rejections.append({
                        'Filename': filename, 'Row Number': row_num,
                        'Reason Rejected': f'Practice account: {account_id}',
                        'Date': str(trade_date), 'Type': activity, 'Symbol': symbol,
                        'Quantity': str(quantity), 'Price': str(price), 'Amount': str(amount),
                        'Currency': currency, 'Account ID': account_id, 'Notes': notes,
                    })
                    continue

                trades.append({
                    'date':              trade_date,
                    'symbol':            symbol,
                    'type':              type_code,
                    'type_label':        type_label,
                    'quantity':          quantity,
                    'price':             price,
                    'commission':        commission,
                    'amount':            amount,
                    'currency':          currency,
                    'exchange_rate_cad': usd_to_cad_rate,
                    'account_id':        account_id,
                    'notes':             notes,
                })

    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")
    except PermissionError:
        raise PermissionError(f"Permission denied: {file_path}")

    logger.info(f"Parsed {len(trades)} trades from RBC CSV")
    return trades, usd_to_cad_rate
