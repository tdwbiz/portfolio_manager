"""Views for the exceptions page (phase 4, step 14c).

Everything shown is names, dates, currencies, symbols and refs only —
never account digits. Accept/reopen change only the exception row.

Plan: docs/plans/exceptions-page.md (Decisions: tdw, Oct 30).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Case, Count, IntegerField, Value, When
import re

from django.shortcuts import get_object_or_404, redirect, render

from core.models import Account, MonthlyCheckResult, MonthlyException
from core.utils.monthly_check_store import accept_exceptions, reopen_exception

_STATUS_RANK = Case(
    When(status=MonthlyException.STATUS_OPEN, then=Value(0)),
    When(status=MonthlyException.STATUS_ACCEPTED, then=Value(1)),
    When(status=MonthlyException.STATUS_RESOLVED, then=Value(2)),
    default=Value(3),
    output_field=IntegerField(),
)

_TAXABLE = Case(
    When(account__category="TAXABLE", then=Value(1)),
    default=Value(0),
    output_field=IntegerField(),
)


def _apply_list_filters(qs, request):
    """Apply the optional GET filters; invalid values are ignored."""
    account_param = request.GET.get("account", "").strip()
    if account_param:
        try:
            account_pk = int(account_param)
        except ValueError:
            pass
        else:
            if Account.objects.filter(pk=account_pk).exists():
                qs = qs.filter(account_id=account_pk)

    kind = request.GET.get("kind", "").strip().upper()
    if kind in dict(MonthlyException.KIND_CHOICES):
        qs = qs.filter(kind=kind)

    status = request.GET.get("status", "").strip().lower()
    if status in dict(MonthlyException.STATUS_CHOICES):
        qs = qs.filter(status=status)

    year_param = request.GET.get("year", "").strip()
    if year_param:
        try:
            year = int(year_param)
        except ValueError:
            pass
        else:
            qs = qs.filter(statement_date__year=year)

    month_param = request.GET.get("month", "").strip()
    match = re.fullmatch(r"(\d{4})-(\d{2})", month_param) if month_param else None
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            qs = qs.filter(
                statement_date__year=year, statement_date__month=month
            )

    return qs


@login_required
def exceptions_list(request):
    """GET /exceptions/ — filterable list of MonthlyException rows.

    Ordering: status rank open -> accepted -> resolved, then taxable
    first (category == "TAXABLE"), then account name, statement_date,
    kind. Status counts are computed over the filtered queryset.
    """
    filtered = _apply_list_filters(
        MonthlyException.objects.all().select_related("account"), request
    )
    # Counts come from the clean (unannotated, unsorted) queryset: the list
    # annotations and ordering would otherwise leak into the GROUP BY.
    status_counts = {s: 0 for s, _ in MonthlyException.STATUS_CHOICES}
    for row in filtered.values("status").annotate(n=Count("pk")):
        status_counts[row["status"]] = row["n"]

    qs = filtered.annotate(status_rank=_STATUS_RANK, taxable=_TAXABLE).order_by(
        "status_rank", "-taxable", "account__name", "statement_date", "kind"
    )

    context = {
        "exceptions": qs,
        "status_counts": status_counts,
        "kind_choices": MonthlyException.KIND_CHOICES,
        "status_choices": MonthlyException.STATUS_CHOICES,
        "accounts": Account.objects.order_by("name"),
        "filters": {
            "account": request.GET.get("account", "").strip(),
            "kind": request.GET.get("kind", "").strip().upper(),
            "status": request.GET.get("status", "").strip().lower(),
            "year": request.GET.get("year", "").strip(),
            "month": request.GET.get("month", "").strip(),
        },
    }
    return render(request, "core/exceptions_list.html", context)


@login_required
def exception_detail(request, pk):
    """GET/POST /exceptions/<pk>/ — one exception with its stored detail.

    POST (CSRF only) takes ``action`` = accept (only when status open) or
    reopen (only when status accepted) and a required ``reason``. A blank
    reason or the wrong status gets an error message and a redirect back
    with nothing changed; success redirects to the detail page.
    """
    exc = get_object_or_404(
        MonthlyException.objects.select_related("account"), pk=pk
    )

    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        reason = (request.POST.get("reason") or "").strip()
        if not reason:
            messages.error(request, "A reason is required.")
        elif action == "accept" and exc.status != MonthlyException.STATUS_OPEN:
            messages.error(
                request,
                f"Only open exceptions can be accepted (this one is {exc.status}).",
            )
        elif action == "reopen" and exc.status != MonthlyException.STATUS_ACCEPTED:
            messages.error(
                request,
                f"Only accepted exceptions can be reopened (this one is {exc.status}).",
            )
        elif action not in ("accept", "reopen"):
            messages.error(request, "Unknown action.")
        else:
            if action == "accept":
                count, _per = accept_exceptions(reason, row_id=pk)
                ok = count == 1
            else:
                ok = reopen_exception(pk, reason) is not None
            if ok:
                messages.success(
                    request,
                    f"Exception {action}d: {reason}",
                )
            else:
                messages.error(request, "Nothing was changed.")
        return redirect("exception_detail", pk=pk)

    context = {
        "exc": exc,
        "taxable": exc.account.category == "TAXABLE",
    }
    return render(request, "core/exception_detail.html", context)


_CHECK_KINDS = (
    MonthlyException.KIND_CASH,
    MonthlyException.KIND_CHAIN,
    MonthlyException.KIND_QUANTITY,
)

_STATE_LETTERS = {
    "PASS": "P",
    "FAIL": "F",
    "ACCEPTED": "A",
    "FIRST": "1",
    "NO_CSV": "\u2013",  # en dash
    "GAP": "\u00b7",  # middle dot
}

_STATE_CLASSES = {
    "PASS": "table-success",
    "FAIL": "table-danger",
    "ACCEPTED": "table-warning",
    "FIRST": "table-info",
    "NO_CSV": "table-secondary",
    "GAP": "",
}


def _combined_verdict(verdicts):
    """Combine the per-currency verdicts of one statement."""
    if MonthlyCheckResult.VERDICT_FAIL in verdicts:
        return MonthlyCheckResult.VERDICT_FAIL
    if MonthlyCheckResult.VERDICT_FIRST in verdicts:
        return MonthlyCheckResult.VERDICT_FIRST
    if MonthlyCheckResult.VERDICT_NO_CSV in verdicts:
        return MonthlyCheckResult.VERDICT_NO_CSV
    return MonthlyCheckResult.VERDICT_PASS


@login_required
def exceptions_coverage(request):
    """GET /exceptions/coverage/ — accounts x statement dates grid.

    Exactly two data queries: all MonthlyCheckResult rows and all
    MonthlyException rows (each select_related("account")); the grid is
    built in Python. Rows are all accounts, taxable first then by name;
    columns are every statement_date present in MonthlyCheckResult.
    """
    results = list(MonthlyCheckResult.objects.select_related("account"))
    exceptions = list(MonthlyException.objects.select_related("account"))

    results_by_pair = {}
    for r in results:
        results_by_pair.setdefault((r.account_id, r.statement_date), []).append(r)
    excs_by_pair = {}
    for e in exceptions:
        excs_by_pair.setdefault((e.account_id, e.statement_date), []).append(e)

    dates = sorted({r.statement_date for r in results})

    rows = []
    for acct in Account.objects.order_by("name"):
        taxable = acct.category == "TAXABLE"
        cells = []
        for d in dates:
            pair_results = results_by_pair.get((acct.pk, d), [])
            pair_excs = excs_by_pair.get((acct.pk, d), [])
            check_excs = [e for e in pair_excs if e.kind in _CHECK_KINDS]
            has_import = any(e.kind == MonthlyException.KIND_IMPORT for e in pair_excs)

            if not pair_results:
                state = "GAP"
            else:
                combined = _combined_verdict([r.verdict for r in pair_results])
                open_check = any(
                    e.status == MonthlyException.STATUS_OPEN for e in check_excs
                )
                all_accepted = bool(check_excs) and all(
                    e.status == MonthlyException.STATUS_ACCEPTED
                    for e in check_excs
                )
                if open_check:
                    state = "FAIL"
                elif combined == MonthlyCheckResult.VERDICT_FAIL and all_accepted:
                    state = "ACCEPTED"
                else:
                    state = combined

            letters = _STATE_LETTERS[state]
            if has_import:
                letters += " i"
            title_parts = [
                f"{r.currency} {r.verdict}"
                for r in sorted(pair_results, key=lambda r: r.currency)
            ]
            cells.append(
                {
                    "date": d,
                    "state": state,
                    "css_class": _STATE_CLASSES[state],
                    "letters": letters,
                    "title": (
                        f"{d} \u2014 {', '.join(title_parts)}"
                        if title_parts
                        else str(d)
                    ),
                }
            )
        rows.append({"account": acct, "taxable": taxable, "cells": cells})
    rows.sort(key=lambda row: (0 if row["taxable"] else 1, row["account"].name))

    # Year header groups over the date columns.
    year_groups = []
    for d in dates:
        if year_groups and year_groups[-1][0] == d.year:
            year_groups[-1][1].append(d)
        else:
            year_groups.append((d.year, [d]))

    context = {
        "rows": rows,
        "dates": dates,
        "year_groups": year_groups,
        "legend": [
            ("P", "PASS"),
            ("F", "FAIL"),
            ("A", "ACCEPTED (verdict FAIL, all check exceptions accepted)"),
            ("1", "FIRST (no previous statement to chain from)"),
            ("\u2013", "NO_CSV (no CSV coverage for the month)"),
            ("\u00b7", "no stored result (gap — normal)"),
            ("i", "IMPORT exception on that date"),
        ],
    }
    return render(request, "core/exceptions_coverage.html", context)

