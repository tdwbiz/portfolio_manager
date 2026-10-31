"""Tests for the book_cost_check command (step 15d).

Synthetic data only; fake accounts 90000001-90000004 as Account rows with
category='TAXABLE' and stored fingerprints (same convention as
core/tests/test_taxable_events.py). A BrokerStatement is always created
before a Transaction; BUY rows use negative amount_cad. Plan:
docs/plans/book-cost-check.md (Decisions: tdw, Oct 31).
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

from core.models import (
    Account,
    BrokerStatement,
    OpeningPosition,
    RbcMonthly,
    RbcMonthlyHolding,
    Transaction,
)
from core.utils import book_cost_check as bcc
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


class BookCostCheckBase(TestCase):
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

    def holding(self, stmt, symbol, qty, cost, currency="CAD"):
        return RbcMonthlyHolding.objects.create(
            rbc_monthly=stmt,
            currency=currency,
            security=f"{symbol} Inc.",
            symbol=symbol,
            quantity=qty,
            book_cost=cost,
        )

    def run_command(self, **options):
        options.setdefault("out", self.out_path)
        call_command("book_cost_check", **options)
        with open(self.out_path, encoding="utf-8") as f:
            return f.read()


class SelectionTests(BookCostCheckBase):
    def test_default_accounts_and_account_filter(self):
        a = self.accounts["ACCOUNT_A"]
        b = self.accounts["ACCOUNT_B"]
        self.rbc_stmt(a, date(2025, 1, 31))
        self.tx(a)
        # TAXABLE with transactions but no stored statement: not selected.
        self.tx(b)
        report = self.run_command()
        self.assertIn("accounts: ACCOUNT_A", report.splitlines()[2])
        with self.assertRaises(CommandError):
            self.run_command(account="ACCOUNT_B")
        with self.assertRaises(CommandError):
            self.run_command(account="GHOST")


class ReplayRuleTests(BookCostCheckBase):
    def test_buy_adds_cost(self):
        a = self.accounts["ACCOUNT_A"]
        stmt = self.rbc_stmt(a, date(2025, 1, 31))
        self.holding(stmt, "AAPL", Decimal("10"), Decimal("100.00"))
        self.tx(a, symbol="AAPL")
        replay = bcc.replay_account(a)
        state = replay.snapshots[date(2025, 1, 31)][("AAPL", "CAD")]
        self.assertEqual(state.quantity, Decimal("10"))
        self.assertEqual(state.cost, Decimal("100.00"))

    def test_sell_removes_average_cost(self):
        a = self.accounts["ACCOUNT_A"]
        self.rbc_stmt(a, date(2025, 1, 31))
        feb = self.rbc_stmt(a, date(2025, 2, 28))
        self.holding(feb, "AAPL", Decimal("5"), Decimal("50.00"))
        self.tx(a, symbol="AAPL")
        self.tx(a, type="SELL", symbol="AAPL", date=date(2025, 2, 10),
                settlement_date=date(2025, 2, 12), quantity=Decimal("-5"),
                price=Decimal("11.00"), amount=Decimal("55.00"),
                amount_cad=Decimal("55.00"))
        replay = bcc.replay_account(a)
        state = replay.snapshots[date(2025, 2, 28)][("AAPL", "CAD")]
        self.assertEqual(state.quantity, Decimal("5"))
        self.assertEqual(state.cost, Decimal("50.00"))

    def test_reinvest_note_adds_qty_times_price(self):
        a = self.accounts["ACCOUNT_A"]
        stmt = self.rbc_stmt(a, date(2025, 1, 31))
        self.holding(stmt, "RBF2010", Decimal("2"), Decimal("20.00"))
        self.tx(a, type="INT", symbol="RBF2010", quantity=Decimal("2"),
                price=Decimal("10.00"), amount=Decimal("0.00"),
                amount_cad=Decimal("0.00"),
                notes="REI - RBF2010 2 UNITS REINV@C$10.00")
        replay = bcc.replay_account(a)
        state = replay.snapshots[date(2025, 1, 31)][("RBF2010", "CAD")]
        self.assertEqual(state.quantity, Decimal("2"))
        self.assertEqual(state.cost, Decimal("20.00"))
        # Decision 1: the regex also accepts the other spelling.
        self.assertEqual(
            bcc.REINV_RE.search("REINVEST @ $10.00").group(1), "10.00"
        )

    def test_qty_positive_zero_amount_without_note_is_flagged(self):
        a = self.accounts["ACCOUNT_A"]
        self.rbc_stmt(a, date(2025, 1, 31))
        self.tx(a, type="DIV", symbol="XYZF", quantity=Decimal("3"),
                price=Decimal("12.50"), amount=Decimal("0.00"),
                amount_cad=Decimal("0.00"))
        replay = bcc.replay_account(a)
        self.assertEqual(len(replay.flags), 1)
        self.assertIn("no reinvest-at-price note", replay.flags[0])
        state = replay.snapshots[date(2025, 1, 31)][("XYZF", "CAD")]
        self.assertEqual(state.quantity, Decimal("3"))
        self.assertEqual(state.cost, Decimal("0"))

    def test_return_of_capital_and_notional_adjustment(self):
        a = self.accounts["ACCOUNT_A"]
        stmt = self.rbc_stmt(a, date(2025, 1, 31))
        self.holding(stmt, "XYZF", Decimal("10"), Decimal("75.59"))
        self.tx(a, symbol="XYZF")
        self.tx(a, type="RETURN_OF_CAPITAL", symbol="XYZF",
                date=date(2025, 1, 20), settlement_date=date(2025, 1, 22),
                quantity=Decimal("0"), amount=Decimal("-25.00"),
                amount_cad=Decimal("-25.00"),
                notes="RTC - ADJUSTMENT TO BOOK COST $25.00")
        # Typed DEP: the note rule applies whatever the stored type, and the
        # dot-first amount ($.59) must parse.
        self.tx(a, type="DEP", symbol="XYZF", date=date(2025, 1, 21),
                settlement_date=date(2025, 1, 23), quantity=Decimal("0"),
                amount=Decimal("-10.00"), amount_cad=Decimal("-10.00"),
                notes="ADJ - NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK VALUE $.59")
        replay = bcc.replay_account(a)
        state = replay.snapshots[date(2025, 1, 31)][("XYZF", "CAD")]
        # 100.00 - 25.00 + 0.59: the note amounts move the cost, not the
        # row amounts (-25.00 / -10.00 would give 65.00).
        self.assertEqual(state.cost, Decimal("75.59"))

    def test_return_of_capital_without_amount_in_notes(self):
        a = self.accounts["ACCOUNT_A"]
        stmt = self.rbc_stmt(a, date(2025, 1, 31))
        self.holding(stmt, "ABCF", Decimal("10"), Decimal("70.00"))
        self.tx(a, symbol="ABCF")
        # Decision 2: no $X in the notes — fall back to abs(amount).
        self.tx(a, type="RETURN_OF_CAPITAL", symbol="ABCF",
                date=date(2025, 1, 20), settlement_date=date(2025, 1, 22),
                quantity=Decimal("0"), amount=Decimal("-30.00"),
                amount_cad=Decimal("-30.00"),
                notes="ROC ON 10 UNITS FUNDED FROM POSITION")
        # Zero amount and no $X: add 0 and flag.
        self.tx(a, symbol="DEFF", date=date(2025, 1, 16),
                settlement_date=date(2025, 1, 20))
        self.tx(a, type="RETURN_OF_CAPITAL", symbol="DEFF",
                date=date(2025, 1, 20), settlement_date=date(2025, 1, 22),
                quantity=Decimal("0"), amount=Decimal("0.00"),
                amount_cad=Decimal("0.00"), notes="ROC ADJUSTMENT")
        replay = bcc.replay_account(a)
        self.assertEqual(
            replay.snapshots[date(2025, 1, 31)][("ABCF", "CAD")].cost,
            Decimal("70.00"),
        )
        self.assertEqual(
            replay.snapshots[date(2025, 1, 31)][("DEFF", "CAD")].cost,
            Decimal("100.00"),
        )
        self.assertTrue(
            any("no $X in notes and zero amount" in line for line in replay.flags)
        )

    def test_usd_row_uses_amount_not_amount_cad(self):
        a = self.accounts["ACCOUNT_A"]
        stmt = self.rbc_stmt(a, date(2025, 1, 31), currency="USD")
        self.holding(stmt, "AAPL", Decimal("10"), Decimal("100.00"),
                     currency="USD")
        self.tx(a, symbol="AAPL", currency="USD",
                exchange_rate_cad=Decimal("1.350000"),
                amount=Decimal("-100.00"), amount_cad=Decimal("-135.00"))
        replay = bcc.replay_account(a)
        state = replay.snapshots[date(2025, 1, 31)][("AAPL", "USD")]
        self.assertEqual(state.cost, Decimal("100.00"))

    def test_transfer_pair_flagged_and_netting(self):
        a = self.accounts["ACCOUNT_A"]
        feb = self.rbc_stmt(a, date(2025, 2, 28))
        self.holding(feb, "MSFT", Decimal("10"), Decimal("50.00"))
        self.tx(a, symbol="MSFT")
        self.tx(a, type="TRANSFER", symbol="MSFT", date=date(2025, 1, 20),
                settlement_date=date(2025, 1, 22), quantity=Decimal("-5"),
                price=Decimal("10.00"), amount=Decimal("0.00"),
                amount_cad=Decimal("0.00"))
        self.tx(a, type="TRANSFER", symbol="MSFT", date=date(2025, 1, 24),
                settlement_date=date(2025, 1, 28), quantity=Decimal("5"),
                price=Decimal("10.00"), amount=Decimal("0.00"),
                amount_cad=Decimal("0.00"))
        replay = bcc.replay_account(a)
        # Decision 6: both directions of the pair are flagged.
        self.assertEqual(len(replay.flags), 2)
        for line in replay.flags:
            self.assertIn("TRANSFER with quantity", line)
        state = replay.snapshots[date(2025, 2, 28)][("MSFT", "CAD")]
        # The pair nets the quantity to the bought 10; the out-row removed
        # the average cost of 5 units and the in-row added 0.
        self.assertEqual(state.quantity, Decimal("10"))
        self.assertEqual(state.cost, Decimal("50.00"))


class ComparisonTests(BookCostCheckBase):
    def test_reorganization_flagged_and_not_checked(self):
        a = self.accounts["ACCOUNT_A"]
        jan = self.rbc_stmt(a, date(2025, 1, 31))
        feb = self.rbc_stmt(a, date(2025, 2, 28))
        self.holding(jan, "NVDA", Decimal("10"), Decimal("100.00"))
        self.holding(feb, "NVDA", Decimal("310"), Decimal("100.00"))
        self.tx(a, symbol="NVDA")
        self.tx(a, type="REORGANIZATION", symbol="NVDA",
                date=date(2025, 2, 10), settlement_date=date(2025, 2, 12),
                quantity=Decimal("300"), price=Decimal("0.00"),
                amount=Decimal("0.00"), amount_cad=Decimal("0.00"),
                notes="DIS NVDA STK SPLIT 4:1 ON 100 SHS")
        report = self.run_command()
        self.assertIn("2025-01-31 [CAD] NVDA: MATCH", report)
        self.assertIn(
            "2025-02-28 [CAD] NVDA: NOT CHECKED "
            "(reorganization on or before 2025-02-12)",
            report,
        )
        self.assertTrue(
            any("REORGANIZATION — not checked from 2025-02-12" in line
                for line in bcc.replay_account(a).flags)
        )

    def test_match_and_diff_with_delta_and_txs_since_last_match(self):
        a = self.accounts["ACCOUNT_A"]
        jan = self.rbc_stmt(a, date(2025, 1, 31))
        feb = self.rbc_stmt(a, date(2025, 2, 28))
        self.holding(jan, "AAPL", Decimal("10"), Decimal("100.00"))
        self.holding(feb, "AAPL", Decimal("10"), Decimal("100.00"))
        self.tx(a, symbol="AAPL")
        self.tx(a, type="RETURN_OF_CAPITAL", symbol="AAPL",
                date=date(2025, 2, 10), settlement_date=date(2025, 2, 12),
                quantity=Decimal("0"), amount=Decimal("-5.00"),
                amount_cad=Decimal("-5.00"),
                notes="RTC - ADJUSTMENT TO BOOK COST $5.00")
        report = self.run_command()
        self.assertIn("2025-01-31 [CAD] AAPL: MATCH", report)
        self.assertIn(
            "2025-02-28 [CAD] AAPL: DIFF — cost replay 95.00 vs PDF 100.00 "
            "(diff -5.00); change since 2025-01-31: -5.00",
            report,
        )
        self.assertIn(
            "since last MATCH: 2025-02-10 RETURN_OF_CAPITAL AAPL [CAD] "
            "qty=0.0000 amount=-5.00",
            report,
        )
        # Summary: one MATCH, one DIFF, first DIFF and max |diff|.
        self.assertIn(
            "ACCOUNT_A AAPL [CAD]: MATCH=1 DIFF=1 first DIFF 2025-02-28 "
            "max |diff| 5.00",
            report,
        )

    def test_one_sided_securities_listed(self):
        a = self.accounts["ACCOUNT_A"]
        stmt = self.rbc_stmt(a, date(2025, 1, 31))
        # PDF-only: printed on the PDF, never traded in the CSV.
        self.holding(stmt, "TSLA", Decimal("3"), Decimal("300.00"))
        # Replay-only: traded in the CSV, not printed on the PDF.
        self.tx(a, symbol="MSFT")
        report = self.run_command()
        self.assertIn(
            "2025-01-31 [CAD] TSLA: PDF-ONLY qty=3.0000 book_cost=300.00",
            report,
        )
        self.assertIn(
            "2025-01-31 [CAD] MSFT: REPLAY-ONLY qty=10.0000 cost=100.00",
            report,
        )


class SeedTests(BookCostCheckBase):
    def test_seed_from_opening_position(self):
        a = self.accounts["ACCOUNT_A"]
        jan = self.rbc_stmt(a, date(2025, 1, 31))
        feb = self.rbc_stmt(a, date(2025, 2, 28))
        OpeningPosition.objects.create(
            account=a, statement=jan, symbol="AAPL", currency="CAD",
            quantity=Decimal("10"), book_cost=Decimal("100.00"),
            source="RBC_BOOK_COST",
        )
        # Before the seed date: not replayed.
        self.tx(a, symbol="AAPL", date=date(2024, 12, 15),
                settlement_date=date(2024, 12, 17), quantity=Decimal("5"),
                amount=Decimal("-50.00"), amount_cad=Decimal("-50.00"))
        # After the seed date: replayed.
        self.tx(a, symbol="AAPL", date=date(2025, 2, 10),
                settlement_date=date(2025, 2, 12), quantity=Decimal("2"),
                amount=Decimal("-20.00"), amount_cad=Decimal("-20.00"))
        replay = bcc.replay_account(a)
        jan_state = replay.snapshots[date(2025, 1, 31)][("AAPL", "CAD")]
        self.assertEqual(jan_state.quantity, Decimal("10"))
        self.assertEqual(jan_state.cost, Decimal("100.00"))
        feb_state = replay.snapshots[date(2025, 2, 28)][("AAPL", "CAD")]
        self.assertEqual(feb_state.quantity, Decimal("12"))
        self.assertEqual(feb_state.cost, Decimal("120.00"))

    def test_manual_opening_position_seeded_at_first_statement(self):
        b = self.accounts["ACCOUNT_B"]
        jan = self.rbc_stmt(b, date(2025, 1, 31))
        OpeningPosition.objects.create(
            account=b, statement=None, symbol="MSFT", currency="CAD",
            quantity=Decimal("4"), book_cost=Decimal("40.00"),
            source="MANUAL",
        )
        # Before the (first-statement) seed date: not replayed.
        self.tx(b, symbol="MSFT", date=date(2024, 12, 15),
                settlement_date=date(2024, 12, 17), quantity=Decimal("1"),
                amount=Decimal("-10.00"), amount_cad=Decimal("-10.00"))
        replay = bcc.replay_account(b)
        self.assertEqual(len(replay.notes), 1)
        self.assertIn(
            "MANUAL opening position MSFT [CAD] has no statement — seeded "
            "at first stored statement 2025-01-31",
            replay.notes[0],
        )
        state = replay.snapshots[date(2025, 1, 31)][("MSFT", "CAD")]
        self.assertEqual(state.quantity, Decimal("4"))
        self.assertEqual(state.cost, Decimal("40.00"))


class CandidatesTests(BookCostCheckBase):
    def test_candidates_fire_on_book_cost_change_without_transaction(self):
        a = self.accounts["ACCOUNT_A"]
        jan = self.rbc_stmt(a, date(2025, 1, 31))
        feb = self.rbc_stmt(a, date(2025, 2, 28))
        self.holding(jan, "AAPL", Decimal("10"), Decimal("100.00"))
        self.holding(feb, "AAPL", Decimal("10"), Decimal("110.00"))
        report = self.run_command()
        self.assertIn(
            "ACCOUNT_A 2025 [CAD] AAPL: book cost 100.00 -> 110.00 "
            "(qty 10.0000) between 2025-01-31 and 2025-02-28, "
            "no transaction in period",
            report,
        )


class NoWriteTests(BookCostCheckBase):
    def test_command_writes_nothing_to_the_database(self):
        a = self.accounts["ACCOUNT_A"]
        stmt = self.rbc_stmt(a, date(2025, 1, 31))
        self.holding(stmt, "AAPL", Decimal("10"), Decimal("100.00"))
        self.tx(a, symbol="AAPL")
        counts = {
            model: model.objects.count()
            for model in (
                Account, BrokerStatement, Transaction, RbcMonthly,
                RbcMonthlyHolding, OpeningPosition,
            )
        }
        with CaptureQueriesContext(connection) as ctx:
            self.run_command()
        # Word boundaries: the column 'updated_at' contains 'UPDATE'.
        writers = [
            q["sql"] for q in ctx.captured_queries
            if re.search(r"\b(?:INSERT|UPDATE|DELETE)\b", q["sql"].upper())
        ]
        self.assertEqual(writers, [])
        for model, count in counts.items():
            self.assertEqual(model.objects.count(), count)
