"""CSV parsers for IB and RBC broker statement imports."""

import csv
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def parse_ib_csv(file_path: str) -> List[Dict[str, Any]]:
    """Parse Interactive Brokers CSV — supports two formats:

    1. Activity Statement (multi-section): first column is the section name
       e.g. "Trades", "Dividends", with a header row then data rows.
    2. Transaction History (flat export): columns are
       Section, Row-type, Date, Account, Description, Type, Symbol,
       Quantity, Price, Currency, Gross Amount, Commission, Net Amount.

    Only Buy and Sell rows are imported; all other rows are skipped.

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
      4  Description
      5  Transaction Type (Buy / Sell / Dividend / …)
      6  Symbol
      7  Quantity
      8  Price
      9  Price Currency
      10 Gross Amount
      11 Commission
      12 Net Amount
    """
    trades: List[Dict[str, Any]] = []

    for row_num, row in enumerate(rows, start=1):
        if len(row) < 13:
            continue
        if row[0].strip() != 'Transaction History' or row[1].strip() != 'Data':
            continue

        tx_type = row[5].strip()
        if tx_type not in ('Buy', 'Sell'):
            continue

        try:
            date_str = row[2].strip()
            trade_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            logger.warning(f"Row {row_num}: Could not parse date '{row[2].strip()}'")
            continue

        symbol = row[6].strip()
        if not symbol or symbol == '-':
            logger.warning(f"Row {row_num}: Missing symbol, skipping")
            continue

        try:
            quantity = abs(Decimal(row[7].strip()))
        except Exception:
            logger.warning(f"Row {row_num}: Could not parse quantity '{row[7].strip()}'")
            continue

        try:
            price = Decimal(row[8].strip())
        except Exception:
            logger.warning(f"Row {row_num}: Could not parse price '{row[8].strip()}'")
            continue

        currency = row[9].strip() if row[9].strip() not in ('', '-') else 'USD'

        try:
            amount = abs(Decimal(row[10].strip()))
        except Exception:
            logger.warning(f"Row {row_num}: Could not parse gross amount '{row[10].strip()}'")
            continue

        try:
            commission = abs(Decimal(row[11].strip()))
        except Exception:
            commission = Decimal('0.00')

        trades.append({
            'date': trade_date,
            'symbol': symbol,
            'type': tx_type.upper(),
            'quantity': quantity,
            'price': price,
            'commission': commission,
            'amount': amount,
            'currency': currency,
            'exchange_rate_cad': Decimal('1.000000'),
        })

    return trades


def _parse_ib_activity_statement(rows: List[List[str]]) -> List[Dict[str, Any]]:
    """Parse IB Activity Statement CSV format (multi-section with "Trades" header)."""
    trades: List[Dict[str, Any]] = []
    in_trades_section: bool = False
    header: List[str] = []

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
                        if buy_sell not in ('BUY', 'SELL'):
                            logger.warning(f"Row {row_num}: Unknown type '{buy_sell}', skipping")
                            continue

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

                        trades.append({
                            'date': trade_date,
                            'symbol': symbol,
                            'type': buy_sell,
                            'quantity': quantity,
                            'price': price,
                            'commission': commission,
                            'amount': amount,
                            'currency': currency,
                            'exchange_rate_cad': Decimal('1.000000'),
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

    Args:
        file_path: Path to the RBC CSV file.

    Returns:
        Tuple of (trades_list, usd_to_cad_rate).
        Rate is Decimal('1.0') if not found in file.
    """
    import re as _re

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
                exchange_rate_match = _re.search(
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

                # Parse data rows — require at least 11 columns
                if len(row) < 11:
                    continue

                cleaned = [cell.strip().strip('"') for cell in row]

                activity = cleaned[1]
                if activity == 'Buy':
                    tx_type = 'BUY'
                elif activity == 'Sell':
                    tx_type = 'SELL'
                elif activity == 'Dividends':
                    tx_type = 'DIV'
                else:
                    continue  # skip Others

                try:
                    trade_date = datetime.strptime(cleaned[0], '%B %d, %Y').date()
                except ValueError:
                    logger.warning(f"Row {row_num}: Could not parse date '{cleaned[0]}'")
                    continue

                try:
                    quantity = abs(Decimal(cleaned[4]))
                    price = Decimal(cleaned[5])
                    amount = abs(Decimal(cleaned[8]))
                except Exception:
                    logger.warning(f"Row {row_num}: Could not parse numeric fields, skipping")
                    continue

                # Commission = difference between gross and net value
                gross = quantity * price
                commission = abs(gross - amount).quantize(Decimal('0.01'))
                if commission < 0 or commission > 50:
                    commission = Decimal('0.00')

                currency = cleaned[9] if len(cleaned) > 9 else 'USD'
                symbol = cleaned[2]

                trades.append({
                    'date': trade_date,
                    'symbol': symbol,
                    'type': tx_type,
                    'quantity': quantity,
                    'price': price,
                    'commission': commission,
                    'amount': amount,
                    'currency': currency,
                    'exchange_rate_cad': usd_to_cad_rate,
                })

    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")
    except PermissionError:
        raise PermissionError(f"Permission denied: {file_path}")

    logger.info(f"Parsed {len(trades)} trades from RBC CSV")
    return trades, usd_to_cad_rate
