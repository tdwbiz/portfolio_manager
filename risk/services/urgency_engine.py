"""
Urgency Scoring Engine for M2-Spec-2.

Computes urgency scores for all risk entities based on:
1. M1 metric severity
2. Security driver tier
3. Regime adjustment
4. Market condition score
"""

import json
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from risk.models import (
    MarketCondition,
    PortfolioRiskMetrics,
    RegimeSensitivity,
    RegimeState,
    ScoringConfig,
    SecurityDriver,
    SecurityRiskMetrics,
    SecurityUniverse,
    UrgencyScore,
)
from risk.services.audit_hooks import write_audit_score_snapshot


def _get_config_value(key: str, default: str) -> str:
    """Retrieve a config value from rm_scoring_config, falling back to default."""
    try:
        return ScoringConfig.objects.get(config_key=key).config_value
    except ScoringConfig.DoesNotExist:
        return default


def load_scoring_config(calc_date: date) -> dict:
    """
    Load all scoring parameters from rm_scoring_config.
    Returns a dict with keys:

    Weights (must sum to 1.0):
        weight_m1_metrics      (default 0.35)
        weight_security_driver (default 0.20)
        weight_regime          (default 0.25)
        weight_market_condition (default 0.20)

    Thresholds:
        threshold_low    (default 25)
        threshold_medium (default 50)
        threshold_high   (default 75)

    Adjustments:
        earnings_proximity_boost (default 10)
        regime_transition_dampener (default 0.80)
    """
    config = {
        'weight_m1_metrics': float(_get_config_value('weight_m1_metrics', '0.35')),
        'weight_security_driver': float(_get_config_value('weight_security_driver', '0.20')),
        'weight_regime': float(_get_config_value('weight_regime', '0.25')),
        'weight_market_condition': float(_get_config_value('weight_market_condition', '0.20')),
        'threshold_low': float(_get_config_value('threshold_low', '25')),
        'threshold_medium': float(_get_config_value('threshold_medium', '50')),
        'threshold_high': float(_get_config_value('threshold_high', '75')),
        'earnings_proximity_boost': float(_get_config_value('earnings_proximity_boost', '10')),
        'regime_transition_dampener': float(_get_config_value('regime_transition_dampener', '0.80')),
    }

    weight_sum = (
        config['weight_m1_metrics']
        + config['weight_security_driver']
        + config['weight_regime']
        + config['weight_market_condition']
    )
    if abs(weight_sum - 1.0) > 0.001:
        raise ImproperlyConfigured(
            f"Scoring config weights must sum to 1.0, got {weight_sum}"
        )

    return config


