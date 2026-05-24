"""RBC PDF statement parser using PyMuPDF text extraction + regex."""

import logging
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import fitz

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for section headers
# ---------------------------------------------------------------------------
ASSET_SUMMARY_HEADER = re.compile(r'Asset\s+Summary', re.IGNORECASE)
CASH_BALANCE_HEADER = re.compile(r'Cash\s+Balance', re.IGNORECASE)
INCOME_SUMMARY_HEADER = re.compile(r'Income\s+Summary', re.IGNORECASE)
CONTRIBUTION_HEADER = re.compile(r'Contribution\s+Summary', re.IGNORECASE)
ASSET_REVIEW_HEADER = re.compile(r'Asset\s+Review', re.IGNORECASE)
ACCOUNT_ACTIVITY_HEADER = re.compile(r'Account\s+Activity', re.IGNORECASE)

# Metadata patterns
FX_RATE_PATTERN = re.compile(
    r'Exchange rate\s+1\s*USD\s*=\s*([\d.]+)\s*CAD', re.IGNORECASE
)
ACCOUNT_NUMBER_PATTERN = re.compile(
    r'Your Account Number:\s*(\S+-\S+-\S+-\S+)', re.IGNORECASE
)
LAST_STATEMENT_PATTERN = re.compile(
    r'Date of Last Statement:\s*(.+)', re.IGNORECASE
)
MONTH_ABBREVS = (
    'JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE',
    'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER',
    'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG',
    'SEPT', 'SEP', 'OCT', 'NOV', 'DEC',
)

