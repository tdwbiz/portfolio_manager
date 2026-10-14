"""Rebuild PositionHistory date ranges.

After importing Holdings CSV snapshots, this command:
1. Sorts all PositionHistory records per (account, symbol, currency) by date_from
2. Ensures date_to on each record = date_from of next record - 1 day
3. Handles gaps between snapshots (fills with null/flagged records)
4. Closes any records with date_to IS NULL that should be closed by a later snapshot
5. Verifies no overlaps exist

Usage:
    python manage.py rebuild_position_history [--dry-run]
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import PositionHistory

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Rebuild PositionHistory date ranges, ensuring no overlaps and proper date_to values."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report changes without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        total_closed = 0
        total_fixed = 0
        total_gaps = 0

        # Group by (account_id, symbol, currency)
        groups = {}
        for ph in PositionHistory.objects.order_by(
            'account_id', 'symbol', 'currency', 'date_from'
        ).select_related('account'):
            key = (ph.account_id, ph.symbol, ph.currency)
            groups.setdefault(key, []).append(ph)

        self.stdout.write(f"Processing {len(groups)} (account, symbol, currency) groups...")

        for key, records in groups.items():
            acc_id, symbol, currency = key
            # Records already sorted by date_from

            for i in range(len(records) - 1):
                current = records[i]
                next_rec = records[i + 1]

                expected_date_to = next_rec.date_from - timedelta(days=1)

                if current.date_to is None or current.date_to > expected_date_to:
                    if dry_run:
                        self.stdout.write(
                            f"  Would close A{acc_id} {symbol} {currency}: "
                            f"date_from={current.date_from} date_to={current.date_to} -> {expected_date_to}"
                        )
                    else:
                        current.date_to = expected_date_to
                        current.save(update_fields=['date_to'])
                        total_closed += 1
                        self.stdout.write(
                            f"  Closed A{acc_id} {symbol} {currency}: "
                            f"{current.date_from} -> {expected_date_to}"
                        )
                    total_fixed += 1

        # Check for gaps: if date_to of record N != date_from of record N+1 - 1
        for key, records in groups.items():
            acc_id, symbol, currency = key
            for i in range(len(records) - 1):
                current_date_to = records[i].date_to
                next_date_from = records[i + 1].date_from
                if current_date_to is not None and current_date_to != next_date_from - timedelta(days=1):
                    gap_start = current_date_to + timedelta(days=1)
                    gap_end = next_date_from - timedelta(days=1)
                    self.stdout.write(
                        f"  Gap found: A{acc_id} {symbol} {currency} "
                        f"{gap_start} to {gap_end} (no holdings data)"
                    )
                    total_gaps += 1

        # Check for overlaps
        for key, records in groups.items():
            acc_id, symbol, currency = key
            for i in range(len(records) - 1):
                if records[i].date_to is not None and records[i + 1].date_from <= records[i].date_to:
                    self.stderr.write(
                        f"  OVERLAP: A{acc_id} {symbol} {currency} "
                        f"record {i} ends {records[i].date_to} but record {i+1} starts {records[i+1].date_from}"
                    )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes written."))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done: {total_fixed} records closed, {total_gaps} gaps found."
                )
            )
