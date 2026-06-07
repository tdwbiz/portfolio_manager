"""Account Risk Summary view."""
from django.shortcuts import render, get_object_or_404
from core.models import Account
from risk.models import (
    UrgencyScore,
    PortfolioRiskMetrics,
    SecurityRiskMetrics,
    RegimeState,
    MarketCondition,
)
from risk.views.helpers import get_latest_calc_date


def account_summary(request, account_id: str):
    """
    Per-account risk summary page.
    """
    calc_date = get_latest_calc_date()
    account = get_object_or_404(Account, account_id=account_id)

    if not calc_date:
        context = {
            'calc_date': None,
            'account_id': account.account_id,
            'account': account,
            'account_score': None,
            'account_metrics': None,
            'security_scores': [],
            'security_metrics': {},
            'current_regime': None,
            'is_taxable': account.category in ('Investment', 'Investment Joint'),
            'is_lif_rrif': account.category in ('LIF', 'RRIF'),
        }
        return render(request, 'risk/account_summary.html', context)

    account_score = get_object_or_404(
        UrgencyScore, entity_type='account', entity_id=account_id, calc_date=calc_date
    )
    account_metrics = PortfolioRiskMetrics.objects.filter(
        entity_type='account', entity_id=account_id, calc_date=calc_date
    ).first()

    # All security-level urgency scores for positions in this account
    security_scores = UrgencyScore.objects.filter(
        entity_type='security_in_account',
        account_id=account_id,
        calc_date=calc_date
    ).order_by('-score_raw')

    # Security risk metrics for each held symbol
    symbols = list(security_scores.values_list('symbol', flat=True))
    security_metrics = {
        m.symbol: m for m in SecurityRiskMetrics.objects.filter(
            symbol__in=symbols,
            calc_date=calc_date
        )
    }

    # Market conditions for each symbol
    market_conditions = {
        mc.symbol: mc for mc in MarketCondition.objects.filter(
            symbol__in=symbols,
            calc_date=calc_date
        )
    }

    # Regime sensitivity for each symbol
    current_regime = RegimeState.objects.filter(calc_date__lte=calc_date).order_by('-calc_date').first()

    # Is this a taxable account?
    is_taxable = account.category in ('Investment', 'Investment Joint')
    is_lif_rrif = account.category in ('LIF', 'RRIF')

    context = {
        'calc_date':         calc_date,
        'account_id':        account.account_id,
        'account':           account,
        'account_score':     account_score,
        'account_metrics':   account_metrics,
        'security_scores':   security_scores,
        'security_metrics':  security_metrics,
        'market_conditions': market_conditions,
        'current_regime':    current_regime,
        'is_taxable':        is_taxable,
        'is_lif_rrif':       is_lif_rrif,
    }
    return render(request, 'risk/account_summary.html', context)
