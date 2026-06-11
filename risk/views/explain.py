"""
Explainability view for M8-Spec-1.

Provides a "Why is this entity at this urgency level?" view that reads
the audit snapshot for a specific UrgencyScore row and presents a ranked,
plain-English breakdown of contributing factors.

View:
    explain_score(request, urgency_score_id) -> HttpResponse

Template:
    risk/templates/risk/explain.html
"""

from django.shortcuts import get_object_or_404, render

from risk.models import AuditScoreSnapshot, UrgencyScore
from risk.services.audit_rationale import build_historical_context, build_ranked_factors


def explain_score(request, urgency_score_id: int):
    """
    Explain why an entity has its current urgency level.

    Reads the AuditScoreSnapshot for the given UrgencyScore and presents:
    - Ranked contributing factors (M1, driver, regime, market)
    - Historical context (days at current level)
    - Configuration at calculation time (weights, thresholds)

    If no snapshot exists (score predates M8 or audit write failed),
    degrades gracefully rather than 404.
    """
    score = get_object_or_404(UrgencyScore, id=urgency_score_id)

    # Find the most recent snapshot for this entity/date (in case of re-runs)
    snapshot = AuditScoreSnapshot.objects.filter(
        entity_type=score.entity_type,
        entity_id=score.entity_id,
        symbol=score.symbol,
        calc_date=score.calc_date,
    ).order_by('-written_at').first()

    if snapshot is not None:
        ranked_factors = build_ranked_factors(snapshot)
        history = build_historical_context(score)
    else:
        ranked_factors = []
        history = {
            'days_at_current_level': 0,
            'days_examined': 0,
            'summary': f"No historical context available for this score.",
        }

    weights_at_calc = snapshot.weights_at_calc_json if snapshot else {}

    context = {
        'score': score,
        'snapshot': snapshot,
        'ranked_factors': ranked_factors,
        'history': history,
        'weights_at_calc': weights_at_calc,
    }
    return render(request, 'risk/explain.html', context)
