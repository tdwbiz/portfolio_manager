"""Shared view helpers for risk module."""
from datetime import date
from core.models import Account
from risk.models import UrgencyScore


def get_latest_calc_date() -> date | None:
    """
    Return the most recent calc_date in rm_urgency_score.
    Returns None if no data has been calculated yet.
    """
    latest = UrgencyScore.objects.order_by('-calc_date').values_list('calc_date', flat=True).first()
    return latest


def get_show_excluded(request) -> bool:
    """
    Read the 'show_excluded' query param or session flag.
    When True, TAXABLE_CAR and any other exclude_from_risk accounts are included.
    Default: False (excluded accounts hidden).
    """
    return request.GET.get('show_excluded', 'false').lower() == 'true'


def get_excluded_account_ids() -> list:
    """Return list of account_ids where exclude_from_risk=True."""
    return list(
        Account.objects.filter(exclude_from_risk=True).values_list('account_id', flat=True)
    )
