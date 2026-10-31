"""Tests for the one forward statement generator (phase 4, step 14d).

Synthetic data only — fake accounts 90000001-90000008 (ACCOUNT_A ...
ACCOUNT_H) per AGENTS.md. Covers the five scenarios of step 8 in
docs/plans/statements-forward.md: a gap month; a new currency section; a
DLR journal across two sections (nets to zero per account); a FAIL month
that differs from the PDF by exactly its exception; and a re-run that
changes nothing (byte-identical rows).
"""

from datetime import date
from decimal import Decimal

import pytest
from django.core.management import call_command

from core.models import (
    Account,
    BrokerStatement,
    CoreMonthlyStatement,
    CoreMonthlyStatementPosition,
    RbcMonthly,
    RbcMonthlyBalance,
    Transaction,
)
from core.utils.account_fingerprint import fingerprint_account_number
from core.utils.statements import generate_forward_statements


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
            symbol="", qty=Decimal("0"), settlement=None):
    stmt = BrokerStatement.objects.create(
        source="RBC", file_date=d, raw_filename="t.csv"
    )
    return Transaction.objects.create(
        statement=stmt,
        account=account,
        date=d,
        settlement_date=settlement or d,
        symbol=symbol,
        type=tx_type,
        quantity=qty,
        price=Decimal("0"),
        commission=Decimal("0"),
        amount=amount,
        currency=currency,
    )


def make_monthly(account, d, currency="CAD", last_stmt=None,
                 begin=Decimal("0"), end=Decimal("0")):
    return RbcMonthly.objects.create(
        account=account,
        file_name=f"{d.isoformat()}_monthly_{account.name}_{currency}_aabbccdd.pdf",
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


def holding(rbc, symbol, qty, price, value, currency="CAD"):
    from core.models import RbcMonthlyHolding

    RbcMonthlyHolding.objects.create(
        rbc_monthly=rbc,
        currency=currency,
        security=symbol,
        symbol=symbol,
        quantity=qty,
        market_price=price,
        market_value=value,
    )


def row_by_date(rows, d, currency="CAD"):
    return next(r for r in rows if r.statement_date == d and r.currency == currency)


@pytest.mark.django_db
def test_gap_month(account):
    # January and March PDF statements; February has no statement and no
    # transactions -> a GAP row at the calendar month end, chained from
    # January. The March REPLAY row takes the deposit.
    jan = make_monthly(account, date(2025, 1, 31), begin=Decimal("100"),
                       end=Decimal("200"))
    cash(jan, Decimal("200"))
    mar = make_monthly(account, date(2025, 3, 31), last_stmt=date(2025, 1, 31),
                       begin=Decimal("200"), end=Decimal("250"))
    cash(mar, Decimal("250"))
    make_tx(account, date(2025, 3, 10), "DEP", Decimal("50"),
            settlement=date(2025, 3, 12))

    rows, report = generate_forward_statements(account)

    assert [r.statement_date for r in rows] == [
        date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31)]
    assert report["counts"] == {"REPLAY": 1, "PDF": 1, "GAP": 1}

    pdf_row = row_by_date(rows, date(2025, 1, 31))
    assert pdf_row.source == "PDF"
    assert (pdf_row.beginning_market_value, pdf_row.ending_market_value) == (
        Decimal("100"), Decimal("200"))

    gap = row_by_date(rows, date(2025, 2, 28))
    assert gap.source == "GAP"
    assert gap.statement_month == date(2025, 2, 1)
    # Carried forward from January: begin = end = previous ending.
    assert (gap.beginning_market_value, gap.ending_market_value) == (
        Decimal("200"), Decimal("200"))
    assert gap.ending_cash_balance == Decimal("200")
    assert gap.positions == [] and not gap.has_stale_prices

    replay = row_by_date(rows, date(2025, 3, 31))
    assert replay.source == "REPLAY"
    assert (replay.beginning_market_value, replay.ending_market_value) == (
        Decimal("200"), Decimal("250"))
    assert replay.total_deposits == Decimal("50")
    assert replay.net_flow == Decimal("50")
    assert replay.total_transactions == 1

    # The March row is a live PASS verdict and equals the PDF to the cent.
    assert report["pass_rows"] == 1 and report["pass_match"] == 1
    assert report["mismatches"] == []


