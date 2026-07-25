"""
RBC Monthly Statement Parser — Pure Text/PyMuPDF (no VL model).

Parses RBC monthly brokerage statements using only text extraction.
Returns: {sections, transactions, holdings, balances, statement_date, account_number}

RBC monthly PDF layout:
  - Page 1: Account Info (Asset Summary, Income Summary, Cash Balance)
  - Page 2: Asset Review (Holdings table) + Account Activity (Transactions)
  - Page 3: Footnotes

Holdings table columns:
  SECURITY | SYMBOL | QUANTITY/SEGREGATED | BOOK COST | MARKET PRICE | MARKET VALUE

Transaction table columns:
  DATE | ACTIVITY | DESCRIPTION | QUANTITY | RATE | DEBIT | CREDIT

Transaction types (fixed choices in Core models):
  BOUGHT, SOLD, DIST, DIVIDEND, DIV REIN, INTEREST_RECEIVED, INTEREST_PAID,
  TRANSFER, TFR OUT, WIRE TFR, CONTRIB, DEPOSIT, WITHDRAWAL, ADJUST, RTC, FEE, OTHER
"""

from __future__ import annotations

import re
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


MONTH_NAMES = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _parse_statement_date(text: str) -> Optional[date]:
    """Extract statement date from page header.

    RBC uses 'DEC. 31' on one line and '2020' on the next line.
    Also handles: 'DEC 31, 2020' or 'December 31, 2020'
    """
    # Pattern: "MONTH DD." followed by year on next line (with various spacing)
    # Handle multiple line-break formats: \n, \r\n, with various whitespace
    m = re.search(
        r'(JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?|'
        r'JUL(?:Y)?|AUG(?:UST)?|SEP(?:TEMBER)?|OCT(?:OBER)?|NOV(?:EMBER)?|'
        r'DEC(?:EMBER)?)\.?\s+(\d{1,2})\.?\s*\r?\n\s*(\d{4})',
        text, re.IGNORECASE,
    )
    if m:
        month_str = m.group(1)[:3].upper()
        day = int(m.group(2))
        year = int(m.group(3))
        month_num = MONTH_NAMES.get(month_str)
        if month_num:
            try:
                return date(year, month_num, day)
            except ValueError:
                return None

    # Pattern: "MONTH DD, YYYY" or "MONTH DD. YYYY" on same line
    m = re.search(
        r'(JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?|'
        r'JUL(?:Y)?|AUG(?:UST)?|SEP(?:TEMBER)?|OCT(?:OBER)?|NOV(?:EMBER)?|'
        r'DEC(?:EMBER)?)\.?\s+(\d{1,2})\.?\s*[,\.]?\s*(\d{4})',
        text, re.IGNORECASE,
    )
    if m:
        month_str = m.group(1)[:3].upper()
        day = int(m.group(2))
        year = int(m.group(3))
        month_num = MONTH_NAMES.get(month_str)
        if month_num:
            try:
                return date(year, month_num, day)
            except ValueError:
                return None

    # Pattern: "DD MONTH YYYY" (less common)
    m = re.search(
        r'(\d{1,2})\s+(JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?|'
        r'JUL(?:Y)?|AUG(?:UST)?|SEP(?:TEMBER)?|OCT(?:OBER)?|NOV(?:EMBER)?|'
        r'DEC(?:EMBER)?)\.?\s+(\d{4})',
        text, re.IGNORECASE,
    )
    if m:
        day = int(m.group(1))
        month_str = m.group(2)[:3].upper()
        year = int(m.group(3))
        month_num = MONTH_NAMES.get(month_str)
        if month_num:
            try:
                return date(year, month_num, day)
            except ValueError:
                return None

    return None


