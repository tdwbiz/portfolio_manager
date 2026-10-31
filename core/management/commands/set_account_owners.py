"""Set the ownership shares for one account.

Usage:
    manage.py set_account_owners --account NAME --share Tom:60 --share Susan:40 [--apply]

Dry run by default: prints what would be stored and writes nothing. With
--apply, replaces the account's whole AccountOwner set with the given
shares (creating missing Owner rows, first names only). Refuses if the
account is unknown, a share value is malformed, the same owner appears
twice, or the shares do not add up to exactly 100. Output shows the
account name and owner names/percentages only — never account digits.
"""

from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Account, AccountOwner, Owner


class Command(BaseCommand):
    help = "Set the ownership shares for one account (dry run unless --apply)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--account",
            required=True,
            help="exact account name",
        )
        parser.add_argument(
            "--share",
            action="append",
            default=[],
            required=True,
            metavar="NAME:PERCENT",
            help="owner first name and percentage (0-100), repeatable",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="write the changes (default is a dry run)",
        )

    def handle(self, *args, **options):
        account = Account.objects.filter(name=options["account"]).first()
        if account is None:
            raise CommandError(
                f"No account named {options['account']!r}; nothing done."
            )

        shares = []
        seen = set()
        for raw in options["share"]:
            name, sep, pct = raw.partition(":")
            if not sep or not name.strip():
                raise CommandError(
                    f"Malformed --share {raw!r}: expected NAME:PERCENT."
                )
            owner_name = name.strip()
            try:
                percent = Decimal(pct.strip())
            except InvalidOperation:
                raise CommandError(
                    f"Malformed --share {raw!r}: percentage is not a number."
                )
            if percent < 0 or percent > 100:
                raise CommandError(
                    f"--share {raw!r}: percentage must be between 0 and 100."
                )
            if owner_name in seen:
                raise CommandError(
                    f"Owner {owner_name!r} appears more than once; nothing done."
                )
            seen.add(owner_name)
            shares.append((owner_name, percent))

        total = sum(percent for _, percent in shares)
        if total != Decimal("100"):
            raise CommandError(
                f"Shares must add up to exactly 100; total is {total}. Nothing done."
            )

        listing = ", ".join(f"{name} {percent}" for name, percent in shares)
        if not options["apply"]:
            self.stdout.write(
                f"Dry run: would set owners for {account.name}: {listing}"
            )
            return

        with transaction.atomic():
            AccountOwner.objects.filter(account=account).delete()
            rows = []
            for owner_name, percent in shares:
                owner, _ = Owner.objects.get_or_create(name=owner_name)
                rows.append(
                    AccountOwner(
                        account=account, owner=owner, share_percent=percent
                    )
                )
            AccountOwner.objects.bulk_create(rows)

        self.stdout.write(
            self.style.SUCCESS(f"Set owners for {account.name}: {listing}")
        )
