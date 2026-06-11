"""
Tax acknowledgement workflow for M8-Spec-1.

Provides the gate that prevents marking a tax-triggering recommendation
as 'actioned' until the user has acknowledged the tax impact.

Functions:
    can_mark_actioned(recommendation) -> (allowed: bool, reason: str)
    mark_actioned(recommendation, actioned_by: str) -> None
"""

from risk.models import StrategyRecommendation, TaxDecisionAcknowledgement


def can_mark_actioned(recommendation: StrategyRecommendation) -> tuple[bool, str]:
    """
    Check whether a recommendation can be marked as actioned.

    Returns:
        (allowed, reason_if_not)

    allowed = True if:
        - recommendation.status == 'active'
        - AND (recommendation.is_tax_triggering == False
               OR a TaxDecisionAcknowledgement exists for this recommendation)

    reason_if_not, when allowed=False:
        - "Recommendation is not active (status={status})."
        - "Tax impact must be acknowledged before this recommendation can be marked as actioned."
    """
    if recommendation.status != 'active':
        return False, f"Recommendation is not active (status={recommendation.status})."

    if recommendation.is_tax_triggering:
        has_ack = TaxDecisionAcknowledgement.objects.filter(
            recommendation_id=recommendation.id
        ).exists()
        if not has_ack:
            return False, (
                "Tax impact must be acknowledged before this recommendation can be "
                "marked as actioned."
            )

    return True, ""


def mark_actioned(recommendation: StrategyRecommendation, actioned_by: str) -> None:
    """
    Mark a recommendation as actioned.

    Sets status='actioned', actioned_at=now(), actioned_by=actioned_by.
    Raises ValueError (with the reason from can_mark_actioned) if not allowed.
    Does NOT silently no-op.
    """
    allowed, reason = can_mark_actioned(recommendation)
    if not allowed:
        raise ValueError(reason)

    from django.utils import timezone

    recommendation.status = 'actioned'
    recommendation.actioned_at = timezone.now()
    recommendation.actioned_by = actioned_by
    recommendation.save()
