"""PDF document-type classifier using the configured OpenAI-compatible VL model
(default: Halogen).

Renders only the first page of a PDF and asks the VL model whether the
document is an RBC Annual Investment Performance Report or an RBC monthly
account statement.

Returns one of the three strings:  'annual' | 'monthly' | 'unknown'

This module has no Django model imports — it is safe to call before the
ORM is set up (e.g. during management command arg-parsing).
"""
import base64
import logging

import pymupdf  # PyMuPDF
from django.conf import settings

from imports.llm_client import VLParseError, VLClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = """\
Look at the first page of this RBC Direct Investing (Royal Bank of Canada) PDF document.

Determine which of the following document types it is:
  - "annual"  — RBC Annual Investment Performance Report. Covers a full calendar
                year, shows beginning/ending values, deposits, withdrawals, net
                return, performance percentages, fees, and compensation.
                Key indicators: "Annual investment performance report" title,
                "Change in account value" table, "Money-weighted rate of return",
                "Annual charges and compensation report", full-year period dates.
  - "monthly" — RBC monthly account statement for a single month. The account type
                header on page 1 may say any of the following (all are monthly):
                  "Cdn. Dollar Statement", "USD Dollar Statement",
                  "RRSP Statement (CDN$)", "RRSP Statement (USD$)",
                  "RRIF Statement (CDN$)", "RRIF Statement (USD$)",
                  "RESP Statement", "TFSA Statement", or similar.
                Key indicators: shows an address block, account number, date of
                last statement, Asset Summary table with market values by category
                (Cash, Common Shares, Mutual Funds, etc.), and Income Summary.
                A monthly statement covers a SINGLE MONTH only (e.g. October 2025,
                November 2025), NOT a full year.
  - "unknown" — Cannot be determined from this page.

IMPORTANT DISTINCTION:
- If the document covers a FULL YEAR period (e.g. "January 1, 2025 to December 31, 2025"),
  it is an ANNUAL report, NOT a monthly statement.
- If the document covers a SINGLE MONTH (e.g. "October 1, 2025 to October 31, 2025"),
  it is a MONTHLY statement.
- The presence of "Annual investment performance report" or "Annual charges" headings
  means this is definitely an ANNUAL report.
- The presence of "Asset Summary", "Income Summary", or "Account Activity" for one
  month means this is a MONTHLY statement.

Return ONLY a JSON object with a single key — no markdown, no prose:
{"doc_type": "annual" | "monthly" | "unknown"}
"""


# ---------------------------------------------------------------------------
# Filename heuristics — fast pre-classification before calling VL model
# ---------------------------------------------------------------------------

ANNUAL_FILENAME_PATTERNS = [
    'annual report',
    'annual investment performance',
    'annual charges',
]

MONTHLY_FILENAME_PATTERNS = [
    'monthly statement',
    'account statement',
]


def _classify_by_filename(file_path: str) -> str | None:
    """Return 'annual' or 'monthly' if the filename strongly indicates type.
    
    Returns None if the filename doesn't match any known pattern, in which
    case the caller should use the VL model.
    """
    name = file_path.lower()
    for pattern in ANNUAL_FILENAME_PATTERNS:
        if pattern in name:
            return 'annual'
    for pattern in MONTHLY_FILENAME_PATTERNS:
        if pattern in name:
            return 'monthly'
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def classify_pdf(file_path: str) -> str:
    """Classify a PDF as 'annual', 'monthly', or 'unknown'.

    First tries a fast filename heuristic. If the filename doesn't match
    any known pattern, renders the first page at VL_PAGE_DPI and sends it
    to the VL model. On any error, returns 'unknown' rather than raising.

    Parameters
    ----------
    file_path:
        Absolute path to the PDF file.

    Returns
    -------
    str
        One of: 'annual', 'monthly', 'unknown'.
    """
    # Fast path: check filename for known patterns
    hint = _classify_by_filename(file_path)
    if hint:
        logger.info("classify_pdf: filename heuristic → %s for %s", hint, file_path)
        return hint

    base_url = settings.VL_MODEL_BASE_URL
    model = settings.VL_MODEL_NAME
    dpi = settings.VL_PAGE_DPI
    max_retries = settings.VL_MAX_RETRIES
    timeout = settings.VL_TIMEOUT_SECONDS

    logger.info("classify_pdf: rendering page 1 of %s at %d DPI", file_path, dpi)
    try:
        pages_b64 = _render_first_page(file_path, dpi)
    except Exception as exc:
        logger.error("classify_pdf: failed to render %s — %s", file_path, exc)
        return 'unknown'

    client = VLClient(
        base_url=base_url,
        model=model,
        api_key=settings.VL_MODEL_API_KEY,
        timeout=timeout,
        max_retries=max_retries,
        max_tokens=settings.VL_MAX_TOKENS,
        temperature=settings.VL_TEMPERATURE,
    )
    try:
        raw = client.extract_json(pages_b64, CLASSIFY_PROMPT)
        doc_type = str(raw.get('doc_type', 'unknown')).lower().strip()
        if doc_type not in ('annual', 'monthly'):
            logger.warning(
                "classify_pdf: unexpected doc_type %r from model for %s — treating as 'unknown'",
                doc_type, file_path,
            )
            doc_type = 'unknown'
        logger.info("classify_pdf: %s → %s", file_path, doc_type)
        return doc_type
    except (VLParseError, Exception) as exc:
        logger.error("classify_pdf: classification failed for %s — %s", file_path, exc)
        return 'unknown'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_first_page(file_path: str, dpi: int) -> list[str]:
    """Render the first page of a PDF as a base64-encoded PNG list."""
    doc = pymupdf.open(file_path)
    try:
        page = doc[0]
        matrix = pymupdf.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        png_bytes = pix.tobytes("png")
        return [base64.b64encode(png_bytes).decode('ascii')]
    finally:
        doc.close()
