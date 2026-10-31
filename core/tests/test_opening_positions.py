"""Tests for step 15c: set_account_owners and load_opening_positions."""

import io
from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import (
    Account,
    AccountOwner,
    BrokerStatement,
    OpeningPosition,
    Owner,
    RbcMonthly,
    RbcMonthlyHolding,
    Transaction,
)
from core.utils.account_fingerprint import fingerprint_account_number

STMT_DATE = "2025-01-31"


def _account(name="ACCOUNT_A", number="90000001"):
    return Account.objects.create(
        name=name,
        broker="RBC",
        fingerprint=fingerprint_account_number(number),
        last_4=number[-4:],
    )


def _statement(account, currency, date_str=STMT_DATE):
    return RbcMonthly.objects.create(
        account=account,
        file_name=f"rbc-{date_str}-{currency}.pdf",
        statement_date=date.fromisoformat(date_str),
        currency=currency,
    )


def _holding(stmt, symbol, security, currency, quantity, book_cost):
    return RbcMonthlyHolding.objects.create(
        rbc_monthly=stmt,
        currency=currency,
        security=security,
        symbol=symbol,
        quantity=quantity,
        book_cost=book_cost,
    )


def _tx(account, settle_str, tx_date="2025-01-03"):
    stmt = BrokerStatement.objects.create(
        source="RBC", file_date=date.fromisoformat(tx_date), raw_filename="x.csv"
    )
    return Transaction.objects.create(
        statement=stmt,
        account=account,
        date=date.fromisoformat(tx_date),
        settlement_date=date.fromisoformat(settle_str),
        symbol="VOO",
        type="BUY",
        quantity=Decimal("1"),
        price=Decimal("10.0000"),
        commission=Decimal("0.00"),
        amount=Decimal("-10.00"),
    )


def _run(args):
    out = io.StringIO()
    call_command("load_opening_positions", *args, stdout=out)
    return out.getvalue()


class TestSetAccountOwners(TestCase):
    def setUp(self):
        self.account = _account()

    def _owners_cmd(self, *shares, apply=False):
        args = ["--account", "ACCOUNT_A"]
        for s in shares:
            args += ["--share", s]
        if apply:
            args.append("--apply")
        out = io.StringIO()
        call_command("set_account_owners", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        out = self._owners_cmd("Tom:60", "Susan:40")
        self.assertEqual(Owner.objects.count(), 0)
        self.assertEqual(AccountOwner.objects.count(), 0)
        self.assertIn("Dry run", out)
        self.assertIn("ACCOUNT_A", out)
        self.assertNotIn("90000001", out)

    def test_apply_creates_owners_and_shares(self):
        self._owners_cmd("Tom:60", "Susan:40", apply=True)
        self.assertEqual(Owner.objects.count(), 2)
        rows = AccountOwner.objects.filter(account=self.account).order_by("owner__name")
        self.assertEqual([r.owner.name for r in rows], ["Susan", "Tom"])
        self.assertEqual(rows[1].share_percent, Decimal("60"))

    def test_rerun_replaces_without_duplicates(self):
        self._owners_cmd("Tom:60", "Susan:40", apply=True)
        self._owners_cmd("Tom:50", "Susan:50", apply=True)
        rows = AccountOwner.objects.filter(account=self.account).order_by("owner__name")
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {r.owner.name: r.share_percent for r in rows},
            {"Tom": Decimal("50"), "Susan": Decimal("50")},
        )

    def test_refuses_shares_not_100(self):
        with self.assertRaises(CommandError) as ctx60:
            self._owners_cmd("Tom:60", "Susan:30")
        self.assertIn("90", str(ctx60.exception))  # printed total on refusal
        with self.assertRaises(CommandError):
            self._owners_cmd("Tom:50", "Susan:50.01")
        self.assertEqual(AccountOwner.objects.count(), 0)

    def test_refuses_malformed_share(self):
        for bad in ("Tom", "Tom:abc", ":40"):
            with self.assertRaises(CommandError):
                self._owners_cmd("Tom:60", bad)
        self.assertEqual(Owner.objects.count(), 0)

    def test_refuses_unknown_account(self):
        with self.assertRaises(CommandError):
            call_command(
                "set_account_owners",
                "--account",
                "NOPE",
                "--share",
                "Tom:100",
                stdout=io.StringIO(),
            )

    def test_owners_created_on_demand(self):
        self._owners_cmd("Susan:100", apply=True)
        self.assertTrue(Owner.objects.filter(name="Susan").exists())
        # A second account reuses the existing owner row.
        other = _account("ACCOUNT_B", "90000002")
        call_command(
            "set_account_owners",
            "--account",
            "ACCOUNT_B",
            "--share",
            "Susan:100",
            "--apply",
            stdout=io.StringIO(),
        )
        self.assertEqual(Owner.objects.filter(name="Susan").count(), 1)
        self.assertTrue(AccountOwner.objects.filter(account=other).exists())