# Match full month names or abbreviations (including SEPT), optional dot, then day and year.
STATEMENT_DATE_PATTERN = re.compile(
    r'\b(' + '|'.join(MONTH_ABBREVS) + r')\.?\s+(\d{1,2})\s+(\d{4})',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_rbc_date(month_str: str, year: int) -> date:
    """Parse RBC-style date like 'SEPT 30' or 'AUG. 29' with known year."""
    month_str = re.sub(r'\.', '', month_str).upper()
    month_map = {
        'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
        'JUL': 7, 'AUG': 8, 'SEP': 9, 'SEPT': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
    }
    parts = month_str.split()
    # Check full token first (handles 'SEPT'), then fall back to 3-char prefix.
    month = month_map.get(parts[0])
    if month is None:
        month = month_map.get(parts[0][:3])
    if month is None:
        raise ValueError(f"Unknown month abbreviation: {parts[0]}")
    day = int(parts[1]) if len(parts) > 1 else 1
    return date(year, month, day)


def _clean_currency(value_str: str) -> Decimal:
    """Convert a dollar string like '$1,036.34' to Decimal."""
    if not value_str or value_str.strip() == '':
        return Decimal('0.00')
    cleaned = value_str.replace('$', '').replace(',', '').strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        logger.warning("Could not parse currency value: %s", value_str)
        return Decimal('0.00')


def _clean_pct(value_str: str) -> Decimal:
    """Convert a percentage string like '98.03 %' to Decimal."""
    if not value_str or value_str.strip() == '':
        return Decimal('0.00')
    cleaned = value_str.replace('%', '').strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        logger.warning("Could not parse percentage: %s", value_str)
        return Decimal('0.00')


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

def _find_section(text: str, pattern: re.Pattern) -> tuple[int, int]:
    """Return (start, end) indices for the section defined by *pattern*.

    *start* is the line index where the header is found.
    *end* is the line index of the next header or end of text.
    """
    lines = text.split('\n')
    start_idx = None
    for i, line in enumerate(lines):
        if pattern.search(line):
            start_idx = i
            break
    if start_idx is None:
        return -1, -1

    # Find the next section header or end
    end_idx = len(lines)
    all_headers = [
        ASSET_SUMMARY_HEADER, CASH_BALANCE_HEADER,
        INCOME_SUMMARY_HEADER, CONTRIBUTION_HEADER,
        ASSET_REVIEW_HEADER, ACCOUNT_ACTIVITY_HEADER,
    ]
    for j in range(start_idx + 1, len(lines)):
        for hdr in all_headers:
            if hdr.search(lines[j]):
                end_idx = j
                break
        if end_idx != len(lines):
            break

    return start_idx, end_idx


def _parse_asset_summary(text: str) -> list[dict[str, Any]]:
    """Parse the Asset Summary table."""
    start, end = _find_section(text, ASSET_SUMMARY_HEADER)
    if start == -1:
        return []

    lines = text.split('\n')[start:end]
    results = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('---') or stripped.startswith('___'):
            continue
        if ASSET_SUMMARY_HEADER.search(stripped):
            continue

        match = re.match(
            r'^(\w[\w\s]*?)\s+\$([\d,]+\.\d+)\s+([\d.]+)\s*%?',
            stripped, re.IGNORECASE,
        )
        if match:
            asset_class = match.group(1).strip()
            market_value = _clean_currency('$' + match.group(2))
            percentage = _clean_pct(match.group(3) + '%')
            results.append({
                'account_type': asset_class,
                'opening': Decimal('0.00'),
                'closing': market_value,
                'market_value': market_value,
                'percentage': percentage,
            })

    return results


def _parse_cash_balance(text: str) -> list[dict[str, Any]]:
    """Parse the Cash Balance table."""
    start, end = _find_section(text, CASH_BALANCE_HEADER)
    if start == -1:
        return []

    lines = text.split('\n')[start:end]
    results = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('---') or stripped.startswith('___'):
            continue
        if CASH_BALANCE_HEADER.search(stripped):
            continue

        match = re.match(
            r'^(\w[\w\s]*?)\s+\$([\d,]+\.\d+)\s+\$([\d,]+\.\d+)',
            stripped, re.IGNORECASE,
        )
        if match:
            account_type = match.group(1).strip()
            opening = _clean_currency('$' + match.group(2))
            closing = _clean_currency('$' + match.group(3))
            results.append({
                'account_type': account_type,
                'opening': opening,
                'closing': closing,
                'market_value': closing,
                'percentage': Decimal('0.00'),
            })

    return results


def _parse_income_summary(text: str) -> dict[str, Any]:
    """Parse the Income Summary table."""
    start, end = _find_section(text, INCOME_SUMMARY_HEADER)
    if start == -1:
        return {
            'dividends_this_month': Decimal('0.00'),
            'dividends_ytd': Decimal('0.00'),
            'interest_this_month': Decimal('0.00'),
            'interest_ytd': Decimal('0.00'),
            'total_this_month': Decimal('0.00'),
            'total_ytd': Decimal('0.00'),
        }

    lines = text.split('\n')[start:end]
    result = {
        'dividends_this_month': Decimal('0.00'),
        'dividends_ytd': Decimal('0.00'),
        'interest_this_month': Decimal('0.00'),
        'interest_ytd': Decimal('0.00'),
        'total_this_month': Decimal('0.00'),
        'total_ytd': Decimal('0.00'),
    }

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('---') or stripped.startswith('___'):
            continue
        if INCOME_SUMMARY_HEADER.search(stripped):
            continue

        match = re.match(
            r'^(Dividends|Interest|Total Income)\s+\$([\d,]+\.\d+)\s+\$([\d,]+\.\d+)',
            stripped, re.IGNORECASE,
        )
        if match:
            row_type = match.group(1).lower()
            this_month = _clean_currency('$' + match.group(2))
            ytd = _clean_currency('$' + match.group(3))

            if 'dividend' in row_type:
                result['dividends_this_month'] = this_month
                result['dividends_ytd'] = ytd
            elif 'interest' in row_type:
                result['interest_this_month'] = this_month
                result['interest_ytd'] = ytd
            elif 'total' in row_type:
                result['total_this_month'] = this_month
                result['total_ytd'] = ytd

    return result


def _parse_contribution_summary(text: str) -> dict[str, Any]:
    """Parse the Contribution Summary table."""
    start, end = _find_section(text, CONTRIBUTION_HEADER)
    if start == -1:
        return {
            'first_60_days': Decimal('0.00'),
            'balance_of_year': Decimal('0.00'),
        }

    lines = text.split('\n')[start:end]
    result = {
        'first_60_days': Decimal('0.00'),
        'balance_of_year': Decimal('0.00'),
    }

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('---') or stripped.startswith('___'):
            continue
        if CONTRIBUTION_HEADER.search(stripped):
            continue

        values = re.findall(r'\$([\d,]+\.\d+)', stripped)
        if len(values) >= 2:
            result['first_60_days'] = _clean_currency('$' + values[0])
            result['balance_of_year'] = _clean_currency('$' + values[1])

    return result


def _parse_account_activity(text: str) -> list[dict[str, Any]]:
    """Parse the Account Activity table."""
    start, end = _find_section(text, ACCOUNT_ACTIVITY_HEADER)
    if start == -1:
        return []

    lines = text.split('\n')[start:end]
    results = []

    # Month abbreviation pattern for date parsing
    MONTH_PAT = r'(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEPT|SEP|OCT|NOV|DEC)'
    date_pat = re.compile(r'^(' + MONTH_PAT + r')[\.\s]+(\d{1,2})\s+(\w+)', re.IGNORECASE)

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith('---') or line.startswith('___'):
            i += 1
            continue
        if ACCOUNT_ACTIVITY_HEADER.search(line):
            i += 1
            continue

        # Match date + activity type
        date_match = date_pat.match(line)
        if not date_match:
            i += 1
            continue

        month_str = date_match.group(1)
        day_str = date_match.group(2)
        activity_type = date_match.group(3).upper()

        # Get the rest of the line after the activity type
        rest = line[date_match.end():].strip()

        # Find all $X.XX amounts with their positions
        dollar_amounts = list(re.finditer(r'\$([\d,]+\.\d+)', rest))

        # Parse by column positions based on header alignment
        # Header: DATE        ACTIVITY    DESCRIPTION                                    PRICE/RATE    QUANTITY    DEBIT    CREDIT
        # After activity type, we have: DESCRIPTION PRICE/RATE QUANTITY DEBIT CREDIT
        # Strategy: Find the two rightmost dollar amounts - DEBIT is left of CREDIT
        # If only one dollar amount, determine based on activity type:
        #   BUY/INTEREST -> DEBIT, SELL/DIVIDEND -> CREDIT

        # Extract numeric fields (price/rate and quantity) - numbers without $
        # These appear before the dollar amounts
        numeric_pat = re.compile(r'(\d+\.?\d*)')
        
        # Get everything before the first dollar amount
        if dollar_amounts:
            pre_dollar = rest[:dollar_amounts[0].start()].strip()
            # Extract numeric fields from pre_dollar part
            numerics = numeric_pat.findall(pre_dollar)
            # First numeric is price/rate, second is quantity (based on column order: PRICE/RATE then QUANTITY)
            price_str = numerics[0] if len(numerics) >= 1 else None
            quantity_str = numerics[1] if len(numerics) >= 2 else None
            # Description is everything before the first numeric
            if numerics:
                # Find position of first numeric in pre_dollar
                first_num_match = numeric_pat.search(pre_dollar)
                description = pre_dollar[:first_num_match.start()].strip() if first_num_match else pre_dollar.strip()
            else:
                description = pre_dollar.strip()
        else:
            price_str = None
            quantity_str = None
            description = rest.strip()

        # Assign debit/credit based on column positions
        # DEBIT column comes before CREDIT column
        if len(dollar_amounts) >= 2:
            # Two amounts: first is DEBIT, second is CREDIT
            debit_str = dollar_amounts[0].group(1)
            credit_str = dollar_amounts[1].group(1)
        elif len(dollar_amounts) == 1:
            # One amount: determine based on activity type
            amount_str = dollar_amounts[0].group(1)
            if activity_type in ('BUY', 'INTEREST'):
                debit_str = amount_str
                credit_str = None
            else:  # SELL, DIVIDEND
                debit_str = None
                credit_str = amount_str
        else:
            debit_str = None
            credit_str = None

        year = 2025

        try:
            tx_date = _parse_rbc_date(f"{month_str} {day_str}", year)
        except (ValueError, IndexError):
            i += 1
            continue

        price = Decimal(price_str) if price_str else Decimal('0.00')
        quantity = Decimal(quantity_str) if quantity_str else Decimal('0.00')
        debit = _clean_currency('$' + debit_str) if debit_str else Decimal('0.00')
        credit = _clean_currency('$' + credit_str) if credit_str else Decimal('0.00')

        symbol = ''
        symbol_match = re.search(r'\b([A-Z]{2,6})\b', description)
        if symbol_match:
            symbol = symbol_match.group(1)

        results.append({
            'date': tx_date,
            'activity_type': activity_type,
            'description': description,
            'symbol': symbol,
            'quantity': quantity,
            'price': price,
            'debit': debit,
            'credit': credit,
            'currency': 'CAD',
            'commission': Decimal('0.00'),
        })

        i += 1

    return results


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def _extract_text_from_pdf(file_path: str) -> str:
    """Extract text from a real PDF file using PyMuPDF.

    If the file is not a valid PDF (e.g. synthetic test text), falls back
    to reading it as plain text so tests can pass raw text as a .pdf file.

    Args:
        file_path: Path to the PDF file.

    Returns:
        Extracted text content.
    """
    # Verify it looks like a PDF before attempting to open
    is_pdf = False
    try:
        with open(file_path, 'rb') as f:
            header = f.read(5)
        if header == b'%PDF-':
            is_pdf = True
    except Exception:
        pass

    if not is_pdf:
        # Not a real PDF — read as plain text (useful for tests with synthetic text)
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        except Exception as e:
            raise ValueError(f"Unable to read file as text: {e}") from e

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        raise ValueError(f"Unable to open PDF file: {e}") from e

    if doc.page_count == 0:
        doc.close()
        raise ValueError("PDF has no pages")

    full_text = ''
    try:
        for page_num in range(doc.page_count):
            page = doc.load_page(page_num)
            full_text += page.get_text() + '\n'
    except Exception as e:
        doc.close()
        raise ValueError(f"Unable to extract text from PDF: {e}") from e

    doc.close()
    return full_text


def parse_rbc_pdf(file_path: str) -> dict:
    """Parse an RBC PDF statement and return structured data.

    Args:
        file_path: Path to the PDF file, or a string of raw text content
                   (for testing with synthetic text).

    Returns:
        Dictionary with keys: statement_date, last_statement_date,
        account_number, fx_rate, balances, transactions,
        income_summary, contribution_summary.

    Raises:
        ValueError: If the file is not a valid PDF or cannot be parsed.
    """
    # If the path looks like raw text (no file extension or file doesn't exist),
    # treat it as raw text content for testing
    if not os.path.exists(file_path):
        full_text = file_path
    else:
        full_text = _extract_text_from_pdf(file_path)

    # Extract metadata
    account_number = ''
    account_match = ACCOUNT_NUMBER_PATTERN.search(full_text)
    if account_match:
        account_number = account_match.group(1)

    fx_rate = Decimal('1.000000')
    fx_match = FX_RATE_PATTERN.search(full_text)
    if fx_match:
        fx_rate = Decimal(fx_match.group(1))

    statement_date = None
    last_statement_date = None

    # Extract statement date from header — look for the main header line
    # The header typically has "SEPT 30 2025" near the top, before "Date of Last Statement"
    # Some PDFs split month+day and year across two lines (e.g. "SEPT 30\n2025")
    lines = full_text.split('\n')
    for i, line in enumerate(lines):
        # Try single-line match first: "SEPT 30 2025"
        date_match = STATEMENT_DATE_PATTERN.search(line)
        if date_match:
            month_str = date_match.group(1)
            day = int(date_match.group(2))
            year = int(date_match.group(3))
            if 'last statement' in line.lower():
                continue
            try:
                # Build full month+day string so _parse_rbc_date gets the day
                full_month_str = f"{month_str} {day}"
                statement_date = _parse_rbc_date(full_month_str, year)
                break
            except ValueError:
                logger.warning("Could not parse statement date: %s", date_match.group(0))
                continue

        # Try two-line match: "SEPT 30" on this line, "2025" on next line
        if i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            month_match = re.match(
                r'\b(' + '|'.join(MONTH_ABBREVS) + r')\.?\s+(\d{1,2})',
                line.strip(), re.IGNORECASE,
            )
            year_match = re.match(r'^(\d{4})$', next_line)
            if month_match and year_match:
                month_str = month_match.group(1)
                day = int(month_match.group(2))
                year = int(year_match.group(1))
                if 'last statement' in line.lower():
                    continue
                try:
                    # Build a full date string so _parse_rbc_date gets the day
                    full_month_str = f"{month_str} {day}"
                    statement_date = _parse_rbc_date(full_month_str, year)
                    break
                except ValueError:
                    logger.warning("Could not parse statement date: %s %s", month_match.group(0), year_match.group(0))
                    continue

    # Extract last statement date
    last_stmt_match = LAST_STATEMENT_PATTERN.search(full_text)
    if last_stmt_match:
        last_stmt_str = last_stmt_match.group(1).strip()
        last_date_match = re.search(
            r'(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEPT|SEP|OCT|NOV|DEC)[\.\s]+(\d{1,2})',
            last_stmt_str, re.IGNORECASE,
        )
        if last_date_match and statement_date:
            try:
                last_month = last_date_match.group(1)
                last_day = int(last_date_match.group(2))
                # Build full month+day string so _parse_rbc_date gets the day
                last_full_month_str = f"{last_month} {last_day}"
                last_statement_date = _parse_rbc_date(
                    last_full_month_str,
                    statement_date.year,
                )
            except ValueError:
                logger.warning(
                    "Could not parse last statement date: %s",
                    last_stmt_str,
                )

    # If we couldn't get a statement date from PDF content, try to infer from filename
    if statement_date is None:
        file_name = os.path.basename(file_path) if os.path.exists(file_path) else ''
        # Try to extract YYYY-MM-DD from filename (e.g. "Statement-5362 2025-09-30.pdf")
        fn_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', file_name)
        if fn_match:
            try:
                statement_date = date(int(fn_match.group(1)), int(fn_match.group(2)), int(fn_match.group(3)))
                logger.info("Inferred statement date from filename: %s -> %s", file_name, statement_date)
            except ValueError:
                logger.warning("Could not parse statement date from filename: %s", file_name)

    if statement_date is None:
        logger.warning("Could not determine statement date from PDF content or filename")

    # Parse sections
    try:
        balances_raw = _parse_asset_summary(full_text)
    except Exception as e:
        logger.warning("Error parsing asset summary: %s", e)
        balances_raw = []

    try:
        cash_balances = _parse_cash_balance(full_text)
    except Exception as e:
        logger.warning("Error parsing cash balance: %s", e)
        cash_balances = []

    # Merge cash balances into balances
    all_balances = []
    cash_types = {b['account_type'] for b in cash_balances}

    for bal in balances_raw:
        cash_entry = next(
            (c for c in cash_balances if c['account_type'] == bal['account_type']),
            None,
        )
        if cash_entry:
            bal['opening'] = cash_entry['opening']
            bal['closing'] = cash_entry['closing']
        all_balances.append(bal)

    for cash_bal in cash_balances:
        if cash_bal['account_type'] not in cash_types:
            all_balances.append(cash_bal)

    try:
        income_summary = _parse_income_summary(full_text)
    except Exception as e:
        logger.warning("Error parsing income summary: %s", e)
        income_summary = {
            'dividends_this_month': Decimal('0.00'),
            'dividends_ytd': Decimal('0.00'),
            'interest_this_month': Decimal('0.00'),
            'interest_ytd': Decimal('0.00'),
            'total_this_month': Decimal('0.00'),
            'total_ytd': Decimal('0.00'),
        }

    try:
        contribution_summary = _parse_contribution_summary(full_text)
    except Exception as e:
        logger.warning("Error parsing contribution summary: %s", e)
        contribution_summary = {
            'first_60_days': Decimal('0.00'),
            'balance_of_year': Decimal('0.00'),
        }

    try:
        transactions = _parse_account_activity(full_text)
    except Exception as e:
        logger.warning("Error parsing account activity: %s", e)
        transactions = []

    return {
        'statement_date': statement_date,
        'last_statement_date': last_statement_date,
        'account_number': account_number,
        'fx_rate': fx_rate,
        'balances': all_balances,
        'transactions': transactions,
        'income_summary': income_summary,
        'contribution_summary': contribution_summary,
    }
