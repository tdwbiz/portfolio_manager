"""Tests for annual statement generation and reconciliation (Phase E: E3-C, E3-D).

The monthly-statement generation tests of E3-B went with the old generators
(plan docs/plans/statements-forward.md, step 7); the one forward generator is
tested in tests/test_statements_forward.py.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    Account,
    CoreAnnualReport,
    CoreMonthlyStatement,
    CoreReconciliation,
    RbcAnnual,
    RbcMonthly,
)
from core.utils.reconciliation import (
    reconcile_account,
    reconcile_annual,
    reconcile_monthly,
)
from core.utils.statements import generate_annual_reports


class AnnualReportGenerationTests(TestCase):
    """Tests for E3-C: Annual report generation."""

    def setUp(self):
        self.account = Account.objects.create(
            name="Test Account 2",
            broker="RBC-DIRECT",
            currency="CAD")

    def test_generate_annual_from_monthly(self):
        """Annual report aggregates monthly statements for a year."""
        # Monthly rows built directly (the old monthly generator is gone —
        # plan step 7, tdw decision 7): two USD BUY months of the same
        # shape as before (CAD 1950.00 + CAD 1300.00).
        CoreMonthlyStatement.objects.create(
            account=self.account,
            statement_month=date(2024, 1, 1),
            currency="USD",
            beginning_market_value=Decimal("0.00"),
            ending_market_value=Decimal("-1950.00"),
            total_buys=Decimal("1950.00"),
            net_flow=Decimal("-1950.00"),
            total_transactions=1,
        )
        CoreMonthlyStatement.objects.create(
            account=self.account,
            statement_month=date(2024, 2, 1),
            currency="USD",
            beginning_market_value=Decimal("-1950.00"),
            ending_market_value=Decimal("-3250.00"),
            total_buys=Decimal("1300.00"),
            net_flow=Decimal("-1300.00"),
            total_transactions=1,
        )

        reports = generate_annual_reports(self.account)

        self.assertEqual(len(reports), 1)
        report = reports[0]
        self.assertEqual(report.report_year, 2024)
        self.assertEqual(report.total_buys, Decimal("3250.00"))

    def test_generate_annual_no_monthly_statements(self):
        """No annual reports if there are no monthly statements."""
        reports = generate_annual_reports(self.account)
        self.assertEqual(len(reports), 0)


class ReconciliationTests(TestCase):
    """Tests for E3-D: Reconciliation engine."""

    def setUp(self):
        self.account = Account.objects.create(
            name="Test Account 3",
            broker="RBC-DIRECT",
            currency="CAD")

    def test_reconciliation_match(self):
        """When CSV and RBC agree, matching=True."""
        CoreMonthlyStatement.objects.create(
            account=self.account,
            statement_month=date(2024, 3, 1),
            currency="CAD",
            beginning_market_value=Decimal("10000.00"),
            ending_market_value=Decimal("11000.00"),
            total_buys=Decimal("1000.00"),
            total_sells=Decimal("0.00"),
            total_dividends=Decimal("200.00"),
            total_interest=Decimal("50.00"),
            total_commission=Decimal("10.00"),
            net_flow=Decimal("1000.00"),
            total_transactions=3,
        )

        RbcMonthly.objects.create(
            account=self.account,
            file_name="test.pdf",
            statement_date=date(2024, 3, 1),
            currency="CAD",
            begin_total_market_value=Decimal("10000.00"),
            end_total_market_value=Decimal("11000.00"),
        )

        recs = reconcile_monthly(self.account)
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0].matching)
        self.assertEqual(recs[0].discrepancy_amount, Decimal("0.00"))

    def test_reconciliation_discrepancy(self):
        """When CSV and RBC differ, matching=False with discrepancy amount."""
        CoreMonthlyStatement.objects.create(
            account=self.account,
            statement_month=date(2024, 3, 1),
            currency="CAD",
            beginning_market_value=Decimal("10000.00"),
            ending_market_value=Decimal("12000.00"),
            total_buys=Decimal("1000.00"),
            total_sells=Decimal("0.00"),
            total_dividends=Decimal("200.00"),
            total_interest=Decimal("50.00"),
            total_commission=Decimal("10.00"),
            net_flow=Decimal("1000.00"),
            total_transactions=3,
        )

        RbcMonthly.objects.create(
            account=self.account,
            file_name="test.pdf",
            statement_date=date(2024, 3, 1),
            currency="CAD",
            begin_total_market_value=Decimal("10000.00"),
            end_total_market_value=Decimal("11000.00"),
        )

        recs = reconcile_monthly(self.account)
        self.assertEqual(len(recs), 1)
        self.assertFalse(recs[0].matching)
        self.assertGreater(recs[0].discrepancy_amount, Decimal("0.00"))

    def test_reconciliation_rbc_only(self):
        """RBC has data but CSV doesn't — reconciliation notes missing CSV."""
        RbcMonthly.objects.create(
            account=self.account,
            file_name="test.pdf",
            statement_date=date(2024, 3, 1),
            currency="CAD",
            begin_total_market_value=Decimal("10000.00"),
            end_total_market_value=Decimal("11000.00"),
        )

        recs = reconcile_monthly(self.account)
        self.assertEqual(len(recs), 1)
        self.assertFalse(recs[0].matching)
        self.assertIn("No CSV", recs[0].discrepancy_notes)

    def test_reconciliation_csv_only(self):
        """CSV has data but RBC doesn't — reconciliation notes the missing RBC."""
        CoreMonthlyStatement.objects.create(
            account=self.account,
            statement_month=date(2024, 3, 1),
            currency="CAD",
            beginning_market_value=Decimal("10000.00"),
            ending_market_value=Decimal("11000.00"),
            total_buys=Decimal("1000.00"),
            total_sells=Decimal("0.00"),
            total_dividends=Decimal("200.00"),
            total_interest=Decimal("50.00"),
            total_commission=Decimal("10.00"),
            net_flow=Decimal("1000.00"),
            total_transactions=3,
        )

        recs = reconcile_monthly(self.account)
        self.assertEqual(len(recs), 1)
        self.assertFalse(recs[0].matching)
        self.assertIn("No RBC statement", recs[0].discrepancy_notes)

    def test_reconciliation_idempotent(self):
        """Running reconciliation twice produces same count."""
        CoreMonthlyStatement.objects.create(
            account=self.account,
            statement_month=date(2024, 3, 1),
            currency="CAD",
            beginning_market_value=Decimal("10000.00"),
            ending_market_value=Decimal("11000.00"),
            total_buys=Decimal("1000.00"),
            total_sells=Decimal("0.00"),
            total_dividends=Decimal("200.00"),
            total_interest=Decimal("50.00"),
            total_commission=Decimal("10.00"),
            net_flow=Decimal("1000.00"),
            total_transactions=3,
        )

        RbcMonthly.objects.create(
            account=self.account,
            file_name="test.pdf",
            statement_date=date(2024, 3, 1),
            currency="CAD",
            begin_total_market_value=Decimal("10000.00"),
            end_total_market_value=Decimal("11000.00"),
        )

        reconcile_monthly(self.account)
        first_count = CoreReconciliation.objects.filter(account=self.account).count()

        reconcile_monthly(self.account)
        second_count = CoreReconciliation.objects.filter(account=self.account).count()

        self.assertEqual(first_count, second_count)

    def test_reconciliation_with_discrepancy_threshold(self):
        """Small differences within $1 threshold are considered matches."""
        CoreMonthlyStatement.objects.create(
            account=self.account,
            statement_month=date(2024, 3, 1),
            currency="CAD",
            beginning_market_value=Decimal("10000.00"),
            ending_market_value=Decimal("11000.01"),
            total_buys=Decimal("1000.00"),
            total_sells=Decimal("0.00"),
            total_dividends=Decimal("200.00"),
            total_interest=Decimal("50.00"),
            total_commission=Decimal("10.00"),
            net_flow=Decimal("1000.00"),
            total_transactions=3,
        )

        RbcMonthly.objects.create(
            account=self.account,
            file_name="test.pdf",
            statement_date=date(2024, 3, 1),
            currency="CAD",
            begin_total_market_value=Decimal("10000.00"),
            end_total_market_value=Decimal("11000.00"),
        )

        recs = reconcile_monthly(self.account)
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0].matching)

    def test_reconciliation_annual(self):
        """Annual reconciliation compares annual reports vs RBC annual."""
        CoreAnnualReport.objects.create(
            account=self.account,
            report_year=2024,
            currency="CAD",
            beginning_value=Decimal("10000.00"),
            ending_value=Decimal("15000.00"),
            deposits=Decimal("5000.00"),
            withdrawals=Decimal("0.00"),
            net_investment_return=Decimal("1000.00"),
            amount_invested=Decimal("5000.00"),
            total_buys=Decimal("5000.00"),
            total_sells=Decimal("0.00"),
            total_income=Decimal("1000.00"),
            total_commission=Decimal("100.00"),
            total_transactions=10,
        )

        RbcAnnual.objects.create(
            account=self.account,
            file_name="annual.pdf",
            report_year=2024,
            report_period_start=date(2024, 1, 1),
            report_period_end=date(2024, 12, 31),
            currency="CAD",
            bal_beginning_value=Decimal("10000.00"),
            bal_ending_value=Decimal("15000.00"),
            bal_deposits_in=Decimal("5000.00"),
            bal_withdrawals_out=Decimal("0.00"),
            bal_net_return=Decimal("1000.00"),
        )

        recs = reconcile_annual(self.account)
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0].matching)
        self.assertEqual(recs[0].reconciliation_type, "annual")