class TestLoadOpeningPositionsSnapshot(TestCase):
    def setUp(self):
        self.account = _account()
        self.cad = _statement(self.account, "CAD")
        self.usd = _statement(self.account, "USD")
        self.h1 = _holding(
            self.cad, "VOO", "Vanguard S&P 500", "CAD",
            Decimal("10.0000"), Decimal("500.00"),
        )
        self.h2 = _holding(
            self.usd, "QQQ", "Invesco QQQ", "USD",
            Decimal("5.0000"), Decimal("300.00"),
        )

    def test_dry_run_writes_nothing(self):
        out = _run(["--account", "ACCOUNT_A", "--statement", STMT_DATE])
        self.assertEqual(OpeningPosition.objects.count(), 0)
        self.assertIn("Dry run", out)
        self.assertIn("ACCOUNT_A", out)
        self.assertIn("VOO", out)
        self.assertNotIn("90000001", out)

    def test_apply_writes_one_row_per_holding_both_sections(self):
        _run(
            ["--account", "ACCOUNT_A", "--statement", STMT_DATE, "--apply"]
        )
        rows = {
            r.symbol: r
            for r in OpeningPosition.objects.filter(account=self.account)
        }
        self.assertEqual(set(rows), {"VOO", "QQQ"})
        voo, qqq = rows["VOO"], rows["QQQ"]
        self.assertEqual(voo.symbol, "VOO")
        self.assertEqual(voo.currency, "CAD")
        self.assertEqual(voo.quantity, Decimal("10.0000"))
        self.assertEqual(voo.book_cost, Decimal("500.00"))
        self.assertEqual(voo.source, "RBC_BOOK_COST")
        self.assertEqual(voo.security_name, "Vanguard S&P 500")
        self.assertEqual(voo.statement, self.cad)
        self.assertIn(STMT_DATE, voo.note)
        self.assertEqual(qqq.symbol, "QQQ")
        self.assertEqual(qqq.currency, "USD")
        self.assertEqual(qqq.statement, self.usd)

    def test_rerun_refuses_and_replace_rewrites(self):
        _run(["--account", "ACCOUNT_A", "--statement", STMT_DATE, "--apply"])
        with self.assertRaises(CommandError):
            _run(["--account", "ACCOUNT_A", "--statement", STMT_DATE, "--apply"])
        # --replace rewrites cleanly: still one row per holding.
        out = _run(
            ["--account", "ACCOUNT_A", "--statement", STMT_DATE, "--apply", "--replace"]
        )
        rows = OpeningPosition.objects.filter(account=self.account)
        self.assertEqual(rows.count(), 2)
        self.assertNotIn("90000001", out)

    def test_refuses_missing_symbol(self):
        _holding(self.cad, "", "Mystery Fund", "CAD", Decimal("3.0000"), Decimal("50.00"))
        with self.assertRaises(CommandError) as ctx:
            _run(["--account", "ACCOUNT_A", "--statement", STMT_DATE])
        msg = str(ctx.exception)
        self.assertIn("Mystery Fund", msg)
        self.assertIn("CAD", msg)
        self.assertEqual(OpeningPosition.objects.count(), 0)

    def test_refuses_zero_book_cost(self):
        _holding(self.usd, "SPY", "SPDR S&P 500", "USD", Decimal("2.0000"), Decimal("0.00"))
        with self.assertRaises(CommandError) as ctx:
            _run(["--account", "ACCOUNT_A", "--statement", STMT_DATE])
        msg = str(ctx.exception)
        self.assertIn("SPY", msg)
        self.assertIn("USD", msg)
        self.assertEqual(OpeningPosition.objects.count(), 0)

    def test_refuses_transaction_on_or_before_statement_date(self):
        _tx(self.account, "2025-01-15")
        with self.assertRaises(CommandError) as ctx:
            _run(["--account", "ACCOUNT_A", "--statement", STMT_DATE])
        msg = str(ctx.exception)
        self.assertIn("1", msg)  # the count
        self.assertIn("2025-01-15", msg)
        self.assertEqual(OpeningPosition.objects.count(), 0)

    def test_replace_does_not_skip_transaction_guard(self):
        _run(["--account", "ACCOUNT_A", "--statement", STMT_DATE, "--apply"])
        _tx(self.account, "2025-01-31")
        with self.assertRaises(CommandError):
            _run(
                ["--account", "ACCOUNT_A", "--statement", STMT_DATE,
                 "--apply", "--replace"]
            )

    def test_accepts_transaction_settling_after_statement_date(self):
        _tx(self.account, "2025-02-10")
        _run(["--account", "ACCOUNT_A", "--statement", STMT_DATE, "--apply"])
        self.assertEqual(OpeningPosition.objects.filter(account=self.account).count(), 2)

    def test_refuses_no_statement_on_that_date(self):
        with self.assertRaises(CommandError):
            _run(["--account", "ACCOUNT_A", "--statement", "2024-12-31"])
        self.assertEqual(OpeningPosition.objects.count(), 0)

    def test_duplicate_symbol_across_sections_stored_and_printed(self):
        _holding(
            self.usd, "VOO", "Vanguard S&P 500 (USD)", "USD",
            Decimal("1.0000"), Decimal("90.00"),
        )
        out = _run(["--account", "ACCOUNT_A", "--statement", STMT_DATE, "--apply"])
        rows = OpeningPosition.objects.filter(account=self.account)
        self.assertEqual(rows.count(), 3)
        self.assertIn("VOO [CAD]", out)
        self.assertIn("VOO [USD]", out)