def _parse_account_number(text: str) -> Optional[str]:
    """Extract account number from statement header."""
    m = re.search(r'Account\s*Number\s*:\s*(\d[\d\-]*)', text, re.IGNORECASE)
    if m:
        return m.group(1).replace("-", "")
    m = re.search(r'Account\s*:\s*(\d[\d\-]*)', text, re.IGNORECASE)
    if m:
        return m.group(1).replace("-", "")
    return None


def _parse_currency(text: str) -> str:
    """Determine statement currency from header."""
    if "USD" in text[:500] or "U.S. DOLLAR" in text[:500]:
        return "USD"
    return "CAD"


def _parse_holdings_and_balances(text: str) -> tuple[list[dict], dict]:
    """Parse holdings from the Asset Review section and balances from Account Info.

    Holdings table layout (from raw text):
      SECURITY                    SYMBOL  QUANTITY/  BOOK  MARKET   MARKET
                                  SEGREGATED COST   PRICE  VALUE
      ISHARES CORE MSCI CANADIAN  XDIV    4,558      19.375  98,802.09  $88,311.25
      QUALITY DIVIDEND INDEX ETF               4,558
      VANGUARD FTSE CDN HIGH      VDY      2,891     32.750  100,287.32 $94,680.25
      DIVID YIELD INDEX ETF TR UNIT            2,891
      VANGUARD S&P 500 INDEX ETF  VFV      3,496     84.770  194,290.31 $296,355.92
      TR UNIT                                      3,496

    Balances layout:
      Cash          $22,621.62     $23,073.29
    """
    holdings = []
    balances = {"opening": None, "closing": None}

    # ---- Extract balances from "Cash Balance" section ----
    lines = text.split("\n")

    # Find Cash Balance section
    cb_idx = -1
    for i, line in enumerate(lines):
        if "Cash Balance" in line:
            cb_idx = i
            break

    if cb_idx >= 0:
        # Scan lines after "Cash Balance" looking for balance values
        # Layout can be:
        #   Cash
        #   $1,292.61    <- opening
        #   $1,443.59    <- closing
        # Or:
        #   Cash  $1,292.61  $1,443.59   (both on one line)
        all_amounts = []
        for i in range(cb_idx + 1, min(cb_idx + 20, len(lines))):
            line = lines[i].strip()
            if not line:
                continue
            # Skip header/type lines
            if any(kw in line.upper() for kw in ["ACCOUNT", "TYPE", "OPENING BALANCE", "CLOSING BALANCE"]):
                continue
            if line in ("Cash", "Fixed Income", "Member-Canadian Investor Protection Fund"):
                continue
            # Collect dollar amounts from this line
            amounts = re.findall(r'\$?([\d,]+\.\d{2})', line)
            for a in amounts:
                all_amounts.append(float(a.replace(",", "")))
            # Once we have 2 amounts, we're done
            if len(all_amounts) >= 2:
                balances["opening"] = all_amounts[0]
                balances["closing"] = all_amounts[1]
                break

    # ---- Extract holdings from "Asset Review" section ----
    # Find the Asset Review section
    ar_idx = -1
    for i, line in enumerate(lines):
        if "Asset Review" in line:
            ar_idx = i
            break

    if ar_idx >= 0:
        # Find the table header - it's split across multiple lines
        # Look for lines containing SECURITY, SYMBOL, QUANTITY, BOOK, MARKET keywords
        header_lines = []
        for i in range(ar_idx + 1, min(ar_idx + 25, len(lines))):
            line_upper = lines[i].upper()
            if any(kw in line_upper for kw in ["SECURITY", "SYMBOL", "QUANTITY", "BOOK", "MARKET", "SEGREGATED", "PRICE", "COST", "VALUE", "MKT.", "DEC."]):
                header_lines.append(i)

        # The header ends at a separator line (___) or "Common Shares"/"Total Value"
        header_end = -1
        for i in header_lines:
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if re.match(r'^_{3,}', next_line) or next_line in ["Common Shares", "Total Value"]:
                    header_end = i + 1
                    break

        if header_end > 0:
            # Parse rows after the header
            # Each holding spans multiple lines:
            #   Line 1: Security name (e.g., "ISHARES CORE MSCI CANADIAN")
            #   Line 2: Symbol (e.g., "XDIV")
            #   Line 3: Quantity (e.g., "4,558")
            #   Line 4: Book Cost (e.g., "19.375")
            #   Line 5: Market Price (e.g., "98,802.09")
            #   Line 6: Market Value (e.g., "$88,311.25")

            # Skip past the separator line and any category labels like "Common Shares"
            i = header_end + 1
            while i < len(lines):
                stripped = lines[i].strip()
                if stripped and not re.match(r'^_{3,}', stripped):
                    # Skip category labels like "Common Shares", "Preferred Shares", etc.
                    if not any(x in stripped for x in [
                        "Common Shares", "Preferred Shares", "Mutual Funds",
                        "Foreign Securities", "Fixed Income", "Other"
                    ]):
                        break
                i += 1

            while i < len(lines):
                line = lines[i]
                stripped = line.strip()

                # Stop at section boundaries
                if "Account Activity" in line or "Total Value" in line:
                    break
                if re.match(r'^_{3,}', stripped):
                    break
                if not stripped:
                    i += 1
                    continue

                # Check if this is a security name line (words with optional symbols and numbers in names like "500 INDEX ETF")
                if re.match(r'^[A-Za-z\s\'\-\.\&\,\d]+$', stripped) and len(stripped) > 10:
                    security_name = stripped
                    i += 1

                    # Next line should be symbol
                    if i < len(lines):
                        symbol_line = lines[i].strip()
                        if re.match(r'^[A-Z]{2,6}$', symbol_line):
                            symbol = symbol_line
                            i += 1

                            # Next lines should be numbers: quantity, book cost, market price, market value
                            nums = []
                            while i < len(lines) and len(nums) < 4:
                                num_line = lines[i].strip()
                                # Try to parse as a number (with or without decimal)
                                m = re.match(r'^\$?([\d,]+(?:\.\d+)?)$', num_line)
                                if m:
                                    nums.append(float(m.group(1).replace(",", "")))
                                    i += 1
                                else:
                                    break

                            if len(nums) == 4:
                                qty, book_cost, market_price, market_value = nums
                                holdings.append({
                                    "symbol": symbol,
                                    "description": security_name,
                                    "quantity": qty,
                                    "book_cost": book_cost,
                                    "market_price": market_price,
                                    "market_value": market_value,
                                })
                        else:
                            # Not a symbol line, skip
                            i += 1
                    else:
                        break
                else:
                    i += 1

    return holdings, balances


