"""Management command to reconcile CSV-generated data against RBC PDF data.

Usage:
    python manage.py reconcile --account=TEST001 --type=monthly
    python manage.py reconcile --account=TEST001 --type=annual
    python manage.py reconcile --all --type=monthly
    python manage.py reconcile --all --type=both
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.models import Account
from core.utils.reconciliation import reconcile_account


class Command(BaseCommand):
    help = "Reconcile CSV-generated statements against RBC PDF data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--account",
            type=str,
            help="Reconcile specific account ID",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Reconcile all accounts",
        )
        parser.add_argument(
            "--type",
            type=str,
            choices=["monthly", "annual", "both"],
            default="monthly",
            help="Type of reconciliation (default: monthly)",
        )

    def handle(self, *args, **options):
        account_id = options["account"]
        all_accounts = options["all"]
        period_type = options["type"]

        if account_id:
            accounts = Account.objects.filter(account_id=account_id)
            if not accounts.exists():
                raise CommandError(f"Account {account_id} not found")
        elif all_accounts:
            accounts = Account.objects.all()
            self.stdout.write(f"Reconciling {accounts.count()} accounts...")
        else:
            raise CommandError("Specify --account or --all")

        types_to_run = (
            ["monthly"]
            if period_type == "monthly"
            else (["annual"] if period_type == "annual" else ["monthly", "annual"])
        )

        for account in accounts:
            for ptype in types_to_run:
                self.stdout.write(f"Reconciling {account} ({ptype})...")
                try:
                    recs = reconcile_account(account, period_type=ptype)
                    matches = sum(1 for r in recs if r.matching)
                    discrepancies = sum(1 for r in recs if not r.matching)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  {len(recs)} reconciled: "
                            f"{matches} matches, {discrepancies} discrepancies"
                        )
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"  Error: {type(e).__name__}: {e}")
                    )

        self.stdout.write(self.style.SUCCESS("Done!"))
