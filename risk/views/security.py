"""Security Detail view and export."""
import json
from datetime import date

from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from risk.models import (
    BacktestResult, MarketCondition, RegimeSensitivity, RegimeState,
    SecurityUniverse, SecurityRiskMetrics, StrategyRecommendation,
    UrgencyScore,
)
from risk.services.export import export_security_card_csv, export_security_card_pdf
from risk.views.helpers import get_latest_calc_date


def security_detail(request, symbol: str):
    """
    Security Risk Card view showing all M1 metrics, market conditions,
    regime sensitivity, strategy recommendations, and backtest summary.
    """
    calc_date = get_latest_calc_date()
    if calc_date is None:
        raise Http404("No risk data available")

    security = SecurityUniverse.objects.filter(symbol=symbol).first()
    if not security:
        raise Http404(f"Security {symbol} not found in universe")

    security_metrics = SecurityRiskMetrics.objects.filter(
        symbol=symbol, calc_date=calc_date
    ).first()
    market_condition = MarketCondition.objects.filter(
        symbol=symbol, calc_date=calc_date
    ).first()
    regime_sensitivities = RegimeSensitivity.objects.all().order_by('regime_quadrant')

    # Current regime (latest RegimeState)
    current_regime = RegimeState.objects.order_by('-calc_date').first()

    # Urgency score for this symbol
    urgency = UrgencyScore.objects.filter(
        symbol=symbol, calc_date=calc_date
    ).first()

    # Top recommendations for this symbol
    top_recommendations = StrategyRecommendation.objects.filter(
        symbol=symbol, calc_date=calc_date
    ).select_related('strategy_template').prefetch_related(
        'strategy_template__instrument_links__instrument'
    ).order_by('rank')[:10]

    # Urgency history (last 90 days)
    urgency_history = UrgencyScore.objects.filter(
        symbol=symbol
    ).order_by('calc_date')

    history_labels = [d.isoformat() for d in urgency_history.values_list('calc_date', flat=True)]
    history_values = [float(d.score_raw) for d in urgency_history if d.score_raw is not None]

    # Recent backtest
    recent_backtest = BacktestResult.objects.filter(
        run__symbol=symbol
    ).select_related(
        'run__scenario', 'run__strategy_template'
    ).order_by('-run__run_timestamp').first()

    context = {
        'security': security,
        'security_metrics': security_metrics,
        'market_condition': market_condition,
        'regime_sensitivities': regime_sensitivities,
        'current_regime': current_regime,
        'urgency': urgency,
        'top_recommendations': top_recommendations,
        'history_labels_json': json.dumps(history_labels),
        'history_values_json': json.dumps(history_values),
        'recent_backtest': recent_backtest,
        'calc_date': calc_date,
    }
    return render(request, 'risk/security_detail.html', context)


def security_export(request, symbol: str, fmt: str):
    """Export security risk card as PDF or CSV."""
    calc_date = get_latest_calc_date()
    if calc_date is None:
        raise Http404("No risk data available")

    if fmt == 'pdf':
        return export_security_card_pdf(symbol, calc_date)
    elif fmt == 'csv':
        return export_security_card_csv(symbol, calc_date)
    raise Http404