def _parse_transactions(text: str) -> list[dict]:
    """Parse Account Activity (transactions) from the text.

    Supports two PDF layout formats:

    **Single-line format** (older PDFs):
      DATE        ACTIVITY   DESCRIPTION                          QUANTITY  RATE     DEBIT   CREDIT
      DEC. 08     DIST       VANGUARD FTSE CDN HIGH              0.156     451.67

    **Multi-line / rotated format** (newer PDFs):
      Each column keyword on its own line. Transaction rows use:
        Line 1: DATE (e.g. "JAN. 08")
        Line 2: ACTIVITY (e.g. "DIV REIN")
        Line 3: DESCRIPTION (e.g. "VANGUARD S&P 500 INDEX ETF")
        Line 4: QUANTITY (e.g. "26")
        Line 5: RATE/PRICE (e.g. "1,563.76")
        Continuation: "ON 2891 SHS", "REC ... PAY ..."

    Multi-line format also has "DIVIDEND" rows where:
        Line 1: DATE
        Line 2: "DIVIDEND"
        Line 3: Security name line 1
        Line 4: Amount (quantity)
        Line 5: Amount (price/rate)
        Line 6: "CASH DIV ON"
        Line 7: "X SHS"
        Line 8: "REC ... PAY ..."
    """
    transactions = []

    lines = text.split("\n")

    # Find Account Activity section
    aa_idx = -1
    for i, line in enumerate(lines):
        if "Account Activity" in line:
            aa_idx = i
            break

    if aa_idx < 0:
        return transactions

    # Find the column header — may be on a single line OR spread across
    # separate lines (rotated layout). Look for the "DATE" keyword since
    # it's the last keyword in both layouts.
    th_idx = -1
    single_line_header = False
    for i in range(aa_idx, min(aa_idx + 15, len(lines))):
        header = lines[i].upper()
        if "DATE" in header and "ACTIVITY" in header and "DESCRIPTION" in header:
            th_idx = i
            single_line_header = True
            break
        if "DATE" in header:
            # Found the last keyword of the header in a rotated layout.
            # The header block continues until we see a non-header keyword.
            th_idx = i
            # Skip past any remaining header keyword lines
            data_start = i + 1
            for j in range(i + 1, min(i + 15, len(lines))):
                header_line = lines[j].upper().strip()
                if header_line in (
                    "PRICE", "QUANTITY", "\\RATE", "DEBIT", "CREDIT",
                    "DATE", "ACTIVITY", "DESCRIPTION",
                ):
                    data_start = j + 1
                    continue
                else:
                    break
            th_idx = data_start
            break

    if th_idx < 0:
        return transactions

    # Determine which layout we're in by peeking at the first few data rows.
    # RBC dates can appear as:
    #   "JAN. 08" (with dot) or "JAN 08" (without dot)
    # Activity type may be on the same line as date (single-line layout):
    #   "JAN 08 BOUGHT  ..."  OR on the next line (multi-line layout):
    #   "JAN 08"
    #   "BOUGHT  ..."
    peek_start = th_idx if not single_line_header else th_idx + 1
    is_multiline = False
    for k in range(peek_start, min(peek_start + 5, len(lines))):
        peek = lines[k].strip()
        if not peek or "Opening Balance" in peek:
            continue
        # Check if the line is ONLY a date (no activity type on same line)
        # Supports both "JAN. 08" and "JAN 08" formats
        if re.match(r'^[A-Z]{3}\.?\s*\d{1,2}$', peek):
            is_multiline = True
            break
        # If we see a single-line transaction (date + activity on same line),
        # it's single-line layout: "JAN 08 BOUGHT ..." or "JAN. 08 BOUGHT ..."
        if re.match(
            r'^[A-Z]{3}\.?\s*\d{1,2}\s+(BOUGHT|SOLD|DIST|DIVIDEND|DIV REIN|'
            r'INTEREST RECEIVED|INTEREST PAID|TRANSFER|TFR OUT|WIRE TFR|'
            r'CONTRIB|DEPOSIT|WITHDRAWAL|ADJUST|RTC|FEE|OTHER)\b',
            peek, re.IGNORECASE,
        ):
            break
        # If we see a non-date, non-empty line that's an activity type,
        # it's multi-line (date is on the previous line)
        if re.match(r'^(BOUGHT|SOLD|DIST|DIVIDEND|DIV REIN|'
                    r'INTEREST RECEIVED|INTEREST PAID|TRANSFER|TFR OUT|WIRE TFR|'
                    r'CONTRIB|DEPOSIT|WITHDRAWAL|ADJUST|RTC|FEE|OTHER)\b',
                    peek, re.IGNORECASE):
            is_multiline = True
            break

    # ------------------------------------------------------------------
    # Single-line parser (original logic, preserved)
    # ------------------------------------------------------------------
    def _parse_single_line():
        txn_match_re = re.compile(
            r'^(\w{3}\.?\s*\d{1,2})\s+(BOUGHT|SOLD|DIST|DIVIDEND|DIV REIN|'
            r'INTEREST RECEIVED|INTEREST PAID|TRANSFER|TFR OUT|WIRE TFR|'
            r'CONTRIB|DEPOSIT|WITHDRAWAL|ADJUST|RTC|FEE|OTHER)\b',
            re.IGNORECASE,
        )
        cont_re = re.compile(
            r'^(ON\s+\d+\s+SHS|REC\s+\d{2}/\d{2}/\d{2}\s+PAY\s+\d{2}/\d{2}/\d{2})',
            re.IGNORECASE,
        )
        num_re = re.compile(
            r'^\s*([\d,]+(?:\.\d{1,3})?)\s+([\d,]+\.\d{2})\s+(\$?[\d,]+\.\d{2})?\s*(\$?[\d,]+\.\d{2})?$',
        )

        current_txn = None
        result = []
        start = th_idx + 1 if single_line_header else th_idx

        for i in range(start, len(lines)):
            line = lines[i]
            stripped = line.strip()

            if "Closing Balance" in line or "Continued on Next Page" in line:
                break
            if re.match(r'^_{3,}', stripped):
                break
            if "Opening Balance" in line:
                continue
            if not stripped:
                continue

            m = txn_match_re.match(stripped)
            if m:
                if current_txn:
                    result.append(current_txn)
                date_str = m.group(1)
                activity = _normalize_activity_type(m.group(2).upper(), stripped)
                current_txn = {
                    "date": date_str,
                    "activity_type": activity,
                    "description": "",
                    "quantity": 0,
                    "rate": 0,
                    "debit": 0,
                    "credit": 0,
                }
                continue

            if current_txn:
                m2 = cont_re.match(stripped)
                if m2:
                    if current_txn["description"]:
                        current_txn["description"] += " " + stripped
                    else:
                        current_txn["description"] = stripped
                    continue

                m3 = num_re.match(stripped)
                if m3:
                    current_txn["quantity"] = float(m3.group(1).replace(",", ""))
                    current_txn["rate"] = float(m3.group(2).replace(",", ""))
                    if m3.group(3):
                        current_txn["debit"] = float(m3.group(3).strip("$").replace(",", ""))
                    if m3.group(4):
                        current_txn["credit"] = float(m3.group(4).strip("$").replace(",", ""))
                    continue

                if not re.match(r'^[_\-]+', stripped):
                    if current_txn["description"]:
                        current_txn["description"] += " " + stripped
                    else:
                        current_txn["description"] = stripped
                    continue

        if current_txn:
            result.append(current_txn)
        return result

    # ------------------------------------------------------------------
    # Multi-line parser (new logic for rotated header PDFs)
    # ------------------------------------------------------------------
    def _parse_multiline():
        """Parse multi-line/rotated-header transaction format.

        Layout per transaction:
            Line 1: DATE          e.g. "JAN. 08"
            Line 2: ACTIVITY      e.g. "DIV REIN"
            Line 3: DESCRIPTION   e.g. "VANGUARD S&P 500 INDEX ETF"
            Line 4: QUANTITY      e.g. "26" (integer or decimal)
            Line 5: RATE          e.g. "1,563.76"
            Followed by optional continuation lines:
                "ON 2891 SHS"          (for DIST)
                "REC ... PAY ..."      (for all)
                "CASH DIV ON" / "X SHS" (for DIVIDEND)
                "REINV@C$60.1447"      (for DIV REIN)
        """
        date_re = re.compile(r'^([A-Z]{3}\.?\s*\d{1,2})$')
        activity_re = re.compile(
            r'^(BOUGHT|SOLD|DIST|DIVIDEND|DIV REIN|'
            r'INTEREST RECEIVED|INTEREST PAID|TRANSFER|TFR OUT|WIRE TFR|'
            r'CONTRIB|DEPOSIT|WITHDRAWAL|ADJUST|RTC|FEE|OTHER)\b',
            re.IGNORECASE,
        )
        shs_re = re.compile(r'^(\d+)\s+SHS$', re.IGNORECASE)
        num_re = re.compile(r'^([\d,]+(?:\.\d+)?)$')
        separator_re = re.compile(r'^_{3,}')

        result = []
        start = th_idx if not single_line_header else th_idx + 1
        i = start
        num_fields = 0
        current_txn = None

        while i < len(lines):
            stripped = lines[i].strip()

            # Stop conditions
            if not stripped or "closing balance" in lines[i].lower():
                break
            if "continued on next page" in lines[i].lower():
                break
            if separator_re.match(stripped):
                break
            if "opening balance" in lines[i].lower():
                i += 1
                continue

            # Try to match a date — this starts a new transaction
            m_date = date_re.match(stripped)
            if not m_date:
                # Not a date, skip (could be orphan data between transactions)
                i += 1
                continue

            # Found a date. Look ahead for the activity type.
            # Normal case: line 2 is the activity type.
            # DIST case: line 2+ has description/quantity/rate, then activity type
            # appears after them (e.g., "DIST" on line 6).
            i += 1
            if i >= len(lines):
                break
            next_stripped = lines[i].strip()
            m_act = activity_re.match(next_stripped)

            if not m_act:
                # Line 2 is not an activity type. Scan forward to find it.
                # We expect pattern like:
                #   <description text>
                #   <quantity number>
                #   <rate/amount number>
                #   <more description>
                #   ACTIVITY_TYPE
                #
                # Collect all lines up to and including the activity type,
                # then process the pre-activity lines.
                # Include the current line (line 2) in pre_lines since it's
                # part of the transaction data (e.g., security name for DIST)
                pre_lines = [next_stripped]  # line 2 is always pre-activity data
                scan_idx = i + 1
                # Track which break path the scan loop took
                scan_found_activity = False
                scan_hit_new_date = False
                while scan_idx < len(lines):
                    s = lines[scan_idx].strip()
                    if not s:
                        scan_idx += 1
                        continue
                    if "closing balance" in lines[scan_idx].lower():
                        break
                    if "continued on next page" in lines[scan_idx].lower():
                        break
                    if separator_re.match(s):
                        break
                    # Check for a NEW date — if found, this transaction is
                    # malformed (no activity type before next date)
                    if date_re.match(s):
                        # No activity type found for this transaction; skip it
                        # and let the outer loop handle the new date
                        i = scan_idx
                        scan_hit_new_date = True
                        break
                    m_scan_act = activity_re.match(s)
                    if m_scan_act:
                        # Found the activity type after pre-description lines
                        activity = _normalize_activity_type(
                            m_scan_act.group(1).upper(), s
                        )
                        i = scan_idx + 1  # advance past the activity line
                        # Process collected pre-activity lines
                        # Heuristic: first number = quantity, second = rate
                        qty = Decimal('0')
                        rate = Decimal('0')
                        desc_parts = []
                        num_count = 0
                        for pl in pre_lines:
                            m_num = num_re.match(pl)
                            if m_num:
                                val = Decimal(pl.replace(",", ""))
                                if num_count == 0:
                                    qty = val
                                    num_count += 1
                                elif num_count == 1:
                                    rate = val
                                    num_count += 1
                                else:
                                    desc_parts.append(pl)
                            else:
                                desc_parts.append(pl)
                        current_txn = {
                            "date": m_date.group(1),
                            "activity_type": activity,
                            "description": " ".join(desc_parts),
                            "quantity": qty,
                            "rate": rate,
                            "debit": Decimal('0'),
                            "credit": Decimal('0'),
                        }
                        num_fields = 2  # qty and rate already filled
                        scan_found_activity = True
                        break
                    pre_lines.append(s)
                    scan_idx += 1
                else:
                    # Ran out of lines without finding activity type
                    i += 1
                    continue

                # If we hit a new date, skip this malformed transaction and
                # let the outer loop parse the new date normally
                if scan_hit_new_date:
                    continue

                # Only proceed if we actually found the activity type via scanning.
                # If scan broke on closing_balance / continued / separator without
                # finding activity, treat as malformed and skip.
                if not scan_found_activity:
                    continue

                # Found activity type after pre-activity lines (DIST pattern)
                # current_txn already built with pre-activity data; skip re-init
                _from_scan = True
            else:
                activity = _normalize_activity_type(m_act.group(1).upper(), next_stripped)
                _from_scan = False

            if not _from_scan:
                # Start building the transaction
                current_txn = {
                    "date": m_date.group(1),
                    "activity_type": activity,
                    "description": "",
                    "quantity": Decimal('0'),
                    "rate": Decimal('0'),
                    "debit": Decimal('0'),
                    "credit": Decimal('0'),
                }
                num_fields = 0

            # Parse subsequent lines for this transaction
            while i + 1 < len(lines):
                i += 1
                next_stripped = lines[i].strip()

                # End of this transaction?
                if not next_stripped:
                    break
                if "closing balance" in lines[i].lower():
                    break
                if "continued on next page" in lines[i].lower():
                    # Skip past this marker and page footer to find next section
                    while i + 1 < len(lines):
                        i += 1
                        if lines[i].strip() == "" or "Account Activity" in lines[i]:
                            break
                    # Skip empty lines and find the next Account Activity header
                    while i + 1 < len(lines):
                        i += 1
                        if "Account Activity" in lines[i]:
                            # Found next page's Account Activity — continue parsing from here
                            i += 1  # Move past the header
                            # Skip the rotated header lines (PRICE, QUANTITY, etc.)
                            while i < len(lines):
                                if lines[i].strip().upper() in (
                                    "PRICE", "QUANTITY", "\\RATE", "DEBIT", "CREDIT",
                                    "DATE", "ACTIVITY", "DESCRIPTION",
                                ):
                                    i += 1
                                else:
                                    break
                            # Continue outer loop from this new position
                            i -= 1  # Undo the i += 1 from inner while
                            break
                    else:
                        break
                    break  # Break the inner while, outer while will continue
                if separator_re.match(next_stripped):
                    break
                if date_re.match(next_stripped):
                    # New transaction starting — stop here
                    # Save current transaction before breaking
                    result.append(current_txn)
                    num_fields = 0
                    break

                # Handle "CASH DIV ON" — skip, next line has SHS
                if re.match(r'^CASH\s+DIV\s+ON$', next_stripped, re.IGNORECASE):
                    continue

                # "X SHS" after CASH DIV ON
                m_shs = shs_re.match(next_stripped)
                if m_shs:
                    suffix = " ON " + m_shs.group(1) + " SHS"
                    current_txn["description"] += suffix
                    continue

                # Numeric field: quantity / rate / debit / credit
                m_num = num_re.match(next_stripped)
                if m_num:
                    val = Decimal(next_stripped.replace(",", ""))
                    if num_fields == 0:
                        current_txn["quantity"] = val
                    elif num_fields == 1:
                        current_txn["rate"] = val
                    elif num_fields == 2:
                        current_txn["debit"] = val
                    else:
                        current_txn["credit"] = val
                    num_fields += 1
                    continue

                # Any other text → description
                current_txn["description"] += " " + next_stripped

            result.append(current_txn)

        return result

    # ------------------------------------------------------------------
    # Run the appropriate parser
    # ------------------------------------------------------------------
    if is_multiline:
        from decimal import Decimal
        transactions = _parse_multiline()
    else:
        transactions = _parse_single_line()

    return transactions


