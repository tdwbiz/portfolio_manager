"""RBC PDF statement parser using pdfplumber text extraction + regex."""

import logging
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

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

# Asset Review holdings table (layout per docs/audit/05-text-parser-check.md):
# a header line carrying "SECURITY … QUANTITY", then holding rows whose fixed
# columns are right-anchored:
#   ... DESCRIPTION SYMBOL QTY MKT_PRICE BOOK_COST $MARKET_VALUE
ASSET_REVIEW_TABLE_HEADER = re.compile(r'SECURITY\s+QUANTITY', re.IGNORECASE)
HOLDINGS_FOOTNOTES_HEADER = re.compile(r'^\s*FOOTNOTES\b', re.IGNORECASE)
_HOLDING_SYMBOL = re.compile(r'^[A-Z][A-Z0-9.\-]{1,9}$')
# A trailing '-' marks a negative value (short option position) in the
# quantity, book cost and market value columns — never in the price column.
_HOLDING_QTY = re.compile(r'^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?-?$')
_HOLDING_PRICE = re.compile(r'^\d+(?:\.\d+)?$')
_HOLDING_MONEY = re.compile(r'^[\d,]+\.\d+-?$')
_HOLDING_MV = re.compile(r'^\$[\d,]+\.\d+-?$')
# Option security name — the FULL pattern only (tdw Oct 28, extra A): CALL
# or PUT, optional space, underlying symbol, expiry date MM/DD/YY and
# strike. Never a description that merely starts with CALL or PUT, so
# holdings like CALLAWAY or PUTNAM funds are not read as options.
_HOLDING_OPTION = re.compile(
    r'^(?:CALL|PUT)\s?[A-Z][A-Z0-9.\-]*\s+\d{2}/\d{2}/\d{2}\s+\d+(?:\.\d+)?$',
    re.IGNORECASE,
)

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

# Match full month names or abbreviations (including SEPT), optional dot, then day and
# year. The year must be a whole 4-digit number: the lookarounds keep the last four
# digits of a longer number from counting as a year.
STATEMENT_DATE_PATTERN = re.compile(
    r'\b(' + '|'.join(MONTH_ABBREVS) + r')\.?\s+(\d{1,2})\s+(?<!\d)(\d{4})(?!\d)',
    re.IGNORECASE,
)

# Two-line shape: the year sits at the end of the next line, again as a whole
# 4-digit number (word boundary on both sides).
_STATEMENT_YEAR_EOL = re.compile(r'(?<!\d)(\d{4})(?!\d)\s*$')


def _statement_year_ok(year: int) -> bool:
    """Sanity window for a statement year: 2000 .. next year (inclusive)."""
    return 2000 <= year <= date.today().year + 1

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


def _extract_date_from_filename(file_name: str) -> date | None:
    """Extract statement date from RBC PDF filename.

    Handles multiple filename formats:
    - YYYY-MM-DD (e.g., 'Statement-0003 2025-09-30.pdf')
    - YYYYMonDD (e.g., '90000004-2014Apr30-2014Apr30.pdf')
    """
    if not file_name:
        return None

    # Try YYYY-MM-DD format first
    fn_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', file_name)
    if fn_match:
        try:
            return date(int(fn_match.group(1)), int(fn_match.group(2)), int(fn_match.group(3)))
        except ValueError:
            pass

    # Try YYYYMonDD format (e.g., 2014Apr30, 2019Dec31)
    fn_match = re.search(r'(\d{4})(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEPT|SEP|OCT|NOV|DEC)(\d{1,2})', file_name, re.IGNORECASE)
    if fn_match:
        try:
            year = int(fn_match.group(1))
            month_str = fn_match.group(2)
            day = int(fn_match.group(3))
            return _parse_rbc_date(f"{month_str} {day}", year)
        except (ValueError, IndexError):
            pass

    return None


