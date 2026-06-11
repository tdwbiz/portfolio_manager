"""
Alert helpers for M7-Spec-1: badge count and acknowledgement.

These functions are called from views and the context processor to
display the unacknowledged alert count and process acknowledgements.
"""

from __future__ import annotations

from datetime import date

from django.db.models import Q
from risk.models import AlertHistory


def get_unacknowledged_alert_count() -> int:
    """
    Return the number of unacknowledged alerts from the last 7 days.

    Returns
    -------
    int
        Count of unacknowledged alerts.
    """
    week_ago = date.today()
    from datetime import timedelta
    week_ago = week_ago - timedelta(days=7)

    return AlertHistory.objects.filter(
        acknowledged_at__isnull=True,
        triggered_at__gte=week_ago,
    ).count()


def acknowledge_alert(alert_id: int, user: str = 'anonymous') -> bool:
    """
    Mark a single alert as acknowledged.

    Parameters
    ----------
    alert_id : int
        Primary key of the AlertHistory record.
    user : str
        Username performing the acknowledgement.

    Returns
    -------
    bool
        ``True`` if the alert was found and acknowledged, ``False`` otherwise.
    """
    try:
        alert = AlertHistory.objects.get(pk=alert_id)
        if not alert.acknowledged_at:
            alert.acknowledged_at = _now()
            alert.acknowledged_by = user
            alert.save(update_fields=['acknowledged_at', 'acknowledged_by'])
            return True
    except AlertHistory.DoesNotExist:
        pass
    return False


def acknowledge_alerts(alert_ids: list[int], user: str = 'anonymous') -> int:
    """
    Mark multiple alerts as acknowledged.

    Parameters
    ----------
    alert_ids : list[int]
        List of AlertHistory primary keys.
    user : str
        Username performing the acknowledgement.

    Returns
    -------
    int
        Number of alerts successfully acknowledged.
    """
    count = 0
    for alert_id in alert_ids:
        if acknowledge_alert(alert_id, user):
            count += 1
    return count


def _now():
    """Return current datetime (timezone-aware)."""
    from django.utils import timezone
    return timezone.now()
