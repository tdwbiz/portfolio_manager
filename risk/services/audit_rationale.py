"""
Audit rationale generation for M8-Spec-1.

Provides plain-English (template-driven, NOT AI-generated) explanations for:
- Why a strategy was selected or ranked (build_selection_rationale)
- Ranked contributing factors for an urgency score (build_ranked_factors)
- Historical context for an urgency score (build_historical_context)

All text uses deterministic templates — no LLM calls.
"""

from datetime import date, timedelta

from decimal import Decimal

from django.db.models import Q

from risk.models import AuditScoreSnapshot, UrgencyScore


# Human-readable mapping for ineligibility reasons
_INELIGIBILITY_LABELS = {
    'options_not_available': 'options are not available on this security',
    'not_in_universe': 'this security is not in the investment universe',
    'insufficient_history': 'insufficient price history for analysis',
    'no_instrument_links': 'no instrument links defined for this strategy',
    'inactive': 'this strategy template is inactive',
    'tax_shelter_ineligible': 'this strategy is not eligible for the account type',
    'risk_limit_exceeded': 'applying this strategy would exceed risk limits',
    'concentration_limit': 'applying this strategy would exceed concentration limits',
    'other': 'this strategy was not eligible for other reasons',
}


def build_selection_rationale(
    rank: int | None,
    was_eligible: bool,
    ineligibility_reason: str,
    cost_per_var_reduction: float | None,
    rank1_cost_per_var_reduction: float | None,
    tax_cost_estimate_usd: Decimal | None,
    is_tax_triggering: bool | None,
    tax_penalty_multiplier: float = 1.0,
) -> str:
    """
    Build plain-English rationale for why a strategy was selected or rejected.

    Parameters:
        rank: 1 for selected, >1 for other ranked candidates, None for rejected.
        was_eligible: True if the template passed all eligibility filters.
        ineligibility_reason: Code explaining why the template was rejected.
        cost_per_var_reduction: Cost per dollar of VaR reduction for this template.
        rank1_cost_per_var_reduction: Cost per VaR reduction for the rank-1 template.
        tax_cost_estimate_usd: Estimated tax cost if the strategy is tax-triggering.
        is_tax_triggering: Whether this strategy triggers a taxable event.
        tax_penalty_multiplier: Penalty multiplier applied to tax costs.

    Returns:
        A plain-English rationale string.
    """
    if not was_eligible:
        reason_label = _INELIGIBILITY_LABELS.get(ineligibility_reason, ineligibility_reason)
        return f"Not considered: {reason_label}."

    if rank == 1:
        parts = [f"Selected: lowest cost per dollar of VaR reduction among eligible candidates ({cost_per_var_reduction:.4f})."]
        if is_tax_triggering and tax_cost_estimate_usd and tax_cost_estimate_usd > 0:
            parts.append(
                f" Tax cost (${tax_cost_estimate_usd:,.0f}) included at penalty multiplier {tax_penalty_multiplier}."
            )
        else:
            parts.append(" Not tax-triggering.")
        return "".join(parts)

    # Rank > 1
    parts = [f"Ranked {rank}: cost_per_var_reduction ({cost_per_var_reduction:.4f}) higher than rank-1 ({rank1_cost_per_var_reduction:.4f})."]
    if (tax_cost_estimate_usd and tax_cost_estimate_usd > 0
            and rank1_cost_per_var_reduction is not None
            and (rank1_cost_per_var_reduction == 0 or rank1_cost_per_var_reduction < cost_per_var_reduction * 0.5)):
        parts.append(f" Primary driver: tax cost (${tax_cost_estimate_usd:,.0f}).")
    return "".join(parts)


def build_ranked_factors(snapshot: AuditScoreSnapshot) -> list[dict]:
    """
    Parse component_scores_json and weights_at_calc_json from an AuditScoreSnapshot.
    For each of the four components (m1, driver, regime, market), compute:
        contribution = component_score * component_weight

    Sort descending by contribution. Return a list of dicts with:
        component, label, score, weight, contribution, contribution_pct_of_total, explanation

    Explanation text is template-driven plain English (NOT AI-generated).
    """
    component_scores = snapshot.component_scores_json or {}
    weights = snapshot.weights_at_calc_json or {}
    input_metrics = snapshot.input_metrics_json or {}

    score_raw = float(snapshot.score_raw)

    labels = {
        'm1': 'Risk Metric Severity',
        'driver': 'Security Risk Profile',
        'regime': 'Macro Regime Adjustment',
        'market': 'Market Technicals',
    }

    components = []
    for comp_key in ('m1', 'driver', 'regime', 'market'):
        comp_data = component_scores.get(comp_key, {})
        comp_score = float(comp_data.get('score', 0))
        comp_weight = float(weights.get(f'weight_{comp_key}' if comp_key != 'm1' else 'weight_m1_metrics', 0))
        contribution = comp_score * comp_weight

        # Build plain-English explanation
        explanation = _build_component_explanation(comp_key, comp_score, comp_weight, input_metrics, snapshot)

        components.append({
            'component': comp_key,
            'label': labels[comp_key],
            'score': round(comp_score, 1),
            'weight': round(comp_weight, 2),
            'contribution': round(contribution, 1),
            'contribution_pct_of_total': round(contribution / score_raw * 100, 0) if score_raw > 0 else 0,
            'explanation': explanation,
        })

    # Sort descending by contribution
    components.sort(key=lambda x: x['contribution'], reverse=True)
    return components


