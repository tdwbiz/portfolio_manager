"""Tests for the scrub_account_numbers management command."""

from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TestCase

from core.models import (
    Account,
    BrokerStatement,
    FileImportLog,
    RbcAnnual,
    RbcMonthly,
    RbcMonthlyTransaction,
    Transaction,
)
from core.utils.account_fingerprint import fingerprint_account_number


class ScrubAccountNumbersTests(TestCase):
    def setUp(self) -> None:
        self.account = Account.objects.create(
            name="ACCOUNT_A",
            broker="RBC",
            fingerprint=fingerprint_account_number("90000001"),
            last_4="0001")
        self.statement = BrokerStatement.objects.create(
            source="RBC",
            file_date=date(2024, 1, 15),
            raw_filename="rbc_90000001_jan.csv",
        )
        self.tx = Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=date(2024, 1, 15),
            symbol="GOOGL",
            type="BUY",
            quantity=Decimal("5"),
            price=Decimal("2800.00"),
            commission=Decimal("0.00"),
            amount=Decimal("-13990.05"),
            currency="USD",
            amount_cad=Decimal("-18886.57"),
            notes="Buy GOOGL on 90000001, settled 2026-10-21",
        )
        self.log = FileImportLog.objects.create(
            filename="rbc_90000001_jan.csv",
            broker="RBC",
            status="error",
            error_message="failed to read rbc 90000001 statement",
        )
        self.monthly = RbcMonthly.objects.create(
            account=self.account,
            file_name="rbc_90000001_2024-01.pdf",
            statement_date=date(2024, 1, 31),
            currency="CAD",
        )
        self.rmt = RbcMonthlyTransaction.objects.create(
            rbc_monthly=self.monthly,
            date=date(2024, 1, 15),
            activity_type="BOUGHT",
            description="BUY GOOGL ACCT 90000001",
            symbol="GOOGL",
        )
        self.annual = RbcAnnual.objects.create(
            account=self.account,
            file_name="rbc_90000001_2023.pdf",
            report_year=2023,
            report_period_start=date(2023, 1, 1),
            report_period_end=date(2023, 12, 31),
            currency="CAD",
        )

    def _run(self, *args) -> str:
        out = StringIO()
        call_command("scrub_account_numbers", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_counts_and_writes_nothing(self) -> None:
        output = self._run()

        # Counts only — no value from the database is printed.
        self.assertIn(
            "core_transaction,notes,matched=1,replaced=1,unmatched=1,bank=0",
            output,
        )
        self.assertIn("DRY-RUN", output)
        for needle in ("90000001", "BUY GOOGL ACCT"):
            self.assertNotIn(needle, output)

        # Nothing was written.
        self.assertEqual(Transaction.objects.get(pk=self.tx.pk).notes, "Buy GOOGL on 90000001, settled 2026-10-21")
        self.assertEqual(
            FileImportLog.objects.get(pk=self.log.pk).error_message,
            "failed to read rbc 90000001 statement",
        )
        self.assertEqual(
            RbcMonthly.objects.get(pk=self.monthly.pk).file_name, "rbc_90000001_2024-01.pdf"
        )

    def test_apply_replaces_numbers_with_account_name(self) -> None:
        output = self._run("--apply")

        self.assertIn("APPLY", output)
        self.assertEqual(
            Transaction.objects.get(pk=self.tx.pk).notes, "Buy GOOGL on ACCOUNT_A, settled 2026-10-21"
        )
        self.assertEqual(
            FileImportLog.objects.get(pk=self.log.pk).error_message,
            "failed to read rbc ACCOUNT_A statement",
        )
        self.assertEqual(BrokerStatement.objects.get(pk=self.statement.pk).raw_filename, "rbc_ACCOUNT_A_jan.csv")
        self.assertEqual(
            RbcMonthly.objects.get(pk=self.monthly.pk).file_name, "rbc_ACCOUNT_A_2024-01.pdf"
        )
        self.assertEqual(
            RbcMonthlyTransaction.objects.get(pk=self.rmt.pk).description, "BUY GOOGL ACCT ACCOUNT_A"
        )
        self.assertEqual(RbcAnnual.objects.get(pk=self.annual.pk).file_name, "rbc_ACCOUNT_A_2023.pdf")
        # The look-alike date was not touched.
        self.assertIn("2026-10-21", self.tx.notes)

    def test_apply_is_idempotent(self) -> None:
        self._run("--apply")
        output = self._run("--apply")

        self.assertEqual(
            Transaction.objects.get(pk=self.tx.pk).notes, "Buy GOOGL on ACCOUNT_A, settled 2026-10-21"
        )
        # Second pass finds no matches (the name is not a number candidate);
        # the look-alike date is still counted as unmatched.
        self.assertIn(
            "core_transaction,notes,matched=0,replaced=0,unmatched=1,bank=0",
            output,
        )

    def test_bank_account_run_reported_and_rewritten(self) -> None:
        bank_tx = Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=date(2024, 1, 15),
            symbol="CASH",
            type="TRANSFER",
            quantity=Decimal("0"),
            price=Decimal("0.00"),
            commission=Decimal("0.00"),
            amount=Decimal("-500.00"),
            currency="CAD",
            amount_cad=Decimal("-500.00"),
            notes="transfer to 123456789012345 done",
        )

        # Dry run: the 15-digit run is counted as bank (not unmatched) and
        # nothing is written; the value is never printed.
        output = self._run()
        self.assertIn(
            "core_transaction,notes,matched=1,replaced=2,unmatched=1,bank=1",
            output,
        )
        self.assertNotIn("123456789012345", output)
        self.assertEqual(
            Transaction.objects.get(pk=bank_tx.pk).notes,
            "transfer to 123456789012345 done",
        )

        # Apply: the run is rewritten with the fixed label.
        self._run("--apply")
        self.assertEqual(
            Transaction.objects.get(pk=bank_tx.pk).notes,
            "transfer to BANK_ACCOUNT done",
        )

    def _make_txs(self, count: int) -> None:
        for i in range(count):
            Transaction.objects.create(
                statement=self.statement,
                account=self.account,
                date=date(2024, 1, 15),
                symbol="GOOGL",
                type="BUY",
                quantity=Decimal("1"),
                price=Decimal("100.00"),
                commission=Decimal("0.00"),
                amount=Decimal("-100.00"),
                currency="USD",
                amount_cad=Decimal("-135.00"),
                notes=f"Buy {i} on 90000001",
            )

    def _run_and_count_queries(self) -> int:
        """Run the command (dry run) and return how many queries it issued."""
        out = StringIO()
        connection.force_debug_cursor = True
        try:
            start = len(connection.queries)
            call_command("scrub_account_numbers", stdout=out)
            return len(connection.queries) - start
        finally:
            connection.force_debug_cursor = False

    def test_query_count_flat_across_row_counts(self) -> None:
        # 3 rows on one target field (setUp's tx + 2 more).
        self._make_txs(2)
        count_3 = self._run_and_count_queries()
        self.assertGreater(count_3, 0)

        # Bring the total to 10 rows — still well under one chunk of 200,
        # so the command must issue exactly the same number of queries.
        self._make_txs(7)
        count_10 = self._run_and_count_queries()

        self.assertEqual(count_3, count_10)
