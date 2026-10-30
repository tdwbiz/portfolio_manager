"""Views for the exceptions page (phase 4, step 14c).

Everything shown is names, dates, currencies, symbols and refs only —
never account digits. Accept/reopen change only the exception row.

Plan: docs/plans/exceptions-page.md (Decisions: tdw, Oct 30).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Case, Count, IntegerField, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

from core.models import Account, MonthlyException
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

    date_param = request.GET.get("statement_date", "").strip()
    parsed = parse_date(date_param) if date_param else None
    if parsed is not None:
        qs = qs.filter(statement_date=parsed)

    return qs


@login_required
def exceptions_list(request):
    """GET /exceptions/ — filterable list of MonthlyException rows.

    Ordering: status rank open -> accepted -> resolved, then taxable
    first (category == "TAXABLE"), then account name, statement_date,
    kind. Status counts are computed over the filtered queryset.
    """
    qs = _apply_list_filters(
        MonthlyException.objects.all()
        .select_related("account")
        .annotate(status_rank=_STATUS_RANK, taxable=_TAXABLE),
        request,
    ).order_by(
        "status_rank", "-taxable", "account__name", "statement_date", "kind"
    )

    status_counts = {s: 0 for s, _ in MonthlyException.STATUS_CHOICES}
    for row in qs.values("status").annotate(n=Count("pk")):
        status_counts[row["status"]] = row["n"]

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
            "statement_date": request.GET.get("statement_date", "").strip(),
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