class TestLoadOpeningPositionsOverride(TestCase):
    def setUp(self):
        self.account = _account()
        self.cad = _statement(self.account, "CAD")
        _holding(
            self.cad, "VOO", "Vanguard S&P 500", "CAD",
            Decimal("10.0000"), Decimal("500.00"),
        )
        _run(["--account", "ACCOUNT_A", "--statement", STMT_DATE, "--apply"])
        self.row = OpeningPosition.objects.get(
            account=self.account, symbol="VOO", currency="CAD"
        )

    def test_dry_run_changes_nothing(self):
        out = _run(
            ["--account", "ACCOUNT_A", "--set", "VOO", "--currency", "CAD",
             "--book-cost", "480.50"]
        )
        self.row.refresh_from_db()
        self.assertEqual(self.row.book_cost, Decimal("500.00"))
        self.assertEqual(self.row.source, "RBC_BOOK_COST")
        self.assertIn("Dry run", out)
        self.assertNotIn("90000001", out)

    def test_apply_updates_book_cost_and_source_keeps_statement(self):
        _run(
            ["--account", "ACCOUNT_A", "--set", "VOO", "--currency", "CAD",
             "--book-cost", "480.50", "--note", "corrected", "--apply"]
        )
        self.row.refresh_from_db()
        self.assertEqual(self.row.book_cost, Decimal("480.50"))
        self.assertEqual(self.row.source, "MANUAL")
        self.assertEqual(self.row.note, "corrected")
        # The existing row's statement link is kept unchanged.
        self.assertEqual(self.row.statement, self.cad)

    def test_refuses_when_row_does_not_exist(self):
        with self.assertRaises(CommandError):
            _run(
                ["--account", "ACCOUNT_A", "--set", "NOPE", "--currency", "CAD",
                 "--book-cost", "1.00"]
            )

    def test_set_without_book_cost_refused(self):
        with self.assertRaises(CommandError):
            _run(["--account", "ACCOUNT_A", "--set", "VOO", "--currency", "CAD"])

    def test_statement_and_replace_not_allowed_with_set(self):
        with self.assertRaises(CommandError):
            _run(
                ["--account", "ACCOUNT_A", "--set", "VOO", "--currency", "CAD",
                 "--book-cost", "1.00", "--statement", STMT_DATE]
            )
        with self.assertRaises(CommandError):
            _run(
                ["--account", "ACCOUNT_A", "--set", "VOO", "--currency", "CAD",
                 "--book-cost", "1.00", "--replace"]
            )