def _normalize_activity_type(activity: str, full_line: str) -> str:
    """Normalize activity type to match RBC model choices."""
    full_upper = full_line.upper()

    # DIST: "DIST ON X SHS" in description
    if "DIST ON" in full_upper and "SHS" in full_upper:
        return "DIST"

    # DIV REIN: "REINV@" in description
    if "REINV@" in full_upper:
        return "DIV REIN"

    # INTEREST: "RBC INVESTMENT SAVINGS" in description
    if "RBC INVESTMENT SAVINGS" in full_upper:
        return "INTEREST_RECEIVED"

    # Map common variations
    activity_map = {
        "BUY": "BOUGHT",
        "BOUGHT": "BOUGHT",
        "BUYING": "BOUGHT",
        "SELL": "SOLD",
        "SOLD": "SOLD",
        "SELLING": "SOLD",
        "DIV": "DIVIDEND",
        "DIVIDEND": "DIVIDEND",
        "DISTRIBUTION": "DIST",
        "DIST": "DIST",
        "WITHDRAWAL": "WITHDRAWAL",
        "WIRE": "WIRE TFR",
        "TRANSFER": "TRANSFER",
        "FEE": "FEE",
        "ADJUST": "ADJUST",
        "DEPOSIT": "DEPOSIT",
        "DEP": "DEPOSIT",
        "CONTRIBUTION": "CONTRIB",
        "CONTRIB": "CONTRIB",
        "OTHER": "OTHER",
    }

    normalized = activity_map.get(activity.upper(), activity.upper())

    # Additional checks for INTEREST_RECEIVED
    if "INTEREST" in normalized and "PAID" not in normalized:
        return "INTEREST_RECEIVED"

    return normalized