def _extract_statement_date(lines: list[str]) -> date | None:
    """Extract the statement date from header lines (shared by both entry points).

    Handles the shapes seen in RBC statements:
    - single line: "SEPT 30 2025" (month, day and year together);
    - two lines with the month/day at the start of the line: "SEPT 30" / "2025";
    - two lines with the month/day mid-line — both layouts: e.g.
      "Order Execution Only SEPT 30" / "Cdn. Dollar Statement 2025"
      (the year sits at the end of the next line, which may carry other words).

    The year must be a whole 4-digit number (word boundary — the last four digits
    of a longer number never count) between 2000 and next year; anything else is
    skipped. The "Date of Last Statement" line is excluded in every shape.
    Returns None when no date can be read.
    """
    month_day_pat = re.compile(
        r'\b(' + '|'.join(MONTH_ABBREVS) + r')\.?\s+(\d{1,2})',
        re.IGNORECASE,
    )
    for i, line in enumerate(lines):
        if 'last statement' in line.lower():
            continue

        # Single-line match: "SEPT 30 2025"
        date_match = STATEMENT_DATE_PATTERN.search(line)
        if date_match:
            year = int(date_match.group(3))
            if not _statement_year_ok(year):
                logger.warning(
                    "Statement date year outside 2000..next year: %s",
                    date_match.group(0),
                )
                continue
            try:
                return _parse_rbc_date(
                    f"{date_match.group(1)} {int(date_match.group(2))}",
                    year,
                )
            except ValueError:
                logger.warning("Could not parse statement date: %s", date_match.group(0))
                continue

        # Two-line match: month+day anywhere on this line, the year at the end
        # of the next line (a bare "2025" line ends with its year too).
        if i + 1 < len(lines):
            month_match = month_day_pat.search(line)
            year_match = _STATEMENT_YEAR_EOL.search(lines[i + 1].strip())
            if month_match and year_match:
                year = int(year_match.group(1))
                if not _statement_year_ok(year):
                    logger.warning(
                        "Statement date year outside 2000..next year: %s",
                        year_match.group(0),
                    )
                    continue
                try:
                    return _parse_rbc_date(
                        f"{month_match.group(1)} {int(month_match.group(2))}",
                        int(year_match.group(1)),
                    )
                except ValueError:
                    logger.warning(
                        "Could not parse statement date: %s %s",
                        month_match.group(0), year_match.group(0),
                    )
                    continue
    return None


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
# Currency section splitting
# ---------------------------------------------------------------------------

# Comprehensive patterns for CAD statement headers
# RBC PDFs have multiple formats:
#   - "RRSP Statement (CDN$) 2026"
#   - "TFSA Statement (CDN$) 2026"
#   - "LIF Statement (CDN$) 2026"
#   - "FHSA Statement (CDN$) 2026"
#   - "RRIF Statement (CDN$) 2025"
#   - "Cdn. Dollar Statement 2022"
#   - "Your Canadian dollar account" (Annual Investment Performance Report format)
CAD_STATEMENT_PATTERNS = [
    re.compile(r'(?:RRSP|TFSA|LIF|FHSA|RRIF)\s+Statement\s+\(CDN\$\)', re.IGNORECASE),
    re.compile(r'Cdn\.\s*Dollar\s+Statement', re.IGNORECASE),
    re.compile(r'Your\s+Canadian\s+dollar\s+account', re.IGNORECASE),
]

# Comprehensive patterns for USD statement headers
#   - "RRSP Statement (U.S.$) 2026"
#   - "TFSA Statement (U.S.$) 2026"
#   - "LIF Statement (U.S.$) 2026"
#   - "FHSA Statement (U.S.$) 2026"
#   - "RRIF Statement (U.S.$) 2025"
#   - "U.S. Dollar Statement"
#   - "Your U.S. dollar account" (Annual Investment Performance Report format)
USD_STATEMENT_PATTERNS = [
    re.compile(r'(?:RRSP|TFSA|LIF|FHSA|RRIF)\s+Statement\s+\(U\.S\.\$\)', re.IGNORECASE),
    re.compile(r'U\.S\.\s*Dollar\s+Statement', re.IGNORECASE),
    re.compile(r'Your\s+U\.S\.\s*dollar\s+account', re.IGNORECASE),
    re.compile(r'Your\s+US\s+dollar\s+account', re.IGNORECASE),
]

def _is_cad_header(line: str) -> bool:
    """Check if a line is a CAD statement header."""
    return any(p.search(line) for p in CAD_STATEMENT_PATTERNS)

def _is_usd_header(line: str) -> bool:
    """Check if a line is a USD statement header."""
    return any(p.search(line) for p in USD_STATEMENT_PATTERNS)


def _split_pdf_into_sections(full_text: str) -> list[tuple[str, str]]:
    """Split PDF text into CAD and USD statement sections.

    RBC PDFs often contain two separate statements concatenated:
    - First statement: CAD (pages 1-N)
    - Second statement: USD (pages N+1-M)

    RBC PDFs have multiple header formats:
      CAD: "RRSP Statement (CDN$) 2026", "Cdn. Dollar Statement 2022",
           "Your Canadian dollar account"
      USD: "RRSP Statement (U.S.$) 2026", "U.S. Dollar Statement",
           "Your U.S. dollar account"

    This function splits the text at currency-changing headers,
    returning a list of (currency, text) tuples.

    Returns:
        List of (currency, text) tuples. Always includes at least
        one CAD section. If USD sections are found, they're returned
        as additional tuples.
    """
    lines = full_text.split('\n')
    sections = []
    current_currency = 'CAD'
    current_lines = []

    for i, line in enumerate(lines):
        # Check if this line starts a new currency section
        if _is_usd_header(line) and current_currency == 'CAD':
            # Switching from CAD to USD - save CAD section
            if current_lines:
                sections.append(('CAD', '\n'.join(current_lines)))
            current_currency = 'USD'
            current_lines = [line]
        elif _is_cad_header(line) and current_currency == 'USD':
            # Switching from USD to CAD (rare but possible) - save USD section
            if current_lines:
                sections.append(('USD', '\n'.join(current_lines)))
            current_currency = 'CAD'
            current_lines = [line]
        else:
            current_lines.append(line)

    # Add the last section
    if current_lines:
        sections.append((current_currency, '\n'.join(current_lines)))

    # If no sections were found, return the whole text as CAD
    if not sections:
        return [('CAD', full_text)]

    return sections


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

