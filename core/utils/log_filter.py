"""Logging filter that scrubs account numbers out of every log record.

Built on ``scrub_text`` (exact fingerprint match → account name; exactly-15-digit
runs → ``BANK_ACCOUNT``). The fingerprint→name map is cached with a fixed
300-second TTL (tdw decision, Oct 25: no env setting).

The filter does **not** fail open: when the name map cannot be built (no DB,
query error) or ``scrub_text`` raises (missing/short fingerprint key), it falls
back to replacing every run of exactly 15 digits with ``BANK_ACCOUNT`` and
every remaining run of ≥ 8 digits with ``ACCOUNT``. It never raises inside
``logging`` — any unexpected error leaves the record untouched.
"""

import logging
import re
import time

from core.utils.account_fingerprint import build_fingerprint_name_map, scrub_text

# Fixed cache TTL in code (no env setting — tdw decision, Oct 25).
CACHE_TTL_SECONDS = 300

_BANK_ACCOUNT_RE = re.compile(r"\b\d{15}\b")
_LONG_DIGIT_RUN_RE = re.compile(r"\b\d{8,}\b")


def fallback_scrub(text: str) -> str:
    """Degraded scrubbing used when the fingerprint name map is unavailable.

    Exactly-15-digit runs (outside bank accounts) become ``BANK_ACCOUNT``;
    every remaining run of ≥ 8 digits becomes ``ACCOUNT``. Dashes and shorter
    runs are left alone.
    """
    text = _BANK_ACCOUNT_RE.sub("BANK_ACCOUNT", text)
    return _LONG_DIGIT_RUN_RE.sub("ACCOUNT", text)


class ScrubFilter(logging.Filter):
    """logging.Filter that rewrites account numbers out of record messages."""

    # Class-level cache shared by every filter instance: {'ts': float, 'map': dict | None}
    _cache: dict = {}

    @classmethod
    def reset_cache(cls) -> None:
        cls._cache = {}

    def _get_name_map(self):
        now = time.monotonic()
        cached = self._cache
        if "ts" in cached and now - cached["ts"] < CACHE_TTL_SECONDS:
            return cached.get("map")
        try:
            name_map = build_fingerprint_name_map()
        except Exception:
            name_map = None  # cache the failure for the TTL as well
        ScrubFilter._cache = {"ts": now, "map": name_map}  # class-level: shared
        return name_map

    def _scrub(self, msg: str) -> str:
        name_map = self._get_name_map()
        if name_map:
            try:
                return scrub_text(msg, name_map)[0]
            except Exception:
                pass  # e.g. missing/short fingerprint key → degraded mode
        return fallback_scrub(msg)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if not isinstance(msg, str) or not msg:
                return True
            cleaned = self._scrub(msg)
            if cleaned != msg:
                record.msg = cleaned
                record.args = None
        except Exception:
            # Never raise inside logging; worst case the record passes through.
            pass
        return True