@pytest.mark.django_db
def test_new_currency_section(account):
    # January has only CAD; February adds a USD section (begin total 0 —
    # not an exception). The February USD row is a REPLAY row that opens
    # from zero cash.
    jan = make_monthly(account, date(2025, 1, 31), begin=Decimal("100"),
                       end=Decimal("200"))
    cash(jan, Decimal("200"))
    feb_cad = make_monthly(account, date(2025, 2, 28),
                           last_stmt=date(2025, 1, 31),
                           begin=Decimal("200"), end=Decimal("200"))
    cash(feb_cad, Decimal("200"))
    feb_usd = make_monthly(account, date(2025, 2, 28), currency="USD",
                           last_stmt=date(2025, 1, 31),
                           begin=Decimal("0"), end=Decimal("100"))
    cash(feb_usd, Decimal("100"), currency="USD")
    make_tx(account, date(2025, 2, 10), "DEP", Decimal("100"), currency="USD",
            settlement=date(2025, 2, 12))

    rows, report = generate_forward_statements(account)

    # January: one PDF row (CAD only). February: REPLAY rows for both
    # sections.
    assert [(r.statement_date, r.currency, r.source) for r in rows] == [
        (date(2025, 1, 31), "CAD", "PDF"),
        (date(2025, 2, 28), "CAD", "REPLAY"),
        (date(2025, 2, 28), "USD", "REPLAY"),
    ]

    usd = row_by_date(rows, date(2025, 2, 28), currency="USD")
    # New section: opens from zero, the deposit is its whole ending.
    assert (usd.beginning_market_value, usd.ending_market_value) == (
        Decimal("0"), Decimal("100"))
    assert usd.total_deposits == Decimal("100")
    assert usd.net_flow == Decimal("100")
    assert usd.ending_cash_balance == Decimal("100")

    cad = row_by_date(rows, date(2025, 2, 28), currency="CAD")
    assert (cad.beginning_market_value, cad.ending_market_value) == (
        Decimal("200"), Decimal("200"))

    # Both February rows are live PASS verdicts equal to the PDF.
    assert report["pass_rows"] == 2 and report["pass_match"] == 2
    assert report["mismatches"] == []


