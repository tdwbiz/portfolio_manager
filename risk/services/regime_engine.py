"""
Regime detection engine.

Pure Python functions for macroeconomic regime classification.
No Django ORM calls inside computation functions.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def score_yield_curve(slope: float | None) -> float | None:
    """
    Score the yield curve slope (10y minus 2y, in percentage points).
    Positive slope = growth/normal = higher score.
    Negative slope (inversion) = contraction signal = lower score.

    Breakpoints:
    slope >= +1.50: score = 85  (strongly normal, growth signal)
    slope >= +0.50: score = 65
    slope >= +0.00: score = 50  (flat)
    slope >= -0.50: score = 35  (mildly inverted)
    slope  < -0.50: score = 15  (deeply inverted, recession signal)

    Returns None if slope is None.
    """
    if slope is None:
        return None
    if slope >= 1.50:
        return 85.0
    elif slope >= 0.50:
        return 65.0
    elif slope >= 0.00:
        return 50.0
    elif slope >= -0.50:
        return 35.0
    else:
        return 15.0


def score_vix(vix: float | None) -> float | None:
    """
    Score the VIX level. Lower VIX = calmer markets = higher growth score.
    Note: this is a growth score, not a fear score.

    VIX < 12:  score = 85  (complacency, risk-on)
    VIX < 18:  score = 65
    VIX < 25:  score = 45
    VIX < 35:  score = 25  (elevated fear)
    VIX >= 35: score = 10  (crisis-level fear)

    Returns None if vix is None.
    """
    if vix is None:
        return None
    if vix < 12:
        return 85.0
    elif vix < 18:
        return 65.0
    elif vix < 25:
        return 45.0
    elif vix < 35:
        return 25.0
    else:
        return 10.0


def score_cpi(cpi_yoy: float | None) -> float | None:
    """
    Score CPI YoY. Higher CPI = higher inflation = lower score (bad for Q1/Q3).
    Score is the INFLATION dimension, inverted for quadrant mapping.

    cpi_yoy < 1.0:  score = 20  (deflation risk)
    cpi_yoy < 2.5:  score = 40  (target range)
    cpi_yoy < 4.0:  score = 60  (moderate)
    cpi_yoy < 6.0:  score = 80  (elevated)
    cpi_yoy >= 6.0: score = 95  (high inflation)

    Returns None if cpi_yoy is None.
    """
    if cpi_yoy is None:
        return None
    if cpi_yoy < 1.0:
        return 20.0
    elif cpi_yoy < 2.5:
        return 40.0
    elif cpi_yoy < 4.0:
        return 60.0
    elif cpi_yoy < 6.0:
        return 80.0
    else:
        return 95.0


def score_pmi(pmi: float | None) -> float | None:
    """
    Score PMI composite. >50 = expansion = higher growth score.

    pmi >= 57: score = 85
    pmi >= 52: score = 65
    pmi >= 50: score = 50  (neutral)
    pmi >= 47: score = 35
    pmi  < 47: score = 15  (contraction)

    Returns None if pmi is None.
    """
    if pmi is None:
        return None
    if pmi >= 57:
        return 85.0
    elif pmi >= 52:
        return 65.0
    elif pmi >= 50:
        return 50.0
    elif pmi >= 47:
        return 35.0
    else:
        return 15.0


def score_gdp(gdp_qoq: float | None) -> float | None:
    """
    Score GDP QoQ growth rate (annualised %).

    gdp_qoq >= 3.0: score = 80
    gdp_qoq >= 1.5: score = 60
    gdp_qoq >= 0.0: score = 45
    gdp_qoq  < 0.0: score = 20  (contraction)

    Returns None if gdp_qoq is None.
    """
    if gdp_qoq is None:
        return None
    if gdp_qoq >= 3.0:
        return 80.0
    elif gdp_qoq >= 1.5:
        return 60.0
    elif gdp_qoq >= 0.0:
        return 45.0
    else:
        return 20.0


def compute_quadrant_probabilities(
    indicator_scores: dict[str, float | None],
    weights: dict[str, float],
) -> tuple[dict[str, float], float, str]:
    """
    Map indicator scores to quadrant probabilities.

    indicator_scores: {indicator_name: score_0_to_100}
    weights: from rm_scoring_config keys:
        regime_weight_yield_curve (default 0.35)
        regime_weight_vix         (default 0.25)
        regime_weight_cpi         (default 0.20)
        regime_weight_pmi         (default 0.10)
        regime_weight_gdp         (default 0.10)

    If an indicator is NULL: redistribute its weight proportionally to available indicators.

    Quadrant mapping logic:
    Each quadrant is characterised by two dimensions:
        Growth dimension:    yield_curve_score, vix_score, pmi_score, gdp_score
        Inflation dimension: cpi_score

    growth_score  = weighted average of growth-dimension indicators
    inflate_score = cpi_score (or 50 if NULL — neutral assumption)

    Quadrant probabilities use a softmax-like distribution:
    Q1 probability ∝ growth_score  * (100 - inflate_score)  [high growth, low inflation]
    Q2 probability ∝ growth_score  * inflate_score           [high growth, high inflation]
    Q3 probability ∝ (100 - growth_score) * (100 - inflate_score)  [low growth, low inflation]
    Q4 probability ∝ (100 - growth_score) * inflate_score    [low growth, high inflation]

    Normalise to sum to 1.0.

    Returns:
        probs: {'Q1': float, 'Q2': float, 'Q3': float, 'Q4': float}
        confidence: float (0–100) — difference between top and second probability * 100
        quadrant: str — the highest-probability quadrant ('Q1', 'Q2', 'Q3', or 'Q4')
    """
    # Growth indicators
    growth_keys = ['yield_curve', 'vix', 'pmi', 'gdp']
    inflation_keys = ['cpi']

    # Compute weighted growth score
    growth_weight_sum = 0.0
    growth_weighted_sum = 0.0
    for key in growth_keys:
        w = weights.get(f'regime_weight_{key}', 0.0)
        score = indicator_scores.get(key)
        if score is not None:
            growth_weighted_sum += w * score
            growth_weight_sum += w

    # Compute weighted inflation score
    inflation_weight_sum = 0.0
    inflation_weighted_sum = 0.0
    for key in inflation_keys:
        w = weights.get(f'regime_weight_{key}', 0.0)
        score = indicator_scores.get(key)
        if score is not None:
            inflation_weighted_sum += w * score
            inflation_weight_sum += w

    # Redistribute weights for missing indicators
    total_weight = sum(weights.values())
    available_growth_weight = growth_weight_sum if growth_weight_sum > 0 else 1.0
    available_inflation_weight = inflation_weight_sum if inflation_weight_sum > 0 else 1.0

    # Normalize growth and inflation scores to 0-100 range
    growth_score = (growth_weighted_sum / available_growth_weight) if available_growth_weight > 0 else 50.0
    inflation_score = (inflation_weighted_sum / available_inflation_weight) if available_inflation_weight > 0 else 50.0

    # Quadrant raw probabilities (softmax-like)
    q1_raw = growth_score * (100 - inflation_score)
    q2_raw = growth_score * inflation_score
    q3_raw = (100 - growth_score) * (100 - inflation_score)
    q4_raw = (100 - growth_score) * inflation_score

    total_raw = q1_raw + q2_raw + q3_raw + q4_raw
    if total_raw == 0:
        # Fallback: equal probabilities
        probs = {'Q1': 0.25, 'Q2': 0.25, 'Q3': 0.25, 'Q4': 0.25}
    else:
        probs = {
            'Q1': q1_raw / total_raw,
            'Q2': q2_raw / total_raw,
            'Q3': q3_raw / total_raw,
            'Q4': q4_raw / total_raw,
        }

    # Confidence: difference between top and second highest probability * 100
    sorted_probs = sorted(probs.values(), reverse=True)
    confidence = (sorted_probs[0] - sorted_probs[1]) * 100

    # Determine quadrant
    quadrant = max(probs, key=probs.get)

    return probs, confidence, quadrant