def score_m1_metrics(
    symbol: str,
    security_metrics: dict,
    all_security_metrics: list[dict],
) -> tuple[float, dict]:
    """
    Score the M1 metric severity for a symbol relative to the universe.

    Three sub-metrics, equally weighted:
    1. VaR percentile rank (0–100): Highest VaR = rank 100 (most risky).
    2. Drawdown rank (0–100): Most negative drawdown = rank 100.
    3. Volatility rank (0–100): Highest vol = rank 100.

    Returns (score, inputs_dict).
    """
    var_val = security_metrics.get('var_parametric_95')
    dd_val = security_metrics.get('current_drawdown_from_252d_peak')
    vol_val = security_metrics.get('vol_252d')

    # Collect non-None values for ranking
    var_values = [m.get('var_parametric_95') for m in all_security_metrics if m.get('var_parametric_95') is not None]
    dd_values = [m.get('current_drawdown_from_252d_peak') for m in all_security_metrics if m.get('current_drawdown_from_252d_peak') is not None]
    vol_values = [m.get('vol_252d') for m in all_security_metrics if m.get('vol_252d') is not None]

    # If all three are NULL, return 0
    if var_val is None and dd_val is None and vol_val is None:
        return 0.0, {"score": 0.0, "inputs": {"var_percentile_rank": 0, "drawdown_rank": 0, "vol_rank": 0}}

    def percentile_rank(value: float | None, values: list[float]) -> float:
        """Rank where highest value = 100, lowest = 0.
        Uses position in sorted array: rank = (pos / (total-1)) * 100
        """
        if value is None:
            return 0.0
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        if len(sorted_vals) == 1:
            return 100.0
        pos = sorted_vals.index(value)
        return (pos / (len(sorted_vals) - 1)) * 100.0

    var_rank = percentile_rank(var_val, var_values)

    # For drawdown: more negative = riskier = higher rank
    # current_drawdown_from_252d_peak is negative when below peak
    # So -0.10 is riskier than -0.02
    # We want the most negative (smallest value) to rank 100
    if dd_val is not None and dd_values:
        # Sort ascending: most negative (smallest) first
        dd_sorted_asc = sorted(dd_values)
        # Find position: most negative (index 0) -> rank 100
        rank_pos = dd_sorted_asc.index(dd_val) if dd_val in dd_sorted_asc else 0
        # rank_pos 0 (most negative) -> 100, rank_pos last (least negative) -> 0
        dd_rank = (1 - rank_pos / (len(dd_sorted_asc) - 1)) * 100.0 if len(dd_sorted_asc) > 1 else 0.0
    else:
        dd_rank = 0.0

    vol_rank = percentile_rank(vol_val, vol_values)

    # Average only available ranks
    available_ranks = []
    if var_val is not None:
        available_ranks.append(var_rank)
    if dd_val is not None:
        available_ranks.append(dd_rank)
    if vol_val is not None:
        available_ranks.append(vol_rank)

    if not available_ranks:
        return 0.0, {"score": 0.0, "inputs": {"var_percentile_rank": 0, "drawdown_rank": 0, "vol_rank": 0}}

    final_score = sum(available_ranks) / len(available_ranks)

    inputs_dict = {
        "score": final_score,
        "inputs": {
            "var_percentile_rank": round(var_rank, 1),
            "drawdown_rank": round(dd_rank, 1),
            "vol_rank": round(vol_rank, 1),
        }
    }

    return final_score, inputs_dict


def score_security_driver(
    symbol: str,
    driver: dict,
    config: dict,
) -> tuple[float, dict]:
    """
    Score the inherent riskiness of this instrument.

    Three sub-scores equally weighted:
    1. asset_class_baseline
    2. sector_cyclicality
    3. liquidity_tier

    Returns (score, inputs_dict).
    """
    base_score = (
        driver['asset_class_baseline']
        + driver['sector_cyclicality']
        + driver['liquidity_tier']
    ) / 3.0

    if driver.get('earnings_proximity_flag'):
        final_score = min(100.0, base_score + config['earnings_proximity_boost'])
    else:
        final_score = base_score

    inputs_dict = {
        "score": final_score,
        "inputs": {
            "asset_class_baseline": driver['asset_class_baseline'],
            "sector_cyclicality": driver['sector_cyclicality'],
            "liquidity_tier": driver['liquidity_tier'],
        }
    }

    return final_score, inputs_dict


def score_regime_adjustment(
    symbol: str,
    driver_score: float,
    security_driver: dict,
    regime_state: dict,
    regime_sensitivities: dict[str, float],
    config: dict,
) -> tuple[float, dict]:
    """
    Adjust the driver score by the current regime sensitivity.

    Returns (adjusted_score, inputs_dict).
    """
    asset_class = security_driver.get('asset_class', '')
    multiplier = regime_sensitivities.get(asset_class, 1.0)

    adjusted_score = driver_score * multiplier

    if regime_state.get('transition_flag'):
        adjusted_score *= config['regime_transition_dampener']

    # Clamp to 0-100
    adjusted_score = max(0.0, min(100.0, adjusted_score))

    inputs_dict = {
        "score": adjusted_score,
        "inputs": {
            "driver_score": driver_score,
            "multiplier": multiplier,
            "quadrant": regime_state.get('quadrant', ''),
        }
    }

    return adjusted_score, inputs_dict


