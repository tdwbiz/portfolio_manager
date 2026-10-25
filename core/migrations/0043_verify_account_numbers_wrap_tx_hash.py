"""Data migration — phase 2 step 10 (docs/plans/number-columns.md).

Verifies that every stored account number still matches the linked account,
empties the parked risk tables, and wraps every stored tx_hash exactly once.
Data only. It never re-points a link and never prints a value: any problem
aborts the migration with table + row counts only.

This file carries its own private copies of the normalisation and HMAC code
(keyed by ``settings.ACCOUNT_FINGERPRINT_KEY`` at run time) so later changes
to ``core/utils/account_fingerprint.py`` cannot change what this migration
does.

Reverse is a no-op by design: wrapped hashes and wiped risk rows cannot be
restored; rollback is tdw's PortfolioDEV backup.
"""

import hashlib
import hmac

from django.conf import settings
from django.db import migrations

MIN_KEY_LENGTH = 32

# Parked risk tables emptied here (plan Q2): all test/computed data, and the
# string account copies in them are labels, never real numbers.
RISK_MODELS_TO_EMPTY = [
    "PortfolioRiskMetrics",
    "UrgencyScore",
    "StrategyRecommendation",
    "BacktestRun",
    "SimulationRun",
    "CustomStressScenario",
    "AlertConfig",
    "AlertHistory",
    "AuditScoreSnapshot",
    "AuditRecommendationLog",
]


def _normalize(raw: str) -> str:
    """Private copy of normalize_account_number (do not edit silently)."""
    cleaned = "".join(ch for ch in (raw or "") if ch.isalnum()).upper()
    if cleaned.isdigit() and len(cleaned) >= 8:
        cleaned = cleaned[:8]
    return cleaned


def _key() -> bytes:
    key = (getattr(settings, "ACCOUNT_FINGERPRINT_KEY", "") or "").strip()
    if len(key) < MIN_KEY_LENGTH:
        raise ValueError(
            f"ACCOUNT_FINGERPRINT_KEY is not set (need at least {MIN_KEY_LENGTH} "
            "characters); migration 0043 cannot run — stopping."
        )
    return key.encode("utf-8")


def _hmac_hex(message: str) -> str:
    """Private copy of the HMAC-SHA256 used for fingerprints and tx hashes."""
    return hmac.new(_key(), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_number_links(apps, model_name: str, number_field: str):
    """Check one table; return (checked, problems) counts.

    Every distinct non-empty stored number must fingerprint-match exactly the
    account its FK points at. A value matching no account, or matching a
    different account than the FK, is counted as a problem — never re-pointed.
    """
    model = apps.get_model("core", model_name)
    fp_to_pk = {
        acct.fingerprint: acct.pk
        for acct in apps.get_model("core", "Account").objects.all()
        if acct.fingerprint
    }
    rows = (
        model.objects.filter(**{f"{number_field}__isnull": False})
        .exclude(**{number_field: ""})
        .values_list(number_field, "account_id")
        .distinct()
    )
    checked = 0
    problems = 0
    for value, fk_pk in rows:
        checked += 1
        target_pk = fp_to_pk.get(_hmac_hex(_normalize(value)))
        if target_pk != fk_pk:
            problems += 1
    return checked, problems


def migrate_number_columns(apps, schema_editor):
    # 1. Verify stored numbers against the linked accounts (counts only).
    checks = [
        ("core_rbcmonthly", _verify_number_links(apps, "RbcMonthly", "account_number")),
        ("core_rbcannual", _verify_number_links(apps, "RbcAnnual", "account_number")),
        ("core_ledger_account", _verify_number_links(apps, "LedgerAccount", "account_id_str")),
    ]
    problem_tables = [(table, problems) for table, (checked, problems) in checks if problems]
    if problem_tables:
        raise RuntimeError(
            "Migration 0043 aborted — stored account numbers do not match their "
            "linked accounts (counts only): "
            + ", ".join(f"{table}: {problems} row(s)" for table, problems in problem_tables)
        )

    # 2. Empty the parked risk tables (Q2: test/computed data).
    for model_name in RISK_MODELS_TO_EMPTY:
        apps.get_model("risk", model_name).objects.all().delete()

    # 3. Wrap every stored tx_hash exactly once: new = HMAC-SHA256(key, old).
    tx_model = apps.get_model("core", "Transaction")
    for (old_hash,) in tx_model.objects.exclude(tx_hash="").values_list("tx_hash").distinct():
        tx_model.objects.filter(tx_hash=old_hash).update(tx_hash=_hmac_hex(old_hash))


def reverse_noop(apps, schema_editor):
    # Documented no-op — see module docstring.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0042_remove_account_account_id"),
        ("risk", "0016_alter_alertconfig_symbol_and_more"),
    ]

    atomic = True

    operations = [
        migrations.RunPython(migrate_number_columns, reverse_noop),
    ]
