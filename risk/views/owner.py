"""Owner Summary view."""
from django.shortcuts import render
from django.db.models import Q
from django.http import Http404
from core.models import Account
from risk.models import UrgencyScore, PortfolioRiskMetrics
from risk.views.helpers import get_latest_calc_date


def owner_summary(request, owner: str):
    """
    Aggregated view for one owner across all their accounts.
    owner: 'TOM' or 'SUSAN' (case-sensitive, matches core_account.owner)
    """
    if owner not in ('TOM', 'SUSAN'):
        raise Http404("Owner must be TOM or SUSAN")

    calc_date = get_latest_calc_date()

    owner_score = UrgencyScore.objects.filter(
        entity_type='owner', entity_id=owner, calc_date=calc_date
    ).first() if calc_date else None

    owner_metrics = PortfolioRiskMetrics.objects.filter(
        entity_type='owner', entity_id=owner, calc_date=calc_date
    ).first() if calc_date else None

    # This owner's account scores, ordered by urgency
    account_scores = UrgencyScore.objects.filter(
        entity_type='account',
        calc_date=calc_date
    ).filter(
        Q(entity_id__in=Account.objects.filter(owner=owner).values('name')) |
        Q(entity_id__in=Account.objects.filter(owner='JOINT').values('name'))
    ).order_by('-score_raw') if calc_date else []

    # Other owner for comparison panel
    other_owner = 'SUSAN' if owner == 'TOM' else 'TOM'
    other_owner_score = UrgencyScore.objects.filter(
        entity_type='owner', entity_id=other_owner, calc_date=calc_date
    ).first() if calc_date else None
    other_owner_metrics = PortfolioRiskMetrics.objects.filter(
        entity_type='owner', entity_id=other_owner, calc_date=calc_date
    ).first() if calc_date else None

    context = {
        'calc_date':           calc_date,
        'owner':               owner,
        'other_owner':         other_owner,
        'owner_score':         owner_score,
        'owner_metrics':       owner_metrics,
        'account_scores':      account_scores,
        'other_owner_score':   other_owner_score,
        'other_owner_metrics': other_owner_metrics,
    }
    return render(request, 'risk/owner_summary.html', context)
