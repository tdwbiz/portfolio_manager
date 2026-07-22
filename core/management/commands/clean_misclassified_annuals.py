"""Cleanup command to remove RbcMonthly records that were incorrectly created
from annual report PDFs.

Annual reports should create RbcAnnual records, not RbcMonthly records.
When the VL classifier misclassifies an annual report as a monthly statement,
it creates bogus RbcMonthly + RbcMonthlyTransaction records.

This command finds and deletes those records.

Usage:
    python manage.py clean_misclassified_annuals [--dry-run]
    python manage.py clean_misclassified_annuals --file-pattern "Annual Report"
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Delete RbcMonthly records that were misclassified from annual report PDFs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting.',
        )
        parser.add_argument(
            '--file-pattern',
            type=str,
            default='annual report',
            help='Filename pattern to match (case-insensitive). Default: "annual report".',
        )

    def handle(self, *args, **options):
        from core.models import RbcMonthly, RbcMonthlyTransaction, RbcMonthlyBalance, RbcMonthlyHolding

        dry_run = options['dry_run']
        pattern = options['file_pattern'].lower()

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no records will be deleted'))

        # Find RbcMonthly records whose filename matches the annual report pattern
        from django.db.models import Q

        # Build case-insensitive filter
        filter_kwargs = {}
        for part in pattern.split():
            filter_kwargs[f'file_name__icontains'] = part

        # Use the first part for icontains (Django doesn't support multiple icontains cleanly)
        q = Q()
        for part in pattern.split():
            q &= Q(file_name__icontains=part)

        bad_records = RbcMonthly.objects.filter(q)

        if not bad_records.exists():
            self.stdout.write(self.style.SUCCESS(f'No misclassified records found matching pattern "{pattern}"'))
            return

        self.stdout.write(f'Found {bad_records.count()} suspicious RbcMonthly record(s):')

        total_tx_deleted = 0
        total_bal_deleted = 0
        total_hold_deleted = 0
        total_stmt_deleted = 0

        for rm in bad_records:
            tx_count = RbcMonthlyTransaction.objects.filter(rbc_monthly=rm).count()
            bal_count = RbcMonthlyBalance.objects.filter(rbc_monthly=rm).count()
            hold_count = RbcMonthlyHolding.objects.filter(rbc_monthly=rm).count()

            self.stdout.write(
                f'  RbcMonthly id={rm.id} | {rm.file_name} | '
                f'account={rm.account_id} | date={rm.statement_date} | '
                f'txns={tx_count} | balances={bal_count} | holdings={hold_count}'
            )

            if not dry_run:
                # Delete child records first
                RbcMonthlyTransaction.objects.filter(rbc_monthly=rm).delete()
                RbcMonthlyBalance.objects.filter(rbc_monthly=rm).delete()
                RbcMonthlyHolding.objects.filter(rbc_monthly=rm).delete()
                # Then the parent
                rm.delete()

                total_tx_deleted += tx_count
                total_bal_deleted += bal_count
                total_hold_deleted += hold_count
                total_stmt_deleted += 1

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'\nWould delete {bad_records.count()} RbcMonthly record(s) and their children'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nDeleted {total_stmt_deleted} RbcMonthly, '
                    f'{total_tx_deleted} transactions, '
                    f'{total_bal_deleted} balances, '
                    f'{total_hold_deleted} holdings'
                )
            )
