"""Tests for the set_account_fingerprint one-off command."""

from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.models import Account
from core.utils.account_fingerprint import fingerprint_account_number


def run_command(number1="900-00003-1-8", number2=None, name="TOM_TFSA"):
    """Run the command with patched prompts; returns captured stdout."""
    if number2 is None:
        number2 = number1
    buf = []
    with mock.patch("builtins.input", return_value=name), mock.patch(
        "core.management.commands.set_account_fingerprint.getpass.getpass",
        side_effect=[number1, number2],
    ) as gp:
        call_command("set_account_fingerprint", stdout=mock.Mock(write=lambda s: buf.append(s)))
    return buf, gp


class SetAccountFingerprintTests(TestCase):
    def test_stores_fingerprint_and_last4(self):
        Account.objects.create(account_id="TOM_TFSA", name="TOM_TFSA", broker="RBC")
        out, _ = run_command()
        account = Account.objects.get(name="TOM_TFSA")
        self.assertEqual(account.fingerprint, fingerprint_account_number("900-00003-1-8"))
        self.assertEqual(account.last_4, "0003")
        self.assertEqual(account.broker, "RBC")
        # The number itself is never stored or printed.
        joined = "".join(str(s) for s in out)
        self.assertNotIn("900", joined)
        self.assertIn("TOM_TFSA", joined)
        self.assertIn("0003", joined)

    def test_updates_existing_account_by_name(self):
        account = Account.objects.create(account_id="EXISTING", name="SUSAN IB", broker="RBC")
        run_command(name="SUSAN IB")
        account.refresh_from_db()
        self.assertEqual(account.fingerprint, fingerprint_account_number("900-00003-1-8"))
        self.assertEqual(account.last_4, "0003")

    def test_unknown_name_rejected_no_row_created(self):
        # Empty Account table: the command must error before asking for the number.
        with mock.patch("builtins.input", return_value="TOM_TFSA"), mock.patch(
            "core.management.commands.set_account_fingerprint.getpass.getpass"
        ) as gp:
            with self.assertRaises(CommandError) as ctx:
                call_command("set_account_fingerprint")
        gp.assert_not_called()
        self.assertEqual(Account.objects.count(), 0)
        # Error message must not list names or number digits.
        msg = str(ctx.exception)
        self.assertNotIn("TOM_TFSA", msg)
        self.assertNotIn("900", msg)

    def test_duplicate_name_rejected(self):
        a1 = Account.objects.create(account_id="DUP1", name="TOM_TFSA", broker="RBC")
        a2 = Account.objects.create(account_id="DUP2", name="TOM_TFSA", broker="RBC")
        with self.assertRaises(CommandError) as ctx:
            run_command()
        self.assertEqual(Account.objects.count(), 2)
        a1.refresh_from_db()
        a2.refresh_from_db()
        self.assertFalse(a1.fingerprint)
        self.assertFalse(a2.fingerprint)
        # Error message must not list names or number digits.
        msg = str(ctx.exception)
        self.assertNotIn("TOM_TFSA", msg)
        self.assertNotIn("DUP", msg)
        self.assertNotIn("900", msg)

    def test_equivalent_formats_accepted(self):
        Account.objects.create(account_id="TOM_TFSA", name="TOM_TFSA", broker="RBC")
        run_command(number1="900-00003-1-8", number2="9000000318")
        account = Account.objects.get(name="TOM_TFSA")
        self.assertEqual(account.fingerprint, fingerprint_account_number("90000003"))
        self.assertEqual(account.last_4, "0003")

    def test_mismatched_entries_store_nothing(self):
        account = Account.objects.create(account_id="TOM_TFSA", name="TOM_TFSA", broker="RBC")
        with self.assertRaises(CommandError):
            run_command(number1="90000003", number2="90000004")
        account.refresh_from_db()
        self.assertFalse(account.fingerprint)
        self.assertEqual(Account.objects.count(), 1)

    def test_refuses_overwrite_without_replace(self):
        account = Account.objects.create(
            account_id="TOM_TFSA",
            name="TOM_TFSA",
            broker="RBC",
            fingerprint=fingerprint_account_number("90000001"),
            last_4="0001",
        )
        with self.assertRaises(CommandError):
            run_command()
        account.refresh_from_db()
        self.assertEqual(account.last_4, "0001")

    def test_replace_overwrites(self):
        Account.objects.create(
            account_id="TOM_TFSA",
            name="TOM_TFSA",
            broker="RBC",
            fingerprint=fingerprint_account_number("90000001"),
            last_4="0001",
        )
        with mock.patch(
            "core.management.commands.set_account_fingerprint.getpass.getpass",
            side_effect=["900-00003-1-8", "900-00003-1-8"],
        ), mock.patch("builtins.input", return_value="TOM_TFSA"):
            call_command(
                "set_account_fingerprint",
                "--replace",
                stdout=mock.Mock(write=lambda s: None),
            )
        account = Account.objects.get(name="TOM_TFSA")
        self.assertEqual(account.last_4, "0003")

    def test_empty_number_stores_nothing(self):
        account = Account.objects.create(account_id="TOM_TFSA", name="TOM_TFSA", broker="RBC")
        with self.assertRaises(CommandError):
            run_command(number1="", number2="")
        account.refresh_from_db()
        self.assertFalse(account.fingerprint)
        self.assertEqual(Account.objects.count(), 1)

    @override_settings(ACCOUNT_FINGERPRINT_KEY="")
    def test_refuses_without_key(self):
        with mock.patch("builtins.input", return_value="TOM_TFSA"), mock.patch(
            "core.management.commands.set_account_fingerprint.getpass.getpass",
            side_effect=["90000003", "90000003"],
        ):
            with self.assertRaises(CommandError):
                call_command("set_account_fingerprint")
        self.assertFalse(Account.objects.exists())
