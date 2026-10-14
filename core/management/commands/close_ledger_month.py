"""Close a ledger month and lock PositionHistory for that period.

When a month is closed:
1. Creates LedgerMonthClosure record with period_end = last day of month
2. Locks all PositionHistory records with date_from <= period_end
   (sets date_to = period_end for any still-open positions)
3. Prevents future imports from creating data in closed months

Usage:
    python manage.py close_ledger_month --account <ID> --month YYYY-MM
    python manage.py close_ledger_month --all --month YYYY-MM
    python manage.py close_ledger_month --all
"""

import logging
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    Account,
    LedgerMonthClosure,
    PositionHistory,
    MonthlyLedgerBalance,
)

logger = logging.getLogger(__name__)


def get_last_day_of_month(year, month):
    """Get the last day of a given month/year."""
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


class Command(BaseCommand):
    help = "Close a ledger month and lock PositionHistory for that period."

    def add_arguments(self, parser):
        parser.add_argument(
            '--account',
            type=str,
            help='Account ID (e.g., 90000003). Use --all for all accounts.',
        )
        parser.add_argument(
            '--month',
            type=str,
            help='Month to close in YYYY-MM format (e.g., 2026-08).',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Close all unclosed months for the specified account(s).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be closed without writing.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        account_id = options['account']
        month_str = options['month']
        all_months = options['all']

        if all_months:
            # Close all unclosed months
            if not month_str:
                raise CommandError("--month is required with --all")

            # Parse month
            try:
                year, month = map(int, month_str.split('-'))
            except (ValueError, AttributeError):
                raise CommandError("--month must be in YYYY-MM format")

            period_end = get_last_day_of_month(year, month)

            # Get accounts
            if account_id:
                try:
                    accounts = [Account.objects.get(pk=int(account_id))]
                except (Account.DoesNotExist, ValueError):
                    raise CommandError(f"Account {account_id} not found")
            else:
                accounts = list(Account.objects.filter(broker='RBC').order_by('id'))

            self.stdout.write(f"Closing month {period_end} for {len(accounts)} accounts...\n")

            for acc in accounts:
                result = self._close_month(acc, period_end, dry_run)
                if result:
                    self.stdout.write(result)

        elif account_id and month_str:
            # Close specific account, specific month
            try:
                year, month = map(int, month_str.split('-'))
            except (ValueError, AttributeError):
                raise CommandError("--month must be in YYYY-MM format")

            try:
                acc = Account.objects.get(pk=int(account_id))
            except (Account.DoesNotExist, ValueError):
                raise CommandError(f"Account {account_id} not found")

            period_end = get_last_day_of_month(year, month)
            result = self._close_month(acc, period_end, dry_run)
            if result:
                self.stdout.write(result)
        else:
            raise CommandError("Provide --account and --month, or --all --month")

    def _close_month(self, account, period_end, dry_run):
        """Close a single account for a single month."""
        period_start = period_end - timedelta(days=31)
        # Adjust to first of month
        period_start = period_end.replace(day=1)

        # Check if already closed
        existing = LedgerMonthClosure.objects.filter(
            account=account,
            statement_month=period_start,
        ).first()

        if existing:
            if existing.is_closed:
                return f"  A{account.id} ({account.name}) {period_start.strftime('%Y-%m')}: already closed"
            else:
                return f"  A{account.id} ({account.name}) {period_start.strftime('%Y-%m')}: open but exists (not auto-closing)"

        # Count existing data
        pos_count = PositionHistory.objects.filter(
            account=account,
            date_from__lte=period_end,
        ).count()
        balance_count = MonthlyLedgerBalance.objects.filter(
            ledger_account__account=account,
            statement_month=period_start,
        ).count()

        if pos_count == 0 and balance_count == 0:
            self.stdout.write(
                self.style.WARNING(
                    f"  A{account.id} ({account.name}) {period_start.strftime('%Y-%m')}: "
                    f"no data to close"
                )
            )
            return None

        if dry_run:
            self.stdout.write(
                f"  [DRY RUN] A{account.id} ({account.name}) {period_start.strftime('%Y-%m')}: "
                f"would close {pos_count} position records, create closure record"
            )
            return None

        # Close open PositionHistory records for this period
        # Any position with date_from <= period_end and date_to IS NULL
        # should have date_to = period_end
        open_positions = PositionHistory.objects.filter(
            account=account,
            date_from__lte=period_end,
            date_to__isnull=True,
        )
        closed_count = 0
        for pos in open_positions:
            # Only close if this position started on or before the period
            # and doesn't have a later snapshot already closing it
            later_snapshot = PositionHistory.objects.filter(
                account=pos.account,
                symbol=pos.symbol,
                currency=pos.currency,
                date_from__gt=pos.date_from,
                date_from__lte=period_end,
            ).exists()
            if not later_snapshot:
                # Check if closing this would be correct (date_to should be period_end)
                pos.date_to = period_end
                pos.save(update_fields=['date_to'])
                closed_count += 1

        # Create closure record
        LedgerMonthClosure.objects.create(
            statement_month=period_start,
            account=account,
            currency='MIX',
            is_closed=True,
            closed_at=datetime.now(),
            closed_by="close_ledger_month",
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"  A{account.id} ({account.name}) {period_start.strftime('%Y-%m')}: "
                f"closed ({pos_count} positions, {closed_count} locked, {balance_count} balances)"
            )
        )
        return f"A{account.id} closed for {period_start.strftime('%Y-%m')}"
