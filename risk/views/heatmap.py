"""Risk Heat Map view."""
from django.shortcuts import render
from core.models import Account
from risk.models import UrgencyScore, PortfolioRiskMetrics
from risk.views.helpers import get_latest_calc_date, get_show_excluded, get_excluded_account_ids


def risk_heatmap(request):
    """
    Heat map: rows = accounts, columns = symbols.
    Cell colour = urgency level. Cell opacity/size = position weight.
    """
    calc_date = get_latest_calc_date()
    show_excluded = get_show_excluded(request)

    if not calc_date:
        context = {
            'calc_date': None,
            'show_excluded': show_excluded,
            'grid': {},
            'sorted_accounts': [],
            'sorted_symbols': [],
            'portfolio_mv': 0,
        }
        return render(request, 'risk/heatmap.html', context)

    # All security-in-account urgency scores for today
    scores = UrgencyScore.objects.filter(
        entity_type='security_in_account',
        calc_date=calc_date
    ).select_related()

    if not show_excluded:
        excluded_ids = get_excluded_account_ids()
        scores = scores.exclude(account_id__in=excluded_ids)

    # Build grid: {account_id: {symbol: {urgency_level, score_raw, weight}}}
    # Weight = position_mv / total portfolio MV
    portfolio_mv = PortfolioRiskMetrics.objects.filter(
        entity_type='portfolio', entity_id='ALL', calc_date=calc_date
    ).values_list('total_market_value_usd', flat=True).first() or 1

    grid = {}
    all_symbols = set()
    for score in scores:
        if score.account_id not in grid:
            grid[score.account_id] = {}
        grid[score.account_id][score.symbol] = {
            'urgency_level': score.urgency_level,
            'score_raw': float(score.score_raw),
        }
        all_symbols.add(score.symbol)

    # Sort: accounts by total score descending, symbols alphabetically
    sorted_accounts = sorted(grid.keys(),
        key=lambda a: max(v['score_raw'] for v in grid[a].values()), reverse=True)
    sorted_symbols = sorted(all_symbols)

    context = {
        'calc_date':       calc_date,
        'show_excluded':   show_excluded,
        'grid':            grid,
        'sorted_accounts': sorted_accounts,
        'sorted_symbols':  sorted_symbols,
        'portfolio_mv':    portfolio_mv,
    }
    return render(request, 'risk/heatmap.html', context)
