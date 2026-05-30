"""RBC Annual Report parser using PyMuPDF text extraction + regex.

RBC Annual Reports have wide character spacing that must be cleaned
before any regex matching. This module provides ``parse_rbc_annual_report()``
which returns a structured dict suitable for bulk-insert into the
RbcAnnual* model hierarchy.
"""

import logging
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import fitz

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Month helpers
# ---------------------------------------------------------------------------

MONTH_MAP: dict[str, int] = {
    'JANUARY': 1, 'FEBRUARY': 2, 'MARCH': 3, 'APRIL': 4,
    'MAY': 5, 'JUNE': 6, 'JULY': 7, 'AUGUST': 8,
    'SEPTEMBER': 9, 'OCTOBER': 10, 'NOVEMBER': 11, 'DECEMBER': 12,
}

MONTH_ABBREVS = tuple(MONTH_MAP.keys()) + (
    'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
    'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC',
)


def _parse_full_date(month_str: str, year: int) -> date:
    """Parse a full month name like 'January 1, 2025' or 'JAN 1, 2025'."""
    month_str = month_str.strip().upper()
    for name, num in MONTH_MAP.items():
        if month_str.startswith(name):
            day_match = re.search(r'\d+', month_str)
            day = int(day_match.group()) if day_match else 1
            return date(year, num, day)
    raise ValueError(f"Cannot parse month: {month_str}")


def _parse_short_date(month_str: str, day_str: str, year: int) -> date:
    """Parse a short date like 'JAN 1 2025' or 'JANUARY 1 2025'."""
    month_str = month_str.strip().upper()
    day = int(day_str.strip())
    for name, num in MONTH_MAP.items():
        if month_str.startswith(name):
            return date(year, num, day)
    raise ValueError(f"Cannot parse month: {month_str}")


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

def _clean_rbc_text(text: str) -> str:
    """Clean RBC PDF text by removing excessive inter-character whitespace.

    RBC PDFs use wide character spacing, producing output like:
        'A n n u a l   i n v e s t m e n t'
    or partly collapsed fragments on one line:
        'An nual', 'acc ount', 'Canadi an', 'Fo r', '3 1,'

    This function collapses these fragments back to normal words.
    """
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        tokens = line.split()
        if len(tokens) >= 2:
            # Detect wide-spacing indicators and collapse letter pairs.
            # Heuristics (any one triggers collapse):
            #   1. Any token is a single alpha character ("t", "r", "J")
            #   2. All tokens are purely alpha AND max token length ≤ 5
            #      (catches "acc ount", "Cha nge", "Fo r")
            #   3. Two-token line, both purely alpha, shortest ≤ 2 chars
            #      (catches "Canadi an", "You r", "An nual")
            all_alpha = all(re.match(r'^[a-zA-Z]+$', t) for t in tokens)
            max_len = max(len(t) for t in tokens)
            is_wide_spaced = (
                any(len(t) == 1 and t.isalpha() for t in tokens)
                or (all_alpha and max_len <= 5)
                or (len(tokens) == 2 and all_alpha and any(len(t) <= 2 for t in tokens))
            )
            if is_wide_spaced:
                prev = None
                while prev != line:
                    prev = line
                    line = re.sub(r'([a-zA-Z]) ([a-zA-Z])', r'\1\2', line)

        # Always collapse digit-space-digit to handle dates like "3 1," → "31,"
        prev = None
        while prev != line:
            prev = line
            line = re.sub(r'(\d) (\d)', r'\1\2', line)

        cleaned = re.sub(r'  +', ' ', line).strip()
        cleaned_lines.append(cleaned)
    return '\n'.join(cleaned_lines)


# ---------------------------------------------------------------------------
# Section header patterns (applied to cleaned text)
# ---------------------------------------------------------------------------

PERFORMANCE_HEADER = re.compile(r'Annual\s*investment\s*performance\s*report', re.IGNORECASE)
CHARGES_HEADER = re.compile(r'Annual\s*charges\s*and\s*compensation\s*report', re.IGNORECASE)

