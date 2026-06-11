"""
Management command: audit_retention_check

Reports records approaching the 7-year retention limit.
This command NEVER deletes data - it is purely reporting.

Retention policy:
- All rm_audit_* tables have a 7-year (2557 day) retention period
- Records older than 7 years are flagged for review
- Records within warning_days of the limit are reported as warnings

Usage:
    python manage.py audit_retention_check [--warning-days 90]
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from risk.models import (
    AuditActionLog,
    AuditEntry,
    AuditRationale,
    AuditRegimeLog,
    AuditRecommendationLog,
    AuditRetentionLog,
    AuditScoreSnapshot,
    AuditSimulationRun,
    AuditTaxLog,
)


class Command(BaseCommand):
    help = 'Report audit records approaching the 7-year retention limit.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--warning-days',
            type=int,
            default=90,
            help='Flag records expiring within this many days (default: 90).',
        )

    def handle(self, *args, **options):
        warning_days = options['warning_days']
        today = timezone.now().date()
        retention_cutoff = today + timedelta(days=2557)  # 7 years
        warning_cutoff = today + timedelta(days=warning_days)

        self.stdout.write(f"Audit Retention Check — {today}")
        self.stdout.write(f"  Retention period: 7 years (2557 days)")
        self.stdout.write(f"  Warning threshold: {warning_days} days")
        self.stdout.write(f"  Records expiring after: {retention_cutoff}")
        self.stdout.write("")

        total_warnings = 0
        total_expired = 0

        # Check each audit table
        tables = [
            ('AuditEntry', AuditEntry.objects),
            ('AuditScoreSnapshot', AuditScoreSnapshot.objects),
            ('AuditRationale', AuditRationale.objects),
            ('AuditRecommendationLog', AuditRecommendationLog.objects),
            ('AuditActionLog', AuditActionLog.objects),
            ('AuditRegimeLog', AuditRegimeLog.objects),
            ('AuditSimulationRun', AuditSimulationRun.objects),
            ('AuditTaxLog', AuditTaxLog.objects),
            ('AuditRetentionLog', AuditRetentionLog.objects),
        ]

        for table_name, queryset in tables:
            # Count total records
            total_count = queryset.count()

            # Find records expiring within warning period
            # A record expires calc_date + 2557 days
            expiring_soon = queryset.filter(
                calc_date__lte=warning_cutoff,
                calc_date__gt=today,
            )

            # Find records already expired
            expired = queryset.filter(calc_date__lt=today)

            warnings = expiring_soon.count()
            expired_count = expired.count()

            if warnings or expired_count:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {table_name}: {total_count} total, "
                        f"{warnings} expiring within {warning_days} days, "
                        f"{expired_count} expired"
                    )
                )
                total_warnings += warnings
                total_expired += expired_count

                # Show details for expiring records
                if warnings > 0 and warnings <= 20:
                    for record in expiring_soon.select_related()[:20]:
                        expiry_date = record.calc_date + timedelta(days=2557)
                        days_left = (expiry_date - today).days
                        self.stdout.write(
                            f"    - {table_name} #{record.pk}: "
                            f"calc_date={record.calc_date}, "
                            f"expires={expiry_date} ({days_left} days left)"
                        )
            else:
                self.stdout.write(
                    f"  {table_name}: {total_count} total, no warnings"
                )

        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(f"SUMMARY: {total_warnings} records expiring soon, "
                          f"{total_expired} already expired")
        self.stdout.write("NOTE: This command NEVER deletes data. "
                          "Manual review required before archival.")

        if total_expired > 0:
            self.stdout.write(
                self.style.ERROR(
                    f"WARNING: {total_expired} records have exceeded the "
                    f"7-year retention period and require manual review."
                )
            )
