"""Vision-language parser for RBC monthly brokerage account statements.

Uses the configured OpenAI-compatible vision model (settings.VL_MODEL_NAME,
default: Halogen) to extract structured data from PDF pages rendered as PNG
images.  Hard error on failure — no regex fallback.

Returned value is a **list** of section dicts (one per currency found in the
statement), matching the shape that manage_inbox_import already expects:

[
    {
        'statement_date':            date,
        'account_number':            str,
        'currency':                  'CAD' | 'USD',
        'last_statement_date':       date | None,
        'fx_rate':                   Decimal,
        'begin_total_market_value':  Decimal,
        'end_total_market_value':    Decimal,
        'balances': [
            {
                'account_type':  str,   # e.g. "Cash", "Common Shares"
                'opening':       Decimal,
                'closing':       Decimal,
                'market_value':  Decimal,
                'percentage':    Decimal,
            },
            ...
        ],
        'transactions': [
            {
                'date':          date,
                'activity_type': str,   # e.g. "BUY", "SELL", "INTEREST"
                'description':   str,
                'symbol':        str,
                'quantity':      Decimal,
                'price':         Decimal,
                'debit':         Decimal,
                'credit':        Decimal,
                'currency':      str,
                'commission':    Decimal,
                'notes':         str,
            },
            ...
        ],
    },
    ...
]
"""
import base64
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
import pymupdf  # PyMuPDF

from django.conf import settings
from imports.llm_client import VLClient, VLParseError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Valid RBC PDF activity types
# ---------------------------------------------------------------------------

RBC_ACTIVITY_TYPES = {
    'BOUGHT',              # Real cash purchase of shares — DEBIT = cost (money out)
    'SOLD',                # Sale of shares — DEBIT or CREDIT = proceeds (money in)
    'DIST',                # Cash distribution/dividend — CREDIT = cash (money in)
    'DIVIDEND',            # Cash dividend — CREDIT = cash received (money in)
    'DIV REIN',            # Dividend reinvestment (BUY of shares with dividend cash). Net cash = 0.
    'INTEREST_RECEIVED',   # RBF2010/RBF2014 or IB interest earned. quantity = new shares. Net cash = 0.
    'INTEREST_PAID',       # Interest paid on margin/debt. DEBIT = amount. (IB accounts)
    'TRANSFER',            # Cash transfer in/out
    'TFR OUT',             # Transfer out
    'WIRE TFR',            # Wire transfer
    'CONTRIB',             # Contribution (deposit)
    'DEPOSIT',             # Deposit
    'WITHDRAWAL',          # Withdrawal
    'ADJUST',              # Book cost adjustment. No cash effect.
    'RTC',                 # Book cost adjustment. No cash effect.
    'FEE',                 # Fee/charge
    'OPENING BALANCE',     # Opening cash balance line — NOT a real transaction
    'CLOSING BALANCE',     # Closing cash balance line — NOT a real transaction
}

# Activity types that represent cash inflow (money into the account)
CASH_IN_TYPES = {'SOLD', 'DIVIDEND', 'DIST', 'TRANSFER', 'CONTRIB', 'DEPOSIT', 'WIRE TFR'}

# Activity types that represent cash outflow (money out of the account)
CASH_OUT_TYPES = {'BOUGHT', 'WITHDRAWAL', 'TFR OUT', 'FEE'}