CAD_ACCOUNT_HEADER = re.compile(r'Your\s+Canadian\s+dollar\s+account', re.IGNORECASE)
USD_ACCOUNT_HEADER = re.compile(r'Your\s+U\.?\s*S\.?\s*dollar\s+account', re.IGNORECASE)

BALANCES_HEADER = re.compile(r'Change\s+in\s+account\s+value', re.IGNORECASE)
PERFORMANCE_TABLE_HEADER = re.compile(r'Money[\s-]*weighted\s+rate\s+of\s+return', re.IGNORECASE)

OPERATING_CHARGES_HEADER = re.compile(r'Operating\s+charges', re.IGNORECASE)
TRANSACTION_CHARGES_HEADER = re.compile(r'Transaction\s+charges', re.IGNORECASE)
COMMISSIONS_HEADER = re.compile(r'Commissions[\s\-—]*Equities', re.IGNORECASE)
TOTAL_CHARGES_HEADER = re.compile(r'Total\s+transaction\s+charges', re.IGNORECASE)
TOTAL_PAID_HEADER = re.compile(r'Total\s+paid\s+by\s+you', re.IGNORECASE)

TRAILING_COMMISSION_HEADER = re.compile(r'Trailing\s+commission', re.IGNORECASE)
OTHER_COMPENSATION_HEADER = re.compile(r'Other\s+compensation', re.IGNORECASE)
TOTAL_COMPENSATION_HEADER = re.compile(r'Total\s+compensation\s+from\s+third\s+parties', re.IGNORECASE)

# Amount-invested section (standalone header only — NOT the value row inside the balances table)
AMOUNT_INVESTED_HEADER = re.compile(r'^\s*Amount\s+invested\s*$', re.IGNORECASE)

# Period header
PERIOD_HEADER = re.compile(r'from\s+(\w+\s+\d+,\s+\d+)\s+to\s+(\w+\s+\d+,\s+\d+)', re.IGNORECASE)

