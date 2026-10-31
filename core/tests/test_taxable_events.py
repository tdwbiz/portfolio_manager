"""Tests for the taxable_events command (step 15a).

Synthetic data only; fake accounts 90000001-90000004 as Account rows with
category='TAXABLE' and stored fingerprints (same convention as
core/tests/test_csv_coverage.py). A BrokerStatement is always created
before a Transaction; BUY rows use negative amount_cad. Plan:
docs/plans/taxable-events.md (Decisions: tdw, Oct 31).
"""

import os
import re
import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import (
    Account,
    BrokerStatement,
    MonthlyCheckResult,
    RbcMonthly,
    RbcMonthlyHolding,
    RbcMonthlyTransaction,
    Transaction,
)
from core.utils import taxable_events as te
from core.utils.account_fingerprint import fingerprint_account_number

FAKE_NUMBERS = ("90000001", "90000002", "90000003", "90000004")
ACCOUNT_NAMES = ("ACCOUNT_A", "ACCOUNT_B", "ACCOUNT_C", "ACCOUNT_D")


def make_taxable_account(name, number):
    return Account.objects.create(
        name=name,
        broker="RBC",
        category="TAXABLE",
        fingerprint=fingerprint_account_number(number),
        last_4=number[-4:],
    )


def section_text(report, number):
    """Body of section ``number`` (between its header and the next one)."""
    match = re.search(
        rf"== {number}\..*?\n(.*?)(?=\n== |\Z)", report, flags=re.S
    )
    assert match, f"section {number} not found in report"
    return match.group(1)


