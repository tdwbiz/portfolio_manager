"""One-off command: store an account's fingerprint and last 4 digits.

The full account number is entered twice at a hidden prompt, compared in
memory, and then discarded — it is never printed, logged or stored. Only
the keyed HMAC-SHA256 fingerprint and the last 4 digits are saved on the
Account row. Refuses to run without a valid ACCOUNT_FINGERPRINT_KEY and
refuses to overwrite an existing fingerprint unless --replace is given.
"""

import getpass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import Account
from core.utils.account_fingerprint import (
    MIN_KEY_LENGTH,
    fingerprint_account_number,
    last4,
    normalize_account_number,
)


class Command(BaseCommand):
    help = "Store an account's fingerprint and last 4 (number entered hidden, never stored)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--replace",
            action="store_true",
            help="overwrite an existing fingerprint for this account",
        )

    def handle(self, *args, **options):
        key = (getattr(settings, "ACCOUNT_FINGERPRINT_KEY", "") or "").strip()
        if len(key) < MIN_KEY_LENGTH:
            raise CommandError(
                f"ACCOUNT_FINGERPRINT_KEY is not set (need at least {MIN_KEY_LENGTH} characters); refusing to run."
            )

        name = input("Account name: ").strip()
        if not name:
            raise CommandError("No account name given; nothing stored.")

        number1 = getpass.getpass("Account number (hidden): ")
        number2 = getpass.getpass("Account number again (hidden): ")
        if not normalize_account_number(number1):
            raise CommandError("No account number given; nothing stored.")
        if number1 != number2:
            raise CommandError("The two entries differ; nothing stored.")

        account = Account.objects.filter(name=name).first()
        if account is None:
            account = Account.objects.create(account_id=name, name=name, broker="RBC")

        if account.fingerprint and not options["replace"]:
            raise CommandError(
                f"{name} already has a fingerprint; re-run with --replace to overwrite it."
            )

        account.fingerprint = fingerprint_account_number(number1)
        account.last_4 = last4(number1)
        account.save(update_fields=["fingerprint", "last_4"])
        self.stdout.write(self.style.SUCCESS(f"Stored fingerprint and last 4 for {name} (…{account.last_4})"))
