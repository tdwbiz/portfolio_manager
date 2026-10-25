"""Account fingerprints: keyed hashes of normalised RBC account numbers.

The full account number is never stored, printed or logged — only its
HMAC-SHA256 fingerprint (``Account.fingerprint``) and the last 4 digits
(``Account.last_4``). The HMAC key comes from ``ACCOUNT_FINGERPRINT_KEY``
in the environment; it must be at least 32 characters. With an empty or
too-short key, every call fails loudly with ``ValueError`` at the point of
use (imports and the one-off command surface it as an error).
"""

import hashlib
import hmac
import re

from django.conf import settings

MIN_KEY_LENGTH = 32

# Scrub candidates: IB ids (U/F + 7+ digits) and runs of digits with
# optional embedded dashes. A space ends a candidate, so only the
# account-number part of e.g. "90000003 12345678" is ever replaced.
_SCRUB_CANDIDATE_RE = re.compile(r"\b[UF]\d{7,}\b|\d[\d-]*")

_MIN_RUN_DIGITS = 8


def normalize_account_number(raw: str) -> str:
    """Normalise an account number for fingerprinting.

    Strip everything that is not a letter or digit, upper-case; if the
    result is all digits and has >= 8 digits, keep the first 8 (same rule
    as ``_normalize_rbc_account_id`` in manage_inbox_import / auto_import,
    so ``900-00003-1-8``, ``9000000318`` and ``90000003`` all normalise to
    ``90000003``).
    """
    cleaned = "".join(ch for ch in (raw or "") if ch.isalnum()).upper()
    if cleaned.isdigit() and len(cleaned) >= 8:
        cleaned = cleaned[:8]
    return cleaned


def _get_key() -> str:
    key = (getattr(settings, "ACCOUNT_FINGERPRINT_KEY", "") or "").strip()
    if len(key) < MIN_KEY_LENGTH:
        raise ValueError(
            f"ACCOUNT_FINGERPRINT_KEY is not set (need at least {MIN_KEY_LENGTH} characters)"
        )
    return key


def fingerprint_account_number(raw: str) -> str:
    """Hex HMAC-SHA256 of the normalised account number.

    Raises ``ValueError`` if the fingerprint key is empty or too short.
    """
    digest = hmac.new(
        _get_key().encode("utf-8"),
        normalize_account_number(raw).encode("utf-8"),
        hashlib.sha256,
    )
    return digest.hexdigest()


def last4(raw: str) -> str:
    """Last 4 characters of the normalised account number."""
    return normalize_account_number(raw)[-4:]


def lookup_account(raw: str):
    """Return the ``Account`` whose fingerprint matches ``raw``, or ``None``.

    Raises ``ValueError`` if the fingerprint key is not set.
    """
    from core.models import Account  # local import to avoid a cycle at module load

    return Account.objects.filter(fingerprint=fingerprint_account_number(raw)).first()


def wrap_tx_hash(raw_hash: str) -> str:
    """HMAC-SHA256 of a computed tx hash, keyed by ``ACCOUNT_FINGERPRINT_KEY``.

    The value stored in ``Transaction.tx_hash`` is this wrapped digest; the
    raw SHA-256 from ``compute_tx_hash`` is never stored. Raises
    ``ValueError`` if the fingerprint key is empty or too short.
    """
    return hmac.new(
        _get_key().encode("utf-8"),
        (raw_hash or "").encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _is_scrub_candidate(candidate: str) -> bool:
    """True for IB ids and digit runs with at least 8 digits."""
    if any(ch.isalpha() for ch in candidate):
        return True  # IB id (U/F + digits)
    return len(re.sub(r"\D", "", candidate)) >= _MIN_RUN_DIGITS


def scrub_text(text: str) -> tuple:
    """Replace real account numbers in free text with the account name.

    Returns ``(cleaned, matched, replaced, unmatched)`` where ``matched`` is
    the number of candidates that matched a stored fingerprint, ``replaced``
    the number of spans rewritten and ``unmatched`` the number of
    look-alike candidates left untouched. Only an exact fingerprint match is
    replaced, so dates (``2026-10-21``), amounts and other look-alikes stay.
    Never returns or prints the original number. Raises ``ValueError`` if
    the fingerprint key is empty or too short.
    """
    from core.models import Account  # local import to avoid a cycle at module load

    if not text:
        return (text or "", 0, 0, 0)

    matched = replaced = unmatched = 0
    out = []
    pos = 0
    for m in _SCRUB_CANDIDATE_RE.finditer(text):
        candidate = m.group(0)
        if not _is_scrub_candidate(candidate):
            continue
        account = Account.objects.filter(
            fingerprint=fingerprint_account_number(candidate)
        ).first()
        if account is None:
            unmatched += 1
            continue
        matched += 1
        replaced += 1
        out.append(text[pos:m.start()])
        out.append(account.name)
        pos = m.end()
    out.append(text[pos:])
    return ("".join(out), matched, replaced, unmatched)
