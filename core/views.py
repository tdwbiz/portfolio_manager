from django.conf import settings
from django.core.cache import cache
from django.utils.dateparse import parse_date
import re
from pathlib import Path
from datetime import date
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

# SECURITY: Helper functions for authorization and rate limiting
def _check_login_attempts(ip_address):
    """Check if IP address has exceeded login attempt limit."""
    cache_key = f'login_attempts:{ip_address}'
    attempts = cache.get(cache_key, 0)
    if attempts >= settings.LOGIN_ATTEMPT_LIMIT:
        return False
    return True

def _record_login_attempt(ip_address):
    """Record a failed login attempt for rate limiting."""
    cache_key = f'login_attempts:{ip_address}'
    attempts = cache.get(cache_key, 0)
    cache.set(cache_key, attempts + 1, settings.LOGIN_ATTEMPT_WINDOW)

def _get_client_ip(request):
    """Get client IP address from request, accounting for proxies."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDER_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
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


def is_safe_url(url: str, allowed_hosts: set = None) -> bool:
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
    if '://' in url:
        parsed = re.match(r'^https?://([^/]+)', url)
        if parsed:
            host = parsed.group(1).lower()
            if allowed_hosts is None:
                allowed_hosts = set(settings.ALLOWED_HOSTS)
            return host in allowed_hosts

    # Relative URLs are always safe
    if url.startswith('/') and not url.startswith('//'):
        return True

    return False


from core.models import TransactionType
from core.utils import fetch_last_prices as utils_fetch_last_prices  # Re-export for modular views


TRANSACTION_TYPE_LABELS = {
    'BUY': 'Buy',
    'SELL': 'Sell',
    'DIV': 'Dividend',
    'DIST': 'Distribution',
    'INT': 'Interest',
    'WITHTAX': 'Withholding Tax',
    'TAX': 'Tax',
}
POSITION_TRANSACTION_TYPES = ('BUY', 'SELL')
CASH_ACTIVITY_TRANSACTION_TYPES = ('BUY', 'SELL', 'DIV', 'DIST', 'INT', 'WITHTAX', 'TAX')
INCOME_TRANSACTION_TYPES = ('DIV', 'DIST', 'INT')
TAX_TRANSACTION_TYPES = ('WITHTAX', 'TAX')
TRANSACTION_EXPORT_HEADERS = [
    'Date', 'Symbol', 'Type', 'Quantity', 'Price', 'Amount', 'Currency',
    'Amount (CAD)', 'Exchange Rate', 'Account', 'Broker', 'Notes',
]


def _get_transaction_type_options(codes=None):
    requested_codes = tuple(codes) if codes is not None else tuple(TRANSACTION_TYPE_LABELS.keys())
    type_map = {
        tx_type.code: tx_type.label
        for tx_type in TransactionType.objects.filter(code__in=requested_codes).order_by('code')
    }

    options = [
        {
            'code': code,
            'label': type_map.get(code, TRANSACTION_TYPE_LABELS.get(code, code.title())),
        }
        for code in requested_codes
    ]

    if codes is None:
        known_codes = {option['code'] for option in options}
        for tx_type in TransactionType.objects.exclude(code__in=known_codes).order_by('code'):
            options.append({'code': tx_type.code, 'label': tx_type.label})

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
        return self._cad.bal_ending_value if self._cad else Decimal('0')

    @property
    def net_return(self):
        return self._cad.bal_net_return if self._cad else Decimal('0')

    @property
    def total_charges_cad(self):
        return self._cad.chg_total_paid if self._cad else Decimal('0')

    @property
    def total_compensation_cad(self):
        return self._cad.comp_total if self._cad else Decimal('0')

    @property
    def balance_snapshots(self):
        """Return a simple queryset-like object with balance data from the CAD record."""
        if not self._cad:
            return _EmptyQuerySet([])
        return _EmptyQuerySet([{
            'currency': 'CAD',
            'beginning_market_value': self._cad.bal_beginning_value,
            'ending_market_value': self._cad.bal_ending_value,
            'net_investment_return': self._cad.bal_net_return,
        }])


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
    return JsonResponse({'status': 'ok', 'timestamp': timezone.now().isoformat()})