# Account metadata — match "RRSP Account 695- 62 2 59" or "LIF Account 900-00008"
ACCOUNT_NUMBER_PATTERN = re.compile(r'Account\s+(\d{3})\s*[-\s]*(\d{3,5})', re.IGNORECASE)
# Only match known account type codes before "Account"
ACCOUNT_TYPE_PATTERN = re.compile(
    r'(?:RRIF|RRSP|RIF|LIF|LRIF|PRIF|TFSA|LIRA|RESP|IF)\s+Account',
    re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _extract_text_from_pdf(file_path: str) -> str:
    """Extract text from a PDF using PyMuPDF (fitz).

    Falls back to plain text if the file is not a valid PDF
    (useful for testing with synthetic text).
    """
    try:
        doc = fitz.open(file_path)
        if doc.page_count == 0:
            doc.close()
            raise ValueError("PDF has no pages")
        full_text = '\n'.join(
            doc.load_page(i).get_text() for i in range(doc.page_count)
        )
        doc.close()
        return full_text
    except Exception:
        pass

    # Fallback to plain text (for tests with synthetic text)
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception as e:
        raise ValueError(f"Unable to read file: {e}") from e


def _find_section_bounds(text: str, start_pattern: re.Pattern,
                         end_patterns: list | None = None) -> tuple[int, int]:
    """Return (start_line_idx, end_line_idx) for a section.

    Searches the full text (patterns may span multiple lines via \\s* or \\s+).
    Returns line indices into text.split('\\n').
    """
    start_match = start_pattern.search(text)
    if start_match is None:
        return -1, -1

    lines = text.split('\n')
    start_idx = text[:start_match.start()].count('\n')

    end_idx = len(lines)
    if end_patterns:
        after_start = start_match.end()
        earliest_end_char = len(text)
        for pat in end_patterns:
            m = pat.search(text, after_start)
            if m and m.start() < earliest_end_char:
                earliest_end_char = m.start()
        if earliest_end_char < len(text):
            end_idx = text[:earliest_end_char].count('\n')

    return start_idx, end_idx


def _read_table_values(lines: list[str], labels: list[str]) -> dict[str, Decimal]:
    """Read values from a simple table.

    For each label, find the line containing it and extract the dollar
    value from that line or the next line.
    """
    results: dict[str, Decimal] = {}
    for label in labels:
        for i, line in enumerate(lines):
            if label in line:
                # Try to find a dollar amount on this line or the next
                for j in range(i, min(i + 3, len(lines))):
                    dollar_matches = re.findall(r'\$([\d,]+\.\d+)', lines[j])
                    if dollar_matches:
                        results[label] = _clean_currency('$' + dollar_matches[0])
                        break
                else:
                    results[label] = Decimal('0.00')
                break
        else:
            results[label] = Decimal('0.00')
    return results


# ---------------------------------------------------------------------------
# Balances parser
# ---------------------------------------------------------------------------

def _parse_balances_section(text: str, currency: str) -> dict[str, Any] | None:
    """Parse the 'Change in Account Value' table for a given currency section."""
    start, end = _find_section_bounds(
        text, BALANCES_HEADER,
        end_patterns=[AMOUNT_INVESTED_HEADER, PERFORMANCE_TABLE_HEADER, CHARGES_HEADER],
    )
    if start == -1:
        return None

    lines = text.split('\n')[start:end]
    values = _read_table_values(
        lines,
        [
            'Beginning market value',
            'Deposits and transfers-in',
            'Withdrawals and transfers-out',
            'Net investment return',
            'Ending market value',
            'Amount invested',
        ],
    )

    return {
        'currency': currency,
        'beginning_market_value': values.get('Beginning market value', Decimal('0.00')),
        'deposits_transfers_in': values.get('Deposits and transfers-in', Decimal('0.00')),
        'withdrawals_transfers_out': values.get('Withdrawals and transfers-out', Decimal('0.00')),
        'net_investment_return': values.get('Net investment return', Decimal('0.00')),
        'ending_market_value': values.get('Ending market value', Decimal('0.00')),
        'amount_invested': values.get('Amount invested', Decimal('0.00')),
    }


# ---------------------------------------------------------------------------
# Performance parser
# ---------------------------------------------------------------------------

def _parse_performance_section(text: str, currency: str) -> dict[str, Any] | None:
    """Parse the Money-Weighted Rate of Return table for a given currency section."""
    start, end = _find_section_bounds(
        text, PERFORMANCE_TABLE_HEADER,
        end_patterns=[CAD_ACCOUNT_HEADER, USD_ACCOUNT_HEADER, CHARGES_HEADER],
    )
    if start == -1:
        return None

    lines = text.split('\n')[start:end]

    # Find since_date from "Since January 1, 2016" type text
    since_date = None
    since_match = re.search(r'Since\s+(\w+\s+\d+,\s+\d+)', text[start:end], re.IGNORECASE)
    if since_match:
        try:
            since_date = _parse_full_date(since_match.group(1), 2025)
        except ValueError:
            pass

    # Extract return percentages from each column
    # Columns: Past 1 year, Past 3 years, Past 5 years, Past 10 years, Since [date]
    period_labels = ['1_year', '3_years', '5_years', '10_years', 'since_date']
    column_patterns = [
        re.compile(r'Past\s+1\s+year', re.IGNORECASE),
        re.compile(r'Past\s+3\s+years?', re.IGNORECASE),
        re.compile(r'Past\s+5\s+years?', re.IGNORECASE),
        re.compile(r'Past\s+10\s+years?', re.IGNORECASE),
        re.compile(r'Since\s+\w', re.IGNORECASE),
    ]

    returns: list[dict[str, Any]] = []
    for idx, (label, col_pat) in enumerate(zip(period_labels, column_patterns)):
        for i, line in enumerate(lines):
            if col_pat.search(line):
                # The percentage value is usually on the next line or same line
                for j in range(i, min(i + 3, len(lines))):
                    pct_match = re.search(r'([\d.]+)\s*%', lines[j])
                    if pct_match:
                        returns.append({
                            'period_label': label,
                            'return_pct': _clean_pct(pct_match.group(1) + '%'),
                        })
                        break
                break

    if not returns:
        return None

    return {
        'currency': currency,
        'since_date': since_date,
        'returns': returns,
    }


# ---------------------------------------------------------------------------
# Charges parser
# ---------------------------------------------------------------------------

def _parse_charges_section(text: str, currency: str) -> list[dict[str, Any]]:
    """Parse the charges section for a given currency."""
    charges: list[dict[str, Any]] = []

    # Operating charges
    start, end = _find_section_bounds(text, OPERATING_CHARGES_HEADER,
                                      end_patterns=[TRANSACTION_CHARGES_HEADER, CHARGES_HEADER])
    if start != -1:
        lines = text.split('\n')[start:end]
        values = _read_table_values(lines, ['Operating Charges'])
        charges.append({
            'currency': currency,
            'category': 'Operating Charges',
            'amount': values.get('Operating Charges', Decimal('0.00')),
            'is_subtotal': False,
        })

    # Commissions - Equities/options
    start, end = _find_section_bounds(text, COMMISSIONS_HEADER,
                                      end_patterns=[TOTAL_CHARGES_HEADER, CHARGES_HEADER])
    if start != -1:
        lines = text.split('\n')[start:end]
        values = _read_table_values(lines, ['Commissions'])
        charges.append({
            'currency': currency,
            'category': 'Commissions - Equities/options',
            'amount': values.get('Commissions', Decimal('0.00')),
            'is_subtotal': False,
        })

    # Total transaction charges
    start, end = _find_section_bounds(text, TOTAL_CHARGES_HEADER,
                                      end_patterns=[TOTAL_PAID_HEADER, CHARGES_HEADER])
    if start != -1:
        lines = text.split('\n')[start:end]
        values = _read_table_values(lines, ['Total transaction charges'])
        charges.append({
            'currency': currency,
            'category': 'Total transaction charges',
            'amount': values.get('Total transaction charges', Decimal('0.00')),
            'is_subtotal': True,
        })

    # Total paid by you
    start, end = _find_section_bounds(text, TOTAL_PAID_HEADER,
                                      end_patterns=[TRAILING_COMMISSION_HEADER, CHARGES_HEADER])
    if start != -1:
        lines = text.split('\n')[start:end]
        values = _read_table_values(lines, ['Total paid by you'])
        charges.append({
            'currency': currency,
            'category': 'Total paid by you',
            'amount': values.get('Total paid by you', Decimal('0.00')),
            'is_subtotal': True,
        })

    return charges


# ---------------------------------------------------------------------------
# Compensation parser
# ---------------------------------------------------------------------------

def _parse_compensation_section(text: str, currency: str) -> list[dict[str, Any]]:
    """Parse the third-party compensation section for a given currency."""
    compensation: list[dict[str, Any]] = []

    # Trailing commission
    start, end = _find_section_bounds(text, TRAILING_COMMISSION_HEADER,
                                      end_patterns=[OTHER_COMPENSATION_HEADER, TOTAL_COMPENSATION_HEADER, CHARGES_HEADER])
    if start != -1:
        lines = text.split('\n')[start:end]
        values = _read_table_values(lines, ['Trailing commission'])
        compensation.append({
            'currency': currency,
            'type': 'Trailing commission',
            'amount': values.get('Trailing commission', Decimal('0.00')),
        })

    # Other compensation
    start, end = _find_section_bounds(text, OTHER_COMPENSATION_HEADER,
                                      end_patterns=[TOTAL_COMPENSATION_HEADER, CHARGES_HEADER])
    if start != -1:
        lines = text.split('\n')[start:end]
        values = _read_table_values(lines, ['Other compensation'])
        compensation.append({
            'currency': currency,
            'type': 'Other compensation',
            'amount': values.get('Other compensation', Decimal('0.00')),
        })

    # Total compensation from third parties
    start, end = _find_section_bounds(text, TOTAL_COMPENSATION_HEADER,
                                      end_patterns=[CHARGES_HEADER])
    if start != -1:
        lines = text.split('\n')[start:end]
        values = _read_table_values(lines, ['Total compensation from third parties'])
        compensation.append({
            'currency': currency,
            'type': 'Total compensation from third parties',
            'amount': values.get('Total compensation from third parties', Decimal('0.00')),
        })

    return compensation


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_rbc_annual_report(file_path: str) -> dict[str, Any]:
    """Parse an RBC Annual Report PDF and return structured data.

    Returns:
        {
            'broker': str,  # Always 'RBC'
            'report_year': int,
            'report_period_start': date,
            'report_period_end': date,
            'account_number': str,
            'account_type_code': str,  # e.g. 'RIF', 'LIF', 'RRSP', 'IF'
            'balances': [...],
            'performance': [...],
            'charges': [...],
            'compensation': [...],
        }
    """
    # Extract text
    if not os.path.exists(file_path):
        raise ValueError(f"File not found: {file_path}")

    raw_text = _extract_text_from_pdf(file_path)
    cleaned_text = _clean_rbc_text(raw_text)

    # --- Account metadata ---
    account_number = ''
    # RBC account numbers are XXX-YYYYY format (3-digit prefix, 3-5 digit suffix).
    # The PDF may have the text split across lines, e.g.:
    #   "RRSP Accou\nnt\n695-\n62\n2\n59"
    # Strategy: search for "Account" (possibly split as "Accou\nnt") followed by
    # a 3-digit prefix and digit groups that form the suffix.
    # Also try a broader pattern: any 3-digit number followed by dash and more digits.
    # First, try the "Account" pattern with possible line breaks.
    for acct_match in re.finditer(r'A[\s\n]*c[\s\n]*c[\s\n]*o[\s\n]*u[\s\n]*n[\s\n]*t', cleaned_text):
        acct_pos = acct_match.start()
        # Look ahead up to 50 chars (may span multiple lines)
        window = cleaned_text[acct_pos:acct_pos + 50]
        digit_groups = re.findall(r'\d+', window)
        if digit_groups:
            prefix = digit_groups[0]
            suffix = ''.join(digit_groups[1:])
            if prefix and len(prefix) == 3 and suffix and 3 <= len(suffix) <= 5:
                account_number = f"{prefix}-{suffix}"
                break
    # Fallback: if no account found via "Account" pattern, try filename-based extraction.
    # Filenames look like "Annual Report-2259 2022-12-31.pdf" where 2259 is the last 4 digits.
    if not account_number:
        basename = os.path.basename(file_path)
        fname_match = re.search(r'Annual\s*Report[-\s]*(\d{4})', basename, re.IGNORECASE)
        if fname_match:
            suffix4 = fname_match.group(1)
            # Store suffix for later matching in the management command
            account_number = f'FALLBACK-{suffix4}'

    account_type_code = ''
    # After cleaning, the name line may look like "ELSIESMARAISRRIFAccount"
    # or be split across lines: "ARAISR\nRSP Accou\nnt"
    # Try known account type codes as suffixes before "Account" (possibly split).
    # Use [\s\n]* (zero-or-more) between each character so both contiguous
    # (e.g. "RRSP") and split (e.g. "R\nRSP") forms are matched.
    for code in ['RRIF', 'RRSP', 'RIF', 'LIF', 'LRIF', 'PRIF', 'TFSA', 'LIRA', 'RESP', 'IF']:
        code_pattern = r'[\s\n]*'.join(re.escape(c) for c in code)
        account_pattern = r'[\s\n]*'.join(['A', 'c', 'c', 'o', 'u', 'n', 't'])
        pattern = code_pattern + r'[\s\n]*' + account_pattern
        if re.search(pattern, cleaned_text, re.IGNORECASE):
            account_type_code = code
            break

    # --- Period ---
    period_match = PERIOD_HEADER.search(cleaned_text)
    report_period_start = None
    report_period_end = None
    report_year = 2025  # default
    if period_match:
        try:
            report_period_start = _parse_full_date(period_match.group(1), 2025)
            report_period_end = _parse_full_date(period_match.group(2), 2025)
            report_year = report_period_end.year
        except ValueError as e:
            logger.warning("Could not parse report period: %s", e)

    # --- Split into performance and charges sections ---
    perf_start, perf_end = _find_section_bounds(cleaned_text, PERFORMANCE_HEADER,
                                                 end_patterns=[CHARGES_HEADER])
    charges_start, charges_end = _find_section_bounds(cleaned_text, CHARGES_HEADER)

    _cleaned_lines = cleaned_text.split('\n')
    perf_text = '\n'.join(_cleaned_lines[perf_start:perf_end]) if perf_start != -1 else cleaned_text
    charges_text = '\n'.join(_cleaned_lines[charges_start:charges_end]) if charges_start != -1 else ''

    # --- Split perf_text into currency sections ---
    _perf_lines = perf_text.split('\n')
    _cad_start = next((i for i, l in enumerate(_perf_lines) if CAD_ACCOUNT_HEADER.search(l)), None)
    _usd_start = next((i for i, l in enumerate(_perf_lines) if USD_ACCOUNT_HEADER.search(l)), None)

    if _cad_start is not None:
        _cad_end = _usd_start if _usd_start is not None else len(_perf_lines)
        cad_section_text = '\n'.join(_perf_lines[_cad_start:_cad_end])
    elif _usd_start is None:
        # No currency headers found — treat entire perf_text as single section
        cad_section_text = perf_text
    else:
        cad_section_text = ''
    usd_section_text = '\n'.join(_perf_lines[_usd_start:]) if _usd_start is not None else ''

    # --- Balances ---
    balances: list[dict[str, Any]] = []
    if cad_section_text:
        cad_bal = _parse_balances_section(cad_section_text, 'CAD')
        if cad_bal:
            balances.append(cad_bal)
    if usd_section_text:
        usd_bal = _parse_balances_section(usd_section_text, 'USD')
        if usd_bal:
            balances.append(usd_bal)

    # --- Performance ---
    performance: list[dict[str, Any]] = []
    if cad_section_text:
        cad_perf = _parse_performance_section(cad_section_text, 'CAD')
        if cad_perf:
            performance.append(cad_perf)
    if usd_section_text:
        usd_perf = _parse_performance_section(usd_section_text, 'USD')
        if usd_perf:
            performance.append(usd_perf)

    # --- Charges ---
    charges: list[dict[str, Any]] = []
    if charges_text:
        # Only parse CAD charges if CAD section exists
        if re.search(r'Paid\s+by\s+you\s*\(CAD\)', charges_text, re.IGNORECASE):
            cad_charges = _parse_charges_section(charges_text, 'CAD')
            if cad_charges:
                charges.extend(cad_charges)
        # Only parse USD charges if USD section exists
        if re.search(r'Paid\s+by\s+you\s*\(USD\)', charges_text, re.IGNORECASE):
            usd_charges = _parse_charges_section(charges_text, 'USD')
            if usd_charges:
                charges.extend(usd_charges)

    # --- Compensation ---
    compensation: list[dict[str, Any]] = []
    if charges_text:
        # Only parse CAD compensation if CAD section exists
        if re.search(r'Third-party\s+compensation\s*\(CAD\)', charges_text, re.IGNORECASE):
            cad_comp = _parse_compensation_section(charges_text, 'CAD')
            if cad_comp:
                compensation.extend(cad_comp)
        # Only parse USD compensation if USD section exists
        if re.search(r'Third-party\s+compensation\s*\(USD\)', charges_text, re.IGNORECASE):
            usd_comp = _parse_compensation_section(charges_text, 'USD')
            if usd_comp:
                compensation.extend(usd_comp)

    return {
        'broker': 'RBC',
        'report_year': report_year,
        'report_period_start': report_period_start or date(2025, 1, 1),
        'report_period_end': report_period_end or date(2025, 12, 31),
        'account_number': account_number,
        'account_type_code': account_type_code,
        'balances': balances,
        'performance': performance,
        'charges': charges,
        'compensation': compensation,
    }
