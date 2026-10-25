"""Tests for monthly/annual statement generation and reconciliation (Phase E: E3-B, E3-C, E3-D)."""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    Account,
    BrokerStatement,
    CoreAnnualReport,
    CoreMonthlyStatement,
    CoreReconciliation,
    RbcAnnual,
    RbcMonthly,
    Transaction,
)
from core.utils.reconciliation import (
    reconcile_account,
    reconcile_annual,
    reconcile_monthly,
)
from core.utils.statements import generate_annual_reports, generate_monthly_statements


class MonthlyStatementGenerationTests(TestCase):
    """Tests for E3-B: Monthly statement generation."""

    def setUp(self):
        self.account = Account.objects.create(
            name="Test Account",
            broker="RBC-DIRECT",
            currency="CAD")
        self.statement = BrokerStatement.objects.create(
            source="RBC",
            file_date=date(2024, 1, 15),
            raw_filename="test.csv",
        )

    def _make_tx(
        self,
        month,
        day,
        symbol,
        tx_type,
        amount_cad,
        commission=Decimal("0"),
        currency="CAD",
    ):
        """Helper to create a transaction.

        Uses CSV sign convention:
        - BUY/WDRAW/TAX = negative amount (money out)
        - SELL/DEP/DIV/INT = positive amount (money in)
        - Commission is already included in amount_cad
        """
        return Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=date(2024, month, day),
            symbol=symbol,
            type=tx_type,
            quantity=Decimal("10.0000"),
            price=Decimal("100.0000"),
            commission=commission,
            amount=amount_cad,
            currency=currency,
            exchange_rate_cad=Decimal("1.000000"),
            amount_cad=amount_cad,
        )

    def test_generate_single_month(self):
        """A single BUY transaction generates one monthly statement."""
        # BUY is negative amount (money out). Commission already in amount_cad.
        # amount_cad = -1950 (buy), commission is included in this value
        self._make_tx(3, 15, "AAPL", "BUY", Decimal("-1950.00"))

        stmts = generate_monthly_statements(self.account)
        self.assertEqual(len(stmts), 1)
        stmt = stmts[0]
        self.assertEqual(stmt.statement_month, date(2024, 3, 1))
        self.assertEqual(stmt.currency, "CAD")
        # total_buys stored as absolute magnitude
        self.assertEqual(stmt.total_buys, Decimal("1950.00"))
        self.assertEqual(stmt.total_commission, Decimal("0"))
        self.assertEqual(stmt.beginning_market_value, Decimal("0.00"))
        # net_flow = -1950, ending = 0 + (-1950) = -1950
        self.assertEqual(stmt.ending_market_value, Decimal("-1950.00"))
        self.assertEqual(stmt.total_commission, Decimal("0"))

    def test_generate_multi_month(self):
        """Transactions in multiple months create separate statements."""
        # BUYs are negative amounts
        self._make_tx(1, 10, "AAPL", "BUY", Decimal("-1950.00"))
        self._make_tx(2, 15, "GOOGL", "BUY", Decimal("-1300.00"))

        stmts = generate_monthly_statements(self.account)
        self.assertEqual(len(stmts), 2)

        jan = stmts[0]
        self.assertEqual(jan.statement_month, date(2024, 1, 1))
        # net_flow = -1950, ending = 0 + (-1950) = -1950
        self.assertEqual(jan.ending_market_value, Decimal("-1950.00"))

        feb = stmts[1]
        self.assertEqual(feb.statement_month, date(2024, 2, 1))
        # beginning = jan ending = -1950
        self.assertEqual(feb.beginning_market_value, Decimal("-1950.00"))
        self.assertEqual(feb.total_buys, Decimal("1300.00"))

    def test_generate_with_dividends_and_interest(self):
        """Income transactions are properly summed."""
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=date(2024, 3, 15),
            symbol="TD",
            type="DIV",
            quantity=Decimal("100.0000"),
            price=Decimal("1.2000"),
            commission=Decimal("0.0000"),
            amount=Decimal("120.0000"),
            currency="CAD",
            exchange_rate_cad=Decimal("1.000000"),
            amount_cad=Decimal("120.00"),
        )
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=date(2024, 3, 20),
            symbol="GIC",
            type="INT",
            quantity=Decimal("1.0000"),
            price=Decimal("50.0000"),
            commission=Decimal("0.0000"),
            amount=Decimal("50.0000"),
            currency="CAD",
            exchange_rate_cad=Decimal("1.000000"),
            amount_cad=Decimal("50.00"),
        )

        stmts = generate_monthly_statements(self.account)
        self.assertEqual(len(stmts), 1)
        stmt = stmts[0]
        self.assertEqual(stmt.total_dividends, Decimal("120.00"))
        self.assertEqual(stmt.total_interest, Decimal("50.00"))

    def test_generate_sell_transactions(self):
        """SELL transactions add to market value (positive cash flow)."""
        # SELL is positive amount (money in)
        self._make_tx(3, 15, "AAPL", "SELL", Decimal("2080.00"))

        stmts = generate_monthly_statements(self.account)
        self.assertEqual(len(stmts), 1)
        stmt = stmts[0]
        self.assertEqual(stmt.total_sells, Decimal("2080.00"))
        # net_flow = +2080, ending = 0 + 2080 = 2080
        self.assertEqual(stmt.ending_market_value, Decimal("2080.00"))

    def test_generate_idempotent(self):
        """Running generation twice updates existing records (update_or_create)."""
        self._make_tx(3, 15, "AAPL", "BUY", Decimal("1950.00"), Decimal("5.00"))

        generate_monthly_statements(self.account)
        self.assertEqual(
            CoreMonthlyStatement.objects.filter(account=self.account).count(), 1
        )

        generate_monthly_statements(self.account)
        self.assertEqual(
            CoreMonthlyStatement.objects.filter(account=self.account).count(), 1
        )

    def test_generate_no_transactions(self):
        """Empty account returns no statements."""
        stmts = generate_monthly_statements(self.account)
        self.assertEqual(len(stmts), 0)

    def test_generate_with_date_range(self):
        """Only transactions within the date range are included."""
        self._make_tx(1, 10, "AAPL", "BUY", Decimal("1950.00"), Decimal("5.00"))
        self._make_tx(6, 15, "GOOGL", "BUY", Decimal("1300.00"), Decimal("5.00"))

        from_date = date(2024, 3, 1)
        to_date = date(2024, 6, 30)
        stmts = generate_monthly_statements(
            self.account, from_date=from_date, to_date=to_date
        )
        self.assertEqual(len(stmts), 1)
        self.assertEqual(stmts[0].statement_month, date(2024, 6, 1))


