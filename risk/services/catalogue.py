from typing import List

from django.db import models
from risk.models import StrategyTemplate, StrategyInstrumentLink, InstrumentType


def get_eligible_templates(
    asset_class: str,
    account_category: str,
    urgency_level: str,
    regime_quadrant: str,
    options_available: bool,
    is_cef: bool,
    is_lif_or_rrif: bool,
    unrealised_gain_usd: float,
) -> List[StrategyTemplate]:
    """
    Return all active strategy templates eligible for this position.

    Filtering rules (all must pass):
    1. applicable_asset_types contains asset_class (or is a superset)
    2. applicable_account_categories contains account_category (or == 'ALL')
    3. applicable_urgency_levels contains urgency_level
    4. applicable_regime_quadrants contains regime_quadrant (or == 'ALL')
    5. If requires_options=True: options_available must be True
    6. If is_cef=True: cef_eligible must be True
    7. If is_lif_or_rrif=True: lif_rrif_eligible must be True
    8. unrealised_gain_usd >= template.min_unrealised_gain_usd
    9. is_active=True

    Returns ordered by sort_order.
    """
    templates = StrategyTemplate.objects.filter(is_active=True)

    # Filter 1: applicable_asset_types contains asset_class
    templates = templates.filter(
        applicable_asset_types__contains=asset_class
    )

    # Filter 2: applicable_account_categories contains account_category or is 'ALL'
    templates = templates.filter(
        models.Q(applicable_account_categories='ALL') |
        models.Q(applicable_account_categories__contains=account_category)
    )

    # Filter 3: applicable_urgency_levels contains urgency_level
    templates = templates.filter(
        models.Q(applicable_urgency_levels='ALL') |
        models.Q(applicable_urgency_levels__contains=urgency_level)
    )

    # Filter 4: applicable_regime_quadrants contains regime_quadrant or is 'ALL'
    templates = templates.filter(
        models.Q(applicable_regime_quadrants='ALL') |
        models.Q(applicable_regime_quadrants__contains=regime_quadrant)
    )

    # Filter 5: If requires_options=True, options_available must be True
    if not options_available:
        templates = templates.filter(requires_options=False)

    # Filter 6: If is_cef=True, cef_eligible must be True
    if is_cef:
        templates = templates.filter(cef_eligible=True)

    # Filter 7: If is_lif_or_rrif=True, lif_rrif_eligible must be True
    if is_lif_or_rrif:
        templates = templates.filter(lif_rrif_eligible=True)

    # Filter 8: unrealised_gain_usd >= template.min_unrealised_gain_usd
    templates = templates.filter(
        min_unrealised_gain_usd__lte=unrealised_gain_usd
    )

    return list(templates.order_by('sort_order', 'name'))


def get_instruments_for_template(template: StrategyTemplate) -> List[dict]:
    """
    Return the instrument types for a strategy template, including role.
    Returns list of {instrument: InstrumentType, role: str} dicts.
    """
    links = StrategyInstrumentLink.objects.filter(strategy=template).order_by('sort_order')
    return [
        {
            'instrument': link.instrument,
            'role': link.role,
        }
        for link in links
    ]