def _find_section(text: str, pattern: re.Pattern) -> tuple[int, int]:
    """Return (start, end) indices for the section defined by *pattern*.

    *start* is the line index where the header is found.
    *end* is the line index of the next header or end of text.
    
    For multi-page sections (like Account Activity that repeats on each page),
    returns the range from the first header to the last occurrence.
    """
    lines = text.split('\n')
    
    # Find all occurrences of the header
    header_indices = [i for i, line in enumerate(lines) if pattern.search(line)]
    if not header_indices:
        return -1, -1

    start_idx = header_indices[0]
    
    # For sections that repeat across pages (like Account Activity),
    # find the last occurrence and extend to the next different section
    if len(header_indices) > 1:
        # Use the last occurrence as start, then find what comes after
        start_idx = header_indices[-1]
    
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


def _parse_asset_summary(text: str) -> dict[str, Any]:
    """Parse the Asset Summary table.

    Returns a dict with:
      - 'balances': list of asset category dicts (Cash, Common Shares, etc.)
      - 'begin_total': opening total from 'Total on [prev date]' footer line
      - 'end_total': closing total from 'Total on [curr date]' line
    """
    start, end = _find_section(text, ASSET_SUMMARY_HEADER)
    if start == -1:
        return {'balances': [], 'begin_total': Decimal('0.00'), 'end_total': Decimal('0.00')}

    lines = text.split('\n')[start:end]
    results = []
    begin_total = Decimal('0.00')
    end_total = Decimal('0.00')

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('---') or stripped.startswith('___'):
            continue
        if ASSET_SUMMARY_HEADER.search(stripped):
            continue

        # Check for "Total on [date]" footer lines FIRST (before asset row regex)
        # Format: "Total on MAY 31 $420.85 100.00 %" or "Total on APR. 30 $420.85"
        # NOTE: PDF text extraction may prepend address text (e.g., "Toronto, ON Total on...")
        # so we use re.search instead of re.match to find "Total on" anywhere in the line.
        total_match = re.search(
            r'Total on\s+(.+?)\s+\$([\d,]+\.\d+)(?:\s+([\d.]+)\s*%)?',
            stripped, re.IGNORECASE,
        )
        if total_match:
            date_str = total_match.group(1).strip()
            total_value = _clean_currency('$' + total_match.group(2))
            # Determine if this is BEGINNING or ENDING total based on percentage
            # The "Total on MAY 31" line has the % sign and is the ENDING total
            # The "Total on APR. 30" line has no % and is the BEGINNING total
            if total_match.group(3):
                # Has percentage → this is the ENDING total (current statement date)
                end_total = total_value
            else:
                # No percentage → this is the BEGINNING total (previous statement date)
                begin_total = total_value
            continue

        match = re.match(
            r'^(\w[\w\s]*?)\s+\$([\d,]+\.\d+)\s+([\d.]+)\s*%?',
            stripped, re.IGNORECASE,
        )
        if match:
            asset_class = match.group(1).strip()
            # Handle false positives: postal addresses may precede the category name
            # e.g., "200 Bay Street Other" where "Other" is the actual category
            address_pattern = r'\b\d+\s+[A-Z][a-z]+\s+(Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Lane|Ln|Way|Court|Ct|Place|Pl)\b'
            addr_match = re.search(address_pattern, asset_class, re.IGNORECASE)
            if addr_match:
                # Extract the category name after the address
                after_addr = asset_class[addr_match.end():].strip()
                if after_addr:
                    # The category is after the address (e.g., "Other" from "200 Bay Street Other")
                    asset_class = after_addr
                else:
                    # No category after address — skip this line (pure address false positive)
                    logger.debug("Skipping false positive asset summary line: %s", stripped)
                    continue
            market_value = _clean_currency('$' + match.group(2))
            percentage = _clean_pct(match.group(3) + '%')
            results.append({
                'account_type': asset_class,
                'opening': Decimal('0.00'),
                'closing': market_value,
                'market_value': market_value,
                'percentage': percentage,
            })

    return {'balances': results, 'begin_total': begin_total, 'end_total': end_total}


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


