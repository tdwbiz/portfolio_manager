"""Strategy Recommendation Report views."""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from risk.models import StrategyRecommendation, TaxDecisionAcknowledgement
from risk.views.helpers import get_latest_calc_date


def strategy_report(request, rec_id: int):
    """
    Full detail report for one strategy recommendation.
    Shows all candidates considered, not just the selected one.
    Links to backtest if available.
    """
    recommendation = get_object_or_404(
        StrategyRecommendation.objects.select_related(
            'strategy_template'
        ).prefetch_related(
            'strategy_template__instrument_links__instrument'
        ),
        id=rec_id
    )

    # All candidates for the same symbol+account+date (all ranks, not just this one)
    all_candidates = StrategyRecommendation.objects.filter(
        symbol=recommendation.symbol,
        account_id=recommendation.account_id,
        calc_date=recommendation.calc_date,
    ).select_related('strategy_template').order_by('rank')

    # Most recent backtest for this symbol + strategy template combination
    related_backtest = None
    try:
        from risk.models import BacktestResult
        related_backtest = BacktestResult.objects.filter(
            run__symbol=recommendation.symbol,
            run__strategy_template=recommendation.strategy_template,
        ).select_related('run__scenario').order_by('-run__run_timestamp').first()
    except Exception:
        pass

    # Has this recommendation been tax-acknowledged?
    tax_acknowledgement = None
    if recommendation.is_tax_triggering:
        tax_acknowledgement = TaxDecisionAcknowledgement.objects.filter(
            recommendation_id=rec_id
        ).first()

    # Can the user mark this as actioned?
    can_action = (
        recommendation.status == 'active' and
        (not recommendation.is_tax_triggering or tax_acknowledgement is not None)
    )

    context = {
        'recommendation': recommendation,
        'all_candidates': all_candidates,
        'related_backtest': related_backtest,
        'tax_acknowledgement': tax_acknowledgement,
        'can_action': can_action,
    }
    return render(request, 'risk/strategy_report.html', context)


def strategy_acknowledge(request, rec_id: int):
    """
    Tax acknowledgement workflow for tax-triggering recommendations.
    GET: show acknowledgement form with tax cost estimate.
    POST: record acknowledgement to rm_tax_decision_acknowledgement.
    """
    recommendation = get_object_or_404(
        StrategyRecommendation, id=rec_id, is_tax_triggering=True
    )

    if request.method == 'POST':
        user_notes = request.POST.get('user_notes', '')
        TaxDecisionAcknowledgement.objects.create(
            recommendation_id=rec_id,
            presented_to_user=recommendation.owner,
            presented_at=recommendation.calc_timestamp,
            acknowledged_at=timezone.now(),
            acknowledged_by=request.user.username if request.user.is_authenticated else 'anonymous',
            tax_cost_estimate_presented=recommendation.tax_cost_estimate_usd,
            user_notes=user_notes,
        )
        messages.success(
            request,
            "Tax impact acknowledged. You may now mark this recommendation as actioned."
        )
        return redirect('risk:strategy_report', rec_id=rec_id)

    context = {
        'recommendation': recommendation,
    }
    return render(request, 'risk/strategy_acknowledge.html', context)


def strategy_mark_actioned(request, rec_id: int):
    """
    Mark a strategy recommendation as actioned.
    POST only — records actioned_at and actioned_by.
    """
    recommendation = get_object_or_404(StrategyRecommendation, id=rec_id)

    if request.method == 'POST':
        recommendation.status = 'actioned'
        recommendation.actioned_at = timezone.now()
        recommendation.actioned_by = (
            request.user.username if request.user.is_authenticated else 'anonymous'
        )
        recommendation.save()
        messages.success(request, "Recommendation marked as actioned.")
        return redirect('risk:strategy_report', rec_id=rec_id)

    return redirect('risk:strategy_report', rec_id=rec_id)
