"""Management command to generate monthly statements from transactions.

Usage:
    python manage.py generate_monthly_statements --account=TEST001
    python manage.py generate_monthly_statements --account=TEST001 --from=2020-01-01 --to=2024-12-31
    python manage.py generate_monthly_statements --all
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Account
from core.utils.statements import generate_monthly_statements


class Command(BaseCommand):
    help = "Generate monthly statements from transaction data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--account",
            type=str,
            help="Generate for specific account ID",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Generate for all accounts",
        )
        parser.add_argument(
            "--from",
            dest="from_date",
            type=str,
            help="Start date (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--to",
            dest="to_date",
            type=str,
            help="End date (YYYY-MM-DD)",
        )

    def handle(self, *args, **options):
        from datetime import date

        account_id = options["account"]
        all_accounts = options["all"]
        from_date = None
        to_date = None

        if options["from_date"]:
            from_date = date.fromisoformat(options["from_date"])
        if options["to_date"]:
            to_date = date.fromisoformat(options["to_date"])

        if account_id:
            accounts = Account.objects.filter(name=account_id)
            if not accounts.exists():
                raise CommandError(f"Account {account_id} not found")
        elif all_accounts:
            accounts = Account.objects.all()
            self.stdout.write(f"Generating for {accounts.count()} accounts...")
        else:
            raise CommandError("Specify --account or --all")

        for account in accounts:
            self.stdout.write(f"Generating monthly statements for {account}...")
            try:
                stmts = generate_monthly_statements(
                    account, from_date=from_date, to_date=to_date
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Generated/updated {len(stmts)} monthly statements"
                    )
                )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Error: {type(e).__name__}: {e}"))

        self.stdout.write(self.style.SUCCESS("Done!"))