def _parse_asset_review(text: str) -> list[dict[str, Any]]:
    """Parse the Asset Review holdings table.

    Returns one dict per holding row with the same keys as the VL parser's
    holdings (``_coerce_response`` in ``imports/parsers_monthly_vl.py``):
    ``security``, ``symbol``, ``quantity``, ``book_cost``, ``market_price``,
    ``market_value``.

    The fixed columns are right-anchored on each row; the security description
    may span continuation lines and carries no dollar value. Total lines are
    skipped in both layouts (recent: "Total Value of …", old: joined words like
    "TotalValueof…").

    A column that cannot be read is returned as ``None`` — never 0 as a
    placeholder; the self-check (``self_check_sections``) fails on it and the
    file goes to the VL parser.
    """
    lines = text.split('\n')
    start = None
    for i, line in enumerate(lines):
        if ASSET_REVIEW_TABLE_HEADER.search(line):
            start = i
            break
    if start is None:
        return []

    # End of the table: the next statement section or the footnotes block.
    end = len(lines)
    for j in range(start + 1, len(lines)):
        candidate = lines[j]
        if (ACCOUNT_ACTIVITY_HEADER.search(candidate)
                or HOLDINGS_FOOTNOTES_HEADER.match(candidate.strip())
                or ASSET_SUMMARY_HEADER.search(candidate)
                or CASH_BALANCE_HEADER.search(candidate)
                or INCOME_SUMMARY_HEADER.search(candidate)
                or CONTRIBUTION_HEADER.search(candidate)):
            end = j
            break

    def _decimal(pattern: re.Pattern, value: str) -> Decimal | None:
        if not pattern.match(value):
            return None
        # A trailing '-' negates the value (short option position).
        negative = value.endswith('-')
        try:
            result = Decimal(
                value.replace('$', '').replace(',', '').rstrip('-')
            )
        except InvalidOperation:
            return None
        return -result if negative else result

    holdings: list[dict[str, Any]] = []
    for line in lines[start + 1:end]:
        stripped = line.strip()
        if not stripped or set(stripped) <= {'-', '_', ' '}:
            continue
        # Total lines — recent layout "Total Value of …", old layout joined words.
        if re.match(r'(?i)^total', stripped):
            continue
        # A holding row carries the $ market value; description continuation
        # lines and category headers do not.
        tokens = stripped.split()
        if not any(_HOLDING_MV.match(t) for t in tokens):
            continue
        if len(tokens) >= 5 and _HOLDING_MV.match(tokens[-1]):
            symbol_s, qty_s, price_s, cost_s, mv_s = tokens[-5:]
            if (_HOLDING_SYMBOL.match(symbol_s) and _HOLDING_QTY.match(qty_s)
                    and _HOLDING_PRICE.match(price_s)
                    and _HOLDING_MONEY.match(cost_s)):
                holdings.append({
                    'security': ' '.join(tokens[:-5]),
                    'symbol': symbol_s,
                    'quantity': _decimal(_HOLDING_QTY, qty_s),
                    'book_cost': _decimal(_HOLDING_MONEY, cost_s),
                    'market_price': _decimal(_HOLDING_PRICE, price_s),
                    'market_value': _decimal(_HOLDING_MV, mv_s),
                })
                continue
            # Option row: the description is the full CALL/PUT pattern (no
            # separate symbol token) and the fixed columns are the four
            # QTY PRICE BOOK_COST $MARKET_VALUE — a trailing '-' on any of
            # them marks a negative (short position).
            desc = ' '.join(tokens[:-4])
            qty_s, price_s, cost_s, mv_s = tokens[-4:]
            if (_HOLDING_OPTION.match(desc)
                    and _HOLDING_QTY.match(qty_s)
                    and _HOLDING_PRICE.match(price_s)
                    and _HOLDING_MONEY.match(cost_s)):
                holdings.append({
                    'security': desc,
                    'symbol': '',
                    'quantity': _decimal(_HOLDING_QTY, qty_s),
                    'book_cost': _decimal(_HOLDING_MONEY, cost_s),
                    'market_price': _decimal(_HOLDING_PRICE, price_s),
                    'market_value': _decimal(_HOLDING_MV, mv_s),
                })
                continue
        # A holding row whose fixed columns cannot be read in order (e.g. a
        # missing column shifts the tokens). Fail loud — never store 0 as a
        # placeholder: the self-check fails on the None fields and the file
        # goes to the VL parser.
        holdings.append({
            'security': stripped,
            'symbol': '',
            'quantity': None,
            'book_cost': None,
            'market_price': None,
            'market_value': _decimal(
                _HOLDING_MV,
                next(t for t in reversed(tokens) if _HOLDING_MV.match(t)),
            ),
        })
    return holdings