def _build_component_explanation(
    component: str,
    score: float,
    weight: float,
    input_metrics: dict,
    snapshot: AuditScoreSnapshot,
) -> str:
    """Build plain-English explanation for a single component."""
    symbol = snapshot.symbol
    entity_type = snapshot.entity_type

    # For non-security entities, driver/regime/market are not applicable
    if entity_type != 'security_in_account':
        if component in ('driver', 'regime', 'market'):
            return "Not applicable at this entity level — this score is a weighted roll-up of constituent securities."

    if component == 'm1':
        var_val = input_metrics.get('var_parametric_95', 0)
        dd_val = input_metrics.get('current_drawdown_from_252d_peak', 0)
        vol_val = input_metrics.get('vol_252d', 0)
        return (
            f"{symbol}'s 1-day VaR ({var_val:.2%}), drawdown from 52-week peak "
            f"({dd_val:.1%}), and annualised volatility ({vol_val:.1%}) rank it "
            f"{score:.0f}/100 for risk severity relative to the rest of the portfolio."
        )

    if component == 'driver':
        inputs = input_metrics.get('inputs', input_metrics)
        ac_baseline = inputs.get('asset_class_baseline', 0)
        sector_cyc = inputs.get('sector_cyclicality', 0)
        liq_tier = inputs.get('liquidity_tier', 0)
        earnings_flag = inputs.get('earnings_proximity_flag', False)

        parts = [
            f"As a {inputs.get('asset_class', 'security')}, this security carries a baseline risk profile of "
            f"{ac_baseline:.0f}/100, with sector cyclicality {sector_cyc:.0f}/100 "
            f"and liquidity tier {liq_tier:.0f}/100."
        ]
        if earnings_flag:
            parts.append(" An upcoming earnings announcement adds to this score.")
        return "".join(parts)

    if component == 'regime':
        inputs = input_metrics.get('inputs', input_metrics)
        quadrant = inputs.get('quadrant', 'unknown')
        multiplier = inputs.get('multiplier', 1.0)
        driver_score = inputs.get('driver_score', score)

        action = 'amplifier' if multiplier > 1 else 'dampener'
        direction = 'increasing' if multiplier > 1 else 'reducing'
        regime_score = driver_score * multiplier

        parts = [
            f"The current macro regime ({quadrant}) applies a "
            f"{multiplier:.2f}x {action} to this security's baseline risk profile "
            f"({direction} the contribution from {driver_score:.0f} to {regime_score:.0f})."
        ]
        # Infer regime transition dampener
        raw_multiplier = inputs.get('multiplier_raw', multiplier)
        if raw_multiplier > 1 and multiplier < raw_multiplier:
            parts.append(" A regime transition was in progress, which moderated this adjustment.")
        return "".join(parts)

    if component == 'market':
        inputs = input_metrics.get('inputs', input_metrics)
        rsi = inputs.get('rsi_14', 50)
        crossover = inputs.get('crossover_type', 'none')
        phase = inputs.get('market_phase', 'neutral')
        pct_b = inputs.get('bb_pct_b', 0.5)

        if rsi < 30:
            rsi_status = 'oversold'
        elif rsi > 70:
            rsi_status = 'overbought'
        else:
            rsi_status = 'neutral'

        parts = [f"Current technicals: RSI {rsi:.0f} ({rsi_status}), {phase} phase"]
        if crossover == 'death':
            parts.append(", with a recent Death Cross")
        elif crossover == 'golden':
            parts.append(", with a recent Golden Cross")
        parts.append(".")
        return "".join(parts)

    return ""


def build_historical_context(score: UrgencyScore, lookback_days: int = 30) -> dict:
    """
    Query UrgencyScore for the same (entity_type, entity_id, symbol) over
    the most recent `lookback_days` distinct calc_dates (including today).

    Returns:
        {
            'days_at_current_level': int,
            'days_examined': int,
            'summary': str,
        }
    """
    scores = UrgencyScore.objects.filter(
        entity_type=score.entity_type,
        entity_id=score.entity_id,
        symbol=score.symbol,
        calc_date__lte=score.calc_date,
    ).order_by('-calc_date')[:lookback_days]

    days_examined = scores.count()
    days_at_current_level = sum(
        1 for s in scores if s.urgency_level == score.urgency_level
    )

    entity_label_map = {
        'security_in_account': f"{score.symbol} position in {score.account_id}",
        'account': f"{score.entity_id} account",
        'owner': f"{score.entity_id}'s portfolio",
        'portfolio': "the portfolio",
    }
    entity_label = entity_label_map.get(score.entity_type, score.entity_id)

    return {
        'days_at_current_level': days_at_current_level,
        'days_examined': days_examined,
        'summary': (
            f"This {entity_label} has been {score.urgency_level} for "
            f"{days_at_current_level} of the last {days_examined} calculated days."
        ),
    }
