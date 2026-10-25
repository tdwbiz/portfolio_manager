"""Safe file names for imported statements (audit 4 batch D).

A safe name never contains the account number or its last 4 digits:

    2026-10-31_monthly_TOM_TFSA_a1b2c3d4.pdf
    2026_annual_TOM_TFSA_a1b2c3d4.pdf
    2026-10-31_unknown_UNKNOWN_ACCOUNT_a1b2c3d4.pdf

``hash8`` is the first 8 hex characters of SHA-256 of the file **content**
(tdw decision, Oct 25): a hash of the original *name* would be brute-forceable
because the name carries the account number. The mapping stays deterministic
for re-imports (same content → same name), which the duplicate check relies on.
"""

import hashlib
import re
from datetime import date as date_type
from pathlib import Path, PurePath

MONTHLY = "monthly"
ANNUAL = "annual"
UNKNOWN = "unknown"

KINDS = (MONTHLY, ANNUAL, UNKNOWN)

_CHUNK_SIZE = 1024 * 1024

_SAFE_NAME_RE = re.compile(
    r"^(?P<date>\d{4}(?:-\d{2}-\d{2})?)_"
    r"(?P<kind>monthly|annual|unknown)_"
    r"(?P<account>[A-Z0-9]+(?:_[A-Z0-9]+)*)_"
    r"(?P<hash>[0-9a-f]{8})\."
    r"(?P<ext>[A-Za-z0-9]+)$"
)


def hash8_of(data_or_path) -> str:
    """First 8 hex chars of SHA-256 of the file content.

    Accepts bytes/bytearray or a path (read in chunks). Never hashes a name.
    """
    if isinstance(data_or_path, (bytes, bytearray)):
        return hashlib.sha256(bytes(data_or_path)).hexdigest()[:8]
    digest = hashlib.sha256()
    with open(data_or_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()[:8]


def _sanitize_account_name(account_name: str) -> str:
    """Upper-case; every non-alphanumeric run becomes a single underscore."""
    cleaned = re.sub(r"[^A-Z0-9]+", "_", (account_name or "").upper()).strip("_")
    if not cleaned:
        raise ValueError("account name is empty")
    return cleaned


def _normalise_date(kind: str, date) -> str:
    if kind == MONTHLY:
        if isinstance(date, date_type):
            return date.isoformat()
        text = str(date or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise ValueError(f"monthly safe names need a YYYY-MM-DD date, got {text!r}")
        date_type.fromisoformat(text)  # validates real calendar dates
        return text
    if kind == ANNUAL:
        text = str(date or "").strip()
        if not re.fullmatch(r"\d{4}", text):
            raise ValueError(f"annual safe names need a YYYY year, got {text!r}")
        return text
    # unknown: date defaults to today
    if date is None:
        return date_type.today().isoformat()
    return _normalise_date(MONTHLY, date)


def safe_file_name(
    original_name: str,
    account_name: str,
    kind: str,
    *,
    date=None,
    hash8: str | None = None,
) -> str:
    """Build ``<date>_<kind>_<ACCOUNT_NAME>_<hash8>.<ext>``.

    ``original_name`` contributes only the extension (its stem carries the
    account number and is never used). ``hash8`` (see :func:`hash8_of`) is
    required. Raises ``ValueError`` for an unknown kind, missing hash8 or a
    malformed date/account name.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown safe-name kind: {kind!r}")
    if not hash8 or not re.fullmatch(r"[0-9a-f]{8}", str(hash8)):
        raise ValueError("hash8 (first 8 hex chars of the content SHA-256) is required")
    suffix = PurePath(original_name).suffix
    ext = suffix[1:] if suffix else "pdf"
    return f"{_normalise_date(kind, date)}_{kind}_{_sanitize_account_name(account_name)}_{hash8}.{ext}"


def is_safe_file_name(name: str) -> bool:
    """True if ``name`` matches the safe pattern exactly.

    Enforces kind/date consistency (monthly → YYYY-MM-DD, annual/unknown →
    YYYY or YYYY-MM-DD for unknown) so that only names produced by
    :func:`safe_file_name` round-trip as safe.
    """
    m = _SAFE_NAME_RE.match(name or "")
    if not m:
        return False
    kind = m.group("kind")
    date_part = m.group("date")
    if kind == MONTHLY:
        return len(date_part) == 10
    if kind == ANNUAL:
        return len(date_part) == 4
    return True