def _parse_account_activity(text: str, year: int = 2025) -> list[dict[str, Any]]:
    """Parse the Account Activity table.

    Handles both PyMuPDF (vertical field extraction) and pdfplumber
    (horizontal row extraction) text formats.
    
    Activity types in RBC PDFs: BUY, SELL, SOLD, BOUGHT, DIVIDEND, DIVID,
    INTEREST, TAX, WITHDRAW, TRANSFER. Sometimes the activity type is
    omitted and appears on the continuation line.
    
    Note: Account Activity may span multiple pages with the header repeating.
    We capture from the first header to the next non-Activity section header.
    """
    lines = text.split('\n')
    
    # Find the first Account Activity header
    start_idx = None
    for i, line in enumerate(lines):
        if ACCOUNT_ACTIVITY_HEADER.search(line):
            start_idx = i
            break
    if start_idx is None:
        return []

    # Find the end: next section header that is NOT Account Activity
    # (Account Activity may repeat on multiple pages)
    other_headers = [
        ASSET_SUMMARY_HEADER, CASH_BALANCE_HEADER,
        INCOME_SUMMARY_HEADER, CONTRIBUTION_HEADER,
        ASSET_REVIEW_HEADER,
    ]
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        for hdr in other_headers:
            if hdr.search(lines[j]):
                end_idx = j
                break
        if end_idx != len(lines):
            break

    section_lines = lines[start_idx:end_idx]
    results = []

    # Month abbreviation pattern for date parsing
    MONTH_PAT = r'(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEPT|SEP|OCT|NOV|DEC)'
    # Match date + optional activity type (BUY/SELL/SOLD/BOUGHT/DIVIDEND/DIVID/INTEREST/TAX/WITHDRAW/TRANSFER)
    date_pat = re.compile(
        r'^(' + MONTH_PAT + r')[\.\s]+(\d{1,2})\s+'
        r'(BUY|SELL|SOLD|BOUGHT|DIVIDEND|DIVID|INTEREST|TAX|WITHDRAW|TRANSFER)?\s*'
        r'(.*)',
        re.IGNORECASE
    )

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith('---') or line.startswith('___'):
            i += 1
            continue
        if ACCOUNT_ACTIVITY_HEADER.search(line):
            i += 1
            continue

        # Match date + optional activity type
        date_match = date_pat.match(line)
        if not date_match:
            i += 1
            continue

        month_str = date_match.group(1)
        day_str = date_match.group(2)
        activity_type = (date_match.group(3) or '').upper()
        rest = date_match.group(4).strip()

        # If no activity type on this line, check if it's a continuation-style transaction
        # (e.g., "JAN. 23 VANGUARD FTSE CDN HIGH 0.17 272.76" - no activity type)
        if not activity_type:
            # Look ahead for activity type in continuation lines
            for j in range(i + 1, min(i + 5, len(lines))):
                cont = lines[j].strip().upper()
                if 'DIVID' in cont or 'DIVIDEND' in cont:
                    activity_type = 'DIVIDEND'
                    break
            
            # If still no activity type, skip (likely a non-transaction line)
            if not activity_type:
                i += 1
                continue
            
            # No rest of line - the description starts on the next line
            rest = ''

        # Collect continuation lines that don't end with numbers
        # (pure description continuations like "ETF UNIT CL A")
        while i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if next_line and not re.match(r'^[A-Z]{2,4}\.\s+\d{1,2}\s', next_line, re.IGNORECASE):
                # Check if line ends with numbers (financial data or account info)
                if re.search(r'\d[\d,]*\.?\d*\s*$', next_line):
                    break  # Don't consume - might be financial data
                i += 1
                rest += ' ' + next_line
            else:
                break

        # Extract financial numbers from rest
        tokens = rest.split()

        # Separate plain numeric tokens (price/quantity) from dollar-sign tokens (debit/credit).
        # RBC column order: PRICE/RATE, QUANTITY, DEBIT($) or CREDIT($)
        plain_tokens = []   # no $ prefix: price/rate, quantity
        dollar_tokens = []  # $ prefix: debit or credit column
        first_token_idx = None
        for idx, token in enumerate(tokens):
            is_dollar = token.startswith('$')
            clean = token.lstrip('$').replace(',', '').replace('-', '')
            # Must be purely numeric (digits, dots) and more than 1 char
            if re.match(r'^\d[\d,.]*$', clean) and len(clean) > 1:
                # Exclude 2-digit numbers (days) and 4-digit years
                if clean.isdigit():
                    if len(clean) == 2:
                        continue
                    elif len(clean) == 4 and 2020 <= int(clean) <= 2030:
                        continue
                if first_token_idx is None:
                    first_token_idx = idx
                if is_dollar:
                    dollar_tokens.append(clean)
                else:
                    plain_tokens.append(clean)

        if not plain_tokens and not dollar_tokens:
            i += 1
            continue

        # Description is everything before the first numeric token
        description = ' '.join(tokens[:first_token_idx]).strip() if first_token_idx is not None else ''

        # Map financial tokens to fields based on activity type
        quantity_str = None
        price_str = None
        debit_str = None
        credit_str = None

        if activity_type in ('BUY', 'BOUGHT'):
            # Columns: PRICE/RATE, QUANTITY, DEBIT($)
            price_str = plain_tokens[0] if len(plain_tokens) >= 1 else None
            quantity_str = plain_tokens[1] if len(plain_tokens) >= 2 else None
            debit_str = dollar_tokens[0] if dollar_tokens else None

        elif activity_type in ('SELL', 'SOLD'):
            # Columns: PRICE/RATE, QUANTITY, CREDIT($)
            price_str = plain_tokens[0] if len(plain_tokens) >= 1 else None
            quantity_str = plain_tokens[1] if len(plain_tokens) >= 2 else None
            credit_str = dollar_tokens[0] if dollar_tokens else None

        elif activity_type in ('INTEREST', 'DIVIDEND', 'DIVID'):
            # Columns: PRICE/RATE, QUANTITY, CREDIT($) — use last dollar token (the credit column)
            price_str = plain_tokens[0] if len(plain_tokens) >= 1 else None
            quantity_str = plain_tokens[1] if len(plain_tokens) >= 2 else None
            credit_str = dollar_tokens[-1] if dollar_tokens else (plain_tokens[-1] if plain_tokens else None)

        elif activity_type == 'TAX':
            # Tax: single debit amount (withheld)
            debit_str = dollar_tokens[0] if dollar_tokens else (plain_tokens[0] if plain_tokens else None)

        elif activity_type == 'WITHDRAW':
            # Withdraw: single credit amount
            credit_str = dollar_tokens[0] if dollar_tokens else (plain_tokens[0] if plain_tokens else None)

        elif activity_type == 'TRANSFER':
            # Transfer: quantity only (no money changes account)
            quantity_str = plain_tokens[0] if plain_tokens else None

        try:
            tx_date = _parse_rbc_date(f"{month_str} {day_str}", year)
        except (ValueError, IndexError):
            i += 1
            continue

        # Clean trailing dash from quantity (e.g., "100-" -> "100")
        if quantity_str:
            quantity_str = quantity_str.replace('-', '')
        
        # Validate and convert financial tokens
        def safe_decimal(val, label=''):
            if not val:
                return None
            try:
                return Decimal(val.replace(',', ''))
            except InvalidOperation:
                logger.warning("Invalid %s value '%s' in %s %s", label, val, activity_type, description[:50])
                return None
        
        price = safe_decimal(price_str, 'price')
        quantity = safe_decimal(quantity_str, 'quantity')
        debit = safe_decimal(debit_str, 'debit')
        credit = safe_decimal(credit_str, 'credit')

        # Symbol extraction disabled - current regex grabs random uppercase words.
        # Will be reimplemented later using LLM to look up symbols from transaction data.
        symbol = ''

        # Build the full raw line for notes (everything from date to end of continuations)
        raw_line = f"{month_str}.{day_str} {rest.strip()}" if rest.strip() else f"{month_str}.{day_str}"

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
            'notes': raw_line,
        })

        i += 1

    return results


