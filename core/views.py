import logging
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.models import Account, CoreAnnualReport, CoreMonthlyStatement, TransactionType
from core.utils import fetch_last_prices as utils_fetch_last_prices  # noqa: F401
from core.utils.gap_analysis import all_accounts_gap_analysis
from core.utils.reconciliation import reconcile_account as reconcile_fn
from core.utils.statements import generate_annual_reports

logger = logging.getLogger(__name__)


# SECURITY: Helper functions for authorization and rate limiting
def _check_login_attempts(ip_address):
    """Check if IP address has exceeded login attempt limit."""
    cache_key = f"login_attempts:{ip_address}"
    attempts = cache.get(cache_key, 0)
    return not attempts >= settings.LOGIN_ATTEMPT_LIMIT


def _record_login_attempt(ip_address):
    """Record a failed login attempt for rate limiting."""
    cache_key = f"login_attempts:{ip_address}"
    attempts = cache.get(cache_key, 0)
    cache.set(cache_key, attempts + 1, settings.LOGIN_ATTEMPT_WINDOW)


def _get_client_ip(request):
    """Get client IP address from request, accounting for proxies."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDER_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR")
    return ip


def _is_path_safe(file_path, base_dir):
    """Prevent path traversal attacks by ensuring file_path is within base_dir."""
    file_path = Path(file_path).resolve()
    base_dir = Path(base_dir).resolve()
    try:
        file_path.relative_to(base_dir)
        return True
    except ValueError:
        return False


def _validate_date_param(date_str):
    """Validate date parameter to prevent injection."""
    if not date_str:
        return None
    parsed = parse_date(date_str)
    if parsed is None:
        raise ValueError(f"Invalid date format: {date_str}")
    return parsed


def _parse_account_pk(value):
    """?account= / POST account carries Account.pk (step 9). Return int pk or None."""
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def is_safe_url(url: str, allowed_hosts: set | None = None) -> bool:
    """Validate that a URL is safe for redirects.

    Prevents open redirect attacks by ensuring the URL points to an
    allowed host (the current Django site's host).

    Args:
        url: The URL to validate.
        allowed_hosts: Set of allowed hostnames. Defaults to Django's
            ALLOWED_HOSTS.

    Returns:
        True if the URL is safe, False otherwise.
    """
    if not url:
        return False

    # Reject URLs with protocol or absolute URLs to other domains
    if "://" in url:
        parsed = re.match(r"^https?://([^/]+)", url)
        if parsed:
            host = parsed.group(1).lower()
            if allowed_hosts is None:
                allowed_hosts = set(settings.ALLOWED_HOSTS)
            return host in allowed_hosts

    # Relative URLs are always safe
    return url.startswith("/") and not url.startswith("//")


TRANSACTION_TYPE_LABELS = {
    "BUY": "Buy",
    "SELL": "Sell",
    "DIV": "Dividend",
    "DIST": "Distribution",
    "INT": "Interest",
    "WITHTAX": "Withholding Tax",
    "TAX": "Tax",
}
POSITION_TRANSACTION_TYPES = ("BUY", "SELL")
CASH_ACTIVITY_TRANSACTION_TYPES = (
    "BUY",
    "SELL",
    "DIV",
    "DIST",
    "INT",
    "WITHTAX",
    "TAX",
)
INCOME_TRANSACTION_TYPES = ("DIV", "DIST", "INT")
TAX_TRANSACTION_TYPES = ("WITHTAX", "TAX")
TRANSACTION_EXPORT_HEADERS = [
    "Date",
    "Symbol",
    "Type",
    "Quantity",
    "Price",
    "Amount",
    "Currency",
    "Amount (CAD)",
    "Exchange Rate",
    "Account",
    "Broker",
    "Notes",
]


def _get_transaction_type_options(codes=None):
    requested_codes = (
        tuple(codes) if codes is not None else tuple(TRANSACTION_TYPE_LABELS.keys())
    )
    type_map = {
        tx_type.code: tx_type.label
        for tx_type in TransactionType.objects.filter(
            code__in=requested_codes
        ).order_by("code")
    }

    options = [
        {
            "code": code,
            "label": type_map.get(
                code, TRANSACTION_TYPE_LABELS.get(code, code.title())
            ),
        }
        for code in requested_codes
    ]

    if codes is None:
        known_codes = {option["code"] for option in options}
        for tx_type in TransactionType.objects.exclude(code__in=known_codes).order_by(
            "code"
        ):
            options.append({"code": tx_type.code, "label": tx_type.label})

    return options


class _ReportSummaryRow:
    """Fake object to bridge RbcAnnual flat table to the template that expects a report object.

    The template expects: summary.report.report_year, summary.report.account.name,
    summary.report.id, summary.report.balance_snapshots, summary.report.account_type_code.
    """

    def __init__(self, cad_record):
        self._cad = cad_record
        # The template accesses summary.report.* — expose the RbcAnnual as .report
        self.report = cad_record  # RbcAnnual instance

    @property
    def ending_value(self):
        return self._cad.bal_ending_value if self._cad else Decimal(0)

    @property
    def net_return(self):
        return self._cad.bal_net_return if self._cad else Decimal(0)

    @property
    def total_charges_cad(self):
        return self._cad.chg_total_paid if self._cad else Decimal(0)

    @property
    def total_compensation_cad(self):
        return self._cad.comp_total if self._cad else Decimal(0)

    @property
    def balance_snapshots(self):
        """Return a simple queryset-like object with balance data from the CAD record."""
        if not self._cad:
            return _EmptyQuerySet([])
        return _EmptyQuerySet(
            [
                {
                    "currency": "CAD",
                    "beginning_market_value": self._cad.bal_beginning_value,
                    "ending_market_value": self._cad.bal_ending_value,
                    "net_investment_return": self._cad.bal_net_return,
                }
            ]
        )


class _EmptyQuerySet:
    """Minimal queryset-like wrapper for template rendering."""

    def __init__(self, items):
        self._items = items

    def __iter__(self):
        return iter(self._items)

    def exists(self):
        return bool(self._items)


def health_check(request):
    """Simple health check endpoint for load balancers and monitoring.

    Returns 200 OK with a JSON body when the application is healthy.
    """
    from django.http import JsonResponse

    return JsonResponse({"status": "ok", "timestamp": timezone.now().isoformat()})


def gap_analysis(request):
    """Show per-account data completeness report."""
    data = all_accounts_gap_analysis()
    return render(
        request,
        "core/gap_analysis.html",
        {
            "results": data,
        },
    )


def generate_monthly_statements_view(request):
    """Show the existing monthly statements for an account (read-only).

    Rows are written by the ``generate_statements`` command; this page only
    displays them (plan docs/plans/statements-forward.md, decision 6).
    """
    account_param = request.GET.get("account")  # Account.pk
    account_pk = _parse_account_pk(account_param)
    message = ""
    stmts = []

    if account_pk is not None:
        try:
            account = Account.objects.get(pk=account_pk)
            stmts = CoreMonthlyStatement.objects.filter(account=account).order_by(
                "-statement_month", "currency"
            )[:100]
            if stmts:
                message = (
                    f"Showing {stmts.count()} existing monthly statements for {account}."
                )
            else:
                message = (
                    f"No monthly statements found for {account}. Use the "
                    f"generate_statements command to create them."
                )
        except Account.DoesNotExist:
            message = f"Account '{account_param}' not found."

    accounts = Account.objects.all()
    return render(
        request,
        "core/generate_statements.html",
        {
            "accounts": accounts,
            "selected_account": account_pk,
            "message": message,
            "statements": list(stmts),
        },
    )


def generate_annual_reports_view(request):
    """Show or regenerate annual reports for an account.

    GET without 'regenerate': show existing reports.
    GET with 'regenerate=1': regenerate from monthly statements.
    """
    account_param = request.GET.get("account")  # Account.pk
    account_pk = _parse_account_pk(account_param)
    regenerate = request.GET.get("regenerate", "") == "1"
    message = ""
    reports = []

    if account_pk is not None:
        try:
            account = Account.objects.get(pk=account_pk)

            if regenerate:
                reports = generate_annual_reports(account)
                message = f"Regenerated {len(reports)} annual reports for {account}."
            else:
                # Show existing reports
                reports = CoreAnnualReport.objects.filter(account=account).order_by(
                    "-report_year", "currency"
                )[:100]
                if reports:
                    message = (
                        f"Showing {reports.count()} existing annual reports for {account}."
                    )
                else:
                    message = (
                        f"No annual reports found for {account}. "
                        f"Click 'Regenerate' to create from monthly statements."
                    )
        except Account.DoesNotExist:
            message = f"Account '{account_param}' not found."

    accounts = Account.objects.all()
    return render(
        request,
        "core/generate_annual.html",
        {
            "accounts": accounts,
            "selected_account": account_pk,
            "message": message,
            "reports": list(reports),
            "regenerate": regenerate,
        },
    )


def reconcile_view(request):
    """Reconcile CSV-generated data against RBC PDF data."""
    account_id = request.GET.get("account")
    period_type = request.GET.get("type", "monthly")
    message = ""
    reconciliations = []

    if account_id:
        try:
            account = Account.objects.get(name=account_id)
            reconciliations = reconcile_fn(account, period_type=period_type)
            matches = sum(1 for r in reconciliations if r.matching)
            discrepancies = sum(1 for r in reconciliations if not r.matching)
            message = (
                f"Reconciled {len(reconciliations)} periods for {account}: "
                f"{matches} matches, {discrepancies} discrepancies."
            )
        except Account.DoesNotExist:
            message = f"Account '{account_id}' not found."

    accounts = Account.objects.all()
    return render(
        request,
        "core/reconciliation.html",
        {
            "accounts": accounts,
            "selected_account": account_id,
            "period_type": period_type,
            "message": message,
            "reconciliations": reconciliations[:100],
        },
    )
