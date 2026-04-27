"""CSV parsers for IB and RBC broker statement imports."""

import csv
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def parse_ib_csv(file_path: str) -> List[Dict[str, Any]]:
    """Parse Interactive Brokers Activity Statement CSV.
    
    Detects and imports only the "Trades" section, skipping other sections.
    
    Args:
        file_path: Path to the IB CSV file.
        
    Returns:
        List of dictionaries with parsed trade data.
    """
    trades: List[Dict[str, Any]] = []
    in_trades_section: bool = False
    header: List[str] = []
    
    try:
        with open(file_path, 'r', encoding='utf-8-sig') as csvfile:
            reader = csv.reader(csvfile)
            
            for row_num, row in enumerate(reader, start=1):
                # Skip empty rows
                if not row or all(cell.strip() == '' for cell in row):
                    continue
                
                row_text = row[0].strip().lower()
                
                # Detect section headers
                if row_text == 'trades':
                    in_trades_section = True
                    # Next row should be the header for trades
                    continue
                
                if in_trades_section:
                    # Check if we've hit a new section (non-empty first column that's not data)
                    if len(row) > 0 and row[0].strip():
                        first_cell = row[0].strip().lower()
                        # If first cell is a known section header and we already have data, stop
                        if first_cell in ['dividends', 'fx', 'interest', 'journal', 
                                         'funds', 'misc', 'closed positions', 
                                         'activity summary', 'account']:
                            break
                        # If this looks like a header row (contains typical column names)
                        if any(col in first_cell.lower() for col in ['date/time', 'symbol', 
                                                                       'buy/sell', 'quantity', 
                                                                       't. price']):
                            header = [cell.strip().lower().replace('/', '_') 
                                     for cell in row]
                            continue
                    
                    # Parse trade data row
                    if header and len(row) >= len(header):
                        try:
                            trade_data: Dict[str, Any] = {}
                            
                            # Map IB columns to Transaction fields
                            # IB CSV format: Date/Time, Symbol, Buy/Sell, Quantity, T. Price, Comm/Fee, Proceeds, Currency
                            if len(row) >= 8:
                                # Parse date/time (format: 2024-01-15 10:30:00)
                                date_str = row[0].strip()
                                try:
                                    dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                                    trade_data['date'] = dt.date()
                                except ValueError:
                                    try:
                                        dt = datetime.strptime(date_str, '%m/%d/%Y %H:%M:%S')
                                        trade_data['date'] = dt.date()
                                    except ValueError:
                                        logger.warning(f"Row {row_num}: Could not parse date '{date_str}'")
                                        continue
                                
                                # Symbol
                                trade_data['symbol'] = row[1].strip()
                                
                                # Buy/Sell
                                buy_sell = row[2].strip().upper()
                                if buy_sell in ('BUY', 'SELL'):
                                    trade_data['type'] = buy_sell
                                else:
                                    logger.warning(f"Row {row_num}: Unknown type '{buy_sell}', skipping")
                                    continue
                                
                                # Quantity
                                try:
                                    trade_data['quantity'] = Decimal(row[3].strip())
                                except Exception:
                                    logger.warning(f"Row {row_num}: Could not parse quantity '{row[3].strip()}'")
                                    continue
                                
                                # T. Price
                                try:
                                    trade_data['price'] = Decimal(row[4].strip())
                                except Exception:
                                    logger.warning(f"Row {row_num}: Could not parse price '{row[4].strip()}'")
                                    continue
                                
                                # Comm/Fee
                                try:
                                    trade_data['commission'] = Decimal(row[5].strip())
                                except Exception:
                                    trade_data['commission'] = Decimal('0.00')
                                
                                # Proceeds
                                try:
                                    trade_data['amount'] = Decimal(row[6].strip())
                                except Exception:
                                    logger.warning(f"Row {row_num}: Could not parse proceeds '{row[6].strip()}'")
                                    continue
                                
                                # Currency
                                trade_data['currency'] = row[7].strip() if len(row) > 7 else 'USD'
                                
                                trades.append(trade_data)
                                
                        except Exception as e:
                            logger.error(f"Row {row_num}: Error parsing trade data: {e}")
                            continue
    
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")
    except PermissionError:
        raise PermissionError(f"Permission denied: {file_path}")
    
    logger.info(f"Parsed {len(trades)} trades from IB CSV")
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