def score_market_condition(
    symbol: str,
    market_condition: dict | None,
) -> tuple[float, dict]:
    """
    Score current technical indicator state. Higher = more technically concerning.

    Returns (score, inputs_dict).
    """
    if market_condition is None:
        return 0.0, {"score": 0.0, "inputs": {}}

    # RSI score
    rsi = market_condition.get('rsi_14')
    if rsi is not None:
        if rsi < 20:
            rsi_score = 90
        elif rsi < 30:
            rsi_score = 70
        elif rsi < 45:
            rsi_score = 40
        elif rsi < 55:
            rsi_score = 30
        elif rsi < 70:
            rsi_score = 40
        elif rsi < 80:
            rsi_score = 70
        else:
            rsi_score = 90
    else:
        rsi_score = 30

    # Crossover score
    crossover_type = market_condition.get('crossover_type', '')
    sma_50 = market_condition.get('sma_50')
    sma_200 = market_condition.get('sma_200')

    if crossover_type == 'death':
        crossover_score = 80
    elif crossover_type == 'golden':
        crossover_score = 20
    elif crossover_type == 'none':
        if sma_50 is not None and sma_200 is not None:
            if sma_50 < sma_200:
                crossover_score = 55
            else:
                crossover_score = 25
        else:
            crossover_score = 25
    else:
        crossover_score = 25

    # Market phase score
    phase_scores = {
        'bear': 80,
        'correction': 60,
        'overbought': 55,
        'oversold': 65,
        'uptrend': 20,
    }
    phase = market_condition.get('market_phase', '')
    phase_score = phase_scores.get(phase, 30)

    # Bollinger Band score
    pct_b = market_condition.get('bb_pct_b')
    if pct_b is not None:
        if pct_b > 0.95:
            bb_score = 75
        elif pct_b > 0.80:
            bb_score = 55
        elif pct_b > 0.20:
            bb_score = 25
        elif pct_b > 0.05:
            bb_score = 55
        else:
            bb_score = 75
    else:
        bb_score = 25

    final_score = (rsi_score + crossover_score + phase_score + bb_score) / 4.0

    inputs_dict = {
        "score": final_score,
        "inputs": {
            "rsi_score": rsi_score,
            "crossover_score": crossover_score,
            "phase_score": phase_score,
            "bb_score": bb_score,
        }
    }

    return final_score, inputs_dict


def compute_urgency_score(
    component_m1: float,
    component_driver: float,
    component_regime: float,
    component_market: float,
    config: dict,
) -> tuple[float, str, str]:
    """
    Combine four component scores into a single urgency score.

    Returns (score_raw, urgency_level, dominant_component).
    """
    score_raw = (
        component_m1 * config['weight_m1_metrics']
        + component_driver * config['weight_security_driver']
        + component_regime * config['weight_regime']
        + component_market * config['weight_market_condition']
    )

    # Classify urgency level
    if score_raw < config['threshold_low']:
        urgency_level = 'Low'
    elif score_raw < config['threshold_medium']:
        urgency_level = 'Medium'
    elif score_raw < config['threshold_high']:
        urgency_level = 'High'
    else:
        urgency_level = 'Critical'

    # Determine dominant component
    contributions = {
        'm1_metrics': component_m1 * config['weight_m1_metrics'],
        'security_driver': component_driver * config['weight_security_driver'],
        'regime': component_regime * config['weight_regime'],
        'market_condition': component_market * config['weight_market_condition'],
    }
    dominant_component = max(contributions, key=contributions.get)

    return score_raw, urgency_level, dominant_component


def rollup_account(
    account_id: str,
    security_scores: list[dict],
    position_weights: dict[str, float],
    config: dict,
) -> tuple[float, str]:
    """
    Roll up security_in_account scores to account level.

    Returns (score_raw, urgency_level).
    """
    if not security_scores:
        return 0.0, 'Low'

    # Weighted average score
    weighted_sum = 0.0
    total_weight = 0.0
    for sec_score in security_scores:
        symbol = sec_score.get('symbol', '')
        weight = position_weights.get(symbol, 0.0)
        weighted_sum += sec_score.get('score_raw', 0.0) * weight
        total_weight += weight

    if total_weight > 0:
        score_raw = weighted_sum / total_weight
    else:
        score_raw = weighted_sum

    # Classify using same thresholds
    if score_raw < config['threshold_low']:
        urgency_level = 'Low'
    elif score_raw < config['threshold_medium']:
        urgency_level = 'Medium'
    elif score_raw < config['threshold_high']:
        urgency_level = 'High'
    else:
        urgency_level = 'Critical'

    # Critical floor rule: if any security is Critical, account is at least High
    has_critical = any(s.get('urgency_level') == 'Critical' for s in security_scores)
    if has_critical and urgency_level == 'Low':
        urgency_level = 'High'
    elif has_critical and urgency_level == 'Medium':
        urgency_level = 'High'

    return score_raw, urgency_level


