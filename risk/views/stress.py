"""Stress Test Results view."""
import json

from django.shortcuts import get_object_or_404, render
from django.http import Http404

from risk.models import SimulationRun, SimulationResult


def stress_results(request, run_id: int):
    """
    Display stress test or Monte Carlo simulation results.
    Works for both simulation_type='stress_test' and 'montecarlo'.
    """
    sim_run = get_object_or_404(SimulationRun, id=run_id)
    sim_result = get_object_or_404(SimulationResult, run=sim_run)

    # Parse per-position breakdown for stress tests
    per_position = []
    if sim_result.stress_per_position_json:
        try:
            per_position = json.loads(sim_result.stress_per_position_json)
            per_position.sort(key=lambda x: x.get('loss_usd', 0))  # worst losses first
        except (json.JSONDecodeError, KeyError):
            pass

    # Parse percentile series for Monte Carlo chart
    percentile_series = {}
    if sim_result.percentile_series_json:
        try:
            percentile_series = json.loads(sim_result.percentile_series_json)
        except json.JSONDecodeError:
            pass

    context = {
        'sim_run': sim_run,
        'sim_result': sim_result,
        'per_position': per_position,
        'percentile_series': percentile_series,
        'percentile_json': sim_result.percentile_series_json or '{}',
    }
    return render(request, 'risk/stress_results.html', context)
