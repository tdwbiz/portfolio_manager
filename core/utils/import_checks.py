"""Shared import checks (phase 3, step 13b — docs/plans/import-checks.md).

Helpers used by the PDF and Holdings-CSV import paths to refuse bad files:

- content-based duplicate detection (SHA-256 of the file bytes);
- statement/snapshot date sanity (future, before 2000, and the 1–100-day
  gap against the *Date of Last Statement* printed on the same PDF — never
  against database rows, so import order does not matter; tdw Oct 27);
- the cash gate: ``reconcile_section`` / ``cash_gate_details`` per currency
  section, and ``self_check_vl_sections`` — the text-path checks plus date
  sanity plus a begin-total reason, run on VL-parsed sections (step 13d).

Privacy: nothing here reads or logs account numbers, quantities or prices.
"""

import hashlib
import re
from datetime import date as date_type
from decimal import Decimal
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024

_MIN_STATEMENT_DATE = date_type(2000, 1, 1)
_LAST_STATEMENT_MAX_GAP_DAYS = 100

_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def content_hash_of(path) -> str:
    """SHA-256 hex digest of the file bytes.

    Same digest as ``FileImportLog.content_hash`` and (truncated to 8 chars)
    as ``hash8_of`` uses. Reads in chunks; never hashes a file name.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_date(value):
    """Return ``value`` as a ``datetime.date``, or ``None`` when unusable.

    Accepts ``date`` objects and ISO ``YYYY-MM-DD`` strings (the VL parsers
    may hand back either); malformed values are rejected, not guessed.
    """
    if isinstance(value, date_type):
        return value
    text = str(value or "").strip()
    if not _ISO_DATE_RE.fullmatch(text):
        return None
    try:
        return date_type.fromisoformat(text)
    except ValueError:
        return None


def statement_date_problems(statement_date, *, today, last_known=None) -> list[str]:
    """Sanity reasons for a statement/snapshot date. Empty list = OK.

    - the date is missing or unreadable;
    - the date is in the future (after ``today``);
    - the date is before 2000-01-01;
    - when ``last_known`` (the *Date of Last Statement* printed on the same
      PDF) is given: the date is not 1–100 days after it.

    ``last_known`` must come from the document itself, never from the
    database (tdw decision, Oct 27): import order does not matter, and a
    gap check across months is step 14.
    """
    today = _as_date(today)
    if today is None:
        raise ValueError("today must be a usable date")

    d = _as_date(statement_date)
    if d is None:
        return ["statement date missing or unreadable"]

    problems = []
    if d > today:
        problems.append("statement date in the future")
    if d < _MIN_STATEMENT_DATE:
        problems.append("statement date before 2000-01-01")
    last = _as_date(last_known)
    if last is not None:
        gap = (d - last).days
        if not 1 <= gap <= _LAST_STATEMENT_MAX_GAP_DAYS:
            problems.append(
                f"statement date not 1-{_LAST_STATEMENT_MAX_GAP_DAYS} days "
                "after the Date of Last Statement on the same PDF"
            )
    return problems


_CENT = Decimal("0.01")


def _section_currency(section: dict) -> str:
    """The section's currency, upper-cased and stripped (matching key)."""
    return str(section.get('currency') or 'CAD').upper().strip()


def cash_gate_details(section: dict) -> dict:
    """Cash-gate details for one currency section (step 13d).

    Returns a dict with ``opening``, ``credits``, ``debits`` and ``closing``
    (Decimals), ``difference`` (``closing - (opening + credits - debits)``,
    zero when the gate passes), ``ok`` (bool) and ``reasons`` (the same list
    :func:`reconcile_section` returns).

    The gate is a naive sum over **all** transactions in the section, no
    activity type excluded; a section with no transactions must have equal
    opening and closing cash (same formula, empty sums). Cash = the balance
    rows whose ``account_type`` lowercases to ``cash`` (mirrors
    ``_section_cash`` in imports/parsers_pdf.py); a section with no such row
    fails. The amounts are returned so the command can print the gate line;
    the reasons name only field names and the currency — never amounts,
    quantities, descriptions or digits beyond the currency code.
    """
    cur = _section_currency(section)
    cash_rows = [
        b for b in section.get('balances') or []
        if str(b.get('account_type', '')).strip().lower() == 'cash'
    ]
    if not cash_rows:
        return {
            'opening': Decimal('0'), 'credits': Decimal('0'),
            'debits': Decimal('0'), 'closing': Decimal('0'),
            'difference': Decimal('0'), 'ok': False,
            'reasons': [f'{cur} balances: no cash row'],
        }
    opening = sum(
        (Decimal(b.get('opening') or 0) for b in cash_rows), Decimal('0')
    )
    closing = sum(
        (Decimal(b.get('closing') or 0) for b in cash_rows), Decimal('0')
    )
    transactions = section.get('transactions') or []
    credits = sum(
        (Decimal(tx.get('credit') or 0) for tx in transactions), Decimal('0')
    )
    debits = sum(
        (Decimal(tx.get('debit') or 0) for tx in transactions), Decimal('0')
    )
    expected = (opening + credits - debits).quantize(_CENT)
    ok = expected == closing.quantize(_CENT)
    return {
        'opening': opening, 'credits': credits, 'debits': debits,
        'closing': closing,
        'difference': (closing - expected).quantize(_CENT),
        'ok': ok,
        'reasons': [] if ok else [f'{cur} opening + credits - debits != closing'],
    }


