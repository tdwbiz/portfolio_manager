"""CSV parsers for IB and RBC broker statement imports."""

import csv
import logging
import os
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type normalisation helpers
# ---------------------------------------------------------------------------

# Canonical codes used across both brokers:
#   BUY, SELL, DIV, DIST, INT, TRANSFER, DEP, WDRAW, TAX, WITHTAX, FEE, OTHER

# Maps raw broker type strings → (canonical_code, display_label)
_IB_TYPE_MAP: dict[str, tuple[str, str]] = {
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

_RBC_TYPE_MAP: dict[str, tuple[str, str]] = {
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
    # Corporate actions (dry-run fix B): codes are the sanitised uppercase
    # forms the old auto-create produced, so the TransactionType rows
    # already in the database are reused.
    'Reorganization':                   ('REORGANIZATION', 'Reorganization'),
    'Return of Capital':                ('RETURN_OF_CAPITAL', 'Return of Capital'),
    'Adjustment':                       ('ADJUSTMENT', 'Adjustment'),
    # Catch-all
    'Others':                           ('OTHER',    'Other'),
    'Other':                            ('OTHER',    'Other'),
}


def _normalize_type(raw: str, type_map: dict[str, tuple[str, str]],
                    auto_create: bool = True) -> tuple[str | None, str | None]:
    """Return (code, label) for a raw broker type string.

    When ``auto_create`` is true (IB path), unknown types fall back to a
    sanitised uppercase code and the TransactionType registry auto-creates
    them, so future broker inventions are stored without a code change.

    When ``auto_create`` is false (RBC path), an activity string not in
    ``type_map`` returns ``(None, None)`` — the caller rejects the row
    instead of inventing a type (plan step 4).
    """
    from core.models import TransactionType

    if raw in type_map:
        code, label = type_map[raw]
        if auto_create:
            TransactionType.auto_create_or_get(code, label)
        return (code, label)

    # Case-insensitive fallback (e.g. 'buy' → 'Buy').
    key = raw.strip().lower()
    for map_key, mapped in type_map.items():
        if map_key.lower() == key:
            code, label = mapped
            if auto_create:
                TransactionType.auto_create_or_get(code, label)
            return (code, label)

    if not auto_create:
        return (None, None)

    code = re.sub(r'[^A-Z0-9]', '_', raw.upper())[:20].strip('_') or 'OTHER'
    label = raw
    TransactionType.auto_create_or_get(code, label)
    return (code, label)


def parse_ib_csv(file_path: str, rejections: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
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
    trades: list[dict[str, Any]] = []

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


def _parse_ib_transaction_history(rows: list[list[str]],
                                   rejections: list[dict[str, Any]],
                                   filename: str = '') -> list[dict[str, Any]]:
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
    trades: list[dict[str, Any]] = []

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


def _parse_ib_activity_statement(rows: list[list[str]],
                                  rejections: list[dict[str, Any]],
                                  filename: str = '') -> list[dict[str, Any]]:
    """Parse IB Activity Statement CSV format (multi-section with "Trades" header)."""
    trades: list[dict[str, Any]] = []
    in_trades_section: bool = False
    header: list[str] = []
    account_idx: int | None = None

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


# ---------------------------------------------------------------------------
# RBC CSV parser
# ---------------------------------------------------------------------------

# Header names (lower-case) recognised in RBC Activity exports, mapped to the
# internal field name. Columns are found by header name, never by position.
# Both real layouts share the nine required columns; the new layout adds
# "Symbol Description" (ignored, not stored) and "Description" (notes).
_RBC_COLUMN_NAMES: dict[str, str | None] = {
    'date':               'date',
    'activity':           'activity',
    'symbol':             'symbol',
    'symbol description': None,       # known but ignored (not stored)
    'quantity':           'quantity',
    'price':              'price',
    'settlement date':    'settlement',
    'account':            'account',
    'value':              'value',
    'currency':           'currency',
    'description':        'notes',
    'notes':              'notes',    # legacy alias for the notes column
}
_RBC_REQUIRED_COLUMNS = (
    'date', 'activity', 'symbol', 'quantity', 'price',
    'settlement', 'account', 'value', 'currency',
)

# Date formats seen in RBC Activity exports (both layouts):
#   31-Dec-18, 31-Dec-2018, Dec 31, 2018, December 31, 2018
_CSV_DATE_FORMATS = ('%d-%b-%y', '%d-%b-%Y', '%b %d, %Y', '%B %d, %Y')


def parse_csv_date(value: str):
    """Parse a CSV date cell into a ``datetime.date``, or None if unreadable.

    Handles the formats seen in RBC Activity exports (``31-Dec-18``,
    ``31-Dec-2018``, ``Dec 31, 2018``, ``December 31, 2018``); surrounding
    quotes are stripped. Used for both the Date and Settlement Date columns.
    """
    v = (value or '').strip().strip('"').strip()
    if not v:
        return None
    for fmt in _CSV_DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def is_known_account(account_id: str) -> bool:
    """True if the row's account number matches a stored Account fingerprint.

    The full number is normalised and hashed on the fly; it is never stored.
    Raises ``ValueError`` if ACCOUNT_FINGERPRINT_KEY is not set (fail loudly).
    """
    from core.models import Account
    from core.utils.account_fingerprint import fingerprint_account_number

    return Account.objects.filter(
        fingerprint=fingerprint_account_number(account_id)
    ).exists()

def parse_rbc_csv(file_path: str,
                  rejections: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], Decimal]:
    """Parse RBC Activity Export CSV.

    Handles the real RBC format: quoted CSV with a metadata header block,
    an exchange rate line ("Exchange Rate: 1 USD = X.XXXX CAD"), a data
    header row, then trade data rows.

    All activity types are imported (Buy, Sell, Dividends, Distribution,
    Interest, Transfers, Deposits & Contributions, Taxes, etc.).

    Args:
        file_path: Path to the RBC CSV file.

    Returns:
        Tuple of (trades_list, usd_to_cad_rate, accounting).
        Rate is Decimal('1.0') if not found in file.
        ``accounting`` counts every data row of the file:
        ``data_rows = imported + practice + sum(rejected.values())``
        where ``rejected`` maps rejection reason → count. Rows from the
        RBC disclaimer footer on (first cell 'Disclaimer'/'Disclaimers')
        are counted separately in ``footer`` — never data rows or
        rejections (dry-run fix A).
    """

    if rejections is None:
        rejections = []
    trades: list[dict[str, Any]] = []
    usd_to_cad_rate: Decimal = Decimal('1.0')
    found_header: bool = False
    in_footer: bool = False
    filename = os.path.basename(file_path)
    # Per-file row accounting (plan step 3): every data row is accounted for.
    # Footer rows (RBC disclaimer block) sit outside data_rows (fix A).
    accounting: dict[str, Any] = {
        'data_rows': 0, 'imported': 0, 'practice': 0, 'rejected': {},
        'footer': 0,
    }

    # Column mapping (will be set when header is found)
    col_map: dict[str, int] = {}

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
                        # Build column mapping from header — exact name match
                        # (case-insensitive, quotes stripped), never position.
                        # Unknown extra columns are ignored; a missing
                        # required column fails the file with a clear error.
                        header = [cell.strip().strip('"').lower() for cell in row]
                        col_map = {}
                        for i, col_name in enumerate(header):
                            field = _RBC_COLUMN_NAMES.get(col_name)
                            if field is not None:
                                col_map.setdefault(field, i)
                        missing = [c for c in _RBC_REQUIRED_COLUMNS
                                   if c not in col_map]
                        if missing:
                            raise ValueError(
                                "Missing required column(s) "
                                + ', '.join(missing)
                                + f" in header of {filename}"
                            )
                        continue

                # Skip pre-header metadata rows (account balances, totals, disclaimers)
                if not found_header:
                    continue

                cleaned = [cell.strip().strip('"') for cell in row]

                # RBC disclaimer footer (dry-run fix A): from the first line
                # whose first cell is 'Disclaimer'/'Disclaimers' to EOF the
                # rows are footer — counted separately, never data or
                # rejections.
                if in_footer or (cleaned and cleaned[0] in ('Disclaimer', 'Disclaimers')):
                    in_footer = True
                    accounting['footer'] += 1
                    continue

                # Every data row after the header is counted, whatever its fate.
                accounting['data_rows'] += 1

                def _cell(field: str) -> str:
                    """Value of a mapped column; '' when the row is short."""
                    idx = col_map.get(field)
                    if idx is None or idx >= len(cleaned):
                        return ''
                    return cleaned[idx]

                def _reject(reason: str, **fields) -> None:
                    """Record a rejected row and count it by reason."""
                    rejections.append({
                        'Filename': filename, 'Row Number': row_num,
                        'Reason Rejected': reason,
                        'Date': fields.get('date', ''), 'Type': fields.get('type', ''),
                        'Symbol': fields.get('symbol', ''), 'Quantity': fields.get('quantity', ''),
                        'Price': fields.get('price', ''), 'Amount': fields.get('amount', ''),
                        'Currency': fields.get('currency', ''),
                        'Account ID': fields.get('account_id', ''),
                        'Notes': fields.get('notes', ''),
                    })
                    accounting['rejected'][reason] = (
                        accounting['rejected'].get(reason, 0) + 1
                    )

                activity = _cell('activity')
                if not activity:
                    _reject('Missing activity type', date=_cell('date'), symbol=_cell('symbol'),
                            amount=_cell('value'), currency=_cell('currency'), account_id=_cell('account'))
                    continue

                # RBC path: never invent a TransactionType — an activity
                # string not in _RBC_TYPE_MAP rejects the row (plan step 4).
                type_code, type_label = _normalize_type(activity, _RBC_TYPE_MAP,
                                                        auto_create=False)
                if type_code is None:
                    logger.warning("Row %d: Unknown activity type '%s'", row_num, activity)
                    _reject('Unknown activity type: ' + activity, date=_cell('date'),
                            symbol=_cell('symbol'), quantity=_cell('quantity'), price=_cell('price'),
                            amount=_cell('value'), currency=_cell('currency'), account_id=_cell('account'))
                    continue

                trade_date = parse_csv_date(_cell('date'))
                if trade_date is None:
                    bad_date = _cell('date')
                    logger.warning("Row %d: Could not parse date '%s'", row_num, bad_date)
                    _reject('Invalid date: ' + repr(bad_date), date=bad_date, type=activity,
                            symbol=_cell('symbol'), quantity=_cell('quantity'), price=_cell('price'),
                            amount=_cell('value'), currency=_cell('currency'), account_id=_cell('account'))
                    continue

                # Settlement date is mandatory (plan decision 1): a blank or
                # unreadable cell rejects the row — it never becomes NULL.
                raw_sd = _cell('settlement').strip()
                if not raw_sd:
                    _reject('Missing settlement date', date=str(trade_date), type=activity,
                            symbol=_cell('symbol'), quantity=_cell('quantity'), price=_cell('price'),
                            amount=_cell('value'), currency=_cell('currency'), account_id=_cell('account'))
                    continue
                settlement_date = parse_csv_date(raw_sd)
                if settlement_date is None:
                    _reject('Invalid settlement date: ' + repr(raw_sd), date=str(trade_date), type=activity,
                            symbol=_cell('symbol'), quantity=_cell('quantity'), price=_cell('price'),
                            amount=_cell('value'), currency=_cell('currency'), account_id=_cell('account'))
                    continue

                def _to_decimal(val: str) -> Decimal:
                    """Strip commas and treat dash/empty as zero."""
                    v = val.replace(',', '').strip()
                    if v in ('', '-', '--', 'N/A'):
                        return Decimal('0')
                    return Decimal(v)

                try:
                    # Quantity is stored signed, exactly as the CSV gives it
                    # (dry-run fix C): SELL negative, transfers out negative,
                    # the old security in a merger negative, expiries with
                    # their printed sign.
                    quantity = _to_decimal(_cell('quantity'))
                    price    = _to_decimal(_cell('price'))
                    amount   = _to_decimal(_cell('value'))  # signed — negative = outflow
                except Exception:
                    bad_qty = _cell('quantity')
                    bad_price = _cell('price')
                    bad_amount = _cell('value')
                    logger.warning(
                        "Row %d: Could not parse numeric fields "
                        "(qty=%r, price=%r, value=%r), skipping",
                        row_num, bad_qty, bad_price, bad_amount
                    )
                    reason = ('Invalid numeric fields (qty=' + repr(bad_qty)
                               + ', price=' + repr(bad_price)
                               + ', amount=' + repr(bad_amount) + ')')
                    _reject(reason, date=str(trade_date), type=activity, symbol=_cell('symbol'),
                            quantity=bad_qty, price=bad_price, amount=bad_amount,
                            currency=_cell('currency'), account_id=_cell('account'), notes=_cell('notes'))
                    continue

                # Commission only meaningful for trade types (quantity is
                # signed — dry-run fix C — so use its magnitude here).
                qty_abs = abs(quantity)
                if type_code in ('BUY', 'SELL') and qty_abs > 0 and price > 0:
                    gross = qty_abs * price
                    commission = abs(gross - abs(amount)).quantize(Decimal('0.01'))
                    if commission > 50:   # sanity cap — avg price discrepancy can look large
                        commission = Decimal('0.00')
                else:
                    commission = Decimal('0.00')

                # A missing currency rejects the row (review fix 4) — it is
                # never silently defaulted to USD.
                currency = _cell('currency')
                if not currency:
                    _reject('Missing currency', date=str(trade_date), type=activity,
                            symbol=_cell('symbol'), quantity=str(quantity), price=str(price),
                            amount=str(amount), account_id=_cell('account'), notes=_cell('notes'))
                    continue
                symbol     = _cell('symbol')
                notes      = _cell('notes')

                # Capture Account column — skip practice accounts. The reason
                # never carries the raw number (it may be printed); the
                # 'Account ID' field keeps it for local log files only.
                account_id = _cell('account').strip().upper()
                if account_id.startswith('PRAC'):
                    logger.debug(f"Row {row_num}: Skipping practice account '{account_id}'")
                    accounting['practice'] += 1
                    rejections.append({
                        'Filename': filename, 'Row Number': row_num,
                        'Reason Rejected': 'Practice account',
                        'Date': str(trade_date), 'Type': activity, 'Symbol': symbol,
                        'Quantity': str(quantity), 'Price': str(price), 'Amount': str(amount),
                        'Currency': currency, 'Account ID': account_id, 'Notes': notes,
                    })
                    continue

                # Skip accounts whose number has no stored fingerprint
                if account_id and not is_known_account(account_id):
                    logger.debug(f"Row {row_num}: Skipping unknown account '{account_id}'")
                    _reject('Unknown account', date=str(trade_date), type=activity, symbol=symbol,
                            quantity=str(quantity), price=str(price), amount=str(amount),
                            currency=currency, account_id=account_id, notes=notes)
                    continue

                accounting['imported'] += 1
                trades.append({
                    'date':              trade_date,
                    'settlement_date':   settlement_date,
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
                    'row_number':        row_num,
                })

    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")
    except PermissionError:
        raise PermissionError(f"Permission denied: {file_path}")

    logger.info(f"Parsed {len(trades)} trades from RBC CSV")
    return trades, usd_to_cad_rate, accounting