@pytest.mark.django_db
def test_dlr_journal_nets_to_zero_per_account(account):
    # DLR currency journal: 100 shares move from the USD side to the CAD
    # side via a paired SELL+BUY (net zero). The position nets per account
    # and appears exactly once — in the section where the February PDF
    # holds it (CAD) — not twice, one per currency.
    jan_cad = make_monthly(account, date(2025, 1, 31), begin=Decimal("1200"),
                           end=Decimal("1200"))
    cash(jan_cad, Decimal("1200"))
    jan_usd = make_monthly(account, date(2025, 1, 31), currency="USD",
                           begin=Decimal("1050"), end=Decimal("1050"))
    cash(jan_usd, Decimal("50"), currency="USD")
    holding(jan_usd, "DLR", Decimal("100"), Decimal("10.0000"),
            Decimal("1000"), currency="USD")

    feb_cad = make_monthly(account, date(2025, 2, 28),
                           last_stmt=date(2025, 1, 31),
                           begin=Decimal("1200"), end=Decimal("1200"))
    cash(feb_cad, Decimal("200"))
    holding(feb_cad, "DLR", Decimal("100"), Decimal("10.0000"),
            Decimal("1000"))
    feb_usd = make_monthly(account, date(2025, 2, 28), currency="USD",
                           last_stmt=date(2025, 1, 31),
                           begin=Decimal("1050"), end=Decimal("1050"))
    cash(feb_usd, Decimal("1050"), currency="USD")

    make_tx(account, date(2025, 2, 10), "SELL", Decimal("1000"), symbol="DLR",
            qty=Decimal("-100"), currency="USD", settlement=date(2025, 2, 12))
    make_tx(account, date(2025, 2, 10), "BUY", Decimal("-1000"), symbol="DLR",
            qty=Decimal("100"), currency="CAD", settlement=date(2025, 2, 12))

    rows, report = generate_forward_statements(account)

    # January: the PDF holds DLR in the USD section.
    jan_usd_row = row_by_date(rows, date(2025, 1, 31), currency="USD")
    assert [p.symbol for p in jan_usd_row.positions] == ["DLR"]
    dlr_jan = jan_usd_row.positions[0]
    assert (dlr_jan.quantity, dlr_jan.price, dlr_jan.price_source) == (
        Decimal("100"), Decimal("10.0000"), "PDF")

    # February: the journal nets to zero per account; DLR appears exactly
    # once, in the CAD section where the PDF holds it now.
    feb_rows = [r for r in rows if r.statement_date == date(2025, 2, 28)]
    all_feb_symbols = [p.symbol for r in feb_rows for p in r.positions]
    assert all_feb_symbols.count("DLR") == 1

    feb_cad_row = row_by_date(rows, date(2025, 2, 28), currency="CAD")
    dlr_feb = feb_cad_row.positions[0]
    assert (dlr_feb.quantity, dlr_feb.price, dlr_feb.price_source) == (
        Decimal("100"), Decimal("10.0000"), "PDF")
    assert [p.symbol for p in row_by_date(
        rows, date(2025, 2, 28), currency="USD").positions] == []

    # Cash replayed per currency: CAD 1200 - 1000 = 200, USD 50 + 1000 = 1050.
    assert feb_cad_row.beginning_market_value == Decimal("1200")
    assert feb_cad_row.ending_market_value == Decimal("1200")
    assert row_by_date(rows, date(2025, 2, 28), currency="USD"
                       ).beginning_market_value == Decimal("1050")
    assert row_by_date(rows, date(2025, 2, 28), currency="USD"
                       ).ending_market_value == Decimal("1050")

    # Both February rows are live PASS verdicts equal to the PDF.
    assert report["pass_rows"] == 2 and report["pass_match"] == 2
    assert report["mismatches"] == []


@pytest.mark.django_db
def test_fail_month_differs_from_pdf_by_exactly_its_exception(account):
    # The February cash check FAILs: the CSV has a dividend the PDF does
    # not show. The generated REPLAY row still ends at the replayed cash
    # (275), which differs from the PDF end_total_market_value (250) by
    # exactly the exception (the 25 dividend). FAIL rows are not part of
    # the built-in check, so no mismatch is reported.
    jan = make_monthly(account, date(2025, 1, 31), begin=Decimal("100"),
                       end=Decimal("200"))
    cash(jan, Decimal("200"))
    feb = make_monthly(account, date(2025, 2, 28), last_stmt=date(2025, 1, 31),
                       begin=Decimal("200"), end=Decimal("250"))
    cash(feb, Decimal("250"))
    make_tx(account, date(2025, 2, 10), "DEP", Decimal("50"),
            settlement=date(2025, 2, 12))
    # The unmatched exception: a dividend with no PDF counterpart.
    make_tx(account, date(2025, 2, 17), "DIV", Decimal("25"), symbol="ACME",
            settlement=date(2025, 2, 17))

    rows, report = generate_forward_statements(account)

    replay = row_by_date(rows, date(2025, 2, 28))
    assert replay.source == "REPLAY"
    # Replayed cash: 200 + 50 deposit + 25 dividend = 275.
    assert replay.ending_market_value == Decimal("275")
    assert replay.total_dividends == Decimal("25")
    assert replay.total_deposits == Decimal("50")
    assert replay.net_flow == Decimal("75")

    # The difference from the PDF is exactly the exception.
    pdf_end = feb.end_total_market_value
    assert replay.ending_market_value - pdf_end == Decimal("25")

    # The live verdict is FAIL, so the built-in check skips the row: no
    # PASS rows counted, no mismatch reported.
    assert report["pass_rows"] == 0
    assert report["mismatches"] == []


