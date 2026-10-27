"""Shared import checks (phase 3, step 13b — docs/plans/import-checks.md).

Helpers used by the PDF and Holdings-CSV import paths to refuse bad files:

- content-based duplicate detection (SHA-256 of the file bytes);
- statement/snapshot date sanity (future, before 2000, and the 1–100-day
  gap against the *Date of Last Statement* printed on the same PDF — never
  against database rows, so import order does not matter; tdw Oct 27).

Privacy: nothing here reads or logs account numbers, quantities or prices.
"""

import hashlib
import re
from datetime import date as date_type
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


def is_content_imported(content_hash: str) -> bool:
    """True when a successful import log already has this content hash.

    Only ``success``/``ok`` logs count: a file that previously failed (and
    sits in ``errors/``) may be retried and is not a duplicate.
    """
    from core.models import FileImportLog  # local: keep the module DB-free

    if not content_hash:
        return False
    return FileImportLog.objects.filter(
        content_hash=content_hash, status__in=("success", "ok")
    ).exists()
