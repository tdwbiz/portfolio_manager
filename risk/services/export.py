"""Export services for PDF and CSV generation."""
import csv
import io
import json

from datetime import date
from decimal import Decimal
from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone

from risk.models import SecurityRiskMetrics, StrategyRecommendation
from risk.views.helpers import get_latest_calc_date

try:
    from weasyprint import HTML
    # Test that WeasyPrint can actually render (may fail on Windows without GTK+)
    HTML(string='<html><body>test</body></html>').write_pdf()
    WEASYPRINT_AVAILABLE = True
except Exception:
    WEASYPRINT_AVAILABLE = False


def build_security_detail_context(symbol: str, calc_date: date) -> dict:
    """Build the context dict used by security_detail view."""
    from risk.models import (
        SecurityUniverse, SecurityRiskMetrics, MarketCondition,
        RegimeSensitivity, UrgencyScore, StrategyRecommendation,
        BacktestResult,
    )

    security = SecurityUniverse.objects.filter(symbol=symbol).first()
    security_metrics = SecurityRiskMetrics.objects.filter(
        symbol=symbol, calc_date=calc_date
    ).first()
    market_condition = MarketCondition.objects.filter(
        symbol=symbol, calc_date=calc_date
    ).first()
    regime_sensitivities = RegimeSensitivity.objects.filter(
        symbol=symbol, calc_date=calc_date
    ).order_by('regime_quadrant')

    # Current regime (latest RegimeState)
    from risk.models import RegimeState
    current_regime = RegimeState.objects.order_by('-effective_date').first()

    # Urgency score for this symbol
    urgency = UrgencyScore.objects.filter(
        symbol=symbol, calc_date=calc_date
    ).first()

    # Top recommendations for this symbol
    top_recommendations = StrategyRecommendation.objects.filter(
        symbol=symbol, calc_date=calc_date
    ).select_related(
        'strategy_template', 'strategy_template__instrument_links__instrument'
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

    return {
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


def export_security_card_pdf(symbol: str, calc_date: date) -> HttpResponse:
    """Generate a PDF version of the security risk card using WeasyPrint."""
    if not WEASYPRINT_AVAILABLE:
        from django.http import Http404
        raise Http404("PDF export requires weasyprint")

    context = build_security_detail_context(symbol, calc_date)
    html_string = render_to_string('risk/security_detail_print.html', context)
    html = HTML(string=html_string)
    pdf_bytes = html.write_pdf()
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="risk_{symbol}_{calc_date}.pdf"'
    return response


def export_security_card_csv(symbol: str, calc_date: date) -> HttpResponse:
    """Export security risk metrics as CSV."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="risk_{symbol}_{calc_date}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Metric', 'Value', 'Notes'])

    try:
        security_metrics = SecurityRiskMetrics.objects.get(symbol=symbol, calc_date=calc_date)
        writer.writerow(['Symbol', symbol, ''])
        writer.writerow(['Calc Date', str(calc_date), ''])
        writer.writerow(['Vol 21d (%)', float(security_metrics.vol_21d or 0) * 100, 'Annualised'])
        writer.writerow(['VaR Parametric 95% (%)', float(security_metrics.var_parametric_95 or 0) * 100, '1-day'])
        writer.writerow(['VaR Cornish-Fisher 95% (%)', float(security_metrics.var_cornish_fisher_95 or 0) * 100, '1-day'])
        writer.writerow(['CVaR 95% (%)', float(security_metrics.cvar_95 or 0) * 100, '1-day'])
        writer.writerow(['Beta vs SPY', float(security_metrics.beta_spy or 0), '252d'])
        writer.writerow(['Sharpe Ratio', float(security_metrics.sharpe_ratio or 0), ''])
        writer.writerow(['Liquidity Score', float(security_metrics.liquidity_score or 0), '0-100'])
        writer.writerow(['UGL at Risk USD', float(security_metrics.ugl_at_risk_95_usd or 0), '1-day 95%'])
    except SecurityRiskMetrics.DoesNotExist:
        writer.writerow(['Error', f'No metrics found for {symbol} on {calc_date}', ''])

    return response


def export_strategy_report_pdf(rec_id: int) -> HttpResponse:
    """Generate PDF for a strategy recommendation report."""
    if not WEASYPRINT_AVAILABLE:
        from django.http import Http404
        raise Http404("PDF export requires weasyprint")

    recommendation = StrategyRecommendation.objects.get(id=rec_id)
    context = {'recommendation': recommendation}
    html_string = render_to_string('risk/strategy_report_print.html', context)
    html = HTML(string=html_string)
    pdf_bytes = html.write_pdf()
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="strategy_{recommendation.symbol}_{recommendation.account_id}_{rec_id}.pdf"'
    )
    return response
