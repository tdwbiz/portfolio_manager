"""Vision-language parser for RBC Annual Investment Performance Reports.

Uses the configured OpenAI-compatible vision model (settings.VL_MODEL_NAME,
default: Halogen) to extract structured data from PDF pages rendered as PNG
images.  Hard error on failure — no regex fallback.

Returned dict shape (backward-compatible with manage_annual_report_import):

{
    'account_number': str,
    'account_type_code': str,
    'report_period_start': date,
    'report_period_end': date,
    'report_year': int,
    'cad': {
        'bal_beginning_value': Decimal,
        'bal_deposits_in': Decimal,
        'bal_withdrawals_out': Decimal,
        'bal_net_return': Decimal,
        'bal_ending_value': Decimal,
        'bal_amount_invested': Decimal,
        'perf_since_date': date | None,
        'perf_1yr': Decimal | None,
        'perf_3yr': Decimal | None,
        'perf_5yr': Decimal | None,
        'perf_10yr': Decimal | None,
        'perf_since': Decimal | None,
        'chg_operating': Decimal,
        'chg_commissions': Decimal,
        'chg_total_transaction': Decimal,
        'chg_total_paid': Decimal,
        'comp_trailing': Decimal,
        'comp_other': Decimal,
        'comp_total': Decimal,
    },
    'usd': { ... } | None,
}
"""
import base64
import io
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
from an RBC (Royal Bank of Canada) Annual Investment Performance Report PDF.

IMPORTANT: The PDF contains TWO reports:
1. "Annual investment performance report" (pages 1-3 typically)
   - Shows "Change in account value" table with Beginning market value,
     Deposits, Withdrawals, Net investment return, Ending market value
   - Shows "Summary of amount invested compared to market value"
   - Shows "Money-weighted rate of return" (Past 1 year, 3 years, etc.)
   - Has sections for "Your Canadian dollar account" and/or "Your U.S. dollar account"
2. "Annual charges and compensation report" (pages 4-6 typically)
   - Shows charges paid by you (Operating charges, Transaction charges)
   - Shows compensation received from third parties (Trailing commission, etc.)
   - Has separate (CAD) and (USD) subsections

CRITICAL: You MUST look at ALL pages to find both CAD and USD data.
The USD section is on a DIFFERENT page than the CAD section.
Look for "Your U.S. dollar account" or "(USD)" headings on every page.

Extract the following fields and return ONLY a valid JSON object — no
markdown, no prose, no code fences. Do not add any keys not listed here.
If a value is not present, use null.

JSON schema:
{
  "account_number": "e.g. 900-00005",
  "account_type_code": "e.g. RRSP, RRIF, RIF, LIF, LIRA, TFSA, RESP",
  "report_period_start": "YYYY-MM-DD",
  "report_period_end": "YYYY-MM-DD",
  "report_year": integer,
  "cad": {
    "bal_beginning_value": number,
    "bal_deposits_in": number,
    "bal_withdrawals_out": number,
    "bal_net_return": number,
    "bal_ending_value": number,
    "bal_amount_invested": number,
    "perf_since_date": "YYYY-MM-DD or null",
    "perf_1yr": number or null,
    "perf_3yr": number or null,
    "perf_5yr": number or null,
    "perf_10yr": number or null,
    "perf_since": number or null,
    "chg_operating": number,
    "chg_commissions": number,
    "chg_total_transaction": number,
    "chg_total_paid": number,
    "comp_trailing": number,
    "comp_other": number,
    "comp_total": number
  },
  "usd": null or same structure as "cad"
}

Rules:
- All monetary amounts are stored as positive numbers (withdrawals too).
- Performance percentages are plain numbers, e.g. 15.93 for 15.93%.
- report_year is the year in report_period_end.

*** HOW TO FIND USD DATA ***
- Scan ALL pages for "Your U.S. dollar account" heading
- The USD "Change in account value" table has the same columns as CAD:
  Beginning market value, Deposits and transfers-in, Withdrawals and transfers-out,
  Net investment return, Ending market value at December 31, YYYY (USD)
- USD performance rates are in a "Money-weighted rate of return" table
  that appears AFTER the USD account section
- USD charges are on a later page under "Paid by you (USD)" section
- USD compensation is on a later page under "Compensation we received from
  third parties (USD)" section
- If you find ANY USD data, populate the "usd" section — do NOT set it to null
  unless you have checked ALL pages and confirmed there is truly no USD data.

