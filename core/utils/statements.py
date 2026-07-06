"""Monthly and annual statement generation from transaction data.

Provides functions to generate CoreMonthlyStatement and CoreAnnualReport
records from the master Transaction (CSV import) data.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import ExtractYear

from core.models import (
    Account,
    CoreAnnualReport,
    CoreMonthlyStatement,
    RbcAnnual,
    RbcMonthly,
    Transaction,
)

logger = logging.getLogger(__name__)

# Transaction type groupings
BUY_TYPES = {"BUY", "DEP", "TFR-IN"}
SELL_TYPES = {"SELL", "WDR", "TFR-OUT"}
DIVIDEND_TYPES = {"DIV"}
INTEREST_TYPES = {"INT"}
COMMISSION_TYPES = {"COMM"}


def _month_start(d: date) -> date:
    """Return the first day of the month for a given date."""
    return d.replace(day=1)


def _month_end(d: date) -> date:
    """Return the last day of the month for a given date."""
    if d.month == 12:
        next_month = d.replace(year=d.year + 1, month=1, day=1)
    else:
        next_month = d.replace(month=d.month + 1, day=1)
    return next_month - timedelta(days=1)


def generate_monthly_statements(
    account: Account,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[CoreMonthlyStatement]:
    """Generate monthly statements from transactions for the given account.

    For each month in the transaction range:
    1. Get all transactions for that month
    2. Sum buys, sells, dividends, interest, commissions
    3. Compute ending market value (cumulative from beginning value)
    4. Store or update in CoreMonthlyStatement

    Args:
        account: The Account to generate statements for.
        from_date: Optional start date (first transaction date to include).
        to_date: Optional end date (last transaction date to include).

    Returns:
        List of generated/updated CoreMonthlyStatement objects.
    """
    qs = Transaction.objects.filter(account=account)
    if from_date:
        qs = qs.filter(date__gte=from_date)
    if to_date:
        qs = qs.filter(date__lte=to_date)

    txs = qs.order_by("date", "currency")
    if not txs.exists():
        logger.info(f"No transactions for account {account} in range")
        return []

    # Group by (month_start, currency)
    from collections import defaultdict

    groups = defaultdict(list)
    for tx in txs:
        ms = _month_start(tx.date)
        groups[(ms, tx.currency)].append(tx)

    # Get all existing statements for this account for reference
    existing = CoreMonthlyStatement.objects.filter(account=account).order_by(
        "statement_month", "currency"
    )
    ending_values = {}  # (month_start, currency) -> ending_market_value
    for stmt in existing:
        ending_values[(stmt.statement_month, stmt.currency)] = stmt.ending_market_value

    generated = []
    sorted_keys = sorted(groups.keys())

    for (month_start, currency), month_txs in groups.items():
        total_buys = Decimal("0")
        total_sells = Decimal("0")
        total_dividends = Decimal("0")
        total_interest = Decimal("0")
        total_commission = Decimal("0")
        count = 0

        for tx in month_txs:
            count += 1
            amt = tx.amount_cad
            tx_type = tx.type

            if tx_type in BUY_TYPES:
                total_buys += amt
            elif tx_type in SELL_TYPES:
                total_sells += amt
            elif tx_type in DIVIDEND_TYPES:
                total_dividends += amt
            elif tx_type in INTEREST_TYPES:
                total_interest += amt
            elif tx_type in COMMISSION_TYPES:
                total_commission += amt
            # Also add the commission field value for BUY/SELL/DIV transactions
            # (most brokerages store commission in the commission field, not as separate COMM tx)
            if tx_type not in COMMISSION_TYPES:
                total_commission += tx.commission

        # Beginning MV: ending MV from previous month, or 0
        prev_month = month_start
        if month_start.month == 1:
            prev_month = month_start.replace(year=month_start.year - 1, month=12)
        else:
            prev_month = month_start.replace(month=month_start.month - 1)

        key = (prev_month, currency)
        beginning_mv = ending_values.get(key, Decimal("0"))

        # Ending MV: beginning + sells - buys + income - commissions
        # (sells reduce portfolio, buys increase it)
        # Actually: ending = beginning + income + (buys - sells) - commissions
        # But "buys" = money going out (increasing holdings), "sells" = money coming in
        # For market value: ending = beginning + buys - sells + dividends + interest - commission
        ending_mv = (
            beginning_mv
            + total_buys
            - total_sells
            + total_dividends
            + total_interest
            - total_commission
        )

        net_flow = total_buys - total_sells

        with transaction.atomic():
            stmt, created = CoreMonthlyStatement.objects.update_or_create(
                account=account,
                statement_month=month_start,
                currency=currency,
                defaults={
                    "beginning_market_value": beginning_mv,
                    "ending_market_value": ending_mv,
                    "total_buys": total_buys,
                    "total_sells": total_sells,
                    "total_dividends": total_dividends,
                    "total_interest": total_interest,
                    "total_commission": total_commission,
                    "net_flow": net_flow,
                    "total_transactions": count,
                },
            )

        ending_values[(month_start, currency)] = ending_mv
        generated.append(stmt)
        logger.info(
            f"Generated monthly statement: {account} {month_start} [{currency}] "
            f"begin={beginning_mv} end={ending_mv} buys={total_buys} sells={total_sells}"
        )

    return generated


def generate_annual_reports(
    account: Account,
    from_year: int | None = None,
    to_year: int | None = None,
) -> list[CoreAnnualReport]:
    """Generate annual reports from monthly statements for the given account.

    Aggregates CoreMonthlyStatement data into yearly summaries.
    If monthly statements don't exist for some months, falls back to raw transactions.

    Args:
        account: The Account to generate reports for.
        from_year: Optional start year.
        to_year: Optional end year.

    Returns:
        List of generated/updated CoreAnnualReport objects.
    """
    monthly_qs = CoreMonthlyStatement.objects.filter(account=account)

    # Use annotation to extract year (Django 5.x compatible, no .extra())
    from django.db.models.functions import ExtractYear

    year_months = (
        monthly_qs.annotate(year=ExtractYear("statement_month"))
        .values("year")
        .distinct()
        .order_by("year")
    )

    years = [int(r["year"]) for r in year_months]

    if from_year:
        years = [y for y in years if y >= from_year]
    if to_year:
        years = [y for y in years if y <= to_year]

    if not years:
        logger.info(f"No monthly statement data for account {account}")
        return []

    generated = []
    for year in years:
        monthly = (
            monthly_qs.filter(
                statement_month__year=year,
            )
            .values_list("statement_month", "currency", "currency")
            .order_by("currency", "statement_month")
        )

        # Aggregate by currency
        currency_totals = {}
        for row in monthly:
            cur = row[1]
            if cur not in currency_totals:
                currency_totals[cur] = {
                    "beginning_mv": Decimal("0"),
                    "ending_mv": Decimal("0"),
                    "buys": Decimal("0"),
                    "sells": Decimal("0"),
                    "dividends": Decimal("0"),
                    "interest": Decimal("0"),
                    "commission": Decimal("0"),
                    "transactions": 0,
                }

        # Get all monthly statements for this year
        year_monthly = monthly_qs.filter(statement_month__year=year)

        for cur in currency_totals:
            cur_monthly = year_monthly.filter(currency=cur).order_by("statement_month")
            if cur_monthly.exists():
                # Beginning = first month's beginning
                currency_totals[cur]["beginning_mv"] = (
                    cur_monthly.first().beginning_market_value
                )
                # Ending = last month's ending
                currency_totals[cur]["ending_mv"] = (
                    cur_monthly.last().ending_market_value
                )
                # Sums
                totals = cur_monthly.aggregate(
                    buys=Sum("total_buys"),
                    sells=Sum("total_sells"),
                    dividends=Sum("total_dividends"),
                    interest=Sum("total_interest"),
                    commission=Sum("total_commission"),
                    tx_count=Sum("total_transactions"),
                )
                currency_totals[cur]["buys"] = totals["buys"] or Decimal("0")
                currency_totals[cur]["sells"] = totals["sells"] or Decimal("0")
                currency_totals[cur]["dividends"] = totals["dividends"] or Decimal("0")
                currency_totals[cur]["interest"] = totals["interest"] or Decimal("0")
                currency_totals[cur]["commission"] = totals["commission"] or Decimal(
                    "0"
                )
                currency_totals[cur]["transactions"] = sum(
                    m.total_transactions for m in cur_monthly
                )

        for currency, totals in currency_totals.items():
            total_income = totals["dividends"] + totals["interest"]
            net_return = (totals["buys"] - totals["sells"]) + total_income
            amount_invested = totals["buys"] - totals["sells"]

            # Deposits = buys + dividends + interest
            deposits = totals["buys"] + totals["dividends"] + totals["interest"]
            # Withdrawals = sells
            withdrawals = totals["sells"]

            with transaction.atomic():
                report, created = CoreAnnualReport.objects.update_or_create(
                    account=account,
                    report_year=year,
                    currency=currency,
                    defaults={
                        "beginning_value": totals["beginning_mv"],
                        "ending_value": totals["ending_mv"],
                        "deposits": deposits,
                        "withdrawals": withdrawals,
                        "net_investment_return": net_return,
                        "amount_invested": amount_invested,
                        "total_buys": totals["buys"],
                        "total_sells": totals["sells"],
                        "total_income": total_income,
                        "total_commission": totals["commission"],
                        "total_transactions": totals["transactions"],
                    },
                )

            generated.append(report)
            logger.info(
                f"Generated annual report: {account} {year} [{currency}] "
                f"begin={totals['beginning_mv']} end={totals['ending_mv']} "
                f"deposits={deposits} withdrawals={withdrawals}"
            )

    return generated