# ---------------------------------------------------------------------------
# Self-check (step 12b)
# ---------------------------------------------------------------------------

CENT = Decimal('0.01')
_PRICE_TOLERANCE = Decimal('0.001')  # 0.1 percent
_LAST_STATEMENT_MAX_GAP_DAYS = 100


def _count_currency_sections(full_text: str) -> int:
    """Count the currency sections present in the whole statement text.

    Each currency statement carries exactly one Asset Summary table; when no
    Asset Summary header is present, fall back to the Asset Review holdings
    table headers ("SECURITY … QUANTITY"). Returns 0 when neither is found —
    the caller then skips the count check rather than guessing.
    """
    lines = full_text.split('\n')
    n = sum(1 for line in lines if ASSET_SUMMARY_HEADER.search(line))
    if n:
        return n
    return sum(1 for line in lines if ASSET_REVIEW_TABLE_HEADER.search(line))


def _section_cash(section: dict) -> Decimal:
    """Closing cash for one currency section (same source as the 12a check)."""
    return sum(
        (Decimal(b.get('market_value') or 0)
         for b in section.get('balances') or []
         if str(b.get('account_type', '')).strip().lower() == 'cash'),
        Decimal('0'),
    )


def self_check_sections(
    sections: list[dict], full_text: str | None = None,
) -> list[str]:
    """Self-check the text parser's sections before they may be stored.

    Returns a list of reasons (empty when the parse is trustworthy). Reasons
    name only fields, currencies and symbols — never values. When
    ``full_text`` (the whole statement text) is given, one more check runs:

    - the number of currency sections returned equals the number present in
      the whole text (Asset Summary / Asset Review table headers counted in
      the full text); the reason names only the counts. Without this, an
      unrecognised section header leaves the second currency inside the first
      section and its holdings, cash and totals are silently lost while the
      first currency's numbers still reconcile (tdw decision, Oct 26).

    Per currency section:

    - ``statement_date`` is not None;
    - when ``last_statement_date`` is found, ``statement_date`` is after it
      and at most 100 days later (tdw decision, Oct 26);
    - every holding has a readable ``book_cost`` (never 0 as a placeholder);
    - per holding, ``quantity * market_price`` is within 0.1 percent of
      ``market_value``;
    - ``sum(holdings.market_value) + cash == end_total_market_value`` to the
      cent.

    No begin-total check: only the end total is derivable from holdings +
    cash on the same statement (tdw decision, Oct 26).
    """
    reasons = []
    if full_text is not None:
        expected = _count_currency_sections(full_text)
        parsed = len(sections or [])
        if expected and parsed != expected:
            reasons.append(
                f'currency sections: {parsed} parsed vs {expected} in text'
            )
    for section in sections or []:
        cur = str(section.get('currency') or 'CAD').upper()
        statement_date = section.get('statement_date')
        if statement_date is None:
            reasons.append(f'{cur} statement_date missing')
        else:
            last_date = section.get('last_statement_date')
            if last_date is not None:
                gap = (statement_date - last_date).days
                if not 1 <= gap <= _LAST_STATEMENT_MAX_GAP_DAYS:
                    reasons.append(
                        f'{cur} statement_date not 1-{_LAST_STATEMENT_MAX_GAP_DAYS}'
                        ' days after last_statement_date'
                    )

        mv_sum = Decimal('0')
        for h in section.get('holdings') or []:
            sym = str(h.get('symbol') or '?')
            qty, price, cost, mv = (h.get('quantity'), h.get('market_price'),
                                    h.get('book_cost'), h.get('market_value'))
            if cost is None:
                reasons.append(f'{cur} {sym} book_cost unreadable')
            if mv is None:
                reasons.append(f'{cur} {sym} market_value unreadable')
                continue
            mv_sum += Decimal(mv)
            if qty is None or price is None:
                reasons.append(f'{cur} {sym} quantity/market_price unreadable')
                continue
            expected = Decimal(qty) * Decimal(price)
            if abs(expected - Decimal(mv)) > _PRICE_TOLERANCE * abs(Decimal(mv)):
                reasons.append(
                    f'{cur} {sym} quantity*market_price vs market_value '
                    'beyond 0.1 percent'
                )

        end_total = section.get('end_total_market_value')
        if (end_total is None
                or (mv_sum + _section_cash(section)).quantize(CENT)
                != Decimal(end_total).quantize(CENT)):
            reasons.append(f'{cur} holdings+cash != end_total_market_value')
    return reasons


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def _extract_text_from_pdf(file_path: str) -> str:
    """Extract text from a real PDF file using pdfplumber.

    Falls back to PyMuPDF if pdfplumber is unavailable, then to plain text.

    Args:
        file_path: Path to the PDF file.

    Returns:
        Extracted text content.
    """
    # Try pdfplumber first (best row-level text extraction)
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            return '\n'.join(
                page.extract_text() or '' for page in pdf.pages
            )
    except Exception:
        pass

    # Fallback to PyMuPDF
    try:
        import pymupdf
        doc = pymupdf.open(file_path)
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

    statement_date = _extract_statement_date(full_text.split('\n'))
    last_statement_date = None

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
        statement_date = _extract_date_from_filename(file_name)
        if statement_date:
            logger.info("Inferred statement date from filename: %s -> %s", file_name, statement_date)

    if statement_date is None:
        logger.warning("Could not determine statement date from PDF content or filename")

    # Parse sections
    try:
        asset_data = _parse_asset_summary(full_text)
    except Exception as e:
        logger.warning("Error parsing asset summary: %s", e)
        asset_data = {'balances': [], 'begin_total': Decimal('0.00'), 'end_total': Decimal('0.00')}

    try:
        cash_balances = _parse_cash_balance(full_text)
    except Exception as e:
        logger.warning("Error parsing cash balance: %s", e)
        cash_balances = []

    # Merge cash balances into balances
    all_balances = []
    cash_types = {b['account_type'] for b in cash_balances}

    for bal in asset_data['balances']:
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
        tx_year = statement_date.year if statement_date else 2025
        transactions = _parse_account_activity(full_text, year=tx_year)
    except Exception as e:
        logger.warning("Error parsing account activity: %s", e)
        transactions = []

    try:
        holdings = _parse_asset_review(full_text)
    except Exception as e:
        logger.warning("Error parsing asset review: %s", e)
        holdings = []

    return {
        'statement_date': statement_date,
        'last_statement_date': last_statement_date,
        'account_number': account_number,
        'fx_rate': fx_rate,
        'begin_total_market_value': asset_data['begin_total'],
        'end_total_market_value': asset_data['end_total'],
        'balances': all_balances,
        'holdings': holdings,
        'transactions': transactions,
        'income_summary': income_summary,
        'contribution_summary': contribution_summary,
    }


