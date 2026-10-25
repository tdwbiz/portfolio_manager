"""Management command to generate annual reports from monthly statements.

Usage:
    python manage.py generate_annual_reports --account=TEST001
    python manage.py generate_annual_reports --account=TEST001 --from=2020 --to=2024
    python manage.py generate_annual_reports --all
"""

from django.core.management.base import BaseCommand, CommandError

from core.models import Account
from core.utils.statements import generate_annual_reports


class Command(BaseCommand):
    help = "Generate annual reports from monthly statement data"

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
            dest="from_year",
            type=int,
            help="Start year",
        )
        parser.add_argument(
            "--to",
            dest="to_year",
            type=int,
            help="End year",
        )

    def handle(self, *args, **options):
        account_id = options["account"]
        all_accounts = options["all"]
        from_year = options["from_year"]
        to_year = options["to_year"]

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
            self.stdout.write(f"Generating annual reports for {account}...")
            try:
                reports = generate_annual_reports(
                    account, from_year=from_year, to_year=to_year
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Generated/updated {len(reports)} annual reports"
                    )
                )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Error: {type(e).__name__}: {e}"))

        self.stdout.write(self.style.SUCCESS("Done!"))