def reconcile_section(section: dict) -> list[str]:
    """Cash-reconciliation reasons for one currency section (step 13c-b).

    Delegates to :func:`cash_gate_details`; empty list = the section
    reconciles.
    """
    return cash_gate_details(section)['reasons']


def self_check_vl_sections(sections, *, today) -> list[str]:
    """Self-check VL-parsed currency sections before they may be stored (13d).

    Per section runs:

    - the text-path checks of ``imports.parsers_pdf.self_check_sections``
      (imported lazily to avoid a cycle), after mapping the VL coercion's
      0 placeholders to None for holdings' ``book_cost`` / ``quantity`` /
      ``market_price`` / ``market_value`` so "unreadable" still fires (a
      missing number arrives as 0, tdw decision Q4);
    - the date checks of :func:`statement_date_problems` with the section's
      own *Date of Last Statement*.

    There is deliberately no begin-total check: VL's balances often list
    only cash for a USD section, so a begin total far above the balance sum
    is normal there; the begin total is checked against the previous
    month's end in step 14 instead.

    Reasons name only fields, currencies and symbols — never values.
    """
    from imports.parsers_pdf import self_check_sections  # lazy: keep acyclic

    reasons = []
    for section in sections or []:
        cur = _section_currency(section)

        copy = dict(section)
        holdings = []
        for h in section.get('holdings') or []:
            holding = dict(h)
            for field in ('book_cost', 'quantity', 'market_price', 'market_value'):
                if Decimal(holding.get(field) or 0) == 0:
                    holding[field] = None
            holdings.append(holding)
        copy['holdings'] = holdings
        reasons.extend(self_check_sections([copy]))

        problems = statement_date_problems(
            section.get('statement_date'), today=today,
            last_known=section.get('last_statement_date'),
        )
        reasons.extend(f'{cur} {problem}' for problem in problems)
    return reasons


def merge_and_reconcile(text_sections, vl_sections):
    """Match text and VL currency sections; gate the merged result (13c-b).

    Returns ``(merged, reasons)``. ``merged`` is a list of copies of the
    text sections with ``transactions`` replaced by the matching VL
    section's transactions (currency matched case-insensitively), or
    ``None`` when the currencies do not line up one-to-one. ``reasons``
    lists the currency mismatches (currency names only) and, when the match
    is one-to-one, the per-section cash-reconciliation reasons of the merged
    sections (:func:`reconcile_section`). Empty ``reasons`` = gate passed.
    """
    def _index(sections):
        by_cur = {}
        dupes = set()
        for sec in sections or []:
            cur = _section_currency(sec)
            if cur in by_cur:
                dupes.add(cur)
            else:
                by_cur[cur] = sec
        return by_cur, sorted(dupes)

    text_by_cur, text_dupes = _index(text_sections)
    vl_by_cur, vl_dupes = _index(vl_sections)
    reasons = [f'duplicated currency {c} in text sections' for c in text_dupes]
    reasons += [f'duplicated currency {c} in VL sections' for c in vl_dupes]
    for cur in sorted(set(text_by_cur) - set(vl_by_cur)):
        reasons.append(f'currency {cur} missing from VL sections')
    for cur in sorted(set(vl_by_cur) - set(text_by_cur)):
        reasons.append(f'currency {cur} missing from text sections')
    if reasons:
        return None, reasons

    merged = []
    for sec in text_sections or []:
        copy = dict(sec)
        copy['transactions'] = list(vl_by_cur[_section_currency(sec)].get('transactions') or [])
        merged.append(copy)
    reasons.extend(r for m in merged for r in reconcile_section(m))
    return merged, reasons


def is_content_imported(content_hash: str) -> bool:
    """True when a successful import log already has this content hash.

    Only ``success``/``ok`` logs count: a file that previously failed (and
    sits in ``errors/``) may be retried and is not a duplicate. This is the
    rule for the Holdings CSV import; PDFs use
    :func:`is_pdf_content_stored` instead.
    """
    from core.models import FileImportLog  # local: keep the module DB-free

    if not content_hash:
        return False
    return FileImportLog.objects.filter(
        content_hash=content_hash, status__in=("success", "ok")
    ).exists()


def is_pdf_content_stored(content_hash: str) -> bool:
    """True while stored statement rows carry this PDF's content hash.

    A PDF counts as already imported only while ``RbcMonthly`` or
    ``RbcAnnual`` rows with its ``content_hash`` exist — not because a
    ``FileImportLog`` row does: deleting the file's rows (e.g. to re-import
    a corrected statement) lets the same file be imported again, even if the
    import log remains.
    """
    from core.models import RbcAnnual, RbcMonthly  # local: keep the module DB-free

    if not content_hash:
        return False
    return (
        RbcMonthly.objects.filter(content_hash=content_hash).exists()
        or RbcAnnual.objects.filter(content_hash=content_hash).exists()
    )
