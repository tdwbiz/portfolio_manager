"""
Email notification service for M7-Spec-1 alerting.

Provides ``send_alert_email`` which renders a Django template and sends
an email to the configured recipient(s).  Email failures are caught and
logged — they must never abort the calling command.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_FROM_EMAIL = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com')


def send_alert_email(
    recipient: str,
    alert_type: str,
    entity_description: str,
    trigger_value: str,
    previous_value: str,
    calc_date: date,
    symbol: str = '',
) -> bool:
    """
    Send a single alert notification email.

    Parameters
    ----------
    recipient : str
        Email address of the recipient.
    alert_type : str
        One of the 7 alert type identifiers.
    entity_description : str
        Human-readable description of the triggered entity.
    trigger_value : str
        The value that triggered the alert.
    previous_value : str
        The previous value (may be empty).
    calc_date : date
        The calculation date.
    symbol : str, optional
        Symbol if applicable (default '').

    Returns
    -------
    bool
        ``True`` if the email was sent successfully, ``False`` otherwise.
    """
    subject = f"[Portfolio Alert] {alert_type}: {entity_description}"

    context = {
        'alert_type': alert_type,
        'entity_description': entity_description,
        'trigger_value': trigger_value,
        'previous_value': previous_value,
        'calc_date': calc_date,
        'symbol': symbol,
    }

    html_message = render_to_string(
        'risk/email/alert_notification.html', context
    )
    plain_message = render_to_string(
        'risk/email/alert_notification.txt', context
    )

    try:
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            html_message=html_message,
            fail_silently=False,
        )
        logger.info("Alert email sent to %s for %s", recipient, alert_type)
        return True
    except Exception:
        logger.exception(
            "Failed to send alert email to %s for %s", recipient, alert_type
        )
        return False


def send_alert_digest(
    recipients: list[str],
    alerts: list[dict[str, Any]],
    calc_date: date,
) -> int:
    """
    Send a digest email listing all alerts for a given calc_date.

    Parameters
    ----------
    recipients : list[str]
        List of email addresses.
    alerts : list[dict]
        Alert dicts (as returned by detection functions).
    calc_date : date
        The calculation date.

    Returns
    -------
    int
        Number of recipients successfully emailed.
    """
    subject = f"[Portfolio Alert Digest] {len(alerts)} alert(s) for {calc_date}"

    context = {
        'alerts': alerts,
        'calc_date': calc_date,
        'alert_count': len(alerts),
    }

    html_message = render_to_string(
        'risk/email/alert_digest.html', context
    )
    plain_message = render_to_string(
        'risk/email/alert_digest.txt', context
    )

    sent_count = 0
    for recipient in recipients:
        try:
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                html_message=html_message,
                fail_silently=False,
            )
            sent_count += 1
        except Exception:
            logger.exception("Failed to send digest email to %s", recipient)

    logger.info("Alert digest sent to %d/%d recipients", sent_count, len(recipients))
    return sent_count
