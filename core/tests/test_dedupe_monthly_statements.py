"""Tests for the dedupe_monthly_statements management command (phase 3, step 13a).

Uses fake accounts 90000001+ with fingerprints from the fixed test key —
no .env, no mercury connection.
"""

from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.models import (
    Account,
    RbcMonthly,
    RbcMonthlyBalance,
    RbcMonthlyHolding,
    RbcMonthlyTransaction,
)
from core.utils.account_fingerprint import fingerprint_account_number


class DedupeMonthlyStatementsTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            name="ACCOUNT_A",
            broker="RBC",
            category="TFSA",
            fingerprint=fingerprint_account_number("90000001"),
        )

    # -- helpers ----------------------------------------------------------

    def make_statement(self, file_name, begin=Decimal("100.00"), end=Decimal("200.00")):
        return RbcMonthly.objects.create(
            account=self.account,
            file_name=file_name,
            statement_date=date(2025, 3, 31),
            currency="CAD",
            begin_total_market_value=begin,
            end_total_market_value=end,
        )

    def make_children(self, stmt, n_holdings=2, n_tx=1, n_bal=1):
        for i in range(n_holdings):
            RbcMonthlyHolding.objects.create(
                rbc_monthly=stmt,
                currency="CAD",
                security=f"Fund {i}",
                symbol=f"SYM{i}",
                quantity=Decimal("10.0000"),
                book_cost=Decimal("50.00"),
                market_price=Decimal("10.0000"),
                market_value=Decimal("100.00"),
            )
        for i in range(n_tx):
            RbcMonthlyTransaction.objects.create(
                rbc_monthly=stmt,
                date=date(2025, 3, 15),
                activity_type="DIVIDEND",
                description=f"div {i}",
            )
        for i in range(n_bal):
            RbcMonthlyBalance.objects.create(
                rbc_monthly=stmt,
                currency="CAD",
                account_type=f"Type {i}",
                market_value=Decimal("10.00"),
            )

    def run_command(self, *args):
        out = StringIO()
        call_command("dedupe_monthly_statements", *args, stdout=out)
        return out.getvalue()

    def make_identical_pair(self):
        low = self.make_statement("dup_a.pdf")
        high = self.make_statement("dup_b.pdf")
        self.make_children(low)
        self.make_children(high)
        return low, high

    # -- report only ------------------------------------------------------

    def test_report_only_lists_pair_and_deletes_nothing(self):
        low, high = self.make_identical_pair()
        text = self.run_command()
        self.assertIn(f"ids {low.id}|{high.id} DELETE", text)
        self.assertIn("pairs found: 1  would delete: 1  kept: 0  not-a-pair: 0", text)
        self.assertEqual(RbcMonthly.objects.count(), 2)
        self.assertEqual(low.holdings.count(), 2)

    # -- apply ------------------------------------------------------------

    def test_apply_deletes_higher_id_and_children(self):
        low, high = self.make_identical_pair()
        text = self.run_command("--apply")
        self.assertIn(f"ids {low.id}|{high.id} DELETE", text)
        self.assertIn(
            "deleted: rbcmonthly 1  holdings 2  transactions 1  balances 1", text
        )
        # higher-id row and its children gone
        self.assertFalse(RbcMonthly.objects.filter(pk=high.pk).exists())
        self.assertEqual(RbcMonthlyHolding.objects.count(), 2)
        self.assertEqual(RbcMonthlyTransaction.objects.count(), 1)
        self.assertEqual(RbcMonthlyBalance.objects.count(), 1)
        # kept row and its children intact
        self.assertTrue(RbcMonthly.objects.filter(pk=low.pk).exists())
        self.assertEqual(low.holdings.count(), 2)
        self.assertEqual(low.transactions.count(), 1)
        self.assertEqual(low.balances.count(), 1)

    def test_apply_is_idempotent(self):
        low, high = self.make_identical_pair()
        self.run_command("--apply")
        counts_after_first = {
            "rbc": RbcMonthly.objects.count(),
            "holdings": RbcMonthlyHolding.objects.count(),
            "tx": RbcMonthlyTransaction.objects.count(),
            "bal": RbcMonthlyBalance.objects.count(),
        }
        second = self.run_command("--apply")
        self.assertIn("pairs found: 0  would delete: 0  kept: 0  not-a-pair: 0", second)
        self.assertIn(
            "deleted: rbcmonthly 0  holdings 0  transactions 0  balances 0", second
        )
        self.assertEqual(
            {
                "rbc": RbcMonthly.objects.count(),
                "holdings": RbcMonthlyHolding.objects.count(),
                "tx": RbcMonthlyTransaction.objects.count(),
                "bal": RbcMonthlyBalance.objects.count(),
            },
            counts_after_first,
        )

    # -- kept pairs -------------------------------------------------------

    def assert_kept(self, low, high, reason):
        text = self.run_command("--apply")
        self.assertIn(f"ids {low.id}|{high.id} KEEP {reason}", text)
        self.assertEqual(RbcMonthly.objects.count(), 2)
        self.assertTrue(RbcMonthly.objects.filter(pk=high.pk).exists())

    def test_begin_differs_kept(self):
        low = self.make_statement("dup_a.pdf", begin=Decimal("100.00"))
        high = self.make_statement("dup_b.pdf", begin=Decimal("101.00"))
        self.make_children(low)
        self.make_children(high)
        self.assert_kept(low, high, "begin_differs")

    def test_end_differs_kept(self):
        low = self.make_statement("dup_a.pdf", end=Decimal("200.00"))
        high = self.make_statement("dup_b.pdf", end=Decimal("201.00"))
        self.make_children(low)
        self.make_children(high)
        self.assert_kept(low, high, "end_differs")

    def test_holdings_differ_kept(self):
        low = self.make_statement("dup_a.pdf")
        high = self.make_statement("dup_b.pdf")
        self.make_children(low)
        self.make_children(high)
        # same count, different content
        holding = high.holdings.first()
        holding.symbol = "OTHER"
        holding.save()
        self.assert_kept(low, high, "holdings_differ")

    def test_tx_count_differs_kept(self):
        low = self.make_statement("dup_a.pdf")
        high = self.make_statement("dup_b.pdf")
        self.make_children(low, n_tx=1)
        self.make_children(high, n_tx=2)
        self.assert_kept(low, high, "tx_count_differs")

    def test_bal_count_differs_kept(self):
        low = self.make_statement("dup_a.pdf")
        high = self.make_statement("dup_b.pdf")
        self.make_children(low, n_bal=1)
        self.make_children(high, n_bal=2)
        self.assert_kept(low, high, "bal_count_differs")

    # -- not a pair -------------------------------------------------------

    def test_three_row_group_not_a_pair(self):
        rows = [
            self.make_statement(f"dup_{letter}.pdf")
            for letter in ("a", "b", "c")
        ]
        for row in rows:
            self.make_children(row)
        text = self.run_command("--apply")
        ids = "|".join(str(row.id) for row in rows)
        self.assertIn(f"ids {ids} NOT-A-PAIR", text)
        self.assertIn("pairs found: 0  would delete: 0  kept: 0  not-a-pair: 1", text)
        self.assertEqual(RbcMonthly.objects.count(), 3)

    # -- output hygiene ---------------------------------------------------

    def test_output_has_no_file_names_or_account_digits(self):
        low, high = self.make_identical_pair()
        for args in ((), ("--apply",)):
            text = self.run_command(*args)
            self.assertNotIn("dup_a.pdf", text)
            self.assertNotIn("dup_b.pdf", text)
            self.assertNotIn("90000001", text)
