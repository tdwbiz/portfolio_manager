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
STATEMENT_DATE_PATTERN = re.compile(
    r'\b(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|'
    r'JANUAR|FEBRUAR|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|'
    r'JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\w*\.?\s+(\d{1,2})\s+(\d{4})',
    re.IGNORECASE,
)

# Simpler fallback pattern for statement date extraction
STATEMENT_DATE_SIMPLE = re.compile(
    r'(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|'
    r'JANUAR|FEBRUAR|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|'
    r'JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\w*\.?\s+(\d{1,2})\s+(\d{4})',
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
        'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
    }
    parts = month_str.split()
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

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith('---') or line.startswith('___'):
            i += 1
            continue
        if ACCOUNT_ACTIVITY_HEADER.search(line):
            i += 1
            continue

        match = re.match(
            r'^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\.\s]+(\d{1,2})\s+'
            r'(\w+)\s+'
            r'(.+?)\s+'
            r'([\d.]+)?\s*'
            r'([\d.]+)?\s*'
            r'\$([\d,]+\.\d+)?\s*'
            r'\$([\d,]+\.\d+)?',
            line, re.IGNORECASE,
        )

        if match:
            month_str = match.group(1)
            day_str = match.group(2)
            activity_type = match.group(3).upper()
            description = match.group(4).strip()
            price_str = match.group(5)
            quantity_str = match.group(6)
            debit_str = match.group(7)
            credit_str = match.group(8)

            year = 2025

            try:
                tx_date = _parse_rbc_date(month_str, year)
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

    Args:
        file_path: Path to the PDF file.

    Returns:
        Extracted text content.

    Raises:
        ValueError: If the file is not a valid PDF.
    """
    # Verify it looks like a PDF before attempting to open
    try:
        with open(file_path, 'rb') as f:
            header = f.read(5)
        if header != b'%PDF-':
            raise ValueError("File does not appear to be a valid PDF")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Unable to read PDF file: {e}") from e

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
    lines = full_text.split('\n')
    statement_date = None
    for i, line in enumerate(lines):
        date_match = STATEMENT_DATE_PATTERN.search(line)
        if date_match:
            month_str = date_match.group(1)
            day_str = date_match.group(2)
            year = int(date_match.group(3))
            # Skip if this line also contains "Last Statement"
            if 'last statement' in line.lower():
                continue
            try:
                statement_date = _parse_rbc_date(month_str, year)
                break
            except ValueError:
                logger.warning("Could not parse statement date: %s", date_match.group(0))
                continue

    # Extract last statement date
    last_stmt_match = LAST_STATEMENT_PATTERN.search(full_text)
    if last_stmt_match:
        last_stmt_str = last_stmt_match.group(1).strip()
        last_date_match = re.search(
            r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\.\s]+(\d{1,2})',
            last_stmt_str, re.IGNORECASE,
        )
        if last_date_match and statement_date:
            try:
                last_statement_date = _parse_rbc_date(
                    last_date_match.group(1),
                    statement_date.year,
                )
            except ValueError:
                logger.warning(
                    "Could not parse last statement date: %s",
                    last_stmt_str,
                )

    # If we couldn't get a statement date, try to infer from filename
    if statement_date is None:
        logger.warning("Could not determine statement date from PDF content")

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
