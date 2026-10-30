"""Tests for fill_from_pdf (phase 4, step 14-0d). Synthetic data only.

Fake accounts 90000001-90000008 (ACCOUNT_A ... ACCOUNT_H) per AGENTS.md.
Plan: docs/plans/fill-from-pdf.md (Decisions: tdw, Oct 30).
"""

import io
from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from core.models import (
    Account,
    BrokerStatement,
    RbcMonthly,
    RbcMonthlyBalance,
    RbcMonthlyHolding,
    RbcMonthlyTransaction,
    Transaction,
)
from core.utils.account_fingerprint import fingerprint_account_number
from core.utils.fill_from_pdf import derive_pdf_symbol
from core.utils.monthly_check import (
    check_statement,
    csv_coverage_bounds,
    group_statements,
    previous_groups,
)

JAN = date(2025, 1, 31)
FEB = date(2025, 2, 28)


def make_account(name, number):
    return Account.objects.create(
        name=name,
        fingerprint=fingerprint_account_number(number),
        last_4=number[-4:],
        broker="RBC",
    )


def make_tx(account, d, tx_type="DEP", amount=Decimal("0"), currency="CAD",
            symbol="", qty=Decimal("0"), settlement=None, notes="",
            filename="t.csv", price=Decimal("0")):
    stmt = BrokerStatement.objects.create(
        source="RBC", file_date=d, raw_filename=filename
    )
    return Transaction.objects.create(
        statement=stmt,
        account=account,
        date=d,
        settlement_date=settlement,
        symbol=symbol,
        type=tx_type,
        quantity=qty,
        price=price,
        commission=Decimal("0"),
        amount=amount,
        currency=currency,
        notes=notes,
    )


def make_monthly(account, d, currency="CAD", last_stmt=None,
                 begin=Decimal("0"), end=Decimal("0")):
    return RbcMonthly.objects.create(
        account=account,
        file_name=f"{d.isoformat()}_monthly_{account.name}_aabbccdd.pdf",
        statement_date=d,
        currency=currency,
        last_statement_date=last_stmt,
        begin_total_market_value=begin,
        end_total_market_value=end,
    )


def cash(rbc, closing, currency="CAD"):
    RbcMonthlyBalance.objects.create(
        rbc_monthly=rbc,
        currency=currency,
        account_type="Cash",
        opening_balance=closing,
        closing_balance=closing,
        market_value=closing,
    )


def holding(rbc, symbol, qty, currency="CAD"):
    RbcMonthlyHolding.objects.create(
        rbc_monthly=rbc,
        currency=currency,
        security=symbol,
        symbol=symbol,
        quantity=qty,
    )


def pdf_tx(feb, d, activity_type, **kw):
    defaults = dict(
        debit=Decimal("0"), credit=Decimal("0"), quantity=Decimal("0"),
        price=Decimal("0"), currency="CAD", symbol="", description="",
    )
    defaults.update(kw)
    return RbcMonthlyTransaction.objects.create(rbc_monthly=feb, date=d,
                                                activity_type=activity_type,
                                                **defaults)


def feb_check(account):
    """The CAD CurrencyResult of the February statement (fresh DB check)."""
    groups = group_statements(Account.objects.filter(pk=account.pk))
    prev_of = previous_groups(groups)
    bounds = csv_coverage_bounds()
    group = next(g for g in groups if g.statement_date == FEB)
    check = check_statement(group, prev_of[id(group)], bounds)
    return next(c for c in check.currencies if c.currency == "CAD")


def run_cmd(*args):
    out = io.StringIO()
    call_command("fill_from_pdf", "--account", "ACCOUNT_A", *args, stdout=out)
    return out.getvalue()


def kinds_of(result):
    """Unique failure kinds of a CurrencyResult (line prefix before ':')."""
    return sorted({f.split(":", 1)[0].strip() for f in result.failures})


def fill_statements():
    return BrokerStatement.objects.filter(raw_filename__startswith="pdf-fill-")


