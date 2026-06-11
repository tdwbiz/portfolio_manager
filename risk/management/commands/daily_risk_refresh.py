"""
Daily risk refresh orchestrator for M7-Spec-1 alerting.

Runs the full risk calculation pipeline (regime, urgency, market
conditions, strategy recommendations, backtests, Monte Carlo, stress
tests) and then evaluates alerts.

Designed to be run once per day via cron/systemd/scheduled task.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'Run the full daily risk refresh pipeline: recalculate all risk '
        'models and evaluate alerts. Intended for daily scheduled execution.'
    )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE('=== Daily Risk Refresh Starting ==='))

        steps = [
            ('Regime detection', 'calc_regime'),
            ('Urgency scoring', 'calc_urgency'),
            ('Market conditions', 'calc_market_conditions'),
            ('Strategy recommendations', 'calc_strategy_recommendations'),
            ('Backtesting', 'run_backtest'),
            ('Monte Carlo simulation', 'run_montecarlo'),
            ('Stress testing', 'run_stress_test'),
            ('Alert evaluation', 'evaluate_alerts'),
        ]

        for step_name, command_name in steps:
            self.stdout.write(self.style.NOTICE(f'--- {step_name} ({command_name}) ---'))
            try:
                call_command(command_name, verbosity=1)
            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f'{step_name} FAILED: {exc}')
                )

        self.stdout.write(
            self.style.SUCCESS('=== Daily Risk Refresh Complete ===')
        )