@pytest.mark.django_db
def test_position_symbol_allows_long_fallback_names(account):
    # The "(no symbol: <security name>)" fallback can exceed 50 chars; the
    # model and migration 0047 allow 200.
    field = CoreMonthlyStatementPosition._meta.get_field("symbol")
    assert field.max_length == 200

    stmt = CoreMonthlyStatement.objects.create(
        account=account, statement_month=date(2025, 1, 1),
        statement_date=date(2025, 1, 31), currency="CAD", source="PDF",
    )
    long_symbol = "(no symbol: " + "A" * 80 + ")"
    pos = CoreMonthlyStatementPosition.objects.create(
        statement=stmt, symbol=long_symbol, quantity=Decimal("1"),
        price=Decimal("1.0000"), value=Decimal("1.00"),
    )
    assert pos.symbol == long_symbol


@pytest.mark.django_db
def test_apply_writes_positions(account):
    # --apply creates the row's positions (bulk_create, batch_size=200).
    jan = make_monthly(account, date(2025, 1, 31), begin=Decimal("1100"),
                       end=Decimal("1100"))
    cash(jan, Decimal("100"))
    holding(jan, "ACME", Decimal("100"), Decimal("10.0000"), Decimal("1000"))

    call_command("generate_statements", "--account", "ACCOUNT_A", "--apply")

    pos = CoreMonthlyStatementPosition.objects.filter(
        statement__account=account).get()
    assert (pos.symbol, pos.quantity, pos.price, pos.price_source) == (
        "ACME", Decimal("100"), Decimal("10.0000"), "PDF")


