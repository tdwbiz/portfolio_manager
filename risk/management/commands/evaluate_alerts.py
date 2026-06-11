"""
Evaluate alerts command for M7-Spec-1 alerting.

Matches pre-computed alert data against AlertConfig rows and writes
AlertHistory records.  Idempotent — no duplicate rows for the same
(alert_type, entity_type, entity_id, calc_date) key.

Email failures are caught and logged; they never abort the command.
"""

from __future__ import annotations

import importlib
import logging
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from risk.models import AlertConfig, AlertHistory

logger = logging.getLogger(__name__)

# Mapping from alert_type string to detection function name
ALERT_TYPE_TO_FUNCTION = {
    'urgency_level_change': 'detect_urgency_level_changes',
    'var_portfolio_breach': 'detect_var_portfolio_breach',
    'regime_shift': 'detect_regime_shift',
    'death_cross': 'detect_death_crosses',
    'earnings_proximity': 'detect_earnings_proximity',
    'concentration_breach': 'detect_concentration_breach',
    'taxable_gain_bracket': 'detect_taxable_gain_brackets',
}


def _load_detection_module() -> dict:
    """
    Load the alert_engine module once.

    Returns
    -------
    dict
        Module-level namespace with all detection functions.
    """
    mod = importlib.import_module('risk.services.alert_engine')
    return mod


class Command(BaseCommand):
    help = (
        'Evaluate all active alert configs against pre-computed data '
        'and fire AlertHistory records. Idempotent.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--calc-date',
            type=date.fromisoformat,
            help='Calculation date to evaluate (default: today).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Evaluate but do not write AlertHistory records.',
        )

    def handle(self, *args, **options):
        calc_date = options['calc_date'] or date.today()
        dry_run = options.get('dry_run', False)

        self.stdout.write(
            self.style.NOTICE(f"Evaluating alerts for {calc_date}...")
        )

        active_configs = AlertConfig.objects.filter(is_active=True)
        if not active_configs.exists():
            self.stdout.write(self.style.WARNING('No active alert configs found.'))
            return

        module = _load_detection_module()

        total_fired = 0
        total_emails_sent = 0
        total_emails_failed = 0

        for config in active_configs:
            func_name = ALERT_TYPE_TO_FUNCTION.get(config.alert_type)
            if not func_name:
                self.stdout.write(
                    self.style.ERROR(
                        f"Unknown alert_type '{config.alert_type}' in config {config.pk}"
                    )
                )
                continue

            detection_fn = getattr(module, func_name, None)
            if detection_fn is None:
                self.stdout.write(
                    self.style.ERROR(
                        f"Detection function '{func_name}' not found in alert_engine."
                    )
                )
                continue

            # --- Run detection ---
            try:
                results = detection_fn()
            except Exception:
                logger.exception(
                    "Detection function '%s' failed for config %s",
                    func_name, config.pk,
                )
                continue

            # --- Match results against config scope ---
            for result in results:
                # Scope filter: owner
                if config.owner and result.get('entity_id') != config.owner:
                    continue
                # Scope filter: account_id (not used in current detection)
                if config.account_id:
                    continue
                # Scope filter: symbol
                if config.symbol and result.get('symbol') != config.symbol:
                    continue

                # Threshold check
                threshold = config.threshold_value
                if threshold is not None:
                    trigger = result.get('trigger_value', '')
                    try:
                        trigger_val = float(trigger) if trigger else 0
                        if config.threshold_operator == '>':
                            if not (trigger_val > float(threshold)):
                                continue
                        elif config.threshold_operator == '>=':
                            if not (trigger_val >= float(threshold)):
                                continue
                        elif config.threshold_operator == '<':
                            if not (trigger_val < float(threshold)):
                                continue
                        elif config.threshold_operator == '<=':
                            if not (trigger_val <= float(threshold)):
                                continue
                        elif config.threshold_operator == '==':
                            if not (str(trigger_val) == str(threshold)):
                                continue
                        elif config.threshold_operator == '!=':
                            if not (str(trigger_val) != str(threshold)):
                                continue
                    except (ValueError, TypeError):
                        pass  # Skip threshold check if values are non-numeric

                # --- Deduplication key ---
                dedup_key = (
                    config.alert_type,
                    result.get('entity_type', ''),
                    result.get('entity_id', ''),
                    calc_date,
                )

                exists = AlertHistory.objects.filter(
                    alert_config=config,
                    alert_type=dedup_key[0],
                    entity_type=dedup_key[1],
                    entity_id=dedup_key[2],
                    calc_date=dedup_key[3],
                ).exists()

                if exists:
                    continue  # Idempotent — skip duplicate

                if dry_run:
                    self.stdout.write(
                        f"  [DRY RUN] Would fire: {config.alert_type} — "
                        f"{result.get('entity_description', '')}"
                    )
                    total_fired += 1
                    continue

                # --- Write AlertHistory ---
                try:
                    AlertHistory.objects.create(
                        alert_config=config,
                        alert_type=dedup_key[0],
                        calc_date=dedup_key[3],
                        entity_description=result.get('entity_description', ''),
                        entity_type=result.get('entity_type', ''),
                        entity_id=result.get('entity_id', ''),
                        symbol=result.get('symbol', ''),
                        trigger_value=result.get('trigger_value', ''),
                        previous_value=result.get('previous_value', ''),
                    )
                    total_fired += 1
                    self.stdout.write(
                        f"  Fired: {config.alert_type} — "
                        f"{result.get('entity_description', '')}"
                    )
                except IntegrityError:
                    # Race condition — another process wrote the same row
                    logger.warning(
                        "Duplicate AlertHistory for %s/%s/%s/%s",
                        *dedup_key,
                    )
                    continue

                # --- Send email ---
                if config.notify_email:
                    try:
                        from risk.services.alert_email import send_alert_email
                        send_alert_email(
                            recipient=config.notify_email,
                            alert_type=config.alert_type,
                            entity_description=result.get('entity_description', ''),
                            trigger_value=result.get('trigger_value', ''),
                            previous_value=result.get('previous_value', ''),
                            calc_date=calc_date,
                            symbol=result.get('symbol', ''),
                        )
                        total_emails_sent += 1
                    except Exception:
                        total_emails_failed += 1
                        logger.exception(
                            "Email failed for alert %s on config %s",
                            config.alert_type, config.pk,
                        )

        # --- Summary ---
        self.stdout.write(
            self.style.SUCCESS(
                f"Evaluated {active_configs.count()} active configs. "
                f"Fired {total_fired} alerts. "
                f"Emails sent: {total_emails_sent}, failed: {total_emails_failed}."
            )
        )