class AnnualReportGenerationTests(TestCase):
    """Tests for E3-C: Annual report generation."""

    def setUp(self):
        self.account = Account.objects.create(
            name="Test Account 2",
            broker="RBC-DIRECT",
            currency="CAD")
        self.statement = BrokerStatement.objects.create(
            source="RBC",
            file_date=date(2024, 1, 15),
            raw_filename="test.csv",
        )

    def test_generate_annual_from_monthly(self):
        """Annual report aggregates monthly statements for a year."""
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=date(2024, 1, 10),
            symbol="AAPL",
            type="BUY",
            quantity=Decimal("10.0000"),
            price=Decimal("150.0000"),
            commission=Decimal("5.0000"),
            amount=Decimal("1500.0000"),
            currency="USD",
            exchange_rate_cad=Decimal("1.300000"),
            amount_cad=Decimal("1950.00"),
        )
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=date(2024, 2, 15),
            symbol="GOOGL",
            type="BUY",
            quantity=Decimal("5.0000"),
            price=Decimal("200.0000"),
            commission=Decimal("5.0000"),
            amount=Decimal("1000.0000"),
            currency="USD",
            exchange_rate_cad=Decimal("1.300000"),
            amount_cad=Decimal("1300.00"),
        )

        generate_monthly_statements(self.account)
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