@pytest.mark.django_db
def test_migration_0047_backfill_statement_date(account):
    # Migration 0047 is not applied on mercury yet: its backfill reads the
    # (pk, statement_month) keys first and bulk_updates in batches of 200
    # (SQL Server allows 2,100 parameters per statement). More than 200 rows
    # forces a second batch.
    import calendar
    import importlib

    mod = importlib.import_module(
        "core.migrations.0047_coremonthlystatement_source_and_more"
    )
    months = [date(2000 + i // 12, i % 12 + 1, 1) for i in range(250)]
    CoreMonthlyStatement.objects.bulk_create(
        CoreMonthlyStatement(
            account=account, statement_month=m, currency="CAD",
            statement_date=None,
        )
        for m in months
    )

    class FakeApps:
        def get_model(self, app_label, model_name):
            return CoreMonthlyStatement

    mod.backfill_statement_date(FakeApps(), None)

    assert CoreMonthlyStatement.objects.filter(
        statement_date__isnull=True).count() == 0
    for m in months:
        row = CoreMonthlyStatement.objects.get(account=account, statement_month=m)
        last_day = calendar.monthrange(m.year, m.month)[1]
        assert row.statement_date == m.replace(day=last_day)


@pytest.mark.django_db
def test_gap_fill_starts_after_the_anchor_month(account):
    # Statements dated Friday 2026-01-30 and 2026-03-31: the anchor's own
    # month already has a row, so gap fill starts in the calendar month
    # after the anchor month — no GAP row for 2026-01-31. The same rule
    # holds after the last PDF (no April row). No two rows share
    # (account, statement_month, currency).
    jan = make_monthly(account, date(2026, 1, 30), begin=Decimal("100"),
                       end=Decimal("200"))
    cash(jan, Decimal("200"))
    mar = make_monthly(account, date(2026, 3, 31), last_stmt=date(2026, 1, 30),
                       begin=Decimal("200"), end=Decimal("250"))
    cash(mar, Decimal("250"))
    make_tx(account, date(2026, 3, 15), "DEP", Decimal("50"),
            settlement=date(2026, 3, 17))

    rows, report = generate_forward_statements(account)

    assert [(r.statement_date, r.source) for r in rows] == [
        (date(2026, 1, 30), "PDF"),
        (date(2026, 2, 28), "GAP"),
        (date(2026, 3, 31), "REPLAY"),
    ]
    keys = [(r.statement_month, r.currency) for r in rows]
    assert len(keys) == len(set(keys))


@pytest.mark.django_db
def test_fail_month_does_not_carry_into_next_period(account):
    # February FAILs its live cash check (an extra CSV dividend the PDF does
    # not show). The chain re-anchors on the February PDF after every stored
    # statement, so March starts from the PDF's closing cash (250), not the
    # replayed 275 — its row equals its PDF to the cent and no mismatch is
    # reported.
    jan = make_monthly(account, date(2025, 1, 31), begin=Decimal("100"),
                       end=Decimal("200"))
    cash(jan, Decimal("200"))
    feb = make_monthly(account, date(2025, 2, 28), last_stmt=date(2025, 1, 31),
                       begin=Decimal("200"), end=Decimal("250"))
    cash(feb, Decimal("250"))
    mar = make_monthly(account, date(2025, 3, 31), last_stmt=date(2025, 2, 28),
                       begin=Decimal("250"), end=Decimal("300"))
    cash(mar, Decimal("300"))
    make_tx(account, date(2025, 2, 10), "DEP", Decimal("50"),
            settlement=date(2025, 2, 12))
    # The unmatched exception that FAILs February.
    make_tx(account, date(2025, 2, 17), "DIV", Decimal("25"), symbol="ACME",
            settlement=date(2025, 2, 17))
    make_tx(account, date(2025, 3, 10), "DEP", Decimal("50"),
            settlement=date(2025, 3, 12))

    rows, report = generate_forward_statements(account)

    feb_row = row_by_date(rows, date(2025, 2, 28))
    # February is replayed (cash 275) and FAILs the live check.
    assert feb_row.ending_market_value == Decimal("275")

    mar_row = row_by_date(rows, date(2025, 3, 31))
    # March starts from the February PDF closing cash (250), not 275.
    assert mar_row.beginning_market_value == Decimal("250")
    assert mar_row.ending_market_value == mar.end_total_market_value

    # The live verdict is PASS and the row equals the PDF to the cent:
    # no mismatch reported.
    assert report["pass_rows"] == 1 and report["pass_match"] == 1
    assert report["mismatches"] == []


@pytest.mark.django_db
def test_re_run_changes_nothing(account):
    # Applying twice (delete-and-replace) yields byte-identical rows and
    # positions: same values in the same order.
    jan = make_monthly(account, date(2025, 1, 31), begin=Decimal("100"),
                       end=Decimal("200"))
    cash(jan, Decimal("200"))
    mar = make_monthly(account, date(2025, 3, 31), last_stmt=date(2025, 1, 31),
                       begin=Decimal("200"), end=Decimal("250"))
    cash(mar, Decimal("250"))
    make_tx(account, date(2025, 3, 10), "DEP", Decimal("50"),
            settlement=date(2025, 3, 12))

    fields = [
        "statement_month", "statement_date", "currency", "source",
        "beginning_market_value", "ending_market_value", "total_buys",
        "total_sells", "total_dividends", "total_interest",
        "total_commission", "total_deposits", "total_withdrawals",
        "total_transfers", "total_income", "total_fees", "total_tax",
        "net_flow", "total_transactions", "ending_cash_balance",
        "has_stale_prices",
    ]

    def snapshot():
        stmts = list(
            CoreMonthlyStatement.objects.filter(account=account)
            .order_by("statement_date", "currency")
            .values(*fields)
        )
        positions = list(
            CoreMonthlyStatementPosition.objects.filter(
                statement__account=account
            )
            .order_by("statement__statement_date", "statement__currency",
                      "symbol")
            .values("statement__statement_date", "statement__currency",
                    "symbol", "quantity", "price", "price_source", "value")
        )
        return stmts, positions

    call_command("generate_statements", "--account", "ACCOUNT_A", "--apply")
    first = snapshot()
    assert len(first[0]) == 3  # PDF + GAP + REPLAY

    call_command("generate_statements", "--account", "ACCOUNT_A", "--apply")
    assert snapshot() == first