*** HOW TO FIND CHARGES ***
- Look for "Annual charges and compensation report" (starts on a separate page)
- Under "Paid by you (CAD)": find Operating charges, Transaction charges (Commissions)
- Under "Paid by you (USD)": same structure
- Under "Compensation we received from third parties": find Trailing commission
- chg_operating = Total operating charges
- chg_commissions = Commissions Equities/options
- chg_total_transaction = Total transaction charges
- chg_total_paid = Total paid by you
- comp_trailing = Trailing commission
- comp_other = Other compensation (if any)
- comp_total = Total compensation we received
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_rbc_annual_report_vl(file_path: str) -> dict:
    """Parse an RBC Annual Report PDF using the configured VL vision model.

    Parameters
    ----------
    file_path:
        Absolute path to the PDF file.

    Returns
    -------
    dict
        Structured data matching the schema in the module docstring.

    Raises
    ------
    VLParseError
        If the VL model fails after all retries.
    ValueError
        If required fields are missing or unparseable in the model response.
    """
    base_url = settings.VL_MODEL_BASE_URL
    model = settings.VL_MODEL_NAME
    dpi = getattr(settings, 'VL_PAGE_DPI', 150)
    max_retries = getattr(settings, 'VL_MAX_RETRIES', 2)
    timeout = getattr(settings, 'VL_TIMEOUT_SECONDS', 120)

    # Render all pages — annual reports have charges on pages 4-6
    max_pages = 0  # 0 = unlimited
    logger.info("parse_rbc_annual_report_vl: rendering %s at %d DPI (all pages)", file_path, dpi)
    pages_b64 = _render_pdf_pages(file_path, dpi, max_pages=0)
    logger.info("parse_rbc_annual_report_vl: %d page(s) rendered, calling VL model", len(pages_b64))

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

    return _coerce_response(raw)


# ---------------------------------------------------------------------------
# PDF → images
# ---------------------------------------------------------------------------

def _render_pdf_pages(file_path: str, dpi: int, max_pages: int = 0) -> list[str]:
    """Render PDF pages to PNG and return base64-encoded strings.
    
    max_pages=0 means render all pages.
    """
    doc = fitz.open(file_path)
    pages_b64 = []
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    try:
        for i, page in enumerate(doc):
            if max_pages > 0 and i >= max_pages:
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

def _coerce_response(raw: dict) -> dict:
    """Validate and coerce the raw VL model response into typed Python objects."""
    result = {}

    # --- Required header fields ---
    result['account_number'] = str(raw.get('account_number') or '')
    result['account_type_code'] = str(raw.get('account_type_code') or '')

    start_str = raw.get('report_period_start')
    end_str = raw.get('report_period_end')
    if not start_str or not end_str:
        raise ValueError(f"VL model did not return report_period_start/end: {raw}")

    result['report_period_start'] = _parse_date(start_str)
    result['report_period_end'] = _parse_date(end_str)
    result['report_year'] = int(raw.get('report_year') or result['report_period_end'].year)

    # --- Currency sections ---
    result['cad'] = _coerce_currency_section(raw.get('cad'), 'cad')
    if result['cad'] is None:
        raise ValueError("VL model returned null CAD section — cannot import.")
    result['usd'] = _coerce_currency_section(raw.get('usd'), 'usd')

    return result


def _coerce_currency_section(data: Optional[dict], label: str) -> Optional[dict]:
    """Coerce one CAD or USD currency section dict."""
    if data is None:
        return None

    d: dict = {}
    decimal_fields = [
        'bal_beginning_value', 'bal_deposits_in', 'bal_withdrawals_out',
        'bal_net_return', 'bal_ending_value', 'bal_amount_invested',
        'chg_operating', 'chg_commissions', 'chg_total_transaction', 'chg_total_paid',
        'comp_trailing', 'comp_other', 'comp_total',
    ]
    nullable_decimal_fields = ['perf_1yr', 'perf_3yr', 'perf_5yr', 'perf_10yr', 'perf_since']

    for field in decimal_fields:
        raw_val = data.get(field)
        d[field] = _to_decimal(raw_val, default=Decimal('0.00'))

    for field in nullable_decimal_fields:
        raw_val = data.get(field)
        d[field] = _to_decimal(raw_val, default=None)

    since_date_raw = data.get('perf_since_date')
    if since_date_raw:
        try:
            d['perf_since_date'] = _parse_date(str(since_date_raw))
        except (ValueError, TypeError):
            d['perf_since_date'] = None
    else:
        d['perf_since_date'] = None

    return d


def _parse_date(s: str) -> date:
    """Parse YYYY-MM-DD date string."""
    try:
        from datetime import datetime
        return datetime.strptime(s.strip(), '%Y-%m-%d').date()
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Cannot parse date '{s}': {exc}") from exc


def _to_decimal(value, default) -> Optional[Decimal]:
    """Convert a value (number, string, or None) to Decimal."""
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default
