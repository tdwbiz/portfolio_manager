"""Tests for monthly_check (phase 4, step 14a). Synthetic data only.

Fake accounts 90000001-90000008 (ACCOUNT_A ... ACCOUNT_H) per AGENTS.md.
Plan: docs/plans/monthly-check-report.md.
"""

from datetime import date
from decimal import Decimal

import pytest

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
from core.utils.monthly_check import (
    check_statement,
    csv_coverage_bounds,
    group_statements,
    previous_groups,
)


def make_account(name, number):
    return Account.objects.create(
        name=name,
        fingerprint=fingerprint_account_number(number),
        last_4=number[-4:],
        broker="RBC",
    )


@pytest.fixture
def account():
    return make_account("ACCOUNT_A", "90000001")


def make_tx(account, d, tx_type="DEP", amount=Decimal("0"), currency="CAD",
            symbol="", qty=Decimal("0"), settlement=None, notes=""):
    stmt = BrokerStatement.objects.create(
        source="RBC", file_date=d, raw_filename="t.csv"
    )
    return Transaction.objects.create(
        statement=stmt,
        account=account,
        date=d,
        settlement_date=settlement,
        symbol=symbol,
        type=tx_type,
        quantity=qty,
        price=Decimal("0"),
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


def cash(rbc, closing, currency="CAD", opening=None):
    RbcMonthlyBalance.objects.create(
        rbc_monthly=rbc,
        currency=currency,
        account_type="Cash",
        opening_balance=opening if opening is not None else closing,
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


def run_checks(account=None):
    """Run the read-only check for every statement; key (name, date)."""
    accounts = (
        Account.objects.all()
        if account is None
        else Account.objects.filter(pk=account.pk)
    )
    groups = group_statements(accounts)
    prev_of = previous_groups(groups)
    bounds = csv_coverage_bounds()
    return {
        (g.account_name, g.statement_date): check_statement(
            g, prev_of[id(g)], bounds
        )
        for g in groups
    }


def cad(checks, name, d):
    """The CAD CurrencyResult of one statement."""
    check = checks[(name, d)]
    return next(c for c in check.currencies if c.currency == "CAD")


def kinds_of(result):
    """Unique failure kinds of a CurrencyResult (line prefix before ':')."""
    return sorted({f.split(":", 1)[0].strip() for f in result.failures})


@pytest.mark.django_db
def test_pass_month(account):
    jan = make_monthly(account, date(2025, 1, 31), begin=Decimal("100"),
                       end=Decimal("200"))
    cash(jan, Decimal("100"))
    feb = make_monthly(account, date(2025, 2, 28), last_stmt=date(2025, 1, 31),
                       begin=Decimal("200"), end=Decimal("250"))
    cash(feb, Decimal("150"))
    make_tx(account, date(2025, 2, 10), "DEP", Decimal("50"),
            settlement=date(2025, 2, 12))

    checks = run_checks()
    assert cad(checks, "ACCOUNT_A", date(2025, 1, 31)).verdict == "FIRST"
    feb_res = cad(checks, "ACCOUNT_A", date(2025, 2, 28))
    assert feb_res.verdict == "PASS"
    assert feb_res.failures == []


@pytest.mark.django_db
def test_cash_fail_shows_exactly_one_missing_row(account):
    jan = make_monthly(account, date(2025, 1, 31), end=Decimal("200"))
    cash(jan, Decimal("100"))
    feb = make_monthly(account, date(2025, 2, 28), last_stmt=date(2025, 1, 31),
                       begin=Decimal("200"), end=Decimal("250"))
    cash(feb, Decimal("150"))
    make_tx(account, date(2025, 2, 10), "DEP", Decimal("50"),
            settlement=date(2025, 2, 12))
    # The dividend has no matching PDF row: exactly one unmatched CSV row.
    make_tx(account, date(2025, 2, 15), "DIV", Decimal("25"), symbol="ACME",
            settlement=date(2025, 2, 17))
    RbcMonthlyTransaction.objects.create(
        rbc_monthly=feb, date=date(2025, 2, 12), activity_type="DEPOSIT",
        description="TEST DEPOSIT", debit=Decimal("0"), credit=Decimal("50"),
        currency="CAD",
    )

    res = cad(run_checks(), "ACCOUNT_A", date(2025, 2, 28))
    assert res.verdict == "FAIL"
    cash_lines = [f for f in res.failures if f.startswith("CASH")]
    assert any("diff -25" in f for f in cash_lines)
    unmatched_csv = [f for f in cash_lines if "unmatched CSV row" in f]
    unmatched_pdf = [f for f in cash_lines if "unmatched PDF row" in f]
    assert len(unmatched_csv) == 1 and "ACME" in unmatched_csv[0]
    assert unmatched_pdf == []


@pytest.mark.django_db
def test_quantity_fail(account):
    jan = make_monthly(account, date(2025, 1, 31), end=Decimal("200"))
    cash(jan, Decimal("100"))
    holding(jan, "ACME", Decimal("100"))
    feb = make_monthly(account, date(2025, 2, 28), last_stmt=date(2025, 1, 31),
                       begin=Decimal("200"), end=Decimal("250"))
    cash(feb, Decimal("500"))
    holding(feb, "ACME", Decimal("50"))  # should be 60 after the SELL of 40
    make_tx(account, date(2025, 2, 10), "SELL", Decimal("400"), symbol="ACME",
            qty=Decimal("-40"), settlement=date(2025, 2, 12))

    res = cad(run_checks(), "ACCOUNT_A", date(2025, 2, 28))
    assert res.verdict == "FAIL"
    assert kinds_of(res) == ["QUANTITY"]
    qty_lines = [f for f in res.failures if f.startswith("QUANTITY")]
    assert len(qty_lines) == 1 and "ACME" in qty_lines[0]


@pytest.mark.django_db
def test_gap_month_with_matching_chain_passes(account):
    jan = make_monthly(account, date(2025, 1, 31), end=Decimal("200"))
    cash(jan, Decimal("100"))
    # Feb and Mar have no statements; the April statement chains to January.
    apr = make_monthly(account, date(2025, 4, 30), last_stmt=date(2025, 1, 31),
                       begin=Decimal("200"), end=Decimal("210"))
    cash(apr, Decimal("110"))
    make_tx(account, date(2025, 4, 15), "DEP", Decimal("10"),
            settlement=date(2025, 4, 17))

    res = cad(run_checks(), "ACCOUNT_A", date(2025, 4, 30))
    assert res.verdict == "PASS"


@pytest.mark.django_db
def test_chain_break_fails(account):
    jan = make_monthly(account, date(2025, 1, 31), end=Decimal("200"))
    cash(jan, Decimal("100"))
    # begin_total_market_value does not match January's end.
    feb = make_monthly(account, date(2025, 2, 28), last_stmt=date(2025, 1, 31),
                       begin=Decimal("190"), end=Decimal("200"))
    cash(feb, Decimal("110"))
    make_tx(account, date(2025, 2, 10), "DEP", Decimal("10"),
            settlement=date(2025, 2, 12))

    res = cad(run_checks(), "ACCOUNT_A", date(2025, 2, 28))
    assert res.verdict == "FAIL"
    assert kinds_of(res) == ["CHAIN"]


@pytest.mark.django_db
def test_no_csv_before_first_and_after_last_settlement():
    # Global coverage bounds (decision 2): one transaction on ACCOUNT_B in
    # March 2025 defines the whole window.
    acct_b = make_account("ACCOUNT_B", "90000002")
    make_monthly(acct_b, date(2024, 12, 31))
    make_monthly(acct_b, date(2025, 3, 31), last_stmt=date(2024, 12, 31))
    make_monthly(acct_b, date(2025, 6, 30), last_stmt=date(2025, 3, 31))
    # ACCOUNT_D has no transactions at all; its quiet months must NOT be
    # NO_CSV while they sit inside the global window's neighbours' coverage.
    acct_d = make_account("ACCOUNT_D", "90000004")
    make_monthly(acct_d, date(2025, 1, 31))
    make_monthly(acct_d, date(2025, 2, 28), last_stmt=date(2025, 1, 31))

    make_tx(acct_b, date(2025, 3, 15), "DEP", Decimal("10"),
            settlement=date(2025, 3, 17))

    checks = run_checks()
    assert cad(checks, "ACCOUNT_B", date(2024, 12, 31)).verdict == "FIRST"
    # Period (Dec 31, Mar 31] contains the only settlement: in coverage.
    assert cad(checks, "ACCOUNT_B", date(2025, 3, 31)).verdict != "NO_CSV"
    # Period (Mar 31, Jun 30] is after the last settlement: NO_CSV.
    assert cad(checks, "ACCOUNT_B", date(2025, 6, 30)).verdict == "NO_CSV"
    # ACCOUNT_D's period (Jan 31, Feb 28] ends before the first settlement.
    assert cad(checks, "ACCOUNT_D", date(2025, 2, 28)).verdict == "NO_CSV"


@pytest.mark.django_db
def test_first_statement_is_first(account):
    stmt = make_monthly(account, date(2025, 1, 31), begin=Decimal("100"),
                        end=Decimal("200"))
    cash(stmt, Decimal("100"))
    usd = make_monthly(account, date(2025, 1, 31), currency="USD",
                       begin=Decimal("0"), end=Decimal("0"))
    cash(usd, Decimal("0"), currency="USD")

    check = run_checks()[("ACCOUNT_A", date(2025, 1, 31))]
    assert [c.verdict for c in check.currencies] == ["FIRST", "FIRST"]


@pytest.mark.django_db
def test_dlr_journal_passes_when_pooled_per_account(account):
    # DLR currency journal: 100 shares move from the USD side to the CAD
    # side via a paired SELL+BUY (net zero). Pooled per account this passes;
    # per-currency it would not.
    jan_cad = make_monthly(account, date(2025, 1, 31), begin=Decimal("300"),
                           end=Decimal("300"))
    cash(jan_cad, Decimal("1200"))
    jan_usd = make_monthly(account, date(2025, 1, 31), currency="USD",
                           begin=Decimal("400"), end=Decimal("400"))
    cash(jan_usd, Decimal("50"), currency="USD")
    holding(jan_usd, "DLR", Decimal("100"), currency="USD")

    feb_cad = make_monthly(account, date(2025, 2, 28), last_stmt=date(2025, 1, 31),
                           begin=Decimal("300"), end=Decimal("300"))
    cash(feb_cad, Decimal("200"))
    holding(feb_cad, "DLR", Decimal("100"))
    feb_usd = make_monthly(account, date(2025, 2, 28), currency="USD",
                           last_stmt=date(2025, 1, 31),
                           begin=Decimal("400"), end=Decimal("400"))
    cash(feb_usd, Decimal("1050"), currency="USD")

    make_tx(account, date(2025, 2, 10), "SELL", Decimal("1000"), symbol="DLR",
            qty=Decimal("-100"), currency="USD", settlement=date(2025, 2, 12))
    make_tx(account, date(2025, 2, 10), "BUY", Decimal("-1000"), symbol="DLR",
            qty=Decimal("100"), currency="CAD", settlement=date(2025, 2, 12))

    check = run_checks()[("ACCOUNT_A", date(2025, 2, 28))]
    assert [c.verdict for c in check.currencies] == ["PASS", "PASS"]


@pytest.mark.django_db
def test_income_row_uses_payment_date_from_notes(account):
    # RBC books the year-end dividend in the December statement; the CSV
    # dates it Jan 2 with settlement Jan 4, but the notes carry PAY 12/31.
    nov = make_monthly(account, date(2021, 11, 30), end=Decimal("100"))
    cash(nov, Decimal("100"))
    dec = make_monthly(account, date(2021, 12, 31), last_stmt=date(2021, 11, 30),
                       begin=Decimal("100"), end=Decimal("140"))
    cash(dec, Decimal("140"))
    make_tx(account, date(2021, 12, 15), "DEP", Decimal("25"),
            settlement=date(2021, 12, 17))
    # Paid Dec 31: belongs to December although the CSV dates it in January.
    make_tx(account, date(2021, 12, 31), "DIV", Decimal("15"), symbol="ACME",
            settlement=date(2022, 1, 4),
            notes="REC 12/21/21 PAY 12/31/21")
    # Paid Jan 15: even though it settles in December, it belongs to
    # January and must NOT be counted here.
    make_tx(account, date(2021, 12, 20), "DIV", Decimal("10"), symbol="ACME",
            settlement=date(2021, 12, 22),
            notes="REC 12/21/21 PAY 01/15/22")

    res = cad(run_checks(), "ACCOUNT_A", date(2021, 12, 31))
    assert res.verdict == "PASS"
    assert res.failures == []


@pytest.mark.django_db
def test_settlement_date_after_statement_date(account):
    # A trade dated Jan 30 settles Mar 3: it belongs to the March statement.
    # On settlement-date membership both Feb and Mar pass; on trade-date
    # membership the cash sums would break.
    jan = make_monthly(account, date(2025, 1, 31), end=Decimal("105"))
    cash(jan, Decimal("105"))
    feb = make_monthly(account, date(2025, 2, 28), last_stmt=date(2025, 1, 31),
                       begin=Decimal("105"), end=Decimal("105"))
    cash(feb, Decimal("105"))
    mar = make_monthly(account, date(2025, 3, 31), last_stmt=date(2025, 2, 28),
                       begin=Decimal("105"), end=Decimal("205"))
    cash(mar, Decimal("205"))

    make_tx(account, date(2025, 1, 10), "DEP", Decimal("5"),
            settlement=date(2025, 1, 12))
    make_tx(account, date(2025, 1, 30), "DEP", Decimal("100"),
            settlement=date(2025, 3, 3))

    checks = run_checks()
    feb_res = cad(checks, "ACCOUNT_A", date(2025, 2, 28))
    mar_res = cad(checks, "ACCOUNT_A", date(2025, 3, 31))
    assert feb_res.verdict == "PASS" and feb_res.failures == []
    assert mar_res.verdict == "PASS" and mar_res.failures == []