class FillFromPdfTest(TestCase):
    """Shared shape: January anchor statement + a failing February."""

    def setUp(self):
        self.account = make_account("ACCOUNT_A", "90000001")
        jan = make_monthly(self.account, JAN, begin=Decimal("200"),
                           end=Decimal("250"))
        cash(jan, Decimal("100"))
        holding(jan, "ACME", Decimal("100"))

    def feb(self, closing, acme=None):
        feb = make_monthly(self.account, FEB, last_stmt=JAN,
                           begin=Decimal("250"), end=Decimal("300"))
        cash(feb, closing)
        if acme is not None:
            holding(feb, "ACME", acme)
        return feb

    def base_period(self, feb):
        """A DEP 50 in the CSV with a matching PDF DEPOSIT row (coverage)."""
        make_tx(self.account, date(2025, 2, 5), "DEP", Decimal("50"),
                settlement=date(2025, 2, 7))
        pdf_tx(feb, date(2025, 2, 5), "DEPOSIT", credit=Decimal("50"),
               description="TEST DEPOSIT")

    # ------------------------------------------------------------------
    # 1. Missing SELL filled and the period passes
    # ------------------------------------------------------------------

    def test_missing_sell_filled_and_period_passes(self):
        feb = self.feb(Decimal("550"), acme=Decimal("60"))
        self.base_period(feb)
        pdf_tx(feb, date(2025, 2, 12), "SOLD", credit=Decimal("400"),
               quantity=Decimal("40"), price=Decimal("10"), symbol="ACME",
               description="SELL ACME")

        res = feb_check(self.account)
        self.assertEqual(res.verdict, "FAIL")

        text = run_cmd("--from", "2025-02", "--to", "2025-02", "--apply")
        self.assertIn("filled 1", text)

        sell = Transaction.objects.get(type="SELL", symbol="ACME")
        self.assertEqual(sell.quantity, Decimal("-40"))
        self.assertEqual(sell.amount, Decimal("400.00"))
        self.assertEqual(sell.date, date(2025, 2, 12))
        self.assertEqual(sell.settlement_date, date(2025, 2, 12))
        self.assertIn("source=pdf-fill", sell.notes)

        stmt = fill_statements().get()
        self.assertTrue(stmt.raw_filename.startswith("pdf-fill-"))
        self.assertEqual(stmt.transactions.count(), 1)
        self.assertEqual(stmt.period_start, date(2025, 2, 12))
        self.assertEqual(stmt.period_end, date(2025, 2, 12))

        res = feb_check(self.account)
        self.assertEqual(res.verdict, "PASS")
        self.assertEqual(res.failures, [])

    # ------------------------------------------------------------------
    # 2. Period still failing after the fill is left unchanged
    # ------------------------------------------------------------------

    def test_still_failing_after_fill_left_unchanged(self):
        feb = self.feb(Decimal("700"), acme=Decimal("60"))  # wrong closing
        self.base_period(feb)
        pdf_tx(feb, date(2025, 2, 12), "SOLD", credit=Decimal("400"),
               quantity=Decimal("40"), price=Decimal("10"), symbol="ACME",
               description="SELL ACME")

        before = Transaction.objects.count()
        text = run_cmd("--from", "2025-02", "--to", "2025-02", "--apply")
        # The in-memory simulation already fails, so the period is never
        # applied and nothing changes.
        self.assertIn("left unchanged (after-check still fails)", text)

        self.assertEqual(Transaction.objects.count(), before)
        self.assertFalse(Transaction.objects.filter(type="SELL").exists())
        # Decision 4: the empty fill statement is deleted again.
        self.assertEqual(fill_statements().count(), 0)
        self.assertEqual(feb_check(self.account).verdict, "FAIL")

    # ------------------------------------------------------------------
    # 3. Slice row with a wrong sign/amount is removed and replaced
    # ------------------------------------------------------------------

    def test_slice_row_removed_and_replaced(self):
        feb = self.feb(Decimal("550"), acme=Decimal("60"))
        self.base_period(feb)
        pdf_tx(feb, date(2025, 2, 12), "SOLD", credit=Decimal("400"),
               quantity=Decimal("40"), price=Decimal("10"), symbol="ACME",
               description="SELL ACME")
        # The cleaned-CSV slice row has the wrong sign/amount.
        slice_sell = make_tx(
            self.account, date(2025, 2, 12), "SELL", Decimal("-200"),
            symbol="ACME", qty=Decimal("-40"), price=Decimal("5"),
            settlement=date(2025, 2, 12),
            filename="rbc-act-cleaned-slice.csv",
        )

        res = feb_check(self.account)
        self.assertEqual(res.verdict, "FAIL")
        # Only CASH fails: the slice row's quantity is right, its amount wrong.
        self.assertEqual(kinds_of(res), ["CASH"])

        text = run_cmd("--from", "2025-02", "--to", "2025-02", "--apply")
        self.assertIn("remove:", text)
        self.assertIn("filled 1", text)

        self.assertFalse(Transaction.objects.filter(pk=slice_sell.pk).exists())
        sells = Transaction.objects.filter(type="SELL", symbol="ACME")
        self.assertEqual(sells.count(), 1)
        self.assertEqual(sells.first().amount, Decimal("400.00"))
        res = feb_check(self.account)
        self.assertEqual(res.verdict, "PASS")

    # ------------------------------------------------------------------
    # 4. NRT row splits into income + TAX
    # ------------------------------------------------------------------

    def test_nrt_row_splits_into_income_and_tax(self):
        jan = RbcMonthly.objects.get(account=self.account, statement_date=JAN)
        RbcMonthlyHolding.objects.filter(rbc_monthly=jan).delete()
        # 100 start + 50 DEP + 100 DIV - 15 NRT
        feb = self.feb(Decimal("235"))
        self.base_period(feb)
        pdf_tx(feb, date(2025, 2, 15), "DIVIDEND", credit=Decimal("100"),
               debit=Decimal("15"), symbol="ACME",
               description="DIV ON 100 SHS (NRT)")

        res = feb_check(self.account)
        self.assertEqual(res.verdict, "FAIL")

        text = run_cmd("--from", "2025-02", "--to", "2025-02", "--apply")
        self.assertIn("filled 1", text)

        div = Transaction.objects.get(type="DIV", symbol="ACME")
        tax = Transaction.objects.get(type="TAX")
        self.assertEqual(div.amount, Decimal("100.00"))
        self.assertEqual(tax.amount, Decimal("-15.00"))
        self.assertEqual(div.date, date(2025, 2, 15))
        self.assertEqual(tax.date, date(2025, 2, 15))

        res = feb_check(self.account)
        self.assertEqual(res.verdict, "PASS")

    # ------------------------------------------------------------------
    # 5. Unmapped activity (ADJUST) stops the period
    # ------------------------------------------------------------------

    def test_unmapped_activity_stops_period(self):
        feb = self.feb(Decimal("550"), acme=Decimal("60"))
        self.base_period(feb)
        pdf_tx(feb, date(2025, 2, 12), "SOLD", credit=Decimal("400"),
               quantity=Decimal("40"), price=Decimal("10"), symbol="ACME",
               description="SELL ACME")
        # An ADJUST row with net cash: unmatched and unmapped -> stops.
        pdf_tx(feb, date(2025, 2, 20), "ADJUST", debit=Decimal("10"),
               description="BOOK COST ADJUSTMENT")

        before = Transaction.objects.count()
        text = run_cmd("--from", "2025-02", "--to", "2025-02")
        self.assertIn("STOPPED (unmapped activity)", text)
        self.assertIn("ADJUST", text)
        self.assertEqual(Transaction.objects.count(), before)

        text = run_cmd("--from", "2025-02", "--to", "2025-02", "--apply")
        self.assertIn("STOPPED (unmapped activity)", text)
        self.assertFalse(Transaction.objects.filter(type="SELL").exists())
        self.assertEqual(Transaction.objects.count(), before)
        self.assertEqual(fill_statements().count(), 0)

    # ------------------------------------------------------------------
    # 6. A second --apply run adds nothing
    # ------------------------------------------------------------------

    def test_second_apply_run_adds_nothing(self):
        feb = self.feb(Decimal("550"), acme=Decimal("60"))
        self.base_period(feb)
        pdf_tx(feb, date(2025, 2, 12), "SOLD", credit=Decimal("400"),
               quantity=Decimal("40"), price=Decimal("10"), symbol="ACME",
               description="SELL ACME")

        run_cmd("--from", "2025-02", "--to", "2025-02", "--apply")
        count = Transaction.objects.count()
        hashes = set(
            Transaction.objects.values_list("tx_hash", flat=True)
        )
        self.assertEqual(fill_statements().count(), 1)

        text = run_cmd("--from", "2025-02", "--to", "2025-02", "--apply")
        self.assertIn("failing periods in window: 0", text)
        self.assertEqual(Transaction.objects.count(), count)
        self.assertEqual(
            set(Transaction.objects.values_list("tx_hash", flat=True)), hashes
        )
        # The second run's empty fill statement was deleted again.
        self.assertEqual(fill_statements().count(), 1)

    # ------------------------------------------------------------------
    # 7. The dry run writes nothing
    # ------------------------------------------------------------------

    def test_dry_run_writes_nothing(self):
        feb = self.feb(Decimal("550"), acme=Decimal("60"))
        self.base_period(feb)
        pdf_tx(feb, date(2025, 2, 12), "SOLD", credit=Decimal("400"),
               quantity=Decimal("40"), price=Decimal("10"), symbol="ACME",
               description="SELL ACME")

        count = Transaction.objects.count()
        hashes = set(
            Transaction.objects.values_list("tx_hash", flat=True)
        )
        text = run_cmd("--from", "2025-02", "--to", "2025-02")
        self.assertIn("dry run", text)
        self.assertIn("would fill 1", text)
        self.assertIn("add:", text)

        self.assertEqual(Transaction.objects.count(), count)
        self.assertEqual(
            set(Transaction.objects.values_list("tx_hash", flat=True)), hashes
        )
        self.assertEqual(fill_statements().count(), 0)
        self.assertEqual(feb_check(self.account).verdict, "FAIL")

    # ------------------------------------------------------------------
    # Decision 1: zero-net rows with a quantity are matched by
    # date/symbol/quantity and filled when unmatched
    # ------------------------------------------------------------------

    def test_zero_net_units_filled_by_quantity_match(self):
        jan = RbcMonthly.objects.get(account=self.account, statement_date=JAN)
        # Replace the ACME anchor holding with savings-fund units.
        RbcMonthlyHolding.objects.filter(rbc_monthly=jan).delete()
        holding(jan, "RBF2010", Decimal("100"))
        feb = self.feb(Decimal("100"), acme=None)
        holding(feb, "RBF2010", Decimal("105"))
        # A zero-cash settlement inside the period keeps it out of NO_CSV.
        make_tx(self.account, date(2025, 2, 3), "DEP", Decimal("0"),
                settlement=date(2025, 2, 7))
        # The PDF shows 5 reinvested units with all money columns zero.
        pdf_tx(feb, date(2025, 2, 1), "INTEREST_RECEIVED",
               quantity=Decimal("5"), symbol="RBF2010",
               description="INT REINVESTED RBF2010")

        res = feb_check(self.account)
        self.assertEqual(res.verdict, "FAIL")
        self.assertIn("QUANTITY", kinds_of(res))

        text = run_cmd("--from", "2025-02", "--to", "2025-02", "--apply")
        self.assertIn("filled 1", text)

        int_tx = Transaction.objects.get(type="INT", symbol="RBF2010")
        self.assertEqual(int_tx.quantity, Decimal("5"))
        self.assertEqual(int_tx.amount, Decimal("0.00"))
        self.assertEqual(int_tx.date, date(2025, 2, 1))

        res = feb_check(self.account)
        self.assertEqual(res.verdict, "PASS")

    # ------------------------------------------------------------------
    # Real-data fix (Oct 30) 2: symbol-less PDF rows take the symbol from
    # the holdings of the same / previous stored statement
    # ------------------------------------------------------------------

    VFV_NAME = "VANGUARD FTSE CDN HIGH DIVID YIELD INDEX ETF TR UNIT"

    def named_holding(self, rbc, security, symbol, qty):
        return RbcMonthlyHolding.objects.create(
            rbc_monthly=rbc, currency="CAD", security=security,
            symbol=symbol, quantity=qty,
        )

    def test_derive_symbol_from_same_statement_holding(self):
        feb = self.feb(Decimal("100"))
        self.named_holding(feb, self.VFV_NAME, "VFV", Decimal("10"))
        pdf = pdf_tx(feb, date(2025, 2, 12), "SOLD", credit=Decimal("400"),
                     quantity=Decimal("40"),
                     description=self.VFV_NAME)
        self.assertEqual(derive_pdf_symbol(pdf), "VFV")

    def test_derive_symbol_falls_back_to_previous_statement(self):
        jan = RbcMonthly.objects.get(account=self.account, statement_date=JAN)
        feb = self.feb(Decimal("100"))  # no matching holding in February
        self.named_holding(jan, self.VFV_NAME, "VFV", Decimal("10"))
        pdf = pdf_tx(feb, date(2025, 2, 12), "SOLD", credit=Decimal("400"),
                     quantity=Decimal("40"),
                     description=self.VFV_NAME)
        self.assertEqual(derive_pdf_symbol(pdf), "")
        self.assertEqual(derive_pdf_symbol(pdf, jan), "VFV")

    def test_derive_symbol_never_guesses_ambiguous_or_explicit(self):
        feb = self.feb(Decimal("100"))
        # Two holdings whose names both start with the description: no guess.
        self.named_holding(feb, "VANGUARD FTSE ALL-WORLD INDEX ETF",
                           "VWC", Decimal("10"))
        self.named_holding(feb, "VANGUARD FTSE 50 INDEX ETF",
                           "VEF", Decimal("10"))
        pdf = pdf_tx(feb, date(2025, 2, 12), "SOLD", credit=Decimal("400"),
                     quantity=Decimal("40"), description="VANGUARD FTSE")
        self.assertEqual(derive_pdf_symbol(pdf), "")
        # An explicit symbol is always kept as-is.
        pdf.symbol = "ACME"
        self.assertEqual(derive_pdf_symbol(pdf), "ACME")

    def test_symbolless_sell_gets_symbol_from_holdings(self):
        jan = RbcMonthly.objects.get(account=self.account, statement_date=JAN)
        RbcMonthlyHolding.objects.filter(rbc_monthly=jan).delete()
        self.named_holding(jan, self.VFV_NAME, "VFV", Decimal("100"))
        feb = self.feb(Decimal("550"))
        self.named_holding(feb, self.VFV_NAME, "VFV", Decimal("60"))
        self.base_period(feb)
        pdf_tx(feb, date(2025, 2, 12), "SOLD", credit=Decimal("400"),
               quantity=Decimal("40"), price=Decimal("10"),
               description=self.VFV_NAME)

        res = feb_check(self.account)
        self.assertEqual(res.verdict, "FAIL")

        text = run_cmd("--from", "2025-02", "--to", "2025-02", "--apply")
        self.assertIn("filled 1", text)

        sell = Transaction.objects.get(type="SELL")
        self.assertEqual(sell.symbol, "VFV")
        res = feb_check(self.account)
        self.assertEqual(res.verdict, "PASS")

    def test_symbolless_row_without_holding_match_fails_after_check(self):
        # No holding matches the description: the row is filled without a
        # symbol, the after-check fails, and the period stays unchanged.
        feb = self.feb(Decimal("550"), acme=Decimal("60"))
        self.base_period(feb)
        pdf_tx(feb, date(2025, 2, 12), "SOLD", credit=Decimal("400"),
               quantity=Decimal("40"), price=Decimal("10"),
               description="SELL SOME UNKNOWN FUND")

        before = Transaction.objects.count()
        text = run_cmd("--from", "2025-02", "--to", "2025-02", "--apply")
        self.assertIn("left unchanged (after-check still fails)", text)
        self.assertEqual(Transaction.objects.count(), before)

    def test_zero_net_units_already_in_csv_are_not_filled(self):
        jan = RbcMonthly.objects.get(account=self.account, statement_date=JAN)
        RbcMonthlyHolding.objects.filter(rbc_monthly=jan).delete()
        holding(jan, "RBF2010", Decimal("100"))
        feb = self.feb(Decimal("100"), acme=None)
        holding(feb, "RBF2010", Decimal("105"))
        # The CSV already has the units: matched by quantity, not a candidate.
        make_tx(self.account, date(2025, 2, 1), "INT", Decimal("0"),
                symbol="RBF2010", qty=Decimal("5"),
                settlement=date(2025, 2, 1))
        pdf_tx(feb, date(2025, 2, 1), "INTEREST_RECEIVED",
               quantity=Decimal("5"), symbol="RBF2010",
               description="INT REINVESTED RBF2010")

        res = feb_check(self.account)
        self.assertEqual(res.verdict, "PASS")
        text = run_cmd("--from", "2025-02", "--to", "2025-02")
        self.assertIn("failing periods in window: 0", text)
