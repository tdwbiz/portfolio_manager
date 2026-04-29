"""CSV parsers for IB and RBC broker statement imports."""

import csv
import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type normalisation helpers
# ---------------------------------------------------------------------------

# Maps raw broker type strings → (code, label)
_IB_TYPE_MAP: Dict[str, Tuple[str, str]] = {
    'Buy':                       ('BUY',      'Buy'),
    'Sell':                      ('SELL',     'Sell'),
    'Dividend':                  ('DIV',      'Dividend'),
    'Dividends':                 ('DIV',      'Dividend'),
    'Fee':                       ('FEE',      'Fee'),
    'Transfer':                  ('TRANSFER', 'Transfer'),
    'Sales Tax':                 ('TAX',      'Sales Tax'),
    'Foreign Tax Withholding':   ('WITHTAX',  'Foreign Tax Withholding'),
    'Other':                     ('OTHER',    'Other'),
}

_RBC_TYPE_MAP: Dict[str, Tuple[str, str]] = {
    'Buy':                           ('BUY',      'Buy'),
    'Sell':                          ('SELL',     'Sell'),
    'Dividends':                     ('DIV',      'Dividend'),
    'Distribution':                  ('DIST',     'Distribution'),
    'Interest':                      ('INT',      'Interest'),
    'Transfers':                     ('TRANSFER', 'Transfer'),
    'Deposits & Contributions':      ('DEP',      'Deposit/Contribution'),
    'Withdrawals & De-registrations':('WDRAW',    'Withdrawal/De-registration'),
    'Taxes':                         ('TAX',      'Tax'),
    'Others':                        ('OTHER',    'Other'),
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


def parse_ib_csv(file_path: str) -> List[Dict[str, Any]]:
    """Parse Interactive Brokers CSV — supports two formats:

    1. Activity Statement (multi-section): first column is the section name
       e.g. "Trades", "Dividends", with a header row then data rows.
    2. Transaction History (flat export): columns are
       Section, Row-type, Date, Account, Description, Type, Symbol,
       Quantity, Price, Currency, Gross Amount, Commission, Net Amount.

    All row types are imported (Buy, Sell, Dividend, Fee, Tax, Transfer, etc.).

    Args:
        file_path: Path to the IB CSV file.

    Returns:
        List of dictionaries with parsed trade data.
    """
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
            trades = _parse_ib_transaction_history(rows)
        else:
            trades = _parse_ib_activity_statement(rows)

    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")
    except PermissionError:
        raise PermissionError(f"Permission denied: {file_path}")

    logger.info(f"Parsed {len(trades)} trades from IB CSV")
    return trades


def _parse_ib_transaction_history(rows: List[List[str]]) -> List[Dict[str, Any]]:
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
            continue

        try:
            date_str = row[2].strip()
            trade_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            logger.warning(f"Row {row_num}: Could not parse date '{row[2].strip()}'")
            continue

        account_id_raw = row[3].strip().upper()
        if account_id_raw.startswith('PRAC'):
            logger.debug(f"Row {row_num}: Skipping practice account '{account_id_raw}'")
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


def _parse_ib_activity_statement(rows: List[List[str]]) -> List[Dict[str, Any]]:
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
                                continue

                        symbol = row[1].strip()
                        buy_sell = row[2].strip().upper()
                        type_code, type_label = _normalize_type(buy_sell, _IB_TYPE_MAP)

                        try:
                            quantity = Decimal(row[3].strip())
                        except Exception:
                            logger.warning(f"Row {row_num}: Could not parse quantity")
                            continue

                        try:
                            price = Decimal(row[4].strip())
                        except Exception:
                            logger.warning(f"Row {row_num}: Could not parse price")
                            continue

                        try:
                            commission = Decimal(row[5].strip())
                        except Exception:
                            commission = Decimal('0.00')

                        try:
                            amount = Decimal(row[6].strip())
                        except Exception:
                            logger.warning(f"Row {row_num}: Could not parse proceeds")
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
                    continue

    return trades


def parse_rbc_csv(file_path: str) -> Tuple[List[Dict[str, Any]], Decimal]:
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

    trades: List[Dict[str, Any]] = []
    usd_to_cad_rate: Decimal = Decimal('1.0')
    found_header: bool = False

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
                    continue

                cleaned = [cell.strip().strip('"') for cell in row]

                activity = cleaned[1]
                if not activity:
                    continue

                type_code, type_label = _normalize_type(activity, _RBC_TYPE_MAP)

                try:
                    trade_date = datetime.strptime(cleaned[0], '%B %d, %Y').date()
                except ValueError:
                    logger.warning(f"Row {row_num}: Could not parse date '{cleaned[0]}'")
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