def _parse_date_from_string(date_str: str) -> Optional[date]:
    """Parse date string from PDF. RBC format: 'JAN. 08', 'JAN 08', 'Dec 08', or '12/08/20'."""
    # Try "JAN. 08" or "JAN 08" format (dot optional, then optional whitespace)
    m = re.match(r'([A-Z]{3})\.?\s*(\d{1,2})', date_str, re.IGNORECASE)
    if m:
        month_str = m.group(1)[:3].upper()
        day = int(m.group(2))
        month_num = MONTH_NAMES.get(month_str)
        if month_num:
            return date(2020, month_num, day)  # Year TBD — caller fills it in

    # Try "Dec 08" format (no dot, no year — year comes from statement header)
    m = re.match(r'([A-Z]{3})\.?\s+(\d{1,2})', date_str, re.IGNORECASE)
    if m:
        month_str = m.group(1)[:3].upper()
        day = int(m.group(2))
        month_num = MONTH_NAMES.get(month_str)
        if month_num:
            return date(2020, month_num, day)  # Year TBD — caller fills it in

    # Try "12/08/2020" or "12/08/20"
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', date_str)
    if m:
        try:
            if len(m.group(3)) == 2:
                return date(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass

    return None


def parse_rbc_monthly(pdf_path: str) -> Optional[dict]:
    """Parse an RBC monthly statement PDF.

    Returns dict with keys:
      - sections: {type, sections, holdings, transactions, balances, account_number}
      - statement_date: date object
      - account_number: str
      - currency: str ('CAD' or 'USD')

    This is a pure text/PyMuPDF parser — no VL model required.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)

    all_text = ""
    for page in doc:
        all_text += page.get_text() + "\n"

    # Parse holdings and balances from the full text
    holdings, balances = _parse_holdings_and_balances(all_text)

    # Parse transactions
    transactions = _parse_transactions(all_text)

    # Extract statement metadata
    statement_date = _parse_statement_date(all_text)
    account_number = _parse_account_number(all_text)
    currency = _parse_currency(all_text)

    # Fill in statement year for transactions without year
    if statement_date and transactions:
        year = statement_date.year
        for txn in transactions:
            if isinstance(txn["date"], str):
                parsed = _parse_date_from_string(txn["date"])
                if parsed:
                    txn["date"] = parsed.replace(year=year)

    if not holdings:
        logger.warning(f"No holdings found in {pdf_path}")
        return None

    return {
        "sections": {
            "type": "monthly",
            "sections": [],
            "holdings": holdings,
            "transactions": transactions,
            "balances": balances,
            "account_number": account_number,
        },
        "statement_date": statement_date,
        "account_number": account_number,
        "currency": currency,
    }
