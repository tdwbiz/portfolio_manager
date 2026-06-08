"""Portfolio Comparison view."""
from datetime import date

from django.shortcuts import render

from risk.models import (
    AlternativePortfolio, PortfolioRiskMetrics, SecurityRiskMetrics,
    UrgencyScore,
)
from risk.views.helpers import get_latest_calc_date


def compute_alternative_risk_preview(portfolio, calc_date):
    """
    Compute estimated risk metrics for an alternative portfolio on-demand.
    Uses SecurityRiskMetrics for individual metrics and builds a simple
    weighted portfolio metric.

    Returns dict with:
        weighted_var_95_pct: float
        weighted_beta_spy: float
        hhi_symbol: float
        position_count: int
        top1_symbol: str
        top1_weight: float
    """
    holdings = portfolio.holdings.all()
    if not holdings:
        return None

    # Fetch metrics for each holding
    symbols = [h.symbol for h in holdings]
    metrics_map = {}
    for m in SecurityRiskMetrics.objects.filter(symbol__in=symbols, calc_date=calc_date):
        metrics_map[m.symbol] = m

    weighted_var = 0.0
    weighted_beta = 0.0
    hhi = 0.0
    top_weight = 0.0
    top_symbol = ''

    for holding in holdings:
        w = float(holding.target_weight)
        h = metrics_map.get(holding.symbol)
        var = float(h.var_95_pct) if h and h.var_95_pct else 0.0
        beta = float(h.beta_spy) if h and h.beta_spy else 0.0
        weighted_var += w * var
        weighted_beta += w * beta
        hhi += w * w
        if w > top_weight:
            top_weight = w
            top_symbol = holding.symbol

    return {
        'weighted_var_95_pct': round(weighted_var * 100, 2),
        'weighted_beta_spy': round(weighted_beta, 2),
        'hhi_symbol': round(hhi, 4),
        'position_count': holdings.count(),
        'top1_symbol': top_symbol,
        'top1_weight': round(top_weight * 100, 1),
    }


def portfolio_compare(request):
    """
    Compare current portfolio vs historical snapshot vs alternative portfolio.
    Query params: as_of_date (YYYY-MM-DD), alt_portfolio_id (int)
    """
    calc_date = get_latest_calc_date()
    as_of_date_str = request.GET.get('as_of_date', '')
    alt_portfolio_id = request.GET.get('alt_portfolio_id')

    # Current portfolio metrics
    current_metrics = PortfolioRiskMetrics.objects.filter(
        entity_type='portfolio', entity_id='ALL', calc_date=calc_date
    ).first()
    current_score = UrgencyScore.objects.filter(
        entity_type='portfolio', entity_id='ALL', calc_date=calc_date
    ).first()

    # Historical snapshot (as-of-date)
    historical_metrics = None
    historical_score = None
    as_of_date = None
    if as_of_date_str:
        try:
            as_of_date = date.fromisoformat(as_of_date_str)
            historical_metrics = PortfolioRiskMetrics.objects.filter(
                entity_type='portfolio', entity_id='ALL', calc_date=as_of_date
            ).first()
            historical_score = UrgencyScore.objects.filter(
                entity_type='portfolio', entity_id='ALL', calc_date=as_of_date
            ).first()
        except ValueError:
            pass

    # Alternative portfolio
    alt_portfolio = None
    alt_risk_preview = None
    if alt_portfolio_id:
        alt_portfolio = AlternativePortfolio.objects.filter(
            id=alt_portfolio_id, is_active=True
        ).prefetch_related('holdings').first()
        if alt_portfolio:
            alt_risk_preview = compute_alternative_risk_preview(alt_portfolio, calc_date)

    # All alternative portfolios for selector
    all_alternatives = AlternativePortfolio.objects.filter(
        is_active=True
    ).select_related('account')

    context = {
        'calc_date': calc_date,
        'current_metrics': current_metrics,
        'current_score': current_score,
        'as_of_date': as_of_date,
        'historical_metrics': historical_metrics,
        'historical_score': historical_score,
        'alt_portfolio': alt_portfolio,
        'alt_risk_preview': alt_risk_preview,
        'all_alternatives': all_alternatives,
    }
    return render(request, 'risk/portfolio_compare.html', context)
