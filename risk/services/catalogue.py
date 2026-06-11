from typing import List, Tuple

from django.db import models
from risk.models import StrategyTemplate, StrategyInstrumentLink, InstrumentType


def get_all_templates_with_reasons(
    asset_class: str,
    account_category: str,
    urgency_level: str,
    regime_quadrant: str,
    options_available: bool,
    is_cef: bool,
    is_lif_or_rrif: bool,
    unrealised_gain_usd: float,
) -> Tuple[List[StrategyTemplate], List[Tuple[StrategyTemplate, str]]]:
    """
    Return eligible templates AND rejected templates with ineligibility reasons.

    Returns:
        (eligible_templates, rejected_with_reasons)
        where rejected_with_reasons is a list of (template, reason) tuples.
    """
    templates = StrategyTemplate.objects.filter(is_active=True)

    # Track which templates pass each filter
    eligible = []
    rejected = []

    for template in templates.order_by('sort_order', 'name'):
        reason = None

        # Filter 1: applicable_asset_types contains asset_class
        if asset_class not in template.applicable_asset_types:
            reason = f"asset_class '{asset_class}' not in applicable_asset_types"
        # Filter 2: applicable_account_categories
        elif (template.applicable_account_categories != 'ALL' and
              account_category not in template.applicable_account_categories):
            reason = f"account_category '{account_category}' not in applicable_account_categories"
        # Filter 3: applicable_urgency_levels
        elif (template.applicable_urgency_levels != 'ALL' and
              urgency_level not in template.applicable_urgency_levels):
            reason = f"urgency_level '{urgency_level}' not in applicable_urgency_levels"
        # Filter 4: applicable_regime_quadrants
        elif (template.applicable_regime_quadrants != 'ALL' and
              regime_quadrant not in template.applicable_regime_quadrants):
            reason = f"regime_quadrant '{regime_quadrant}' not in applicable_regime_quadrants"
        # Filter 5: requires_options
        elif template.requires_options and not options_available:
            reason = "requires_options=True but options not available"
        # Filter 6: cef_eligible
        elif is_cef and not template.cef_eligible:
            reason = "CEF position but template not cef_eligible"
        # Filter 7: lif_rrif_eligible
        elif is_lif_or_rrif and not template.lif_rrif_eligible:
            reason = "LIF/RRIF account but template not lif_rrif_eligible"
        # Filter 8: min_unrealised_gain_usd
        elif unrealised_gain_usd < template.min_unrealised_gain_usd:
            reason = (f"unrealised_gain {unrealised_gain_usd:.2f} < "
                      f"template min {template.min_unrealised_gain_usd:.2f}")

        if reason:
            rejected.append((template, reason))
        else:
            eligible.append(template)

    return eligible, rejected


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