def rollup_owner(
    owner: str,
    account_scores: list[dict],
    account_mv_weights: dict[str, float],
    config: dict,
) -> tuple[float, str]:
    """
    Roll up account scores to owner level.

    Returns (score_raw, urgency_level).
    """
    if not account_scores:
        return 0.0, 'Low'

    weighted_sum = 0.0
    total_weight = 0.0
    for acc_score in account_scores:
        acc_id = acc_score.get('entity_id', '')
        weight = account_mv_weights.get(acc_id, 0.0)
        weighted_sum += acc_score.get('score_raw', 0.0) * weight
        total_weight += weight

    if total_weight > 0:
        score_raw = weighted_sum / total_weight
    else:
        score_raw = weighted_sum

    if score_raw < config['threshold_low']:
        urgency_level = 'Low'
    elif score_raw < config['threshold_medium']:
        urgency_level = 'Medium'
    elif score_raw < config['threshold_high']:
        urgency_level = 'High'
    else:
        urgency_level = 'Critical'

    # Critical floor rule
    has_critical = any(s.get('urgency_level') == 'Critical' for s in account_scores)
    if has_critical and urgency_level in ('Low', 'Medium'):
        urgency_level = 'High'

    return score_raw, urgency_level


def rollup_portfolio(
    account_scores: list[dict],
    account_mv_weights: dict[str, float],
    config: dict,
) -> tuple[float, str]:
    """
    Roll up all non-excluded account scores to portfolio level.

    Returns (score_raw, urgency_level).
    """
    if not account_scores:
        return 0.0, 'Low'

    weighted_sum = 0.0
    total_weight = 0.0
    for acc_score in account_scores:
        acc_id = acc_score.get('entity_id', '')
        weight = account_mv_weights.get(acc_id, 0.0)
        weighted_sum += acc_score.get('score_raw', 0.0) * weight
        total_weight += weight

    if total_weight > 0:
        score_raw = weighted_sum / total_weight
    else:
        score_raw = weighted_sum

    if score_raw < config['threshold_low']:
        urgency_level = 'Low'
    elif score_raw < config['threshold_medium']:
        urgency_level = 'Medium'
    elif score_raw < config['threshold_high']:
        urgency_level = 'High'
    else:
        urgency_level = 'Critical'

    # Critical floor rule
    has_critical = any(s.get('urgency_level') == 'Critical' for s in account_scores)
    if has_critical and urgency_level in ('Low', 'Medium'):
        urgency_level = 'High'

    return score_raw, urgency_level


