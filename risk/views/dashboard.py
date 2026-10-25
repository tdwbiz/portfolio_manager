"""Portfolio Risk Dashboard view."""
from django.shortcuts import render
from django.db.models import Q
from core.models import Account
from risk.models import (
    TaxDecisionAcknowledgement,
    UrgencyScore,
    PortfolioRiskMetrics,
    RegimeState,
    MarketCondition,
    StrategyRecommendation,
)
from risk.views.helpers import get_latest_calc_date, get_show_excluded, get_excluded_account_ids


def get_top_ugl_positions(calc_date, limit=5, include_excluded=False):
    """
    Return the top N positions by unrealised gain in USD, with tax-sensitivity flag.

    Query logic:
    1. Load all open positions from core_position_history (date_to IS NULL)
    2. Join with rm_market_price_history (latest close for calc_date)
    3. Join with rm_fx_rates (for CAD conversion)
    4. Join with core_account (for account_category and exclude_from_risk)
    5. Compute UGL per position: (current_price_usd - cost_basis_per_unit_usd) * quantity
    6. Filter: exclude_from_risk=True positions excluded unless include_excluded=True
    7. Sort descending by UGL, limit to N

    Returns list of dicts with keys:
        symbol, account_id, account_name, account_category, owner,
        quantity, current_price_usd, cost_basis_usd, market_value_usd,
        unrealised_gain_usd, unrealised_gain_cad, is_tax_sensitive
    """
    # This is a simplified implementation - the full version would use raw SQL
    # or DataSource to query vw_current_portfolio joined with price data
    # For now, return empty list - the view handles None gracefully
    return []


def dashboard(request):
    """
    Portfolio-level risk dashboard.
    Reads from: rm_urgency_score (entity_type='portfolio'),
                rm_portfolio_risk_metrics (entity_type='portfolio'),
                rm_regime_state,
                rm_market_condition,
                vw_current_portfolio (for top UGL positions),
                core_position_history (for UGL computation).
    """
    calc_date = get_latest_calc_date()
    show_excluded = get_show_excluded(request)

    if not calc_date:
        # Risk features not yet calculated — show "Coming Soon"
        context = {
            'calc_date': None,
            'show_excluded': show_excluded,
            'portfolio_score': None,
            'portfolio_metrics': None,
            'regime': None,
            'death_cross_count': 0,
            'oversold_count': 0,
            'overbought_count': 0,
            'top_ugl_positions': [],
            'account_scores': [],
            'index_concentration': {},
            'coming_soon': True,
        }
        return render(request, 'risk/dashboard.html', context)

    # Portfolio urgency score
    portfolio_score = UrgencyScore.objects.filter(
        entity_type='portfolio',
        entity_id='ALL',
        calc_date=calc_date
    ).first()

    # Portfolio risk metrics
    portfolio_metrics = PortfolioRiskMetrics.objects.filter(
        entity_type='portfolio',
        entity_id='ALL',
        calc_date=calc_date
    ).first()

    # Current regime
    regime = RegimeState.objects.filter(calc_date__lte=calc_date).order_by('-calc_date').first()

    # Market condition summary
    market_conditions = MarketCondition.objects.filter(calc_date=calc_date)
    death_cross_count = market_conditions.filter(crossover_type='death').count()
    oversold_count = market_conditions.filter(rsi_14__lt=30).count()
    overbought_count = market_conditions.filter(rsi_14__gt=70).count()

    # Top 5 unrealised gains with tax-sensitive flag
    top_ugl_positions = get_top_ugl_positions(
        calc_date=calc_date,
        limit=5,
        include_excluded=show_excluded
    )

    # Account urgency scores for nav/summary strip
    account_scores = UrgencyScore.objects.filter(
        entity_type='account',
        calc_date=calc_date
    ).order_by('-score_raw')
    
    # Annotate account information for display
    from core.models import Account
    account_ids = [score.entity_id for score in account_scores]
    accounts_dict = {acc.name: acc for acc in Account.objects.filter(name__in=account_ids)}
    for score in account_scores:
        score.account = accounts_dict.get(score.entity_id)

    if not show_excluded:
        excluded_ids = get_excluded_account_ids()
        account_scores = account_scores.exclude(entity_id__in=excluded_ids)

    # Effective index concentration (from portfolio_metrics)
    index_concentration = {
        'SP500':         float(portfolio_metrics.effective_sp500_weight or 0) * 100 if portfolio_metrics else 0,
        'NASDAQ100':     float(portfolio_metrics.effective_nasdaq100_weight or 0) * 100 if portfolio_metrics else 0,
        'TSX':           float(portfolio_metrics.effective_tsx_weight or 0) * 100 if portfolio_metrics else 0,
        'Semiconductor': float(portfolio_metrics.effective_semiconductor_weight or 0) * 100 if portfolio_metrics else 0,
        'Technology':    float(portfolio_metrics.effective_technology_weight or 0) * 100 if portfolio_metrics else 0,
        'Dividend':      float(portfolio_metrics.effective_dividend_weight or 0) * 100 if portfolio_metrics else 0,
    }

    # Unacknowledged tax-triggering recommendations (M8-Spec-1 §4.3)
    unacknowledged_tax_recs = get_unacknowledged_tax_recommendations(calc_date)

    context = {
        'calc_date':              calc_date,
        'show_excluded':          show_excluded,
        'portfolio_score':        portfolio_score,
        'portfolio_metrics':      portfolio_metrics,
        'regime':                 regime,
        'death_cross_count':      death_cross_count,
        'oversold_count':         oversold_count,
        'overbought_count':       overbought_count,
        'top_ugl_positions':      top_ugl_positions,
        'account_scores':         account_scores,
        'index_concentration':    index_concentration,
        'unacknowledged_tax_recs': unacknowledged_tax_recs,
    }
    return render(request, 'risk/dashboard.html', context)


def get_unacknowledged_tax_recommendations(calc_date) -> list:
    """
    Return active, is_tax_triggering=True recommendations at calc_date for
    which no rm_tax_decision_acknowledgement row exists, ordered by
    tax_cost_estimate_usd descending (largest tax exposure first).

    Implementation note: TaxDecisionAcknowledgement has no FK to
    StrategyRecommendation (see M8-Spec-1 §1.6 rationale), so this is a
    Python-side set difference: load the candidate recommendation ids,
    load the set of acknowledged recommendation_ids, subtract.
    """
    # Active, tax-triggering recommendations for this calc_date
    candidates = StrategyRecommendation.objects.filter(
        calc_date=calc_date,
        is_tax_triggering=True,
        status='active',
    ).select_related('strategy_template').order_by('-tax_cost_estimate_usd')

    # Already acknowledged recommendation ids
    acknowledged_ids = set(
        TaxDecisionAcknowledgement.objects.values_list(
            'recommendation_id', flat=True
        )
    )

    # Filter out acknowledged ones
    return [
        rec for rec in candidates
        if rec.id not in acknowledged_ids
    ]
