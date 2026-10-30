"""Persistence helpers for ``monthly_check --store`` (phase 4, step 14b).

Pure functions over the check dataclasses (plus reads of Account) that turn
a ``StatementCheck`` and the parked import list into exception dicts. No DB
writes in this module yet — upserts live in ``store_results`` /
``store_exceptions`` (step 3). Everything stored or printed is names, dates,
currencies, symbols and amounts only — never account digits.

Plan: docs/plans/monthly-check-store.md (Decisions: tdw, Oct 30).
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from core.models import Account

# Parked safe names carry a full statement date (monthly statements are
# month-end dated); anything else is skipped and printed.
_PARKED_NAME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_monthly_"
    r"(?P<account>[A-Z0-9]+(?:_[A-Z0-9]+)*)_"
    r"(?P<hash>[0-9a-f]{8})\.[A-Za-z0-9]+$"
)

_NUM = r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?"
_CASH_DIFF_RE = re.compile(rf", diff ({_NUM})$")
_CHAIN_DIFF_RE = re.compile(rf"\(diff ({_NUM})\)")
_QUANTITY_RE = re.compile(
    rf"^QUANTITY: (?P<sym>.+?): expected (?P<exp>{_NUM}) "
    rf"\(prev {_NUM} \+ csv {_NUM}\), got (?P<act>{_NUM})$"
)


def _decimal(text):
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def exceptions_from_check(check):
    """Group a StatementCheck's failure lines into exception dicts.

    One dict per (kind, currency, ref), sorted by that key:
    - ``CASH:`` lines -> kind CASH, currency = the result's currency, ref "";
      difference parsed from the line ending in ``diff <amount>`` (None when
      no line carries one).
    - ``CHAIN:`` lines -> kind CHAIN, same currency, ref ""; difference parsed
      from ``(diff <amount>)``.
    - ``QUANTITY: <sym>:`` lines -> kind QUANTITY, currency "" (per account),
      ref = the symbol; difference = got - expected. The pooled failures are
      attached to every currency by ``check_statement``, so they deduplicate
      naturally on this key.

    ``detail`` is all grouped lines joined by newline — this keeps the
    unmatched CSV/PDF rows inside the exception (decision 1). Raises
    ValueError on a failure line that cannot be attributed to a
    kind/currency/ref (stop condition in the plan).
    """
    groups = {}
    for cur in check.currencies:
        for line in cur.failures:
            if line.startswith("CASH:"):
                key = ("CASH", cur.currency, "")
                match = _CASH_DIFF_RE.search(line)
                difference = _decimal(match.group(1)) if match else None
            elif line.startswith("CHAIN:"):
                key = ("CHAIN", cur.currency, "")
                match = _CHAIN_DIFF_RE.search(line)
                difference = _decimal(match.group(1)) if match else None
            else:
                match = _QUANTITY_RE.match(line)
                if match is None:
                    raise ValueError(f"unattributable failure line: {line}")
                key = ("QUANTITY", "", match.group("sym"))
                difference = Decimal(match.group("act")) - Decimal(
                    match.group("exp")
                )
            entry = groups.setdefault(
                key,
                {
                    "kind": key[0],
                    "currency": key[1],
                    "ref": key[2],
                    "difference": None,
                    "lines": [],
                },
            )
            # The pooled QUANTITY lines are attached to every currency; the
            # identical line per currency deduplicates here (decision 2).
            if line not in entry["lines"]:
                entry["lines"].append(line)
            if difference is not None and entry["difference"] is None:
                entry["difference"] = difference

    out = []
    for key in sorted(groups):
        entry = groups[key]
        entry["detail"] = "\n".join(entry["lines"])
        del entry["lines"]
        out.append(entry)
    return out


def import_exceptions_from_parked(parked):
    """Parked entries -> (exceptions, skipped) for kind IMPORT.

    Each parked safe name ``YYYY-MM-DD_monthly_<ACCOUNT>_<hash8>.pdf`` gives
    the statement_date, the account name and ref = hash8; detail is the
    reason(s); difference stays None (decision 5). Entries whose account name
    matches no Account, or whose file name does not parse to a full date,
    come back in ``skipped`` — printed by the command, never stored
    (decision 4). Conflicts are never passed in.
    """
    names = set(Account.objects.values_list("name", flat=True))
    exceptions, skipped = [], []
    for entry in parked:
        match = _PARKED_NAME_RE.match(entry["filename"] or "")
        if not match or match.group("account") not in names:
            skipped.append(entry)
            continue
        try:
            statement_date = date.fromisoformat(match.group("date"))
        except ValueError:
            skipped.append(entry)
            continue
        exceptions.append(
            {
                "kind": "IMPORT",
                "account_name": match.group("account"),
                "statement_date": statement_date,
                "currency": "",
                "ref": match.group("hash"),
                "difference": None,
                "detail": entry["reason"],
            }
        )
    return exceptions, skipped
