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

from django.conf import settings

MIN_KEY_LENGTH = 32


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
