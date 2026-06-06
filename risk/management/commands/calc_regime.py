"""
Management command: calc_regime

Reads rm_economic_indicators, detects current regime quadrant,
writes one row to rm_regime_state.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from risk.models import EconomicIndicator, RegimeState, ScoringConfig
from risk.services.regime_engine import (
    score_cpi,
    score_gdp,
    score_pmi,
    score_vix,
    score_yield_curve,
    compute_quadrant_probabilities,
)

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    'regime_weight_yield_curve': 0.35,
    'regime_weight_vix': 0.25,
    'regime_weight_cpi': 0.20,
    'regime_weight_pmi': 0.10,
    'regime_weight_gdp': 0.10,
}


class Command(BaseCommand):
    help = 'Calculate macroeconomic regime and store in rm_regime_state'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Calculation date (YYYY-MM-DD). Defaults to today.',
        )

    def handle(self, *args, **options):
        calc_date_str = options.get('date')
        if calc_date_str:
            try:
                calc_date = date.fromisoformat(calc_date_str)
            except ValueError:
                raise CommandError(f"Invalid date format: {calc_date_str}. Use YYYY-MM-DD.")
        else:
            calc_date = date.today()

        # Load indicators
        indicators = EconomicIndicator.objects.filter(
            date__lte=calc_date
        ).order_by('date')

        indicator_values = {}
        for ind in indicators:
            indicator_values[ind.indicator_name] = float(ind.value)

        # Get most recent values
        yield_curve_slope = indicator_values.get('yield_curve_slope')
        vix_close = indicator_values.get('vix_close')
        cpi_yoy = indicator_values.get('cpi_yoy')
        pmi_composite = indicator_values.get('pmi_composite')
        gdp_growth_qoq = indicator_values.get('gdp_growth_qoq')

        # Score indicators
        yield_curve_score = score_yield_curve(yield_curve_slope)
        vix_score_val = score_vix(vix_close)
        cpi_score_val = score_cpi(cpi_yoy)
        pmi_score_val = score_pmi(pmi_composite)
        gdp_score_val = score_gdp(gdp_growth_qoq)

        indicator_scores = {
            'yield_curve': yield_curve_score,
            'vix': vix_score_val,
            'cpi': cpi_score_val,
            'pmi': pmi_score_val,
            'gdp': gdp_score_val,
        }

        # Load weights from config
        weights = dict(DEFAULT_WEIGHTS)
        for key in weights:
            config_entry = ScoringConfig.objects.filter(config_key=key).first()
            if config_entry:
                try:
                    weights[key] = float(config_entry.config_value)
                except (ValueError, TypeError):
                    pass

        # Compute quadrant probabilities
        probs, confidence, quadrant = compute_quadrant_probabilities(indicator_scores, weights)

        # Detect transition flag
        transition_flag = False
        prev_state = RegimeState.objects.filter(calc_date__lt=calc_date).order_by('-calc_date').first()
        if prev_state:
            transition_threshold = 15.0  # Default threshold
            config_entry = ScoringConfig.objects.filter(config_key='transition_threshold_pct').first()
            if config_entry:
                try:
                    transition_threshold = float(config_entry.config_value)
                except (ValueError, TypeError):
                    pass

            if prev_state.quadrant != quadrant:
                # Check probability shift
                prev_probs = {
                    'Q1': float(prev_state.prob_q1),
                    'Q2': float(prev_state.prob_q2),
                    'Q3': float(prev_state.prob_q3),
                    'Q4': float(prev_state.prob_q4),
                }
                shift = abs(probs[quadrant] - prev_probs.get(quadrant, 0))
                if shift * 100 > transition_threshold:
                    transition_flag = True

        # Build indicator snapshot
        indicator_snapshot = {
            'yield_curve_slope': yield_curve_slope,
            'vix_close': vix_close,
            'cpi_yoy': cpi_yoy,
            'pmi_composite': pmi_composite,
            'gdp_growth_qoq': gdp_growth_qoq,
        }

        # UPSERT into rm_regime_state
        RegimeState.objects.update_or_create(
            calc_date=calc_date,
            defaults={
                'quadrant': quadrant,
                'confidence_score': confidence,
                'transition_flag': transition_flag,
                'yield_curve_score': yield_curve_score,
                'vix_score': vix_score_val,
                'cpi_score': cpi_score_val,
                'pmi_score': pmi_score_val,
                'gdp_score': gdp_score_val,
                'prob_q1': probs['Q1'],
                'prob_q2': probs['Q2'],
                'prob_q3': probs['Q3'],
                'prob_q4': probs['Q4'],
                'indicator_snapshot_json': json.dumps(indicator_snapshot),
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Regime calculated for {calc_date}: {quadrant} "
                f"(confidence: {confidence:.2f}, transition: {transition_flag})"
            )
        )
