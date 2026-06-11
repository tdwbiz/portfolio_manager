"""
Shared audit hook functions for M8-Spec-1.

These functions write audit rows after primary calculation writes.
All are wrapped in try/except in the calling commands — audit failures
are logged at ERROR but never raise out of the M2/M4/M5 commands.

Functions:
    write_audit_score_snapshot() — after calc_urgency UPSERT
    write_audit_recommendation_log() — after calc_strategy_recommendations ranking
    write_audit_regime_log() — after calc_market_conditions
    write_audit_simulation_run() — after run_backtest/run_montecarlo/run_stress_test
"""

import json
import logging
from datetime import date, datetime

from decimal import Decimal

from django.db import IntegrityError

from risk.models import (
    AuditRegimeLog,
    AuditRecommendationLog,
    AuditScoreSnapshot,
    AuditSimulationRun,
    MarketCondition,
    RegimeState,
)

logger = logging.getLogger(__name__)


def write_audit_score_snapshot(
    entity_type: str,
    entity_id: str,
    symbol: str,
    calc_date: date,
    urgency_level: str,
    score_raw: Decimal,
    component_scores_json: str,
    weights_config: dict,
    input_metrics_json: str,
) -> None:
    """
    Create one rm_audit_score_snapshot row after an UrgencyScore UPSERT.

    Wrapped in try/except in the calling command: on ImmutableRecordError
    or any DB error, log logger.error(...) with full context and continue.
    """
    try:
        AuditScoreSnapshot.objects.create(
            entity_type=entity_type,
            entity_id=entity_id,
            symbol=symbol,
            calc_date=calc_date,
            urgency_level=urgency_level,
            score_raw=score_raw,
            component_scores_json=json.loads(component_scores_json) if component_scores_json else {},
            weights_at_calc_json=weights_config,
            input_metrics_json=json.loads(input_metrics_json) if input_metrics_json else {},
        )
    except (IntegrityError, Exception) as e:
        logger.error(
            "Audit snapshot write failed: entity_type=%s entity_id=%s symbol=%s calc_date=%s error=%s",
            entity_type, entity_id, symbol, calc_date, e,
        )


def write_audit_recommendation_log(
    symbol: str,
    account_id: str,
    owner: str,
    calc_date: date,
    input_urgency_level: str,
    input_urgency_score: Decimal,
    input_regime_quadrant: str,
    strategy_template_code: str,
    strategy_template_name: str,
    was_eligible: bool,
    ineligibility_reason: str,
    rank: int | None,
    cost_result: dict | None,
    var_result: dict | None,
    cost_per_var_reduction: float | None,
    selection_rationale: str,
    constraints_applied: list[str],
    is_tax_triggering: bool = False,
    tax_cost_estimate_usd: Decimal | None = None,
) -> None:
    """
    Create one rm_audit_recommendation_log row for every template considered
    (eligible or rejected) for each (symbol, account, calc_date).

    Same try/except-and-log pattern — never raises.
    """
    try:
        AuditRecommendationLog.objects.create(
            symbol=symbol,
            account_id=account_id,
            owner=owner,
            calc_date=calc_date,
            input_urgency_level=input_urgency_level,
            input_urgency_score=input_urgency_score,
            input_regime_quadrant=input_regime_quadrant,
            strategy_template_code=strategy_template_code,
            strategy_template_name=strategy_template_name,
            was_eligible=was_eligible,
            ineligibility_reason=ineligibility_reason,
            rank=rank,
            cost_result_json=cost_result or {},
            var_result_json=var_result or {},
            cost_per_var_reduction_usd=cost_per_var_reduction,
            selection_rationale=selection_rationale,
            constraints_applied_json=constraints_applied,
            is_tax_triggering=is_tax_triggering,
            tax_cost_estimate_usd=tax_cost_estimate_usd,
        )
    except (IntegrityError, Exception) as e:
        logger.error(
            "Audit recommendation log write failed: symbol=%s account_id=%s "
            "template=%s calc_date=%s error=%s",
            symbol, account_id, strategy_template_code, calc_date, e,
        )


def write_audit_regime_log(calc_date: date) -> None:
    """
    Called at the end of calc_market_conditions.
    1. Read RegimeState for calc_date (must exist — calc_regime ran first).
    2. Aggregate MarketCondition for calc_date.
    3. UPSERT-as-insert into AuditRegimeLog (unique on calc_date).
       If a row already exists for calc_date, log WARNING and skip
       (re-run scenario, not a data integrity violation).
    """
    try:
        regime = RegimeState.objects.get(calc_date=calc_date)
    except RegimeState.DoesNotExist:
        logger.error("RegimeState not found for calc_date=%s — cannot write audit regime log", calc_date)
        return

    # Aggregate market conditions for calc_date
    conditions = MarketCondition.objects.filter(calc_date=calc_date)
    death_cross_count = conditions.filter(crossover_type='death', crossover_date=calc_date).count()
    golden_cross_count = conditions.filter(crossover_type='golden', crossover_date=calc_date).count()
    oversold_count = conditions.filter(rsi_14__lt=30).count()
    overbought_count = conditions.filter(rsi_14__gt=70).count()
    bear_phase_symbols = list(conditions.filter(market_phase='bear').values_list('symbol', flat=True))

    try:
        AuditRegimeLog.objects.update_or_create(
            calc_date=calc_date,
            defaults={
                'quadrant': regime.quadrant,
                'confidence_score': regime.confidence_score,
                'transition_flag': regime.transition_flag,
                'indicator_snapshot_json': regime.indicator_snapshot_json or {},
                'death_cross_count': death_cross_count,
                'golden_cross_count': golden_cross_count,
                'oversold_count': oversold_count,
                'overbought_count': overbought_count,
                'bear_phase_symbols_json': list(bear_phase_symbols),
            },
        )
    except IntegrityError:
        logger.warning("AuditRegimeLog already exists for calc_date=%s — skipping (re-run)", calc_date)
    except Exception as e:
        logger.error("Audit regime log write failed for calc_date=%s error=%s", calc_date, e)


def write_audit_simulation_run(
    simulation_family: str,
    source_run_id: int,
    symbol: str,
    account_id: str,
    entity_type: str,
    entity_id: str,
    strategy_template_code: str,
    scenario_name: str,
    parameters: dict,
    summary: dict,
    calc_date: date,
    run_timestamp: datetime,
) -> None:
    """
    Create one rm_audit_simulation_run row after a simulation command completes.
    Same try/except-and-log pattern — never raises.
    """
    try:
        AuditSimulationRun.objects.create(
            simulation_family=simulation_family,
            source_run_id=source_run_id,
            symbol=symbol,
            account_id=account_id,
            entity_type=entity_type,
            entity_id=entity_id,
            strategy_template_code=strategy_template_code,
            scenario_name=scenario_name,
            parameters_json=parameters,
            summary_json=summary,
            calc_date=calc_date,
            run_timestamp=run_timestamp,
        )
    except (IntegrityError, Exception) as e:
        logger.error(
            "Audit simulation run write failed: family=%s source_run_id=%s "
            "symbol=%s calc_date=%s error=%s",
            simulation_family, source_run_id, symbol, calc_date, e,
        )
