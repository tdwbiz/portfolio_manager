"""Reconciliation engine for CSV-generated vs RBC PDF data.

Compares CoreMonthlyStatement/CoreAnnualReport (CSV master) against
RbcMonthly/RbcAnnual (PDF import) to find matches and discrepancies.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum

from core.models import (
    Account,
    CoreAnnualReport,
    CoreMonthlyStatement,
    CoreReconciliation,
    RbcAnnual,
    RbcMonthly,
)

logger = logging.getLogger(__name__)

# Threshold for flagging discrepancies (anything above this is flagged)
DISCREPANCY_THRESHOLD = Decimal("1.00")


def _compare_decimals(a: Decimal | None, b: Decimal | None) -> tuple[bool, Decimal]:
    """Compare two Decimal values. Returns (is_match, absolute_difference)."""
    if a is None and b is None:
        return True, Decimal("0")
    if a is None or b is None:
        return False, Decimal("999999")
    diff = abs(a - b)
    return diff <= DISCREPANCY_THRESHOLD, diff


def reconcile_monthly(
    account: Account,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[CoreReconciliation]:
    """Reconcile CSV-generated monthly statements against RBC PDF data.

    For each month that has a CoreMonthlyStatement, find the matching
    RbcMonthly statement and compare key values.

    Args:
        account: The Account to reconcile.
        from_date: Optional start date.
        to_date: Optional end date.

    Returns:
        List of CoreReconciliation records.
    """
    csv_statements = CoreMonthlyStatement.objects.filter(account=account)
    rbc_statements = RbcMonthly.objects.filter(account=account)

    if from_date:
        csv_statements = csv_statements.filter(statement_month__gte=from_date)
        rbc_statements = rbc_statements.filter(statement_date__gte=from_date)
    if to_date:
        csv_statements = csv_statements.filter(statement_month__lte=to_date)
        rbc_statements = rbc_statements.filter(statement_date__lte=to_date)

    # Build a lookup of RBC statements by date and currency
    rbc_lookup = {}
    for rbc in rbc_statements:
        key = (rbc.statement_date, rbc.currency)
        rbc_lookup[key] = rbc

    generated = []

    for stmt in csv_statements.order_by("statement_month", "currency"):
        # Find matching RBC statement (match by month-end or close date)
        rbc = rbc_lookup.get((stmt.statement_month, stmt.currency))

        # Compare beginning value
        csv_begin = stmt.beginning_market_value
        rbc_begin = rbc.begin_total_market_value if rbc else None
        begin_match, begin_diff = _compare_decimals(csv_begin, rbc_begin)

        # Compare ending value
        csv_end = stmt.ending_market_value
        rbc_end = rbc.end_total_market_value if rbc else None
        end_match, end_diff = _compare_decimals(csv_end, rbc_end)

        # Compare total buys
        csv_buys = stmt.total_buys
        # RBC monthly statements don't break out buys/sells by transaction type;
        # only begin/end market values are available
        rbc_buys = None

        # Compare total sells
        csv_sells = stmt.total_sells
        rbc_sells = None

        # Compare total income (dividends + interest)
        csv_income = stmt.total_dividends + stmt.total_interest
        rbc_income = None

        # Compare transaction count
        csv_tx_count = stmt.total_transactions
        rbc_tx_count = None

        # Overall match: both beginning and ending must match
        is_matching = begin_match and end_match

        # Total discrepancy
        total_diff = begin_diff + end_diff
        if rbc_buys is not None:
            _, buys_diff = _compare_decimals(csv_buys, rbc_buys)
            total_diff += buys_diff
        if rbc_sells is not None:
            _, sells_diff = _compare_decimals(csv_sells, rbc_sells)
            total_diff += sells_diff

        notes_parts = []
        if rbc is None:
            notes_parts.append("No matching RBC statement found")
        else:
            if not begin_match:
                notes_parts.append(
                    f"Beginning value differs: CSV={csv_begin}, RBC={rbc_begin}"
                )
            if not end_match:
                notes_parts.append(
                    f"Ending value differs: CSV={csv_end}, RBC={rbc_end}"
                )

        notes = "; ".join(notes_parts) if notes_parts else ""

        period_end = stmt.statement_month + timedelta(days=31)

        with transaction.atomic():
            rec, created = CoreReconciliation.objects.update_or_create(
                account=account,
                reconciliation_type="monthly",
                period_start=stmt.statement_month,
                period_end=period_end,
                defaults={
                    "csv_beginning_value": csv_begin,
                    "csv_ending_value": csv_end,
                    "csv_total_buys": csv_buys,
                    "csv_total_sells": csv_sells,
                    "csv_total_income": csv_income,
                    "csv_total_transactions": csv_tx_count,
                    "rbc_beginning_value": rbc_begin,
                    "rbc_ending_value": rbc_end,
                    "rbc_total_buys": rbc_buys,
                    "rbc_total_sells": rbc_sells,
                    "rbc_total_income": rbc_income,
                    "rbc_total_transactions": rbc_tx_count,
                    "matching": is_matching,
                    "discrepancy_amount": total_diff,
                    "discrepancy_notes": notes,
                    "reconciled_by": "auto",
                },
            )

        generated.append(rec)
        status = "MATCH" if is_matching else "DISCREPANCY"
        logger.info(
            f"Reconciled monthly: {account} {stmt.statement_month} [{status}] "
            f"diff=${total_diff:.2f}"
        )

    return generated


def reconcile_annual(
    account: Account,
    from_year: int | None = None,
    to_year: int | None = None,
) -> list[CoreReconciliation]:
    """Reconcile CSV-generated annual reports against RBC PDF data.

    Args:
        account: The Account to reconcile.
        from_year: Optional start year.
        to_year: Optional end year.

    Returns:
        List of CoreReconciliation records.
    """
    csv_reports = CoreAnnualReport.objects.filter(account=account)
    rbc_reports = RbcAnnual.objects.filter(account=account)

    if from_year:
        csv_reports = csv_reports.filter(report_year__gte=from_year)
        rbc_reports = rbc_reports.filter(report_year__gte=from_year)
    if to_year:
        csv_reports = csv_reports.filter(report_year__lte=to_year)
        rbc_reports = rbc_reports.filter(report_year__lte=to_year)

    # Build RBC lookup by year and currency
    rbc_lookup = {}
    for rbc in rbc_reports:
        key = (rbc.report_year, rbc.currency)
        rbc_lookup[key] = rbc

    generated = []

    for report in csv_reports.order_by("report_year", "currency"):
        rbc = rbc_lookup.get((report.report_year, report.currency))

        # Compare beginning value
        csv_begin = report.beginning_value
        rbc_begin = rbc.bal_beginning_value if rbc else None
        begin_match, begin_diff = _compare_decimals(csv_begin, rbc_begin)

        # Compare ending value
        csv_end = report.ending_value
        rbc_end = rbc.bal_ending_value if rbc else None
        end_match, end_diff = _compare_decimals(csv_end, rbc_end)

        # Compare total buys (deposits in RBC terms)
        csv_buys = report.total_buys
        rbc_buys = rbc.bal_deposits_in if rbc else None

        # Compare sells (withdrawals)
        csv_sells = report.total_sells
        rbc_sells = rbc.bal_withdrawals_out if rbc else None

        # Compare income (net return)
        csv_income = report.total_income
        rbc_income = rbc.bal_net_return if rbc else None

        is_matching = begin_match and end_match

        total_diff = begin_diff + end_diff
        if rbc_buys is not None:
            _, buys_diff = _compare_decimals(csv_buys, rbc_buys)
            total_diff += buys_diff
        if rbc_sells is not None:
            _, sells_diff = _compare_decimals(csv_sells, rbc_sells)
            total_diff += sells_diff
        if rbc_income is not None:
            _, income_diff = _compare_decimals(csv_income, rbc_income)
            total_diff += income_diff

        notes_parts = []
        if rbc is None:
            notes_parts.append("No matching RBC annual report found")
        else:
            if not begin_match:
                notes_parts.append(
                    f"Beginning value differs: CSV={csv_begin}, RBC={rbc_begin}"
                )
            if not end_match:
                notes_parts.append(
                    f"Ending value differs: CSV={csv_end}, RBC={rbc_end}"
                )

        notes = "; ".join(notes_parts) if notes_parts else ""

        period_start = date(report.report_year, 1, 1)
        period_end = date(report.report_year, 12, 31)

        with transaction.atomic():
            rec, created = CoreReconciliation.objects.update_or_create(
                account=account,
                reconciliation_type="annual",
                period_start=period_start,
                period_end=period_end,
                defaults={
                    "csv_beginning_value": csv_begin,
                    "csv_ending_value": csv_end,
                    "csv_total_buys": csv_buys,
                    "csv_total_sells": csv_sells,
                    "csv_total_income": csv_income,
                    "csv_total_transactions": report.total_transactions,
                    "rbc_beginning_value": rbc_begin,
                    "rbc_ending_value": rbc_end,
                    "rbc_total_buys": rbc_buys,
                    "rbc_total_sells": rbc_sells,
                    "rbc_total_income": rbc_income,
                    "rbc_total_transactions": None,
                    "matching": is_matching,
                    "discrepancy_amount": total_diff,
                    "discrepancy_notes": notes,
                    "reconciled_by": "auto",
                },
            )

        generated.append(rec)
        status = "MATCH" if is_matching else "DISCREPANCY"
        logger.info(
            f"Reconciled annual: {account} {report.report_year} [{status}] "
            f"diff=${total_diff:.2f}"
        )

    return generated


def reconcile_account(
    account: Account,
    period_type: str = "monthly",
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[CoreReconciliation]:
    """Run full reconciliation for an account.

    Args:
        account: The Account to reconcile.
        period_type: 'monthly' or 'annual'.
        from_date: Optional start date (or year start for annual).
        to_date: Optional end date (or year end for annual).

    Returns:
        List of CoreReconciliation records.
    """
    if period_type == "annual":
        from_year = from_date.year if from_date else None
        to_year = to_date.year if to_date else None
        return reconcile_annual(account, from_year=from_year, to_year=to_year)
    else:
        return reconcile_monthly(account, from_date=from_date, to_date=to_date)