def parse_rbc_pdf_multi(file_path: str) -> list[dict]:
    """Parse an RBC PDF statement and return one result per currency section.

    RBC PDFs often contain both CAD and USD holdings. This function splits
    the PDF text at USD section headers and parses each section separately,
    returning a list of parsed results (one per currency).

    Args:
        file_path: Path to the PDF file, or a string of raw text content.

    Returns:
        List of dictionaries, one per currency section. Each dict has keys:
        currency, statement_date, last_statement_date, account_number,
        fx_rate, balances, transactions, income_summary, contribution_summary.
    """
    # Extract full text
    if not os.path.exists(file_path):
        full_text = file_path
    else:
        full_text = _extract_text_from_pdf(file_path)

    # Split into CAD and USD sections
    sections = _split_pdf_into_sections(full_text)

    # Extract metadata from full text (same for all currency sections)
    account_number = ''
    account_match = ACCOUNT_NUMBER_PATTERN.search(full_text)
    if account_match:
        account_number = account_match.group(1)

    fx_rate = Decimal('1.000000')
    fx_match = FX_RATE_PATTERN.search(full_text)
    if fx_match:
        fx_rate = Decimal(fx_match.group(1))

    results = []
    for currency, section_text in sections:
        statement_date = _extract_statement_date(section_text.split('\n'))
        last_statement_date = None

        # Extract last statement date
        last_stmt_match = LAST_STATEMENT_PATTERN.search(section_text)
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
                    last_full_month_str = f"{last_month} {last_day}"
                    last_statement_date = _parse_rbc_date(
                        last_full_month_str,
                        statement_date.year,
                    )
                except ValueError:
                    logger.warning("Could not parse last statement date: %s", last_stmt_str)

        # If we couldn't get a statement date, try filename
        if statement_date is None:
            file_name = os.path.basename(file_path) if os.path.exists(file_path) else ''
            statement_date = _extract_date_from_filename(file_name)
            if statement_date:
                logger.info("Inferred statement date from filename: %s -> %s", file_name, statement_date)

        # Parse section-specific data
        try:
            asset_data = _parse_asset_summary(section_text)
        except Exception as e:
            logger.warning("Error parsing asset summary for %s: %s", currency, e)
            asset_data = {'balances': [], 'begin_total': Decimal('0.00'), 'end_total': Decimal('0.00')}

        try:
            cash_balances = _parse_cash_balance(section_text)
        except Exception as e:
            logger.warning("Error parsing cash balance for %s: %s", currency, e)
            cash_balances = []

        # Merge cash balances
        all_balances = []
        cash_types = {b['account_type'] for b in cash_balances}
        for bal in asset_data['balances']:
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
            income_summary = _parse_income_summary(section_text)
        except Exception as e:
            logger.warning("Error parsing income summary for %s: %s", currency, e)
            income_summary = {
                'dividends_this_month': Decimal('0.00'),
                'dividends_ytd': Decimal('0.00'),
                'interest_this_month': Decimal('0.00'),
                'interest_ytd': Decimal('0.00'),
                'total_this_month': Decimal('0.00'),
                'total_ytd': Decimal('0.00'),
            }

        try:
            contribution_summary = _parse_contribution_summary(section_text)
        except Exception as e:
            logger.warning("Error parsing contribution summary for %s: %s", currency, e)
            contribution_summary = {
                'first_60_days': Decimal('0.00'),
                'balance_of_year': Decimal('0.00'),
            }

        try:
            tx_year = statement_date.year if statement_date else 2025
            transactions = _parse_account_activity(section_text, year=tx_year)
        except Exception as e:
            logger.warning("Error parsing account activity for %s: %s", currency, e)
            transactions = []

        try:
            holdings = _parse_asset_review(section_text)
        except Exception as e:
            logger.warning("Error parsing asset review for %s: %s", currency, e)
            holdings = []

        # Tag transactions with currency
        for tx in transactions:
            tx['currency'] = currency

        results.append({
            'currency': currency,
            'statement_date': statement_date,
            'last_statement_date': last_statement_date,
            'account_number': account_number,
            'fx_rate': fx_rate,
            'begin_total_market_value': asset_data['begin_total'],
            'end_total_market_value': asset_data['end_total'],
            'balances': all_balances,
            'holdings': holdings,
            'transactions': transactions,
            'income_summary': income_summary,
            'contribution_summary': contribution_summary,
        })

    return results
