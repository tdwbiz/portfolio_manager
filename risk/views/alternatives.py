"""Alternative Portfolio Maintenance views."""
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import Account
from risk.models import AlternativePortfolio, AlternativeHolding, SecurityUniverse
from risk.views.helpers import get_latest_calc_date


def alternatives_list(request):
    """List all alternative portfolios across all accounts."""
    portfolios = AlternativePortfolio.objects.filter(
        is_active=True
    ).select_related('account').prefetch_related('holdings').order_by('account', 'name')
    return render(request, 'risk/alternatives_list.html', {'portfolios': portfolios})


def alternatives_account(request, account_id: str):
    """
    List and create alternative portfolios for a specific account.
    Also provides risk preview for each alternative.
    """
    account = get_object_or_404(Account, name=account_id)  # URL carries the account name
    portfolios = AlternativePortfolio.objects.filter(
        account=account, is_active=True
    ).prefetch_related('holdings')

    context = {
        'account': account,
        'portfolios': portfolios,
    }
    return render(request, 'risk/alternatives_account.html', context)


def alternatives_edit(request, portfolio_id: int):
    """
    Edit an alternative portfolio's name and holdings.
    Holdings must sum to 100% — enforce server-side and client-side.
    """
    portfolio = get_object_or_404(AlternativePortfolio, id=portfolio_id)
    symbols_available = SecurityUniverse.objects.filter(is_active=True).order_by('symbol')

    if request.method == 'POST':
        # Parse holdings from POST: symbol_N, weight_N pairs
        holdings_data = []
        i = 0
        while f'symbol_{i}' in request.POST:
            sym = request.POST.get(f'symbol_{i}', '').strip()
            try:
                wt = Decimal(request.POST.get(f'weight_{i}', '0')) / 100  # convert % to fraction
            except (InvalidOperation, ValueError):
                wt = Decimal('0')
            if sym and wt > 0:
                holdings_data.append({'symbol': sym, 'target_weight': wt})
            i += 1

        # Validate total weight
        total = sum((h['target_weight'] for h in holdings_data), Decimal('0'))
        if abs(total - Decimal('1.0')) > Decimal('0.001'):
            messages.error(
                request,
                f"Holdings must sum to 100%. Current total: {float(total * 100):.2f}%"
            )
        else:
            # Update holdings atomically
            with transaction.atomic():
                portfolio.holdings.all().delete()
                for h in holdings_data:
                    AlternativeHolding.objects.create(
                        portfolio=portfolio,
                        symbol=h['symbol'],
                        target_weight=h['target_weight']
                    )
                portfolio.name = request.POST.get('name', portfolio.name)
                portfolio.description = request.POST.get('description', portfolio.description)
                portfolio.save()
            messages.success(request, "Alternative portfolio updated.")
            return redirect('risk:alternatives_account', account_id=portfolio.account.account_id)

    context = {
        'portfolio': portfolio,
        'symbols_available': symbols_available,
        'holdings': portfolio.holdings.all(),
    }
    return render(request, 'risk/alternatives_edit.html', context)


def alternatives_create(request):
    """Create a new alternative portfolio for a given account."""
    accounts = Account.objects.filter(exclude_from_risk=False).order_by('name')

    if request.method == 'POST':
        account = get_object_or_404(Account, name=request.POST.get('account_id'))
        portfolio = AlternativePortfolio.objects.create(
            owner=account.owner,
            account=account,
            name=request.POST.get('name', 'New Alternative'),
            description=request.POST.get('description', ''),
        )
        return redirect('risk:alternatives_edit', portfolio_id=portfolio.id)

    return render(request, 'risk/alternatives_create.html', {'accounts': accounts})
