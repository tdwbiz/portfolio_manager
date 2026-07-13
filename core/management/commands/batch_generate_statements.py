"""Management command: batch-generate all monthly and annual statements from RBC anchor.

Usage:
    python manage.py batch_generate_statements                    # All accounts
    python manage.py batch_generate_statements --account 12345678  # Single account
    python manage.py batch_generate_statements --dry-run           # Preview only
"""

import logging
from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import Account
from core.utils.statements import batch_generate_all_statements

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Batch-generate monthly and annual statements anchored from RBC data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--account",
            type=str,
            default=None,
            help="Generate statements for a single account (by account_id).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes.",
        )

    def handle(self, *args, **options):
        account_id = options["account"]
        dry_run = options["dry_run"]

        if account_id:
            accounts = Account.objects.filter(account_id=account_id)
            if not accounts.exists():
                self.stderr.write(self.style.ERROR(f"Account '{account_id}' not found."))
                return
        else:
            accounts = Account.objects.all()

        total_monthly = 0
        total_annual = 0

        for account in accounts:
            self.stdout.write(f"\nProcessing: {account} ({account.account_id})")

            # Check for existing RBC data
            rbc_count = account.rbc_monthly_statements.count()
            if rbc_count == 0:
                self.stdout.write(
                    f"  SKIP: No RBC data found for {account}. Skipping."
                )
                continue

            # Get latest RBC end balances (preview)
            from core.models import RbcMonthly

            currencies = (
                RbcMonthly.objects.filter(account=account)
                .values_list("currency", flat=True)
                .distinct()
            )
            balances = {}
            latest_date = None
            for cur in currencies:
                latest = (
                    RbcMonthly.objects.filter(account=account, currency=cur)
                    .order_by("-statement_date")
                    .first()
                )
                if latest and latest.end_total_market_value:
                    balances[cur] = latest.end_total_market_value
                    if not latest_date or latest.statement_date > latest_date:
                        latest_date = latest.statement_date

            if not balances:
                self.stdout.write(
                    f"  SKIP: No valid RBC ending balances for {account}."
                )
                continue

            self.stdout.write(
                f"  RBC anchor: {latest_date} | Balances: {balances}"
            )

            if dry_run:
                self.stdout.write(
                    f"  DRY-RUN: Would generate monthly + annual statements for {account}."
                )
                continue

            try:
                monthly_stmts, annual_reports = batch_generate_all_statements(account)
                total_monthly += len(monthly_stmts)
                total_annual += len(annual_reports)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  OK: {len(monthly_stmts)} monthly + {len(annual_reports)} annual statements."
                    )
                )
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"  ERROR: {account}: {e}")
                )
                logger.exception(f"Batch generation failed for {account}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Total: {total_monthly} monthly statements, {total_annual} annual reports."
            )
        )