def normalize_activity_type(raw_type: str, description: str = '') -> str:
    """Normalize and validate an activity type from the VL parser.
    
    Uses the description to disambiguate when the LLM misclassifies.
    Returns a valid activity type from RBC_ACTIVITY_TYPES.
    """
    raw = raw_type.upper().strip()
    desc = description.upper()
    
    # If already valid, return as-is
    if raw in RBC_ACTIVITY_TYPES:
        return raw

    # Empty activity type (tdw decision, Oct 27): match only the income
    # wording — never a security name, and never DIVIDEND by default.
    if not raw:
        if 'CASH DIV' in desc:
            return 'DIVIDEND'
        if re.search(r'\bDIST\b', desc):
            return 'DIST'
        logger.warning(
            "normalize_activity_type: empty activity type with no income "
            "wording (description %r) — using OTHER",
            description[:80],
        )
        return 'OTHER'

    # --- Fix known LLM misclassifications based on description ---

    # RBF savings interest must win over the DIST/REINV rules: a check-1
    # style savings row is one net-zero INTEREST_RECEIVED row even when its
    # description mentions reinvestment (Q1, plan vl-fallback-checks).
    if 'RBC INVESTMENT SAVINGS' in desc:
        return 'INTEREST_RECEIVED'

    # "DIST ON X SHS" in description = cash distribution (money in)
    if 'DIST ON' in desc:
        return 'DIST'

    # Dividend reinvestment: description contains "REINV@" or "REINVEST"
    if 'REINV' in desc or 'REINVEST' in desc:
        return 'DIV REIN'

    # Interest on RBF savings funds
    if 'INTEREST' in desc:
        return 'INTEREST_RECEIVED'
    
    # Map common LLM mistakes
    type_map = {
        'BUY': 'BOUGHT',
        'SELL': 'SOLD',
        'DIV': 'DIVIDEND',
        'DISTRIBUTION': 'DIST',
        'DIV REINVEST': 'DIV REIN',
        'INT': 'INTEREST_RECEIVED',
        'INTEREST': 'INTEREST_RECEIVED',
        'TFR': 'TRANSFER',
        'TFR IN': 'TRANSFER',
        'TFR-OUT': 'TFR OUT',
        'TRANSFER OUT': 'TFR OUT',
        'TFO': 'TFR OUT',
        'DEP': 'DEPOSIT',
        'WDRAW': 'WITHDRAWAL',
        'CHG': 'FEE',
        'CHARGE': 'FEE',
        'COMMISSION': 'FEE',
        'OPENING': 'OPENING BALANCE',
        'CLOSING': 'CLOSING BALANCE',
    }
    
    if raw in type_map:
        return type_map[raw]
    
    # Try partial match
    for valid in RBC_ACTIVITY_TYPES:
        if valid in raw or raw in valid:
            return valid
    
    # Log warning and return UNKNOWN
    logger.warning(
        "normalize_activity_type: unknown type %r for description %r — using OTHER",
        raw_type, description[:80],
    )
    return 'OTHER'


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """\
You are a financial data extraction assistant. The attached images are pages
from an RBC (Royal Bank of Canada) monthly brokerage account statement.

Extract the following fields and return ONLY a valid JSON object — no
markdown, no prose, no code fences. Do not add any keys not listed here.
If a value is not present, use null or 0 as appropriate.

JSON schema:
{
  "account_number": "e.g. 12345-678901",
  "statement_date": "YYYY-MM-DD",
  "last_statement_date": "YYYY-MM-DD or null",
  "fx_rate": number or null,
  "sections": [
    {
      "currency": "CAD",
      "begin_total_market_value": number or 0,
      "end_total_market_value": number or 0,
      "balances": [
        {
          "account_type": "e.g. Cash, Mutual Funds, Common Shares, Foreign Securities",
          "opening": number or 0,
          "closing": number or 0,
          "market_value": number or 0,
          "percentage": number or 0
        }
      ],
      "holdings": [
        {
          "security": "e.g. VANGUARD FTSE CDN HIGH, RBC INVESTMENT SAVINGS",
          "symbol": "e.g. VDY, RBF2010",
          "quantity": number or 0,
          "book_cost": number or 0,
          "market_price": number or 0,
          "market_value": number or 0
        }
      ],
      "transactions": [
        {
          "date": "YYYY-MM-DD",
          "activity_type": "ONE OF: BOUGHT, SOLD, DIST, DIVIDEND, DIV REIN, INTEREST_RECEIVED, TRANSFER, TFR OUT, WIRE TFR, CONTRIB, DEPOSIT, WITHDRAWAL, ADJUST, RTC, FEE",
          "description": "full description text from statement",
          "symbol": "ticker symbol or empty string",
          "quantity": number or 0,
          "price": number or 0,
          "debit": number or 0,
          "credit": number or 0,
          "commission": number or 0,
          "notes": ""
        }
      ]
    }
  ]
}

Rules:
- All monetary amounts must be positive numbers (debit and credit are both
  positive — debit means money out, credit means money in).
- fx_rate is from the line "Exchange rate 1 USD = X.XXXX CAD"; use null if
  not present in the visible pages.
- statement_date is the "as of" date for this statement (end of month).
- last_statement_date is the date of the previous statement, if shown.
- Include one section object per currency (CAD and/or USD).
- If there is no USD section in the statement, the sections array contains
  only the CAD section.
- activity_type must be ONE of these exact values:
  BOUGHT, SOLD, DIST, DIVIDEND, DIV REIN, INTEREST_RECEIVED, TRANSFER, TFR OUT,
  WIRE TFR, CONTRIB, DEPOSIT, WITHDRAWAL, ADJUST, RTC, FEE

- **CRITICAL: How to classify each activity type from the PDF**
  The PDF "Account Activity" table has columns: DATE | ACTIVITY | DESCRIPTION
  | QUANTITY | RATE | DEBIT | CREDIT
  
  Use these rules to assign the correct activity_type:
  
  1. **BOUGHT**: The ACTIVITY column says "BOUGHT" or "BUY". You paid cash
     to buy shares. DEBIT = cost (money out). quantity = shares purchased.
  
  2. **SOLD**: The ACTIVITY column says "SOLD" or "SELL". You sold shares
     for cash. DEBIT or CREDIT = proceeds (money in). quantity = shares sold.
  
  3. **DIST**: Description contains "DIST ON X SHS". This is a cash
     distribution/dividend you RECEIVE. The total cash amount is in the
     CREDIT column. The per-share distribution rate is in the PRICE column
     (small number like 0.169). Leave QUANTITY as 0 (it is blank in the PDF).
     Example: "VDY DIST ON 6182 SHS" → CREDIT = 1047.29, PRICE = 0.169, QUANTITY = 0.
     This is NOT a buy — it is cash income.
  
  4. **DIVIDEND**: The ACTIVITY column says "DIVIDEND" or "DIV". Cash
     dividend received. CREDIT = cash amount (money in).
  
  5. **DIV REIN**: Description contains "REINV@" or "REINVEST".
     Dividend was automatically used to buy more shares. Net cash = 0.
     quantity = new shares purchased.
  
  6. **INTEREST_RECEIVED**: Description contains "RBC INVESTMENT SAVINGS" or
     "INTEREST". This is interest on RBF2010/RBF2014 savings funds.
     quantity = new shares earned (e.g., 2.067). PRICE = 0 (the $10 rate
     is fixed and not a meaningful price). DEBIT = 0, CREDIT = 0.
     Net cash effect = 0 (interest reinvested as shares).
  
  7. **TRANSFER** / **TFR OUT** / **WIRE TFR** / **CONTRIB** / **DEPOSIT** / **WITHDRAWAL** / **FEE**:
     These are CASH-ONLY transactions. They move money but do NOT involve
     buying/selling shares. **QUANTITY must be 0 for all these types.**
     - TFR OUT: DEBIT = amount transferred out (money out), quantity = 0
     - TRANSFER/CONTRIB/WIRE TFR: CREDIT = inflow (money in), quantity = 0
     - DEPOSIT: CREDIT = amount deposited (money in), quantity = 0
     - WITHDRAWAL: DEBIT = amount withdrawn (money out), quantity = 0
     - FEE: DEBIT = amount charged, quantity = 0
  
  10. **ADJUST** / **RTC**: Book cost adjustments. No cash effect.
  
  **IMPORTANT: Do NOT include "Opening Balance" or "Closing Balance" as
  transactions.** These are balance summary lines, not transactions.
  The Opening Balance line appears at the TOP of the Account Activity table.
  It shows a date in parentheses like "Opening Balance (APR. 30, 2019)"
  and a dollar amount in the DEBIT column. This is NOT a transaction —
  it is just a reference balance. DO NOT parse it as a SOLD/BOUGHT/etc.
  The first REAL transaction is the NEXT row after the Opening Balance line.

- If a transaction has no ticker symbol, use an empty string for symbol.

- **CRITICAL: begin_total_market_value and end_total_market_value**
  These are found at the BOTTOM of the "Asset Summary" or "Market Value" table.
  The table lists individual holdings with their market values and percentages.
  At the very end of this table, there are TWO "Total on [date]" lines:

    Total on MAY 31 $287,192.32 100.00 %    ← ENDING total (current statement date)
    Total on APR. 30 $285,450.10             ← BEGINNING total (previous statement date)

  - end_total_market_value = the dollar amount from the "Total on [CURRENT DATE]" line
    (the one that HAS a percentage, e.g. "Total on MAY 31 $287,192.32 100.00 %")
  - begin_total_market_value = the dollar amount from the "Total on [PREVIOUS DATE]" line
    (the one that does NOT have a percentage, e.g. "Total on APR. 30 $285,450.10")

  IMPORTANT: These totals represent the FULL PORTFOLIO VALUE, NOT individual holdings.
  Typical portfolio values range from $10,000 to $1,000,000+.

  HOW TO FIND THE TOTALS:
  1. Look for the "Asset Summary" or "Market Value" table
  2. Scroll to the VERY BOTTOM of this table
  3. Find lines that start with "Total on" — there should be TWO:
     - One with the current statement date (has percentage) → end_total
     - One with the previous statement date (no percentage) → begin_total
  4. Extract the DOLLAR AMOUNT from each line (remove $ and commas)

  CRITICAL WARNINGS:
  - DO NOT use the market value of a single stock/holding
  - DO NOT use any value from the middle of the table
  - DO NOT return 0 or small values (< $1,000) unless the account truly has that little
  - The "Total on" lines are ALWAYS at the bottom of the table, AFTER all holdings

  Example CORRECT extraction:
  - Text: "Total on MAY 31 $287,192.32 100.00 %" → end_total = 287192.32
  - Text: "Total on APR. 30 $285,450.10" → begin_total = 285450.10

- **CRITICAL: fx_rate for USD statements**
  Look for a line that says "Exchange rate 1 USD = X.XXXX CAD" or similar.
  The typical range for USD/CAD is 0.70 to 1.60.
  If you see this line, extract the X.XXXX value as fx_rate.
  If you do NOT see this line, use null (NOT 1.0 or 0).

  Common mistake: Do NOT return 1.000000 if you cannot find the exchange rate.
  Return null instead. The system will handle null values correctly.

- **CRITICAL: each individual holding (security) in the "Asset Review" or "Market Value" table**
  Every row in the Holdings/Asset Review table is one security. For EACH row, extract:
    - security: full name (e.g. "VANGUARD FTSE CDN HIGH")
    - symbol: ticker (e.g. "VDY")
    - quantity: number of shares/units (e.g. 1617, 245.833)
    - book_cost: book value (e.g. 90232.06)
    - market_price: price per share/unit (e.g. 71.190)
    - market_value: total market value of that security (e.g. 115114.23)
  Each holding row goes into the "holdings" array — one object per security.
  - Do NOT merge holdings — each row is a separate object.
  - If a row has no ticker symbol, leave symbol empty.

- Include ALL transactions visible on ALL pages provided.
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_rbc_monthly_vl(file_path: str) -> list[dict]:
    """Parse an RBC monthly statement PDF using the configured VL vision model.

    Falls back to a regex-based parser if the VL model fails after all retries.

    Parameters
    ----------
    file_path:
        Absolute path to the PDF file.

    Returns
    -------
    list[dict]
        List of section dicts — one per currency — matching the schema in the
        module docstring.  Always contains at least one CAD section.

    Raises
    ------
    VLParseError
        If the VL model fails after all retries AND regex fallback also fails.
    ValueError
        If required fields (statement_date, account_number) are missing.
    """
    base_url = settings.VL_MODEL_BASE_URL
    model = settings.VL_MODEL_NAME
    dpi = settings.VL_PAGE_DPI
    max_retries = settings.VL_MAX_RETRIES
    timeout = settings.VL_TIMEOUT_SECONDS

    logger.info(
        "parse_rbc_monthly_vl: rendering %s at %d DPI (all pages)",
        file_path, dpi,
    )
    pages_b64 = _render_pdf_pages(file_path, dpi)
    logger.info(
        "parse_rbc_monthly_vl: %d page(s) rendered, calling VL model",
        len(pages_b64),
    )

    try:
        client = VLClient(
            base_url=base_url,
            model=model,
            api_key=settings.VL_MODEL_API_KEY,
            timeout=timeout,
            max_retries=max_retries,
            max_tokens=settings.VL_MAX_TOKENS,
            temperature=settings.VL_TEMPERATURE,
        )
        raw = client.extract_json(pages_b64, EXTRACTION_PROMPT)
        return _coerce_response(raw, file_path)
    except (VLParseError, Exception) as exc:
        logger.warning(
            "parse_rbc_monthly_vl: VL model failed (%s), falling back to regex parser",
            exc,
        )
        return _parse_rbc_monthly_regex(file_path)


# ---------------------------------------------------------------------------
# PDF → images
# ---------------------------------------------------------------------------

def _render_pdf_pages(file_path: str, dpi: int) -> list[str]:
    """Render PDF pages to PNG and return base64 strings.
    
    Limits to VL_MONTHLY_MAX_PAGES (default 0 = unlimited).
    Set to 0 to render all pages, or a specific number to limit.
    """
    doc = pymupdf.open(file_path)
    total = len(doc)
    max_pages = settings.VL_MONTHLY_MAX_PAGES
    
    # 0 means unlimited - render all pages
    if max_pages <= 0:
        render_count = total
        logger.info(
            "parse_rbc_monthly_vl: PDF has %d page(s), rendering all (unlimited)",
            total,
        )
    else:
        render_count = min(total, max_pages)
        logger.info(
            "parse_rbc_monthly_vl: PDF has %d page(s), rendering first %d (max=%d)",
            total, render_count, max_pages,
        )
    
    pages_b64 = []
    matrix = pymupdf.Matrix(dpi / 72, dpi / 72)
    try:
        for i, page in enumerate(doc):  # pyright: ignore[reportArgumentType]
            if max_pages > 0 and i >= max_pages:
                logger.info("parse_rbc_monthly_vl: skipping page %d (max=%d)", i, max_pages)
                break
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            png_bytes = pix.tobytes("png")
            pages_b64.append(base64.b64encode(png_bytes).decode('ascii'))
    finally:
        doc.close()
    return pages_b64


# ---------------------------------------------------------------------------
# Response coercion
# ---------------------------------------------------------------------------

def _coerce_response(raw: dict, file_path: str) -> list[dict]:
    """Validate and coerce the raw VL model response into typed Python objects."""
    # --- Required top-level fields ---
    account_number = str(raw.get('account_number') or '')
    statement_date_str = raw.get('statement_date')
    if not statement_date_str:
        raise ValueError(
            f"VL model did not return statement_date for {file_path}: {raw}"
        )

    statement_date = _parse_date(statement_date_str)
    last_stmt_str = raw.get('last_statement_date')
    last_statement_date = _parse_date(last_stmt_str) if last_stmt_str else None

    fx_raw = raw.get('fx_rate')
    fx_rate = _to_decimal(fx_raw) if fx_raw is not None else Decimal('0')

    sections_raw = raw.get('sections') or []
    if not sections_raw:
        raise ValueError(
            f"VL model returned no sections (currency sections) for {file_path}: {raw}"
        )

    result = []
    for sec in sections_raw:
        currency = str(sec.get('currency') or 'CAD').upper().strip()
        if currency not in ('CAD', 'USD'):
            logger.warning(
                "parse_rbc_monthly_vl: unexpected currency %r — skipping section",
                currency,
            )
            continue

        begin_total_mv = _to_decimal(sec.get('begin_total_market_value', 0))
        end_total_mv = _to_decimal(sec.get('end_total_market_value', 0))

        balances = []
        for b in sec.get('balances') or []:
            balances.append({
                'account_type': str(b.get('account_type') or ''),
                'opening':      _to_decimal(b.get('opening', 0)),
                'closing':      _to_decimal(b.get('closing', 0)),
                'market_value': _to_decimal(b.get('market_value', 0)),
                'percentage':   _to_decimal(b.get('percentage', 0)),
            })

        holdings = []
        for h in sec.get('holdings') or []:
            holdings.append({
                'security':     str(h.get('security') or ''),
                'symbol':       str(h.get('symbol') or ''),
                'quantity':     _to_decimal(h.get('quantity', 0)),
                'book_cost':    _to_decimal(h.get('book_cost', 0)),
                'market_price': _to_decimal(h.get('market_price', 0)),
                'market_value': _to_decimal(h.get('market_value', 0)),
            })

        transactions = []
        for tx in sec.get('transactions') or []:
            tx_date_str = tx.get('date')
            if not tx_date_str:
                logger.warning(
                    "parse_rbc_monthly_vl: transaction missing date — skipping: %s",
                    tx,
                )
                continue
            try:
                tx_date = _parse_date(tx_date_str)
            except ValueError:
                logger.warning(
                    "parse_rbc_monthly_vl: unparseable transaction date %r — skipping",
                    tx_date_str,
                )
                continue

            raw_type = str(tx.get('activity_type') or '').upper().strip()
            description = str(tx.get('description') or '')
            
            # Skip Opening/Closing balance lines — not real transactions
            if normalize_activity_type(raw_type, description) in ('OPENING BALANCE', 'CLOSING BALANCE'):
                logger.info(
                    "parse_rbc_monthly_vl: skipping balance line: %s",
                    description[:80],
                )
                continue

            # Post-fix: detect Opening Balance that was misclassified as a transaction.
            # Pattern: description contains "Opening Balance" or matches date pattern
            # like "(APR. 30, 2019)" with no real activity
            if 'OPENING BALANCE' in description.upper():
                logger.info(
                    "parse_rbc_monthly_vl: skipping misclassified opening balance: %s",
                    description[:80],
                )
                continue
            
            activity = normalize_activity_type(raw_type, description)
            
            # Post-fix: if LLM labeled as BOUGHT but description says "DIST ON",
            # force to DIST
            if activity == 'BOUGHT' and 'DIST ON' in description.upper():
                activity = 'DIST'
                logger.warning(
                    "parse_rbc_monthly_vl: corrected BOUGHT->DIST for: %s",
                    description[:80],
                )

            # Post-fix: if LLM labeled as BUY but description says "DIST ON"
            if raw_type == 'BUY' and 'DIST ON' in description.upper():
                activity = 'DIST'

            # Post-fix: if description says "REINV@" or "REINVEST", force to
            # DIV REIN — savings rows excluded, RBC INVESTMENT SAVINGS wins
            # (Q1): they stay INTEREST_RECEIVED and must not keep amounts.
            desc_upper = description.upper()
            if (('REINV' in desc_upper or 'REINVEST' in desc_upper)
                    and 'RBC INVESTMENT SAVINGS' not in desc_upper):
                if activity != 'DIV REIN':
                    logger.warning(
                        "parse_rbc_monthly_vl: corrected %s->DIV REIN for: %s",
                        activity, description[:80],
                    )
                    activity = 'DIV REIN'

            # Post-fix: if LLM labeled as BOUGHT but description says "DIST ON"
            if raw_type == 'BOUGHT' and 'DIST ON' in description.upper():
                activity = 'DIST'

            quantity = _to_decimal(tx.get('quantity', 0))
            price = _to_decimal(tx.get('price', 0))
            debit = _to_decimal(tx.get('debit', 0))
            credit = _to_decimal(tx.get('credit', 0))

            # --- Fix BOUGHT: clear credit, ensure cost in debit ---
            # BOUGHT = money out. The VL model sometimes puts the amount in
            # credit instead of debit, or leaves both set.
            if activity == 'BOUGHT':
                if credit > 0 and debit == 0:
                    logger.warning(
                        "parse_rbc_monthly_vl: BOUGHT moved credit->debit for: %s",
                        description[:60],
                    )
                    debit = credit
                    credit = Decimal('0')
                elif credit > 0 and debit > 0:
                    logger.warning(
                        "parse_rbc_monthly_vl: BOUGHT cleared credit for: %s",
                        description[:60],
                    )
                    credit = Decimal('0')

            # --- Fix SOLD: clear debit, ensure proceeds in credit ---
            # SOLD = money in. The VL model sometimes puts the Opening Balance
            # amount from the previous row into the debit column of the first
            # transaction. Also, sometimes proceeds go to debit instead of credit.
            if activity == 'SOLD':
                if debit > 0 and credit == 0:
                    # Proceeds went to wrong column
                    logger.warning(
                        "parse_rbc_monthly_vl: SOLD moved debit->credit for: %s",
                        description[:60],
                    )
                    credit = debit
                    debit = Decimal('0')
                elif debit > 0 and credit > 0:
                    # Both set — debit is likely Opening Balance leak
                    logger.warning(
                        "parse_rbc_monthly_vl: SOLD cleared debit (opening balance leak) for: %s",
                        description[:60],
                    )
                    debit = Decimal('0')

            # --- Fix DIST: per-share rate goes in price, quantity is 0 ---
            # PDF: DIST has per-share rate in RATE column, quantity blank in PDF
            # VL model often puts the rate in quantity and total cash in credit/debit
            if activity == 'DIST':
                # If quantity looks like a per-share rate (small decimal < 5),
                # move it to price and set quantity to 0
                if 0 < quantity < Decimal('5'):
                    price = quantity
                    quantity = Decimal('0')
                    logger.info(
                        "parse_rbc_monthly_vl: DIST rate->price fix: price=%.4f qty=0 for: %s",
                        price, description[:60],
                    )
                # Ensure cash is in credit (money in)
                if debit > 0 and credit == 0:
                    credit = debit
                    debit = Decimal('0')

            # DIV REIN keeps its printed debit (the whole shares bought with
            # the dividend cash): a reinvested dividend is two PDF rows —
            # DIV REIN (debit) + a DIVIDEND/DIST credit row — and the cash
            # gate needs both amounts (plan vl-fallback-checks, goal 2).

            # --- Fix INTEREST_RECEIVED: only quantity matters, clear price/debit/credit ---
            # RBF2010/RBF2014 interest: quantity = new shares earned
            # The PDF shows quantity and $10 rate, but no real debit/credit
            if activity == 'INTEREST_RECEIVED':
                # Clear the $10 rate from price field — it's not a real price
                if price == Decimal('10.00'):
                    price = Decimal('0')
                # Clear any debit/credit that may have been parsed
                # (interest is reinvested as shares, no cash flow)
                debit = Decimal('0')
                credit = Decimal('0')
                logger.info(
                    "parse_rbc_monthly_vl: INTEREST fix: qty=%s price=0 for: %s",
                    quantity, description[:60],
                )

            # --- Fix cash-only transactions: quantity must be 0 ---
            # TFR OUT, TRANSFER, WIRE TFR, CONTRIB, DEPOSIT, WITHDRAWAL,
            # FEE are cash-only — no shares involved
            CASH_ONLY_TYPES = {'TFR OUT', 'TRANSFER', 'WIRE TFR', 'CONTRIB',
                               'DEPOSIT', 'WITHDRAWAL', 'FEE'}
            if activity in CASH_ONLY_TYPES and quantity > 0:
                logger.warning(
                    "parse_rbc_monthly_vl: %s should have qty=0, clearing qty=%s for: %s",
                    activity, quantity, description[:60],
                )
                quantity = Decimal('0')
                price = Decimal('0')

            # --- Fix TFR OUT: cash out must be in debit ---
            if activity == 'TFR OUT':
                if credit > 0 and debit == 0:
                    debit = credit
                    credit = Decimal('0')
                    logger.warning(
                        "parse_rbc_monthly_vl: TFR OUT moved credit->debit for: %s",
                        description[:60],
                    )

            # --- Fix DIVIDEND: ensure cash is in credit ---
            if activity == 'DIVIDEND':
                if debit > 0 and credit == 0:
                    credit = debit
                    debit = Decimal('0')
                    logger.warning(
                        "parse_rbc_monthly_vl: DIVIDEND moved debit->credit for: %s",
                        description[:60],
                    )

            # --- Fix WITHDRAWAL: ensure cash is in debit ---
            if activity == 'WITHDRAWAL':
                if credit > 0 and debit == 0:
                    debit = credit
                    credit = Decimal('0')
                    logger.warning(
                        "parse_rbc_monthly_vl: WITHDRAWAL moved credit->debit for: %s",
                        description[:60],
                    )

            # --- Validate: cash-in types must have credit > 0 ---
            if activity in CASH_IN_TYPES and debit == 0 and credit == 0:
                logger.warning(
                    "parse_rbc_monthly_vl: %s has zero debit and credit — skipping: %s",
                    activity, description[:60],
                )
                continue

            # --- Validate: cash-out types must have debit > 0 ---
            if activity in CASH_OUT_TYPES and debit == 0 and credit == 0:
                logger.warning(
                    "parse_rbc_monthly_vl: %s has zero debit and credit — skipping: %s",
                    activity, description[:60],
                )
                continue

            transactions.append({
                'date':          tx_date,
                'activity_type': activity,
                'description':   description,
                'symbol':        str(tx.get('symbol') or ''),
                'quantity':      quantity,
                'price':         price,
                'debit':         debit,
                'credit':        credit,
                'currency':      currency,
                'commission':    _to_decimal(tx.get('commission', 0)),
                'notes':         str(tx.get('notes') or ''),
            })

        result.append({
            'statement_date':            statement_date,
            'account_number':            account_number,
            'currency':            currency,
            'last_statement_date': last_statement_date,
            'fx_rate':             fx_rate,
            'begin_total_market_value':  begin_total_mv,
            'end_total_market_value':    end_total_mv,
            'balances':            balances,
            'holdings':            holdings,
            'transactions':        transactions,
        })

    if not result:
        raise ValueError(
            f"VL model returned sections but none had a valid currency for {file_path}"
        )

    # Post-processing validation: flag suspicious values
    for section in result:
        end_total = section.get('end_total_market_value', Decimal('0'))
        begin_total = section.get('begin_total_market_value', Decimal('0'))

        # Sum up all balance market values for this section
        balance_sum = sum((b.get('market_value', Decimal('0')) for b in section.get('balances', [])), Decimal('0'))

        # If end_total is suspiciously low but balances sum to a large value,
        # the VL model likely extracted a single holding instead of the total
        if end_total < Decimal('1000') and balance_sum > Decimal('1000'):
            logger.warning(
                "parse_rbc_monthly_vl: %s [%s] end_total=%.2f but balances sum to %.2f — "
                "VL model likely extracted wrong value (single holding vs total)",
                file_path, section.get('currency', 'CAD'), end_total, balance_sum,
            )
            # Attempt to fix: use balance_sum as end_total if it's more reasonable
            if balance_sum > end_total * 10:  # Only if balance_sum is 10x larger
                logger.warning(
                    "parse_rbc_monthly_vl: %s [%s] replacing end_total %.2f with balance_sum %.2f",
                    file_path, section.get('currency', 'CAD'), end_total, balance_sum,
                )
                section['end_total_market_value'] = balance_sum

        # Same check for begin_total
        if begin_total < Decimal('1000') and balance_sum > Decimal('1000'):
            logger.warning(
                "parse_rbc_monthly_vl: %s [%s] begin_total=%.2f but balances sum to %.2f — "
                "VL model likely extracted wrong value",
                file_path, section.get('currency', 'CAD'), begin_total, balance_sum,
            )

        # If end_total is suspiciously HIGH (10x balance sum), the VL model
        # likely hallucinated an extra digit or merged multiple values
        if balance_sum > Decimal('0') and end_total > balance_sum * 10:
            logger.warning(
                "parse_rbc_monthly_vl: %s [%s] end_total=%.2f is %.0fx balance_sum %.2f — "
                "likely VL hallucination; replacing with balance_sum",
                file_path, section.get('currency', 'CAD'),
                end_total, end_total / balance_sum, balance_sum,
            )
            section['end_total_market_value'] = balance_sum

        # Same for begin_total
        if balance_sum > Decimal('0') and begin_total > balance_sum * 10:
            logger.warning(
                "parse_rbc_monthly_vl: %s [%s] begin_total=%.2f is %.0fx balance_sum %.2f — "
                "likely VL hallucination; replacing with balance_sum",
                file_path, section.get('currency', 'CAD'),
                begin_total, begin_total / balance_sum, balance_sum,
            )
            section['begin_total_market_value'] = balance_sum

    return result


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _parse_date(value: str) -> date:
    """Parse an ISO-8601 date string (YYYY-MM-DD) to a Python date."""
    if not value:
        raise ValueError("Empty date string")
    # Strip time component if the model returns a full datetime string
    date_part = str(value).strip()[:10]
    try:
        return date.fromisoformat(date_part)
    except ValueError as exc:
        raise ValueError(f"Cannot parse date {value!r}: {exc}") from exc


def _to_decimal(value) -> Decimal:
    """Convert a number or string to Decimal, returning 0 on failure."""
    if value is None:
        return Decimal('0')
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        logger.warning("parse_rbc_monthly_vl: cannot convert %r to Decimal — using 0", value)
        return Decimal('0')


# ---------------------------------------------------------------------------
# Regex fallback parser
# ---------------------------------------------------------------------------

def _parse_rbc_monthly_regex(file_path: str) -> list[dict]:
    """Regex-based fallback parser for RBC monthly statements.

    Used when the VL model fails. Extracts what it can from the raw PDF
    text using pattern matching.

    Parameters
    ----------
    file_path:
        Absolute path to the PDF file.

    Returns
    -------
    list[dict]
        List of section dicts. May be incomplete compared to VL output.
    """
    doc = pymupdf.open(file_path)
    full_text = '\n'.join([str(page.get_text()) for page in doc])
    doc.close()

    result = []

    # Extract statement date
    stmt_date_match = re.search(
        r'(?:Statement\s+Date|As\s+of\s+Date|Period\s+End)[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
        full_text, re.IGNORECASE,
    )
    statement_date = None
    if stmt_date_match:
        date_str = stmt_date_match.group(1).replace('/', '-')
        parts = date_str.split('-')
        if len(parts) == 3 and len(parts[0]) == 4:
            statement_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
        elif len(parts) == 3:
            statement_date = date(int(parts[2]), int(parts[0]), int(parts[1]))

    # Extract account number
    acct_match = re.search(
        r'(?:Account\s+No\.?|Account\s+Number)[:\s]+(\d[\d\-]+)',
        full_text, re.IGNORECASE,
    )
    account_number = acct_match.group(1) if acct_match else ''

    # Extract FX rate
    fx_match = re.search(
        r'Exchange\s+rate\s+1\s+USD\s*=\s*([\d.]+)\s+CAD',
        full_text, re.IGNORECASE,
    )
    fx_rate = Decimal(fx_match.group(1)) if fx_match else Decimal('0')

    # Try to extract currency sections (CAD/USD)
    # Look for patterns like "CAD" or "USD" followed by holdings
    currency_sections = re.findall(
        r'(CAD|USD)\s*\n((?:.*\n)*?)(?=(?:CAD|USD)\s*\n|$)',
        full_text, re.IGNORECASE,
    )

    for curr, section_text in currency_sections:
        curr = curr.upper()
        section_result = {
            'statement_date': statement_date,
            'account_number': account_number,
            'currency': curr,
            'last_statement_date': None,
            'fx_rate': fx_rate if curr == 'USD' else Decimal('0'),
            'begin_total_market_value': Decimal('0'),
            'end_total_market_value': Decimal('0'),
            'balances': [],
            'transactions': [],
        }

        # Extract total market values
        total_match = re.search(
            r'Total\s+on\s+\w+\s+\$([\d,]+\.?\d*)\s*(?:\d+\.?\d*\s*%)?',
            section_text, re.IGNORECASE,
        )
        if total_match:
            val = Decimal(total_match.group(1).replace(',', ''))
            section_result['end_total_market_value'] = val

        # Extract transactions (date, type, symbol, quantity, amount patterns)
        tx_patterns = [
            r'(\d{1,2}[/-]\d{1,2}[/-]\d{4})\s+(\w+)\s+(\w+)\s+([\d,]+\.?\d*)\s+([\d.]+)\s+\$?([\d,]+\.?\d*)',
        ]
        for tx_pat in tx_patterns:
            for m in re.finditer(tx_pat, section_text):
                try:
                    tx_date_str = m.group(1).replace('/', '-')
                    tx_parts = tx_date_str.split('-')
                    if len(tx_parts) == 3 and len(tx_parts[0]) == 4:
                        tx_date = date(int(tx_parts[0]), int(tx_parts[1]), int(tx_parts[2]))
                    else:
                        tx_date = date(int(tx_parts[2]), int(tx_parts[0]), int(tx_parts[1]))

                    section_result['transactions'].append({
                        'date': tx_date,
                        'activity_type': m.group(2).upper(),
                        'description': '',
                        'symbol': m.group(3),
                        'quantity': Decimal(m.group(4).replace(',', '')),
                        'price': Decimal(m.group(5)),
                        'debit': Decimal('0'),
                        'credit': Decimal(m.group(6).replace(',', '')),
                        'currency': curr,
                        'commission': Decimal('0'),
                        'notes': '',
                    })
                except (ValueError, IndexError):
                    continue

        result.append(section_result)

    # If no currency sections found, create a default CAD section
    if not result:
        result.append({
            'statement_date': statement_date,
            'account_number': account_number,
            'currency': 'CAD',
            'last_statement_date': None,
            'fx_rate': Decimal('0'),
            'begin_total_market_value': Decimal('0'),
            'end_total_market_value': Decimal('0'),
            'balances': [],
            'transactions': [],
        })

    logger.info(
        "parse_rbc_monthly_regex: extracted %d section(s) from %s",
        len(result), file_path,
    )
    return result