class TaxableEventsBase(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.out_path = os.path.join(self.temp_dir, "report.txt")
        self.accounts = {
            name: make_taxable_account(name, number)
            for name, number in zip(ACCOUNT_NAMES, FAKE_NUMBERS)
        }
        self.statement = BrokerStatement.objects.create(
            source="RBC", file_date=date(2025, 1, 31), raw_filename="activity.csv"
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def tx(self, account, **overrides):
        defaults = dict(
            statement=self.statement,
            account=account,
            date=date(2025, 1, 15),
            settlement_date=date(2025, 1, 17),
            symbol="",
            type="BUY",
            quantity=Decimal("10"),
            price=Decimal("10.00"),
            commission=Decimal("0.00"),
            amount=Decimal("-100.00"),
            currency="CAD",
            exchange_rate_cad=Decimal("1.000000"),
            amount_cad=Decimal("-100.00"),
            notes="",
        )
        defaults.update(overrides)
        return Transaction.objects.create(**defaults)

    def rbc_stmt(self, account, day, currency="CAD"):
        # Safe file name (classify_safe_name -> 'monthly').
        return RbcMonthly.objects.create(
            account=account,
            file_name=f"{day:%Y-%m-%d}_monthly_{account.name}_abcdef12.pdf",
            statement_date=day,
            currency=currency,
        )

    def run_command(self, **options):
        options.setdefault("out", self.out_path)
        call_command("taxable_events", **options)
        with open(self.out_path, encoding="utf-8") as f:
            return f.read()


class SelectionTests(TaxableEventsBase):
    def test_only_taxable_accounts_with_transactions(self):
        self.tx(self.accounts["ACCOUNT_A"])
        # A TFSA account with transactions and a TAXABLE one without.
        e = Account.objects.create(
            name="ACCOUNT_E", broker="RBC", category="TFSA",
            fingerprint=fingerprint_account_number("90000005"), last_4="0005",
        )
        self.tx(e)
        Account.objects.create(
            name="ACCOUNT_F", broker="RBC", category="TAXABLE",
            fingerprint=fingerprint_account_number("90000006"), last_4="0006",
        )
        self.assertEqual([a.name for a in te.taxable_accounts()], ["ACCOUNT_A"])
        report = self.run_command()
        self.assertIn("accounts: ACCOUNT_A", report.splitlines()[2])
        body = section_text(report, 1)
        for name in ("ACCOUNT_B", "ACCOUNT_C", "ACCOUNT_D", "ACCOUNT_E", "ACCOUNT_F"):
            self.assertNotIn(f"  {name} ", body)

    def test_account_filter(self):
        self.tx(self.accounts["ACCOUNT_A"])
        self.tx(self.accounts["ACCOUNT_B"], date=date(2025, 1, 20))
        report = self.run_command(account="ACCOUNT_B")
        self.assertIn("accounts: ACCOUNT_B", report.splitlines()[2])

    def test_rejects_non_taxable_and_unknown_names(self):
        self.tx(self.accounts["ACCOUNT_A"])
        Account.objects.create(
            name="ACCOUNT_E", broker="RBC", category="TFSA",
            fingerprint=fingerprint_account_number("90000005"), last_4="0005",
        )
        with self.assertRaises(CommandError):
            self.run_command(account="ACCOUNT_E")
        with self.assertRaises(CommandError):
            self.run_command(account="GHOST")

    def test_bad_month_rejected(self):
        self.tx(self.accounts["ACCOUNT_A"])
        with self.assertRaises(CommandError):
            self.run_command(date_from="2025-13")


class BoundsTests(TaxableEventsBase):
    def test_from_to_bound_the_sections(self):
        a = self.accounts["ACCOUNT_A"]
        self.tx(a, type="TAX", settlement_date=date(2025, 1, 15),
                amount=Decimal("-10.00"), amount_cad=Decimal("-10.00"))
        self.tx(a, type="TAX", date=date(2025, 3, 15),
                settlement_date=date(2025, 3, 15),
                amount=Decimal("-20.00"), amount_cad=Decimal("-20.00"))
        report = self.run_command(date_from="2025-02", date_to="2025-03")
        body = section_text(report, 1)
        self.assertIn("2025-03-15", body)
        self.assertNotIn("2025-01-15", body)


class Section1Tests(TaxableEventsBase):
    def test_reorg_subtype_known_and_unknown(self):
        a = self.accounts["ACCOUNT_A"]
        self.tx(a, type="REORGANIZATION", symbol="NVDA",
                quantity=Decimal("300"), price=Decimal("0.00"),
                amount=Decimal("0.00"), amount_cad=Decimal("0.00"),
                notes="DIS NVDA STK SPLIT 4:1 ON 100 SHS")
        self.tx(a, type="REORGANIZATION", symbol="XYZ",
                date=date(2025, 1, 16), quantity=Decimal("0"),
                price=Decimal("0.00"), amount=Decimal("5.00"),
                amount_cad=Decimal("5.00"), notes="STK SPLIT 4:1")
        body = section_text(self.run_command(), 1)
        self.assertIn("[DIS]", body)
        self.assertIn("[?]", body)

    def test_return_of_capital_fields(self):
        a = self.accounts["ACCOUNT_A"]
        self.tx(a, type="RETURN_OF_CAPITAL", symbol="XYZF",
                quantity=Decimal("0"), amount=Decimal("-25.50"),
                amount_cad=Decimal("-25.50"),
                notes="ROC ON 100 UNITS FUNDED FROM POSITION")
        body = section_text(self.run_command(), 1)
        self.assertIn("RETURN_OF_CAPITAL", body)
        self.assertIn("amount_cad=-25.50", body)
        self.assertIn("qty=0", body)
        self.assertIn("notes: ROC ON 100 UNITS FUNDED FROM POSITION", body)

    def test_transfer_only_listed_with_quantity(self):
        a = self.accounts["ACCOUNT_A"]
        # Plain cash transfer (noise): not listed.
        self.tx(a, type="TRANSFER", date=date(2025, 1, 16),
                quantity=Decimal("0"), amount=Decimal("500.00"),
                amount_cad=Decimal("500.00"))
        # Share transfer: listed.
        self.tx(a, type="TRANSFER", date=date(2025, 1, 17), symbol="DLR",
                quantity=Decimal("10"), price=Decimal("25.00"),
                amount=Decimal("-250.00"), amount_cad=Decimal("-250.00"))
        body = section_text(self.run_command(), 1)
        self.assertIn("2025-01-17 TRANSFER", body)
        self.assertNotIn("2025-01-16", body)

    def test_qty_nonzero_zero_amount_catch(self):
        a = self.accounts["ACCOUNT_A"]
        # A plain DIV row with reinvested units: qty > 0, amount_cad == 0.
        self.tx(a, type="DIV", symbol="XYZF", quantity=Decimal("4"),
                price=Decimal("12.50"), amount=Decimal("-50.00"),
                amount_cad=Decimal("0.00"),
                notes="REI - XYZF 4 SHS REINV@C$12.50")
        body = section_text(self.run_command(), 1)
        self.assertIn("qty!=0 with amount_cad=0", body)

    def test_option_symbol_detection(self):
        a = self.accounts["ACCOUNT_A"]
        # Short-option code (symbol starts with 9).
        self.tx(a, type="BUY", date=date(2025, 1, 20), symbol="9BSCCN2",
                quantity=Decimal("-1"), price=Decimal("1.00"),
                amount=Decimal("-1.00"), amount_cad=Decimal("-1.00"))
        # Option text in the notes of an otherwise plain row.
        self.tx(a, type="BUY", date=date(2025, 1, 21), symbol="QQQ",
                quantity=Decimal("1"), price=Decimal("3.00"),
                amount=Decimal("-3.00"), amount_cad=Decimal("-3.00"),
                notes="BUY CALL QQQ 03/31/21 321")
        body = section_text(self.run_command(), 1)
        self.assertIn("short-option code 9BSCCN2", body)
        self.assertIn("option text in notes: CALL QQQ 03/31/21 321", body)

    def test_plain_rows_not_listed(self):
        a = self.accounts["ACCOUNT_A"]
        self.tx(a, type="BUY", symbol="AAPL")
        self.tx(a, type="SELL", date=date(2025, 1, 18), symbol="AAPL",
                quantity=Decimal("-10"), amount=Decimal("110.00"),
                amount_cad=Decimal("110.00"))
        self.assertEqual(section_text(self.run_command(), 1).strip(), "none")


class Section2Tests(TaxableEventsBase):
    def test_rbf_csv_rows_and_january_reference_rows(self):
        a = self.accounts["ACCOUNT_A"]
        self.tx(a, type="INT", symbol="RBF2010", quantity=Decimal("2"),
                price=Decimal("10.00"), amount=Decimal("0.00"),
                amount_cad=Decimal("0.00"), notes="INTEREST REINVESTED")
        stmt = self.rbc_stmt(a, date(2025, 1, 31))
        RbcMonthlyTransaction.objects.create(
            rbc_monthly=stmt, date=date(2025, 1, 15),
            activity_type="INTEREST_RECEIVED", symbol="RBF2010",
            quantity=Decimal("2"), price=Decimal("0.00"))
        # No symbol: matched by the INVESTMENT SAVINGS description (decision 1).
        RbcMonthlyTransaction.objects.create(
            rbc_monthly=stmt, date=date(2025, 1, 31), activity_type="OTHER",
            description="RBC INVESTMENT SAVINGS INTEREST CREDIT")
        # Distractor: must not appear.
        RbcMonthlyTransaction.objects.create(
            rbc_monthly=stmt, date=date(2025, 1, 10), activity_type="BOUGHT",
            description="AAPL PURCHASE", symbol="AAPL",
            quantity=Decimal("5"), debit=Decimal("250.00"))
        # An INTEREST_RECEIVED row on a non-January statement: not a reference row.
        stmt_march = self.rbc_stmt(a, date(2025, 3, 31))
        RbcMonthlyTransaction.objects.create(
            rbc_monthly=stmt_march, date=date(2025, 3, 15),
            activity_type="INTEREST_RECEIVED", symbol="RBF2014",
            quantity=Decimal("1"))
        body = section_text(self.run_command(), 2)
        self.assertIn("CSV 2025-01-15 settle=2025-01-17 qty=2", body)
        self.assertIn("sym=RBF2010", body)
        self.assertIn("INVESTMENT SAVINGS", body)
        self.assertNotIn("AAPL", body)
        self.assertNotIn("RBF2014", body)


class Section3Tests(TaxableEventsBase):
    def test_currency_counts_and_usd_fx_presence(self):
        a = self.accounts["ACCOUNT_A"]
        self.tx(a, type="BUY", symbol="AAPL")
        self.tx(a, type="SELL", date=date(2025, 1, 18), symbol="AAPL",
                currency="USD", exchange_rate_cad=Decimal("1.350000"),
                amount=Decimal("100.00"), amount_cad=Decimal("135.00"))
        self.tx(a, type="BUY", date=date(2025, 1, 19), symbol="MSFT",
                currency="USD", exchange_rate_cad=Decimal("1.000000"),
                amount=Decimal("-50.00"), amount_cad=Decimal("-67.50"),
                notes="CONVERTED @ 1.3500")
        self.tx(a, type="DEP", date=date(2025, 1, 20), quantity=Decimal("0"),
                currency="USD", amount=Decimal("200.00"),
                amount_cad=Decimal("270.00"), notes="FX WIRE FROM BANK")
        body = section_text(self.run_command(), 3)
        self.assertIn("ACCOUNT_A CAD BUY: 1", body)
        self.assertIn("ACCOUNT_A USD SELL: 1", body)
        self.assertIn("ACCOUNT_A USD BUY: 1", body)
        self.assertIn("ACCOUNT_A USD DEP: 1", body)
        self.assertIn("USD rows with exchange_rate_cad != 1: 1", body)
        self.assertIn(
            "USD rows with rate-looking notes token (@x.xx or FX): 2", body
        )


class Section4Tests(TaxableEventsBase):
    def test_dlr_csv_and_pdf_rows(self):
        a = self.accounts["ACCOUNT_A"]
        self.tx(a, type="BUY", symbol="DLR", price=Decimal("25.00"),
                amount=Decimal("-250.00"), amount_cad=Decimal("-337.50"),
                notes="TFR USD TO DLR")
        self.tx(a, type="SELL", date=date(2025, 1, 18), symbol="DLR",
                quantity=Decimal("-10"), price=Decimal("25.10"),
                amount=Decimal("251.00"), amount_cad=Decimal("338.85"),
                notes="TFR DLR TO USD")
        stmt = self.rbc_stmt(a, date(2025, 1, 31))
        RbcMonthlyTransaction.objects.create(
            rbc_monthly=stmt, date=date(2025, 1, 15), activity_type="TRANSFER",
            description="DLR CURRENCY CONVERSION USD TO CAD",
            debit=Decimal("250.00"))
        # Distractor without DLR: must not appear.
        RbcMonthlyTransaction.objects.create(
            rbc_monthly=stmt, date=date(2025, 1, 16), activity_type="DEPOSIT",
            description="WIRE FROM BANK", credit=Decimal("100.00"))
        body = section_text(self.run_command(), 4)
        self.assertIn("CSV 2025-01-15 BUY qty=10.0000 amount_cad=-337.50", body)
        self.assertIn("notes: TFR DLR TO USD", body)
        self.assertIn("PDF 2025-01-15 TRANSFER debit=250.00 credit=0.00", body)
        self.assertNotIn("WIRE FROM BANK", body)


class Section5Tests(TaxableEventsBase):
    def test_cross_account_same_month_symbol(self):
        a, b = self.accounts["ACCOUNT_A"], self.accounts["ACCOUNT_B"]
        # Both accounts need a transaction to be selected.
        self.tx(a, type="BUY", symbol="AAPL")
        self.tx(b, type="BUY", date=date(2025, 1, 20), symbol="AAPL")
        sa = self.rbc_stmt(a, date(2025, 1, 31))
        sb = self.rbc_stmt(b, date(2025, 1, 31))
        RbcMonthlyHolding.objects.create(
            rbc_monthly=sa, currency="CAD", security="Apple Inc.",
            symbol="AAPL", quantity=Decimal("10"), book_cost=Decimal("100.00"))
        RbcMonthlyHolding.objects.create(
            rbc_monthly=sb, currency="USD", security="Apple Inc.",
            symbol="AAPL", quantity=Decimal("5"), book_cost=Decimal("60.00"))
        # Held by one account only: not listed.
        RbcMonthlyHolding.objects.create(
            rbc_monthly=sa, currency="CAD", security="Tesla Inc.",
            symbol="TSLA", quantity=Decimal("3"), book_cost=Decimal("300.00"))
        body = section_text(self.run_command(), 5)
        self.assertIn("2025-01 AAPL:", body)
        self.assertIn("ACCOUNT_A qty=10.0000 CAD", body)
        self.assertIn("ACCOUNT_B qty=5.0000 USD", body)
        self.assertNotIn("TSLA", body)


class Section67Tests(TaxableEventsBase):
    def test_section6_flags_missing_book_cost(self):
        a = self.accounts["ACCOUNT_A"]
        stmt = self.rbc_stmt(a, date(2017, 12, 29))
        RbcMonthlyHolding.objects.create(
            rbc_monthly=stmt, currency="CAD", security="Nvidia Corp.",
            symbol="NVDA", quantity=Decimal("100"),
            book_cost=Decimal("5000.00"))
        RbcMonthlyHolding.objects.create(
            rbc_monthly=stmt, currency="CAD", security="Microsoft Corp.",
            symbol="MSFT", quantity=Decimal("50"), book_cost=Decimal("0.00"))
        joined = "\n".join(
            te.section_6_target_statement(account_name="ACCOUNT_A", month="2017-12")
        )
        self.assertIn("(file kind: monthly)", joined)
        self.assertIn("NVDA qty=100.0000 CAD book_cost=5000.00\n", joined + "\n")
        self.assertNotIn(
            "NVDA qty=100.0000 CAD book_cost=5000.00 (book cost missing)", joined
        )
        self.assertIn(
            "MSFT qty=50.0000 CAD book_cost=0.00 (book cost missing)", joined
        )

    def test_section6_not_stored_note(self):
        # Default target TS_HUISGELD 2017-12: not in the test DB.
        lines = te.section_6_target_statement()
        self.assertEqual(
            lines,
            [f"  {te.SECTION6_ACCOUNT} {te.SECTION6_MONTH}: no stored statement"],
        )

    def test_section7_first_stored_statement(self):
        b = self.accounts["ACCOUNT_B"]
        first = self.rbc_stmt(b, date(2019, 5, 31))
        later = self.rbc_stmt(b, date(2020, 1, 31))
        RbcMonthlyHolding.objects.create(
            rbc_monthly=first, currency="CAD", security="Apple Inc.",
            symbol="AAPL", quantity=Decimal("4"), book_cost=Decimal("40.00"))
        RbcMonthlyHolding.objects.create(
            rbc_monthly=later, currency="CAD", security="Microsoft Corp.",
            symbol="MSFT", quantity=Decimal("2"), book_cost=Decimal("0.00"))
        joined = "\n".join(te.section_7_first_statement(account_name="ACCOUNT_B"))
        self.assertIn("2019-05-31", joined)
        self.assertIn("AAPL qty=4.0000 CAD book_cost=40.00", joined)
        self.assertNotIn("MSFT", joined)

    def test_section7_not_stored_note(self):
        # Default target TS_CAR_LIVING: not in the test DB.
        lines = te.section_7_first_statement()
        self.assertEqual(lines, [f"  {te.SECTION7_ACCOUNT}: no stored statement"])


class Section8Tests(TaxableEventsBase):
    def _two_statements(self, account, book_cost_jan, book_cost_feb):
        jan = self.rbc_stmt(account, date(2025, 1, 31))
        feb = self.rbc_stmt(account, date(2025, 2, 28))
        RbcMonthlyHolding.objects.create(
            rbc_monthly=jan, currency="CAD", security="Apple Inc.",
            symbol="AAPL", quantity=Decimal("10"), book_cost=book_cost_jan)
        RbcMonthlyHolding.objects.create(
            rbc_monthly=feb, currency="CAD", security="Apple Inc.",
            symbol="AAPL", quantity=Decimal("10"), book_cost=book_cost_feb)

    def test_fires_on_book_cost_change_without_transaction(self):
        a = self.accounts["ACCOUNT_A"]
        self._two_statements(a, Decimal("100.00"), Decimal("110.00"))
        lines = te.section_8_book_cost_changes([a], None, None)
        self.assertEqual(len(lines), 1)
        self.assertIn("2025 [CAD] AAPL: book cost 100.00 -> 110.00", lines[0])
        self.assertIn("no transaction in period", lines[0])

    def test_silent_when_transaction_in_period(self):
        a = self.accounts["ACCOUNT_A"]
        self._two_statements(a, Decimal("100.00"), Decimal("110.00"))
        self.tx(a, type="BUY", symbol="AAPL", date=date(2025, 2, 10),
                settlement_date=date(2025, 2, 12))
        lines = te.section_8_book_cost_changes([a], None, None)
        self.assertEqual(lines, [])

    def test_silent_when_quantity_changed(self):
        a = self.accounts["ACCOUNT_A"]
        jan = self.rbc_stmt(a, date(2025, 1, 31))
        feb = self.rbc_stmt(a, date(2025, 2, 28))
        RbcMonthlyHolding.objects.create(
            rbc_monthly=jan, currency="CAD", security="Apple Inc.",
            symbol="AAPL", quantity=Decimal("10"), book_cost=Decimal("100.00"))
        RbcMonthlyHolding.objects.create(
            rbc_monthly=feb, currency="CAD", security="Apple Inc.",
            symbol="AAPL", quantity=Decimal("20"), book_cost=Decimal("110.00"))
        lines = te.section_8_book_cost_changes([a], None, None)
        self.assertEqual(lines, [])


class Section9Tests(TaxableEventsBase):
    def test_prints_stored_verdicts(self):
        a = self.accounts["ACCOUNT_A"]
        MonthlyCheckResult.objects.create(
            account=a, statement_date=date(2021, 6, 30), currency="CAD",
            verdict="FAIL", notes="CASH: expected 100.00, got 90.00",
            checked_at=timezone.now())
        lines = te.section_9_stored_verdicts(
            account_name="ACCOUNT_A", months=("2021-06", "2024-05")
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("2021-06-30 [CAD] FAIL", lines[0])
        self.assertIn("CASH: expected 100.00, got 90.00", lines[0])

    def test_none_stored_note(self):
        # Default target TS_CAR_LIVING: not in the test DB.
        lines = te.section_9_stored_verdicts()
        self.assertEqual(
            lines,
            [f"  {te.SECTION9_ACCOUNT}: no stored verdicts for "
             f"{', '.join(te.SECTION9_MONTHS)}"],
        )


class NoWriteTests(TaxableEventsBase):
    def test_command_writes_nothing_to_the_database(self):
        a = self.accounts["ACCOUNT_A"]
        self.tx(a, type="TAX", amount=Decimal("-10.00"),
                amount_cad=Decimal("-10.00"))
        stmt = self.rbc_stmt(a, date(2025, 1, 31))
        RbcMonthlyHolding.objects.create(
            rbc_monthly=stmt, currency="CAD", security="Apple Inc.",
            symbol="AAPL", quantity=Decimal("10"), book_cost=Decimal("0.00"))
        counts = {
            model: model.objects.count()
            for model in (
                Account, BrokerStatement, Transaction, RbcMonthly,
                RbcMonthlyHolding, RbcMonthlyTransaction, MonthlyCheckResult,
            )
        }
        with CaptureQueriesContext(connection) as ctx:
            self.run_command()
        writers = [
            q["sql"] for q in ctx.captured_queries
            if any(word in q["sql"].upper()
                   for word in ("INSERT", "UPDATE", "DELETE"))
        ]
        self.assertEqual(writers, [])
        for model, count in counts.items():
            self.assertEqual(model.objects.count(), count)


class PrivacyTests(TaxableEventsBase):
    def test_report_contains_no_account_digits(self):
        for name in ACCOUNT_NAMES:
            self.tx(self.accounts[name], type="TAX",
                    amount=Decimal("-1.00"), amount_cad=Decimal("-1.00"))
        report = self.run_command()
        for number in FAKE_NUMBERS:
            self.assertNotIn(number, report)
