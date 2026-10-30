"""Persistence helpers for ``monthly_check --store`` (phase 4, step 14b).

Turns a ``StatementCheck`` and the parked import list into exception dicts
and upserts them plus the per-currency verdicts. Everything stored or printed is names, dates, currencies, symbols and
amounts only — never account digits.

Plan: docs/plans/monthly-check-store.md (Decisions: tdw, Oct 30).
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from core.models import Account, MonthlyCheckResult, MonthlyException

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


def accept_exceptions(
    reason,
    *,
    row_id=None,
    account_name=None,
    kind=None,
    ref=None,
    month_start=None,
    month_end=None,
):
    """Accept open exceptions matching all filters (a note only).

    Same rules as the command's ``--accept``: only rows with status open are
    touched; ``ref`` is an exact match or a prefix ending in ``*`` (e.g.
    ``RBF*``); the month window bounds ``statement_date``. Each matched row
    gets ``status=accepted``, ``reason`` and ``accepted_at=now``. Never
    touches transactions, statements or results.

    Returns ``(count, per)`` where ``per`` maps (account name, kind) -> count.
    """
    qs = MonthlyException.objects.filter(
        status=MonthlyException.STATUS_OPEN
    ).select_related("account")
    if row_id is not None:
        qs = qs.filter(pk=row_id)
    if account_name:
        qs = qs.filter(account__name=account_name)
    if kind:
        qs = qs.filter(kind=kind)
    if ref:
        if ref.endswith("*"):
            qs = qs.filter(ref__startswith=ref[:-1])
        else:
            qs = qs.filter(ref=ref)
    if month_start is not None:
        qs = qs.filter(statement_date__gte=month_start)
    if month_end is not None:
        qs = qs.filter(statement_date__lte=month_end)

    now = timezone.now()
    per = {}
    count = 0
    for row in qs:
        row.status = MonthlyException.STATUS_ACCEPTED
        row.reason = reason
        row.accepted_at = now
        row.save()
        key = (row.account.name, row.kind)
        per[key] = per.get(key, 0) + 1
        count += 1
    return count, per


def reopen_exception(row_id, reason):
    """Reopen one exception: status open + reason note appended to detail.

    Same rules as the command's ``--reopen``: ``resolved_at`` and
    ``accepted_at`` are cleared and ``[reopened YYYY-MM-DD: reason]`` is
    appended to ``detail``. Returns the row, or None when no exception has
    that id.
    """
    row = (
        MonthlyException.objects.filter(pk=row_id)
        .select_related("account")
        .first()
    )
    if row is None:
        return None
    now = timezone.now()
    row.status = MonthlyException.STATUS_OPEN
    row.resolved_at = None
    row.accepted_at = None
    note = f"[reopened {now:%Y-%m-%d}: {reason}]"
    row.detail = (row.detail + "\n" if row.detail else "") + note
    row.save()
    return row


def store_results(results):
    """Upsert one MonthlyCheckResult per (group, currency verdict).

    ``results`` is a list of (StatementGroup, StatementCheck). Every verdict
    is stored, FIRST and NO_CSV included (decision 6), so the table is a
    complete per-month picture. ``checked_at`` is set only on create; re-runs
    update verdict/notes in place and never create a second row for the same
    key. Returns the number of rows written.
    """
    now = timezone.now()
    count = 0
    for group, check in results:
        for cur in check.currencies:
            # checked_at is set only on create (get_or_create defaults apply
            # only when the row is created); a re-run updates verdict/notes
            # in place and keeps the original checked_at.
            obj, _created = MonthlyCheckResult.objects.get_or_create(
                account_id=group.account_pk,
                statement_date=group.statement_date,
                currency=cur.currency,
                defaults={"checked_at": now},
            )
            obj.verdict = cur.verdict
            obj.notes = "\n".join(cur.notes)
            obj.save()
            count += 1
    return count


def store_exceptions(account, statement_date, found):
    """Upsert the exceptions found for one (account, statement_date).

    ``found`` is a list of exception dicts (kind / currency / ref /
    difference / detail). Rules (Decisions 1 and 5, tdw, Oct 30):
    - not stored yet -> created open (first_seen = last_seen = now);
    - open or accepted -> detail, difference and last_seen updated; an
      accepted row whose non-NULL difference changed goes back to open with
      a note appended to detail — a NULL difference never auto-reopens;
    - resolved but found again -> back to open (resolved_at cleared);
    - stored rows not in ``found`` -> resolved (resolved_at = now).

    Returns (created, updated, reopened, resolved) counts.
    """
    now = timezone.now()
    found_keys = {(e["kind"], e["currency"], e["ref"]) for e in found}
    created = updated = reopened = resolved_count = 0
    for exc in found:
        # first_seen/last_seen are non-NULL: defaults cover the create path
        # (a re-run never touches first_seen).
        row, was_created = MonthlyException.objects.get_or_create(
            kind=exc["kind"],
            account=account,
            statement_date=statement_date,
            currency=exc["currency"],
            ref=exc["ref"],
            defaults={
                "status": MonthlyException.STATUS_OPEN,
                "first_seen": now,
                "last_seen": now,
            },
        )
        detail = exc["detail"]
        if was_created:
            row.status = MonthlyException.STATUS_OPEN
            row.first_seen = now
            created += 1
        else:
            updated += 1
            if row.status == MonthlyException.STATUS_RESOLVED:
                # Vanished and then reappeared (decision 5).
                row.status = MonthlyException.STATUS_OPEN
                row.resolved_at = None
            elif (
                row.status == MonthlyException.STATUS_ACCEPTED
                and row.difference is not None
                and exc["difference"] is not None
                and exc["difference"] != row.difference
            ):
                row.status = MonthlyException.STATUS_OPEN
                detail += (
                    f"\n[reopened {now:%Y-%m-%d}: difference changed from "
                    f"{row.difference} to {exc['difference']}]"
                )
                reopened += 1
        row.difference = exc["difference"]
        row.detail = detail
        row.last_seen = now
        row.save()

    for row in MonthlyException.objects.filter(
        account=account, statement_date=statement_date
    ):
        if (row.kind, row.currency, row.ref) in found_keys:
            continue
        if row.status != MonthlyException.STATUS_RESOLVED:
            row.status = MonthlyException.STATUS_RESOLVED
            row.resolved_at = now
            row.save()
            resolved_count += 1
    return created, updated, reopened, resolved_count
