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
                        })
                except Exception as e:
                    logger.error(f"Row {row_num}: Error parsing trade data: {e}")
                    continue

    return trades


def parse_rbc_csv(file_path: str) -> List[Dict[str, Any]]:
    """Parse RBC Direct Investing trade confirmation CSV.
    
    Maps RBC-specific columns to Transaction fields.
    
    Args:
        file_path: Path to the RBC CSV file.
        
    Returns:
        List of dictionaries with parsed trade data.
    """
    trades: List[Dict[str, Any]] = []
    header: List[str] = []
    
    try:
        with open(file_path, 'r', encoding='utf-8-sig') as csvfile:
            reader = csv.reader(csvfile)
            
            for row_num, row in enumerate(reader, start=1):
                # Skip empty rows
                if not row or all(cell.strip() == '' for cell in row):
                    continue
                
                # First non-empty row is the header
                if not header:
                    header = [cell.strip().lower().replace(' ', '_') for cell in row]
                    continue
                
                # Parse data row
                if header and len(row) >= len(header):
                    try:
                        trade_data: Dict[str, Any] = {}
                        
                        # Create a mapping from header to values
                        row_dict: Dict[str, str] = {}
                        for i, col_name in enumerate(header):
                            if i < len(row):
                                row_dict[col_name] = row[i].strip()
                        
                        # Map RBC columns to Transaction fields
                        # RBC typical columns: Date, Symbol, Description, Type, Quantity, Price, Commission, Total, Currency
                        
                        # Date
                        date_str = row_dict.get('date', '')
                        if date_str:
                            try:
                                dt = datetime.strptime(date_str, '%Y-%m-%d')
                                trade_data['date'] = dt.date()
                            except ValueError:
                                try:
                                    dt = datetime.strptime(date_str, '%m/%d/%Y')
                                    trade_data['date'] = dt.date()
                                except ValueError:
                                    try:
                                        dt = datetime.strptime(date_str, '%d/%m/%Y')
                                        trade_data['date'] = dt.date()
                                    except ValueError:
                                        logger.warning(f"Row {row_num}: Could not parse date '{date_str}'")
                                        continue
                        else:
                            logger.warning(f"Row {row_num}: Missing date")
                            continue
                        
                        # Symbol
                        trade_data['symbol'] = row_dict.get('symbol', '')
                        
                        # Type (Buy/Sell)
                        trade_type = row_dict.get('type', '').upper()
                        if 'BUY' in trade_type or 'PURCHASE' in trade_type:
                            trade_data['type'] = 'BUY'
                        elif 'SELL' in trade_type or 'SALE' in trade_type:
                            trade_data['type'] = 'SELL'
                        else:
                            logger.warning(f"Row {row_num}: Unknown type '{trade_type}', skipping")
                            continue
                        
                        # Quantity
                        try:
                            trade_data['quantity'] = Decimal(row_dict.get('quantity', '0'))
                        except Exception:
                            logger.warning(f"Row {row_num}: Could not parse quantity '{row_dict.get('quantity')}'")
                            continue
                        
                        # Price
                        try:
                            trade_data['price'] = Decimal(row_dict.get('price', '0'))
                        except Exception:
                            logger.warning(f"Row {row_num}: Could not parse price '{row_dict.get('price')}'")
                            continue
                        
                        # Commission
                        try:
                            trade_data['commission'] = Decimal(row_dict.get('commission', '0'))
                        except Exception:
                            trade_data['commission'] = Decimal('0.00')
                        
                        # Amount (Total)
                        try:
                            trade_data['amount'] = Decimal(row_dict.get('total', '0'))
                        except Exception:
                            logger.warning(f"Row {row_num}: Could not parse total '{row_dict.get('total')}'")
                            continue
                        
                        # Currency
                        trade_data['currency'] = row_dict.get('currency', 'USD')
                        
                        trades.append(trade_data)
                        
                    except Exception as e:
                        logger.error(f"Row {row_num}: Error parsing RBC trade data: {e}")
                        continue
    
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")
    except PermissionError:
        raise PermissionError(f"Permission denied: {file_path}")
    
    logger.info(f"Parsed {len(trades)} trades from RBC CSV")
    return trades