def calculate_urgency_for_date(
    calc_date: date,
    entity_type_filter: str | None = None,
    account_id_filter: str | None = None,
    symbol_filter: str | None = None,
) -> dict:
    """
    Main entry point for urgency calculation.

    Returns summary dict with counts and distribution.
    """
    config = load_scoring_config(calc_date)

    # Load regime state
    try:
        regime_state = RegimeState.objects.get(calc_date=calc_date)
        regime_dict = {
            'quadrant': regime_state.quadrant,
            'transition_flag': regime_state.transition_flag,
        }
    except RegimeState.DoesNotExist:
        regime_dict = {'quadrant': 'Q1', 'transition_flag': False}

    # Load regime sensitivities
    sensitivities_map = {}
    for rs in RegimeSensitivity.objects.filter(regime_quadrant=regime_dict['quadrant']):
        sensitivities_map[rs.asset_class] = float(rs.sensitivity_multiplier)

    # Load market conditions
    market_conditions = {}
    if symbol_filter:
        symbols_to_load = [symbol_filter]
    else:
        symbols_to_load = list(
            SecurityUniverse.objects.filter(is_held=True).values_list('symbol', flat=True)
        )

    for mc in MarketCondition.objects.filter(
        symbol__in=symbols_to_load, calc_date=calc_date
    ):
        market_conditions[mc.symbol] = {
            'rsi_14': float(mc.rsi_14) if mc.rsi_14 is not None else None,
            'crossover_type': mc.crossover_type,
            'sma_50': float(mc.sma_50) if mc.sma_50 is not None else None,
            'sma_200': float(mc.sma_200) if mc.sma_200 is not None else None,
            'market_phase': mc.market_phase,
            'bb_pct_b': float(mc.bb_pct_b) if mc.bb_pct_b is not None else None,
        }

    # Load security metrics
    security_metrics = {}
    for sm in SecurityRiskMetrics.objects.filter(
        symbol__in=symbols_to_load, calc_date=calc_date
    ):
        security_metrics[sm.symbol] = {
            'var_parametric_95': float(sm.var_parametric_95) if sm.var_parametric_95 is not None else None,
            'current_drawdown_from_252d_peak': float(sm.current_drawdown_from_252d_peak) if sm.current_drawdown_from_252d_peak is not None else None,
            'vol_252d': float(sm.vol_252d) if sm.vol_252d is not None else None,
        }

    # Load security drivers
    security_drivers = {}
    for sd in SecurityDriver.objects.filter(symbol__in=symbols_to_load):
        security_drivers[sd.symbol] = {
            'asset_class_baseline': float(sd.asset_class_baseline),
            'sector_cyclicality': float(sd.sector_cyclicality),
            'liquidity_tier': float(sd.liquidity_tier),
            'earnings_proximity_flag': sd.earnings_proximity_flag,
        }

    # Load security universe for asset_class mapping
    universe_map = {}
    for su in SecurityUniverse.objects.filter(symbol__in=symbols_to_load):
        universe_map[su.symbol] = su.asset_class

    # Load portfolio risk metrics for roll-up
    portfolio_metrics = {}
    if entity_type_filter is None or entity_type_filter in ('account', 'owner', 'portfolio'):
        for prm in PortfolioRiskMetrics.objects.filter(calc_date=calc_date):
            portfolio_metrics[f"{prm.entity_type}::{prm.entity_id}"] = prm

    # Calculate security_in_account scores
    security_in_account_scores = []
    skipped_symbols = []

    # Get held symbols in accounts
    # We need to know which symbols are in which accounts
    # For now, use the security universe and assume all held symbols are in all accounts
    # In reality, we'd query position_history or similar
    # Using a simplified approach: iterate held symbols
    
    # Get account IDs from portfolio risk metrics
    account_ids = set()
    for key, prm in portfolio_metrics.items():
        if prm.entity_type == 'account':
            account_ids.add(prm.entity_id)

    if not account_ids:
        # Fallback: try to get from core accounts
        try:
            from core.models import Account
            account_ids = set(Account.objects.values_list('account_id', flat=True))
        except Exception:
            account_ids = {'TAXABLE_TOM_SUSAN', 'RRSP_TOM', 'TFSASUSAN'}

    for symbol in symbols_to_load:
        if symbol_filter and symbol != symbol_filter:
            continue

        if symbol not in security_metrics:
            skipped_symbols.append(symbol)
            continue

        sym_metrics = security_metrics[symbol]
        all_metrics_list = list(security_metrics.values())

        # Component 1: M1 Metrics
        m1_score, m1_inputs = score_m1_metrics(symbol, sym_metrics, all_metrics_list)

        # Component 2: Security Driver
        if symbol in security_drivers:
            driver = security_drivers[symbol]
            driver_score, driver_inputs = score_security_driver(symbol, driver, config)
        else:
            driver_score = 50.0  # Default
            driver_inputs = {"score": 50.0, "inputs": {}}

        # Component 3: Regime Adjustment
        asset_class = universe_map.get(symbol, 'etf_equity')
        driver_for_regime = {'asset_class': asset_class}
        regime_score, regime_inputs = score_regime_adjustment(
            symbol, driver_score, driver_for_regime, regime_dict, sensitivities_map, config
        )

        # Component 4: Market Condition
        mc = market_conditions.get(symbol)
        market_score, market_inputs = score_market_condition(symbol, mc)

        # Composite
        score_raw, urgency_level, dominant = compute_urgency_score(
            m1_score, driver_score, regime_score, market_score, config
        )

        # Build JSON
        component_json = {
            "m1": {"score": m1_score, "weight": config['weight_m1_metrics'], "inputs": m1_inputs.get("inputs", {})},
            "driver": {"score": driver_score, "weight": config['weight_security_driver'], "inputs": driver_inputs.get("inputs", {})},
            "regime": {"score": regime_score, "weight": config['weight_regime'], "inputs": regime_inputs.get("inputs", {})},
            "market": {"score": market_score, "weight": config['weight_market_condition'], "inputs": market_inputs.get("inputs", {})},
        }

        for account_id in account_ids:
            if account_id_filter and account_id != account_id_filter:
                continue

            entity_id = f"{account_id}::{symbol}"
            UrgencyScore.objects.update_or_create(
                entity_type='security_in_account',
                entity_id=entity_id,
                calc_date=calc_date,
                defaults={
                    'symbol': symbol,
                    'account_id': account_id,
                    'score_raw': Decimal(str(round(score_raw, 2))),
                    'urgency_level': urgency_level,
                    'component_m1_score': Decimal(str(round(m1_score, 2))) if m1_score is not None else None,
                    'component_driver_score': Decimal(str(round(driver_score, 2))) if driver_score is not None else None,
                    'component_regime_score': Decimal(str(round(regime_score, 2))) if regime_score is not None else None,
                    'component_market_score': Decimal(str(round(market_score, 2))) if market_score is not None else None,
                    'dominant_component': dominant,
                    'component_scores_json': json.dumps(component_json),
                },
            )

            # Audit hook: write score snapshot
            try:
                write_audit_score_snapshot(
                    entity_type='security_in_account',
                    entity_id=entity_id,
                    symbol=symbol,
                    calc_date=calc_date,
                    urgency_level=urgency_level,
                    score_raw=score_raw,
                    component_scores_json=json.dumps(component_json),
                    weights_config={
                        'weight_m1_metrics': config['weight_m1_metrics'],
                        'weight_security_driver': config['weight_security_driver'],
                        'weight_regime': config['weight_regime'],
                        'weight_market_condition': config['weight_market_condition'],
                    },
                    input_metrics_json=json.dumps(m1_inputs.get("inputs", {})),
                )
            except Exception:
                # Audit write failures logged at ERROR but never raise out of M2
                pass

            security_in_account_scores.append({
                'symbol': symbol,
                'account_id': account_id,
                'entity_id': entity_id,
                'score_raw': score_raw,
                'urgency_level': urgency_level,
            })

    # Roll up to account level
    account_scores = []
    for account_id in account_ids:
        if account_id_filter and account_id != account_id_filter:
            continue

        sec_scores = [s for s in security_in_account_scores if s['account_id'] == account_id]
        if not sec_scores:
            continue

        # Get position weights from portfolio metrics
        acc_key = f"account::{account_id}"
        acc_prm = portfolio_metrics.get(acc_key)
        
        # Simple equal weighting if no position data
        weights = {s['symbol']: 1.0 for s in sec_scores}
        total_w = sum(weights.values())
        if total_w > 0:
            weights = {k: v / total_w for k, v in weights.items()}

        score_raw, urgency_level = rollup_account(account_id, sec_scores, weights, config)

        UrgencyScore.objects.update_or_create(
            entity_type='account',
            entity_id=account_id,
            calc_date=calc_date,
            defaults={
                'symbol': '',
                'account_id': account_id,
                'score_raw': Decimal(str(round(score_raw, 2))),
                'urgency_level': urgency_level,
                'component_m1_score': None,
                'component_driver_score': None,
                'component_regime_score': None,
                'component_market_score': None,
                'dominant_component': '',
                'component_scores_json': json.dumps({"rollup": "account"}),
            },
        )

        # Audit hook: write score snapshot
        try:
            write_audit_score_snapshot(
                entity_type='account',
                entity_id=account_id,
                symbol='',
                calc_date=calc_date,
                urgency_level=urgency_level,
                score_raw=score_raw,
                component_scores_json=json.dumps({"rollup": "account"}),
                weights_config={
                    'weight_m1_metrics': config['weight_m1_metrics'],
                    'weight_security_driver': config['weight_security_driver'],
                    'weight_regime': config['weight_regime'],
                    'weight_market_condition': config['weight_market_condition'],
                },
                input_metrics_json='{}',
            )
        except Exception:
            pass

        account_scores.append({
            'entity_id': account_id,
            'score_raw': score_raw,
            'urgency_level': urgency_level,
        })

    # Roll up to owner level
    # Simplified: assume accounts starting with RRSP or TFSA belong to specific owners
    # In reality, this would query account-owner relationships
    owner_accounts = {}
    for acc in account_ids:
        if 'TOM' in acc.upper():
            owner = 'TOM'
        elif 'SUSAN' in acc.upper():
            owner = 'SUSAN'
        else:
            owner = 'UNKNOWN'
        
        if owner not in owner_accounts:
            owner_accounts[owner] = []
        owner_accounts[owner].append({
            'entity_id': acc,
            'score_raw': next((s['score_raw'] for s in account_scores if s['entity_id'] == acc), 0),
            'urgency_level': next((s['urgency_level'] for s in account_scores if s['entity_id'] == acc), 'Low'),
        })

    for owner, acc_scores_list in owner_accounts.items():
        weights = {a['entity_id']: 1.0 for a in acc_scores_list}
        total_w = sum(weights.values())
        if total_w > 0:
            weights = {k: v / total_w for k, v in weights.items()}

        score_raw, urgency_level = rollup_owner(owner, acc_scores_list, weights, config)

        UrgencyScore.objects.update_or_create(
            entity_type='owner',
            entity_id=owner,
            calc_date=calc_date,
            defaults={
                'symbol': '',
                'account_id': '',
                'score_raw': Decimal(str(round(score_raw, 2))),
                'urgency_level': urgency_level,
                'component_m1_score': None,
                'component_driver_score': None,
                'component_regime_score': None,
                'component_market_score': None,
                'dominant_component': '',
                'component_scores_json': json.dumps({"rollup": "owner"}),
            },
        )

        # Audit hook: write score snapshot
        try:
            write_audit_score_snapshot(
                entity_type='owner',
                entity_id=owner,
                symbol='',
                calc_date=calc_date,
                urgency_level=urgency_level,
                score_raw=score_raw,
                component_scores_json=json.dumps({"rollup": "owner"}),
                weights_config={
                    'weight_m1_metrics': config['weight_m1_metrics'],
                    'weight_security_driver': config['weight_security_driver'],
                    'weight_regime': config['weight_regime'],
                    'weight_market_condition': config['weight_market_condition'],
                },
                input_metrics_json='{}',
            )
        except Exception:
            pass

    # Roll up to portfolio level
    portfolio_weights = {a['entity_id']: 1.0 for a in account_scores}
    total_w = sum(portfolio_weights.values())
    if total_w > 0:
        portfolio_weights = {k: v / total_w for k, v in portfolio_weights.items()}

    score_raw, urgency_level = rollup_portfolio(account_scores, portfolio_weights, config)

    UrgencyScore.objects.update_or_create(
        entity_type='portfolio',
        entity_id='ALL',
        calc_date=calc_date,
        defaults={
            'symbol': '',
            'account_id': '',
            'score_raw': Decimal(str(round(score_raw, 2))),
            'urgency_level': urgency_level,
            'component_m1_score': None,
            'component_driver_score': None,
            'component_regime_score': None,
            'component_market_score': None,
            'dominant_component': '',
            'component_scores_json': json.dumps({"rollup": "portfolio"}),
        },
    )

    # Audit hook: write score snapshot
    try:
        write_audit_score_snapshot(
            entity_type='portfolio',
            entity_id='ALL',
            symbol='',
            calc_date=calc_date,
            urgency_level=urgency_level,
            score_raw=score_raw,
            component_scores_json=json.dumps({"rollup": "portfolio"}),
            weights_config={
                'weight_m1_metrics': config['weight_m1_metrics'],
                'weight_security_driver': config['weight_security_driver'],
                'weight_regime': config['weight_regime'],
                'weight_market_condition': config['weight_market_condition'],
            },
            input_metrics_json='{}',
        )
    except Exception:
        pass

    # Summary
    distribution = {'Low': 0, 'Medium': 0, 'High': 0, 'Critical': 0}
    for us in UrgencyScore.objects.filter(calc_date=calc_date):
        distribution[us.urgency_level] = distribution.get(us.urgency_level, 0) + 1

    return {
        'calc_date': str(calc_date),
        'security_in_account_count': len(security_in_account_scores),
        'account_count': len(account_scores),
        'owner_count': len(owner_accounts),
        'portfolio_count': 1,
        'skipped_symbols': skipped_symbols,
        'distribution': distribution,
    }
