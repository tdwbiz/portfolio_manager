"""Vision-language parser for RBC monthly brokerage account statements.

Uses Qwen2.5-VL-7B on port 11440 to extract structured data from PDF pages
rendered as PNG images.  Hard error on failure — no regex fallback.

Returned value is a **list** of section dicts (one per currency found in the
statement), matching the shape that manage_inbox_import / manage_pdf_import
already expect:

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
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

import fitz  # PyMuPDF

from django.conf import settings
from imports.llm_client import VLClient, VLParseError

logger = logging.getLogger(__name__)

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
      "transactions": [
        {
          "date": "YYYY-MM-DD",
          "activity_type": "e.g. BUY, SELL, INTEREST, DIVIDEND, DEPOSIT, WITHDRAWAL, TRANSFER",
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
- activity_type must be uppercase (e.g. "BUY", "SELL", "INTEREST",
  "DIVIDEND", "DEPOSIT", "WITHDRAWAL", "TRANSFER", "FEE").
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

- Include ALL transactions visible on ALL pages provided.
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_rbc_monthly_vl(file_path: str) -> list[dict]:
    """Parse an RBC monthly statement PDF using the Qwen2.5-VL vision model.

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
        If the VL model fails after all retries.
    ValueError
        If required fields (statement_date, account_number) are missing.
    """
    base_url = settings.VL_MODEL_BASE_URL
    model = getattr(settings, 'VL_MODEL_NAME', 'qwen2.5-vl-7b-instruct-q8_0')
    dpi = getattr(settings, 'VL_PAGE_DPI', 150)
    max_retries = getattr(settings, 'VL_MAX_RETRIES', 2)
    timeout = getattr(settings, 'VL_TIMEOUT_SECONDS', 120)

    logger.info(
        "parse_rbc_monthly_vl: rendering %s at %d DPI (all pages)",
        file_path, dpi,
    )
    pages_b64 = _render_pdf_pages(file_path, dpi)
    logger.info(
        "parse_rbc_monthly_vl: %d page(s) rendered, calling VL model",
        len(pages_b64),
    )

    client = VLClient(base_url=base_url, model=model, timeout=timeout, max_retries=max_retries)
    raw = client.extract_json(pages_b64, EXTRACTION_PROMPT)

    return _coerce_response(raw, file_path)


# ---------------------------------------------------------------------------
# PDF → images
# ---------------------------------------------------------------------------

def _render_pdf_pages(file_path: str, dpi: int) -> list[str]:
    """Render all pages of the PDF to PNG and return base64 strings."""
    doc = fitz.open(file_path)
    total = len(doc)
    logger.info("parse_rbc_monthly_vl: PDF has %d page(s), rendering all", total)
    pages_b64 = []
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    try:
        for page in doc:
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

            transactions.append({
                'date':          tx_date,
                'activity_type': str(tx.get('activity_type') or '').upper().strip(),
                'description':   str(tx.get('description') or ''),
                'symbol':        str(tx.get('symbol') or ''),
                'quantity':      _to_decimal(tx.get('quantity', 0)),
                'price':         _to_decimal(tx.get('price', 0)),
                'debit':         _to_decimal(tx.get('debit', 0)),
                'credit':        _to_decimal(tx.get('credit', 0)),
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

    return result


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _parse_date(value: str) -> date:
    """Parse an ISO-8601 date string (YYYY-MM-DD) to a Python date."""
    if not value:
        raise ValueError(f"Empty date string")
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
